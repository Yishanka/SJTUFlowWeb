from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any, Callable, Literal


RiskLevel = Literal["read", "write", "external_write", "destructive"]


@dataclass
class ToolContext:
    app: Any
    interactive: bool = True


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error: str = ""

    def to_message_content(self) -> str:
        return json.dumps(
            {"ok": self.ok, "data": self.data, "error": self.error},
            ensure_ascii=False,
            default=str,
        )


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    risk_level: RiskLevel
    requires_confirmation: bool
    handler: Callable[..., Any]

    def openai_schema(self) -> dict[str, Any]:
        return self.model_schema(self.name)

    def model_schema(self, model_name: str) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": model_name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Duplicate tool: {spec.name}")
        self._tools[spec.name] = spec

    def tool(
        self,
        *,
        name: str,
        description: str,
        input_schema: dict[str, Any] | None = None,
        risk_level: RiskLevel = "read",
        requires_confirmation: bool | None = None,
    ):
        def decorator(handler: Callable[..., Any]):
            schema = input_schema or schema_from_signature(handler)
            self.register(
                ToolSpec(
                    name=name,
                    description=description,
                    input_schema=schema,
                    risk_level=risk_level,
                    requires_confirmation=requires_confirmation
                    if requires_confirmation is not None
                    else risk_level != "read",
                    handler=handler,
                )
            )
            return handler

        return decorator

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {name}") from exc

    def resolve_name(self, name: str) -> str:
        if name in self._tools:
            return name
        internal = name.replace("__", ".")
        if internal in self._tools:
            return internal
        raise KeyError(f"Unknown tool: {name}")

    def list(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def openai_schemas(self) -> list[dict[str, Any]]:
        return [tool.model_schema(tool.name.replace(".", "__")) for tool in self.list()]


def _annotation_to_schema(annotation: Any) -> dict[str, Any]:
    if annotation in {str, inspect.Signature.empty}:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    if getattr(annotation, "__origin__", None) is list:
        return {"type": "array", "items": {"type": "string"}}
    return {"type": "string"}


def schema_from_signature(handler: Callable[..., Any]) -> dict[str, Any]:
    signature = inspect.signature(handler)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, parameter in signature.parameters.items():
        if name == "ctx":
            continue
        properties[name] = _annotation_to_schema(parameter.annotation)
        if parameter.default is inspect.Signature.empty:
            required.append(name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def run_tool(spec: ToolSpec, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
    try:
        value = spec.handler(ctx=ctx, **arguments)
        if isinstance(value, ToolResult):
            return value
        return ToolResult(ok=True, data=value)
    except Exception as exc:  # Tool errors should feed back into the model.
        return ToolResult(ok=False, error=str(exc))
