from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

backend_root = Path(SPECPATH)
repository_root = backend_root.parent
frontend_dist = repository_root / "frontend" / "dist"

if not (frontend_dist / "index.html").is_file():
    raise SystemExit("frontend/dist is missing; run the frontend production build first")

a = Analysis(
    [str(backend_root / "app" / "cli.py")],
    pathex=[str(backend_root)],
    binaries=[],
    datas=[
        (str(backend_root / "alembic.ini"), "app"),
        (str(backend_root / "migrations"), "app/migrations"),
        (str(frontend_dist), "app/static"),
        *collect_data_files("weasyprint"),
    ],
    hiddenimports=[
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        *collect_submodules("weasyprint"),
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="yuque-backup",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
