"""Convert OpenAI chat request format to Claude CLI input."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

MODEL_MAP: dict[str, str] = {
    "claude-opus-4": "opus",
    "claude-sonnet-4": "sonnet",
    "claude-sonnet-5": "sonnet",
    "claude-haiku-4": "haiku",
    # Versioned model names (strands agents proxy mode)
    "claude-opus-4-6": "opus",
    "claude-opus-4-5": "opus",
    "claude-sonnet-4-6": "sonnet",
    "claude-sonnet-4-5": "sonnet",
    "claude-haiku-4-5": "haiku",
    # With provider prefix
    "claude-code-cli/claude-opus-4": "opus",
    "claude-code-cli/claude-sonnet-4": "sonnet",
    "claude-code-cli/claude-sonnet-5": "sonnet",
    "claude-code-cli/claude-haiku-4": "haiku",
    "opus": "opus",
    "sonnet": "sonnet",
    "haiku": "haiku",
}


def extract_model(model: str) -> str:
    """Resolve model name to a Claude CLI alias (opus/sonnet/haiku)."""
    if model in MODEL_MAP:
        return MODEL_MAP[model]

    stripped = model.replace("claude-code-cli/", "", 1)
    if stripped in MODEL_MAP:
        return MODEL_MAP[stripped]

    return "opus"


def _extract_text(content: Any) -> str:
    """Extract plain text from message content.

    OpenAI API allows content to be either:
      - a string: "Hello!"
      - an array of content blocks: [{"type": "text", "text": "Hello!"}, ...]
      - None
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block["text"]
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content) if content is not None else ""


def _split_messages(messages: list[dict[str, Any]]) -> tuple[str, str]:
    """Split OpenAI messages into (system_prompt, user_prompt).

    Returns:
        system_prompt: Combined system messages (passed via --system-prompt for caching)
        user_prompt:   The last user message only (for --resume mode, previous
                       messages are already in the session history)
    """
    system_parts: list[str] = []
    last_user_msg: str = ""

    for msg in messages:
        role = msg.get("role", "")
        content = _extract_text(msg.get("content"))
        if not content:
            continue

        if role == "system":
            system_parts.append(content)
        elif role == "user":
            last_user_msg = content  # keep overwriting — we want the LAST one

    return "\n\n".join(system_parts), last_user_msg


def _build_full_prompt(messages: list[dict[str, Any]]) -> str:
    """Build full conversation prompt (for first call with no session history)."""
    parts: list[str] = []

    for msg in messages:
        role = msg.get("role", "")
        content = _extract_text(msg.get("content"))
        if not content:
            continue

        if role == "system":
            # System messages are passed via --system-prompt, skip here
            continue
        elif role == "user":
            parts.append(content)
        elif role == "assistant":
            parts.append(f"<previous_response>\n{content}\n</previous_response>")

    return "\n\n".join(parts)


@dataclass
class CLIInput:
    """Parsed input ready to pass to the subprocess."""
    prompt: str                    # user prompt (piped via stdin)
    system_prompt: str = ""        # system messages (--system-prompt flag)
    model: str = "sonnet"          # CLI model alias
    session_id: str | None = None  # OpenAI 'user' field
    cwd: str | None = None         # working directory
    is_first_call: bool = True     # True = send full conversation; False = last msg only


def openai_to_cli(request: dict[str, Any], *, is_resumed: bool = False) -> CLIInput:
    """Convert an OpenAI chat request body to CLI input.

    Args:
        request:    The OpenAI-format request body.
        is_resumed: If True, the session already has history — only send
                    the last user message. If False, send the full conversation.

    System messages are always extracted separately for --system-prompt.
    """
    cwd = request.get("cwd") or os.getenv("CLAUDE_CWD") or None
    messages = request.get("messages", [])

    system_prompt, last_user_msg = _split_messages(messages)

    if is_resumed:
        # Session already has conversation history — just send the new message
        prompt = last_user_msg
    else:
        # First call — send the full conversation (minus system messages)
        prompt = _build_full_prompt(messages)

    return CLIInput(
        prompt=prompt,
        system_prompt=system_prompt,
        model=extract_model(request.get("model", "claude-sonnet-4")),
        session_id=request.get("user"),
        cwd=cwd,
        is_first_call=not is_resumed,
    )
