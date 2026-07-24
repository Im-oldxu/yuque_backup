from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Cookie, Depends, Header, Request
from fastapi.security import APIKeyCookie
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.errors import AppError
from app.core.models import Admin, AdminSession
from app.core.security import (
    CSRF_COOKIE,
    CSRF_HEADER,
    SESSION_COOKIE,
    hash_token,
    require_json_content_type,
    require_same_origin,
)

session_cookie = APIKeyCookie(name=SESSION_COOKIE, auto_error=False)

DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]

_SESSION_TOUCH_INTERVAL = timedelta(minutes=1)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _touch_admin_session(db: Session, session_id: str, now: datetime) -> None:
    # Use a fresh transaction so a WAL snapshot created during authentication is
    # never upgraded to a writer after the worker has committed concurrently.
    with Session(bind=db.get_bind(), autoflush=False, expire_on_commit=False) as touch_db:
        touch_db.execute(
            update(AdminSession)
            .where(AdminSession.id == session_id)
            .values(last_used_at=now)
        )
        touch_db.commit()


def _is_database_locked(exc: OperationalError) -> bool:
    return "database is locked" in str(exc).lower()


def get_current_admin(
    request: Request,
    db: DbSession,
    session_token: Annotated[str | None, Depends(session_cookie)],
) -> Admin:
    if not session_token:
        raise AppError(401, "AUTH_REQUIRED", "需要有效的管理员会话")

    admin_session = db.scalar(
        select(AdminSession).where(AdminSession.token_hash == hash_token(session_token))
    )
    if admin_session is None:
        raise AppError(401, "AUTH_REQUIRED", "需要有效的管理员会话")
    admin = db.get(Admin, admin_session.admin_id)
    if admin is None:
        raise AppError(401, "AUTH_REQUIRED", "需要有效的管理员会话")
    now = datetime.now(UTC)
    if (
        admin_session.revoked_at is not None
        or _as_utc(admin_session.expires_at) <= now
        or admin_session.password_version != admin.password_version
    ):
        raise AppError(401, "AUTH_REQUIRED", "需要有效的管理员会话")

    should_touch = now - _as_utc(admin_session.last_used_at) >= _SESSION_TOUCH_INTERVAL
    # End the read transaction before any authenticated route starts its own
    # work. This is required for reliable read-then-write requests under WAL.
    db.commit()
    if should_touch:
        try:
            _touch_admin_session(db, admin_session.id, now)
        except OperationalError as exc:
            if not _is_database_locked(exc):
                raise
    request.state.admin_session = admin_session
    request.state.current_admin = admin
    return admin


CurrentAdmin = Annotated[Admin, Depends(get_current_admin)]


def require_csrf_admin(
    request: Request,
    admin: CurrentAdmin,
    csrf_cookie: Annotated[
        str | None,
        Cookie(alias=CSRF_COOKIE, include_in_schema=False),
    ] = None,
    csrf_header: Annotated[
        str | None,
        Header(alias=CSRF_HEADER, include_in_schema=False),
    ] = None,
) -> Admin:
    admin_session = getattr(request.state, "admin_session", None)
    if not isinstance(admin_session, AdminSession) or not csrf_cookie or not csrf_header:
        raise AppError(403, "CSRF_INVALID", "CSRF 或同源校验失败")
    if not secrets.compare_digest(csrf_cookie, csrf_header):
        raise AppError(403, "CSRF_INVALID", "CSRF 或同源校验失败")
    if not secrets.compare_digest(admin_session.csrf_hash, hash_token(csrf_header)):
        raise AppError(403, "CSRF_INVALID", "CSRF 或同源校验失败")
    return admin


CsrfAdmin = Annotated[Admin, Depends(require_csrf_admin)]


def require_public_write_security(request: Request, settings: AppSettings) -> None:
    require_json_content_type(request)
    require_same_origin(request, settings)


PublicWriteSecurity = Annotated[None, Depends(require_public_write_security)]
