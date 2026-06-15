from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from sjtuflow.llm.base import LLMResponse, Message, ToolCall
from sjtuflow.storage.config import ModelConfig


class OpenAICompatibleProvider:
    """Small OpenAI-compatible Chat Completions provider.

    It targets endpoints shaped like /v1/chat/completions and supports model
    tool calls. This keeps SJTUFlow independent from any vendor SDK.
    """

    def __init__(self, config: ModelConfig) -> None:
        self.config = config

    def complete(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        api_key = self.config.resolved_api_key()
        if not api_key:
            raise RuntimeError(
                f"No model API key configured. Set {self.config.api_key_env} or model.api_key in config."
            )

        endpoint = self.config.endpoint.rstrip("/")
        url = f"{endpoint}/chat/completions"
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        request = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "SJTUFlow/0.1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            hint = ""
            if exc.code in {401, 403}:
                hint = (
                    " Check model.endpoint, model.model, and the configured API key. "
                    "HTTP 403 means the model service rejected the request before SJTUFlow could call Canvas."
                )
            raise RuntimeError(f"Model request failed with HTTP {exc.code}: {detail}{hint}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Model request failed: {exc.reason}") from exc

        choice = payload.get("choices", [{}])[0]
        message = choice.get("message", {})
        content = message.get("content") or ""
        tool_calls: list[ToolCall] = []
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            raw_args = function.get("arguments") or "{}"
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                args = {"_raw": raw_args}
            tool_calls.append(
                ToolCall(
                    id=call.get("id") or f"call_{len(tool_calls)}",
                    name=function.get("name") or "",
                    arguments=args if isinstance(args, dict) else {"value": args},
                )
            )
        return LLMResponse(content=content, tool_calls=tool_calls, raw=payload)
