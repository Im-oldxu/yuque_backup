from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.models import Base, Operation, QueueItem, YuqueCredential
from app.core.security import encrypt_token
from app.integrations.yuque.client import (
    YuqueAuthError,
    YuqueClient,
    YuqueNotFoundError,
    YuqueQuotaError,
    YuqueResponseError,
    YuqueTransientError,
    YuqueUnsafeRedirectError,
)
from app.worker.service import WorkerService

BASE_URL = "https://api.example.test"
SECRET_TOKEN = "yuque-secret-token-that-must-never-leak"
FIXED_NOW = datetime(2026, 7, 24, 4, 30, tzinfo=UTC)
RATE_HEADERS = {
    "X-RateLimit-Limit": "5000",
    "X-RateLimit-Remaining": "4999",
}


def _exception_text(error: BaseException) -> str:
    parts: list[str] = []
    current: BaseException | None = error
    while current is not None:
        parts.extend((str(current), repr(current)))
        current = current.__cause__
    return "\n".join(parts)


@pytest.mark.asyncio
async def test_client_uses_only_get_and_preserves_documented_pagination_parameters() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": []}, headers=RATE_HEADERS)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport_client:
        client = YuqueClient(BASE_URL, SECRET_TOKEN, client=transport_client)
        await client.list_user_groups("user-1", offset=11, limit=22)
        await client.list_user_repositories("alice", offset=23, limit=24, repo_type="Book")
        await client.list_group_repositories("team", offset=25, limit=26, repo_type=None)
        await client.get_repository("book-1")
        await client.get_toc("book-1")
        await client.list_documents(
            "book-1",
            offset=27,
            limit=28,
            changed_at_gte=datetime(2026, 7, 23, 12, 13, 14, tzinfo=UTC),
            deleted=True,
        )
        await client.get_document("doc-1", page=3, page_size=200)

    assert requests
    assert {request.method for request in requests} == {"GET"}
    assert all(request.headers["X-Auth-Token"] == SECRET_TOKEN for request in requests)
    assert [(request.url.path, dict(request.url.params)) for request in requests] == [
        ("/api/v2/users/user-1/groups", {"offset": "11", "limit": "22"}),
        (
            "/api/v2/users/alice/repos",
            {"offset": "23", "limit": "24", "type": "Book"},
        ),
        ("/api/v2/groups/team/repos", {"offset": "25", "limit": "26"}),
        ("/api/v2/repos/book-1", {}),
        ("/api/v2/repos/book-1/toc", {}),
        (
            "/api/v2/repos/book-1/docs",
            {
                "offset": "27",
                "limit": "28",
                "deleted": "true",
                "changed_at_gte": "2026-07-23T12:13:14Z",
                "optional_properties": "latest_version_id",
            },
        ),
        ("/api/v2/repos/docs/doc-1", {"page": "3", "page_size": "200"}),
    ]


@pytest.mark.asyncio
async def test_same_origin_redirect_is_followed_with_get_and_authentication() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(307, headers={"Location": "/api/v2/user?redirected=true"})
        return httpx.Response(200, json={"data": {"id": "u1"}}, headers=RATE_HEADERS)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport_client:
        payload = await YuqueClient(
            BASE_URL,
            SECRET_TOKEN,
            client=transport_client,
        ).get_current_subject()

    assert payload.data == {"id": "u1"}
    assert [(request.method, str(request.url)) for request in requests] == [
        ("GET", f"{BASE_URL}/api/v2/user"),
        ("GET", f"{BASE_URL}/api/v2/user?redirected=true"),
    ]
    assert all(request.headers["X-Auth-Token"] == SECRET_TOKEN for request in requests)


@pytest.mark.asyncio
async def test_cross_origin_redirect_is_rejected_before_token_can_be_forwarded() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://attacker.example/api/v2/user"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport_client:
        client = YuqueClient(BASE_URL, SECRET_TOKEN, client=transport_client)
        with pytest.raises(YuqueUnsafeRedirectError) as caught:
            await client.get_current_subject()

    assert len(requests) == 1
    assert requests[0].url.host == "api.example.test"
    assert SECRET_TOKEN not in _exception_text(caught.value)


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (401, YuqueAuthError),
        (403, YuqueAuthError),
        (404, YuqueNotFoundError),
    ],
)
@pytest.mark.asyncio
async def test_permanent_http_errors_are_mapped_without_response_or_token_leakage(
    status_code: int,
    error_type: type[YuqueAuthError] | type[YuqueNotFoundError],
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            json={"message": f"upstream echoed {SECRET_TOKEN}"},
            headers=RATE_HEADERS,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport_client:
        client = YuqueClient(BASE_URL, SECRET_TOKEN, client=transport_client, now=lambda: FIXED_NOW)
        with pytest.raises(error_type) as caught:
            await client.get_current_subject()

    assert caught.value.status_code == status_code
    assert caught.value.rate_limit is not None
    assert caught.value.rate_limit.limit == 5000
    assert caught.value.rate_limit.remaining == 4999
    assert SECRET_TOKEN not in _exception_text(caught.value)


@pytest.mark.parametrize("retry_after", ["73", "73.0"])
@pytest.mark.asyncio
async def test_429_retry_after_seconds_is_parsed(retry_after: str) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"message": SECRET_TOKEN},
            headers={**RATE_HEADERS, "Retry-After": retry_after},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport_client:
        client = YuqueClient(BASE_URL, SECRET_TOKEN, client=transport_client, now=lambda: FIXED_NOW)
        with pytest.raises(YuqueQuotaError) as caught:
            await client.get_current_subject()

    assert caught.value.retry_after_seconds == 73
    assert caught.value.status_code == 429
    assert SECRET_TOKEN not in _exception_text(caught.value)


@pytest.mark.asyncio
async def test_429_retry_after_http_date_is_parsed_against_response_time() -> None:
    retry_at = FIXED_NOW + timedelta(seconds=91)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"message": SECRET_TOKEN},
            headers={**RATE_HEADERS, "Retry-After": format_datetime(retry_at, usegmt=True)},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport_client:
        client = YuqueClient(BASE_URL, SECRET_TOKEN, client=transport_client, now=lambda: FIXED_NOW)
        with pytest.raises(YuqueQuotaError) as caught:
            await client.get_current_subject()

    assert caught.value.retry_after_seconds == 91
    assert SECRET_TOKEN not in _exception_text(caught.value)


@pytest.mark.parametrize("status_code", [408, 500, 502, 503, 599])
@pytest.mark.asyncio
async def test_temporary_http_errors_map_to_transient_error(status_code: int) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text=SECRET_TOKEN, headers=RATE_HEADERS)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport_client:
        client = YuqueClient(BASE_URL, SECRET_TOKEN, client=transport_client, now=lambda: FIXED_NOW)
        with pytest.raises(YuqueTransientError) as caught:
            await client.get_current_subject()

    assert caught.value.status_code == status_code
    assert SECRET_TOKEN not in _exception_text(caught.value)


@pytest.mark.parametrize("network_error", [httpx.ConnectError, httpx.ReadTimeout])
@pytest.mark.asyncio
async def test_network_and_timeout_exceptions_map_to_transient_error(
    network_error: type[httpx.ConnectError] | type[httpx.ReadTimeout],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise network_error("upstream unavailable", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport_client:
        client = YuqueClient(BASE_URL, SECRET_TOKEN, client=transport_client)
        with pytest.raises(YuqueTransientError) as caught:
            await client.get_current_subject()

    assert caught.value.status_code is None
    assert isinstance(caught.value.__cause__, network_error)
    assert SECRET_TOKEN not in _exception_text(caught.value)


@pytest.mark.asyncio
async def test_local_protocol_error_is_terminal_and_drops_sensitive_exception_context() -> None:
    unsafe_token = f"{SECRET_TOKEN}\nsmuggled-header"

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.headers["X-Auth-Token"]
        raise httpx.LocalProtocolError(f"Illegal header value {token!r}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport_client:
        client = YuqueClient(BASE_URL, unsafe_token, client=transport_client)
        with pytest.raises(YuqueResponseError) as caught:
            await client.get_current_subject()

    assert caught.value.code == "YUQUE_RESPONSE_ERROR"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert unsafe_token not in str(caught.value)
    assert unsafe_token not in repr(caught.value)


@pytest.mark.asyncio
async def test_non_json_success_response_is_rejected_without_body_leakage() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=f"<html>{SECRET_TOKEN}</html>",
            headers={**RATE_HEADERS, "Content-Type": "text/html"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport_client:
        client = YuqueClient(BASE_URL, SECRET_TOKEN, client=transport_client)
        with pytest.raises(YuqueResponseError) as caught:
            await client.get_current_subject()

    assert caught.value.status_code == 200
    assert SECRET_TOKEN not in _exception_text(caught.value)


@pytest.mark.asyncio
async def test_success_response_missing_data_envelope_is_rejected() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"meta": {"request_id": "upstream-1"}}, headers=RATE_HEADERS)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport_client:
        client = YuqueClient(BASE_URL, SECRET_TOKEN, client=transport_client)
        with pytest.raises(YuqueResponseError, match=r"unexpected|missing"):
            await client.get_current_subject()


def _make_session_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'yuque-contract.sqlite3'}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _make_settings(tmp_path: Path) -> Settings:
    return Settings(
        app_master_key="11" * 32,
        data_root=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'yuque-contract.sqlite3'}",
        yuque_request_interval_seconds=0,
    )


def _seed_verification(
    sessions: sessionmaker[Session],
    settings: Settings,
    *,
    credential_id: str,
    token: str = SECRET_TOKEN,
) -> None:
    encrypted, nonce = encrypt_token(token, credential_id, settings)
    with sessions.begin() as session:
        credential = YuqueCredential(
            id=credential_id,
            name="contract-test",
            base_url=BASE_URL,
            encrypted_token=encrypted,
            token_nonce=nonce,
            token_suffix="leak",
            subject_type="user",
            subject_id="old-user",
            login="old-login",
            status="valid",
            enabled=True,
            verification_valid=True,
        )
        operation = Operation(type="credential_verify", credential_id=credential_id, status="queued")
        session.add_all([credential, operation])
        session.flush()
        session.add(
            QueueItem(
                category="credential_verify",
                payload={},
                available_at=FIXED_NOW,
                idempotency_key=f"operation:{operation.id}",
                operation_id=operation.id,
                credential_id=credential_id,
            )
        )


@pytest.mark.asyncio
async def test_worker_401_invalidates_credential_and_fails_operation_durably(tmp_path: Path) -> None:
    sessions = _make_session_factory(tmp_path)
    settings = _make_settings(tmp_path)
    credential_id = "55555555-5555-4555-8555-555555555555"
    _seed_verification(sessions, settings, credential_id=credential_id)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v2/user"
        return httpx.Response(
            401,
            json={"message": SECRET_TOKEN},
            headers=RATE_HEADERS,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport_client:
        service = WorkerService(
            sessions,
            settings,
            worker_id="contract-worker",
            yuque_http_client=transport_client,
            resource_http_client=transport_client,
            now=lambda: FIXED_NOW,
        )
        assert await service.run_once() is True
        await service.aclose()

    with sessions() as session:
        credential = session.get(YuqueCredential, credential_id)
        operation = session.scalar(select(Operation))
        queue_item = session.scalar(select(QueueItem))
        assert credential is not None
        assert credential.status == "action_required"
        assert credential.enabled is False
        assert credential.verification_valid is False
        assert credential.last_error_code == "YUQUE_AUTH_FAILED"
        assert credential.pause_reason == "authentication"
        assert operation is not None
        assert operation.status == "failed"
        assert operation.error == {
            "code": "YUQUE_AUTH_FAILED",
            "message": "Yuque rejected the configured credential",
        }
        assert queue_item is not None
        assert queue_item.status == "failed"
        assert queue_item.last_error_code == "YUQUE_AUTH_FAILED"
        assert SECRET_TOKEN not in str(operation.error)
        assert SECRET_TOKEN not in str(queue_item.last_error_message)


@pytest.mark.asyncio
async def test_worker_contains_local_protocol_error_and_persists_only_safe_failure(tmp_path: Path) -> None:
    sessions = _make_session_factory(tmp_path)
    settings = _make_settings(tmp_path)
    credential_id = "77777777-7777-4777-8777-777777777777"
    unsafe_token = f"{SECRET_TOKEN}\nsmuggled-header"
    _seed_verification(
        sessions,
        settings,
        credential_id=credential_id,
        token=unsafe_token,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.headers["X-Auth-Token"]
        raise httpx.LocalProtocolError(f"Illegal header value {token!r}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport_client:
        service = WorkerService(
            sessions,
            settings,
            worker_id="protocol-error-worker",
            yuque_http_client=transport_client,
            resource_http_client=transport_client,
            now=lambda: FIXED_NOW,
        )
        assert await service.run_once() is True
        assert await service.run_once() is False
        await service.aclose()

    with sessions() as session:
        credential = session.get(YuqueCredential, credential_id)
        operation = session.scalar(select(Operation))
        queue_item = session.scalar(select(QueueItem))
        assert credential is not None
        assert credential.status == "action_required"
        assert credential.enabled is False
        assert credential.verification_valid is False
        assert credential.last_error_code == "YUQUE_RESPONSE_ERROR"
        assert operation is not None
        assert operation.status == "failed"
        assert operation.error == {
            "code": "YUQUE_RESPONSE_ERROR",
            "message": "Yuque response could not be processed",
        }
        assert queue_item is not None
        assert queue_item.status == "failed"
        assert queue_item.last_error_code == "YUQUE_RESPONSE_ERROR"
        persisted = f"{operation.error!r}\n{queue_item.last_error_message!r}"
        assert unsafe_token not in persisted


@pytest.mark.asyncio
async def test_worker_rejects_subject_response_missing_required_identity_fields(tmp_path: Path) -> None:
    sessions = _make_session_factory(tmp_path)
    settings = _make_settings(tmp_path)
    credential_id = "66666666-6666-4666-8666-666666666666"
    _seed_verification(sessions, settings, credential_id=credential_id)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": {"type": "user"}},
            headers=RATE_HEADERS,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport_client:
        service = WorkerService(
            sessions,
            settings,
            worker_id="contract-worker",
            yuque_http_client=transport_client,
            resource_http_client=transport_client,
            now=lambda: FIXED_NOW,
        )
        assert await service.run_once() is True
        await service.aclose()

    with sessions() as session:
        credential = session.get(YuqueCredential, credential_id)
        operation = session.scalar(select(Operation))
        assert credential is not None
        assert credential.status == "action_required"
        assert credential.verification_valid is False
        assert credential.last_error_code == "YUQUE_RESPONSE_ERROR"
        assert operation is not None
        assert operation.status == "failed"
        assert operation.error is not None
        assert operation.error["code"] == "YUQUE_RESPONSE_ERROR"
