from __future__ import annotations

import json
from typing import Any

from fastmcp import FastMCP

from sjtuflow.runtime import AppContext
from sjtuflow.tools import build_registry
from sjtuflow.tools.registry import ToolContext, run_tool


def build_mcp_server(app: AppContext) -> FastMCP:
    registry = build_registry()
    mcp = FastMCP("SJTUFlow", instructions="Canvas-first SJTUFlow tools.")

    def call_tool(name: str, arguments: dict[str, Any]) -> Any:
        spec = registry.get(name)
        if spec.risk_level != "read" and not app.config.permissions.allow_non_interactive_writes:
            raise RuntimeError(
                f"{name} is a {spec.risk_level} tool. Enable permissions.allow_non_interactive_writes "
                "or use the interactive CLI confirmation flow."
            )
        result = run_tool(spec, ToolContext(app=app, interactive=False), arguments)
        if not result.ok:
            raise RuntimeError(result.error)
        return result.data

    @mcp.tool(name="canvas.connection_status", description="Check Canvas token/configuration and optionally ping Canvas.")
    def canvas_connection_status(ping: bool = True) -> Any:
        return call_tool("canvas.connection_status", {"ping": ping})

    @mcp.tool(name="canvas.list_courses", description="List active Canvas courses.")
    def canvas_list_courses(enrollment_state: str = "active", limit: int = 50) -> Any:
        return call_tool("canvas.list_courses", {"enrollment_state": enrollment_state, "limit": limit})

    @mcp.tool(name="canvas.list_assignments", description="List assignments for one Canvas course.")
    def canvas_list_assignments(course_id: str, include_past: bool = False, limit: int = 100) -> Any:
        return call_tool(
            "canvas.list_assignments",
            {"course_id": course_id, "include_past": include_past, "limit": limit},
        )

    @mcp.tool(name="canvas.list_upcoming_assignments", description="List upcoming assignments across active courses.")
    def canvas_list_upcoming_assignments(window_days: int = 14, limit_courses: int = 50) -> Any:
        return call_tool(
            "canvas.list_upcoming_assignments",
            {"window_days": window_days, "limit_courses": limit_courses},
        )

    @mcp.tool(name="canvas.list_recent_announcements", description="List recent Canvas announcements.")
    def canvas_list_recent_announcements(since_days: int = 3, limit: int = 20) -> Any:
        return call_tool("canvas.list_recent_announcements", {"since_days": since_days, "limit": limit})

    @mcp.tool(name="canvas.list_files", description="List Canvas files for one course or folder.")
    def canvas_list_files(course_id: str, folder_id: str | None = None, limit: int = 100) -> Any:
        return call_tool("canvas.list_files", {"course_id": course_id, "folder_id": folder_id, "limit": limit})

    @mcp.tool(name="canvas.get_file", description="Read Canvas file metadata without downloading.")
    def canvas_get_file(file_id: str) -> Any:
        return call_tool("canvas.get_file", {"file_id": file_id})

    @mcp.tool(name="canvas.download_file", description="Download a Canvas file into the SJTUFlow data directory.")
    def canvas_download_file(file_id: str, out_dir: str | None = None, course_label: str | None = None) -> Any:
        return call_tool("canvas.download_file", {"file_id": file_id, "out_dir": out_dir, "course_label": course_label})

    @mcp.tool(name="filesystem.read_text", description="Read a local text file.")
    def filesystem_read_text(path: str, max_chars: int = 12000) -> Any:
        return call_tool("filesystem.read_text", {"path": path, "max_chars": max_chars})

    @mcp.tool(name="filesystem.list_dir", description="List a local directory.")
    def filesystem_list_dir(path: str = ".", max_entries: int = 100) -> Any:
        return call_tool("filesystem.list_dir", {"path": path, "max_entries": max_entries})

    @mcp.tool(name="filesystem.write_text", description="Write a local UTF-8 text file.")
    def filesystem_write_text(path: str, content: str, overwrite: bool = False) -> Any:
        return call_tool("filesystem.write_text", {"path": path, "content": content, "overwrite": overwrite})

    @mcp.tool(name="filesystem.workspace_info", description="Show SJTUFlow workspace information.")
    def filesystem_workspace_info() -> Any:
        return call_tool("filesystem.workspace_info", {})

    @mcp.tool(name="skills.list", description="List local SJTUFlow skills.")
    def skills_list() -> Any:
        return call_tool("skills.list", {})

    @mcp.tool(name="skills.read", description="Read one local skill by name.")
    def skills_read(name: str) -> Any:
        return call_tool("skills.read", {"name": name})

    @mcp.tool(name="skills.write", description="Create or update a local skill.")
    def skills_write(name: str, content: str, overwrite: bool = False) -> Any:
        return call_tool("skills.write", {"name": name, "content": content, "overwrite": overwrite})

    @mcp.tool(name="sjtuflow.call_tool_json", description="Call any SJTUFlow internal tool by name with a JSON object string.")
    def sjtuflow_call_tool_json(name: str, arguments_json: str = "{}") -> Any:
        return call_tool(name, json.loads(arguments_json))

    return mcp


def run_mcp_server(app: AppContext) -> None:
    build_mcp_server(app).run()
