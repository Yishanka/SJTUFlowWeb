from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sjtuflow.agent.briefing import collect_startup_briefing
from sjtuflow.agent.loop import AgentLoop, AgentResult
from sjtuflow.llm.mock_provider import build_provider
from sjtuflow.runtime import AppContext, build_app_context
from sjtuflow.storage.config import (
    Config,
    config_to_toml,
    default_config_path,
    ensure_default_config,
    load_config,
    set_config_value,
)
from sjtuflow.storage.sessions import SessionStore
from sjtuflow.tools import build_registry
from sjtuflow.tools.registry import ToolRegistry
from sjtuflow.utils.text import redact, sanitize_slug


def _to_plain(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _to_plain(item) for key, item in asdict(value).items()}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_plain(item) for key, item in value.items()}
    return value


@dataclass
class SessionState:
    id: str
    loop: AgentLoop
    created_at: str
    updated_at: str


class LocalAppService:
    """Stateful local backend service used by the browser API.

    The service is intentionally process-local. SJTUFlow's first web version is
    a single-user local app. Active sessions live in memory, while chat history
    is persisted under the local state directory for browser restore.
    """

    def __init__(self, *, cwd: Path | None = None) -> None:
        self.cwd = (cwd or Path.cwd()).resolve()
        self._sessions: dict[str, SessionState] = {}

    def config_path(self) -> Path:
        return default_config_path()

    def ensure_config(self) -> Path:
        return ensure_default_config()

    def load_config(self) -> Config:
        self.ensure_config()
        return load_config()

    def app_context(self) -> AppContext:
        return build_app_context(self.load_config(), cwd=self.cwd)

    def registry(self) -> ToolRegistry:
        return build_registry()

    def session_store(self) -> SessionStore:
        return SessionStore(self.app_context().workspace.state_dir)

    def config_view(self, *, reveal_secrets: bool = False) -> dict[str, Any]:
        config = self.load_config()
        values = _to_plain(config)
        if not reveal_secrets:
            values = redact(values)
        return {
            "path": str(config.path),
            "toml": config_to_toml(config, reveal_secrets=reveal_secrets),
            "values": values,
        }

    def update_config(self, updates: dict[str, Any]) -> dict[str, Any]:
        path = self.ensure_config()
        for dotted_key, value in updates.items():
            set_config_value(path, dotted_key, value)
        return self.config_view()

    def doctor(self) -> dict[str, Any]:
        config = self.load_config()
        app = build_app_context(config, cwd=self.cwd)
        registry = self.registry()
        skills = app.skills.list_metadata()
        return {
            "config": str(config.path),
            "config_exists": config.path.exists(),
            "state_dir": str(app.workspace.state_dir),
            "data_dir": str(app.workspace.data_dir),
            "model": {
                "provider": config.model.provider,
                "endpoint": config.model.endpoint,
                "model": config.model.model,
                "key_configured": bool(config.model.resolved_api_key()),
                "api_key_env": config.model.api_key_env,
            },
            "canvas": {
                "base_url": config.canvas.base_url,
                "token_configured": bool(config.canvas.resolved_token()),
                "access_token_env": config.canvas.access_token_env,
            },
            "skills_loaded": len(skills),
            "tools_registered": len(registry.list()),
        }

    def collect_briefing(self) -> dict[str, Any]:
        return collect_startup_briefing(self.app_context())

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "risk_level": tool.risk_level,
                "requires_confirmation": tool.requires_confirmation,
            }
            for tool in self.registry().list()
        ]

    def list_skills(self) -> list[dict[str, Any]]:
        return [skill.__dict__ for skill in self.app_context().skills.list_metadata()]

    def read_skill(self, name: str) -> dict[str, Any]:
        skill = self.app_context().skills.find(name)
        if skill is None:
            raise FileNotFoundError(f"Skill not found: {name}")
        return {
            "name": skill.name,
            "title": skill.title,
            "description": skill.description,
            "path": str(skill.path),
            "source": skill.source,
            "content": skill.content,
        }

    def write_skill(self, name: str, content: str, *, overwrite: bool = False) -> dict[str, Any]:
        app = self.app_context()
        slug = sanitize_slug(name)
        existing = app.skills.find(slug)
        if existing is not None and existing.source == "builtin":
            raise ValueError(f"{slug} is a built-in skill. Copy it with a new name before editing.")
        target = app.workspace.assert_safe_write_path(app.skills.default_skill_path(slug))
        if target.exists() and not overwrite:
            raise FileExistsError(f"{target} exists; set overwrite=true to replace it")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        app.audit.record("skill_write", {"name": slug, "path": str(target), "overwrite": overwrite})
        written = target.read_text(encoding="utf-8")
        from sjtuflow.skills.loader import Skill

        skill = Skill(name=slug, path=target, content=written, source="user")
        return {
            "name": skill.name,
            "title": skill.title,
            "description": skill.description,
            "path": str(skill.path),
            "source": skill.source,
            "content": skill.content,
        }

    def delete_user_skill(self, name: str) -> dict[str, Any]:
        app = self.app_context()
        slug = sanitize_slug(name)
        target = app.workspace.assert_safe_write_path(app.skills.default_skill_path(slug))
        if not target.exists():
            raise FileNotFoundError(f"User skill not found: {slug}")
        target.unlink()
        try:
            target.parent.rmdir()
        except OSError:
            pass
        app.audit.record("skill_delete", {"name": slug, "path": str(target)})
        return {"ok": True, "name": slug, "path": str(target)}

    def list_transcripts(self) -> list[dict[str, Any]]:
        from sjtuflow.tools.transcripts import list_transcript_metadata

        return list_transcript_metadata(self.app_context())

    def read_transcript(self, transcript_id: str) -> dict[str, Any]:
        from sjtuflow.tools.transcripts import read_transcript_by_id

        return read_transcript_by_id(self.app_context(), transcript_id)

    def list_sessions(self) -> list[dict[str, Any]]:
        return self.session_store().list()

    def read_session(self, session_id: str) -> dict[str, Any]:
        return self.session_store().read(session_id).to_payload()

    def delete_session(self, session_id: str) -> dict[str, Any]:
        self._sessions.pop(session_id, None)
        return self.session_store().delete(session_id)

    def create_session(self, *, run_briefing: bool | None = None) -> dict[str, Any]:
        config = self.load_config()
        app = build_app_context(config, cwd=self.cwd)
        provider = build_provider(config.model.provider, config.model)
        loop = AgentLoop(app=app, provider=provider, registry=self.registry(), interactive=False)
        should_brief = config.agent.startup_briefing if run_briefing is None else run_briefing
        loop.start(run_briefing=should_brief)
        session_id = uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        self._sessions[session_id] = SessionState(id=session_id, loop=loop, created_at=now, updated_at=now)
        self._persist_session(self._sessions[session_id])
        return self._session_payload(self._sessions[session_id])

    def get_session(self, session_id: str) -> SessionState:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            stored = self.session_store().read(session_id)
            config = self.load_config()
            app = build_app_context(config, cwd=self.cwd)
            provider = build_provider(config.model.provider, config.model)
            loop = AgentLoop(app=app, provider=provider, registry=self.registry(), interactive=False)
            loop.messages = stored.messages
            loop.briefing = stored.briefing
            session = SessionState(
                id=stored.id,
                loop=loop,
                created_at=stored.created_at,
                updated_at=stored.updated_at,
            )
            self._sessions[session_id] = session
            return session

    def send_message(self, session_id: str, message: str) -> dict[str, Any]:
        session = self.get_session(session_id)
        result = session.loop.run_user_message(message)
        session.updated_at = datetime.now(timezone.utc).isoformat()
        self._persist_session(session)
        return self._session_payload(session, result=result)

    def clear_session(self, session_id: str, *, run_briefing: bool = False) -> dict[str, Any]:
        session = self.get_session(session_id)
        session.loop.start(run_briefing=run_briefing)
        session.updated_at = datetime.now(timezone.utc).isoformat()
        self._persist_session(session)
        return self._session_payload(session)

    def _persist_session(self, session: SessionState) -> None:
        self.session_store().save(
            session_id=session.id,
            created_at=session.created_at,
            updated_at=session.updated_at,
            messages=session.loop.messages,
            briefing=session.loop.briefing,
        )

    def _session_payload(self, session: SessionState, *, result: AgentResult | None = None) -> dict[str, Any]:
        loop = session.loop
        payload: dict[str, Any] = {
            "id": session.id,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "briefing": loop.briefing,
            "messages": loop.messages,
        }
        if result is not None:
            payload["final_text"] = result.final_text
        return payload
