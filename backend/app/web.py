from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route


def bundled_frontend_directory() -> Path | None:
    directory = Path(__file__).resolve().parent / "static"
    return directory if (directory / "index.html").is_file() else None


def mount_frontend(application: FastAPI, directory: Path | None = None) -> bool:
    frontend_directory = directory or bundled_frontend_directory()
    if frontend_directory is None:
        return False
    index = frontend_directory / "index.html"
    if not index.is_file():
        raise RuntimeError(f"frontend index is missing: {index}")

    def file_endpoint(path: Path) -> Callable[[Request], Awaitable[Response]]:
        async def serve(_request: Request) -> Response:
            return FileResponse(path)

        return serve

    application.router.routes.append(
        Route("/", endpoint=file_endpoint(index), methods=["GET", "HEAD"], name="frontend-index")
    )
    for path in sorted(frontend_directory.iterdir()):
        if path.is_file() and path.name != "index.html":
            application.router.routes.append(
                Route(
                    f"/{path.name}",
                    endpoint=file_endpoint(path),
                    methods=["GET", "HEAD"],
                    name=f"frontend-{path.name}",
                )
            )
    assets = frontend_directory / "assets"
    if assets.is_dir():
        application.mount("/assets", StaticFiles(directory=assets), name="frontend-assets")
    return True
