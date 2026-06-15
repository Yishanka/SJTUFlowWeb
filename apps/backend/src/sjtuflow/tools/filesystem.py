from __future__ import annotations

from pathlib import Path

from sjtuflow.tools.registry import ToolContext, ToolRegistry
from sjtuflow.utils.text import truncate_text


def register_filesystem_tools(registry: ToolRegistry) -> None:
    @registry.tool(
        name="filesystem.read_text",
        description="Read a text-like local file from the project, SJTUFlow data directory, or state directory.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative or absolute path to read."},
                "max_chars": {"type": "integer", "description": "Maximum characters to return.", "default": 12000},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def read_text(ctx: ToolContext, path: str, max_chars: int = 12000):
        target = ctx.app.workspace.resolve_read_path(path)
        if not target.exists():
            raise FileNotFoundError(str(target))
        if not target.is_file():
            raise IsADirectoryError(str(target))
        text = target.read_text(encoding="utf-8", errors="replace")
        return {"path": str(target), "content": truncate_text(text, max_chars)}

    @registry.tool(
        name="filesystem.list_dir",
        description="List files in a local directory under the project, data directory, or state directory.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "default": "."},
                "max_entries": {"type": "integer", "default": 100},
            },
            "required": [],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def list_dir(ctx: ToolContext, path: str = ".", max_entries: int = 100):
        target = ctx.app.workspace.resolve_read_path(path)
        if not target.exists():
            raise FileNotFoundError(str(target))
        if not target.is_dir():
            raise NotADirectoryError(str(target))
        entries = []
        for entry in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))[:max_entries]:
            entries.append(
                {
                    "name": entry.name,
                    "path": str(entry),
                    "type": "dir" if entry.is_dir() else "file",
                    "size": entry.stat().st_size if entry.is_file() else None,
                }
            )
        return {"path": str(target), "entries": entries}

    @registry.tool(
        name="filesystem.write_text",
        description="Write a UTF-8 text file under the project directory or SJTUFlow data directory after confirmation.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Destination path. Relative paths resolve from current project."},
                "content": {"type": "string"},
                "overwrite": {"type": "boolean", "default": False},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        risk_level="write",
        requires_confirmation=True,
    )
    def write_text(ctx: ToolContext, path: str, content: str, overwrite: bool = False):
        target = ctx.app.workspace.resolve_write_path(path)
        if target.exists() and not overwrite:
            raise FileExistsError(f"{target} exists; set overwrite=true to replace it")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"path": str(target), "bytes": len(content.encode("utf-8"))}

    @registry.tool(
        name="filesystem.workspace_info",
        description="Show SJTUFlow workspace, data directory, allowed write roots, and current project path.",
        risk_level="read",
    )
    def workspace_info(ctx: ToolContext):
        workspace = ctx.app.workspace
        return {
            "cwd": str(workspace.cwd),
            "state_dir": str(workspace.state_dir),
            "data_dir": str(workspace.data_dir),
            "allowed_write_roots": [str(root) for root in workspace.allowed_write_roots],
        }

