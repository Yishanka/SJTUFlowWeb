from __future__ import annotations

import json
from pathlib import Path

import pytest

from sjtuflow.runtime import build_app_context
from sjtuflow.storage.config import Config
from sjtuflow.tools import build_registry
from sjtuflow.tools.media import save_transcript
from sjtuflow.tools.registry import ToolContext, run_tool
from sjtuflow.tools.transcripts import (
    delete_transcript,
    list_transcript_metadata,
    read_transcript_by_id,
    refresh_transcript_summary,
    rename_transcript,
    search_transcripts,
)


def _config(tmp_path: Path) -> Config:
    cfg = Config(path=tmp_path / "config.toml")
    cfg.workspace.state_dir = str(tmp_path / "state")
    cfg.workspace.data_dir = str(tmp_path / "data")
    return cfg


def _app(tmp_path: Path):
    return build_app_context(_config(tmp_path), cwd=tmp_path)


def test_search_returns_metadata_and_snippets_without_full_content(tmp_path):
    app = _app(tmp_path)
    save_transcript(
        app,
        title="Search Lecture",
        description="Algorithms",
        segments=[
            {"start": 0, "end": 3, "text": "opening remarks"},
            {"start": 3, "end": 9, "text": "the teacher mentioned attendance check near the end"},
        ],
    )

    results = search_transcripts(app, "attendance check")

    assert len(results) == 1
    assert results[0]["title"] == "Search Lecture"
    assert "snippet" in results[0]
    assert "attendance check" in results[0]["snippet"]
    assert "content" not in results[0]
    assert "segments" not in results[0]


def test_refresh_summary_cache_updates_listing_description(tmp_path):
    app = _app(tmp_path)
    save_transcript(app, title="Summary Lecture", content="long body that should not be the final summary")
    item = next(item for item in list_transcript_metadata(app) if item["title"] == "Summary Lecture")

    refreshed = refresh_transcript_summary(app, item["id"], "Short cached summary")
    listed = next(item for item in list_transcript_metadata(app) if item["title"] == "Summary Lecture")

    assert refreshed["summary_cached"] is True
    assert listed["description"] == "Short cached summary"


def test_rename_transcript_moves_pair_and_updates_titles(tmp_path):
    app = _app(tmp_path)
    save_transcript(app, title="Old Lecture", segments=[{"start": 0, "end": 3, "text": "body"}])
    item = next(item for item in list_transcript_metadata(app) if item["title"] == "Old Lecture")

    renamed = rename_transcript(app, item["id"], "New Lecture")

    assert renamed["title"] == "New Lecture"
    assert Path(renamed["path"]).name == "new-lecture.json"
    assert (Path(renamed["path"]).with_suffix(".md")).exists()
    assert not (Path(renamed["path"]).parent / "old-lecture.json").exists()
    payload = json.loads(Path(renamed["path"]).read_text(encoding="utf-8"))
    assert payload["metadata"]["title"] == "New Lecture"
    markdown = Path(renamed["path"]).with_suffix(".md").read_text(encoding="utf-8")
    assert "title: New Lecture" in markdown
    assert "# New Lecture" in markdown


def test_delete_transcript_removes_json_markdown_and_cache(tmp_path):
    app = _app(tmp_path)
    save_transcript(app, title="Delete Lecture", content="remove me")
    item = next(item for item in list_transcript_metadata(app) if item["title"] == "Delete Lecture")
    refresh_transcript_summary(app, item["id"], "cached")

    deleted = delete_transcript(app, item["id"])

    assert deleted["ok"] is True
    for deleted_path in deleted["deleted_paths"]:
        assert not Path(deleted_path).exists()
    with pytest.raises(FileNotFoundError):
        read_transcript_by_id(app, item["id"])


def test_transcript_tools_registered_with_confirmation_flags(tmp_path):
    app = _app(tmp_path)
    save_transcript(app, title="Tool Search", content="needle")
    registry = build_registry()
    names = {tool.name for tool in registry.list()}

    assert {
        "transcripts.search",
        "transcripts.refresh_summary",
        "transcripts.delete",
        "transcripts.rename",
    } <= names
    assert registry.get("transcripts.search").requires_confirmation is False
    assert registry.get("transcripts.refresh_summary").requires_confirmation is True
    assert registry.get("transcripts.delete").requires_confirmation is True
    assert registry.get("transcripts.rename").requires_confirmation is True

    result = run_tool(
        registry.get("transcripts.search"),
        ToolContext(app=app, interactive=False),
        {"query": "needle"},
    )
    assert result.ok is True
    assert result.data[0]["title"] == "Tool Search"
