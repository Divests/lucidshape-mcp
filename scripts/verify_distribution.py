"""Inspect wheel/sdist content and smoke-test the unpacked wheel away from source."""
import argparse
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tempfile
import zipfile

from check_repository import allowed_path

ROOT = Path(__file__).resolve().parents[1]


def archive_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
        raise RuntimeError('Unsafe archive path')
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', type=Path, default=ROOT / 'local-data' / 'dist' / 'packages')
    args = parser.parse_args()
    wheels = list(args.dist.glob('*.whl'))
    sdists = list(args.dist.glob('*.tar.gz'))
    if len(wheels) != 1 or len(sdists) != 1:
        raise SystemExit('Use a directory with exactly one wheel and one sdist')
    expected = {
        p.relative_to(ROOT).as_posix()
        for p in (ROOT / 'lucidshape_mcp').rglob('*') if p.suffix in {'.py', '.do'}
    }
    with zipfile.ZipFile(wheels[0]) as z:
        names = z.namelist()
        actual = {n for n in names if n.startswith('lucidshape_mcp/')}
        if actual != expected:
            raise RuntimeError(f'Wheel package files differ: {actual ^ expected}')
        for name in names:
            path = archive_path(name)
            if not name.startswith('lucidshape_mcp/') and not path.parts[0].endswith('.dist-info'):
                raise RuntimeError('Unexpected wheel payload')
        with tempfile.TemporaryDirectory(prefix='lucidshape-wheel-') as folder:
            z.extractall(folder)
            env = {**os.environ, 'PYTHONPATH': folder, 'LUCIDSHAPE_EXE': ''}
            # Run the MCP script using only this wheel plus already installed dependencies.
            subprocess.run(
                [sys.executable, str(ROOT / 'scripts/smoke_mcp.py')],
                cwd=folder, env=env, check=True, timeout=45,
            )
    with tarfile.open(sdists[0]) as archive:
        files = []
        roots = set()
        for member in archive.getmembers():
            path = archive_path(member.name)
            roots.add(path.parts[0])
            if member.isdir():
                continue
            if not member.isfile():
                raise RuntimeError('Nonregular sdist member')
            relative = PurePosixPath(*path.parts[1:]).as_posix()
            generated = (
                relative in {'PKG-INFO', 'setup.cfg'}
                or relative.startswith('lucidshape_mcp.egg-info/')
            )
            if not generated and not allowed_path(relative):
                raise RuntimeError('Unexpected sdist payload: ' + relative)
            files.append(relative)
        if len(roots) != 1:
            raise RuntimeError('Sdist must contain one root directory')
        if not expected <= set(files):
            raise RuntimeError('Sdist is missing package files')
    print('DISTRIBUTION_VERIFIED: wheel payload, sdist payload, and isolated stdio startup')


if __name__ == '__main__':
    main()
