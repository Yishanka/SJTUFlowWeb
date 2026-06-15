from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sjtuflow.storage.config import Config
from sjtuflow.utils.text import sanitize_slug, truncate_text


@dataclass
class Skill:
    name: str
    path: Path
    content: str
    source: str = "user"

    @property
    def title(self) -> str:
        for line in self.content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip() or self.name
        return self.name

    @property
    def description(self) -> str:
        lines: list[str] = []
        in_heading = False
        for line in self.content.splitlines():
            stripped = line.strip()
            heading = stripped.lower()
            if stripped.startswith("#"):
                in_heading = heading in {"## purpose", "## description", "## summary", "## overview"}
                continue
            if in_heading and stripped:
                lines.append(stripped)
            if in_heading and lines and not stripped:
                break
        if not lines:
            lines = [line.strip() for line in self.content.splitlines() if line.strip() and not line.startswith("#")][:3]
        return truncate_text(" ".join(lines), 500)

    @property
    def summary(self) -> str:
        return self.description


@dataclass
class SkillMetadata:
    name: str
    title: str
    description: str
    path: str
    source: str


class SkillLoader:
    def __init__(self, config: Config, cwd: Path | None = None) -> None:
        self.config = config
        self.cwd = (cwd or Path.cwd()).resolve()

    def builtin_skill_dir(self) -> Path:
        return Path(__file__).resolve().parents[1] / "builtin_skills"

    def user_skill_root(self) -> Path:
        user_dirs = self.user_skill_dirs()
        return user_dirs[0] if user_dirs else Path("~/.sjtuflow/skills").expanduser()

    def user_skill_dirs(self) -> list[Path]:
        dirs: list[Path] = []
        for value in self.config.workspace.skills_dirs:
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = self.cwd / path
            dirs.append(path.resolve())
        return dirs

    def skill_dirs(self) -> list[Path]:
        dirs: list[Path] = []
        builtin = self.builtin_skill_dir()
        if builtin.exists():
            dirs.append(builtin.resolve())
        dirs.extend(self.user_skill_dirs())
        return dirs

    def load_all(self) -> list[Skill]:
        skills: list[Skill] = []
        builtin_root = self.builtin_skill_dir().resolve()
        for root in self.skill_dirs():
            if not root.exists():
                continue
            for skill_file in sorted(root.glob("*/SKILL.md")):
                skills.append(
                    Skill(
                        name=skill_file.parent.name,
                        path=skill_file,
                        content=skill_file.read_text(encoding="utf-8"),
                        source="builtin" if root.resolve() == builtin_root else "user",
                    )
                )
        return skills

    def list_metadata(self) -> list[SkillMetadata]:
        return [
            SkillMetadata(
                name=skill.name,
                title=skill.title,
                description=skill.description,
                path=str(skill.path),
                source=skill.source,
            )
            for skill in self.load_all()
        ]

    def find(self, name: str) -> Skill | None:
        slug = sanitize_slug(name)
        for skill in self.load_all():
            if skill.name == slug or skill.name == name:
                return skill
        return None

    def default_skill_path(self, name: str) -> Path:
        return self.user_skill_root() / sanitize_slug(name) / "SKILL.md"
