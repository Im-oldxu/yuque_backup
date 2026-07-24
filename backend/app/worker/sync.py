from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.models import (
    AppSetting,
    Asset,
    BackupIssue,
    BackupSubtask,
    Document,
    DocumentVersion,
    Operation,
    QueueItem,
    RateLimitBucket,
    Repository,
    RepositoryCredential,
    SyncCheckpoint,
    TocItem,
    VersionAsset,
    YuqueCredential,
)
from app.core.security import decrypt_token
from app.integrations.yuque.client import (
    RateLimitSnapshot,
    YuqueAuthError,
    YuqueClient,
    YuqueError,
    YuqueNotFoundError,
    YuquePayload,
    YuqueQuotaError,
    YuqueResponseError,
    YuqueTransientError,
    payload_list,
    payload_object,
)
from app.modules.preview import (
    ResourceCandidate,
    build_document_preview,
    extract_resource_candidates,
)
from app.storage import AssetDownloader, ContentStore, ResourceDownloadError, normalized_content_hash
from app.worker.coordinator import aggregate_job_in_session
from app.worker.queue import (
    QUOTA_MAX_DELAY_SECONDS,
    PersistentQueue,
    QueueItemSnapshot,
    QueueLeaseLost,
    transient_retry_delay,
)

TokenResolver = Callable[[YuqueCredential], str]
Sleep = Callable[[float], Awaitable[None]]


@dataclass(slots=True)
class AssetOutcome:
    candidate: ResourceCandidate
    status: str
    asset_id: str | None = None
    sha256: str | None = None
    size: int | None = None
    mime_type: str | None = None
    storage_path: str | None = None
    issue_code: str | None = None
    issue_message: str | None = None
    http_status: int | None = None
    attempts: int = 0


@dataclass(frozen=True, slots=True)
class VersionIssue:
    code: str
    message: str
    http_status: int | None = None
    attempts: int = 1


@dataclass(frozen=True, slots=True)
class TablePageResult:
    raw_response: Any
    data: dict[str, Any]
    issues: tuple[VersionIssue, ...]
    rate_limit: RateLimitSnapshot


@dataclass(frozen=True, slots=True)
class CredentialVerificationRequest:
    credential_id: str
    base_url: str
    token: str
    security_fingerprint: bytes


def enqueue_credential_verification(
    queue: PersistentQueue,
    *,
    operation_id: str,
    credential_id: str,
) -> QueueItemSnapshot:
    return queue.enqueue(
        "credential_verify",
        idempotency_key=f"operation:{operation_id}",
        payload={},
        priority=10,
        operation_id=operation_id,
        credential_id=credential_id,
    )


def enqueue_repository_discovery(
    queue: PersistentQueue,
    *,
    operation_id: str,
    credential_id: str,
) -> QueueItemSnapshot:
    return queue.enqueue(
        "repository_discovery",
        idempotency_key=f"operation:{operation_id}",
        payload={"stage": "start", "offset": 0, "counts": {}},
        priority=20,
        operation_id=operation_id,
        credential_id=credential_id,
    )


class SyncExecutor:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        queue: PersistentQueue,
        store: ContentStore,
        settings: Settings,
        *,
        yuque_http_client: httpx.AsyncClient,
        asset_downloader: AssetDownloader,
        token_resolver: TokenResolver | None = None,
        now: Callable[[], datetime] | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._session_factory = session_factory
        self._queue = queue
        self._store = store
        self._settings = settings
        self._yuque_http_client = yuque_http_client
        self._asset_downloader = asset_downloader
        self._token_resolver = token_resolver or (lambda credential: decrypt_token(credential, settings))
        self._now = now or (lambda: datetime.now(UTC))
        self._sleep = sleep
        self._asset_semaphore = asyncio.Semaphore(settings.resource_download_concurrency)

    async def handle(self, item: QueueItemSnapshot, worker_id: str) -> None:
        try:
            await self._handle_item(item, worker_id)
        except QueueLeaseLost:
            return

    async def _handle_item(self, item: QueueItemSnapshot, worker_id: str) -> None:
        try:
            if item.category == "credential_verify":
                await self._handle_credential_verify(item, worker_id)
            elif item.category == "repository_discovery":
                await self._handle_repository_discovery(item, worker_id)
            elif item.category == "repository_sync":
                await self._handle_repository_sync(item, worker_id)
            elif item.category == "document_sync":
                await self._handle_document_sync(item, worker_id)
            else:
                self._queue.fail(
                    item.id,
                    worker_id,
                    code="QUEUE_CATEGORY_UNKNOWN",
                    message="Queue item category is not supported",
                )
        except YuqueQuotaError as exc:
            self._handle_quota_error(item, worker_id, exc)
        except YuqueTransientError as exc:
            self._handle_transient_error(item, worker_id, exc)
        except YuqueAuthError as exc:
            self._handle_auth_error(item, worker_id, exc)
        except YuqueNotFoundError as exc:
            if item.category == "document_sync":
                await self._handle_document_not_found(item, worker_id, exc)
            else:
                self._handle_terminal_error(item, worker_id, exc.code, str(exc), exc.status_code)
        except YuqueError as exc:
            self._handle_terminal_error(
                item,
                worker_id,
                exc.code,
                "Yuque response could not be processed",
                exc.status_code,
            )
        except (ValueError, KeyError, TypeError):
            self._handle_terminal_error(
                item,
                worker_id,
                "YUQUE_RESPONSE_INVALID",
                "Yuque response could not be processed",
            )
        except (OSError, RuntimeError):
            self._handle_terminal_error(
                item, worker_id, "WORKER_STORAGE_ERROR", "Worker could not commit backup data"
            )

    async def _handle_credential_verify(self, item: QueueItemSnapshot, worker_id: str) -> None:
        request = self._prepare_credential_verification(item, worker_id)
        if request is None:
            return
        try:
            client = YuqueClient(
                request.base_url,
                request.token,
                client=self._yuque_http_client,
                max_redirects=self._settings.resource_redirect_limit,
                now=self._now,
            )
            response = await client.get_current_subject()
            subject = payload_object(response)
            subject_type = str(subject.get("type") or "unknown").lower()
            if subject_type in {"team", "organization"}:
                subject_type = "group"
            subject_id = _optional_str(subject.get("id"))
            login = _optional_str(subject.get("login"))
            if subject_type not in {"user", "group"} or subject_id is None or login is None:
                raise YuqueResponseError("Yuque credential identity is incomplete")
        except YuqueQuotaError as exc:
            self._finish_credential_verification_failure(
                item,
                worker_id,
                request,
                kind="quota",
                code=exc.code,
                queue_message=str(exc),
                operation_message="Waiting for Yuque API quota",
                rate_limit=exc.rate_limit,
                retry_after_seconds=exc.retry_after_seconds,
            )
        except YuqueTransientError as exc:
            self._finish_credential_verification_failure(
                item,
                worker_id,
                request,
                kind="transient",
                code=exc.code,
                queue_message=str(exc),
                operation_message="Yuque request failed after retries",
                rate_limit=exc.rate_limit,
            )
        except YuqueAuthError as exc:
            self._finish_credential_verification_failure(
                item,
                worker_id,
                request,
                kind="auth",
                code=exc.code,
                queue_message=str(exc),
                operation_message="Yuque rejected the configured credential",
                rate_limit=exc.rate_limit,
            )
        except YuqueError as exc:
            self._finish_credential_verification_failure(
                item,
                worker_id,
                request,
                kind="terminal",
                code=exc.code,
                queue_message="Yuque response could not be processed",
                operation_message="Yuque response could not be processed",
            )
        except (ValueError, KeyError, TypeError):
            self._finish_credential_verification_failure(
                item,
                worker_id,
                request,
                kind="terminal",
                code="YUQUE_RESPONSE_INVALID",
                queue_message="Yuque response could not be processed",
                operation_message="Yuque response could not be processed",
            )
        except (OSError, RuntimeError):
            self._finish_credential_verification_failure(
                item,
                worker_id,
                request,
                kind="terminal",
                code="WORKER_STORAGE_ERROR",
                queue_message="Worker could not commit backup data",
                operation_message="Worker could not commit backup data",
            )
        else:
            self._finish_credential_verification_success(
                item,
                worker_id,
                request,
                subject_type=subject_type,
                subject_id=subject_id,
                login=login,
                rate_limit=response.rate_limit,
            )

    def _prepare_credential_verification(
        self,
        item: QueueItemSnapshot,
        worker_id: str,
    ) -> CredentialVerificationRequest | None:
        if item.credential_id is None or item.operation_id is None:
            raise RuntimeError("credential verification queue item is incomplete")
        current = self._now()
        with self._session_factory.begin() as session:
            queue_item = session.get(QueueItem, item.id)
            if (
                queue_item is None
                or queue_item.status != "running"
                or queue_item.lease_owner != worker_id
            ):
                raise RuntimeError("credential verification queue item is not leased by this worker")
            credential = session.get(YuqueCredential, item.credential_id)
            operation = session.get(Operation, item.operation_id)
            if (
                credential is None
                or credential.deleted_at is not None
                or operation is None
                or operation.status not in {"queued", "running", "waiting_quota"}
            ):
                self._finish_queue_item(
                    queue_item,
                    status="cancelled",
                    current=current,
                    code="CREDENTIAL_VERIFICATION_STALE",
                    message="Credential verification was superseded before the request started",
                )
                return None

            if credential.subject_id and credential.subject_type != "unknown":
                bucket = session.scalar(
                    select(RateLimitBucket).where(
                        RateLimitBucket.base_url == credential.base_url,
                        RateLimitBucket.subject_type == credential.subject_type,
                        RateLimitBucket.subject_id == credential.subject_id,
                    )
                )
                if (
                    bucket is not None
                    and bucket.next_allowed_at is not None
                    and _ensure_utc(bucket.next_allowed_at) > current
                ):
                    queue_item.payload = {
                        key: value for key, value in queue_item.payload.items() if key != "_quota_attempt"
                    }
                    queue_item.status = "pending"
                    queue_item.attempt_count = 0
                    queue_item.available_at = _ensure_utc(bucket.next_allowed_at)
                    queue_item.next_retry_at = None
                    queue_item.lease_owner = None
                    queue_item.lease_until = None
                    queue_item.last_error_code = None
                    queue_item.last_error_message = None
                    return None

            token = self._token_resolver(credential)
            security_fingerprint = self._credential_security_fingerprint(credential)
            if operation.started_at is None:
                operation.started_at = current
            operation.status = "running"
            operation.next_retry_at = None
            queue_item.attempt_count += 1
            return CredentialVerificationRequest(
                credential_id=credential.id,
                base_url=credential.base_url,
                token=token,
                security_fingerprint=security_fingerprint,
            )

    def _finish_credential_verification_success(
        self,
        item: QueueItemSnapshot,
        worker_id: str,
        request: CredentialVerificationRequest,
        *,
        subject_type: str,
        subject_id: str,
        login: str,
        rate_limit: RateLimitSnapshot,
    ) -> None:
        current = self._now()
        with self._session_factory.begin() as session:
            records = self._current_credential_verification_records(
                session,
                item,
                worker_id,
                request,
                current=current,
            )
            if records is None:
                return
            queue_item, credential, operation = records
            credential.subject_type = subject_type
            credential.subject_id = subject_id
            credential.login = login
            credential.last_verified_at = current
            credential.last_error_code = None
            credential.pause_reason = None
            credential.next_retry_at = None
            credential.verification_valid = True
            credential.status = "waiting_quota" if rate_limit.remaining == 0 else "valid"
            self._persist_rate_in_session(session, credential, rate_limit, current=current)
            operation.status = "succeeded"
            operation.result = {
                "subject_type": subject_type,
                "subject_id": subject_id,
                "login": login,
            }
            operation.error = None
            operation.next_retry_at = None
            operation.finished_at = current
            self._finish_queue_item(queue_item, status="succeeded", current=current)

    def _finish_credential_verification_failure(
        self,
        item: QueueItemSnapshot,
        worker_id: str,
        request: CredentialVerificationRequest,
        *,
        kind: str,
        code: str,
        queue_message: str,
        operation_message: str,
        rate_limit: RateLimitSnapshot | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        current = self._now()
        with self._session_factory.begin() as session:
            records = self._current_credential_verification_records(
                session,
                item,
                worker_id,
                request,
                current=current,
            )
            if records is None:
                return
            queue_item, credential, operation = records
            if rate_limit is not None:
                self._persist_rate_in_session(session, credential, rate_limit, current=current)
            if kind == "quota":
                payload = dict(queue_item.payload)
                quota_attempt = int(payload.get("_quota_attempt", 0)) + 1
                payload["_quota_attempt"] = quota_attempt
                fallback = min(60 * (2 ** (quota_attempt - 1)), QUOTA_MAX_DELAY_SECONDS)
                delay = retry_after_seconds if retry_after_seconds is not None else fallback
                next_retry_at = current + timedelta(seconds=max(0, delay))
                queue_item.payload = payload
                queue_item.status = "retry_wait"
                queue_item.available_at = next_retry_at
                queue_item.next_retry_at = next_retry_at
                queue_item.last_error_code = code[:64]
                queue_item.last_error_message = "Waiting for Yuque API quota"
                queue_item.lease_owner = None
                queue_item.lease_until = None
                credential.status = "waiting_quota"
                credential.next_retry_at = next_retry_at
                credential.pause_reason = "quota"
                operation.status = "waiting_quota"
                operation.next_retry_at = next_retry_at
                return
            if kind == "transient":
                delay = transient_retry_delay(queue_item.attempt_count)
                if delay is not None:
                    next_retry_at = current + timedelta(seconds=delay)
                    queue_item.status = "retry_wait"
                    queue_item.available_at = next_retry_at
                    queue_item.next_retry_at = next_retry_at
                    queue_item.last_error_code = code[:64]
                    queue_item.last_error_message = queue_message[:1024]
                    queue_item.lease_owner = None
                    queue_item.lease_until = None
                    return

            self._finish_queue_item(
                queue_item,
                status="failed",
                current=current,
                code=code,
                message=queue_message,
            )
            credential.status = "action_required"
            credential.enabled = False
            credential.verification_valid = False
            credential.last_error_code = code
            credential.pause_reason = "authentication" if kind == "auth" else "verification"
            operation.status = "failed"
            operation.error = {"code": code, "message": operation_message}
            operation.finished_at = current
            operation.next_retry_at = None

    def _current_credential_verification_records(
        self,
        session: Session,
        item: QueueItemSnapshot,
        worker_id: str,
        request: CredentialVerificationRequest,
        *,
        current: datetime,
    ) -> tuple[QueueItem, YuqueCredential, Operation] | None:
        queue_item = session.get(QueueItem, item.id)
        if (
            queue_item is None
            or queue_item.status != "running"
            or queue_item.lease_owner != worker_id
        ):
            return None
        credential = session.get(YuqueCredential, request.credential_id)
        operation = session.get(Operation, item.operation_id) if item.operation_id else None
        security_matches = (
            credential is not None
            and credential.deleted_at is None
            and hmac.compare_digest(
                self._credential_security_fingerprint(credential),
                request.security_fingerprint,
            )
        )
        if operation is None or operation.status != "running" or not security_matches:
            self._finish_queue_item(
                queue_item,
                status="cancelled",
                current=current,
                code="CREDENTIAL_VERIFICATION_STALE",
                message="Credential verification response no longer matches the active credential",
            )
            if operation is not None and operation.status in {"queued", "running", "waiting_quota"}:
                operation.status = "cancelled"
                operation.finished_at = current
                operation.next_retry_at = None
            return None
        assert credential is not None
        return queue_item, credential, operation

    @staticmethod
    def _credential_security_fingerprint(credential: YuqueCredential) -> bytes:
        digest = hashlib.sha256()
        values = (
            credential.base_url.encode("utf-8"),
            credential.encrypted_token,
            credential.token_nonce,
            str(credential.key_version).encode("ascii"),
        )
        for value in values:
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
        return digest.digest()

    @staticmethod
    def _finish_queue_item(
        queue_item: QueueItem,
        *,
        status: str,
        current: datetime,
        code: str | None = None,
        message: str | None = None,
    ) -> None:
        queue_item.status = status
        queue_item.finished_at = current
        queue_item.next_retry_at = None
        queue_item.lease_owner = None
        queue_item.lease_until = None
        queue_item.last_error_code = code[:64] if code else None
        queue_item.last_error_message = message[:1024] if message else None

    async def _handle_repository_discovery(self, item: QueueItemSnapshot, worker_id: str) -> None:
        credential = self._credential(item)
        if self._defer_for_rate_limit(item, worker_id, credential):
            return
        self._start_operation(item.operation_id)
        payload = dict(item.payload)
        stage = str(payload.get("stage", "start"))
        if stage == "start":
            if credential.subject_type == "user":
                stage = "user_repositories"
            elif credential.subject_type == "group":
                stage = "group_repositories"
                payload["group_login"] = credential.login
            else:
                raise ValueError("verified credential subject type is unknown")
            payload.update({"stage": stage, "offset": 0, "groups": [], "group_index": 0})

        self._queue.record_attempt(item.id, worker_id)
        client = self._client(credential)
        offset = int(payload.get("offset", 0))
        if stage == "user_repositories":
            response = await client.list_user_repositories(credential.login or "", offset=offset)
            records = payload_list(response)
            self._merge_discovered_repositories(credential.id, records, payload)
            next_stage = "user_groups" if len(records) < 100 else stage
        elif stage == "user_groups":
            response = await client.list_user_groups(credential.subject_id or "", offset=offset)
            records = payload_list(response)
            groups = list(payload.get("groups", []))
            for record in records:
                group = record.get("group") if isinstance(record.get("group"), dict) else record
                login = group.get("login") if isinstance(group, dict) else None
                if login and login not in groups:
                    groups.append(str(login))
            payload["groups"] = groups
            next_stage = "group_repositories" if len(records) < 100 else stage
            if next_stage == "group_repositories":
                payload["group_index"] = 0
                payload["group_login"] = groups[0] if groups else None
        elif stage == "group_repositories":
            group_login = payload.get("group_login")
            if not group_login and credential.subject_type == "group":
                group_login = credential.login
            if not group_login:
                self._finish_discovery(item, worker_id, payload)
                return
            response = await client.list_group_repositories(str(group_login), offset=offset)
            records = payload_list(response)
            self._merge_discovered_repositories(credential.id, records, payload)
            next_stage = stage
            if len(records) < 100:
                groups = list(payload.get("groups", []))
                group_index = int(payload.get("group_index", 0)) + 1
                if credential.subject_type == "user" and group_index < len(groups):
                    payload["group_index"] = group_index
                    payload["group_login"] = groups[group_index]
                else:
                    self._persist_rate(credential.id, response.rate_limit)
                    self._finish_discovery(item, worker_id, payload)
                    return
        else:
            raise ValueError("invalid repository discovery stage")

        self._persist_rate(credential.id, response.rate_limit)
        if next_stage != stage or len(records) < 100:
            payload["offset"] = 0
        else:
            payload["offset"] = offset + 100
        payload["stage"] = next_stage
        self._queue.continue_with_payload(item.id, worker_id, payload)

    async def _handle_repository_sync(self, item: QueueItemSnapshot, worker_id: str) -> None:
        credential = self._credential(item)
        repository = self._repository(item)
        payload = dict(item.payload)
        stage = str(payload.get("stage", "metadata"))
        if stage == "barrier":
            self._finalize_repository(item, worker_id, payload)
            return
        if self._defer_for_rate_limit(item, worker_id, credential):
            return
        self._start_subtask(item.subtask_id)
        self._queue.record_attempt(item.id, worker_id)
        client = self._client(credential)
        if stage == "metadata":
            response = await client.get_repository(repository.yuque_book_id)
            data = payload_object(response)
            with self._session_factory.begin() as session:
                current = session.get(Repository, repository.id)
                if current is None:
                    raise RuntimeError("repository disappeared")
                initial = current.safe_watermark is None
                payload["initial"] = initial
                if current.safe_watermark is not None:
                    payload["changed_at_gte"] = _iso(current.safe_watermark - timedelta(minutes=5))
                current.name = str(data.get("name") or current.name)
                current.slug = _optional_str(data.get("slug"))
                current.namespace = _optional_str(data.get("namespace"))
                current.repo_type = _optional_str(data.get("type")) or current.repo_type
                current.content_updated_at = _parse_datetime(data.get("content_updated_at"))
            payload.update({"stage": "toc", "offset": 0})
        elif stage == "toc":
            response = await client.get_toc(repository.yuque_book_id)
            self._replace_toc(repository.id, payload_list(response))
            payload.update({"stage": "documents", "offset": 0})
        elif stage == "documents":
            offset = int(payload.get("offset", 0))
            response = await client.list_documents(
                repository.yuque_book_id,
                offset=offset,
                changed_at_gte=_parse_datetime(payload.get("changed_at_gte")),
                deleted=False,
            )
            records = payload_list(response)
            specs = self._upsert_document_summaries(item, records)
            for document_id, summary in specs:
                self._queue.enqueue(
                    "document_sync",
                    idempotency_key=f"job:{item.job_id}:document:{document_id}",
                    payload={"summary": summary},
                    priority=60,
                    job_id=item.job_id,
                    subtask_id=item.subtask_id,
                    credential_id=item.credential_id,
                    repository_id=item.repository_id,
                    document_id=document_id,
                )
            if len(records) >= 100:
                payload["offset"] = offset + 100
            else:
                payload.update(
                    {
                        "stage": "barrier" if payload.get("initial") else "deleted_documents",
                        "offset": 0,
                    }
                )
        elif stage == "deleted_documents":
            offset = int(payload.get("offset", 0))
            response = await client.list_documents(
                repository.yuque_book_id,
                offset=offset,
                changed_at_gte=_parse_datetime(payload.get("changed_at_gte")),
                deleted=True,
            )
            records = payload_list(response)
            self._mark_deleted_documents(repository.id, records, item.job_id)
            if len(records) >= 100:
                payload["offset"] = offset + 100
            else:
                payload.update({"stage": "barrier", "offset": 0})
        else:
            raise ValueError("invalid repository sync stage")
        self._persist_rate(credential.id, response.rate_limit)
        self._queue.continue_with_payload(item.id, worker_id, payload)

    async def _handle_document_sync(self, item: QueueItemSnapshot, worker_id: str) -> None:
        if self._document_item_already_committed(item):
            self._queue.complete(item.id, worker_id)
            return
        credential = self._credential(item)
        repository = self._repository(item)
        document = self._document(item)
        if self._defer_for_rate_limit(item, worker_id, credential):
            return
        self._queue.record_attempt(item.id, worker_id)
        client = self._client(credential)
        response = await client.get_document(document.yuque_doc_id, page=1, page_size=200)
        data = payload_object(response)
        raw_response: Any = response.raw
        table_issues: tuple[VersionIssue, ...] = ()
        rate_limit = response.rate_limit
        if str(data.get("type") or document.type) == "Table":
            table_pages = await self._collect_table_pages(
                client, document.yuque_doc_id, response, data
            )
            raw_response = table_pages.raw_response
            data = table_pages.data
            table_issues = table_pages.issues
            rate_limit = table_pages.rate_limit
        self._persist_rate(credential.id, rate_limit)

        candidates = extract_resource_candidates(data)
        with self._session_factory() as session:
            max_asset_size = session.scalar(select(AppSetting.max_asset_size_bytes).where(AppSetting.id == 1))
        token = self._token_resolver(credential)
        outcomes = await asyncio.gather(
            *(
                self._download_asset(
                    candidate,
                    job_id=item.job_id or "operation",
                    max_bytes=max_asset_size,
                    token=token,
                    token_origin=repository.normalized_base_url,
                )
                for candidate in candidates
            )
        )
        self._persist_assets(outcomes)
        resource_map = {
            outcome.candidate.normalized_url: f"/api/v1/assets/{outcome.asset_id}/content"
            for outcome in outcomes
            if outcome.asset_id and outcome.status == "downloaded"
        }
        preview = build_document_preview(data, local_resources=resource_map)
        safe_data = _redact_secret(data, token)
        if not isinstance(safe_data, dict):
            raise ValueError("redacted document payload is invalid")
        safe_raw_response = _redact_secret(raw_response, token)
        metadata = self._normalized_document_metadata(document, safe_data, candidates)
        metadata = _redact_secret(metadata, token)
        if not isinstance(metadata, dict):
            raise ValueError("redacted document metadata is invalid")
        content_hash = normalized_content_hash(metadata)
        self._remove_purged_version_files(document.id, content_hash)
        raw_body, body_format = _raw_body_and_format(safe_data)
        resource_manifest = [self._resource_manifest(outcome, token) for outcome in outcomes]
        committed = self._store.commit_version(
            job_id=item.job_id or "operation",
            repository_id=repository.id,
            document_id=document.id,
            content_hash=content_hash,
            raw_response=safe_raw_response,
            raw_body=raw_body,
            body_format=body_format,
            preview_html=_redact_text(preview.html, token),
            normalized_metadata=metadata,
            resources=resource_manifest,
        )
        partial_preview_codes = {"SHEET_PARSE_FAILED", "TABLE_PARSE_FAILED", "PREVIEW_NOT_AVAILABLE"}
        preview_issues = [code for code in preview.issues if code in partial_preview_codes]
        version_issues = [
            *table_issues,
            *(
                VersionIssue(
                    code=code,
                    message="Document preview is incomplete; original content was retained",
                )
                for code in preview_issues
            ),
        ]
        failed_assets = [outcome for outcome in outcomes if outcome.status != "downloaded"]
        completeness = "partial" if failed_assets or version_issues else "complete"
        self._commit_document_version(
            item,
            safe_data,
            content_hash,
            body_format,
            metadata,
            committed,
            outcomes,
            version_issues,
            completeness,
            token,
        )
        self._queue.complete(item.id, worker_id)

    def _document_item_already_committed(self, item: QueueItemSnapshot) -> bool:
        if item.repository_id is None or item.document_id is None:
            return False
        checkpoint_key = f"document:{item.repository_id}:{item.document_id}"
        with self._session_factory() as session:
            checkpoint = session.scalar(
                select(SyncCheckpoint).where(SyncCheckpoint.checkpoint_key == checkpoint_key)
            )
            return bool(
                checkpoint is not None
                and checkpoint.completed
                and checkpoint.data.get("queue_item_id") == item.id
            )

    async def _collect_table_pages(
        self,
        client: YuqueClient,
        doc_id: str,
        first: YuquePayload,
        data: dict[str, Any],
    ) -> TablePageResult:
        total = _table_total_count(data)
        if total <= 200:
            return TablePageResult(first.raw, data, (), first.rate_limit)

        expected_pages = (total + 199) // 200
        raw_pages = [first.raw]
        data_pages = [data]
        issues: list[VersionIssue] = []
        rate_limit = first.rate_limit
        for page_number in range(2, expected_pages + 1):
            try:
                page = await client.get_document(doc_id, page=page_number, page_size=200)
                page_data = payload_object(page)
            except YuqueError as exc:
                if exc.rate_limit is not None:
                    rate_limit = exc.rate_limit
                issues.append(
                    VersionIssue(
                        code="TABLE_PAGE_FETCH_FAILED",
                        message=(
                            f"Table pagination stopped at page {page_number} of {expected_pages} "
                            f"after {exc.code}; {len(data_pages)} page(s) were retained"
                        ),
                        http_status=exc.status_code,
                    )
                )
                break
            raw_pages.append(page.raw)
            data_pages.append(page_data)
            rate_limit = page.rate_limit

        merged = _merge_table_page_data(data_pages)
        return TablePageResult({"pages": raw_pages}, merged, tuple(issues), rate_limit)

    async def _download_asset(
        self,
        candidate: ResourceCandidate,
        *,
        job_id: str,
        max_bytes: int | None,
        token: str,
        token_origin: str,
    ) -> AssetOutcome:
        if (
            max_bytes is not None
            and candidate.declared_size is not None
            and candidate.declared_size > max_bytes
        ):
            return AssetOutcome(
                candidate=candidate,
                status="skipped",
                issue_code="RESOURCE_TOO_LARGE",
                issue_message="Resource exceeds the configured size limit",
            )
        async with self._asset_semaphore:
            for attempt in range(1, 5):
                try:
                    downloaded = await self._asset_downloader.download(
                        candidate.original_url,
                        job_id=job_id,
                        max_bytes=max_bytes,
                        token=token,
                        token_origin=token_origin,
                    )
                    committed = self._store.commit_asset(
                        downloaded.temp_path,
                        sha256=downloaded.sha256,
                        size=downloaded.size,
                    )
                    return AssetOutcome(
                        candidate=candidate,
                        status="downloaded",
                        sha256=committed.sha256,
                        size=committed.size,
                        mime_type=downloaded.mime_type or candidate.mime_type,
                        storage_path=committed.storage_path,
                        attempts=attempt,
                    )
                except ResourceDownloadError as exc:
                    if exc.transient and attempt < 4:
                        await self._sleep((2, 10, 30)[attempt - 1])
                        continue
                    return AssetOutcome(
                        candidate=candidate,
                        status="failed" if exc.code != "RESOURCE_TOO_LARGE" else "skipped",
                        issue_code=exc.code,
                        issue_message=str(exc),
                        http_status=exc.status_code,
                        attempts=attempt,
                    )
        raise AssertionError("resource retry loop exited unexpectedly")

    def _persist_assets(self, outcomes: list[AssetOutcome]) -> None:
        with self._session_factory.begin() as session:
            for outcome in outcomes:
                if not outcome.sha256 or not outcome.storage_path or outcome.size is None:
                    continue
                asset = session.scalar(select(Asset).where(Asset.sha256 == outcome.sha256))
                if asset is None:
                    asset = Asset(
                        sha256=outcome.sha256,
                        size=outcome.size,
                        mime_type=outcome.mime_type,
                        storage_path=outcome.storage_path,
                    )
                    session.add(asset)
                    session.flush()
                elif asset.storage_path is None:
                    asset.storage_path = outcome.storage_path
                    asset.purged_at = None
                    asset.size = outcome.size
                    asset.mime_type = outcome.mime_type
                outcome.asset_id = asset.id

    def _remove_purged_version_files(self, document_id: str, content_hash: str) -> None:
        with self._session_factory() as session:
            version = session.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.document_id == document_id,
                    DocumentVersion.content_hash == content_hash,
                    DocumentVersion.purged_at.is_not(None),
                )
            )
            if version is None:
                return
            paths = (
                version.raw_response_path,
                version.raw_body_path,
                version.preview_path,
                version.manifest_path,
            )
        try:
            for path in dict.fromkeys(value for value in paths if value):
                self._store.delete_relative(path)
        except ValueError as exc:
            raise RuntimeError("purged version storage path is invalid") from exc

    def _commit_document_version(
        self,
        item: QueueItemSnapshot,
        data: dict[str, Any],
        content_hash: str,
        body_format: str | None,
        metadata: dict[str, Any],
        committed: Any,
        outcomes: list[AssetOutcome],
        version_issues: list[VersionIssue],
        completeness: str,
        token: str,
    ) -> None:
        current = self._now()
        remote_version_id = _optional_str(data.get("latest_version_id") or data.get("version_id"))
        remote_updated_at = _parse_datetime(data.get("updated_at") or data.get("content_updated_at"))
        resource_downloaded = sum(outcome.status == "downloaded" for outcome in outcomes)
        issue_count = sum(outcome.status != "downloaded" for outcome in outcomes) + len(
            version_issues
        )
        with self._session_factory.begin() as session:
            document = session.get(Document, item.document_id)
            subtask = session.get(BackupSubtask, item.subtask_id)
            if document is None or subtask is None or not item.job_id:
                raise RuntimeError("document backup records disappeared")
            existing = session.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.document_id == document.id,
                    DocumentVersion.content_hash == content_hash,
                )
            )
            restore_purged_version = existing is not None and existing.purged_at is not None
            if existing is None:
                version = DocumentVersion(
                    document_id=document.id,
                    remote_version_id=remote_version_id,
                    format=body_format,
                    content_hash=content_hash,
                    completeness=completeness,
                    raw_response_path=committed.raw_response_path,
                    raw_body_path=committed.raw_body_path,
                    preview_path=committed.preview_path,
                    manifest_path=committed.manifest_path,
                    content_size_bytes=committed.content_size_bytes,
                    normalized_metadata=metadata,
                    resource_total=len(outcomes),
                    resource_downloaded=resource_downloaded,
                    issue_count=issue_count,
                    source_job_id=item.job_id,
                    remote_updated_at=remote_updated_at,
                )
                session.add(version)
                session.flush()
            else:
                version = existing
                if remote_version_id is not None:
                    version.remote_version_id = remote_version_id
                if remote_updated_at is not None:
                    version.remote_updated_at = remote_updated_at
                if restore_purged_version:
                    session.execute(delete(VersionAsset).where(VersionAsset.version_id == version.id))
                    session.execute(
                        update(BackupIssue)
                        .where(BackupIssue.version_id == version.id)
                        .values(version_id=None)
                    )
                    version.format = body_format
                    version.completeness = completeness
                    version.raw_response_path = committed.raw_response_path
                    version.raw_body_path = committed.raw_body_path
                    version.preview_path = committed.preview_path
                    version.manifest_path = committed.manifest_path
                    version.content_size_bytes = committed.content_size_bytes
                    version.normalized_metadata = metadata
                    version.resource_total = len(outcomes)
                    version.resource_downloaded = resource_downloaded
                    version.issue_count = issue_count
                    version.source_job_id = item.job_id
                    version.created_at = current
                    version.purged_at = None

            if existing is None or restore_purged_version:
                for outcome in outcomes:
                    session.add(
                        VersionAsset(
                            version_id=version.id,
                            asset_id=outcome.asset_id,
                            original_url=_redact_text(outcome.candidate.original_url, token),
                            normalized_url=_redact_text(outcome.candidate.normalized_url, token),
                            safe_url=_redact_text(outcome.candidate.safe_url, token),
                            name=_redact_text(outcome.candidate.name, token),
                            type=outcome.candidate.type,
                            mime_type=outcome.mime_type or outcome.candidate.mime_type,
                            declared_size=outcome.candidate.declared_size,
                            position=outcome.candidate.position,
                            source_location=_redact_optional_text(outcome.candidate.source_location, token),
                            status=outcome.status,
                            issue_code=outcome.issue_code,
                        )
                    )
                    if outcome.status != "downloaded":
                        self._add_issue(
                            session,
                            item,
                            code=outcome.issue_code or "RESOURCE_DOWNLOAD_FAILED",
                            message=outcome.issue_message or "Resource could not be backed up",
                            version_id=version.id,
                            safe_url=_redact_text(outcome.candidate.safe_url, token),
                            asset_type=outcome.candidate.type,
                            http_status=outcome.http_status,
                            attempts=max(1, outcome.attempts),
                        )
                for issue in version_issues:
                    self._add_issue(
                        session,
                        item,
                        code=issue.code,
                        message=issue.message,
                        version_id=version.id,
                        http_status=issue.http_status,
                        attempts=issue.attempts,
                    )
            checkpoint_key = f"document:{document.repository_id}:{document.id}"
            checkpoint = session.scalar(
                select(SyncCheckpoint).where(SyncCheckpoint.checkpoint_key == checkpoint_key)
            )
            already_accounted = bool(
                checkpoint is not None and checkpoint.data.get("queue_item_id") == item.id
            )

            document.latest_successful_version_id = version.id
            document.type = str(data.get("type") or document.type)
            document.title = str(data.get("title") or document.title)
            document.slug = _optional_str(data.get("slug")) or document.slug
            document.remote_updated_at = _parse_datetime(
                data.get("updated_at") or data.get("content_updated_at")
            )
            document.deleted_at = None
            document.deleted_slug = None
            if not already_accounted:
                subtask.document_completed += 1
                if completeness == "partial":
                    subtask.document_partial += 1
                    subtask.status = "running"
                else:
                    subtask.document_succeeded += 1
                subtask.asset_total += len(outcomes)
                subtask.asset_succeeded += sum(
                    outcome.status == "downloaded" for outcome in outcomes
                )
                subtask.asset_failed += sum(
                    outcome.status != "downloaded" for outcome in outcomes
                )
                subtask.issue_count += sum(
                    outcome.status != "downloaded" for outcome in outcomes
                ) + len(version_issues)
            if checkpoint is None:
                checkpoint = SyncCheckpoint(
                    checkpoint_key=checkpoint_key,
                    repository_id=document.repository_id,
                    document_id=document.id,
                    stage="version_committed",
                )
                session.add(checkpoint)
            checkpoint.completed = True
            checkpoint.next_retry_at = None
            checkpoint.stage = "version_committed"
            checkpoint.data = {
                "content_hash": content_hash,
                "version_id": version.id,
                "queue_item_id": item.id,
            }
            checkpoint.updated_at = current

    def _finalize_repository(
        self,
        item: QueueItemSnapshot,
        worker_id: str,
        payload: dict[str, Any],
    ) -> None:
        current = self._now()
        with self._session_factory() as session:
            pending = session.scalar(
                select(func.count())
                .select_from(QueueItem)
                .where(
                    QueueItem.subtask_id == item.subtask_id,
                    QueueItem.category == "document_sync",
                    QueueItem.status.in_(("pending", "running", "retry_wait")),
                )
            )
        if pending:
            self._queue.continue_with_payload(
                item.id,
                worker_id,
                payload,
                available_at=current,
                priority=100,
            )
            return
        with self._session_factory.begin() as session:
            repository = session.get(Repository, item.repository_id)
            subtask = session.get(BackupSubtask, item.subtask_id)
            if repository is None or subtask is None:
                raise RuntimeError("repository sync records disappeared")
            failed_items = session.scalar(
                select(func.count())
                .select_from(QueueItem)
                .where(
                    QueueItem.subtask_id == subtask.id,
                    QueueItem.category == "document_sync",
                    QueueItem.status == "failed",
                )
            )
            subtask.document_failed = max(subtask.document_failed, int(failed_items or 0))
            if subtask.document_failed and not (subtask.document_succeeded or subtask.document_partial):
                subtask.status = "failed"
            elif subtask.document_failed or subtask.document_partial or subtask.asset_failed:
                subtask.status = "partial"
            else:
                subtask.status = "succeeded"
            subtask.finished_at = current
            candidate = _parse_datetime(payload.get("candidate_watermark")) or current
            has_failed_documents = bool(failed_items)
            if not has_failed_documents:
                repository.safe_watermark = candidate
            if subtask.status in {"succeeded", "partial"}:
                repository.last_success_at = current
            checkpoint_key = f"repository:{repository.id}:watermark"
            checkpoint = session.scalar(
                select(SyncCheckpoint).where(SyncCheckpoint.checkpoint_key == checkpoint_key)
            )
            if checkpoint is None:
                checkpoint = SyncCheckpoint(
                    checkpoint_key=checkpoint_key,
                    repository_id=repository.id,
                    stage="repository_complete",
                )
                session.add(checkpoint)
            checkpoint.completed = not has_failed_documents
            if not has_failed_documents:
                checkpoint.safe_watermark = candidate
            checkpoint.data = {"job_id": item.job_id, "status": subtask.status}
        self._queue.complete(item.id, worker_id)

    def _upsert_document_summaries(
        self,
        item: QueueItemSnapshot,
        records: list[dict[str, Any]],
    ) -> list[tuple[str, dict[str, Any]]]:
        specs: list[tuple[str, dict[str, Any]]] = []
        with self._session_factory.begin() as session:
            subtask = session.get(BackupSubtask, item.subtask_id)
            if subtask is None or not item.repository_id:
                raise RuntimeError("backup subtask disappeared")
            for summary in records:
                remote_id = _optional_str(summary.get("id") or summary.get("doc_id"))
                if not remote_id:
                    continue
                document = session.scalar(
                    select(Document).where(
                        Document.repository_id == item.repository_id,
                        Document.yuque_doc_id == remote_id,
                    )
                )
                toc = session.scalar(
                    select(TocItem).where(
                        TocItem.repository_id == item.repository_id,
                        TocItem.yuque_doc_id == remote_id,
                    )
                )
                path = toc.path if toc else str(summary.get("path") or f"/{summary.get('slug') or remote_id}")
                title = str(summary.get("title") or "Untitled")
                slug = _optional_str(summary.get("slug"))
                doc_type = str(summary.get("type") or "unknown")
                missing = document is None or document.latest_successful_version_id is None
                metadata_changed = bool(
                    document
                    and (
                        document.title != title
                        or document.slug != slug
                        or document.type != doc_type
                        or document.path != path
                    )
                )
                latest_remote = _optional_str(summary.get("latest_version_id"))
                remote_changed = False
                if document and document.latest_successful_version_id and latest_remote:
                    latest = session.get(DocumentVersion, document.latest_successful_version_id)
                    remote_changed = latest is None or latest.remote_version_id != latest_remote
                summary_updated_at = _parse_datetime(
                    summary.get("updated_at") or summary.get("content_updated_at")
                )
                stored_updated_at = (
                    _ensure_utc(document.remote_updated_at)
                    if document is not None and document.remote_updated_at is not None
                    else None
                )
                remote_timestamp_changed = bool(
                    document is not None
                    and summary_updated_at is not None
                    and (stored_updated_at is None or summary_updated_at > stored_updated_at)
                )
                restored = bool(document is not None and document.deleted_at is not None)
                if document is None:
                    document = Document(
                        repository_id=item.repository_id,
                        yuque_doc_id=remote_id,
                        title=title,
                        type=doc_type,
                        slug=slug,
                        path=path,
                        original_path=path,
                        toc_item_id=toc.id if toc else None,
                    )
                    session.add(document)
                    session.flush()
                else:
                    document.title = title
                    document.slug = slug
                    document.type = doc_type
                    document.path = path
                    document.original_path = path
                    document.toc_item_id = toc.id if toc else None
                if missing or metadata_changed or remote_changed or remote_timestamp_changed or restored:
                    specs.append((document.id, summary))
            subtask.document_total += len(specs)
        return specs

    def _replace_toc(self, repository_id: str, records: list[dict[str, Any]]) -> None:
        current = self._now()
        normalized: dict[str, dict[str, Any]] = {}
        for index, record in enumerate(records):
            remote_id = _optional_str(record.get("uuid") or record.get("id")) or f"index-{index}"
            normalized[remote_id] = {
                "remote_id": remote_id,
                "parent_remote_id": _optional_str(record.get("parent_uuid") or record.get("parent_id")),
                "yuque_doc_id": _optional_str(record.get("doc_id") or record.get("document_id")),
                "type": str(record.get("type") or "UNKNOWN"),
                "title": str(record.get("title") or "Untitled"),
                "order_index": index,
            }
        paths: dict[str, str] = {}

        def path_for(remote_id: str, trail: frozenset[str] = frozenset()) -> str:
            if remote_id in paths:
                return paths[remote_id]
            record = normalized[remote_id]
            title = record["title"].strip("/") or "Untitled"
            parent = record["parent_remote_id"]
            if parent in normalized and parent not in trail:
                path = f"{path_for(parent, trail | {remote_id}).rstrip('/')}/{title}"
            else:
                path = f"/{title}"
            paths[remote_id] = path
            return path

        with self._session_factory.begin() as session:
            existing = {
                item.remote_id: item
                for item in session.scalars(select(TocItem).where(TocItem.repository_id == repository_id))
            }
            for remote_id, record in normalized.items():
                item = existing.get(remote_id)
                if item is None:
                    item = TocItem(repository_id=repository_id, remote_id=remote_id)
                    session.add(item)
                item.parent_remote_id = record["parent_remote_id"]
                item.yuque_doc_id = record["yuque_doc_id"]
                item.type = record["type"]
                item.title = record["title"]
                item.order_index = record["order_index"]
                item.path = path_for(remote_id)
                item.updated_at = current
            stale = set(existing) - set(normalized)
            if stale:
                session.execute(
                    delete(TocItem).where(
                        TocItem.repository_id == repository_id,
                        TocItem.remote_id.in_(stale),
                    )
                )
            repository = session.get(Repository, repository_id)
            if repository:
                repository.toc_updated_at = current

    def _mark_deleted_documents(
        self,
        repository_id: str,
        records: list[dict[str, Any]],
        source_job_id: str | None,
    ) -> None:
        with self._session_factory.begin() as session:
            for record in records:
                remote_id = _optional_str(record.get("id") or record.get("doc_id"))
                if not remote_id:
                    continue
                document = session.scalar(
                    select(Document).where(
                        Document.repository_id == repository_id,
                        Document.yuque_doc_id == remote_id,
                    )
                )
                if document is None:
                    continue
                document.deleted_at = _parse_datetime(record.get("deleted_at")) or self._now()
                document.deleted_slug = _optional_str(record.get("deleted_slug"))

    async def _handle_document_not_found(
        self,
        item: QueueItemSnapshot,
        worker_id: str,
        error: YuqueNotFoundError,
    ) -> None:
        credential = self._credential(item)
        repository = self._repository(item)
        document = self._document(item)
        client = self._client(credential)
        response = await client.list_documents(repository.yuque_book_id, deleted=True, offset=0)
        match = next(
            (
                record
                for record in payload_list(response)
                if _optional_str(record.get("id") or record.get("doc_id")) == document.yuque_doc_id
            ),
            None,
        )
        if match:
            self._mark_deleted_documents(repository.id, [match], item.job_id)
            with self._session_factory.begin() as session:
                subtask = session.get(BackupSubtask, item.subtask_id)
                checkpoint_key = f"document:{repository.id}:{document.id}"
                checkpoint = session.scalar(
                    select(SyncCheckpoint).where(
                        SyncCheckpoint.checkpoint_key == checkpoint_key
                    )
                )
                already_accounted = bool(
                    checkpoint is not None and checkpoint.data.get("queue_item_id") == item.id
                )
                if subtask and not already_accounted:
                    subtask.document_completed += 1
                    subtask.document_succeeded += 1
                if checkpoint is None:
                    checkpoint = SyncCheckpoint(
                        checkpoint_key=checkpoint_key,
                        repository_id=repository.id,
                        document_id=document.id,
                        stage="deletion_confirmed",
                    )
                    session.add(checkpoint)
                checkpoint.stage = "deletion_confirmed"
                checkpoint.completed = True
                checkpoint.next_retry_at = None
                checkpoint.data = {
                    "deleted": True,
                    "queue_item_id": item.id,
                }
                checkpoint.updated_at = self._now()
            self._queue.complete(item.id, worker_id)
        else:
            self._handle_terminal_error(
                item,
                worker_id,
                "YUQUE_SOURCE_INCONSISTENT",
                "Document detail was missing and deletion could not be confirmed",
                error.status_code,
            )

    def _merge_discovered_repositories(
        self,
        credential_id: str,
        records: list[dict[str, Any]],
        payload: dict[str, Any],
    ) -> None:
        counts = {key: int(value) for key, value in dict(payload.get("counts", {})).items()}
        counts.setdefault("discovered", 0)
        counts.setdefault("created", 0)
        counts.setdefault("updated", 0)
        counts.setdefault("duplicates", 0)
        counts.setdefault("requires_primary_selection", 0)
        current = self._now()
        with self._session_factory.begin() as session:
            credential = session.get(YuqueCredential, credential_id)
            if credential is None:
                raise RuntimeError("credential disappeared")
            for record in records:
                book_id = _optional_str(record.get("id") or record.get("book_id"))
                if not book_id:
                    continue
                counts["discovered"] += 1
                repository = session.scalar(
                    select(Repository).where(
                        Repository.normalized_base_url == credential.base_url,
                        Repository.yuque_book_id == book_id,
                    )
                )
                created = repository is None
                if repository is None:
                    repo_type = _optional_str(record.get("type"))
                    repository = Repository(
                        normalized_base_url=credential.base_url,
                        yuque_book_id=book_id,
                        name=str(record.get("name") or "Untitled"),
                        slug=_optional_str(record.get("slug")),
                        namespace=_optional_str(record.get("namespace")),
                        repo_type=repo_type,
                        selected=(repo_type or "Book").lower() == "book",
                        content_updated_at=_parse_datetime(record.get("content_updated_at")),
                    )
                    session.add(repository)
                    session.flush()
                    counts["created"] += 1
                else:
                    repository.name = str(record.get("name") or repository.name)
                    repository.slug = _optional_str(record.get("slug"))
                    repository.namespace = _optional_str(record.get("namespace"))
                    repository.content_updated_at = _parse_datetime(record.get("content_updated_at"))
                    counts["updated"] += 1
                relation = session.scalar(
                    select(RepositoryCredential).where(
                        RepositoryCredential.repository_id == repository.id,
                        RepositoryCredential.credential_id == credential_id,
                    )
                )
                if relation is None:
                    existing_count = (
                        session.scalar(
                            select(func.count())
                            .select_from(RepositoryCredential)
                            .where(RepositoryCredential.repository_id == repository.id)
                        )
                        or 0
                    )
                    relation = RepositoryCredential(
                        repository_id=repository.id,
                        credential_id=credential_id,
                        is_primary=existing_count == 0,
                        last_discovered_at=current,
                    )
                    session.add(relation)
                    if existing_count:
                        session.execute(
                            update(RepositoryCredential)
                            .where(RepositoryCredential.repository_id == repository.id)
                            .values(is_primary=False)
                        )
                        counts["duplicates"] += 1
                        counts["requires_primary_selection"] += 1
                else:
                    relation.last_discovered_at = current
                if created:
                    repository.last_success_at = None
        payload["counts"] = counts

    def _finish_discovery(
        self,
        item: QueueItemSnapshot,
        worker_id: str,
        payload: dict[str, Any],
    ) -> None:
        with self._session_factory.begin() as session:
            operation = session.get(Operation, item.operation_id) if item.operation_id else None
            credential = session.get(YuqueCredential, item.credential_id) if item.credential_id else None
            if operation is None or credential is None:
                raise RuntimeError("repository discovery records disappeared")
            operation.status = "succeeded"
            operation.result = dict(payload.get("counts", {}))
            operation.error = None
            operation.next_retry_at = None
            operation.finished_at = self._now()
            if credential.status == "waiting_quota":
                credential.status = "valid"
                credential.next_retry_at = None
        self._queue.complete(item.id, worker_id)

    def _handle_quota_error(
        self,
        item: QueueItemSnapshot,
        worker_id: str,
        error: YuqueQuotaError,
    ) -> None:
        if item.credential_id and error.rate_limit:
            self._persist_rate(item.credential_id, error.rate_limit)
        next_retry = self._queue.retry_quota(
            item.id,
            worker_id,
            retry_after_seconds=error.retry_after_seconds,
        )
        with self._session_factory.begin() as session:
            if item.credential_id:
                credential = session.get(YuqueCredential, item.credential_id)
                if credential:
                    credential.status = "waiting_quota"
                    credential.next_retry_at = next_retry
                    credential.pause_reason = "quota"
            if item.operation_id:
                operation = session.get(Operation, item.operation_id)
                if operation:
                    operation.status = "waiting_quota"
                    operation.next_retry_at = next_retry
            if item.subtask_id:
                subtask = session.get(BackupSubtask, item.subtask_id)
                if subtask:
                    subtask.status = "waiting_quota"
                    subtask.next_retry_at = next_retry

    def _handle_transient_error(
        self,
        item: QueueItemSnapshot,
        worker_id: str,
        error: YuqueTransientError,
    ) -> None:
        if item.credential_id and error.rate_limit:
            self._persist_rate(item.credential_id, error.rate_limit)
        next_retry = self._queue.retry_transient(
            item.id,
            worker_id,
            code=error.code,
            message=str(error),
        )
        if next_retry is None:
            self._mark_verification_failed(item, error.code)
            self._mark_failed_records(
                item, error.code, "Yuque request failed after retries", error.status_code
            )

    def _handle_auth_error(
        self,
        item: QueueItemSnapshot,
        worker_id: str,
        error: YuqueAuthError,
    ) -> None:
        if item.credential_id is None:
            self._queue.fail(item.id, worker_id, code=error.code, message=str(error))
            self._mark_failed_records(
                item,
                error.code,
                "Yuque rejected the configured credential",
                error.status_code,
            )
            return
        current = self._now()
        with self._session_factory.begin() as session:
            queue_item = session.get(QueueItem, item.id)
            if (
                queue_item is None
                or queue_item.status != "running"
                or queue_item.lease_owner != worker_id
            ):
                raise RuntimeError("queue item is not leased by this worker")
            credential = session.get(YuqueCredential, item.credential_id)
            if credential is not None:
                if error.rate_limit is not None:
                    self._persist_rate_in_session(
                        session,
                        credential,
                        error.rate_limit,
                        current=current,
                    )
                credential.status = "action_required"
                credential.enabled = False
                credential.verification_valid = False
                credential.next_retry_at = None
                credential.last_error_code = error.code
                credential.pause_reason = "authentication"
            self._finish_queue_item(
                queue_item,
                status="failed",
                current=current,
                code=error.code,
                message=str(error),
            )
            self._mark_failed_records_in_session(
                session,
                item,
                error.code,
                "Yuque rejected the configured credential",
                error.status_code,
            )

            pending_items = list(
                session.scalars(
                    select(QueueItem).where(
                        QueueItem.credential_id == item.credential_id,
                        QueueItem.status.in_(("pending", "retry_wait")),
                    )
                )
            )
            affected_job_ids = {value for value in (item.job_id,) if value is not None}
            affected_subtask_ids = {value for value in (item.subtask_id,) if value is not None}
            representative_by_subtask: dict[str, QueueItem] = {}
            for pending_item in pending_items:
                self._finish_queue_item(
                    pending_item,
                    status="cancelled",
                    current=current,
                    code=error.code,
                    message="Credential queue stopped after Yuque authentication failed",
                )
                if pending_item.job_id is not None:
                    affected_job_ids.add(pending_item.job_id)
                if pending_item.subtask_id is not None:
                    affected_subtask_ids.add(pending_item.subtask_id)
                    representative_by_subtask.setdefault(pending_item.subtask_id, pending_item)
                if pending_item.operation_id is not None:
                    operation = session.get(Operation, pending_item.operation_id)
                    if operation is not None and operation.status in {
                        "queued",
                        "running",
                        "waiting_quota",
                    }:
                        operation.status = "cancelled"
                        operation.finished_at = current
                        operation.next_retry_at = None

            session.flush()
            for subtask_id in affected_subtask_ids:
                has_active_items = session.scalar(
                    select(func.count())
                    .select_from(QueueItem)
                    .where(
                        QueueItem.subtask_id == subtask_id,
                        QueueItem.status.in_(("pending", "running", "retry_wait")),
                    )
                )
                if has_active_items:
                    continue
                subtask = session.get(BackupSubtask, subtask_id)
                if subtask is None or subtask.status in {"succeeded", "partial", "cancelled"}:
                    continue
                remaining_documents = max(0, subtask.document_total - subtask.document_completed)
                subtask.document_completed += remaining_documents
                subtask.document_failed += remaining_documents
                subtask.status = (
                    "partial"
                    if subtask.document_succeeded or subtask.document_partial
                    else "failed"
                )
                subtask.next_retry_at = None
                subtask.finished_at = current
                subtask.last_issue = "Yuque rejected the configured credential"
                if subtask_id != item.subtask_id:
                    subtask.issue_count += 1
                    representative = representative_by_subtask.get(subtask_id)
                    if representative is not None:
                        self._add_issue(
                            session,
                            QueueItemSnapshot.from_model(representative),
                            code=error.code,
                            message="Yuque rejected the configured credential",
                            http_status=error.status_code,
                        )

            session.flush()
            for job_id in sorted(affected_job_ids):
                aggregate_job_in_session(session, job_id, current=current)

    def _handle_terminal_error(
        self,
        item: QueueItemSnapshot,
        worker_id: str,
        code: str,
        message: str,
        http_status: int | None = None,
    ) -> None:
        self._queue.fail(item.id, worker_id, code=code, message=message)
        self._mark_verification_failed(item, code)
        self._mark_failed_records(item, code, message, http_status)

    def _mark_verification_failed(self, item: QueueItemSnapshot, code: str) -> None:
        if item.category != "credential_verify" or not item.credential_id:
            return
        with self._session_factory.begin() as session:
            credential = session.get(YuqueCredential, item.credential_id)
            if credential is not None:
                credential.status = "action_required"
                credential.enabled = False
                credential.verification_valid = False
                credential.last_error_code = code
                credential.pause_reason = "verification"

    def _mark_failed_records(
        self,
        item: QueueItemSnapshot,
        code: str,
        message: str,
        http_status: int | None = None,
    ) -> None:
        with self._session_factory.begin() as session:
            self._mark_failed_records_in_session(session, item, code, message, http_status)

    def _mark_failed_records_in_session(
        self,
        session: Session,
        item: QueueItemSnapshot,
        code: str,
        message: str,
        http_status: int | None,
    ) -> None:
        current = self._now()
        if item.operation_id:
            operation = session.get(Operation, item.operation_id)
            if operation:
                operation.status = "failed"
                operation.error = {"code": code, "message": message}
                operation.finished_at = current
                operation.next_retry_at = None
        if item.subtask_id:
            subtask = session.get(BackupSubtask, item.subtask_id)
            if subtask:
                if item.category == "document_sync":
                    subtask.document_completed += 1
                    subtask.document_failed += 1
                elif not (subtask.document_succeeded or subtask.document_partial):
                    subtask.status = "failed"
                subtask.issue_count += 1
                subtask.last_issue = message[:512]
        if item.document_id and item.repository_id:
            checkpoint_key = f"document:{item.repository_id}:{item.document_id}"
            checkpoint = session.scalar(
                select(SyncCheckpoint).where(SyncCheckpoint.checkpoint_key == checkpoint_key)
            )
            if checkpoint is None:
                checkpoint = SyncCheckpoint(
                    checkpoint_key=checkpoint_key,
                    repository_id=item.repository_id,
                    document_id=item.document_id,
                    stage="failed",
                )
                session.add(checkpoint)
            checkpoint.completed = False
            checkpoint.next_retry_at = None
            checkpoint.data = {"code": code, "job_id": item.job_id}
        if item.job_id:
            self._add_issue(
                session,
                item,
                code=code,
                message=message,
                http_status=http_status,
                attempts=max(1, item.attempt_count),
            )

    def _add_issue(
        self,
        session: Session,
        item: QueueItemSnapshot,
        *,
        code: str,
        message: str,
        version_id: str | None = None,
        safe_url: str | None = None,
        asset_type: str | None = None,
        http_status: int | None = None,
        attempts: int = 1,
    ) -> None:
        if not item.job_id:
            return
        document = session.get(Document, item.document_id) if item.document_id else None
        session.add(
            BackupIssue(
                job_id=item.job_id,
                subtask_id=item.subtask_id,
                credential_id=item.credential_id,
                repository_id=item.repository_id,
                document_id=item.document_id,
                version_id=version_id,
                level="warning" if code.startswith(("RESOURCE_", "SHEET_", "TABLE_")) else "error",
                code=code[:64],
                message=message[:1024],
                document_title=document.title if document else None,
                asset_type=asset_type,
                safe_url=safe_url,
                http_status=http_status,
                attempt_count=attempts,
                first_occurred_at=self._now(),
                last_occurred_at=self._now(),
            )
        )

    def _persist_rate(self, credential_id: str, snapshot: RateLimitSnapshot) -> None:
        current = self._now()
        with self._session_factory.begin() as session:
            credential = session.get(YuqueCredential, credential_id)
            if credential is None:
                return
            self._persist_rate_in_session(session, credential, snapshot, current=current)

    def _persist_rate_in_session(
        self,
        session: Session,
        credential: YuqueCredential,
        snapshot: RateLimitSnapshot,
        *,
        current: datetime,
    ) -> None:
        credential.rate_limit_limit = snapshot.limit
        credential.rate_limit_remaining = snapshot.remaining
        credential.rate_limit_observed_at = snapshot.observed_at
        if credential.status == "waiting_quota" and (
            snapshot.remaining is None or snapshot.remaining > 0
        ):
            credential.status = "valid"
            credential.next_retry_at = None
            credential.pause_reason = None
        if credential.subject_id and credential.subject_type != "unknown":
            bucket = session.scalar(
                select(RateLimitBucket).where(
                    RateLimitBucket.base_url == credential.base_url,
                    RateLimitBucket.subject_type == credential.subject_type,
                    RateLimitBucket.subject_id == credential.subject_id,
                )
            )
            if bucket is None:
                bucket = RateLimitBucket(
                    base_url=credential.base_url,
                    subject_type=credential.subject_type,
                    subject_id=credential.subject_id,
                )
                session.add(bucket)
            bucket.rate_limit_limit = snapshot.limit
            bucket.rate_limit_remaining = snapshot.remaining
            bucket.observed_at = snapshot.observed_at
            delay = 60.0 if snapshot.remaining == 0 else self._settings.yuque_request_interval_seconds
            bucket.next_allowed_at = current + timedelta(seconds=delay)

    def _defer_for_rate_limit(
        self,
        item: QueueItemSnapshot,
        worker_id: str,
        credential: YuqueCredential,
    ) -> bool:
        if not credential.subject_id or credential.subject_type == "unknown":
            return False
        with self._session_factory() as session:
            bucket = session.scalar(
                select(RateLimitBucket).where(
                    RateLimitBucket.base_url == credential.base_url,
                    RateLimitBucket.subject_type == credential.subject_type,
                    RateLimitBucket.subject_id == credential.subject_id,
                )
            )
            if bucket is None or bucket.next_allowed_at is None:
                return False
            next_allowed = _ensure_utc(bucket.next_allowed_at)
            if next_allowed <= self._now():
                return False
        self._queue.continue_with_payload(item.id, worker_id, item.payload, available_at=next_allowed)
        return True

    def _start_operation(self, operation_id: str | None) -> None:
        if not operation_id:
            raise RuntimeError("operation queue item is missing its operation")
        with self._session_factory.begin() as session:
            operation = session.get(Operation, operation_id)
            if operation is None:
                raise RuntimeError("operation disappeared")
            if operation.started_at is None:
                operation.started_at = self._now()
            operation.status = "running"
            operation.next_retry_at = None

    def _start_subtask(self, subtask_id: str | None) -> None:
        if not subtask_id:
            raise RuntimeError("repository queue item is missing its subtask")
        with self._session_factory.begin() as session:
            subtask = session.get(BackupSubtask, subtask_id)
            if subtask is None:
                raise RuntimeError("backup subtask disappeared")
            if subtask.started_at is None:
                subtask.started_at = self._now()
            subtask.status = "running"
            subtask.next_retry_at = None

    def _credential(self, item: QueueItemSnapshot) -> YuqueCredential:
        if not item.credential_id:
            raise RuntimeError("queue item is missing a credential")
        with self._session_factory() as session:
            credential = session.get(YuqueCredential, item.credential_id)
            if credential is None or credential.deleted_at is not None:
                raise RuntimeError("credential is unavailable")
            return credential

    def _repository(self, item: QueueItemSnapshot) -> Repository:
        if not item.repository_id:
            raise RuntimeError("queue item is missing a repository")
        with self._session_factory() as session:
            repository = session.get(Repository, item.repository_id)
            if repository is None:
                raise RuntimeError("repository is unavailable")
            return repository

    def _document(self, item: QueueItemSnapshot) -> Document:
        if not item.document_id:
            raise RuntimeError("queue item is missing a document")
        with self._session_factory() as session:
            document = session.get(Document, item.document_id)
            if document is None:
                raise RuntimeError("document is unavailable")
            return document

    def _client(self, credential: YuqueCredential) -> YuqueClient:
        token = self._token_resolver(credential)
        return YuqueClient(
            credential.base_url,
            token,
            client=self._yuque_http_client,
            max_redirects=self._settings.resource_redirect_limit,
            now=self._now,
        )

    @staticmethod
    def _normalized_document_metadata(
        document: Document,
        data: dict[str, Any],
        candidates: list[ResourceCandidate],
    ) -> dict[str, Any]:
        raw_body, body_format = _raw_body_and_format(data)
        return {
            "yuque_doc_id": document.yuque_doc_id,
            "title": str(data.get("title") or document.title),
            "slug": _optional_str(data.get("slug")) or document.slug,
            "type": str(data.get("type") or document.type),
            "format": body_format,
            "path": document.path,
            "body": raw_body,
            "resources": [
                {
                    "url": candidate.normalized_url,
                    "name": candidate.name,
                    "type": candidate.type,
                    "position": candidate.position,
                    "source_location": candidate.source_location,
                }
                for candidate in candidates
            ],
        }

    @staticmethod
    def _resource_manifest(outcome: AssetOutcome, token: str) -> dict[str, Any]:
        return {
            "url": _redact_text(outcome.candidate.normalized_url, token),
            "safe_url": _redact_text(outcome.candidate.safe_url, token),
            "name": _redact_text(outcome.candidate.name, token),
            "type": outcome.candidate.type,
            "position": outcome.candidate.position,
            "status": outcome.status,
            "sha256": outcome.sha256,
            "size": outcome.size,
            "issue_code": outcome.issue_code,
        }


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _redact_text(value: str, secret: str) -> str:
    return value.replace(secret, "[REDACTED]") if secret else value


def _redact_optional_text(value: str | None, secret: str) -> str | None:
    return _redact_text(value, secret) if value is not None else None


def _redact_secret(value: Any, secret: str) -> Any:
    if isinstance(value, str):
        return _redact_text(value, secret)
    if isinstance(value, list):
        return [_redact_secret(item, secret) for item in value]
    if isinstance(value, dict):
        return {
            _redact_text(key, secret) if isinstance(key, str) else key: _redact_secret(item, secret)
            for key, item in value.items()
        }
    return value


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return _ensure_utc(value)
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _ensure_utc(parsed)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _ensure_utc(value).isoformat().replace("+00:00", "Z")


def _raw_body_and_format(data: dict[str, Any]) -> tuple[str, str | None]:
    body_format = _optional_str(data.get("format"))
    doc_type = str(data.get("type") or "")
    table_pages = data.get("body_table_pages")
    if doc_type == "Table" and isinstance(table_pages, list):
        return (
            json.dumps(
                {"body_table": data.get("body_table"), "pages": table_pages},
                ensure_ascii=False,
                sort_keys=True,
            ),
            body_format or "json",
        )
    keys = {
        "Sheet": ("body_sheet", "lakesheet"),
        "Table": ("body_table", body_format or "json"),
    }
    preferred_key, fallback_format = keys.get(doc_type, ("body", body_format))
    for key in (preferred_key, "body", "body_html", "body_lake", "body_sheet", "body_table"):
        value = data.get(key)
        if isinstance(value, str):
            return value, body_format or fallback_format
        if value is not None:
            return json.dumps(value, ensure_ascii=False, sort_keys=True), body_format or fallback_format
    return "", body_format or fallback_format


def _merge_table_page_data(pages: list[dict[str, Any]]) -> dict[str, Any]:
    merged = dict(pages[0])
    raw_bodies = [page.get("body_table") for page in pages if "body_table" in page]
    merged["body_table_pages"] = raw_bodies
    if not raw_bodies:
        return merged

    parsed_bodies = [_parse_table_body(body) for body in raw_bodies]
    records: list[Any] = []
    record_key: str | None = None
    for body in parsed_bodies:
        page_records, page_record_key = _table_records(body)
        if page_records is not None:
            records.extend(page_records)
            record_key = record_key or page_record_key

    first_body = parsed_bodies[0]
    if isinstance(first_body, dict):
        combined_body: Any = dict(first_body)
        combined_body[record_key or "records"] = records
    elif isinstance(first_body, list):
        combined_body = records
    else:
        combined_body = first_body
    if isinstance(raw_bodies[0], str) and not isinstance(combined_body, str):
        combined_body = json.dumps(combined_body, ensure_ascii=False, sort_keys=True)
    merged["body_table"] = combined_body
    return merged


def _parse_table_body(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except ValueError:
        return value


def _table_records(body: Any) -> tuple[list[Any] | None, str | None]:
    if isinstance(body, list):
        return body, None
    if isinstance(body, dict):
        for key in ("records", "rows", "data"):
            value = body.get(key)
            if isinstance(value, list):
                return value, key
    return None, None


def _table_total_count(data: dict[str, Any]) -> int:
    candidates = [data.get("totalCount"), data.get("total_count"), data.get("total")]
    body = data.get("body_table")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            body = None
    if isinstance(body, dict):
        candidates.extend((body.get("totalCount"), body.get("total_count"), body.get("total")))
        meta = body.get("meta")
        if isinstance(meta, dict):
            candidates.extend((meta.get("totalCount"), meta.get("total_count"), meta.get("total")))
    for value in candidates:
        if isinstance(value, int) and value >= 0:
            return value
    return 0
