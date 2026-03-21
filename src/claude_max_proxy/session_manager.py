"""Session manager — maps conversation IDs to Claude CLI session IDs.

Persists to ~/.claude-code-cli-sessions.json with a 24h TTL.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SESSION_FILE = Path(os.environ.get("HOME", "/tmp")) / ".claude-code-cli-sessions.json"
SESSION_TTL_MS = 24 * 60 * 60 * 1000  # 24 hours


def _now_ms() -> int:
    return int(time.time() * 1000)


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}
        self._loaded: bool = False

    async def load(self) -> None:
        if self._loaded:
            return
        try:
            data = SESSION_FILE.read_text()
            self._sessions = json.loads(data)
            self._loaded = True
            logger.info("[SessionManager] Loaded %d sessions", len(self._sessions))
        except (FileNotFoundError, json.JSONDecodeError):
            self._sessions = {}
            self._loaded = True

    async def save(self) -> None:
        try:
            SESSION_FILE.write_text(json.dumps(self._sessions, indent=2))
        except Exception as e:
            logger.error("[SessionManager] Save error: %s", e)

    def get_or_create(self, external_id: str, model: str = "sonnet") -> str:
        existing = self._sessions.get(external_id)
        if existing:
            existing["lastUsedAt"] = _now_ms()
            existing["model"] = model
            return existing["claudeSessionId"]

        claude_session_id = str(uuid.uuid4())
        self._sessions[external_id] = {
            "externalId": external_id,
            "claudeSessionId": claude_session_id,
            "createdAt": _now_ms(),
            "lastUsedAt": _now_ms(),
            "model": model,
        }
        logger.info("[SessionManager] Created: %s -> %s", external_id, claude_session_id)
        try:
            asyncio.create_task(self.save())
        except RuntimeError:
            pass
        return claude_session_id

    def get(self, external_id: str) -> dict[str, Any] | None:
        return self._sessions.get(external_id)

    def delete(self, external_id: str) -> bool:
        if external_id in self._sessions:
            del self._sessions[external_id]
            try:
                asyncio.create_task(self.save())
            except RuntimeError:
                pass
            return True
        return False

    def cleanup(self) -> int:
        cutoff = _now_ms() - SESSION_TTL_MS
        expired = [k for k, v in self._sessions.items() if v.get("lastUsedAt", 0) < cutoff]
        for key in expired:
            del self._sessions[key]
        if expired:
            logger.info("[SessionManager] Cleaned up %d expired sessions", len(expired))
            try:
                asyncio.create_task(self.save())
            except RuntimeError:
                pass
        return len(expired)

    @property
    def size(self) -> int:
        return len(self._sessions)


session_manager = SessionManager()
