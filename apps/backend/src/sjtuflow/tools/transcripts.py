from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sjtuflow.runtime import AppContext
from sjtuflow.tools.registry import ToolContext, ToolRegistry
from sjtuflow.utils.text import sanitize_filename, truncate_text


TRANSCRIPT_EXTENSIONS = {".md", ".txt", ".json"}


def transcript_root(app: AppContext) -> Path:
    root = app.workspace.data_dir / "transcripts"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _stable_id(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _extract_json_metadata(path: Path, text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else payload
    segments = payload.get("segments") if isinstance(payload.get("segments"), list) else []
    duration = None
    if segments:
        last = segments[-1]
        if isinstance(last, dict):
            duration = last.get("end") or last.get("end_seconds")
    return {
        "title": metadata.get("title") or metadata.get("name"),
        "description": metadata.get("description") or metadata.get("summary"),
        "source": metadata.get("source") or metadata.get("source_path") or metadata.get("url"),
        "duration_seconds": metadata.get("duration_seconds") or duration,
    }


def _metadata_for_file(root: Path, path: Path) -> dict[str, Any]:
    text = _read_text(path)
    json_meta = _extract_json_metadata(path, text) if path.suffix.lower() == ".json" else {}
    title = json_meta.get("title") or path.stem.replace("-", " ").replace("_", " ").strip() or path.name
    description = json_meta.get("description") or truncate_text(" ".join(text.split()), 500)
    try:
        relative_path = str(path.relative_to(root))
    except ValueError:
        relative_path = path.name
    return {
        "id": _stable_id(path),
        "title": title,
        "description": description,
        "path": str(path),
        "relative_path": relative_path,
        "source": json_meta.get("source") or "",
        "duration_seconds": json_meta.get("duration_seconds"),
        "updated_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
        "size": path.stat().st_size,
    }


def list_transcript_metadata(app: AppContext) -> list[dict[str, Any]]:
    root = transcript_root(app)
    # Media saves write a <slug>.json (canonical, with segments) plus a
    # <slug>.md (readable) pair. Collapse such pairs to a single entry so the
    # library does not show the same transcript twice; the JSON wins because it
    # carries structured metadata and segments.
    by_stem: dict[Path, Path] = {}
    rank = {".json": 0, ".md": 1, ".txt": 2}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TRANSCRIPT_EXTENSIONS:
            continue
        key = path.with_suffix("")
        current = by_stem.get(key)
        if current is None or rank.get(path.suffix.lower(), 9) < rank.get(current.suffix.lower(), 9):
            by_stem[key] = path
    transcripts: list[dict[str, Any]] = []
    for path in sorted(by_stem.values()):
        transcripts.append(_metadata_for_file(root, path))
    return transcripts


def read_transcript_by_id(app: AppContext, transcript_id: str) -> dict[str, Any]:
    root = transcript_root(app)
    for item in list_transcript_metadata(app):
        if item["id"] == transcript_id or item["relative_path"] == transcript_id:
            path = app.workspace.resolve_read_path(item["path"])
            return {**item, "content": _read_text(path)}
    raise FileNotFoundError(f"Transcript not found: {transcript_id}")


def register_transcript_tools(registry: ToolRegistry) -> None:
    @registry.tool(
        name="transcripts.list",
        description="List saved transcript metadata. Full transcript content is not preloaded; use transcripts.read when needed.",
        risk_level="read",
    )
    def list_transcripts(ctx: ToolContext):
        return list_transcript_metadata(ctx.app)

    @registry.tool(
        name="transcripts.read",
        description="Read a saved transcript by id or relative path.",
        input_schema={
            "type": "object",
            "properties": {"transcript_id": {"type": "string"}},
            "required": ["transcript_id"],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def read_transcript(ctx: ToolContext, transcript_id: str):
        return read_transcript_by_id(ctx.app, transcript_id)

    @registry.tool(
        name="transcripts.save_text",
        description="Save transcript text under the local transcript library after confirmation.",
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "content": {"type": "string"},
                "source": {"type": "string", "description": "Optional source video, audio, URL, or file path."},
                "description": {"type": "string", "description": "Optional short summary for future listing."},
                "overwrite": {"type": "boolean", "default": False},
            },
            "required": ["title", "content"],
            "additionalProperties": False,
        },
        risk_level="write",
        requires_confirmation=True,
    )
    def save_text(
        ctx: ToolContext,
        title: str,
        content: str,
        source: str = "",
        description: str = "",
        overwrite: bool = False,
    ):
        root = transcript_root(ctx.app)
        slug = sanitize_filename(title).lower().replace(" ", "-") or "transcript"
        target = ctx.app.workspace.assert_safe_write_path(root / f"{slug}.md")
        if target.exists() and not overwrite:
            raise FileExistsError(f"{target} exists; set overwrite=true to replace it")
        metadata = [
            "---",
            f"title: {title}",
            f"source: {source}",
            f"description: {description}",
            f"saved_at: {datetime.now(timezone.utc).isoformat()}",
            "---",
            "",
        ]
        target.write_text("\n".join(metadata) + content, encoding="utf-8")
        return _metadata_for_file(root, target)
