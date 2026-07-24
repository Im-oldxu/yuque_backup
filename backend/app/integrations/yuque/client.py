from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

import httpx


@dataclass(frozen=True, slots=True)
class RateLimitSnapshot:
    limit: int | None
    remaining: int | None
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class YuquePayload:
    data: Any
    raw: Any
    rate_limit: RateLimitSnapshot


class YuqueError(RuntimeError):
    code = "YUQUE_ERROR"

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        rate_limit: RateLimitSnapshot | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.rate_limit = rate_limit


class YuqueAuthError(YuqueError):
    code = "YUQUE_AUTH_FAILED"


class YuqueNotFoundError(YuqueError):
    code = "YUQUE_NOT_FOUND"


class YuqueQuotaError(YuqueError):
    code = "YUQUE_RATE_LIMITED"

    def __init__(self, *args: Any, retry_after_seconds: int | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.retry_after_seconds = retry_after_seconds


class YuqueTransientError(YuqueError):
    code = "YUQUE_TRANSIENT_ERROR"


class YuqueUnsafeRedirectError(YuqueError):
    code = "YUQUE_UNSAFE_REDIRECT"


class YuqueResponseError(YuqueError):
    code = "YUQUE_RESPONSE_ERROR"


def normalize_base_url(value: str) -> str:
    try:
        url = httpx.URL(value)
    except httpx.InvalidURL as exc:
        raise ValueError("Yuque base URL is invalid") from exc
    if url.scheme != "https" or not url.host:
        raise ValueError("Yuque base URL must be an HTTPS origin")
    if url.path not in ("", "/", b"", b"/") or url.query or url.fragment:
        raise ValueError("Yuque base URL must not contain a path, query, or fragment")
    port = url.port
    authority = url.host.lower()
    if port is not None and port != 443:
        authority = f"{authority}:{port}"
    return f"https://{authority}"


def _origin(url: httpx.URL) -> tuple[str, str, int]:
    default_port = 443 if url.scheme == "https" else 80
    return url.scheme, (url.host or "").lower(), url.port or default_port


def _header_int(headers: Mapping[str, str], name: str) -> int | None:
    value = headers.get(name)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_retry_after(value: str | None, now: datetime) -> int | None:
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            target = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if target.tzinfo is None:
            target = target.replace(tzinfo=UTC)
        seconds = (target.astimezone(UTC) - now.astimezone(UTC)).total_seconds()
    return max(0, math.ceil(seconds))


class YuqueClient:
    """Small, read-only client for the official Yuque OpenAPI surface used by MVP."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: httpx.Timeout | float = 60.0,
        max_redirects: int = 3,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self._base = httpx.URL(self.base_url)
        self._token = token
        self._client = client or httpx.AsyncClient(timeout=timeout, follow_redirects=False)
        self._owns_client = client is None
        self._max_redirects = max_redirects
        self._now = now or (lambda: datetime.now(UTC))

    async def __aenter__(self) -> YuqueClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get(self, path: str, *, params: Mapping[str, Any] | None = None) -> YuquePayload:
        if not path.startswith("/"):
            raise ValueError("Yuque API path must be absolute")
        url = httpx.URL(f"{self.base_url}{path}", params=_clean_params(params))
        redirects = 0
        response: httpx.Response
        while True:
            if _origin(url) != _origin(self._base):
                raise YuqueUnsafeRedirectError("Yuque redirected outside the configured API origin")
            local_protocol_failure = False
            try:
                response = await self._client.request(
                    "GET",
                    url,
                    headers={"X-Auth-Token": self._token, "Accept": "application/json"},
                    follow_redirects=False,
                )
            except (httpx.LocalProtocolError, UnicodeError):
                local_protocol_failure = True
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise YuqueTransientError("Yuque request failed due to a temporary network error") from exc
            if local_protocol_failure:
                # Raise outside the handler so the protocol exception, which may echo a header value,
                # is not retained as __context__ on the safe business error.
                raise YuqueResponseError("Yuque request could not be constructed safely")
            if response.status_code not in {301, 302, 303, 307, 308}:
                break
            location = response.headers.get("Location")
            if not location or redirects >= self._max_redirects:
                raise YuqueUnsafeRedirectError(
                    "Yuque returned an invalid or excessive redirect",
                    status_code=response.status_code,
                )
            next_url = response.url.join(location)
            if _origin(next_url) != _origin(self._base):
                raise YuqueUnsafeRedirectError(
                    "Yuque redirected outside the configured API origin",
                    status_code=response.status_code,
                )
            redirects += 1
            url = next_url

        now = self._now()
        rate_limit = RateLimitSnapshot(
            limit=_header_int(response.headers, "X-RateLimit-Limit"),
            remaining=_header_int(response.headers, "X-RateLimit-Remaining"),
            observed_at=now,
        )
        self._raise_for_status(response, rate_limit, now)
        try:
            raw = response.json()
        except ValueError as exc:
            raise YuqueResponseError(
                "Yuque returned a non-JSON response",
                status_code=response.status_code,
                rate_limit=rate_limit,
            ) from exc
        if not isinstance(raw, dict) or "data" not in raw:
            raise YuqueResponseError(
                "Yuque response is missing the data field",
                status_code=response.status_code,
                rate_limit=rate_limit,
            )
        data = raw["data"]
        return YuquePayload(data=data, raw=raw, rate_limit=rate_limit)

    def _raise_for_status(
        self,
        response: httpx.Response,
        rate_limit: RateLimitSnapshot,
        now: datetime,
    ) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        if status in (401, 403):
            raise YuqueAuthError(
                "Yuque rejected the configured credential",
                status_code=status,
                rate_limit=rate_limit,
            )
        if status == 404:
            raise YuqueNotFoundError(
                "The requested Yuque resource was not found",
                status_code=status,
                rate_limit=rate_limit,
            )
        if status == 429:
            raise YuqueQuotaError(
                "Yuque API quota is temporarily exhausted",
                retry_after_seconds=parse_retry_after(response.headers.get("Retry-After"), now),
                status_code=status,
                rate_limit=rate_limit,
            )
        if status == 408 or status >= 500:
            raise YuqueTransientError(
                "Yuque returned a temporary service error",
                status_code=status,
                rate_limit=rate_limit,
            )
        raise YuqueResponseError(
            "Yuque rejected the read request",
            status_code=status,
            rate_limit=rate_limit,
        )

    async def get_current_subject(self) -> YuquePayload:
        return await self.get("/api/v2/user")

    async def list_user_groups(self, user_id: str, *, offset: int = 0, limit: int = 100) -> YuquePayload:
        return await self.get(
            f"/api/v2/users/{quote(str(user_id), safe='')}/groups",
            params={"offset": offset, "limit": limit},
        )

    async def list_user_repositories(
        self,
        login: str,
        *,
        offset: int = 0,
        limit: int = 100,
        repo_type: str | None = "Book",
    ) -> YuquePayload:
        return await self.get(
            f"/api/v2/users/{quote(login, safe='')}/repos",
            params={"offset": offset, "limit": limit, "type": repo_type},
        )

    async def list_group_repositories(
        self,
        login: str,
        *,
        offset: int = 0,
        limit: int = 100,
        repo_type: str | None = "Book",
    ) -> YuquePayload:
        return await self.get(
            f"/api/v2/groups/{quote(login, safe='')}/repos",
            params={"offset": offset, "limit": limit, "type": repo_type},
        )

    async def get_repository(self, book_id: str) -> YuquePayload:
        return await self.get(f"/api/v2/repos/{quote(str(book_id), safe='')}")

    async def get_toc(self, book_id: str) -> YuquePayload:
        return await self.get(f"/api/v2/repos/{quote(str(book_id), safe='')}/toc")

    async def list_documents(
        self,
        book_id: str,
        *,
        offset: int = 0,
        limit: int = 100,
        changed_at_gte: datetime | None = None,
        deleted: bool = False,
    ) -> YuquePayload:
        return await self.get(
            f"/api/v2/repos/{quote(str(book_id), safe='')}/docs",
            params={
                "offset": offset,
                "limit": limit,
                "deleted": "true" if deleted else None,
                "changed_at_gte": _iso_utc(changed_at_gte) if changed_at_gte else None,
                "optional_properties": "latest_version_id",
            },
        )

    async def get_document(self, doc_id: str, *, page: int = 1, page_size: int = 200) -> YuquePayload:
        return await self.get(
            f"/api/v2/repos/docs/{quote(str(doc_id), safe='')}",
            params={"page": page, "page_size": page_size},
        )


def payload_list(payload: YuquePayload) -> list[dict[str, Any]]:
    data = payload.data
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("items", "data", "records", "rows"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    raise YuqueResponseError("Yuque returned an unexpected list response")


def payload_object(payload: YuquePayload) -> dict[str, Any]:
    if not isinstance(payload.data, dict):
        raise YuqueResponseError("Yuque returned an unexpected object response")
    return payload.data


def _clean_params(params: Mapping[str, Any] | None) -> dict[str, Any]:
    return {key: value for key, value in (params or {}).items() if value is not None}


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
