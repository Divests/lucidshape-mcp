"""Install a downloaded checkout in its own venv and generate client snippets."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv

ROOT = Path(__file__).resolve().parents[1]
# Stdlib-only metadata is available before dependencies have been installed.
sys.path.insert(0, str(ROOT))
from lucidshape_mcp.harnesses import HARNESSES, select_harnesses
from lucidshape_mcp.policy import add_policy_arguments, apply_policy_arguments, Policy


def main() -> int:
    # Redirected Windows output may use cp1252 for Unicode installation paths.
    # Keep the caller's encoding and escape only unrepresentable characters.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(errors='backslashreplace')
    parser = argparse.ArgumentParser(description=__doc__)
    add_policy_arguments(parser)
    parser.add_argument('--lucidshape-exe', default=os.environ.get('LUCIDSHAPE_EXE', ''))
    parser.add_argument('--with-geometry', action='store_true', help='Also install the optional OCCT wheels')
    parser.add_argument('--minimal', action='store_true', help='Install only MCP transport dependencies')
    parser.add_argument('--venv', type=Path, default=ROOT / 'local-data' / 'venv')
    parser.add_argument('--work-dir', type=Path, default=ROOT / 'local-data' / 'work')
    parser.add_argument('--config-dir', type=Path, default=ROOT / 'local-data' / 'config')
    parser.add_argument('--harness', action='append', choices=['all', *HARNESSES],
                        help='Client profile; repeat to select several (default: all)')
    parser.add_argument('--force-config', action='store_true', help='Replace selected generated local snippets')
    args = parser.parse_args()
    apply_policy_arguments(args)
    try:
        selected = select_harnesses(args.harness or ['all'])
    except ValueError as error:
        parser.error(str(error))
    if sys.version_info < (3, 11):
        parser.error('Python 3.11+ is required; Python 3.12 64-bit is the recommended tested version')
    engine = Path(os.path.expandvars(args.lucidshape_exe)).expanduser().resolve() if args.lucidshape_exe else None
    if engine and (not engine.is_file() or engine.name.lower() != 'lush.exe'):
        parser.error('--lucidshape-exe must point to an existing lush.exe')
    environment, work, config = (path.expanduser().resolve() for path in (args.venv, args.work_dir, args.config_dir))
    policy = Policy.from_env(work)
    if not args.force_config and any((config / HARNESSES[name].filename).exists() for name in selected):
        parser.error('Generated configuration exists; use another --config-dir or --force-config')
    if environment.exists() and not (environment / 'pyvenv.cfg').is_file() and any(environment.iterdir()):
        parser.error('--venv points to a nonempty directory that is not a virtual environment')
    print('Creating/reusing virtual environment:', environment, flush=True)
    venv.EnvBuilder(with_pip=True, system_site_packages=False).create(environment)
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    env = dict(os.environ)
    env.update(policy.environment())
    for key in ('PYTHONPATH', 'PYTHONHOME', 'LUCIDSHAPE_KERNEL_PATH'):
        env.pop(key, None)
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8', LUCIDSHAPE_EXE=str(engine) if engine else '',
               LUCIDSHAPE_WORK_DIR=str(work), PIP_DISABLE_PIP_VERSION_CHECK='1')
    env.setdefault('PIP_DEFAULT_TIMEOUT', '60')
    env.setdefault('PIP_RETRIES', '5')
    extras = [] if args.minimal else ['analysis'] + (['catia'] if sys.platform == 'win32' else [])
    if args.with_geometry:
        extras.append('geometry')
    target = str(ROOT) + ('[' + ','.join(extras) + ']' if extras else '')
    subprocess.run([str(python), '-m', 'pip', 'install', '--no-input', target], env=env, check=True)
    subprocess.run([str(python), '-m', 'pip', 'check'], env=env, check=True)
    with tempfile.TemporaryDirectory(prefix='lucidshape-install-check-') as outside:
        base = [str(python), '-B', '-m', 'lucidshape_mcp']
        command = base + ['configure', '--lucidshape-exe', str(engine) if engine else '',
                          '--work-dir', str(work), '--output-dir', str(config)]
        for name in selected:
            command += ['--harness', name]
        if args.force_config:
            command.append('--force')
        subprocess.run(command, cwd=outside, env=env, check=True)
        command = base + ['doctor']
        for extra in extras:
            command += ['--require', extra]
        if engine:
            command += ['--require', 'engine']
        result = subprocess.run(command, cwd=outside, env=env, text=True, encoding='utf-8',
                                errors='replace', capture_output=True)
        (config / 'doctor.json').write_text(result.stdout, encoding='utf-8')
        if result.returncode:
            print(result.stdout, file=sys.stderr)
            print(result.stderr, file=sys.stderr)
            return result.returncode
        report = json.loads(result.stdout)
        for warning in report['warnings']:
            print('Note:', warning, flush=True)
        subprocess.run(base + ['check'], cwd=outside, env=env, check=True)
        frozen = subprocess.run([str(python), '-m', 'pip', 'freeze'], cwd=outside, env=env,
                                check=True, capture_output=True, text=True, encoding='utf-8')
        (config / 'installed-requirements.txt').write_text(frozen.stdout, encoding='utf-8')
    print('Setup complete. Add one generated snippet to your MCP client:', config, flush=True)
    print('No client settings or CAD sessions were changed.', flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, subprocess.CalledProcessError) as error:
        print('Setup failed:', error, file=sys.stderr)
        raise SystemExit(1)
