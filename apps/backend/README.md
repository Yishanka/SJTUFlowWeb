# SJTUFlow Backend

Local Python backend for SJTUFlow.

Package source lives in `apps/backend/src/sjtuflow`, while the root
`pyproject.toml` keeps `uv run sjtuflow ...` working from the monorepo root.

Useful commands:

```bash
uv run sjtuflow web
uv run sjtuflow doctor
uv run sjtuflow config
uv run python apps/backend/main.py doctor
```
