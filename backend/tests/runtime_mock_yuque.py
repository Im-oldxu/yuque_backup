from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Query, Request, Response

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
RATE_HEADERS = {"X-RateLimit-Limit": "5000", "X-RateLimit-Remaining": "4999"}


@app.get("/api/v2/user")
def current_user() -> dict[str, object]:
    return {
        "data": {
            "id": "runtime-user",
            "login": "runtime-admin",
            "type": "user",
        }
    }


@app.get("/api/v2/users/runtime-admin/repos")
def user_repositories(
    offset: int = Query(default=0),
    limit: int = Query(default=100),
    type: str = Query(default="Book"),
) -> dict[str, object]:
    assert offset == 0
    assert limit == 100
    assert type == "Book"
    return {
        "data": [
            {
                "id": "runtime-book",
                "name": "Runtime Acceptance Book",
                "slug": "runtime-book",
                "namespace": "runtime-admin/runtime-book",
                "type": "Book",
            }
        ]
    }


@app.get("/api/v2/users/runtime-user/groups")
def user_groups(offset: int = 0, limit: int = 100) -> dict[str, object]:
    assert offset == 0
    assert limit == 100
    return {"data": []}


@app.get("/api/v2/repos/runtime-book")
def repository() -> dict[str, object]:
    return {
        "data": {
            "id": "runtime-book",
            "name": "Runtime Acceptance Book",
            "slug": "runtime-book",
            "namespace": "runtime-admin/runtime-book",
            "type": "Book",
            "content_updated_at": "2026-07-23T12:00:00Z",
        }
    }


@app.get("/api/v2/repos/runtime-book/toc")
def toc() -> dict[str, object]:
    return {
        "data": [
            {
                "uuid": "runtime-toc",
                "doc_id": "runtime-doc",
                "type": "DOC",
                "title": "Runtime Document",
            }
        ]
    }


@app.get("/api/v2/repos/runtime-book/docs")
def documents(
    offset: int = 0,
    limit: int = 100,
    deleted: str | None = None,
) -> dict[str, object]:
    assert offset == 0
    assert limit == 100
    if deleted == "true":
        return {"data": []}
    return {
        "data": [
            {
                "id": "runtime-doc",
                "title": "Runtime Document",
                "slug": "runtime-document",
                "type": "Doc",
                "latest_version_id": "runtime-v1",
                "updated_at": "2026-07-23T12:00:00Z",
            }
        ]
    }


@app.get("/api/v2/repos/docs/runtime-doc")
def document(page: int = 1, page_size: int = 200) -> dict[str, object]:
    assert page == 1
    assert page_size == 200
    token = os.environ["RUNTIME_YUQUE_TOKEN"]
    return {
        "data": {
            "id": "runtime-doc",
            "title": "Runtime Document",
            "slug": "runtime-document",
            "type": "Doc",
            "format": "markdown",
            "body": f"# Runtime Document\n\nCredential echo: {token}",
            "body_html": f"<h1>Runtime Document</h1><p>Credential echo: {token}</p>",
            "latest_version_id": "runtime-v1",
            "updated_at": "2026-07-23T12:00:00Z",
        }
    }


@app.middleware("http")
async def rate_headers(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    response = await call_next(request)
    for name, value in RATE_HEADERS.items():
        response.headers[name] = value
    return response
