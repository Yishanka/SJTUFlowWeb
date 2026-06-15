from __future__ import annotations

from sjtuflow.tools.canvas import register_canvas_tools
from sjtuflow.tools.filesystem import register_filesystem_tools
from sjtuflow.tools.registry import ToolRegistry
from sjtuflow.tools.skills import register_skill_tools
from sjtuflow.tools.transcripts import register_transcript_tools


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    register_canvas_tools(registry)
    register_skill_tools(registry)
    register_transcript_tools(registry)
    return registry
