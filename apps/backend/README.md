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

Canvas media pages are resolved through a SJTUFlow-managed local browser
profile under the state directory. Install the browser runtime once with:

```bash
uv run playwright install chromium
```
