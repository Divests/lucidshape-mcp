"""Static dialog declarations in the configured Studio installation."""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import os
import re


_ROOTS = ("tasks", "include", "classes")
_SUFFIXES = {".do", ".hdo", ".luf", ".lus"}
_MAX_FILE = 4 * 1024 * 1024
_STRING = r'"(?:\\.|[^"\\])*"'
_SKIP_COMMENTS = re.compile(rf"{_STRING}|'(?:\\.|[^'\\])*'|//[^\n]*|/\*[\s\S]*?(?:\*/|\Z)")
_DIRECTIVE = re.compile(r"^[ \t]*#[^\n]*(?:\n(?<=\\\n)[^\n]*)*", re.M)
_DEFINE = re.compile(r"^[ \t]*#[ \t]*define\s+(CBD_\w+)(?:\(([^)\n]*)\))?[ \t]+(.+)", re.M)
_CONSTRUCT = re.compile(
    rf"{_STRING}|'(?:\\.|[^'\\])*'|\b(?P<name>CBD_[A-Z0-9_]+|[A-Z][\w]*Control|"
    r"DialogBox|MenuItem|gfc_setupDirTree)\b"
)
_CALL_OPEN = re.compile(r"\s*\(")
_FIRST_LITERAL = re.compile(rf"\s*\(\s*({_STRING})\s*[,)]")
_NATIVE_CALL = re.compile(r"^(?:new\s+)?(\w+Control)\s*\(")
_DIALOG_CONTEXT = re.compile(r"\b(?:DialogBox|CBD_[A-Z0-9_]+|\w+EditControl|gfc_setupDirTree)\b")
_CONTROL_KINDS = {
    "ActionControl": "button", "ActionBitmapControl": "button", "HelpActionControl": "button",
    "BoolRadioCheckControl": "radio", "BoolCheckControl": "checkbox",
    "MenuItem": "choice", "DialogBox": "dialog", "gfc_setupDirTree": "runtime_tree",
    "ComboBoxControl": "combo", "ListBoxControl": "list", "TreeControl": "tree",
    "FileControl": "file_picker", "StaticControl": "label", "StaticBitmapControl": "image",
    "GroupControl": "group", "TabRowControl": "tabs", "NoteTabRowControl": "tabs",
}
_SCOPE = ("Static script declarations in tasks, include and classes. Locations can include "
          "alternative editions, conditional branches or shared components. Dynamic list contents, "
          "runtime availability and native-only dialogs require separate evidence. "
          "Catalog entries are not executable commands.")


def _blank(match):
    return re.sub(r"[^\n]", " ", match.group())


def _strip_comments(text):
    return _SKIP_COMMENTS.sub(lambda m: _blank(m) if m.group().startswith("/") else m.group(), text)


def _label(literal):
    return literal[1:-1].replace(r'\"', '"').replace(r"\\", "\\").strip()[:256]


def _kind(name):
    if name.endswith("EditControl"):
        return "field"
    return _CONTROL_KINDS.get(name, "control")


@dataclass(frozen=True)
class _Macro:
    takes_arguments: bool
    kind: str
    caption: str | None
    first_is_label: bool


def _macro_specs(header):
    definitions = {}
    for match in _DEFINE.finditer(_strip_comments(header).replace("\\\n", " ")):
        name, arguments, body = match.groups()
        if not re.search(r"\b(?:\w+Control|CBD_\w+)\b", body):
            continue
        native = _NATIVE_CALL.match(body)
        kind = _kind(native[1]) if native else "composite"
        literal = _FIRST_LITERAL.match(body, native.end(1)) if native else None
        first = (arguments or "").split(",")[0].strip()
        spec = _Macro(arguments is not None, kind, _label(literal[1]) if literal else None,
                      first in {"TEXT", "LABEL", "TITLE", "NAME", "TXT"})
        definitions.setdefault(name, spec)
    return definitions


def _read(path):
    with path.open("rb") as stream:
        data = stream.read(_MAX_FILE + 1)
    if len(data) > _MAX_FILE or b"\0" in data:
        raise ValueError("not a supported text source")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _declarations(text, source, macros):
    text = _DIRECTIVE.sub(_blank, _strip_comments(text))
    # Menu/toolbar definitions have their own catalog. Some dialog-only helper
    # files just contain native ActionControls; keep those without menu syntax.
    if not _DIALOG_CONTEXT.search(text) and re.search(r"\b(?:Menu|MenuItem|ToolBar)\s*\(", text):
        return
    starts = [0] + [m.end() for m in re.finditer("\n", text)]
    for token in _CONSTRUCT.finditer(text):
        name = token["name"]
        if not name or name in {"NullControl", "HiddenControl", "GroupControl", "CBD_G", "CBD_GM"}:
            continue
        macro = macros.get(name)
        if name.startswith("CBD_") and macro is None:
            continue
        if macro is None or macro.takes_arguments:
            if not _CALL_OPEN.match(text, token.end()):
                continue
        kind = macro.kind if macro else _kind(name)
        label = macro.caption if macro else None
        origin = "macro_literal" if label is not None else "unresolved"
        if label is None and (macro.first_is_label if macro else name != "gfc_setupDirTree"):
            literal = _FIRST_LITERAL.match(text, token.end())
            if literal:
                label, origin = _label(literal[1]), "literal"
            elif name == "MenuItem":
                origin = "runtime_expression"
        if name == "gfc_setupDirTree":
            origin = "data_dependent"
        line = bisect_right(starts, token.start())
        yield {"source": source, "line": line, "column": token.start() - starts[line - 1] + 1,
               "construct": name, "kind": kind, "label": label, "label_source": origin}


def _source_files(root):
    for folder in _ROOTS:
        base = root / folder
        if not base.is_dir() or not base.resolve().is_relative_to(root):
            continue
        for current, folders, files in os.walk(base):
            current = Path(current)
            folders[:] = sorted(f for f in folders if (current / f).resolve() == current / f)
            for name in sorted(files):
                path = current / name
                if path.suffix.lower() in _SUFFIXES and path.resolve() == path:
                    yield path


@lru_cache(maxsize=1)
def _scan(root, files):
    header = root / "include" / "windows" / "dialog.hdo"
    macros = {}
    rows, skipped = [], []
    for path, _, _ in files:
        if path == header:
            try:
                macros = _macro_specs(_read(path))
            except (OSError, ValueError):
                pass  # Listed as unreadable in the source pass below.
            break
    for path, _, _ in files:
        source = path.relative_to(root).as_posix()
        try:
            rows.extend(_declarations(_read(path), source, macros))
        except (OSError, ValueError):
            skipped.append(source)
    return rows, skipped, bool(macros)


def dialog_inventory(install_root: Path) -> tuple[list[dict], dict]:
    """Return an independent copy of the complete declaration snapshot, with source coverage."""
    root = Path(install_root).resolve(strict=True)
    files = []
    for path in _source_files(root):
        stat = path.stat()
        files.append((path, stat.st_size, stat.st_mtime_ns))
    if not files:
        raise ValueError("No Studio dialog source files found in tasks, include or classes")
    rows, skipped, macros_available = _scan(root, tuple(files))
    metadata = {"source_files_scanned": len(files) - len(skipped), "skipped_files": list(skipped),
                "macro_definitions_available": macros_available, "total_controls": len(rows),
                "kind_counts": dict(Counter(row["kind"] for row in rows)), "scope": _SCOPE}
    return [dict(row) for row in rows], metadata


def list_dialog_controls(install_root: Path, *, query="", offset=0, limit=50) -> dict:
    """List declaration sites, scoped to the configured installation, without opening Studio."""
    if not isinstance(query, str) or len(query) > 256:
        raise ValueError("query must be a string of at most 256 characters")
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("offset must be nonnegative and limit must be 1..200")
    rows, metadata = dialog_inventory(install_root)
    words = query.casefold().split()
    matches = rows
    if words:
        matches = []
        for row in rows:
            fields = (row["source"], row["construct"], row["kind"], row["label"] or "")
            search_text = " ".join(fields).casefold()
            if all(word in search_text for word in words):
                matches.append(row)
    end = offset + limit
    return {**metadata, "matched_controls": len(matches), "offset": offset, "limit": limit,
            "next_offset": end if end < len(matches) else None, "controls": matches[offset:end]}
