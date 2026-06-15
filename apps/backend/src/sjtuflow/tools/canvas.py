from __future__ import annotations

from sjtuflow.tools.registry import ToolContext, ToolRegistry


def _dataclass_list(items):
    return [item.__dict__ for item in items]


def register_canvas_tools(registry: ToolRegistry) -> None:
    @registry.tool(
        name="canvas.connection_status",
        description="Check whether Canvas configuration has a token and optionally test a lightweight courses request.",
        input_schema={
            "type": "object",
            "properties": {
                "ping": {"type": "boolean", "default": True, "description": "When true, make a small read request."}
            },
            "required": [],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def connection_status(ctx: ToolContext, ping: bool = True):
        config = ctx.app.config.canvas
        status = {
            "base_url": config.base_url,
            "token_configured": bool(config.resolved_token()),
            "token_env": config.access_token_env,
        }
        if not ping:
            return status
        try:
            courses = ctx.app.canvas.list_courses(limit=1)
            status["ok"] = True
            status["sample_courses"] = [course.__dict__ for course in courses]
        except Exception as exc:
            status["ok"] = False
            status["error"] = str(exc)
        return status

    @registry.tool(
        name="canvas.list_courses",
        description="List active Canvas courses for the configured SJTU Canvas account.",
        input_schema={
            "type": "object",
            "properties": {
                "enrollment_state": {"type": "string", "default": "active"},
                "limit": {"type": "integer", "default": 50},
            },
            "required": [],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def list_courses(ctx: ToolContext, enrollment_state: str = "active", limit: int = 50):
        return _dataclass_list(ctx.app.canvas.list_courses(enrollment_state=enrollment_state, limit=limit))

    @registry.tool(
        name="canvas.list_assignments",
        description="List assignments for one Canvas course.",
        input_schema={
            "type": "object",
            "properties": {
                "course_id": {"type": "string"},
                "include_past": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "default": 100},
            },
            "required": ["course_id"],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def list_assignments(ctx: ToolContext, course_id: str, include_past: bool = False, limit: int = 100):
        return _dataclass_list(ctx.app.canvas.list_assignments(course_id, include_past=include_past, limit=limit))

    @registry.tool(
        name="canvas.list_upcoming_assignments",
        description="List upcoming assignments across active Canvas courses within a time window.",
        input_schema={
            "type": "object",
            "properties": {
                "window_days": {"type": "integer", "default": 14},
                "limit_courses": {"type": "integer", "default": 50},
            },
            "required": [],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def list_upcoming_assignments(ctx: ToolContext, window_days: int = 14, limit_courses: int = 50):
        return ctx.app.canvas.list_upcoming_assignments(window_days=window_days, limit_courses=limit_courses)

    @registry.tool(
        name="canvas.list_recent_announcements",
        description="List recent Canvas announcements across active courses.",
        input_schema={
            "type": "object",
            "properties": {
                "since_days": {"type": "integer", "default": 3},
                "limit": {"type": "integer", "default": 20},
            },
            "required": [],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def list_recent_announcements(ctx: ToolContext, since_days: int = 3, limit: int = 20):
        return ctx.app.canvas.list_recent_announcements(since_days=since_days, limit=limit)

    @registry.tool(
        name="canvas.list_files",
        description="List Canvas files for one course or folder.",
        input_schema={
            "type": "object",
            "properties": {
                "course_id": {"type": "string"},
                "folder_id": {"type": "string", "description": "Optional folder id."},
                "limit": {"type": "integer", "default": 100},
            },
            "required": ["course_id"],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def list_files(ctx: ToolContext, course_id: str, folder_id: str | None = None, limit: int = 100):
        return _dataclass_list(ctx.app.canvas.list_files(course_id, folder_id=folder_id or None, limit=limit))

    @registry.tool(
        name="canvas.get_file",
        description="Read Canvas file metadata by file id without downloading it.",
        input_schema={
            "type": "object",
            "properties": {"file_id": {"type": "string"}},
            "required": ["file_id"],
            "additionalProperties": False,
        },
        risk_level="read",
    )
    def get_file(ctx: ToolContext, file_id: str):
        return ctx.app.canvas.get_file(file_id).__dict__

    @registry.tool(
        name="canvas.download_file",
        description="Download a Canvas file into the SJTUFlow data directory after confirmation.",
        input_schema={
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "out_dir": {
                    "type": "string",
                    "description": "Optional destination directory. Relative paths resolve from current project.",
                },
                "course_label": {
                    "type": "string",
                    "description": "Optional readable course folder name when out_dir is omitted.",
                },
            },
            "required": ["file_id"],
            "additionalProperties": False,
        },
        risk_level="write",
        requires_confirmation=True,
    )
    def download_file(ctx: ToolContext, file_id: str, out_dir: str | None = None, course_label: str | None = None):
        if out_dir:
            target_dir = ctx.app.workspace.resolve_write_path(out_dir)
        else:
            target_dir = ctx.app.workspace.canvas_download_dir(course_label)
        file = ctx.app.canvas.download_file(file_id, target_dir)
        return file.__dict__
