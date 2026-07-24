from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.storage import AssetDownloader, ContentStore, ResourceDownloadError, normalized_content_hash


async def public_resolver(_host: str, _port: int) -> list[str]:
    return ["93.184.216.34"]


def test_version_bundle_is_committed_atomically(tmp_path: Path) -> None:
    store = ContentStore(tmp_path)
    digest = normalized_content_hash({"title": "Hello", "body": "world"})

    committed = store.commit_version(
        job_id="job-1",
        repository_id="repo-1",
        document_id="doc-1",
        content_hash=digest,
        raw_response={"data": {"body": "world"}},
        raw_body="world",
        body_format="markdown",
        preview_html="<p>world</p>",
        normalized_metadata={"title": "Hello"},
        resources=[],
    )

    assert store.resolve(committed.raw_response_path).is_file()
    assert store.resolve(committed.raw_body_path).read_text(encoding="utf-8") == "world"
    manifest = json.loads(store.resolve(committed.manifest_path).read_text(encoding="utf-8"))
    assert manifest["content_hash"] == digest
    assert committed.content_size_bytes > 0
    assert not list((tmp_path / "content" / ".tmp" / "job-1").iterdir())


def test_content_store_rejects_paths_inside_data_root_but_outside_content_root(
    tmp_path: Path,
) -> None:
    store = ContentStore(tmp_path)
    database_file = tmp_path / "db" / "yuque-backup.sqlite3"
    database_file.parent.mkdir(parents=True)
    database_file.write_bytes(b"database")

    with pytest.raises(ValueError, match="content root"):
        store.resolve("db/yuque-backup.sqlite3")
    with pytest.raises(ValueError, match="content root"):
        store.delete_relative("db/yuque-backup.sqlite3")

    assert database_file.read_bytes() == b"database"


@pytest.mark.asyncio
async def test_streaming_asset_download_deduplicates_and_strips_token_on_cross_origin_redirect(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "www.yuque.com":
            return httpx.Response(302, headers={"Location": "https://cdn.example/a.bin"})
        return httpx.Response(
            200, content=b"same-content", headers={"Content-Type": "application/octet-stream"}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    store = ContentStore(tmp_path)
    downloader = AssetDownloader(store, client=client, resolver=public_resolver)
    downloaded = await downloader.download(
        "https://www.yuque.com/a.bin",
        job_id="job-1",
        max_bytes=1024,
        token="secret",
        token_origin="https://www.yuque.com",
    )
    first = store.commit_asset(downloaded.temp_path, sha256=downloaded.sha256, size=downloaded.size)

    assert requests[0].headers["X-Auth-Token"] == "secret"
    assert "X-Auth-Token" not in requests[1].headers
    assert store.resolve(first.storage_path).read_bytes() == b"same-content"
    await client.aclose()


@pytest.mark.asyncio
async def test_asset_download_pins_every_redirect_hop_and_preserves_https_authority(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    resolved_hosts: list[tuple[str, int]] = []

    class RecordingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.headers["Host"] == "origin.example":
                return httpx.Response(302, headers={"Location": "https://cdn.example/final.bin"})
            return httpx.Response(200, content=b"pinned-content")

    async def resolver(host: str, port: int) -> list[str]:
        resolved_hosts.append((host, port))
        return {
            "origin.example": ["93.184.216.34"],
            "cdn.example": ["2606:4700:4700::1111"],
        }[host]

    client = httpx.AsyncClient(transport=RecordingTransport(), follow_redirects=False)
    downloader = AssetDownloader(ContentStore(tmp_path), client=client, resolver=resolver)

    downloaded = await downloader.download(
        "https://origin.example/start.bin",
        job_id="job-pinned",
        max_bytes=1024,
    )

    assert resolved_hosts == [("origin.example", 443), ("cdn.example", 443)]
    assert [request.url.host for request in requests] == [
        "93.184.216.34",
        "2606:4700:4700::1111",
    ]
    assert [request.headers["Host"] for request in requests] == [
        "origin.example",
        "cdn.example",
    ]
    assert [request.extensions["sni_hostname"] for request in requests] == [
        "origin.example",
        "cdn.example",
    ]
    assert downloaded.final_url == "https://cdn.example/final.bin"
    assert downloaded.temp_path.read_bytes() == b"pinned-content"
    await client.aclose()


@pytest.mark.asyncio
async def test_asset_redirect_to_private_address_is_blocked_before_second_request(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://internal.example/secret"})

    async def resolver(host: str, _port: int) -> list[str]:
        return ["127.0.0.1"] if host == "internal.example" else ["93.184.216.34"]

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    downloader = AssetDownloader(ContentStore(tmp_path), client=client, resolver=resolver)

    with pytest.raises(ResourceDownloadError) as exc_info:
        await downloader.download(
            "https://origin.example/start.bin",
            job_id="job-private-redirect",
            max_bytes=1024,
        )

    assert exc_info.value.code == "RESOURCE_SSRF_BLOCKED"
    assert len(requests) == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_resource_limit_and_private_address_are_rejected(tmp_path: Path) -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=b"0123456789"))
    )
    store = ContentStore(tmp_path)
    downloader = AssetDownloader(store, client=client, resolver=public_resolver)
    with pytest.raises(ResourceDownloadError, match="size limit") as exc_info:
        await downloader.download("https://cdn.example/a.bin", job_id="job", max_bytes=4)
    assert exc_info.value.code == "RESOURCE_TOO_LARGE"

    async def private_resolver(_host: str, _port: int) -> list[str]:
        return ["127.0.0.1"]

    private = AssetDownloader(store, client=client, resolver=private_resolver)
    with pytest.raises(ResourceDownloadError) as private_error:
        await private.download("https://internal.example/a", job_id="job", max_bytes=100)
    assert private_error.value.code == "RESOURCE_SSRF_BLOCKED"
    await client.aclose()
