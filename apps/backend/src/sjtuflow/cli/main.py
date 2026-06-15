from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sjtuflow.agent.loop import AgentLoop
from sjtuflow.agent.briefing import briefing_to_text
from sjtuflow.llm.mock_provider import build_provider
from sjtuflow.runtime import build_app_context
from sjtuflow.storage.config import (
    config_to_toml,
    default_config_path,
    ensure_default_config,
    load_config,
    parse_scalar,
    set_config_value,
)
from sjtuflow.tools import build_registry


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command

    if command == "config":
        return handle_config(args)
    if command == "doctor":
        return handle_doctor(args)
    if command == "skills":
        return handle_skills(args)
    if command == "mcp":
        return handle_mcp(args)
    if command in {"web", "server"}:
        return handle_web(args)

    prompt = command
    if args.extra:
        prompt = " ".join([part for part in [prompt, *args.extra] if part])
    return run_agent(prompt=prompt, print_mode=args.print_mode, no_briefing=args.no_briefing)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sjtuflow",
        description="SJTUFlow local app backend and developer CLI.",
    )
    parser.add_argument("-p", "--print", dest="print_mode", action="store_true", help="Run one prompt and print the final answer.")
    parser.add_argument("--no-briefing", action="store_true", help="Skip startup briefing for this session.")
    parser.add_argument("command", nargs="?", help='Prompt text, or meta command: web, config, doctor, skills, mcp.')
    parser.add_argument("extra", nargs=argparse.REMAINDER, help="Extra prompt text.")
    return parser


def run_agent(*, prompt: str | None, print_mode: bool, no_briefing: bool) -> int:
    config = load_config()
    app = build_app_context(config)
    registry = build_registry()
    provider = build_provider(config.model.provider, config.model)
    loop = AgentLoop(app=app, provider=provider, registry=registry, interactive=not print_mode)
    try:
        loop.start(run_briefing=not no_briefing)
    except Exception as exc:
        if not print_mode:
            print(f"Startup briefing warning: {exc}")
        loop.start(run_briefing=False)

    if not print_mode and loop.briefing:
        print(briefing_to_text(loop.briefing))
        print()

    if prompt:
        try:
            result = loop.run_user_message(prompt)
        except RuntimeError as exc:
            print(f"SJTUFlow error: {exc}", file=sys.stderr)
            print(
                "Tip: run `uv run sjtuflow doctor`, then verify model.provider, model.endpoint, "
                "model.model, and your model API key.",
                file=sys.stderr,
            )
            return 1
        if result.final_text:
            print(result.final_text)
        if print_mode:
            return 0

    if print_mode:
        print("sjtuflow -p requires a prompt.", file=sys.stderr)
        return 2

    print("SJTUFlow developer CLI session. Type /help, /doctor, /skills, /clear, or /exit.")
    while True:
        try:
            line = input("\nsjtuflow> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in {"/exit", "/quit", "exit", "quit"}:
            return 0
        if line == "/help":
            print("Ask in natural language. Slash commands: /doctor /skills /clear /exit")
            continue
        if line == "/doctor":
            handle_doctor(argparse.Namespace())
            continue
        if line == "/skills":
            handle_skills(argparse.Namespace())
            continue
        if line == "/clear":
            loop.start(run_briefing=False)
            print("Session context cleared.")
            continue
        try:
            result = loop.run_user_message(line)
        except RuntimeError as exc:
            print(f"SJTUFlow error: {exc}")
            print("Tip: check model endpoint/key with `uv run sjtuflow doctor`.")
            continue
        if result.final_text:
            print(result.final_text)


def handle_config(args) -> int:
    config_path = default_config_path()
    extra = getattr(args, "extra", [])
    if not extra:
        ensure_default_config(config_path)
        config = load_config(config_path)
        print(f"Config: {config_path}")
        print(config_to_toml(config))
        return 0

    action = extra[0]
    if action == "init":
        path = ensure_default_config(config_path, force="--force" in extra)
        print(f"Wrote config template: {path}")
        return 0
    if action == "path":
        print(config_path)
        return 0
    if action == "set":
        if len(extra) < 3:
            print("Usage: sjtuflow config set section.key value", file=sys.stderr)
            return 2
        ensure_default_config(config_path)
        set_config_value(config_path, extra[1], parse_scalar(" ".join(extra[2:])))
        print(f"Updated {extra[1]} in {config_path}")
        return 0

    print("Usage: sjtuflow config [init|path|set section.key value]", file=sys.stderr)
    return 2


def handle_doctor(args) -> int:
    ensure_default_config()
    config = load_config()
    app = build_app_context(config)
    registry = build_registry()
    print("SJTUFlow doctor")
    print(f"- config: {config.path} ({'exists' if config.path.exists() else 'missing'})")
    print(f"- state_dir: {app.workspace.state_dir}")
    print(f"- data_dir: {app.workspace.data_dir}")
    print(f"- model.provider: {config.model.provider}")
    print(f"- model.endpoint: {config.model.endpoint}")
    print(f"- model key: {'set' if config.model.resolved_api_key() else 'missing'} ({config.model.api_key_env})")
    print(f"- canvas.base_url: {config.canvas.base_url}")
    print(f"- canvas token: {'set' if config.canvas.resolved_token() else 'missing'} ({config.canvas.access_token_env})")
    print(f"- skills loaded: {len(app.skills.list_metadata())}")
    print(f"- tools registered: {len(registry.list())}")
    return 0


def handle_skills(args) -> int:
    ensure_default_config()
    config = load_config()
    app = build_app_context(config)
    skills = app.skills.list_metadata()
    if not skills:
        print("No skills found.")
        return 0
    for skill in skills:
        print(f"{skill.name}\n  {skill.path}\n  {skill.description}")
    return 0


def handle_mcp(args) -> int:
    from sjtuflow.tools.mcp_server import run_mcp_server

    extra = getattr(args, "extra", [])
    if "-h" in extra or "--help" in extra:
        print("Usage: sjtuflow mcp")
        print("Starts the SJTUFlow FastMCP server on stdio.")
        return 0

    ensure_default_config()
    config = load_config()
    app = build_app_context(config)
    run_mcp_server(app)
    return 0


def handle_web(args) -> int:
    from sjtuflow.web.server import main as web_main

    return web_main(getattr(args, "extra", []))


if __name__ == "__main__":
    raise SystemExit(main())
