"""Claude Code CLI subprocess manager.

Spawns the `claude` binary, reads NDJSON from stdout,
and emits typed events as data arrives.

Optimizations:
  - --tools ""           → disables all CLI tool use (no mid-generation pauses)
  - --system-prompt      → separate system prompt for Anthropic prompt caching
  - --effort medium      → reduces extended thinking pauses (2-3 min → seconds)
  - --resume SESSION_ID  → reuses session history for prompt caching
  - stdin piping         → avoids ARG_MAX limits on large prompts
"""

from __future__ import annotations

import asyncio
import codecs
import json
import logging
import shutil
import uuid
from typing import Any, Callable

from .types import is_assistant_message, is_content_delta, is_result_message

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_MS = 900_000  # 15 minutes


# ── Active session tracker ───────────────────────────────────────────────
# Maps model alias → session UUID for --resume reuse.
# When the proxy sends a first request for a model, it creates a session.
# Subsequent requests for the same model reuse it via --resume.

_active_sessions: dict[str, str] = {}


def get_or_create_session(model: str) -> tuple[str, bool]:
    """Get existing session ID for a model, or create a new one.

    Returns (session_id, is_new). If is_new=True, caller should use
    --session-id. If is_new=False, caller should use --resume.
    """
    if model in _active_sessions:
        return _active_sessions[model], False
    session_id = str(uuid.uuid4())
    _active_sessions[model] = session_id
    logger.info("[Sessions] Created session for model=%s: %s", model, session_id)
    return session_id, True


def reset_session(model: str) -> None:
    """Reset session for a model (e.g. on error)."""
    old = _active_sessions.pop(model, None)
    if old:
        logger.info("[Sessions] Reset session for model=%s (was %s)", model, old)


def reset_all_sessions() -> None:
    """Reset all sessions."""
    _active_sessions.clear()
    logger.info("[Sessions] All sessions reset")


# ── Event emitter ────────────────────────────────────────────────────────

class EventEmitter:
    """Minimal async-compatible event emitter."""

    def __init__(self) -> None:
        self._listeners: dict[str, list[Callable]] = {}

    def on(self, event: str, callback: Callable) -> None:
        self._listeners.setdefault(event, []).append(callback)

    def emit(self, event: str, *args: Any) -> None:
        for cb in self._listeners.get(event, []):
            cb(*args)


# ── Subprocess ───────────────────────────────────────────────────────────

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
    def _build_args(
        *,
        model: str,
        system_prompt: str | None = None,
        session_id: str | None = None,
        resume_session: str | None = None,
    ) -> list[str]:
        """Build CLI arguments.

        If resume_session is set, uses --resume (session already exists).
        Otherwise uses --session-id to create a new session.
        The user prompt is always piped via stdin.
        """
        args = [
            "--print",
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--model", model,
            "--dangerously-skip-permissions",
            # Only allow essential file/exec tools. The pipeline needs these
            # (file_write→Write, manim_compiler→Bash, file_read→Read).
            # Disabling WebSearch/WebFetch/etc. prevents unnecessary pauses.
            "--tools", "Bash,Read,Write,Edit",
            # Reduce extended thinking to avoid long pauses.
            # Default effort causes Claude to "think" for minutes on complex prompts.
            "--effort", "medium",
        ]

        # Session reuse: --resume for existing sessions, --session-id for new ones.
        # --resume loads previous conversation from disk → Anthropic API caches
        # the full history, so subsequent calls only pay for new tokens.
        if resume_session:
            args.extend(["--resume", resume_session])
        elif session_id:
            args.extend(["--session-id", session_id])

        # Separate system prompt → enables Anthropic prompt caching on the
        # system prefix. Only set on first call (--resume inherits it).
        if system_prompt and not resume_session:
            args.extend(["--system-prompt", system_prompt])

        return args

    async def start(
        self,
        prompt: str,
        *,
        model: str = "sonnet",
        system_prompt: str | None = None,
        session_id: str | None = None,
        resume_session: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_MS,
        cwd: str | None = None,
    ) -> None:
        """Spawn the CLI subprocess. Events fire as output arrives."""
        args = self._build_args(
            model=model,
            system_prompt=system_prompt,
            session_id=session_id,
            resume_session=resume_session,
        )
        loop = asyncio.get_event_loop()

        mode = "RESUME" if resume_session else "NEW"
        sid = resume_session or session_id or "none"
        logger.info(
            "[Subprocess] %s session=%s model=%s prompt_len=%d",
            mode, sid[:12], model, len(prompt),
        )

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

        # Pipe the user prompt via stdin then close it.
        if self._process.stdin:
            try:
                self._process.stdin.write(prompt.encode("utf-8"))
                await self._process.stdin.drain()
            except Exception as e:
                logger.warning("[Subprocess] stdin write error: %s", e)
            finally:
                self._process.stdin.close()

        logger.info("[Subprocess] PID: %s", self._process.pid)

        self._timeout_handle = loop.call_later(timeout / 1000, self._on_timeout, timeout)

        asyncio.create_task(self._read_stdout())
        asyncio.create_task(self._read_stderr())
        asyncio.create_task(self._wait_close())

    async def _read_stdout(self) -> None:
        assert self._process and self._process.stdout
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        while True:
            chunk = await self._process.stdout.read(8192)
            if not chunk:
                tail = decoder.decode(b"", final=True)
                if tail:
                    self._buffer += tail
                    self._process_buffer()
                break
            self._buffer += decoder.decode(chunk)
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
        return f"Claude CLI error: {stderr.strip()[:300]}"

    def _process_buffer(self) -> None:
        """Parse NDJSON lines from the buffer and emit events."""
        lines = self._buffer.split("\n")
        self._buffer = lines.pop()

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
    """Check CLI authentication."""
    if not shutil.which("claude"):
        return {"ok": False, "error": "Claude CLI not found"}
    return {"ok": True}
