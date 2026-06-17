from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sjtuflow.runtime import AppContext
from sjtuflow.tools.registry import ToolContext, ToolRegistry
from sjtuflow.utils.text import is_relative_to
from sjtuflow.utils.text import sanitize_filename, truncate_text


TRANSCRIPT_EXTENSIONS = {".md", ".txt", ".json"}
SUMMARY_CACHE_DIRNAME = ".summary-cache"


def transcript_root(app: AppContext) -> Path:
    root = app.workspace.data_dir / "transcripts"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _stable_id(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _summary_cache_path(root: Path, path: Path) -> Path:
    return root / SUMMARY_CACHE_DIRNAME / f"{_stable_id(path)}.summary"


def _read_summary_cache(root: Path, path: Path) -> str:
    cache_path = _summary_cache_path(root, path)
    if not cache_path.exists():
        return ""
    try:
        if cache_path.stat().st_mtime < path.stat().st_mtime:
            return ""
        return truncate_text(" ".join(_read_text(cache_path).split()), 500)
    except OSError:
        return ""


def _write_summary_cache(app: AppContext, root: Path, path: Path, summary: str) -> str:
    cache_dir = app.workspace.assert_safe_write_path(root / SUMMARY_CACHE_DIRNAME)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = app.workspace.assert_safe_write_path(_summary_cache_path(root, path))
    normalized = truncate_text(" ".join(summary.split()), 500)
    cache_path.write_text(normalized, encoding="utf-8")
    return normalized


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
    description = json_meta.get("description") or _read_summary_cache(root, path) or truncate_text(" ".join(text.split()), 500)
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
        if any(part == SUMMARY_CACHE_DIRNAME for part in path.parts):
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
    item, path = _find_transcript(app, root, transcript_id)
    return {**item, "content": _read_text(path)}


def _find_transcript(app: AppContext, root: Path, transcript_id: str) -> tuple[dict[str, Any], Path]:
    for item in list_transcript_metadata(app):
        if item["id"] == transcript_id or item["relative_path"] == transcript_id:
            path = app.workspace.resolve_read_path(item["path"])
            resolved_root = root.resolve()
            if not is_relative_to(path.resolve(), resolved_root):
                raise ValueError(f"Transcript path escapes library root: {transcript_id}")
            return item, path
    raise FileNotFoundError(f"Transcript not found: {transcript_id}")


def _paired_transcript_paths(root: Path, path: Path) -> list[Path]:
    paths: list[Path] = []
    for suffix in (".json", ".md", ".txt"):
        candidate = path.with_suffix(suffix)
        if candidate.exists() and candidate.is_file() and is_relative_to(candidate.resolve(), root.resolve()):
            paths.append(candidate)
    if path not in paths and path.exists() and path.is_file():
        paths.append(path)
    return sorted(set(paths))


def _content_for_search(path: Path) -> str:
    text = _read_text(path)
    if path.suffix.lower() != ".json":
        return text
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    if not isinstance(payload, dict):
        return text
    pieces: list[str] = []
    if isinstance(payload.get("text"), str):
        pieces.append(payload["text"])
    segments = payload.get("segments")
    if isinstance(segments, list):
        for segment in segments:
            if isinstance(segment, dict) and isinstance(segment.get("text"), str):
                pieces.append(segment["text"])
    return "\n".join(piece for piece in pieces if piece.strip()) or text


def _snippet(text: str, query: str, *, max_chars: int = 220) -> str:
    compact = " ".join(text.split())
    if not compact:
        return ""
    index = compact.lower().find(query.lower())
    if index < 0:
        return truncate_text(compact, max_chars)
    radius = max(20, max_chars // 2)
    start = max(0, index - radius)
    end = min(len(compact), index + len(query) + radius)
    prefix = "..." if start else ""
    suffix = "..." if end < len(compact) else ""
    return prefix + compact[start:end].strip() + suffix


def search_transcripts(app: AppContext, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    q = " ".join(query.split()).lower()
    if not q:
        raise ValueError("query is required")
    root = transcript_root(app)
    results: list[dict[str, Any]] = []
    for item in list_transcript_metadata(app):
        path = app.workspace.resolve_read_path(item["path"])
        haystack = "\n".join(
            str(item.get(field) or "") for field in ("title", "description", "source", "relative_path")
        )
        content = ""
        matched_fields: list[str] = []
        if q in haystack.lower():
            matched_fields.append("metadata")
        else:
            content = _content_for_search(path)
            if q in content.lower():
                matched_fields.append("content")
        if not matched_fields:
            continue
        snippet_source = content if content else haystack
        result = {key: value for key, value in item.items() if key != "content"}
        result["matched_fields"] = matched_fields
        result["snippet"] = _snippet(snippet_source, query)
        results.append(result)
        if len(results) >= limit:
            break
    return results


def refresh_transcript_summary(app: AppContext, transcript_id: str, summary: str | None = None) -> dict[str, Any]:
    root = transcript_root(app)
    item, path = _find_transcript(app, root, transcript_id)
    generated = summary
    if generated is None or not generated.strip():
        generated = truncate_text(" ".join(_content_for_search(path).split()), 500)
    description = _write_summary_cache(app, root, path, generated)
    app.audit.record("transcript_summary_refresh", {"id": item["id"], "path": str(path)})
    return {**item, "description": description, "summary_cached": True}


def delete_transcript(app: AppContext, transcript_id: str) -> dict[str, Any]:
    root = transcript_root(app)
    item, path = _find_transcript(app, root, transcript_id)
    deleted: list[str] = []
    for target in _paired_transcript_paths(root, path):
        safe_target = app.workspace.assert_safe_write_path(target)
        safe_target.unlink()
        deleted.append(str(safe_target))
    cache_path = _summary_cache_path(root, path)
    if cache_path.exists():
        app.workspace.assert_safe_write_path(cache_path).unlink()
        deleted.append(str(cache_path))
    app.audit.record("transcript_delete", {"id": item["id"], "paths": deleted})
    return {"ok": True, "id": item["id"], "deleted_paths": deleted}


def rename_transcript(app: AppContext, transcript_id: str, title: str, *, overwrite: bool = False) -> dict[str, Any]:
    root = transcript_root(app)
    item, path = _find_transcript(app, root, transcript_id)
    slug = sanitize_filename(title).lower().replace(" ", "-") or "transcript"
    sources = _paired_transcript_paths(root, path)
    targets = [app.workspace.assert_safe_write_path(root / f"{slug}{source.suffix}") for source in sources]
    for target in targets:
        if target.exists() and target not in sources and not overwrite:
            raise FileExistsError(f"{target} exists; set overwrite=true to replace it")
    moved: list[dict[str, str]] = []
    for source, target in zip(sources, targets):
        safe_source = app.workspace.assert_safe_write_path(source)
        if target.exists() and target != safe_source and overwrite:
            target.unlink()
        safe_source.replace(target)
        moved.append({"from": str(safe_source), "to": str(target)})
        if target.suffix.lower() == ".json":
            _rename_json_title(target, title)
        elif target.suffix.lower() == ".md":
            _rename_markdown_title(target, title)
    cache_path = _summary_cache_path(root, path)
    if cache_path.exists():
        cache_path.unlink()
    app.audit.record("transcript_rename", {"id": item["id"], "title": title, "paths": moved})
    new_item, _ = _find_transcript(app, root, targets[0].relative_to(root).as_posix())
    return {**new_item, "renamed_paths": moved}


def _rename_json_title(path: Path, title: str) -> None:
    try:
        payload = json.loads(_read_text(path))
    except json.JSONDecodeError:
        return
    if not isinstance(payload, dict):
        return
    metadata = payload.setdefault("metadata", {})
    if isinstance(metadata, dict):
        metadata["title"] = title
    else:
        payload["title"] = title
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _rename_markdown_title(path: Path, title: str) -> None:
    lines = _read_text(path).splitlines()
    for index, line in enumerate(lines[:20]):
        if line.startswith("title: "):
            lines[index] = f"title: {title}"
        elif line.startswith("# "):
            lines[index] = f"# {title}"
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


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
        name="transcripts.search",
        description="Search saved transcript metadata and content. Returns metadata plus short snippets, never full transcript content.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def search(ctx: ToolContext, query: str, limit: int = 20):
        return search_transcripts(ctx.app, query, limit=limit)

    @registry.tool(
        name="transcripts.refresh_summary",
        description="Refresh the cached short summary for a transcript. Returns metadata only.",
        input_schema={
            "type": "object",
            "properties": {
                "transcript_id": {"type": "string"},
                "summary": {"type": "string", "description": "Optional explicit summary; generated from content if omitted."},
            },
            "required": ["transcript_id"],
            "additionalProperties": False,
        },
        risk_level="write",
        requires_confirmation=True,
    )
    def refresh_summary(ctx: ToolContext, transcript_id: str, summary: str | None = None):
        return refresh_transcript_summary(ctx.app, transcript_id, summary)

    @registry.tool(
        name="transcripts.delete",
        description="Delete a transcript from the local library after confirmation, including paired JSON/Markdown files.",
        input_schema={
            "type": "object",
            "properties": {"transcript_id": {"type": "string"}},
            "required": ["transcript_id"],
            "additionalProperties": False,
        },
        risk_level="destructive",
        requires_confirmation=True,
    )
    def delete(ctx: ToolContext, transcript_id: str):
        return delete_transcript(ctx.app, transcript_id)

    @registry.tool(
        name="transcripts.rename",
        description="Rename a transcript after confirmation, including paired JSON/Markdown files and stored title metadata.",
        input_schema={
            "type": "object",
            "properties": {
                "transcript_id": {"type": "string"},
                "title": {"type": "string"},
                "overwrite": {"type": "boolean", "default": False},
            },
            "required": ["transcript_id", "title"],
            "additionalProperties": False,
        },
        risk_level="write",
        requires_confirmation=True,
    )
    def rename(ctx: ToolContext, transcript_id: str, title: str, overwrite: bool = False):
        return rename_transcript(ctx.app, transcript_id, title, overwrite=overwrite)

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
