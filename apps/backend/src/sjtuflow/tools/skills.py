from __future__ import annotations

from sjtuflow.tools.registry import ToolContext, ToolRegistry
from sjtuflow.utils.text import sanitize_slug


def register_skill_tools(registry: ToolRegistry) -> None:
    @registry.tool(
        name="skills.list",
        description="List local SJTUFlow skills and short summaries.",
        risk_level="read",
    )
    def list_skills(ctx: ToolContext):
        return [
            {
                "name": skill.name,
                "title": skill.title,
                "description": skill.description,
                "path": str(skill.path),
                "source": skill.source,
            }
            for skill in ctx.app.skills.load_all()
        ]

    @registry.tool(
        name="skills.read",
        description="Read one local skill by name.",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def read_skill(ctx: ToolContext, name: str):
        skill = ctx.app.skills.find(name)
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

    @registry.tool(
        name="skills.write",
        description="Create or update a local skill's SKILL.md after confirmation.",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill slug/name."},
                "content": {"type": "string", "description": "Full SKILL.md content."},
                "overwrite": {"type": "boolean", "default": False},
            },
            "required": ["name", "content"],
            "additionalProperties": False,
        },
        risk_level="write",
        requires_confirmation=True,
    )
    def write_skill(ctx: ToolContext, name: str, content: str, overwrite: bool = False):
        slug = sanitize_slug(name)
        target = ctx.app.workspace.assert_safe_write_path(ctx.app.skills.default_skill_path(slug))
        if target.exists() and not overwrite:
            raise FileExistsError(f"{target} exists; set overwrite=true to replace it")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"name": slug, "path": str(target), "bytes": len(content.encode("utf-8"))}
