from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.web import mount_frontend


def test_frontend_mount_serves_index_and_keeps_existing_api_routes(tmp_path: Path) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<main>Yuque Backup</main>", encoding="utf-8")
    (tmp_path / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")

    app = FastAPI()

    @app.get("/api/v1/example")
    def example() -> dict[str, bool]:
        return {"ok": True}

    assert mount_frontend(app, tmp_path) is True
    with TestClient(app) as client:
        assert client.get("/api/v1/example").json() == {"ok": True}
        assert "Yuque Backup" in client.get("/").text
        assert client.get("/assets/app.js").text == "console.log('ok')"
        assert client.get("/missing").status_code == 404
        assert client.post("/api/v1/example").status_code == 405


def test_frontend_mount_requires_an_index_for_explicit_directory(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="frontend index is missing"):
        mount_frontend(FastAPI(), tmp_path)
