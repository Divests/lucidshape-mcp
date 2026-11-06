"""User-writable state and optional geometry runtime discovery."""
import importlib.util
import os
from pathlib import Path
import sys


def expand_path(value: str | Path) -> Path:
    return Path(os.path.expandvars(str(value))).expanduser().resolve()


def executable_path(value: str | None = None) -> Path | None:
    configured = os.environ.get('LUCIDSHAPE_EXE', '') if value is None else value
    return expand_path(configured) if configured else None


def work_directory() -> Path:
    """Keep installed wheels from writing jobs into site-packages."""
    configured = os.environ.get('LUCIDSHAPE_WORK_DIR')
    if configured:
        return expand_path(configured)
    return Path.home() / '.lucidshape-mcp' / 'work'


def geometry_runtime_path() -> Path | None:
    configured = os.environ.get('LUCIDSHAPE_KERNEL_PATH')
    if configured:
        return expand_path(configured)
    # Prefer local-data while preserving both older checkout layouts.
    root = Path(__file__).resolve().parents[1]
    for relative in (
        'local-data/work/cad-kernel-runtime',
        'workspace/work/cad-kernel-runtime',
        'work/cad-kernel-runtime',
    ):
        runtime = root / relative
        if (runtime / 'OCP').is_dir():
            return runtime.resolve()
    return None


def geometry_available() -> bool:
    path = geometry_runtime_path()
    if path is not None:
        return (path / 'OCP').is_dir()
    return importlib.util.find_spec('OCP') is not None


def configure_geometry_runtime() -> None:
    path = geometry_runtime_path()
    if path is not None:
        if not (path / 'OCP').is_dir():
            raise RuntimeError('LUCIDSHAPE_KERNEL_PATH must contain an OCP package')
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
