from __future__ import annotations

import base64
import binascii
from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "development"
    app_master_key: str = Field(min_length=1)
    data_root: Path = Path("./data")
    database_url: str | None = None
    tz: str = "Asia/Shanghai"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    secure_cookies: bool = False
    trusted_origins: str = "http://127.0.0.1:8000,http://localhost:8000"
    session_ttl_seconds: int = Field(default=86400, ge=300)
    login_window_seconds: int = Field(default=300, ge=60)
    login_max_failures: int = Field(default=5, ge=1)
    login_block_seconds: int = Field(default=300, ge=60)
    queue_poll_seconds: float = Field(default=0.5, gt=0, le=60)
    queue_lease_seconds: int = Field(default=300, ge=30)
    worker_heartbeat_seconds: int = Field(default=10, ge=1)
    yuque_request_interval_seconds: float = Field(default=1.0, ge=0)
    http_connect_timeout_seconds: float = Field(default=10.0, gt=0)
    http_read_timeout_seconds: float = Field(default=60.0, gt=0)
    http_write_timeout_seconds: float = Field(default=60.0, gt=0)
    resource_redirect_limit: int = Field(default=3, ge=0, le=10)
    resource_download_concurrency: int = Field(default=4, ge=1, le=32)

    @model_validator(mode="after")
    def finalize_paths(self) -> Self:
        self.data_root = self.data_root.expanduser().resolve()
        if self.database_url is None:
            db_path = (self.data_root / "db" / "yuque-backup.sqlite3").as_posix()
            self.database_url = f"sqlite:///{db_path}"
        if self.app_env == "production" and not self.secure_cookies:
            raise ValueError("SECURE_COOKIES must be true in production")
        return self

    @property
    def master_key(self) -> bytes:
        value = self.app_master_key.strip()
        try:
            if len(value) == 64:
                decoded = bytes.fromhex(value)
            else:
                decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        except (ValueError, binascii.Error) as exc:
            raise ValueError("APP_MASTER_KEY must be 32 bytes encoded as hex or URL-safe base64") from exc
        if len(decoded) != 32:
            raise ValueError("APP_MASTER_KEY must decode to exactly 32 bytes")
        return decoded

    @property
    def origin_allowlist(self) -> frozenset[str]:
        return frozenset(item.strip().rstrip("/") for item in self.trusted_origins.split(",") if item.strip())

    @property
    def db_path(self) -> Path | None:
        prefix = "sqlite:///"
        if not self.database_url or not self.database_url.startswith(prefix):
            return None
        return Path(self.database_url.removeprefix(prefix)).resolve()

    @property
    def content_root(self) -> Path:
        return self.data_root / "content"

    @property
    def exports_root(self) -> Path:
        return self.data_root / "exports"

    def ensure_database_directory(self) -> None:
        if self.db_path is not None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def ensure_content_directories(self) -> None:
        self.content_root.mkdir(parents=True, exist_ok=True)
        (self.content_root / ".tmp").mkdir(parents=True, exist_ok=True)

    def ensure_export_directories(self) -> None:
        self.exports_root.mkdir(parents=True, exist_ok=True)
        (self.exports_root / ".tmp").mkdir(parents=True, exist_ok=True)

    def ensure_directories(self) -> None:
        self.ensure_database_directory()
        self.ensure_content_directories()


@lru_cache
def get_settings() -> Settings:
    return Settings()
