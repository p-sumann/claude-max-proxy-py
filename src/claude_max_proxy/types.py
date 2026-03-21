"""Type guards for Claude Code CLI JSON streaming output.

The CLI emits NDJSON (one JSON object per line) with these message types:
  - system       (init, hook_started, hook_response)
  - assistant    (full assistant message with content blocks)
  - result       (final result after processing)
  - stream_event (wraps events like content_block_delta)
"""

from __future__ import annotations

from typing import Any


def is_assistant_message(msg: dict[str, Any]) -> bool:
    return msg.get("type") == "assistant"


def is_result_message(msg: dict[str, Any]) -> bool:
    return msg.get("type") == "result"


def is_stream_event(msg: dict[str, Any]) -> bool:
    return msg.get("type") == "stream_event"


def is_content_delta(msg: dict[str, Any]) -> bool:
    return is_stream_event(msg) and msg.get("event", {}).get("type") == "content_block_delta"


def is_system_init(msg: dict[str, Any]) -> bool:
    return msg.get("type") == "system" and msg.get("subtype") == "init"
