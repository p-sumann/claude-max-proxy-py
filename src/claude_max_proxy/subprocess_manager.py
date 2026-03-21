"""Claude Code CLI subprocess manager.

Spawns the `claude` binary, reads NDJSON from stdout,
and emits typed events as data arrives.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from typing import Any, Callable

from .types import is_assistant_message, is_content_delta, is_result_message

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_MS = 300_000  # 5 minutes


class EventEmitter:
    """Minimal async-compatible event emitter."""

    def __init__(self) -> None:
        self._listeners: dict[str, list[Callable]] = {}

    def on(self, event: str, callback: Callable) -> None:
        self._listeners.setdefault(event, []).append(callback)

    def emit(self, event: str, *args: Any) -> None:
        for cb in self._listeners.get(event, []):
            cb(*args)


class ClaudeSubprocess(EventEmitter):
    """Manages a single Claude CLI subprocess.

    Events emitted: message, content_delta, assistant, result, raw, error, close
    """

    def __init__(self) -> None:
        super().__init__()
        self._process: asyncio.subprocess.Process | None = None
        self._buffer: str = ""
        self._stderr_buffer: str = ""
        self._timeout_handle: asyncio.TimerHandle | None = None
        self._is_killed: bool = False

    @staticmethod
    def _build_args(prompt: str, *, model: str, session_id: str | None = None) -> list[str]:
        args = [
            "--print",
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--model", model,
            "--no-session-persistence",
            prompt,
        ]
        if session_id:
            args.extend(["--session-id", session_id])
        return args

    async def start(
        self,
        prompt: str,
        *,
        model: str = "sonnet",
        session_id: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_MS,
        cwd: str | None = None,
    ) -> None:
        """Spawn the CLI subprocess. Events fire as output arrives."""
        args = self._build_args(prompt, model=model, session_id=session_id)
        loop = asyncio.get_event_loop()

        try:
            self._process = await asyncio.create_subprocess_exec(
                "claude", *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "Claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code"
            )

        if self._process.stdin:
            self._process.stdin.close()

        logger.info("[Subprocess] PID: %s", self._process.pid)

        self._timeout_handle = loop.call_later(timeout / 1000, self._on_timeout, timeout)

        asyncio.create_task(self._read_stdout())
        asyncio.create_task(self._read_stderr())
        asyncio.create_task(self._wait_close())

    async def _read_stdout(self) -> None:
        assert self._process and self._process.stdout
        while True:
            chunk = await self._process.stdout.read(8192)
            if not chunk:
                break
            self._buffer += chunk.decode()
            self._process_buffer()

    async def _read_stderr(self) -> None:
        assert self._process and self._process.stderr
        while True:
            chunk = await self._process.stderr.read(4096)
            if not chunk:
                break
            text = chunk.decode().strip()
            if text:
                self._stderr_buffer += text + "\n"
                logger.debug("[stderr] %s", text[:200])

    async def _wait_close(self) -> None:
        assert self._process
        await self._process.wait()
        code = self._process.returncode
        logger.info("[Subprocess] Exited with code %s", code)
        self._clear_timeout()
        if self._buffer.strip():
            self._process_buffer()

        # Emit a descriptive error if the process failed with stderr output
        if code != 0 and self._stderr_buffer.strip():
            error_hint = self._classify_error(self._stderr_buffer)
            self.emit("error", RuntimeError(error_hint))

        self.emit("close", code)

    def _classify_error(self, stderr: str) -> str:
        """Turn raw stderr into a user-friendly error message."""
        lower = stderr.lower()
        if any(kw in lower for kw in ("auth", "login", "token", "unauthorized", "sign in", "credential")):
            return (
                "Authentication failed. Your Claude session may have expired.\n"
                "Fix: run `claude auth login` then retry."
            )
        if any(kw in lower for kw in ("rate limit", "too many", "429")):
            return "Rate limited by Claude. Wait a moment and retry."
        if any(kw in lower for kw in ("not found", "enoent", "no such file")):
            return (
                "Claude CLI binary not found or broken.\n"
                "Fix: run `npm install -g @anthropic-ai/claude-code`"
            )
        if any(kw in lower for kw in ("network", "connect", "timeout", "econnrefused")):
            return "Network error reaching Claude API. Check your internet connection."
        # Fallback: return truncated stderr
        return f"Claude CLI error: {stderr.strip()[:300]}"

    def _process_buffer(self) -> None:
        """Parse NDJSON lines from the buffer and emit events."""
        lines = self._buffer.split("\n")
        self._buffer = lines.pop()  # keep incomplete line

        for line in lines:
            trimmed = line.strip()
            if not trimmed:
                continue
            try:
                message = json.loads(trimmed)
                self.emit("message", message)
                if is_content_delta(message):
                    self.emit("content_delta", message)
                elif is_assistant_message(message):
                    self.emit("assistant", message)
                elif is_result_message(message):
                    self.emit("result", message)
            except json.JSONDecodeError:
                self.emit("raw", trimmed)

    def _on_timeout(self, timeout_ms: int) -> None:
        if not self._is_killed:
            self._is_killed = True
            if self._process:
                self._process.terminate()
            self.emit("error", RuntimeError(f"Request timed out after {timeout_ms}ms"))

    def _clear_timeout(self) -> None:
        if self._timeout_handle:
            self._timeout_handle.cancel()
            self._timeout_handle = None

    def kill(self) -> None:
        if not self._is_killed and self._process:
            self._is_killed = True
            self._clear_timeout()
            self._process.terminate()

    def is_running(self) -> bool:
        return (
            self._process is not None
            and not self._is_killed
            and self._process.returncode is None
        )


async def verify_claude() -> dict[str, Any]:
    """Check that the `claude` binary is on PATH and works."""
    if not shutil.which("claude"):
        return {"ok": False, "error": "Claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code"}

    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            return {"ok": True, "version": stdout.decode().strip()}
        return {"ok": False, "error": "Claude CLI returned non-zero exit code"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def verify_auth() -> dict[str, Any]:
    """Check CLI authentication by running a cheap CLI command.

    If the user isn't logged in, the CLI typically prints an auth error
    to stderr or exits non-zero.
    """
    if not shutil.which("claude"):
        return {"ok": False, "error": "Claude CLI not found"}

    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "--print", "--model", "haiku",
            "--output-format", "stream-json",
            "--no-session-persistence",
            "say ok",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        stderr_text = stderr.decode().strip()

        if proc.returncode != 0:
            # Common auth failure patterns from Claude CLI
            lower = stderr_text.lower()
            if any(kw in lower for kw in ("auth", "login", "token", "credential", "unauthorized", "sign in")):
                return {
                    "ok": False,
                    "error": f"Not authenticated. Run: claude auth login\n  Detail: {stderr_text[:200]}",
                }
            return {
                "ok": False,
                "error": f"CLI exited with code {proc.returncode}: {stderr_text[:200] or 'unknown error'}",
            }

        return {"ok": True}

    except asyncio.TimeoutError:
        return {"ok": False, "error": "Auth check timed out after 30s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
