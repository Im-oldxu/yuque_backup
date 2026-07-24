from __future__ import annotations

from pathlib import Path

from alembic import command, config, script
from alembic.runtime import migration
from sqlalchemy import Engine

from app.core.config import get_settings


def _migration_resources() -> tuple[Path, Path]:
    source_root = Path(__file__).resolve().parents[2]
    package_root = Path(__file__).resolve().parents[1]
    for root in (source_root, package_root):
        config_path = root / "alembic.ini"
        script_path = root / "migrations"
        if config_path.is_file() and script_path.is_dir():
            return config_path, script_path
    raise RuntimeError("Alembic configuration and migration scripts are not installed")


def alembic_config() -> config.Config:
    config_path, script_path = _migration_resources()
    result = config.Config(str(config_path))
    result.set_main_option("script_location", str(script_path))
    return result


def upgrade_database() -> None:
    settings = get_settings()
    settings.ensure_database_directory()
    cfg = alembic_config()
    cfg.set_main_option("sqlalchemy.url", settings.database_url or "")
    command.upgrade(cfg, "head")


def database_is_at_head(engine: Engine) -> bool:
    cfg = alembic_config()
    directory = script.ScriptDirectory.from_config(cfg)
    with engine.begin() as connection:
        context = migration.MigrationContext.configure(connection)
        return set(context.get_current_heads()) == set(directory.get_heads())
