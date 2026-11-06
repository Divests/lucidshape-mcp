"""Command-line setup helpers; no arguments preserves the stdio server entry."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from .harnesses import HARNESSES, catalog, select_harnesses
from .policy import add_policy_arguments, apply_policy_arguments


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='lucidshape-mcp')
    sub = parser.add_subparsers(dest='command')
    serve = sub.add_parser('serve', help='Start the stdio MCP server')
    diagnose = sub.add_parser('doctor', help='Check imports, paths and engine profile without launching CAD')
    configure = sub.add_parser('configure', help='Generate JSON and Codex TOML snippets')
    sub.add_parser('check', help='Check installed stdio startup without a native engine')
    sub.add_parser('harnesses', help='List documented client configuration adapters and destinations')
    for command in (serve, diagnose, configure):
        add_policy_arguments(command)
        command.add_argument('--lucidshape-exe', help='Full path to the installed lush.exe')
        command.add_argument('--work-dir', help='Writable task directory')
        command.add_argument('--kernel-path', help='Optional external directory containing OCP')
    diagnose.add_argument('--require', action='append', default=[],
                          choices=['engine', 'analysis', 'catia', 'geometry', 'extensions'])
    configure.add_argument('--output-dir', type=Path, required=True)
    configure.add_argument('--force', action='store_true', help='Replace previously generated snippets')
    configure.add_argument('--harness', action='append', choices=['all', *HARNESSES],
                           help='Client profile; repeat to select several, or all (default: generic + codex)')
    args = parser.parse_args(argv)
    apply_policy_arguments(args)
    for option, key in (('lucidshape_exe', 'LUCIDSHAPE_EXE'), ('work_dir', 'LUCIDSHAPE_WORK_DIR'),
                        ('kernel_path', 'LUCIDSHAPE_KERNEL_PATH')):
        value = getattr(args, option, None)
        if value is not None:
            os.environ[key] = value
    if args.command in (None, 'serve'):
        from .server import main as serve_stdio
        serve_stdio()
        return 0
    try:
        from . import onboarding
        if args.command == 'configure':
            config = onboarding.client_config(args.lucidshape_exe, args.work_dir, args.kernel_path)
            selected = select_harnesses(args.harness)
            paths = onboarding.write_client_configs(config, args.output_dir, force=args.force, harnesses=selected)
            result = {'status': 'passed', 'files': [str(path) for path in paths],
                      'harnesses': list(selected), 'client_settings_changed': False}
        elif args.command == 'doctor':
            result = onboarding.doctor(tuple(args.require))
        elif args.command == 'harnesses':
            result = {'status': 'passed', 'harnesses': catalog(),
                      'scope': 'Local Windows stdio; config adapters are not end-to-end client certification'}
        else:
            result = asyncio.run(onboarding.smoke_check())
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0 if result['status'] == 'passed' else 1
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        print(f'lucidshape-mcp: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
