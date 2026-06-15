from __future__ import annotations

import argparse
import webbrowser

import uvicorn

from sjtuflow.web.app import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sjtuflow-web", description="Run the local SJTUFlow browser backend.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host. Keep 127.0.0.1 for local-only use.")
    parser.add_argument("--port", type=int, default=8765, help="Bind port.")
    parser.add_argument("--no-open", action="store_true", help="Do not open a browser window.")
    args = parser.parse_args(argv)

    url = f"http://{args.host}:{args.port}"
    if not args.no_open:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    uvicorn.run(create_app(), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
