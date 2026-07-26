from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from typing import NoReturn
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

VERSION = "1.2.0"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yuque-backup",
        description="Yuque Backup unified API, worker, and migration executable",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("api", help="serve the embedded frontend and REST API")
    commands.add_parser("worker", help="run the single persistent backup worker")
    commands.add_parser("migrate", help="upgrade the database schema to the current head")

    health = commands.add_parser("healthcheck", help="check an API or worker process")
    health.add_argument("target", choices=("api", "worker"), nargs="?", default="api")
    health.add_argument("--url", default="http://127.0.0.1:8000/health/ready")
    return parser


def _api_healthcheck(url: str) -> int:
    try:
        with urlopen(url, timeout=3) as response:
            return 0 if response.status == 200 else 1
    except (HTTPError, URLError, TimeoutError, OSError):
        return 1


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _worker_healthcheck() -> int:
    from app.core.config import get_settings
    from app.core.database import SessionLocal
    from app.core.models import WorkerHeartbeat

    settings = get_settings()
    try:
        with SessionLocal() as session:
            heartbeat = session.get(WorkerHeartbeat, 1)
    except Exception:
        return 1
    if heartbeat is None:
        return 1
    online_window = timedelta(seconds=max(settings.worker_heartbeat_seconds * 3, 30))
    return int(_as_utc(heartbeat.last_heartbeat_at) < datetime.now(UTC) - online_window)


def _unknown_command(command: str) -> NoReturn:
    raise RuntimeError(f"unsupported command: {command}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "api":
        from app.main import run

        run()
        return 0
    if args.command == "worker":
        from app.worker.main import run

        run()
        return 0
    if args.command == "migrate":
        from app.core.migrations import upgrade_database

        upgrade_database()
        return 0
    if args.command == "healthcheck":
        return _api_healthcheck(args.url) if args.target == "api" else _worker_healthcheck()
    _unknown_command(args.command)


if __name__ == "__main__":
    sys.exit(main())
