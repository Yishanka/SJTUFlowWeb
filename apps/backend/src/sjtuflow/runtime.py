from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sjtuflow.connectors.canvas import CanvasClient
from sjtuflow.skills.loader import SkillLoader
from sjtuflow.storage.audit import AuditLogger
from sjtuflow.storage.config import Config
from sjtuflow.storage.workspace import Workspace


@dataclass
class AppContext:
    config: Config
    workspace: Workspace
    audit: AuditLogger
    canvas: CanvasClient
    skills: SkillLoader
    cwd: Path


def build_app_context(config: Config, cwd: Path | None = None) -> AppContext:
    current = (cwd or Path.cwd()).resolve()
    workspace = Workspace(config, current)
    workspace.ensure()
    return AppContext(
        config=config,
        workspace=workspace,
        audit=AuditLogger(workspace.state_dir),
        canvas=CanvasClient(config.canvas),
        skills=SkillLoader(config, current),
        cwd=current,
    )

