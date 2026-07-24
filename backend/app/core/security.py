from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Protocol
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Request, Response

from app.core.config import Settings
from app.core.errors import AppError

SESSION_COOKIE = "yb_session"
CSRF_COOKIE = "yb_csrf"
CSRF_HEADER = "X-CSRF-Token"

_password_hasher = PasswordHasher()
_dummy_password_hash = _password_hasher.hash("not-a-real-admin-password")


class EncryptedCredential(Protocol):
    id: str
    encrypted_token: bytes
    token_nonce: bytes
    key_version: int


class TokenDecryptionError(ValueError):
    """Raised when encrypted credential material cannot be authenticated."""


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def verify_password_or_dummy(password_hash: str | None, password: str) -> bool:
    return verify_password(password_hash or _dummy_password_hash, password) and password_hash is not None


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _password_hasher.check_needs_rehash(password_hash)
    except (VerificationError, InvalidHashError):
        return False


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def request_fingerprint(payload: bytes, master_key: bytes) -> str:
    _validate_master_key(master_key)
    return hmac.new(master_key, payload, hashlib.sha256).hexdigest()


def login_attempt_key(source_ip: str, username: str) -> str:
    material = f"{source_ip}\0{username}".encode()
    return hashlib.sha256(material).hexdigest()


def mask_token(token: str) -> str:
    suffix = token[-4:] if len(token) > 4 else "*" * len(token)
    return f"{'*' * 12}{suffix}"


def _token_aad(credential_id: str) -> bytes:
    return f"yuque-credential:{credential_id}".encode()


def _validate_master_key(master_key: bytes) -> None:
    if len(master_key) != 32:
        raise ValueError("AES-256-GCM requires a 32-byte master key")


def encrypt_token(
    token: str,
    credential_id: str,
    settings: Settings,
) -> tuple[bytes, bytes]:
    master_key = settings.master_key
    _validate_master_key(master_key)
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(master_key).encrypt(
        nonce,
        token.encode("utf-8"),
        _token_aad(credential_id),
    )
    return ciphertext, nonce


def decrypt_token(
    credential: EncryptedCredential,
    settings: Settings,
) -> str:
    master_key = settings.master_key
    _validate_master_key(master_key)
    if credential.key_version != 1 or len(credential.token_nonce) != 12:
        raise TokenDecryptionError("Credential token authentication failed")
    try:
        plaintext = AESGCM(master_key).decrypt(
            credential.token_nonce,
            credential.encrypted_token,
            _token_aad(credential.id),
        )
        return plaintext.decode("utf-8")
    except (InvalidTag, UnicodeDecodeError) as exc:
        raise TokenDecryptionError("Credential token authentication failed") from exc


def _canonical_origin(value: str, *, allow_path: bool = False) -> str | None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or (not allow_path and parsed.path not in {"", "/"})
        or (not allow_path and (parsed.query or parsed.fragment))
    ):
        return None
    hostname = parsed.hostname.rstrip(".").lower()
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if ":" in hostname:
        hostname = f"[{hostname}]"
    default_port = 80 if parsed.scheme == "http" else 443
    port_part = "" if port in {None, default_port} else f":{port}"
    return f"{parsed.scheme}://{hostname}{port_part}"


def require_same_origin(request: Request, settings: Settings) -> None:
    origins = request.headers.getlist("origin")
    referers = request.headers.getlist("referer")
    if len(origins) > 1 or len(referers) > 1:
        raise AppError(403, "CSRF_INVALID", "CSRF 或同源校验失败")

    candidate = origins[0] if origins else referers[0] if referers else None
    candidate_origin = _canonical_origin(candidate, allow_path=not origins) if candidate else None
    allowed = {_canonical_origin(value) for value in settings.origin_allowlist}
    allowed.discard(None)
    if candidate_origin is None or candidate_origin not in allowed:
        raise AppError(403, "CSRF_INVALID", "CSRF 或同源校验失败")


def require_json_content_type(request: Request) -> None:
    content_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    if content_type != "application/json":
        raise AppError(
            422,
            "VALIDATION_ERROR",
            "请求参数不合法",
            field_errors=[{"field": "content_type", "reason": "application_json_required"}],
        )


def set_auth_cookies(
    response: Response,
    *,
    session_token: str,
    csrf_token: str,
    settings: Settings,
) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        max_age=settings.session_ttl_seconds,
        path="/",
        secure=settings.secure_cookies,
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=settings.session_ttl_seconds,
        path="/",
        secure=settings.secure_cookies,
        httponly=False,
        samesite="lax",
    )


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=settings.secure_cookies,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        CSRF_COOKIE,
        path="/",
        secure=settings.secure_cookies,
        httponly=False,
        samesite="lax",
    )
