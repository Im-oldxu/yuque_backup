from __future__ import annotations

import json
import math
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.core.models import Admin, AdminSession, IdempotencyRecord, LoginAttempt
from app.core.security import (
    generate_csrf_token,
    generate_session_token,
    hash_password,
    hash_token,
    login_attempt_key,
    password_needs_rehash,
    request_fingerprint,
    verify_password,
    verify_password_or_dummy,
)

INITIALIZATION_OWNER = "system-initialization"
INITIALIZATION_PATH = "/api/v1/system/initialize"
IDEMPOTENCY_TTL = timedelta(hours=24)
_login_attempt_lock = threading.Lock()
_password_change_lock = threading.Lock()


@dataclass(frozen=True, slots=True)
class AuthResult:
    admin: Admin
    session_token: str
    csrf_token: str


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _acquire_sqlite_write_lock(db: Session) -> None:
    if db.get_bind().dialect.name == "sqlite":
        # This is the first statement after a rollback, so SQLite obtains its
        # writer reservation before we create a fresh read snapshot.
        db.execute(text("UPDATE app_setting SET version = version WHERE id = 1"))


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _admin_record(admin: Admin) -> dict[str, str | None]:
    return {
        "id": admin.id,
        "username": admin.username,
        "created_at": _iso_utc(admin.created_at),
        "password_changed_at": _iso_utc(admin.password_changed_at),
    }


def _new_session(db: Session, admin: Admin, settings: Settings, now: datetime) -> tuple[str, str]:
    session_token = generate_session_token()
    csrf_token = generate_csrf_token()
    db.add(
        AdminSession(
            admin_id=admin.id,
            token_hash=hash_token(session_token),
            csrf_hash=hash_token(csrf_token),
            password_version=admin.password_version,
            expires_at=now + timedelta(seconds=settings.session_ttl_seconds),
            last_used_at=now,
            created_at=now,
        )
    )
    return session_token, csrf_token


def is_initialized(db: Session) -> bool:
    return db.scalar(select(Admin.id).limit(1)) is not None


def _initialization_request_hash(username: str, password: str, settings: Settings) -> str:
    body = json.dumps(
        {"password": password, "username": username},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return request_fingerprint(body, settings.master_key)


def _find_idempotency_record(
    db: Session,
    idempotency_key: str,
    now: datetime,
) -> IdempotencyRecord | None:
    return db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.owner_key == INITIALIZATION_OWNER,
            IdempotencyRecord.method == "POST",
            IdempotencyRecord.path == INITIALIZATION_PATH,
            IdempotencyRecord.idempotency_key == idempotency_key,
            IdempotencyRecord.expires_at > now,
        )
    )


def _replay_initialization(
    db: Session,
    record: IdempotencyRecord,
    request_hash: str,
    settings: Settings,
    now: datetime,
) -> AuthResult:
    if not secrets.compare_digest(record.request_hash, request_hash):
        raise AppError(409, "IDEMPOTENCY_CONFLICT", "幂等键已用于不同请求")
    admin = db.scalar(select(Admin).where(Admin.id == record.response_json.get("id")))
    if admin is None:
        raise AppError(500, "INTERNAL_ERROR", "服务器内部错误")
    if _iso_utc(admin.password_changed_at) != record.response_json.get("password_changed_at"):
        raise AppError(409, "INITIALIZATION_ALREADY_COMPLETED", "系统初始化已经完成")
    session_token, csrf_token = _new_session(db, admin, settings, now)
    db.commit()
    return AuthResult(admin=admin, session_token=session_token, csrf_token=csrf_token)


def initialize_admin(
    db: Session,
    *,
    username: str,
    password: str,
    idempotency_key: str,
    settings: Settings,
) -> AuthResult:
    now = datetime.now(UTC)
    request_hash = _initialization_request_hash(username, password, settings)
    existing_record = _find_idempotency_record(db, idempotency_key, now)
    if existing_record is not None:
        return _replay_initialization(db, existing_record, request_hash, settings, now)

    if is_initialized(db):
        raise AppError(409, "INITIALIZATION_ALREADY_COMPLETED", "系统初始化已经完成")

    # Release the SQLite WAL read snapshot before the deliberately slow password hash.
    db.rollback()
    password_hash = hash_password(password)
    admin = Admin(
        singleton_key=1,
        username=username,
        password_hash=password_hash,
        password_version=1,
    )
    db.add(admin)
    try:
        db.flush()
        session_token, csrf_token = _new_session(db, admin, settings, now)
        db.add(
            IdempotencyRecord(
                owner_key=INITIALIZATION_OWNER,
                method="POST",
                path=INITIALIZATION_PATH,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                response_status=201,
                response_json=_admin_record(admin),
                expires_at=now + IDEMPOTENCY_TTL,
            )
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(409, "INITIALIZATION_ALREADY_COMPLETED", "系统初始化已经完成") from None
    return AuthResult(admin=admin, session_token=session_token, csrf_token=csrf_token)


def _retry_after_seconds(attempt: LoginAttempt, now: datetime) -> int | None:
    if attempt.blocked_until is None:
        return None
    remaining = (_as_utc(attempt.blocked_until) - now).total_seconds()
    return max(1, math.ceil(remaining)) if remaining > 0 else None


def _check_login_limit(db: Session, key_hash: str, now: datetime) -> LoginAttempt | None:
    attempt = db.get(LoginAttempt, key_hash)
    if attempt is None:
        return None
    retry_after = _retry_after_seconds(attempt, now)
    if retry_after is not None:
        raise AppError(
            429,
            "LOGIN_RATE_LIMITED",
            "登录尝试过多, 请稍后重试",
            retry_after_seconds=retry_after,
        )
    return attempt


def _record_login_failure(
    db: Session,
    attempt: LoginAttempt | None,
    key_hash: str,
    settings: Settings,
    now: datetime,
) -> None:
    window = timedelta(seconds=settings.login_window_seconds)
    if attempt is None or now - _as_utc(attempt.window_started_at) >= window:
        if attempt is None:
            attempt = LoginAttempt(
                key_hash=key_hash,
                failed_count=0,
                window_started_at=now,
                updated_at=now,
            )
            db.add(attempt)
        else:
            attempt.failed_count = 0
            attempt.window_started_at = now
            attempt.blocked_until = None
    attempt.failed_count += 1
    attempt.updated_at = now
    if attempt.failed_count >= settings.login_max_failures:
        attempt.blocked_until = now + timedelta(seconds=settings.login_block_seconds)
    db.commit()


def login_admin(
    db: Session,
    *,
    username: str,
    password: str,
    source_ip: str,
    settings: Settings,
) -> AuthResult:
    with _login_attempt_lock:
        key_hash = login_attempt_key(source_ip, username)
        read_now = datetime.now(UTC)
        _check_login_limit(db, key_hash, read_now)
        observed_admin = db.scalar(select(Admin).where(Admin.username == username))
        observed_password_hash = observed_admin.password_hash if observed_admin else None

        # Password verification is intentionally expensive. Do it without a
        # SQLite read snapshot so the worker can keep committing concurrently.
        db.rollback()
        password_valid = verify_password_or_dummy(observed_password_hash, password)
        replacement_hash = (
            hash_password(password)
            if password_valid
            and observed_password_hash is not None
            and password_needs_rehash(observed_password_hash)
            else None
        )

        now = datetime.now(UTC)
        _acquire_sqlite_write_lock(db)
        attempt = _check_login_limit(db, key_hash, now)
        admin = db.scalar(select(Admin).where(Admin.username == username))
        if (
            admin is None
            or admin.password_hash != observed_password_hash
            or not password_valid
        ):
            _record_login_failure(db, attempt, key_hash, settings, now)
            raise AppError(401, "INVALID_CREDENTIALS", "用户名或密码错误")

        if replacement_hash is not None:
            admin.password_hash = replacement_hash
        db.execute(delete(LoginAttempt).where(LoginAttempt.key_hash == key_hash))
        session_token, csrf_token = _new_session(db, admin, settings, now)
        db.commit()
        return AuthResult(admin=admin, session_token=session_token, csrf_token=csrf_token)


def logout_admin(db: Session, admin_session: AdminSession) -> None:
    if admin_session.revoked_at is None:
        admin_session.revoked_at = datetime.now(UTC)
        db.commit()


def change_password(
    db: Session,
    *,
    admin: Admin,
    current_session: AdminSession,
    current_password: str,
    new_password: str,
) -> None:
    with _password_change_lock:
        db.refresh(admin)
        db.refresh(current_session)
        if (
            current_session.revoked_at is not None
            or current_session.password_version != admin.password_version
        ):
            raise AppError(401, "AUTH_REQUIRED", "需要有效的管理员会话")

        admin_id = admin.id
        current_session_id = current_session.id
        observed_password_hash = admin.password_hash
        observed_password_version = admin.password_version
        db.rollback()
        if not verify_password(observed_password_hash, current_password):
            raise AppError(400, "CURRENT_PASSWORD_INCORRECT", "当前密码不正确")

        replacement_hash = hash_password(new_password)
        now = datetime.now(UTC)
        _acquire_sqlite_write_lock(db)
        current_admin = db.get(Admin, admin_id)
        active_session = db.get(AdminSession, current_session_id)
        if (
            current_admin is None
            or active_session is None
            or active_session.revoked_at is not None
            or active_session.password_version != current_admin.password_version
            or current_admin.password_version != observed_password_version
        ):
            db.rollback()
            raise AppError(401, "AUTH_REQUIRED", "需要有效的管理员会话")
        if current_admin.password_hash != observed_password_hash:
            db.rollback()
            raise AppError(400, "CURRENT_PASSWORD_INCORRECT", "当前密码不正确")

        current_admin.password_hash = replacement_hash
        current_admin.password_version += 1
        current_admin.password_changed_at = now
        active_session.password_version = current_admin.password_version
        db.execute(
            update(AdminSession)
            .where(
                AdminSession.admin_id == current_admin.id,
                AdminSession.id != active_session.id,
                AdminSession.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        db.commit()
