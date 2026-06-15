"""Tests for Workstream B: media extraction and transcript tools.

ffmpeg/ffprobe and the Whisper model are mocked at the boundary so the suite
runs anywhere (CI, the integrator's machine) without needing the real binaries
installed, while still exercising the actual media-pipeline code paths.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sjtuflow.runtime import build_app_context
from sjtuflow.services.jobs import JobManager
from sjtuflow.services.local_app import LocalAppService
from sjtuflow.storage.config import Config
from sjtuflow.tools import build_registry
from sjtuflow.tools import media as media_mod
from sjtuflow.tools.media import (
    MediaError,
    TranscriptResult,
    TranscriptSegment,
    canvas_media_access_hint,
    probe_media,
    save_transcript,
    transcribe_media,
    transcribe_media_and_save,
    transcribe_stream_to_transcript,
)
from sjtuflow.tools.registry import ToolContext, run_tool


def _config(tmp_path: Path) -> Config:
    cfg = Config(path=tmp_path / "config.toml")
    cfg.workspace.state_dir = str(tmp_path / "state")
    cfg.workspace.data_dir = str(tmp_path / "data")
    return cfg


def _app(tmp_path: Path):
    return build_app_context(_config(tmp_path), cwd=tmp_path)


def _touch(path: Path, data: bytes = b"binary") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def fake_ffprobe(monkeypatch):
    """Patch the ffprobe subprocess call to return a canned JSON payload."""

    payload = {
        "format": {"format_name": "mov,mp4", "duration": "65.0", "bit_rate": "128000"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264"},
            {"codec_type": "audio", "codec_name": "aac", "sample_rate": "44100", "channels": 2},
        ],
    }

    class _Completed:
        stdout = json.dumps(payload)

    monkeypatch.setattr(media_mod, "_run", lambda cmd, timeout=120: _Completed())


@pytest.fixture
def fake_pipeline(monkeypatch):
    """Patch ffmpeg extraction and the Whisper backend for transcribe tests."""

    def fake_extract(app, path, out_dir=None, *, sample_rate=16000, overwrite=True):
        wav = Path(path).with_suffix(".wav")
        wav.write_bytes(b"\x00\x00")
        return {"source": path, "output": str(wav), "sample_rate": sample_rate, "channels": 1, "size_bytes": 2}

    def fake_whisper(audio_path, language, model_size):
        return TranscriptResult(
            segments=[
                TranscriptSegment(0.0, 5.0, "hello world"),
                TranscriptSegment(5.0, 10.0, "second segment"),
            ],
            language=language or "en",
            provider="local-whisper",
        )

    monkeypatch.setattr(media_mod, "extract_audio", fake_extract)
    monkeypatch.setattr(media_mod, "_transcribe_faster_whisper", fake_whisper)


# --------------------------------------------------------------------------- #
# probe
# --------------------------------------------------------------------------- #


def test_probe_parses_ffprobe_output(tmp_path, fake_ffprobe):
    app = _app(tmp_path)
    media = _touch(tmp_path / "data" / "clip.mp4")
    info = probe_media(app, str(media))
    assert info["filename"] == "clip.mp4"
    assert info["duration_seconds"] == 65.0
    assert info["has_audio"] is True
    assert info["has_video"] is True
    assert info["audio_codec"] == "aac"


def test_probe_missing_file_raises(tmp_path):
    app = _app(tmp_path)
    with pytest.raises(FileNotFoundError):
        probe_media(app, str(tmp_path / "nope.mp4"))


def test_canvas_media_access_hint_flags_login_session():
    hint = canvas_media_access_hint("https://oc.sjtu.edu.cn/courses/123/external_tools/456")
    assert hint["requires_browser_login"] is True
    assert hint["canvas_token_supported"] is False
    assert hint["video_saved_locally"] is False
    assert hint["transcript_saved_by_default"] is True
    assert "Canvas API token" in hint["message"]
    assert "登录态" in hint["message"] or "logged in" in hint["message"].lower()
    assert hint["is_external_tool"] is True


# --------------------------------------------------------------------------- #
# transcribe
# --------------------------------------------------------------------------- #


def test_transcribe_produces_timed_segments(tmp_path, fake_pipeline):
    app = _app(tmp_path)
    media = _touch(tmp_path / "data" / "lecture.mp4")
    result = transcribe_media(app, str(media), provider="local-whisper")
    data = result.to_dict()
    assert data["provider"] == "local-whisper"
    assert len(data["segments"]) == 2
    first = data["segments"][0]
    assert first["start"] == 0.0
    assert first["end"] > first["start"]
    assert data["source"] == str(media)
    assert data["duration_seconds"] == 10.0


def test_transcribe_unknown_provider(tmp_path, fake_pipeline):
    app = _app(tmp_path)
    media = _touch(tmp_path / "data" / "x.wav")
    with pytest.raises(MediaError):
        transcribe_media(app, str(media), provider="does-not-exist")


def test_transcribe_progress_callback(tmp_path, fake_pipeline):
    app = _app(tmp_path)
    media = _touch(tmp_path / "data" / "lecture.mp4")
    seen: list[float] = []
    transcribe_media(app, str(media), provider="local-whisper", progress=lambda f, m: seen.append(f))
    assert seen and seen[-1] >= 0.9


def test_transcribe_media_and_save_defaults_to_library(tmp_path, fake_pipeline):
    app = _app(tmp_path)
    media = _touch(tmp_path / "data" / "lecture.mp4")
    meta = transcribe_media_and_save(app, str(media), title="Lecture Save", provider="local-whisper")
    assert Path(meta["path"]).exists()
    assert meta["title"] == "Lecture Save"
    assert meta["segment_count"] == 2
    assert meta["transcript"]["segments"][0]["text"] == "hello world"


def test_stream_transcribe_saves_transcript_without_persisting_media(tmp_path, monkeypatch):
    app = _app(tmp_path)

    def fake_run(cmd, *, timeout=1800):
        output = Path(cmd[-1])
        output.write_bytes(b"\x00\x00")

        class _Completed:
            stdout = ""

        return _Completed()

    def fake_whisper(audio_path, language, model_size):
        return TranscriptResult(
            segments=[TranscriptSegment(0.0, 4.0, "老师提到了签到")],
            language=language or "zh",
            provider="local-whisper",
        )

    monkeypatch.setattr(media_mod, "_run", fake_run)
    monkeypatch.setattr(media_mod, "_transcribe_faster_whisper", fake_whisper)

    meta = transcribe_stream_to_transcript(
        app,
        "https://media.example.test/course/playlist.m3u8",
        "Today Lecture",
        provider="local-whisper",
        request_headers={"Cookie": "session=secret"},
    )

    assert Path(meta["path"]).exists()
    assert meta["source"] == "https://media.example.test/course/playlist.m3u8"
    assert meta["video_saved_locally"] is False
    assert meta["segment_count"] == 1
    assert not list((tmp_path / "state" / "cache" / "media-streams").glob("*.wav"))

    payload = json.loads(Path(meta["path"]).read_text(encoding="utf-8"))
    assert payload["segments"][0]["text"] == "老师提到了签到"
    assert payload["metadata"]["source"] == "https://media.example.test/course/playlist.m3u8"


def test_stream_transcribe_requires_http_url(tmp_path):
    app = _app(tmp_path)
    with pytest.raises(MediaError):
        transcribe_stream_to_transcript(app, "file:///tmp/video.mp4", "Bad Stream")


# --------------------------------------------------------------------------- #
# save_transcript
# --------------------------------------------------------------------------- #


def test_save_transcript_writes_json_and_markdown(tmp_path):
    app = _app(tmp_path)
    segments = [
        {"start": 0.0, "end": 5.0, "text": "Hello world"},
        {"start": 5.0, "end": 10.0, "text": "Second segment"},
    ]
    meta = save_transcript(
        app,
        title="Lecture 03",
        source="/path/to/video.mp4",
        description="Test lecture",
        segments=segments,
        language="en",
    )
    json_path = Path(meta["path"])
    md_path = Path(meta["markdown_path"])
    assert json_path.exists() and json_path.suffix == ".json"
    assert md_path.exists() and md_path.suffix == ".md"
    assert meta["segment_count"] == 2

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["metadata"]["title"] == "Lecture 03"
    assert payload["metadata"]["duration_seconds"] == 10.0
    assert len(payload["segments"]) == 2
    assert payload["segments"][0]["start"] == 0.0
    assert payload["segments"][0]["text"] == "Hello world"

    markdown = md_path.read_text(encoding="utf-8")
    assert "Lecture 03" in markdown
    assert "00:00:00" in markdown


def test_save_transcript_no_overwrite(tmp_path):
    app = _app(tmp_path)
    save_transcript(app, title="Dup", content="first")
    with pytest.raises(FileExistsError):
        save_transcript(app, title="Dup", content="second")
    save_transcript(app, title="Dup", content="third", overwrite=True)


def test_saved_transcript_listed_metadata_first(tmp_path):
    """Saved transcripts appear in the listing, and the list never carries the
    full content (Workstream B/C contract)."""
    app = _app(tmp_path)
    save_transcript(
        app,
        title="Algo Lecture",
        segments=[{"start": 0, "end": 3, "text": "secret body text"}],
    )
    from sjtuflow.tools.transcripts import list_transcript_metadata

    items = list_transcript_metadata(app)
    assert any(item["title"] == "Algo Lecture" for item in items)
    for item in items:
        assert "content" not in item


def test_saved_transcript_json_md_pair_not_duplicated(tmp_path):
    """A media save writes both .json and .md; the listing must show one entry,
    preferring the canonical JSON."""
    app = _app(tmp_path)
    save_transcript(app, title="Paired Lecture", segments=[{"start": 0, "end": 3, "text": "body"}])
    from sjtuflow.tools.transcripts import list_transcript_metadata

    items = [item for item in list_transcript_metadata(app) if item["title"] == "Paired Lecture"]
    assert len(items) == 1
    assert items[0]["path"].endswith(".json")


# --------------------------------------------------------------------------- #
# tool registration
# --------------------------------------------------------------------------- #


def test_media_tools_registered():
    registry = build_registry()
    names = {tool.name for tool in registry.list()}
    assert {
        "media.canvas_access_hint",
        "media.probe",
        "media.extract_audio",
        "media.transcribe",
        "media.transcribe_and_save",
        "media.transcribe_stream",
        "media.save_transcript",
    } <= names


def test_media_transcribe_tool_via_registry(tmp_path, fake_pipeline):
    app = _app(tmp_path)
    media = _touch(tmp_path / "data" / "tool.mp4")
    registry = build_registry()
    spec = registry.get("media.transcribe")
    result = run_tool(spec, ToolContext(app=app, interactive=False), {"path": str(media)})
    assert result.ok is True
    assert result.data["segments"]


def test_media_risk_levels():
    registry = build_registry()
    assert registry.get("media.extract_audio").requires_confirmation is True
    assert registry.get("media.save_transcript").requires_confirmation is True
    assert registry.get("media.transcribe_and_save").requires_confirmation is True
    assert registry.get("media.transcribe_stream").requires_confirmation is True
    assert registry.get("media.probe").risk_level == "read"
    assert registry.get("media.transcribe").risk_level == "read"


# --------------------------------------------------------------------------- #
# JobManager
# --------------------------------------------------------------------------- #


def test_job_manager_sync_success():
    manager = JobManager()
    job = manager.run_sync("test", lambda handle: {"value": 42})
    assert job["status"] == "succeeded"
    assert job["result"] == {"value": 42}
    assert job["progress"] == 1.0
    assert manager.get(job["id"])["status"] == "succeeded"


def test_job_manager_sync_failure_captured():
    manager = JobManager()

    def boom(handle):
        raise ValueError("kaboom")

    job = manager.run_sync("test", boom)
    assert job["status"] == "failed"
    assert "kaboom" in job["error"]


def test_job_manager_async_completes():
    import time

    manager = JobManager()

    def worker(handle):
        handle.update(progress=0.5, message="halfway")
        return "done"

    submitted = manager.submit("test", worker)
    for _ in range(200):
        current = manager.get(submitted["id"])
        if current["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.01)
    final = manager.get(submitted["id"])
    assert final["status"] == "succeeded"
    assert final["result"] == "done"


def test_job_manager_unknown_id():
    manager = JobManager()
    with pytest.raises(KeyError):
        manager.get("missing")


# --------------------------------------------------------------------------- #
# LocalAppService integration (no HTTP layer)
# --------------------------------------------------------------------------- #


def test_service_transcribe_and_save_pipeline(tmp_path, monkeypatch, fake_pipeline):
    monkeypatch.setenv("SJTU_FLOW_CONFIG", str(tmp_path / "config.toml"))
    service = LocalAppService(cwd=tmp_path)
    service.update_config(
        {
            "workspace.state_dir": str(tmp_path / "state"),
            "workspace.data_dir": str(tmp_path / "data"),
        }
    )
    media = _touch(tmp_path / "data" / "session.mp4")

    job = service.media_transcribe(str(media), provider="local-whisper", sync=True)
    assert job["status"] == "succeeded"
    segments = job["result"]["segments"]
    assert segments

    saved = service.media_save_transcript(
        title="Session Recording",
        source=str(media),
        segments=segments,
        language=job["result"].get("language"),
    )
    assert Path(saved["path"]).exists()

    listed = service.list_transcripts()
    assert any(item["title"] == "Session Recording" for item in listed)


def test_service_transcribe_and_save_default_pipeline(tmp_path, monkeypatch, fake_pipeline):
    monkeypatch.setenv("SJTU_FLOW_CONFIG", str(tmp_path / "config.toml"))
    service = LocalAppService(cwd=tmp_path)
    service.update_config(
        {
            "workspace.state_dir": str(tmp_path / "state"),
            "workspace.data_dir": str(tmp_path / "data"),
        }
    )
    media = _touch(tmp_path / "data" / "default-save.mp4")

    job = service.media_transcribe_and_save(
        str(media),
        title="Default Save Recording",
        provider="local-whisper",
        sync=True,
    )
    assert job["status"] == "succeeded"
    assert Path(job["result"]["path"]).exists()
    assert job["result"]["title"] == "Default Save Recording"


def test_service_canvas_access_hint(tmp_path, monkeypatch):
    monkeypatch.setenv("SJTU_FLOW_CONFIG", str(tmp_path / "config.toml"))
    service = LocalAppService(cwd=tmp_path)
    service.update_config(
        {
            "workspace.state_dir": str(tmp_path / "state"),
            "workspace.data_dir": str(tmp_path / "data"),
        }
    )
    hint = service.media_canvas_access_hint("https://oc.sjtu.edu.cn/courses/123/external_tools/456")
    assert hint["status"] == "requires_browser_session"
