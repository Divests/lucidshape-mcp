"""Installation diagnostics and client configuration; never starts CAD software."""
from __future__ import annotations

import asyncio
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

from .config import executable_path, expand_path, geometry_runtime_path, work_directory
from .extensions import VERIFIED_ENGINE
from .harnesses import codex_toml, config_outputs
from .policy import Policy


def package_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def client_config(executable: str | None = None, work_dir: str | None = None,
                  kernel_path: str | None = None) -> dict:
    engine = executable_path(executable)
    if engine and (not engine.is_file() or engine.name.lower() != 'lush.exe'):
        raise ValueError('--lucidshape-exe must point to an existing lush.exe')
    work = expand_path(work_dir) if work_dir else work_directory()
    if work.exists() and not work.is_dir():
        raise ValueError('--work-dir must be a directory')
    env = {'LUCIDSHAPE_EXE': str(engine) if engine else '',
           'LUCIDSHAPE_WORK_DIR': str(work)}
    env.update(Policy.from_env(work).environment())
    if kernel_path:
        kernel = expand_path(kernel_path)
        if not (kernel / 'OCP').is_dir():
            raise ValueError('--kernel-path must contain an OCP package')
        env['LUCIDSHAPE_KERNEL_PATH'] = str(kernel)
    return {'mcpServers': {'lucidshape': {
        # Resolving bin/python on POSIX would escape its virtual environment.
        'command': os.path.abspath(sys.executable),
        'args': ['-m', 'lucidshape_mcp'], 'env': env,
    }}}


def write_client_configs(config: dict, output_dir: Path, *, force: bool = False,
                         harnesses=None) -> list[Path]:
    output_dir = expand_path(output_dir)
    outputs = {output_dir / name: content for name, content in config_outputs(config, harnesses).items()}
    if not force and any(path.exists() for path in outputs):
        raise FileExistsError('Generated configuration exists; choose another directory or use --force')
    output_dir.mkdir(parents=True, exist_ok=True)
    for path, content in outputs.items():
        with path.open('w' if force else 'x', encoding='utf-8', newline='\n') as stream:
            stream.write(content)
    return list(outputs)


def probe_dependency(name: str) -> dict:
    specs = {
        'mcp': ('mcp', 'from mcp.server.fastmcp import FastMCP; from mcp import ClientSession'),
        'analysis': ('numpy', 'import numpy'),
        'catia': ('pywin32', 'import win32com.client'),
        'geometry': ('cadquery-ocp-novtk',
                     'from lucidshape_mcp.config import configure_geometry_runtime; '
                     'configure_geometry_runtime(); from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox'),
    }
    distribution, code = specs[name]
    try:
        run = subprocess.run(
            [sys.executable, '-B', '-c', code], capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=15,
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        return {'distribution': distribution, 'version': package_version(distribution),
                'import_ok': run.returncode == 0,
                'error': run.stderr.strip()[-1200:] if run.returncode else None}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'distribution': distribution, 'version': package_version(distribution),
                'import_ok': False, 'error': str(error)}


def doctor(required: tuple[str, ...] = ()) -> dict:
    dependencies = {name: probe_dependency(name) for name in ('mcp', 'analysis', 'catia', 'geometry')}
    engine = executable_path()
    work = work_directory()
    policy = Policy.from_env(work)
    errors, warnings = [], []
    writable = False
    try:
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=work) as stream:
            stream.write(b'lucidshape-mcp write probe')
        writable = True
    except OSError as error:
        errors.append('Work directory is not writable: ' + str(error))
    templates = Path(__file__).with_name('templates')
    templates_ok = (templates / 'healthcheck.do').is_file() and (templates / 'demo_simulation.do').is_file()
    if not templates_ok:
        errors.append('Native templates are missing; reinstall the package')
    if not dependencies['mcp']['import_ok']:
        errors.append('MCP SDK cannot be imported; reinstall lucidshape-mcp')
    engine_exists = bool(engine and engine.is_file() and engine.name.lower() == 'lush.exe')
    engine_hash = None
    if engine and not engine_exists:
        errors.append('LUCIDSHAPE_EXE must point to an existing lush.exe')
    elif engine_exists:
        try:
            with engine.open('rb') as stream:
                engine_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
        except OSError as error:
            errors.append('Cannot read the configured engine: ' + str(error))
    else:
        warnings.append('Engine is not configured; tool discovery works but native batch jobs cannot run')
    native_platform = sys.platform == 'win32'
    if not native_platform:
        warnings.append('CATIA COM and LucidShape native execution require Windows')
    profile_matches = engine_hash == VERIFIED_ENGINE
    if engine_exists and not profile_matches:
        warnings.append('Optical extensions will refuse this unvalidated engine profile; basic native functions also need verification')
    for name in ('analysis', 'catia', 'geometry'):
        if not dependencies[name]['import_ok']:
            message = f'Optional dependency {name} unavailable; run pip install ".[{name}]" in the downloaded repository'
            (errors if name in required else warnings).append(message)
    if 'engine' in required and not (native_platform and engine_exists and engine_hash):
        errors.append('Required engine prerequisites are not met')
    if 'catia' in required and not native_platform:
        errors.append('CATIA requires Windows')
    if 'extensions' in required and not (native_platform and profile_matches):
        errors.append('Required optical extensions need the validated engine profile')
    runtime = geometry_runtime_path()
    return {
        'status': 'failed' if errors else 'passed', 'version': package_version('lucidshape-mcp'),
        'policy': policy.describe(),
        'package_directory': str(Path(__file__).parent.resolve()),
        'python': sys.version.split()[0], 'python_bits': struct.calcsize('P') * 8,
        'platform': sys.platform, 'work_directory': str(work), 'work_directory_writable': writable,
        'templates_present': templates_ok, 'dependencies': dependencies,
        'geometry_runtime': str(runtime) if runtime else None,
        'engine': {'path': str(engine) if engine else None, 'exists': engine_exists,
                   'sha256': engine_hash, 'optical_extensions_profile_matches': profile_matches},
        'license_status': 'not_checked', 'native_execution_verified': False,
        'application_started': False, 'application_connected': False,
        'errors': errors, 'warnings': warnings,
    }


async def smoke_check() -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    with tempfile.TemporaryDirectory(prefix='lucidshape-mcp-check-') as folder:
        env = {**os.environ, 'LUCIDSHAPE_EXE': '', 'LUCIDSHAPE_WORK_DIR': folder,
               'PYTHONDONTWRITEBYTECODE': '1'}
        params = StdioServerParameters(command=sys.executable, args=['-m', 'lucidshape_mcp'],
                                       env=env, cwd=folder)
        async with asyncio.timeout(30):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    names = sorted(tool.name for tool in (await client.list_tools()).tools)
                    if Policy.from_env(Path(folder)).enabled_tools != set(names):
                        raise RuntimeError('Expected MCP tools are missing')
                    result = await client.call_tool('get_capabilities', {})
                    if result.isError or json.loads(result.content[0].text)['executable_exists']:
                        raise RuntimeError('No-engine capability check failed')
                    if not (Path(__file__).with_name('templates') / 'healthcheck.do').is_file():
                        raise RuntimeError('Installed native templates are missing')
        return {'status': 'passed', 'tool_count': len(names), 'tools': names,
                'application_started': False, 'application_connected': False, 'templates_present': True}
