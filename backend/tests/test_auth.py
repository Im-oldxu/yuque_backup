from __future__ import annotations

import base64
import os
import sqlite3
import uuid
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from cryptography.exceptions import InvalidTag
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response as HTTPXResponse
from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

os.environ.setdefault("APP_MASTER_KEY", base64.urlsafe_b64encode(b"t" * 32).decode())

from app.api import dependencies as auth_dependencies
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.errors import register_exception_handlers
from app.core.models import Admin, AdminSession, Base, LoginAttempt
from app.core.security import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    TokenDecryptionError,
    decrypt_token,
    encrypt_token,
    mask_token,
)
from app.modules.auth import router as auth_router
from app.modules.auth import service as auth_service

ORIGIN = "http://testserver"
PASSWORD = "initial-password-123"
NEW_PASSWORD = "replacement-password-456"


@pytest.fixture
def app_and_sessions(
    tmp_path: Path,
) -> Generator[tuple[FastAPI, sessionmaker[Session], Settings]]:
    database_path = tmp_path / "auth.sqlite3"
    settings = Settings(
        _env_file=None,
        app_master_key=base64.urlsafe_b64encode(b"k" * 32).decode(),
        data_root=tmp_path,
        database_url=f"sqlite:///{database_path.as_posix()}",
        trusted_origins=ORIGIN,
        secure_cookies=False,
        session_ttl_seconds=3600,
        login_window_seconds=300,
        login_max_failures=2,
        login_block_seconds=300,
    )
    database_url = settings.database_url
    assert database_url is not None
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False},
    )
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_db() -> Generator[Session]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_router)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        yield app, session_factory, settings
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


@pytest.fixture
def client(app_and_sessions: tuple[FastAPI, sessionmaker[Session], Settings]) -> Generator[TestClient]:
    app, _, _ = app_and_sessions
    with TestClient(app) as test_client:
        yield test_client


def initialize(
    client: TestClient,
    *,
    key: str | None = None,
    password: str = PASSWORD,
) -> HTTPXResponse:
    return cast(
        HTTPXResponse,
        client.post(
            "/api/v1/system/initialize",
            headers={"Origin": ORIGIN, "Idempotency-Key": key or str(uuid.uuid4())},
            json={"username": " admin ", "password": password},
        ),
    )


def csrf_headers(client: TestClient) -> dict[str, str]:
    token = client.cookies.get(CSRF_COOKIE)
    assert token is not None
    return {"X-CSRF-Token": token}


def test_initialization_is_singleton_idempotent_and_strict(
    client: TestClient,
    app_and_sessions: tuple[FastAPI, sessionmaker[Session], Settings],
) -> None:
    _, session_factory, _ = app_and_sessions
    assert client.get("/api/v1/system/initialization").json() == {"initialized": False}

    key = str(uuid.uuid4())
    response = initialize(client, key=key)
    assert response.status_code == 201
    assert response.json()["username"] == "admin"
    assert client.get("/api/v1/system/initialization").json() == {"initialized": True}

    replay = initialize(client, key=key)
    assert replay.status_code == 201
    conflict = initialize(client, key=key, password="different-password-789")
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    duplicate = initialize(client)
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "INITIALIZATION_ALREADY_COMPLETED"

    with session_factory() as db:
        assert len(db.scalars(select(Admin)).all()) == 1


def test_initialize_rejects_missing_key_wrong_origin_content_type_and_extra_fields(
    client: TestClient,
) -> None:
    missing_key = client.post(
        "/api/v1/system/initialize",
        headers={"Origin": ORIGIN},
        json={"username": "admin", "password": PASSWORD},
    )
    assert missing_key.status_code == 400
    assert missing_key.json()["code"] == "IDEMPOTENCY_KEY_REQUIRED"

    wrong_origin = client.post(
        "/api/v1/system/initialize",
        headers={"Origin": "https://attacker.example", "Idempotency-Key": str(uuid.uuid4())},
        json={"username": "admin", "password": PASSWORD},
    )
    assert wrong_origin.status_code == 403
    assert wrong_origin.json()["code"] == "CSRF_INVALID"

    no_origin = client.post(
        "/api/v1/system/initialize",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={"username": "admin", "password": PASSWORD},
    )
    assert no_origin.status_code == 403

    wrong_type = client.post(
        "/api/v1/system/initialize",
        headers={
            "Origin": ORIGIN,
            "Idempotency-Key": str(uuid.uuid4()),
            "Content-Type": "text/plain",
        },
        content='{"username":"admin","password":"initial-password-123"}',
    )
    assert wrong_type.status_code == 422

    extra = client.post(
        "/api/v1/system/initialize",
        headers={"Origin": ORIGIN, "Idempotency-Key": str(uuid.uuid4())},
        json={"username": "admin", "password": PASSWORD, "role": "owner"},
    )
    assert extra.status_code == 422
    assert extra.json()["code"] == "VALIDATION_ERROR"
    assert any(item["field"] == "role" for item in extra.json()["field_errors"])


def test_concurrent_initialization_has_exactly_one_success(
    app_and_sessions: tuple[FastAPI, sessionmaker[Session], Settings],
) -> None:
    app, session_factory, _ = app_and_sessions

    def submit(index: int) -> tuple[int, str | None]:
        with TestClient(app) as concurrent_client:
            response = initialize(concurrent_client, key=str(uuid.uuid4()))
            return response.status_code, response.json().get("code")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, range(2)))

    assert sorted(status_code for status_code, _ in results) == [201, 409]
    assert [code for status_code, code in results if status_code == 409] == [
        "INITIALIZATION_ALREADY_COMPLETED"
    ]
    with session_factory() as db:
        assert len(db.scalars(select(Admin)).all()) == 1


def test_login_cookies_session_hash_csrf_and_logout(
    client: TestClient,
    app_and_sessions: tuple[FastAPI, sessionmaker[Session], Settings],
) -> None:
    _, session_factory, _ = app_and_sessions
    response = initialize(client)
    set_cookies = response.headers.get_list("set-cookie")
    session_header = next(value for value in set_cookies if value.startswith(f"{SESSION_COOKIE}="))
    csrf_header = next(value for value in set_cookies if value.startswith(f"{CSRF_COOKIE}="))
    assert "HttpOnly" in session_header
    assert "HttpOnly" not in csrf_header
    assert "SameSite=lax" in session_header
    assert "Path=/" in session_header
    assert "Domain=" not in session_header

    session_token = client.cookies.get(SESSION_COOKIE)
    csrf_token = client.cookies.get(CSRF_COOKIE)
    assert session_token and csrf_token
    with session_factory() as db:
        stored = db.scalar(select(AdminSession).order_by(AdminSession.created_at.asc()))
        assert stored is not None
        assert stored.token_hash != session_token
        assert stored.csrf_hash != csrf_token
        assert len(stored.token_hash) == 64

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "admin"

    assert client.post("/api/v1/auth/logout").status_code == 403
    assert client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    logout = client.post("/api/v1/auth/logout", headers=csrf_headers(client))
    assert logout.status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401


def test_password_change_keeps_current_session_and_revokes_others(
    app_and_sessions: tuple[FastAPI, sessionmaker[Session], Settings],
) -> None:
    app, _, _ = app_and_sessions
    with TestClient(app) as first, TestClient(app) as second:
        initialization_key = str(uuid.uuid4())
        assert initialize(first, key=initialization_key).status_code == 201
        login = second.post(
            "/api/v1/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": "admin", "password": PASSWORD},
        )
        assert login.status_code == 200

        changed = first.put(
            "/api/v1/auth/password",
            headers=csrf_headers(first),
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        )
        assert changed.status_code == 204
        assert first.get("/api/v1/auth/me").status_code == 200
        assert second.get("/api/v1/auth/me").status_code == 401
        stale_replay = initialize(first, key=initialization_key)
        assert stale_replay.status_code == 409
        assert stale_replay.json()["code"] == "INITIALIZATION_ALREADY_COMPLETED"

        with TestClient(app) as login_client:
            old_login = login_client.post(
                "/api/v1/auth/login",
                headers={"Referer": f"{ORIGIN}/login"},
                json={"username": "admin", "password": PASSWORD},
            )
            assert old_login.status_code == 401
            new_login = login_client.post(
                "/api/v1/auth/login",
                headers={"Origin": ORIGIN},
                json={"username": "admin", "password": NEW_PASSWORD},
            )
            assert new_login.status_code == 200


def test_login_rate_limit_has_generic_errors_and_retry_after(
    app_and_sessions: tuple[FastAPI, sessionmaker[Session], Settings],
) -> None:
    app, session_factory, _ = app_and_sessions
    with TestClient(app) as admin_client:
        assert initialize(admin_client).status_code == 201

    with TestClient(app) as login_client:
        body = {"username": "admin", "password": "wrong-password"}
        first = login_client.post("/api/v1/auth/login", headers={"Origin": ORIGIN}, json=body)
        second = login_client.post("/api/v1/auth/login", headers={"Origin": ORIGIN}, json=body)
        limited = login_client.post("/api/v1/auth/login", headers={"Origin": ORIGIN}, json=body)
        assert first.status_code == second.status_code == 401
        assert first.json()["code"] == second.json()["code"] == "INVALID_CREDENTIALS"
        assert limited.status_code == 429
        assert limited.json()["code"] == "LOGIN_RATE_LIMITED"
        assert limited.json()["retry_after_seconds"] > 0
        assert int(limited.headers["Retry-After"]) > 0

    with session_factory() as db:
        attempts = db.scalars(select(LoginAttempt)).all()
        assert len(attempts) == 1
        assert len(attempts[0].key_hash) == 64


def test_login_releases_wal_snapshot_during_password_verification(
    app_and_sessions: tuple[FastAPI, sessionmaker[Session], Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, session_factory, _ = app_and_sessions
    with TestClient(app) as initialization_client:
        assert initialize(initialization_client).status_code == 201

    original_verify = auth_service.verify_password_or_dummy
    concurrent_write_committed = False

    def verify_with_concurrent_write(stored_hash: str | None, password: str) -> bool:
        nonlocal concurrent_write_committed
        result = original_verify(stored_hash, password)
        with session_factory.begin() as concurrent_db:
            stored_admin = concurrent_db.scalar(select(Admin))
            assert stored_admin is not None
            stored_admin.updated_at = datetime.now(UTC)
        concurrent_write_committed = True
        return result

    monkeypatch.setattr(
        auth_service,
        "verify_password_or_dummy",
        verify_with_concurrent_write,
    )
    with TestClient(app) as login_client:
        response = login_client.post(
            "/api/v1/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": "admin", "password": PASSWORD},
        )
    assert concurrent_write_committed is True
    assert response.status_code == 200


def test_expired_session_is_rejected(
    client: TestClient,
    app_and_sessions: tuple[FastAPI, sessionmaker[Session], Settings],
) -> None:
    _, session_factory, _ = app_and_sessions
    assert initialize(client).status_code == 201
    with session_factory.begin() as db:
        stored = db.scalar(select(AdminSession).order_by(AdminSession.created_at.desc()))
        assert stored is not None
        stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"


def test_session_last_used_at_is_throttled_and_refreshed(
    client: TestClient,
    app_and_sessions: tuple[FastAPI, sessionmaker[Session], Settings],
) -> None:
    _, session_factory, _ = app_and_sessions
    assert initialize(client).status_code == 201
    with session_factory() as db:
        stored = db.scalar(select(AdminSession).order_by(AdminSession.created_at.desc()))
        assert stored is not None
        fresh_last_used_at = stored.last_used_at

    assert client.get("/api/v1/auth/me").status_code == 200
    with session_factory() as db:
        stored = db.scalar(select(AdminSession).order_by(AdminSession.created_at.desc()))
        assert stored is not None
        assert stored.last_used_at == fresh_last_used_at

    stale_last_used_at = datetime.now(UTC) - timedelta(minutes=2)
    with session_factory.begin() as db:
        stored = db.scalar(select(AdminSession).order_by(AdminSession.created_at.desc()))
        assert stored is not None
        stored.last_used_at = stale_last_used_at

    assert client.get("/api/v1/auth/me").status_code == 200
    with session_factory() as db:
        stored = db.scalar(select(AdminSession).order_by(AdminSession.created_at.desc()))
        assert stored is not None
        assert stored.last_used_at > stale_last_used_at.replace(tzinfo=None)


def test_session_touch_lock_does_not_fail_authenticated_request(
    client: TestClient,
    app_and_sessions: tuple[FastAPI, sessionmaker[Session], Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, session_factory, _ = app_and_sessions
    assert initialize(client).status_code == 201
    with session_factory.begin() as db:
        stored = db.scalar(select(AdminSession).order_by(AdminSession.created_at.desc()))
        assert stored is not None
        stored.last_used_at = datetime.now(UTC) - timedelta(minutes=2)

    def locked_touch(_db: Session, _session_id: str, _now: datetime) -> None:
        raise OperationalError(
            "UPDATE admin_session",
            {},
            sqlite3.OperationalError("database is locked"),
        )

    monkeypatch.setattr(auth_dependencies, "_touch_admin_session", locked_touch)
    assert client.get("/api/v1/auth/me").status_code == 200


def test_aes_gcm_token_helper_binds_ciphertext_to_credential(
    app_and_sessions: tuple[FastAPI, sessionmaker[Session], Settings],
) -> None:
    _, _, settings = app_and_sessions
    credential_id = str(uuid.uuid4())
    first_ciphertext, first_nonce = encrypt_token("secret-token", credential_id, settings)
    second_ciphertext, second_nonce = encrypt_token("secret-token", credential_id, settings)
    assert first_nonce != second_nonce
    assert first_ciphertext != second_ciphertext

    credential = SimpleNamespace(
        id=credential_id,
        encrypted_token=first_ciphertext,
        token_nonce=first_nonce,
        key_version=1,
    )
    assert decrypt_token(credential, settings) == "secret-token"
    assert mask_token("secret-token") == "************oken"
    assert mask_token("abc") == "***************"

    credential.encrypted_token = bytes([first_ciphertext[0] ^ 1]) + first_ciphertext[1:]
    with pytest.raises(TokenDecryptionError) as exc_info:
        decrypt_token(credential, settings)
    assert isinstance(exc_info.value.__cause__, InvalidTag)

    credential.encrypted_token = first_ciphertext
    credential.id = str(uuid.uuid4())
    with pytest.raises(TokenDecryptionError):
        decrypt_token(credential, settings)
