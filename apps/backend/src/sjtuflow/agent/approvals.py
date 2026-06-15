from __future__ import annotations

from sjtuflow.storage.config import Config
from sjtuflow.tools.registry import RiskLevel, ToolSpec
from sjtuflow.utils.text import json_dumps


class ApprovalDenied(RuntimeError):
    pass


class ApprovalManager:
    def __init__(self, config: Config, *, interactive: bool = True) -> None:
        self.config = config
        self.interactive = interactive

    def needs_confirmation(self, tool: ToolSpec) -> bool:
        if tool.risk_level == "read":
            return False
        if tool.risk_level == "write":
            return self.config.permissions.confirm_local_write
        if tool.risk_level == "external_write":
            return self.config.permissions.confirm_external_write
        return self.config.permissions.confirm_destructive

    def approve(self, tool: ToolSpec, arguments: dict) -> bool:
        if not self.needs_confirmation(tool):
            return True
        if not self.interactive:
            return self.config.permissions.allow_non_interactive_writes
        print()
        print(f"Tool needs confirmation: {tool.name} [{tool.risk_level}]")
        print(json_dumps(arguments, limit=4000))
        answer = input("Allow this action? [y/N] ").strip().lower()
        return answer in {"y", "yes"}

