from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


CONFIG_ENV = "SJTU_FLOW_CONFIG"


@dataclass
class ModelConfig:
    provider: str = "mock"
    model: str = "gpt-4.1-mini"
    endpoint: str = "https://api.openai.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
    api_key: str = ""
    timeout_seconds: int = 60
    temperature: float = 0.2

    def resolved_api_key(self) -> str:
        return self.api_key or os.environ.get(self.api_key_env, "")


@dataclass
class CanvasConfig:
    base_url: str = "https://oc.sjtu.edu.cn"
    access_token_env: str = "SJTU_CANVAS_TOKEN"
    access_token: str = ""
    account: str = ""
    password: str = ""
    timeout_seconds: int = 60

    def resolved_token(self) -> str:
        return self.access_token or os.environ.get(self.access_token_env, "")


@dataclass
class WorkspaceConfig:
    state_dir: str = "~/.sjtuflow"
    data_dir: str = "~/SJTUFlowData"
    skills_dirs: list[str] = field(default_factory=lambda: ["~/.sjtuflow/skills"])


@dataclass
class AgentConfig:
    startup_briefing: bool = True
    briefing_window_days: int = 14
    max_tool_calls: int = 12
    max_tool_result_chars: int = 16000


@dataclass
class ASRConfig:
    model: str = "base"
    model_path: str = ""
    download_root: str = ""
    local_files_only: bool = False
    device: str = "cpu"
    compute_type: str = "int8"


@dataclass
class PermissionsConfig:
    confirm_local_write: bool = True
    confirm_external_write: bool = True
    confirm_destructive: bool = True
    allow_non_interactive_writes: bool = False


@dataclass
class Config:
    path: Path
    model: ModelConfig = field(default_factory=ModelConfig)
    canvas: CanvasConfig = field(default_factory=CanvasConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    asr: ASRConfig = field(default_factory=ASRConfig)
    permissions: PermissionsConfig = field(default_factory=PermissionsConfig)


def default_config_path() -> Path:
    configured = os.environ.get(CONFIG_ENV)
    if configured:
        return Path(configured).expanduser()
    return Path("~/.sjtuflow/config.toml").expanduser()


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    return value if isinstance(value, dict) else {}


def _dataclass_from(cls: type, values: dict[str, Any]):
    names = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
    return cls(**{key: value for key, value in values.items() if key in names})


def load_raw_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or default_config_path()
    if not config_path.exists():
        return {}
    with config_path.open("rb") as handle:
        return tomllib.load(handle)


def load_config(path: Path | None = None) -> Config:
    config_path = path or default_config_path()
    raw = load_raw_config(config_path)
    return Config(
        path=config_path,
        model=_dataclass_from(ModelConfig, _section(raw, "model")),
        canvas=_dataclass_from(CanvasConfig, _section(raw, "canvas")),
        workspace=_dataclass_from(WorkspaceConfig, _section(raw, "workspace")),
        agent=_dataclass_from(AgentConfig, _section(raw, "agent")),
        asr=_dataclass_from(ASRConfig, _section(raw, "asr")),
        permissions=_dataclass_from(PermissionsConfig, _section(raw, "permissions")),
    )


DEFAULT_CONFIG_TEXT = """# SJTUFlow configuration.
# This follows the same spirit as Codex-style config.toml: local, explicit,
# and easy to edit by hand.

[model]
# Use "openai-compatible" for a real model endpoint, or "mock" for dry runs.
provider = "mock"
model = "gpt-4.1-mini"
endpoint = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
api_key = ""
timeout_seconds = 60
temperature = 0.2

[canvas]
# SJTU Canvas is Canvas LMS style. Create/paste a Canvas access token here or
# set the environment variable below. Account/password are placeholders for
# future auth flows and are not used by the MVP connector.
base_url = "https://oc.sjtu.edu.cn"
access_token_env = "SJTU_CANVAS_TOKEN"
access_token = ""
account = ""
password = ""
timeout_seconds = 60

[workspace]
state_dir = "~/.sjtuflow"
data_dir = "~/SJTUFlowData"
skills_dirs = ["~/.sjtuflow/skills"]

[agent]
startup_briefing = true
briefing_window_days = 14
max_tool_calls = 12
max_tool_result_chars = 16000

[asr]
# Local transcription uses faster-whisper. When model_path is empty, model is
# resolved through the Hugging Face cache/download flow.
model = "base"
model_path = ""
download_root = ""
local_files_only = false
device = "cpu"
compute_type = "int8"

[permissions]
confirm_local_write = true
confirm_external_write = true
confirm_destructive = true
allow_non_interactive_writes = false
"""


def ensure_default_config(path: Path | None = None, *, force: bool = False) -> Path:
    config_path = path or default_config_path()
    if config_path.exists() and not force:
        return config_path
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(DEFAULT_CONFIG_TEXT, encoding="utf-8")
    return config_path


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def config_to_toml(config: Config, *, reveal_secrets: bool = False) -> str:
    raw = {
        "model": config.model.__dict__.copy(),
        "canvas": config.canvas.__dict__.copy(),
        "workspace": config.workspace.__dict__.copy(),
        "agent": config.agent.__dict__.copy(),
        "asr": config.asr.__dict__.copy(),
        "permissions": config.permissions.__dict__.copy(),
    }
    if not reveal_secrets:
        raw["model"]["api_key"] = "***" if raw["model"].get("api_key") else ""
        raw["canvas"]["access_token"] = "***" if raw["canvas"].get("access_token") else ""
        raw["canvas"]["password"] = "***" if raw["canvas"].get("password") else ""
    lines: list[str] = []
    for section, values in raw.items():
        lines.append(f"[{section}]")
        for key, value in values.items():
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_scalar(value: str) -> Any:
    lower = value.lower()
    if lower in {"true", "false"}:
        return lower == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    if value.startswith("[") and value.endswith("]"):
        items = [item.strip().strip('"').strip("'") for item in value[1:-1].split(",") if item.strip()]
        return items
    return value


def set_config_value(path: Path, dotted_key: str, value: Any) -> None:
    raw = load_raw_config(path)
    section, _, key = dotted_key.partition(".")
    if not section or not key:
        raise ValueError("Use a dotted key such as model.provider or canvas.access_token")
    raw.setdefault(section, {})
    if not isinstance(raw[section], dict):
        raise ValueError(f"Section {section!r} is not an object")
    raw[section][key] = value
    lines: list[str] = []
    for section_name, values in raw.items():
        if not isinstance(values, dict):
            continue
        lines.append(f"[{section_name}]")
        for item_key, item_value in values.items():
            lines.append(f"{item_key} = {_toml_value(item_value)}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
