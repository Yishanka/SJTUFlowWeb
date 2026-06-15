from __future__ import annotations

from pathlib import Path

from sjtuflow.storage.config import Config
from sjtuflow.utils.text import is_relative_to, sanitize_filename


class WorkspaceError(ValueError):
    pass


class Workspace:
    def __init__(self, config: Config, cwd: Path | None = None) -> None:
        self.config = config
        self.cwd = (cwd or Path.cwd()).resolve()
        self.state_dir = Path(config.workspace.state_dir).expanduser().resolve()
        self.data_dir = Path(config.workspace.data_dir).expanduser().resolve()

    def ensure(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "audit").mkdir(parents=True, exist_ok=True)
        (self.state_dir / "cache").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "canvas").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "transcripts").mkdir(parents=True, exist_ok=True)

    @property
    def allowed_write_roots(self) -> list[Path]:
        return [self.cwd, self.data_dir, self.state_dir]

    def assert_safe_write_path(self, path: Path) -> Path:
        resolved = path.expanduser().resolve()
        if not any(is_relative_to(resolved, root) for root in self.allowed_write_roots):
            roots = ", ".join(str(root) for root in self.allowed_write_roots)
            raise WorkspaceError(f"Refusing to write outside allowed roots: {roots}")
        return resolved

    def resolve_write_path(self, path: str, *, default_base: Path | None = None) -> Path:
        raw = Path(path).expanduser()
        if not raw.is_absolute():
            base = default_base or self.cwd
            raw = base / raw
        return self.assert_safe_write_path(raw)

    def resolve_read_path(self, path: str) -> Path:
        raw = Path(path).expanduser()
        if not raw.is_absolute():
            raw = self.cwd / raw
        resolved = raw.resolve()
        allowed_roots = [self.cwd, self.data_dir, self.state_dir]
        if not any(is_relative_to(resolved, root) for root in allowed_roots):
            roots = ", ".join(str(root) for root in allowed_roots)
            raise WorkspaceError(f"Refusing to read outside allowed roots: {roots}")
        return resolved

    def canvas_download_dir(self, course_label: str | None = None) -> Path:
        folder = self.data_dir / "canvas" / "files"
        if course_label:
            folder = self.data_dir / "canvas" / sanitize_filename(course_label)
        return self.assert_safe_write_path(folder)
