from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sjtuflow.utils.text import redact


class AuditLogger:
    def __init__(self, state_dir: Path) -> None:
        self.audit_dir = state_dir / "audit"
        self.audit_dir.mkdir(parents=True, exist_ok=True)

    def record(self, event_type: str, payload: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        path = self.audit_dir / f"{now.date().isoformat()}.jsonl"
        event = {
            "ts": now.isoformat(),
            "event": event_type,
            "payload": redact(payload),
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

