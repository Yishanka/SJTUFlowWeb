from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


SECRET_KEYS = {
    "api_key",
    "access_token",
    "token",
    "password",
    "authorization",
    "cookie",
}


def truncate_text(value: str, limit: int = 12000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n...[truncated {len(value) - limit} chars]"


def sanitize_filename(value: str, fallback: str = "untitled") -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", value).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or fallback


def sanitize_slug(value: str, fallback: str = "skill") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
    return cleaned or fallback


def short_hash(value: bytes | str, length: int = 10) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()[:length]


def json_dumps(value: Any, *, limit: int | None = None) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    return truncate_text(text, limit) if limit else text


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if key.lower() in SECRET_KEYS:
                redacted[key] = "***"
            else:
                redacted[key] = redact(item)
        return redacted
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False

