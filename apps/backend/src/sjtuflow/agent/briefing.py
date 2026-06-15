from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sjtuflow.runtime import AppContext


def collect_startup_briefing(app: AppContext) -> dict[str, Any]:
    config = app.config.agent
    briefing: dict[str, Any] = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "urgent": [],
        "upcoming": [],
        "updates": [],
        "warnings": [],
    }
    try:
        assignments = app.canvas.list_upcoming_assignments(window_days=config.briefing_window_days)
        briefing["upcoming"] = assignments[:10]
    except Exception as exc:
        briefing["warnings"].append({"source": "canvas.assignments", "message": str(exc)})

    try:
        announcements = app.canvas.list_recent_announcements(since_days=3, limit=10)
        briefing["updates"] = announcements[:10]
    except Exception as exc:
        briefing["warnings"].append({"source": "canvas.announcements", "message": str(exc)})

    app.audit.record("startup_briefing", briefing)
    return briefing


def briefing_to_text(briefing: dict[str, Any]) -> str:
    lines = ["Startup briefing:"]
    if briefing.get("urgent"):
        lines.append("Urgent:")
        for item in briefing["urgent"]:
            lines.append(f"- {item}")
    if briefing.get("upcoming"):
        lines.append("Upcoming:")
        for item in briefing["upcoming"]:
            if isinstance(item, dict):
                course = item.get("course") or item.get("course_id") or "Canvas"
                name = item.get("name") or item.get("title") or item.get("assignment_id")
                due = item.get("due_at") or ""
                lines.append(f"- {course}: {name} {due}".rstrip())
            else:
                lines.append(f"- {item}")
    if briefing.get("updates"):
        lines.append("Updates:")
        for item in briefing["updates"]:
            if isinstance(item, dict):
                course = item.get("course") or item.get("course_id") or "Canvas"
                title = item.get("title") or item.get("id")
                posted = item.get("posted_at") or ""
                lines.append(f"- {course}: {title} {posted}".rstrip())
            else:
                lines.append(f"- {item}")
    if briefing.get("warnings"):
        lines.append("Warnings:")
        for item in briefing["warnings"]:
            if isinstance(item, dict):
                lines.append(f"- {item.get('source')}: {item.get('message')}")
            else:
                lines.append(f"- {item}")
    return "\n".join(lines)

