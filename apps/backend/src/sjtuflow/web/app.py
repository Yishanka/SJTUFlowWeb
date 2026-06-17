from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from sjtuflow.services.local_app import LocalAppService


class ConfigUpdateRequest(BaseModel):
    updates: dict[str, Any] = Field(default_factory=dict)


class CreateSessionRequest(BaseModel):
    run_briefing: bool | None = None


class MessageRequest(BaseModel):
    message: str


class ClearSessionRequest(BaseModel):
    run_briefing: bool = False


class SkillWriteRequest(BaseModel):
    name: str
    content: str
    overwrite: bool = False


class MediaProbeRequest(BaseModel):
    path: str


class MediaAccessHintRequest(BaseModel):
    url: str


class MediaResolveStreamRequest(BaseModel):
    source: str
    request_headers: dict[str, str] | None = None


class MediaResolveCanvasPageRequest(BaseModel):
    url: str
    wait_seconds: int = 45


class MediaFindCanvasPagesRequest(BaseModel):
    course_id: str
    query: str = ""
    wait_seconds: int = 20
    max_candidates: int = 20


class MediaExtractAudioRequest(BaseModel):
    path: str
    out_dir: str | None = None
    sync: bool = False


class MediaTranscribeRequest(BaseModel):
    path: str
    provider: str = "local-whisper"
    language: str | None = None
    sync: bool = False


class MediaTranscribeAndSaveRequest(BaseModel):
    path: str
    title: str | None = None
    provider: str = "local-whisper"
    language: str | None = None
    description: str = ""
    overwrite: bool = False
    sync: bool = False


class MediaTranscribeStreamRequest(BaseModel):
    stream_url: str
    title: str
    provider: str = "local-whisper"
    language: str | None = None
    description: str = ""
    overwrite: bool = False
    sync: bool = False
    request_headers: dict[str, str] | None = None


class MediaTranscribeSourceRequest(BaseModel):
    source: str
    title: str
    provider: str = "local-whisper"
    language: str | None = None
    description: str = ""
    overwrite: bool = False
    sync: bool = False
    request_headers: dict[str, str] | None = None


class MediaTranscribeCanvasPageRequest(BaseModel):
    url: str
    title: str
    provider: str = "local-whisper"
    language: str | None = None
    description: str = ""
    overwrite: bool = False
    wait_seconds: int = 45
    sync: bool = False


class MediaSaveTranscriptRequest(BaseModel):
    title: str
    content: str = ""
    source: str = ""
    description: str = ""
    segments: list[dict[str, Any]] | None = None
    language: str | None = None
    overwrite: bool = False


class TranscriptRenameRequest(BaseModel):
    title: str
    overwrite: bool = False


class TranscriptSummaryRequest(BaseModel):
    summary: str | None = None


def create_app(*, service: LocalAppService | None = None, frontend_dir: Path | None = None) -> FastAPI:
    app = FastAPI(
        title="SJTUFlow Local API",
        version="0.1.0",
        description="Local-only backend API for the SJTUFlow browser UI.",
    )
    app.state.service = service or LocalAppService()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    async def health():
        return {"ok": True, "app": "sjtuflow"}

    @app.get("/api/config")
    async def get_config(reveal_secrets: bool = False):
        return await run_service(app, "config_view", reveal_secrets=reveal_secrets)

    @app.put("/api/config")
    async def update_config(request: ConfigUpdateRequest):
        return await run_service(app, "update_config", request.updates)

    @app.get("/api/doctor")
    async def doctor():
        return await run_service(app, "doctor")

    @app.get("/api/briefing")
    async def briefing():
        return await run_service(app, "collect_briefing")

    @app.get("/api/tools")
    async def tools():
        return await run_service(app, "list_tools")

    @app.get("/api/skills")
    async def skills():
        return await run_service(app, "list_skills")

    @app.get("/api/skills/{name}")
    async def skill(name: str):
        return await run_service(app, "read_skill", name)

    @app.post("/api/skills")
    async def create_skill(request: SkillWriteRequest):
        return await run_service(app, "write_skill", request.name, request.content, overwrite=request.overwrite)

    @app.put("/api/skills/{name}")
    async def update_skill(name: str, request: SkillWriteRequest):
        return await run_service(app, "write_skill", name, request.content, overwrite=True)

    @app.delete("/api/skills/{name}")
    async def delete_skill(name: str):
        return await run_service(app, "delete_user_skill", name)

    @app.get("/api/transcripts")
    async def transcripts():
        return await run_service(app, "list_transcripts")

    @app.get("/api/transcripts/search")
    async def search_transcripts(q: str, limit: int = 20):
        return await run_service(app, "search_transcripts", q, limit=limit)

    @app.get("/api/transcripts/{transcript_id}")
    async def transcript(transcript_id: str):
        return await run_service(app, "read_transcript", transcript_id)

    @app.put("/api/transcripts/{transcript_id}")
    async def rename_transcript(transcript_id: str, request: TranscriptRenameRequest):
        return await run_service(
            app, "rename_transcript", transcript_id, request.title, overwrite=request.overwrite
        )

    @app.delete("/api/transcripts/{transcript_id}")
    async def delete_transcript(transcript_id: str):
        return await run_service(app, "delete_transcript", transcript_id)

    @app.post("/api/transcripts/{transcript_id}/summary")
    async def refresh_transcript_summary(transcript_id: str, request: TranscriptSummaryRequest):
        return await run_service(app, "refresh_transcript_summary", transcript_id, request.summary)

    @app.post("/api/media/probe")
    async def media_probe(request: MediaProbeRequest):
        return await run_service(app, "media_probe", request.path)

    @app.post("/api/media/canvas-access-hint")
    async def media_canvas_access_hint(request: MediaAccessHintRequest):
        return await run_service(app, "media_canvas_access_hint", request.url)

    @app.post("/api/media/resolve-stream")
    async def media_resolve_stream(request: MediaResolveStreamRequest):
        return await run_service(
            app,
            "media_resolve_stream",
            request.source,
            request_headers=request.request_headers,
        )

    @app.post("/api/media/resolve-canvas-page")
    async def media_resolve_canvas_page(request: MediaResolveCanvasPageRequest):
        return await run_service(
            app,
            "media_resolve_canvas_page",
            request.url,
            wait_seconds=request.wait_seconds,
        )

    @app.post("/api/media/find-canvas-pages")
    async def media_find_canvas_pages(request: MediaFindCanvasPagesRequest):
        return await run_service(
            app,
            "media_find_canvas_pages",
            request.course_id,
            query=request.query,
            wait_seconds=request.wait_seconds,
            max_candidates=request.max_candidates,
        )

    @app.post("/api/media/extract-audio")
    async def media_extract_audio(request: MediaExtractAudioRequest):
        return await run_service(
            app, "media_extract_audio", request.path, request.out_dir, sync=request.sync
        )

    @app.post("/api/media/transcribe")
    async def media_transcribe(request: MediaTranscribeRequest):
        return await run_service(
            app,
            "media_transcribe",
            request.path,
            request.provider,
            request.language,
            sync=request.sync,
        )

    @app.post("/api/media/transcribe-and-save")
    async def media_transcribe_and_save(request: MediaTranscribeAndSaveRequest):
        return await run_service(
            app,
            "media_transcribe_and_save",
            request.path,
            request.title,
            request.provider,
            request.language,
            request.description,
            overwrite=request.overwrite,
            sync=request.sync,
        )

    @app.post("/api/media/transcribe-stream")
    async def media_transcribe_stream(request: MediaTranscribeStreamRequest):
        return await run_service(
            app,
            "media_transcribe_stream",
            request.stream_url,
            request.title,
            request.provider,
            request.language,
            request.description,
            overwrite=request.overwrite,
            request_headers=request.request_headers,
            sync=request.sync,
        )

    @app.post("/api/media/transcribe-source")
    async def media_transcribe_source(request: MediaTranscribeSourceRequest):
        return await run_service(
            app,
            "media_transcribe_source",
            request.source,
            request.title,
            request.provider,
            request.language,
            request.description,
            overwrite=request.overwrite,
            request_headers=request.request_headers,
            sync=request.sync,
        )

    @app.post("/api/media/transcribe-canvas-page")
    async def media_transcribe_canvas_page(request: MediaTranscribeCanvasPageRequest):
        return await run_service(
            app,
            "media_transcribe_canvas_page",
            request.url,
            request.title,
            request.provider,
            request.language,
            request.description,
            overwrite=request.overwrite,
            wait_seconds=request.wait_seconds,
            sync=request.sync,
        )

    @app.post("/api/media/save-transcript")
    async def media_save_transcript(request: MediaSaveTranscriptRequest):
        return await run_service(
            app,
            "media_save_transcript",
            request.title,
            request.content,
            request.source,
            request.description,
            segments=request.segments,
            language=request.language,
            overwrite=request.overwrite,
        )

    @app.get("/api/jobs")
    async def list_jobs():
        return await run_service(app, "list_jobs")

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str):
        return await run_service(app, "get_job", job_id)

    @app.post("/api/sessions")
    async def create_session(request: CreateSessionRequest):
        return await run_service(app, "create_session", run_briefing=request.run_briefing)

    @app.get("/api/sessions")
    async def list_sessions():
        return await run_service(app, "list_sessions")

    @app.get("/api/sessions/{session_id}")
    async def read_session(session_id: str):
        return await run_service(app, "read_session", session_id)

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str):
        return await run_service(app, "delete_session", session_id)

    @app.post("/api/sessions/{session_id}/messages")
    async def send_message(session_id: str, request: MessageRequest):
        if not request.message.strip():
            raise HTTPException(status_code=400, detail="message is required")
        return await run_service(app, "send_message", session_id, request.message)

    @app.post("/api/sessions/{session_id}/clear")
    async def clear_session(session_id: str, request: ClearSessionRequest):
        return await run_service(app, "clear_session", session_id, run_briefing=request.run_briefing)

    resolved_frontend = frontend_dir or default_frontend_dist()
    assets_dir = resolved_frontend / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/")
    async def index():
        index_path = resolved_frontend / "index.html"
        if index_path.exists():
            return FileResponse(index_path)
        return {
            "ok": True,
            "message": "SJTUFlow API is running. Frontend has not been implemented yet.",
            "api": "/api/health",
        }

    return app


async def run_service(app: FastAPI, method_name: str, *args: Any, **kwargs: Any):
    service: LocalAppService = app.state.service
    method = getattr(service, method_name)
    try:
        return await run_in_threadpool(method, *args, **kwargs)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists() and (parent / "apps").exists():
            return parent
    return Path.cwd()


def default_frontend_dist() -> Path:
    return repo_root() / "apps" / "frontend" / "dist"


app = create_app()
