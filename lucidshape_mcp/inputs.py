"""Snapshot native models and their file references into a private job directory."""
from __future__ import annotations

import hashlib
import json
import locale
from pathlib import Path
import re
from .policy import check_input_path


TOKEN = re.compile(r'//[^\r\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|[A-Za-z_][A-Za-z0-9_]*|[(),]|\S')
NATIVE = {".lue", ".lug", ".lid"}
ASSETS = NATIVE | {".ies", ".ldt", ".lmt", ".cie", ".ray", ".dis", ".dat", ".csv", ".txt", ".stp", ".step", ".igs", ".iges", ".stl", ".obj", ".3dm", ".bmp", ".png", ".jpg", ".jpeg", ".bsdf", ".brdf", ".krs", ".kzu"}
MAX_FILES = 128
MAX_BYTES = 256 * 1024 * 1024


def rewrite_arguments(contents: str, rewrite) -> str:
    """Only rewrite constructor arguments, never object names or comment lists."""
    stack = []
    previous = ""
    pieces = []
    end = 0
    for match in TOKEN.finditer(contents):
        token = match.group()
        if token.startswith(("//", "/*")):
            continue
        if token == "(":
            stack.append([bool(re.fullmatch(r"[A-Z][A-Za-z0-9_]*", previous)), 0])
        elif token == ")" and stack:
            stack.pop()
        elif token == "," and stack:
            stack[-1][1] += 1
        elif token.startswith('"') and stack and stack[-1][0] and stack[-1][1] > 0:
            replacement = rewrite(match)
            if replacement != token:
                pieces.extend((contents[end:match.start()], replacement))
                end = match.end()
        previous = token
    pieces.append(contents[end:])
    return "".join(pieces)


def decode_text(data: bytes) -> tuple[str, str]:
    for encoding in dict.fromkeys(("utf-8-sig", locale.getpreferredencoding(False))):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    raise ValueError("Native input must be text in UTF-8 or the Windows system encoding")


def input_file(value: str, suffixes: set[str]) -> Path:
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        raise ValueError("Input path must be a nonempty local file path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("Input path must be absolute")
    path = check_input_path(path)
    if not path.is_file() or path.suffix.lower() not in suffixes:
        raise ValueError(f"Expected an existing file with extension {sorted(suffixes)}")
    return path


def snapshot(source: Path, directory: Path, target_name: str, *, asset_prefix: str = "") -> list[dict]:
    """Copy existing quoted file references, rewriting only the private copies.

    A missing known asset is an error, not an invitation to run with missing data.
    This packages native data files; it is not a sandbox for arbitrary LucidShell.
    """
    mapped: dict[Path, str] = {}
    manifest: list[dict] = []
    total = 0

    def copy(path: Path, target: str) -> None:
        nonlocal total
        path = check_input_path(path)
        if path in mapped:
            return
        size = path.stat().st_size
        if len(mapped) >= MAX_FILES or total + size > MAX_BYTES:
            raise ValueError("Input package exceeds 128 files or 256 MiB")
        mapped[path] = target
        total += size
        data = path.read_bytes()
        original_hash = hashlib.sha256(data).hexdigest()
        if path.suffix.lower() in NATIVE:
            contents, encoding = decode_text(data)

            def rewrite(match: re.Match) -> str:
                token = match.group()
                try:
                    value = json.loads(token)
                except json.JSONDecodeError:
                    # Do not guess the meaning of non-JSON escapes in file paths.
                    if re.search(r"\.[A-Za-z0-9]+\"$", token):
                        raise ValueError(f"Unsupported string escape in native file: {path.name}")
                    return token
                candidate = Path(value)
                if candidate.suffix.lower() not in ASSETS:
                    return token
                if any(ord(c) < 32 for c in value):
                    raise ValueError(f"Invalid file reference in {path.name}")
                candidate = candidate if candidate.is_absolute() else path.parent / candidate
                if not candidate.is_file():
                    raise ValueError(f"Missing referenced asset: {candidate}")
                candidate = candidate.resolve()
                if candidate not in mapped:
                    copy(candidate, f"{asset_prefix}asset_{len(mapped):04d}{candidate.suffix.lower()}")
                return json.dumps(mapped[candidate])

            contents = rewrite_arguments(contents, rewrite)
            # utf-8-sig is accepted as an input encoding, but do not add a BOM.
            data = contents.encode("utf-8" if encoding == "utf-8-sig" else encoding)
        (directory / target).write_bytes(data)
        manifest.append({"source": str(path), "staged_name": target,
                         "source_bytes": size, "source_sha256": original_hash})

    copy(source, target_name)
    return manifest
