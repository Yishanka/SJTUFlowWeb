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

    @app.get("/api/transcripts/{transcript_id}")
    async def transcript(transcript_id: str):
        return await run_service(app, "read_transcript", transcript_id)

    @app.post("/api/sessions")
    async def create_session(request: CreateSessionRequest):
        return await run_service(app, "create_session", run_briefing=request.run_briefing)

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
