from __future__ import annotations

import base64
from pathlib import Path

from alembic import command
from pytest import MonkeyPatch
from sqlalchemy import create_engine, inspect, text

from app.core.config import get_settings
from app.core.migrations import alembic_config, database_is_at_head, upgrade_database
from app.core.models import Base


def test_initial_migration_replays_and_matches_metadata(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    database_path = tmp_path / "db" / "migration.sqlite3"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("APP_MASTER_KEY", base64.urlsafe_b64encode(b"m" * 32).decode())
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()

    config = alembic_config()
    upgrade_database()
    engine = create_engine(database_url)
    try:
        assert database_is_at_head(engine) is True
        assert set(inspect(engine).get_table_names()) == {*Base.metadata.tables, "alembic_version"}
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT cron, timezone, max_asset_size_bytes FROM app_setting WHERE id = 1")
            ).one() == ("0 2 * * *", "Asia/Shanghai", 524_288_000)
            assert (
                connection.execute(
                    text("SELECT retention_days FROM retention_policy WHERE id = 1")
                ).scalar_one()
                == 15
            )

        command.check(config)
        command.downgrade(config, "base")
        assert database_is_at_head(engine) is False
        command.upgrade(config, "head")
        assert database_is_at_head(engine) is True
    finally:
        engine.dispose()
        get_settings.cache_clear()
