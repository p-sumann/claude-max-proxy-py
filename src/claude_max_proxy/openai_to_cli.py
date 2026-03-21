"""Convert OpenAI chat request format to Claude CLI input."""

from __future__ import annotations

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


def messages_to_prompt(messages: list[dict[str, Any]]) -> str:
    """Flatten OpenAI messages into a single prompt string.

    The CLI expects one prompt, not a conversation, so we wrap
    each role into labeled blocks to preserve context.
    """
    parts: list[str] = []

    for msg in messages:
        role = msg.get("role", "")
        content = _extract_text(msg.get("content"))

        if role == "system":
            parts.append(f"<system>\n{content}\n</system>\n")
        elif role == "user":
            parts.append(content)
        elif role == "assistant":
            parts.append(f"<previous_response>\n{content}\n</previous_response>\n")

    return "\n".join(parts).strip()


@dataclass
class CLIInput:
    """Parsed input ready to pass to the subprocess."""
    prompt: str
    model: str
    session_id: str | None = None


def openai_to_cli(request: dict[str, Any]) -> CLIInput:
    """Convert an OpenAI chat request body to CLI input."""
    return CLIInput(
        prompt=messages_to_prompt(request.get("messages", [])),
        model=extract_model(request.get("model", "claude-sonnet-4")),
        session_id=request.get("user"),
    )
