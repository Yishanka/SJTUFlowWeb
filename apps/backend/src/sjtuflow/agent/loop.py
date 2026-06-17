from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sjtuflow.agent.approvals import ApprovalManager
from sjtuflow.agent.briefing import briefing_to_text, collect_startup_briefing
from sjtuflow.llm.base import LLMProvider, Message
from sjtuflow.runtime import AppContext
from sjtuflow.tools.registry import ToolContext, ToolRegistry, run_tool
from sjtuflow.utils.text import json_dumps, truncate_text


SYSTEM_PROMPT = """You are SJTUFlow, a local CLI learning assistant for Shanghai Jiao Tong University students.

The user gives goals in natural language. Decide when to answer directly and when to call tools.

Core behavior:
- Use Canvas tools to inspect courses, assignments, announcements, and files when the user asks about current course status.
- Use filesystem tools for local files, but ask for confirmation before downloads or writes.
- For SJTU Canvas external_tools video/media pages, do not claim the Canvas token can fetch the stream. Use saved transcripts first; if no transcript exists, use the media Canvas-page tools that open SJTUFlow's managed browser profile. If that profile is not logged in, tell the user to log in in the opened browser window and retry. Do not bypass authentication, CAPTCHA, DRM, or course permissions.
- Media transcripts are saved to the local transcript library by default. Never save the source video stream locally.
- Treat startup briefing as a lightweight snapshot, not a complete source of truth.
- Give concise answers in the user's language. Include source names, course names, file paths, or URLs when available.
- Do not submit assignments, send mail, delete files, or modify remote services unless an explicit future tool supports it and the user confirms.
- Skills are local operating instructions. Use relevant skills when their purpose matches the user's goal.
"""


@dataclass
class AgentResult:
    final_text: str
    messages: list[Message] = field(default_factory=list)
    briefing: dict[str, Any] | None = None


class AgentLoop:
    def __init__(
        self,
        *,
        app: AppContext,
        provider: LLMProvider,
        registry: ToolRegistry,
        interactive: bool = True,
    ) -> None:
        self.app = app
        self.provider = provider
        self.registry = registry
        self.interactive = interactive
        self.approvals = ApprovalManager(app.config, interactive=interactive)
        self.messages: list[Message] = []
        self.briefing: dict[str, Any] | None = None

    def start(self, *, run_briefing: bool = True) -> None:
        context_parts = [SYSTEM_PROMPT, self._skills_context(), self._workspace_context()]
        if run_briefing and self.app.config.agent.startup_briefing:
            self.briefing = collect_startup_briefing(self.app)
            context_parts.append(briefing_to_text(self.briefing))
        self.messages = [{"role": "system", "content": "\n\n".join(part for part in context_parts if part)}]

    def run_user_message(self, prompt: str) -> AgentResult:
        if not self.messages:
            self.start()
        self.messages.append({"role": "user", "content": prompt})

        final_text = ""
        for step in range(self.app.config.agent.max_tool_calls + 1):
            response = self.provider.complete(self.messages, self.registry.openai_schemas())
            assistant_message: Message = {"role": "assistant", "content": response.content or ""}
            if response.tool_calls:
                assistant_message["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, ensure_ascii=False),
                        },
                    }
                    for call in response.tool_calls
                ]
            self.messages.append(assistant_message)

            if not response.tool_calls:
                final_text = response.content or ""
                self.app.audit.record("assistant_response", {"content": final_text})
                break

            if step >= self.app.config.agent.max_tool_calls:
                final_text = "Tool call limit reached before the agent produced a final answer."
                break

            for call in response.tool_calls:
                tool_name = self._resolve_tool_name(call.name)
                result_content = self._execute_tool_call(tool_name, call.arguments)
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": result_content,
                    }
                )
        else:
            final_text = "The agent stopped after reaching the configured tool-call limit."

        return AgentResult(final_text=final_text, messages=self.messages, briefing=self.briefing)

    def _resolve_tool_name(self, name: str) -> str:
        try:
            return self.registry.resolve_name(name)
        except KeyError:
            return name

    def _execute_tool_call(self, name: str, arguments: dict[str, Any]) -> str:
        try:
            spec = self.registry.get(name)
        except KeyError as exc:
            content = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
            self.app.audit.record("tool_call", {"name": name, "arguments": arguments, "result": content})
            return content

        if self.interactive:
            print(f"\n-> {name} {json_dumps(arguments, limit=1000)}")
        approved = self.approvals.approve(spec, arguments)
        if not approved:
            result = {"ok": False, "error": "User denied tool action"}
            self.app.audit.record(
                "tool_call",
                {"name": name, "arguments": arguments, "risk": spec.risk_level, "approved": False, "result": result},
            )
            return json.dumps(result, ensure_ascii=False)

        result = run_tool(spec, ToolContext(app=self.app, interactive=self.interactive), arguments)
        content = truncate_text(result.to_message_content(), self.app.config.agent.max_tool_result_chars)
        self.app.audit.record(
            "tool_call",
            {
                "name": name,
                "arguments": arguments,
                "risk": spec.risk_level,
                "approved": True,
                "ok": result.ok,
                "result": result.data if result.ok else result.error,
            },
        )
        if self.interactive:
            status = "ok" if result.ok else "error"
            print(f"<- {name} {status}")
        return content

    def _skills_context(self) -> str:
        skills = self.app.skills.list_metadata()
        if not skills:
            return "No local skills are currently installed."
        lines = [
            "Available local skills. Only titles and descriptions are preloaded; call skills.read when a task needs the full SKILL.md."
        ]
        for skill in skills:
            lines.append(f"## {skill.title}\nName: {skill.name}\nPath: {skill.path}\nDescription: {skill.description}")
        return "\n\n".join(lines)

    def _workspace_context(self) -> str:
        workspace = self.app.workspace
        return (
            "Workspace:\n"
            f"- cwd: {workspace.cwd}\n"
            f"- data_dir: {workspace.data_dir}\n"
            f"- state_dir: {workspace.state_dir}\n"
            "Writable paths are limited to the current project and data_dir."
        )

    def save_transcript(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json_dumps(self.messages), encoding="utf-8")
