"""Convert Claude CLI output to OpenAI-compatible response format."""

from __future__ import annotations

import time
from typing import Any


def normalize_model_name(model: str) -> str:
    """Shorten full model IDs: 'claude-sonnet-4-5-20250929' -> 'claude-sonnet-4'."""
    if "opus" in model:
        return "claude-opus-4"
    if "sonnet" in model:
        return "claude-sonnet-4"
    if "haiku" in model:
        return "claude-haiku-4"
    return model


def extract_text_content(message: dict[str, Any]) -> str:
    """Pull text from an assistant message's content blocks."""
    content_blocks = message.get("message", {}).get("content", [])
    return "".join(
        block.get("text", "")
        for block in content_blocks
        if block.get("type") == "text"
    )


def cli_to_openai_chunk(
    message: dict[str, Any],
    request_id: str,
    is_first: bool = False,
) -> dict[str, Any]:
    """Build an OpenAI streaming chunk from a CLI assistant message."""
    text = extract_text_content(message)
    model = message.get("message", {}).get("model", "claude-sonnet-4")
    stop_reason = message.get("message", {}).get("stop_reason")

    delta: dict[str, str] = {}
    if is_first:
        delta["role"] = "assistant"
    if text:
        delta["content"] = text

    return {
        "id": f"chatcmpl-{request_id}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": normalize_model_name(model),
        "choices": [{"index": 0, "delta": delta, "finish_reason": "stop" if stop_reason else None}],
    }


def create_done_chunk(request_id: str, model: str) -> dict[str, Any]:
    """Build the final streaming chunk with finish_reason: stop."""
    return {
        "id": f"chatcmpl-{request_id}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": normalize_model_name(model),
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }


def cli_result_to_openai(result: dict[str, Any], request_id: str) -> dict[str, Any]:
    """Build a full OpenAI ChatCompletion response from a CLI result event."""
    model_usage = result.get("modelUsage") or {}
    model_name = next(iter(model_usage.keys()), "claude-sonnet-4") if model_usage else "claude-sonnet-4"
    usage = result.get("usage") or {}

    return {
        "id": f"chatcmpl-{request_id}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": normalize_model_name(model_name),
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": result.get("result", "")},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        },
    }
