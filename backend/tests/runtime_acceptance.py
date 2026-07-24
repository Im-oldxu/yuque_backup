from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

TERMINAL_OPERATIONS = {"succeeded", "failed", "cancelled"}
TERMINAL_JOBS = {"succeeded", "partial", "failed", "cancelled"}


def require(response: httpx.Response, status_code: int) -> httpx.Response:
    if response.status_code != status_code:
        raise RuntimeError(
            f"{response.request.method} {response.request.url}: "
            f"expected {status_code}, got {response.status_code}: {response.text}"
        )
    assert response.headers.get("X-Request-ID")
    return response


def csrf_headers(client: httpx.Client) -> dict[str, str]:
    value = client.cookies.get("yb_csrf")
    if not value:
        raise RuntimeError("CSRF cookie is missing")
    return {"X-CSRF-Token": value}


def login(client: httpx.Client, origin: str, username: str, password: str) -> None:
    require(
        client.post(
            "/api/v1/auth/login",
            headers={"Origin": origin},
            json={"username": username, "password": password},
        ),
        200,
    )


def poll_operation(client: httpx.Client, operation_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        payload = require(client.get(f"/api/v1/operations/{operation_id}"), 200).json()
        if payload["status"] in TERMINAL_OPERATIONS:
            return payload
        time.sleep(0.2)
    raise TimeoutError(f"operation {operation_id} did not finish")


def poll_job(client: httpx.Client, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        payload = require(client.get(f"/api/v1/backup-jobs/{job_id}"), 200).json()
        if payload["status"] in TERMINAL_JOBS:
            return payload
        time.sleep(0.2)
    raise TimeoutError(f"job {job_id} did not finish")


def online(client: httpx.Client, origin: str, state_path: Path) -> None:
    username = os.environ.get("RUNTIME_ADMIN_USERNAME", "runtime-admin")
    password = os.environ["RUNTIME_ADMIN_PASSWORD"]
    token = os.environ["RUNTIME_YUQUE_TOKEN"]
    base_url = os.environ["RUNTIME_YUQUE_BASE_URL"]

    assert require(client.get("/health/live"), 200).json() == {"status": "ok"}
    assert require(client.get("/health/ready"), 200).json() == {"status": "ready"}
    anonymous = require(client.get("/api/v1/credentials"), 401)
    assert anonymous.json()["code"] == "AUTH_REQUIRED"
    assert require(client.get("/api/v1/system/initialization"), 200).json() == {
        "initialized": False
    }
    initialize_response = require(
        client.post(
            "/api/v1/system/initialize",
            headers={"Origin": origin, "Idempotency-Key": str(uuid.uuid4())},
            json={"username": username, "password": password},
        ),
        201,
    )
    assert initialize_response.json()["username"] == username
    assert require(client.get("/api/v1/auth/me"), 200).json()["username"] == username
    invalid_page = require(client.get("/api/v1/documents", params={"page": 0}), 422)
    assert invalid_page.json()["code"] == "VALIDATION_ERROR"
    missing_csrf = require(
        client.put("/api/v1/settings/retention", json={"retention_days": 15}),
        403,
    )
    assert missing_csrf.json()["code"] == "CSRF_INVALID"

    credential_response = require(
        client.post(
            "/api/v1/credentials",
            headers=csrf_headers(client),
            json={"name": "runtime", "base_url": base_url, "token": token},
        ),
        202,
    )
    assert token not in credential_response.text
    credential_payload = credential_response.json()
    credential_id = credential_payload["credential"]["id"]
    verification = poll_operation(client, credential_payload["operation"]["id"])
    assert verification["status"] == "succeeded", verification

    require(
        client.post(
            f"/api/v1/credentials/{credential_id}/enable",
            headers=csrf_headers(client),
        ),
        200,
    )
    discovery_response = require(
        client.post(
            f"/api/v1/credentials/{credential_id}/discover-repositories",
            headers=csrf_headers(client),
        ),
        202,
    ).json()
    discovery = poll_operation(client, discovery_response["id"])
    assert discovery["status"] == "succeeded", discovery

    repositories = require(client.get("/api/v1/repositories"), 200).json()["items"]
    assert len(repositories) == 1
    repository_id = repositories[0]["id"]
    assert repositories[0]["selected"] is True

    idempotency_key = str(uuid.uuid4())
    job_response = require(
        client.post(
            "/api/v1/backup-jobs",
            headers={**csrf_headers(client), "Idempotency-Key": idempotency_key},
            json={"scope": {"type": "repository", "repository_id": repository_id}},
        ),
        202,
    )
    replay_response = require(
        client.post(
            "/api/v1/backup-jobs",
            headers={**csrf_headers(client), "Idempotency-Key": idempotency_key},
            json={"scope": {"type": "repository", "repository_id": repository_id}},
        ),
        202,
    )
    assert replay_response.json() == job_response.json()
    job = poll_job(client, job_response.json()["job"]["id"])
    assert job["status"] == "succeeded", job

    documents = require(
        client.get("/api/v1/documents", params={"repository_id": repository_id}), 200
    ).json()["items"]
    assert len(documents) == 1
    document_id = documents[0]["id"]
    versions = require(client.get(f"/api/v1/documents/{document_id}/versions"), 200).json()[
        "items"
    ]
    assert len(versions) == 1
    version_id = versions[0]["id"]
    preview = require(
        client.get(f"/api/v1/documents/{document_id}/versions/{version_id}/preview"), 200
    )
    assert preview.headers["content-type"].lower().count("charset=") == 1
    raw = require(
        client.get(
            f"/api/v1/documents/{document_id}/versions/{version_id}/downloads/raw-response"
        ),
        200,
    )
    assert token not in preview.text
    assert token not in raw.text
    assert "[REDACTED]" in raw.text
    ranged = require(
        client.get(
            f"/api/v1/documents/{document_id}/versions/{version_id}/downloads/raw-response",
            headers={"Range": "bytes=0-9"},
        ),
        206,
    )
    assert len(ranged.content) == 10
    assert ranged.headers["content-range"].startswith("bytes 0-9/")
    assert ranged.headers["accept-ranges"] == "bytes"
    content_root = Path(os.environ["RUNTIME_DATA_ROOT"]) / "content"
    for stored_file in content_root.rglob("*"):
        if stored_file.is_file():
            assert token.encode() not in stored_file.read_bytes(), stored_file

    require(
        client.put(
            "/api/v1/settings/schedule",
            headers=csrf_headers(client),
            json={"cron": "15 3 * * *", "timezone": "Asia/Shanghai"},
        ),
        200,
    )
    require(
        client.put(
            "/api/v1/settings/retention",
            headers=csrf_headers(client),
            json={"retention_days": 15},
        ),
        200,
    )
    dashboard = require(client.get("/api/v1/dashboard/summary"), 200).json()
    assert dashboard["worker"]["status"] == "online"
    assert dashboard["documents"] == 1

    state_path.write_text(
        json.dumps(
            {
                "username": username,
                "repository_id": repository_id,
                "document_id": document_id,
                "version_id": version_id,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def offline(client: httpx.Client, origin: str, state_path: Path) -> None:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    password = os.environ["RUNTIME_ADMIN_PASSWORD"]
    token = os.environ["RUNTIME_YUQUE_TOKEN"]
    login(client, origin, state["username"], password)
    require(client.get(f"/api/v1/repositories/{state['repository_id']}/toc"), 200)
    require(client.get("/api/v1/search", params={"q": "Runtime"}), 200)
    preview = require(
        client.get(
            f"/api/v1/documents/{state['document_id']}/versions/{state['version_id']}/preview"
        ),
        200,
    )
    raw_body = require(
        client.get(
            f"/api/v1/documents/{state['document_id']}/versions/"
            f"{state['version_id']}/downloads/raw-body"
        ),
        200,
    )
    assert token not in preview.text
    assert token not in raw_body.text


def cancel_create(client: httpx.Client, origin: str, state_path: Path) -> None:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    login(client, origin, state["username"], os.environ["RUNTIME_ADMIN_PASSWORD"])
    accepted = require(
        client.post(
            "/api/v1/backup-jobs",
            headers={**csrf_headers(client), "Idempotency-Key": str(uuid.uuid4())},
            json={
                "scope": {"type": "repository", "repository_id": state["repository_id"]}
            },
        ),
        202,
    ).json()
    job_id = accepted["job"]["id"]
    cancelled = require(
        client.post(f"/api/v1/backup-jobs/{job_id}/cancel", headers=csrf_headers(client)),
        202,
    ).json()
    assert cancelled["cancel_requested_at"] is not None
    state["cancel_job_id"] = job_id
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def cancel_verify(client: httpx.Client, origin: str, state_path: Path) -> None:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    login(client, origin, state["username"], os.environ["RUNTIME_ADMIN_PASSWORD"])
    job = poll_job(client, state["cancel_job_id"])
    assert job["status"] == "cancelled", job


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["online", "offline", "cancel-create", "cancel-verify"])
    args = parser.parse_args()
    api_url = os.environ.get("RUNTIME_API_URL", "http://127.0.0.1:8765")
    origin = os.environ.get("RUNTIME_ORIGIN", api_url)
    state_path = Path(os.environ["RUNTIME_STATE_PATH"])
    with httpx.Client(base_url=api_url, timeout=10) as client:
        {
            "online": online,
            "offline": offline,
            "cancel-create": cancel_create,
            "cancel-verify": cancel_verify,
        }[args.phase](client, origin, state_path)


if __name__ == "__main__":
    main()
