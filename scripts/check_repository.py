"""Check tracked and Git-visible files; optionally export the same reviewed set."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    '.gitignore', '.gitattributes', '.editorconfig',
    'pyproject.toml', 'MANIFEST.in', 'README.md', 'README.en.md',
}
SCRIPTS = {
    'bootstrap.py', 'verify_fresh_install.py', 'verify_harness_configs.py',
    'check_repository.py', 'smoke_mcp.py', 'verify_distribution.py',
    'validate_full_mcp.py', 'validate_optical_extensions.py',
}
FIXTURES = {
    'tests/fixtures/invalid_model.lue', 'tests/fixtures/known_intensity.lid',
}
PRIVATE_SUFFIXES = {
    '.catpart', '.catproduct', '.step', '.stp', '.igs', '.iges', '.stl',
    '.ray', '.lue', '.lug', '.lid', '.ies', '.pdf', '.docx', '.pptx', '.xlsx',
    '.exe', '.dll', '.pdb', '.zip', '.7z', '.pem', '.key', '.pfx', '.p12',
}


def allowed_path(name: str) -> bool:
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or '\\' in name:
        return False
    if name in ROOT_FILES or name in FIXTURES:
        return True
    if len(p.parts) == 1 and p.name.startswith('LICENSE'):
        return p.suffix.lower() not in PRIVATE_SUFFIXES
    if len(p.parts) < 2 or '__pycache__' in p.parts:
        return False
    if any(x.startswith('.') and x != '.github' for x in p.parts):
        return False
    if '.local.' in p.name or p.suffix.lower() in PRIVATE_SUFFIXES:
        return False
    return (
        (p.parts[0] == 'lucidshape_mcp' and p.suffix in {'.py', '.do'})
        or (p.parts[0] == 'tests' and p.suffix == '.py')
        or (p.parts[0] == 'docs' and p.suffix == '.md')
        or name in {'examples/mcp.example.json', 'examples/optical-scene.example.json', 'examples/region-manifest.example.json'}
        or (p.parts[0] == 'scripts' and len(p.parts) == 2 and p.name in SCRIPTS)
        or (p.parts[0] == '.github' and p.suffix in {'.yml', '.yaml'})
    )


def candidates() -> list[str]:
    result = subprocess.run(
        ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
        cwd=ROOT, check=True, capture_output=True,
    )
    return sorted(set(x.decode('utf-8') for x in result.stdout.split(b'\0') if x))


def inspect(names):
    issues = []
    records = []
    # Construct Unix home prefixes so this check does not flag its own pattern.
    home_roots = '|'.join(re.escape('/' + folder + '/') for folder in ('home', 'Users'))
    patterns = [
        ('private key', re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')),
        ('access token', re.compile(
            r'(?:sk-[A-Za-z0-9]{24,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})'
        )),
        ('personal absolute path', re.compile(
            r'(?:[A-Za-z]:[/\\]+Users[/\\]+|' + home_roots + r')[^/\\\s"\']+'
        )),
    ]
    for name in names:
        path = ROOT / name
        if not allowed_path(name):
            issues.append((name, 'outside source allowlist'))
            continue
        if path.is_symlink() or not path.is_file():
            issues.append((name, 'not a regular file'))
            continue
        if not path.resolve().is_relative_to(ROOT.resolve()):
            issues.append((name, 'outside workspace'))
            continue
        if path.stat().st_size > 2 * 1024 * 1024:
            issues.append((name, 'over 2 MiB'))
            continue
        raw = path.read_bytes()
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            issues.append((name, 'not UTF-8 source text'))
            continue
        for label, pattern in patterns:
            if pattern.search(text):
                issues.append((name, label))
        records.append({
            'path': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
        })
    return records, issues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--export', type=Path, help='Optional source ZIP, e.g. local-data/dist/lucidshape-mcp-source.zip',
    )
    args = parser.parse_args()
    records, issues = inspect(candidates())
    if issues:
        print(json.dumps({'status': 'failed', 'issues': issues}, indent=2))
        raise SystemExit(1)
    output = {
        'status': 'passed', 'files': len(records), 'bytes': sum(r['bytes'] for r in records),
        'scope': 'tracked and nonignored files; pattern screening is not a complete secret audit',
    }
    if args.export:
        target = args.export.resolve()
        manifest = target.with_suffix('.manifest.json')
        if target.exists() or manifest.exists():
            raise SystemExit('Refusing to overwrite existing source export or manifest')
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as z:
            for record in records:
                z.write(ROOT / record['path'], 'lucidshape-mcp/' + record['path'])
        manifest.write_text(json.dumps({'files': records}, indent=2), encoding='utf-8')
        output.update(archive=str(target), manifest=str(manifest))
    print(json.dumps(output, indent=2))


if __name__ == '__main__':
    main()
