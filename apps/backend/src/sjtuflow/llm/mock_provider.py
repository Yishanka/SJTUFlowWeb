from __future__ import annotations

import json

from sjtuflow.llm.base import LLMResponse, Message, ToolCall


class MockProvider:
    """A deterministic fallback for local smoke tests and doctor output."""

    def complete(self, messages: list[Message], tools: list[dict]) -> LLMResponse:
        if messages and messages[-1].get("role") == "tool":
            tool_summaries = []
            for message in reversed(messages):
                if message.get("role") != "tool":
                    if message.get("role") == "assistant":
                        continue
                    break
                name = message.get("name") or "tool"
                content = str(message.get("content") or "")
                try:
                    payload = json.loads(content)
                except json.JSONDecodeError:
                    payload = {"ok": False, "error": content}
                if payload.get("ok"):
                    tool_summaries.append(f"- {name}: {json.dumps(payload.get('data'), ensure_ascii=False)[:1200]}")
                else:
                    tool_summaries.append(f"- {name}: ERROR {payload.get('error')}")
            tool_summaries.reverse()
            return LLMResponse(
                content=(
                    "Mock provider 工具调用完成。真实回答需要配置 openai-compatible 模型。\n"
                    + "\n".join(tool_summaries)
                )
            )

        user_text = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                user_text = str(message.get("content") or "")
                break

        lower = user_text.lower()
        if "课程" in user_text or "course" in lower or "canvas" in lower:
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(id="mock_call_1", name="canvas__list_courses", arguments={})],
            )
        if "作业" in user_text or "assignment" in lower:
            return LLMResponse(
                content="",
                tool_calls=[
                    ToolCall(id="mock_call_1", name="canvas__list_courses", arguments={}),
                    ToolCall(id="mock_call_2", name="canvas__list_upcoming_assignments", arguments={}),
                ],
            )
        if "skill" in lower or "技能" in user_text:
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(id="mock_call_1", name="skills__list", arguments={})],
            )
        if "readme" in lower:
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(id="mock_call_1", name="filesystem__read_text", arguments={"path": "README.md"})],
            )
        return LLMResponse(
            content=(
                "Mock provider 已启动。请在 config 中把 model.provider 改为 "
                '"openai-compatible"，并填写 endpoint 与 api key 后使用真实模型。'
            )
        )


def build_provider(provider_name: str, model_config):
    if provider_name in {"mock", "dry-run"}:
        return MockProvider()
    if provider_name in {"openai", "openai-compatible", "compatible"}:
        from sjtuflow.llm.openai_provider import OpenAICompatibleProvider

        return OpenAICompatibleProvider(model_config)
    raise ValueError(f"Unsupported model provider: {provider_name}")
