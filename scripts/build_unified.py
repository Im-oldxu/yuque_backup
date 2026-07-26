from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def run(command: list[str], *, cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def tool(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f"required build tool is not available: {name}")
    return executable


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Yuque Backup unified executable")
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="reuse the existing frontend node_modules directory",
    )
    args = parser.parse_args()

    repository_root = Path(__file__).resolve().parents[1]
    frontend = repository_root / "frontend"
    backend = repository_root / "backend"
    corepack = tool("corepack")
    uv = tool("uv")
    if not args.skip_install:
        run([corepack, "pnpm", "install", "--frozen-lockfile"], cwd=frontend)
    run([corepack, "pnpm", "build"], cwd=frontend)
    run(
        [
            uv,
            "run",
            "--isolated",
            "--frozen",
            "--group",
            "build",
            "pyinstaller",
            "--clean",
            "--noconfirm",
            "yuque_backup.spec",
        ],
        cwd=backend,
    )


if __name__ == "__main__":
    main()
