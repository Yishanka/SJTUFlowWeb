from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from sjtuflow.services.local_app import LocalAppService
from sjtuflow.web.app import create_app, default_frontend_dir


def test_frontend_static_files_are_served(tmp_path):
    frontend = tmp_path / "frontend"
    assets = frontend / "assets"
    assets.mkdir(parents=True)
    (frontend / "index.html").write_text("<!doctype html><div id='app'></div>", encoding="utf-8")
    (frontend / "app.js").write_text("console.log('sjtuflow')", encoding="utf-8")
    (frontend / "styles.css").write_text("body { color: black; }", encoding="utf-8")
    (assets / "logo.txt").write_text("logo", encoding="utf-8")

    client = TestClient(create_app(service=LocalAppService(cwd=tmp_path), frontend_dir=frontend))

    assert client.get("/").status_code == 200
    assert "app" in client.get("/").text
    assert client.get("/app.js").text == "console.log('sjtuflow')"
    assert client.get("/styles.css").text == "body { color: black; }"
    assert client.get("/assets/logo.txt").text == "logo"


def test_default_frontend_dir_uses_source_when_dist_missing():
    path = default_frontend_dir()
    assert path.name in {"frontend", "dist"}
    assert (path / "index.html").exists()
