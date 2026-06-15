from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sjtuflow.llm.base import Message
from sjtuflow.utils.text import sanitize_slug, truncate_text


@dataclass
class StoredSession:
    id: str
    created_at: str
    updated_at: str
    title: str
    briefing: dict[str, Any] | None
    messages: list[Message]

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "title": self.title,
            "briefing": self.briefing,
            "messages": self.messages,
        }

    def to_summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "title": self.title,
            "message_count": len([message for message in self.messages if message.get("role") != "system"]),
        }


class SessionStore:
    def __init__(self, state_dir: Path) -> None:
        self.root = state_dir / "sessions"
        self.root.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[dict[str, Any]]:
        sessions: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                sessions.append(self.read(path.stem).to_summary())
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        return sessions

    def read(self, session_id: str) -> StoredSession:
        path = self.path_for(session_id)
        if not path.exists():
            raise FileNotFoundError(f"Session not found: {session_id}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid session payload: {session_id}")
        messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
        return StoredSession(
            id=str(payload.get("id") or session_id),
            created_at=str(payload.get("created_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            title=str(payload.get("title") or "Untitled session"),
            briefing=payload.get("briefing") if isinstance(payload.get("briefing"), dict) else None,
            messages=messages,
        )

    def save(
        self,
        *,
        session_id: str,
        created_at: str,
        updated_at: str,
        messages: list[Message],
        briefing: dict[str, Any] | None,
        title: str | None = None,
    ) -> StoredSession:
        stored = StoredSession(
            id=session_id,
            created_at=created_at,
            updated_at=updated_at,
            title=title or infer_title(messages),
            briefing=briefing,
            messages=messages,
        )
        self.path_for(session_id).write_text(json.dumps(stored.to_payload(), ensure_ascii=False, indent=2), encoding="utf-8")
        return stored

    def delete(self, session_id: str) -> dict[str, Any]:
        path = self.path_for(session_id)
        if not path.exists():
            raise FileNotFoundError(f"Session not found: {session_id}")
        path.unlink()
        return {"ok": True, "id": session_id}

    def path_for(self, session_id: str) -> Path:
        return self.root / f"{sanitize_slug(session_id, fallback='session')}.json"


def infer_title(messages: list[Message]) -> str:
    for message in messages:
        if message.get("role") == "user":
            content = str(message.get("content") or "").strip()
            if content:
                return truncate_text(" ".join(content.split()), 80)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return f"New session {now}"
