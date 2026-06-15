from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


Message = dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class LLMProvider(Protocol):
    def complete(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        ...

