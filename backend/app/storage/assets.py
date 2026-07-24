from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import os
import socket
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.storage.content import ContentStore

Resolver = Callable[[str, int], Awaitable[list[str]]]


@dataclass(frozen=True, slots=True)
class DownloadedResource:
    temp_path: Path
    sha256: str
    size: int
    mime_type: str | None
    final_url: str


class ResourceDownloadError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        url: str,
        status_code: int | None = None,
        transient: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.safe_url = safe_display_url(url)
        self.status_code = status_code
        self.transient = transient


class AssetDownloader:
    def __init__(
        self,
        store: ContentStore,
        *,
        client: httpx.AsyncClient | None = None,
        resolver: Resolver | None = None,
        redirect_limit: int = 3,
        timeout: httpx.Timeout | float = 60.0,
        total_timeout_seconds: float = 300.0,
    ) -> None:
        self.store = store
        self._client = client or httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            # Pinned URLs are pooled by IP. Avoid reusing a TLS session for a different hostname
            # that happens to resolve to the same address.
            limits=httpx.Limits(max_keepalive_connections=0),
        )
        self._owns_client = client is None
        # MockTransport never opens a socket; preserve logical hosts for deterministic test routing.
        self._uses_mock_transport = client is not None and isinstance(
            getattr(client, "_transport", None), httpx.MockTransport
        )
        self._resolver = resolver or _resolve_public_addresses
        self._redirect_limit = redirect_limit
        self._total_timeout_seconds = total_timeout_seconds

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def download(
        self,
        url: str,
        *,
        job_id: str,
        max_bytes: int | None,
        token: str | None = None,
        token_origin: str | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> DownloadedResource:
        temp_path = self.store.new_temp_path(job_id)
        try:
            async with asyncio.timeout(self._total_timeout_seconds):
                return await self._download_to_path(
                    url,
                    temp_path,
                    max_bytes=max_bytes,
                    token=token,
                    token_origin=token_origin,
                    extra_headers=extra_headers,
                )
        except TimeoutError as exc:
            temp_path.unlink(missing_ok=True)
            raise ResourceDownloadError(
                "RESOURCE_TIMEOUT",
                "Resource download exceeded the total timeout",
                url=url,
                transient=True,
            ) from exc
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise

    async def _download_to_path(
        self,
        url: str,
        temp_path: Path,
        *,
        max_bytes: int | None,
        token: str | None,
        token_origin: str | None,
        extra_headers: Mapping[str, str] | None,
    ) -> DownloadedResource:
        current = url
        token_origin_value = _url_origin(token_origin) if token_origin else None
        for redirect_count in range(self._redirect_limit + 1):
            addresses = await validate_public_http_url(current, self._resolver)
            logical_url = httpx.URL(current)
            headers = httpx.Headers({"Accept": "*/*"})
            headers.update(extra_headers or {})
            if token and token_origin_value and _url_origin(current) == token_origin_value:
                headers["X-Auth-Token"] = token

            request_url = logical_url
            request_extensions = None
            if not self._uses_mock_transport:
                request_url = logical_url.copy_with(host=addresses[0])
                headers["Host"] = _host_header(logical_url)
                if logical_url.scheme == "https":
                    request_extensions = {"sni_hostname": logical_url.raw_host.decode("ascii")}
            try:
                async with self._client.stream(
                    "GET",
                    request_url,
                    headers=headers,
                    follow_redirects=False,
                    extensions=request_extensions,
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("Location")
                        if not location or redirect_count >= self._redirect_limit:
                            raise ResourceDownloadError(
                                "RESOURCE_REDIRECT_INVALID",
                                "Resource returned an invalid or excessive redirect",
                                url=current,
                                status_code=response.status_code,
                            )
                        current = str(logical_url.join(location))
                        continue
                    _raise_resource_status(response, current)
                    declared = _content_length(response.headers.get("Content-Length"))
                    if max_bytes is not None and declared is not None and declared > max_bytes:
                        raise ResourceDownloadError(
                            "RESOURCE_TOO_LARGE",
                            "Resource exceeds the configured size limit",
                            url=current,
                            status_code=response.status_code,
                        )
                    digest = hashlib.sha256()
                    size = 0
                    with temp_path.open("xb") as handle:
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if max_bytes is not None and size > max_bytes:
                                raise ResourceDownloadError(
                                    "RESOURCE_TOO_LARGE",
                                    "Resource exceeded the configured size limit while streaming",
                                    url=current,
                                    status_code=response.status_code,
                                )
                            handle.write(chunk)
                            digest.update(chunk)
                        handle.flush()
                        os.fsync(handle.fileno())
                    return DownloadedResource(
                        temp_path=temp_path,
                        sha256=digest.hexdigest(),
                        size=size,
                        mime_type=_media_type(response.headers.get("Content-Type")),
                        final_url=current,
                    )
            except ResourceDownloadError:
                raise
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise ResourceDownloadError(
                    "RESOURCE_NETWORK_ERROR",
                    "Resource download failed due to a temporary network error",
                    url=current,
                    transient=True,
                ) from exc
        raise AssertionError("redirect loop exited unexpectedly")


async def validate_public_http_url(
    url: str, resolver: Resolver | None = None
) -> tuple[str, ...]:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ResourceDownloadError(
            "RESOURCE_URL_UNSAFE",
            "Resource URL must be an HTTP(S) URL without user information",
            url=url,
        )
    expected_port = 443 if parts.scheme == "https" else 80
    try:
        port = parts.port or expected_port
    except ValueError as exc:
        raise ResourceDownloadError("RESOURCE_URL_UNSAFE", "Resource URL port is invalid", url=url) from exc
    if port != expected_port:
        raise ResourceDownloadError(
            "RESOURCE_PORT_BLOCKED",
            "Resource URL uses a blocked port",
            url=url,
        )
    addresses = await (resolver or _resolve_public_addresses)(parts.hostname, port)
    if not addresses:
        raise ResourceDownloadError(
            "RESOURCE_DNS_FAILED",
            "Resource host did not resolve",
            url=url,
            transient=True,
        )
    validated: list[str] = []
    for value in addresses:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise ResourceDownloadError(
                "RESOURCE_DNS_FAILED", "Resource host resolved to an invalid address", url=url
            ) from exc
        if not address.is_global:
            raise ResourceDownloadError(
                "RESOURCE_SSRF_BLOCKED",
                "Resource host resolved to a non-public address",
                url=url,
            )
        validated.append(str(address))
    return tuple(validated)


async def _resolve_public_addresses(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        records = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return []
    return sorted({record[4][0] for record in records})


def _host_header(url: httpx.URL) -> str:
    host = url.raw_host.decode("ascii")
    return f"[{host}]" if ":" in host else host


def safe_display_url(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    try:
        port = parts.port
    except ValueError:
        port = None
    authority = host if port is None else f"{host}:{port}"
    return urlunsplit((parts.scheme, authority, parts.path, "", ""))


def _url_origin(value: str | None) -> tuple[str, str, int] | None:
    if not value:
        return None
    parts = urlsplit(value)
    if not parts.scheme or not parts.hostname:
        return None
    default_port = 443 if parts.scheme == "https" else 80
    return parts.scheme, parts.hostname.lower(), parts.port or default_port


def _content_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _media_type(value: str | None) -> str | None:
    if not value:
        return None
    return value.partition(";")[0].strip().lower() or None


def _raise_resource_status(response: httpx.Response, url: str) -> None:
    status = response.status_code
    if 200 <= status < 300:
        return
    if status in (401, 403):
        code = "RESOURCE_PERMISSION_DENIED"
    elif status == 404:
        code = "RESOURCE_NOT_FOUND"
    elif status == 408 or status == 429 or status >= 500:
        raise ResourceDownloadError(
            "RESOURCE_TRANSIENT_ERROR",
            "Resource server returned a temporary error",
            url=url,
            status_code=status,
            transient=True,
        )
    else:
        code = "RESOURCE_DOWNLOAD_FAILED"
    raise ResourceDownloadError(code, "Resource download was rejected", url=url, status_code=status)
