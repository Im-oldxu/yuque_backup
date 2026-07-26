from __future__ import annotations

import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased
from starlette.responses import MalformedRangeHeader, RangeNotSatisfiable

from app.api.dependencies import CurrentAdmin, DbSession
from app.api.openapi import binary_content, documented_responses
from app.api.schemas import Completeness, Page
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.models import Asset, BackupIssue, Document, DocumentVersion, Repository, VersionAsset
from app.modules.documents.schemas import (
    AssetReferenceResponse,
    BackupIssueResponse,
    DocumentDetail,
    DocumentSummary,
    SearchResponse,
    VersionDetail,
    VersionSummary,
)
from app.modules.documents.service import (
    INLINE_MIME_TYPES,
    escaped_contains,
    get_asset,
    get_document,
    get_version,
    resolve_asset_path,
    resolve_content_path,
    safe_filename,
    serialize_asset_reference,
    serialize_document,
    serialize_document_detail,
    serialize_issue,
    serialize_version,
    serialize_version_detail,
)
from app.modules.exports import markdown_for_version
from app.modules.exports.pdf import render_markdown_pdf

router = APIRouter(prefix="/api/v1", tags=["documents"])

PREVIEW_CSP = (
    "default-src 'none'; img-src 'self'; style-src 'self' 'unsafe-inline'; "
    "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
)

_STRING_HEADER = {"schema": {"type": "string"}}
_PREVIEW_RESPONSE_HEADERS = {
    "Content-Security-Policy": _STRING_HEADER,
    "X-Content-Type-Options": _STRING_HEADER,
    "Cache-Control": _STRING_HEADER,
    "Accept-Ranges": _STRING_HEADER,
}
_DOWNLOAD_RESPONSE_HEADERS = {
    "Content-Disposition": _STRING_HEADER,
    "Accept-Ranges": _STRING_HEADER,
    "Content-Length": _STRING_HEADER,
}
_ASSET_RESPONSE_HEADERS = {
    **_DOWNLOAD_RESPONSE_HEADERS,
    "X-Content-Type-Options": _STRING_HEADER,
    "Cache-Control": _STRING_HEADER,
}


def _ranged_file_responses(
    *error_statuses: int,
    success_content: dict[str, Any],
    success_headers: dict[str, Any],
) -> dict[int | str, dict[str, Any]]:
    responses = documented_responses(
        *error_statuses,
        success_content=success_content,
        success_headers=success_headers,
    )
    partial = dict(responses[200])
    partial["description"] = "Partial Content"
    partial["headers"] = {
        **partial["headers"],
        "Content-Range": _STRING_HEADER,
    }
    responses[206] = partial
    return responses


def _file_response(
    request: Request,
    path: Path,
    *,
    media_type: str,
    filename: str | None = None,
    headers: Mapping[str, str] | None = None,
    content_disposition_type: str = "attachment",
) -> FileResponse:
    stat_result = path.stat()
    response = FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        headers=headers,
        content_disposition_type=content_disposition_type,
        stat_result=stat_result,
    )
    range_header = request.headers.get("Range")
    if range_header is None:
        return response
    if_range = request.headers.get("If-Range")
    if if_range is not None and not response._should_use_range(if_range):
        return response
    try:
        response._parse_range_header(range_header, stat_result.st_size)
    except (MalformedRangeHeader, RangeNotSatisfiable):
        raise AppError(
            422,
            "VALIDATION_ERROR",
            "Range 请求头不合法",
            field_errors=[{"field": "Range", "reason": "invalid"}],
        ) from None
    return response


def _attachment(filename: str) -> str:
    return f"attachment; filename=\"download\"; filename*=UTF-8''{quote(filename, safe='')}"


@router.get(
    "/documents",
    response_model=Page[DocumentSummary],
    status_code=200,
    responses=documented_responses(401, 422),
)
def list_documents(
    db: DbSession,
    _admin: CurrentAdmin,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: str | None = None,
    repository_id: uuid.UUID | None = None,
    toc_item_id: uuid.UUID | None = None,
    deleted: bool | None = None,
    completeness: Completeness | None = None,
) -> Page[DocumentSummary]:
    statement = select(Document)
    if q:
        statement = statement.where(
            or_(
                escaped_contains(Document.title, q),
                escaped_contains(Document.slug, q),
                escaped_contains(Document.path, q),
                escaped_contains(Document.original_path, q),
            )
        )
    if repository_id is not None:
        statement = statement.where(Document.repository_id == str(repository_id))
    if toc_item_id is not None:
        statement = statement.where(Document.toc_item_id == str(toc_item_id))
    if deleted is not None:
        statement = statement.where(
            Document.deleted_at.is_not(None) if deleted else Document.deleted_at.is_(None)
        )
    if completeness is not None:
        latest = aliased(DocumentVersion)
        statement = statement.join(latest, latest.id == Document.latest_successful_version_id).where(
            latest.completeness == completeness
        )
    statement = statement.order_by(Document.title.asc(), Document.id.asc())
    total = db.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
    documents = db.scalars(statement.offset((page - 1) * page_size).limit(page_size)).all()
    return Page(
        items=[serialize_document(db, document) for document in documents],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/search",
    response_model=SearchResponse,
    status_code=200,
    responses=documented_responses(401, 422),
)
def global_search(
    db: DbSession,
    _admin: CurrentAdmin,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    repository_id: uuid.UUID | None = None,
    deleted: bool | None = None,
) -> SearchResponse:
    repositories = db.scalars(
        select(Repository)
        .where(or_(escaped_contains(Repository.name, q), escaped_contains(Repository.namespace, q)))
        .order_by(Repository.name.asc(), Repository.id.asc())
        .limit(20)
    ).all()
    document_query = select(Document).where(
        or_(
            escaped_contains(Document.title, q),
            escaped_contains(Document.slug, q),
            escaped_contains(Document.path, q),
            escaped_contains(Document.original_path, q),
        )
    )
    if repository_id is not None:
        document_query = document_query.where(Document.repository_id == str(repository_id))
    if deleted is not None:
        document_query = document_query.where(
            Document.deleted_at.is_not(None) if deleted else Document.deleted_at.is_(None)
        )
    documents = db.scalars(document_query.order_by(Document.title.asc(), Document.id.asc()).limit(20)).all()
    return SearchResponse(
        repositories=[
            {
                "id": repository.id,
                "name": repository.name,
                "namespace": repository.namespace,
                "selected": repository.selected,
            }
            for repository in repositories
        ],
        documents=[serialize_document(db, document) for document in documents],
    )


@router.get(
    "/documents/{document_id}",
    response_model=DocumentDetail,
    status_code=200,
    responses=documented_responses(401, 404, 422),
)
def document_detail(document_id: uuid.UUID, db: DbSession, _admin: CurrentAdmin) -> DocumentDetail:
    return serialize_document_detail(db, get_document(db, str(document_id)))


@router.get(
    "/documents/{document_id}/versions",
    response_model=Page[VersionSummary],
    status_code=200,
    responses=documented_responses(401, 404, 422),
)
def list_versions(
    document_id: uuid.UUID,
    db: DbSession,
    _admin: CurrentAdmin,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[VersionSummary]:
    document = get_document(db, str(document_id))
    total = (
        db.scalar(select(func.count(DocumentVersion.id)).where(DocumentVersion.document_id == document.id))
        or 0
    )
    versions = db.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.created_at.desc(), DocumentVersion.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return Page(
        items=[serialize_version(version, document) for version in versions],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/documents/{document_id}/versions/{version_id}",
    response_model=VersionDetail,
    status_code=200,
    responses=documented_responses(401, 404, 422),
)
def version_detail(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    db: DbSession,
    _admin: CurrentAdmin,
) -> VersionDetail:
    document = get_document(db, str(document_id))
    return serialize_version_detail(db, get_version(db, document.id, str(version_id)), document)


@router.get(
    "/documents/{document_id}/versions/{version_id}/assets",
    response_model=Page[AssetReferenceResponse],
    status_code=200,
    responses=documented_responses(401, 404, 422),
)
def version_assets(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    db: DbSession,
    _admin: CurrentAdmin,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Literal["pending", "downloaded", "skipped", "failed"] | None = None,
    type: str | None = None,
) -> Page[AssetReferenceResponse]:
    document = get_document(db, str(document_id))
    version = get_version(db, document.id, str(version_id))
    filters = [VersionAsset.version_id == version.id]
    if status:
        filters.append(VersionAsset.status == status)
    if type:
        filters.append(VersionAsset.type == type)
    total = db.scalar(select(func.count(VersionAsset.id)).where(*filters)) or 0
    references = db.scalars(
        select(VersionAsset)
        .where(*filters)
        .order_by(VersionAsset.position.asc(), VersionAsset.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return Page(
        items=[
            serialize_asset_reference(reference, db.get(Asset, reference.asset_id))
            for reference in references
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/documents/{document_id}/versions/{version_id}/issues",
    response_model=Page[BackupIssueResponse],
    status_code=200,
    responses=documented_responses(401, 404, 422),
)
def version_issues(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    db: DbSession,
    _admin: CurrentAdmin,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    level: Literal["warning", "error"] | None = None,
    code: str | None = None,
) -> Page[BackupIssueResponse]:
    document = get_document(db, str(document_id))
    version = get_version(db, document.id, str(version_id))
    filters = [BackupIssue.version_id == version.id]
    if level:
        filters.append(BackupIssue.level == level)
    if code:
        filters.append(BackupIssue.code == code)
    total = db.scalar(select(func.count(BackupIssue.id)).where(*filters)) or 0
    issues = db.scalars(
        select(BackupIssue)
        .where(*filters)
        .order_by(BackupIssue.last_occurred_at.desc(), BackupIssue.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return Page(
        items=[serialize_issue(issue) for issue in issues],
        page=page,
        page_size=page_size,
        total=total,
    )


def _version_file(
    db: DbSession,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
) -> tuple[Document, DocumentVersion]:
    document = get_document(db, str(document_id))
    return document, get_version(db, document.id, str(version_id))


@router.get(
    "/documents/{document_id}/versions/{version_id}/preview",
    response_class=FileResponse,
    status_code=200,
    responses=_ranged_file_responses(
        401,
        404,
        409,
        410,
        422,
        503,
        success_content={"text/html": {"schema": {"type": "string"}}},
        success_headers=_PREVIEW_RESPONSE_HEADERS,
    ),
)
def preview(
    request: Request,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    db: DbSession,
    _admin: CurrentAdmin,
) -> FileResponse:
    _, version = _version_file(db, document_id, version_id)
    if version.completeness == "failed" or (not version.preview_path and version.purged_at is None):
        raise AppError(409, "PREVIEW_NOT_AVAILABLE", "该版本没有可用预览")
    path = resolve_content_path(get_settings(), version.preview_path, purged=version.purged_at is not None)
    return _file_response(
        request,
        path,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Security-Policy": PREVIEW_CSP,
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
        content_disposition_type="inline",
    )


@router.get(
    "/documents/{document_id}/versions/{version_id}/downloads/raw-response",
    response_class=FileResponse,
    status_code=200,
    responses=_ranged_file_responses(
        401,
        404,
        410,
        422,
        503,
        success_content=binary_content("application/json"),
        success_headers=_DOWNLOAD_RESPONSE_HEADERS,
    ),
)
def download_raw_response(
    request: Request,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    db: DbSession,
    _admin: CurrentAdmin,
) -> FileResponse:
    document, version = _version_file(db, document_id, version_id)
    path = resolve_content_path(
        get_settings(), version.raw_response_path, purged=version.purged_at is not None
    )
    return _file_response(
        request,
        path,
        media_type="application/json",
        filename=safe_filename(f"{document.title}-{version.id}.json", "raw-response.json"),
    )


@router.get(
    "/documents/{document_id}/versions/{version_id}/downloads/raw-body",
    response_class=FileResponse,
    status_code=200,
    responses=_ranged_file_responses(
        401,
        404,
        410,
        422,
        503,
        success_content=binary_content("text/plain", "text/html", "application/json"),
        success_headers=_DOWNLOAD_RESPONSE_HEADERS,
    ),
)
def download_raw_body(
    request: Request,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    db: DbSession,
    _admin: CurrentAdmin,
) -> FileResponse:
    document, version = _version_file(db, document_id, version_id)
    path = resolve_content_path(get_settings(), version.raw_body_path, purged=version.purged_at is not None)
    extensions = {"markdown": "md", "html": "html", "lake": "lake", "lakesheet": "json"}
    extension = extensions.get(version.format or "", "txt")
    media_type = (
        "text/html" if extension == "html" else "application/json" if extension == "json" else "text/plain"
    )
    return _file_response(
        request,
        path,
        media_type=media_type,
        filename=safe_filename(f"{document.title}.{extension}", f"body.{extension}"),
    )


@router.get(
    "/documents/{document_id}/versions/{version_id}/markdown",
    response_class=Response,
    status_code=200,
    responses=documented_responses(
        401,
        404,
        410,
        422,
        success_content=binary_content("text/markdown"),
    ),
)
def read_markdown(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    db: DbSession,
    _admin: CurrentAdmin,
) -> Response:
    document, version = _version_file(db, document_id, version_id)
    if version.purged_at is not None:
        raise AppError(410, "VERSION_CONTENT_PURGED", "版本内容已按保留策略清理")
    markdown = markdown_for_version(version, document, get_settings())
    return Response(
        markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get(
    "/documents/{document_id}/versions/{version_id}/downloads/markdown",
    response_class=Response,
    status_code=200,
    responses=documented_responses(
        401,
        404,
        410,
        422,
        success_content=binary_content("text/markdown"),
        success_headers=_DOWNLOAD_RESPONSE_HEADERS,
    ),
)
def download_markdown(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    db: DbSession,
    _admin: CurrentAdmin,
) -> Response:
    document, version = _version_file(db, document_id, version_id)
    if version.purged_at is not None:
        raise AppError(410, "VERSION_CONTENT_PURGED", "版本内容已按保留策略清理")
    markdown = markdown_for_version(version, document, get_settings())
    filename = safe_filename(f"{document.title}.md", "article.md")
    return Response(
        markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": _attachment(filename)},
    )


@router.get(
    "/documents/{document_id}/versions/{version_id}/downloads/pdf",
    response_class=Response,
    status_code=200,
    responses=documented_responses(
        401,
        404,
        410,
        422,
        503,
        success_content=binary_content("application/pdf"),
        success_headers=_DOWNLOAD_RESPONSE_HEADERS,
    ),
)
def download_pdf(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    db: DbSession,
    _admin: CurrentAdmin,
) -> Response:
    document, version = _version_file(db, document_id, version_id)
    if version.purged_at is not None:
        raise AppError(410, "VERSION_CONTENT_PURGED", "版本内容已按保留策略清理")
    markdown = markdown_for_version(version, document, get_settings())
    try:
        content = render_markdown_pdf(markdown)
    except (OSError, RuntimeError) as exc:
        raise AppError(503, "PDF_EXPORT_UNAVAILABLE", "PDF 导出服务暂不可用") from exc
    filename = safe_filename(f"{document.title}.pdf", "article.pdf")
    return Response(
        content,
        media_type="application/pdf",
        headers={"Content-Disposition": _attachment(filename)},
    )


@router.get(
    "/documents/{document_id}/versions/{version_id}/downloads/offline-html",
    response_class=FileResponse,
    status_code=200,
    responses=_ranged_file_responses(
        401,
        404,
        409,
        410,
        422,
        503,
        success_content=binary_content("text/html"),
        success_headers=_DOWNLOAD_RESPONSE_HEADERS,
    ),
)
def download_offline_html(
    request: Request,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    db: DbSession,
    _admin: CurrentAdmin,
) -> FileResponse:
    document, version = _version_file(db, document_id, version_id)
    if not version.preview_path and version.purged_at is None:
        raise AppError(409, "PREVIEW_NOT_AVAILABLE", "该版本没有可用预览")
    path = resolve_content_path(get_settings(), version.preview_path, purged=version.purged_at is not None)
    return _file_response(
        request,
        path,
        media_type="text/html",
        filename=safe_filename(f"{document.title}.html", "offline.html"),
    )


@router.get(
    "/assets/{asset_id}/content",
    response_class=FileResponse,
    status_code=200,
    responses=_ranged_file_responses(
        401,
        404,
        410,
        422,
        503,
        success_content=binary_content("application/octet-stream", "image/*"),
        success_headers=_ASSET_RESPONSE_HEADERS,
    ),
)
def asset_content(
    request: Request,
    asset_id: uuid.UUID,
    db: DbSession,
    _admin: CurrentAdmin,
) -> FileResponse:
    asset = get_asset(db, str(asset_id))
    path = resolve_asset_path(get_settings(), asset)
    inline = asset.mime_type in INLINE_MIME_TYPES
    return _file_response(
        request,
        path,
        media_type=asset.mime_type or "application/octet-stream",
        filename=None if inline else safe_filename(asset.sha256, "asset.bin"),
        content_disposition_type="inline" if inline else "attachment",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )


@router.get(
    "/assets/{asset_id}/download",
    response_class=FileResponse,
    status_code=200,
    responses=_ranged_file_responses(
        401,
        404,
        410,
        422,
        503,
        success_content=binary_content("application/octet-stream", "*/*"),
        success_headers=_ASSET_RESPONSE_HEADERS,
    ),
)
def download_asset(
    request: Request,
    asset_id: uuid.UUID,
    db: DbSession,
    _admin: CurrentAdmin,
) -> FileResponse:
    asset = get_asset(db, str(asset_id))
    path = resolve_asset_path(get_settings(), asset)
    reference_name = db.scalar(
        select(VersionAsset.name)
        .where(VersionAsset.asset_id == asset.id)
        .order_by(VersionAsset.id.asc())
        .limit(1)
    )
    return _file_response(
        request,
        path,
        media_type=asset.mime_type or "application/octet-stream",
        filename=safe_filename(reference_name or asset.sha256, "asset.bin"),
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )
