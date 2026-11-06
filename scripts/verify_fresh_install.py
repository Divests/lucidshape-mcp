"""Test the download workflow from a clean source copy and an isolated venv."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib

from check_repository import ROOT, candidates, inspect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-geometry', action='store_true')
    args = parser.parse_args()
    records, issues = inspect(candidates())
    if issues:
        raise RuntimeError('Source check failed: ' + repr(issues))
    env = dict(os.environ)
    for key in ('PYTHONPATH', 'PYTHONHOME', 'LUCIDSHAPE_EXE', 'LUCIDSHAPE_KERNEL_PATH', 'LUCIDSHAPE_WORK_DIR'):
        env.pop(key, None)
    for key in list(env):
        if key.startswith('LUCIDSHAPE_'):
            env.pop(key)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    with tempfile.TemporaryDirectory(prefix='lucidshape-download-') as folder:
        clean = Path(folder) / '下载 with spaces'
        clean.mkdir()
        for record in records:
            target = clean / record['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / record['path'], target)
        command = [sys.executable, '-B', str(clean / 'scripts/bootstrap.py')]
        if args.with_geometry:
            command.append('--with-geometry')
        subprocess.run(command, cwd=folder, env=env, check=True)
        config_dir = clean / 'local-data/config'
        generated = json.loads((config_dir / 'mcp.local.json').read_text(encoding='utf-8'))
        codex = tomllib.loads((config_dir / 'codex.local.toml').read_text(encoding='utf-8'))
        spec = generated['mcpServers']['lucidshape']
        if codex['mcp_servers']['lucidshape'] != spec:
            raise RuntimeError('JSON and TOML launch settings differ')
        python = Path(spec['command'])
        environment = clean / 'local-data/venv'
        report = json.loads((config_dir / 'doctor.json').read_text(encoding='utf-8'))
        if not Path(report['package_directory']).is_relative_to(environment):
            raise RuntimeError('Checks accidentally imported source or global site-packages')
        if 'include-system-site-packages = false' not in (environment / 'pyvenv.cfg').read_text().lower():
            raise RuntimeError('Virtual environment inherits system dependencies')
        subprocess.run([str(python), '-B', str(clean / 'scripts/verify_harness_configs.py'),
                        '--config-dir', str(config_dir)], cwd=folder, env=env, check=True)
        subprocess.run([str(python), '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-q'],
                       cwd=clean, env=env, check=True)
        print('FRESH_INSTALL_VERIFIED: source ZIP layout, spaces/Unicode paths, isolated dependencies, '
              'installed MCP startup, all harness configs, and unit tests', flush=True)


if __name__ == '__main__':
    main()
