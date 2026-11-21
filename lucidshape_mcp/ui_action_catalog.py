"""Read menu and toolbar declarations supplied with a LucidShape installation."""

from __future__ import annotations

import hashlib
import json
import os
import re
from bisect import bisect_right
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


_STRING = r'"(?:\\.|[^"\\])*"'
_LITERALS = re.compile(_STRING + r"|'(?:\\.|[^'\\])*'|//[^\n]*|/\*[\s\S]*?\*/")
_TOKEN = re.compile(
    _STRING + r"|'(?:\\.|[^'\\])*'|"
    r'(?P<include>^[ \t]*#\s*include\s*[<"][^">]+[">])|'
    r"\b(?P<call>ActionControl|MenuBitmapItem|MenuItem|Menu)\s*\(|[(),]", re.M
)
_ID = re.compile(r"^\s*#\s*define\s+(CM_\w+)\s+(0[xX][a-fA-F0-9]+|\d+)\s*$", re.M)
_DIRECTIVE = re.compile(r"^\s*#\s*(?:pragma\s+)?(if|ifdef|ifndef|elif|else|endif)\b\s*(.*)")
_INCLUDE = re.compile(r'^\s*#\s*include\s*[<"]([^">]+)[">]')
_IDENT = re.compile(r"[A-Za-z_]\w*")
_LABEL = re.compile(r"\s*(" + _STRING + ")")
_DEFINITION = re.compile(r"^\s*#\s*define\s+([A-Za-z_]\w*)")
_ASSIGN = re.compile(r"\b([A-Za-z_]\w*)\s*=\s*$")


def _toolbar_object(name):
    return name.lower().startswith(("barmenu", "viewbarmenu"))


def _unquote(value):
    if not re.fullmatch(_STRING, value):
        return None
    return re.sub(r'\\(["\\])', r"\1", value[1:-1])


def _label(value):
    return value.replace("&&", "\0").replace("&", "").replace("\0", "&")


def _contents(path):
    data = path.read_bytes()
    try:
        source = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        source = data.decode("cp1252", errors="replace")
    return _LITERALS.sub(
        lambda m: re.sub(r"[^\n]", " ", m[0]) if m[0].startswith("/") else m[0], source
    )


def _line_info(source):
    conditions, macros = [], []
    stack = []
    macro = None
    guard = re.match(r"\s*#\s*ifndef\s+(\w+)\s*\n\s*#\s*define\s+\1\b", source)
    for line in source.splitlines():
        directive = _DIRECTIVE.match(line)
        if directive:
            name, expression = directive.groups()
            expression = expression.strip()
            if name in ("if", "ifdef", "ifndef"):
                guard_line = guard and name == "ifndef" and expression == guard[1]
                stack.append("" if guard_line else f"{name} {expression}")
            elif name == "endif" and stack:
                stack.pop()
            elif name in ("else", "elif") and stack:
                stack[-1] = f"else of ({stack[-1]})" + (f" if {expression}" if name == "elif" else "")
        conditions.append(tuple(x for x in stack if x))
        define = _DEFINITION.match(line)
        if define:
            macro = define[1]
        macros.append(macro)
        if not line.rstrip().endswith("\\"):
            macro = None
    return conditions, macros


def _includes(source):
    if "#include" not in source and not re.search(r"#\s+include\b", source):
        return
    conditions, _ = _line_info(source)
    newlines = [m.start() for m in re.finditer("\n", source)]
    parents = []
    for token in _TOKEN.finditer(source):
        if token["include"]:
            line = bisect_right(newlines, token.start())
            yield _INCLUDE.match(token[0])[1], conditions[line], tuple(p for p in parents if p)
        elif token["call"] or token[0] == "(":
            label = _LABEL.match(source, token.end()) if token["call"] == "Menu" else None
            parents.append(_label(_unquote(label[1])) if label else "")
        elif token[0] == ")" and parents:
            parents.pop()


@dataclass
class _Call:
    name: str
    start: int
    arg_start: int
    menu: str = ""
    owner: str = ""
    args: list[str] = field(default_factory=list)


def _read_actions(path, source_name, source, ids, inherited):
    conditions, macros = _line_info(source)
    newlines = [m.start() for m in re.finditer("\n", source)]
    calls, actions = [], []
    duplicates = Counter()
    toolbar_refs = defaultdict(set)
    for match in _TOKEN.finditer(source):
        token = match[0]
        if match["call"] or token == "(":
            name = match["call"] or ""
            parent = _LABEL.match(source, match.end()) if name == "Menu" else None
            assignment = _ASSIGN.search(source[max(0, match.start() - 128):match.start()]) if name else None
            calls.append(_Call(name, match.start(), match.end(),
                               _label(_unquote(parent[1])) if parent else "",
                               assignment[1] if assignment else ""))
        elif token == "," and calls and calls[-1].name:
            frame = calls[-1]
            frame.args.append(source[frame.arg_start:match.start()].strip())
            frame.arg_start = match.end()
        elif token == ")" and calls:
            frame = calls.pop()
            if not frame.name:
                continue
            frame.args.append(source[frame.arg_start:match.start()].strip())
            if frame.name == "Menu":
                if frame.menu and _toolbar_object(frame.owner):
                    for ref in _IDENT.findall(_LITERALS.sub("", source[frame.start:match.start()])):
                        toolbar_refs[ref].add(frame.menu)
                continue
            line = bisect_right(newlines, frame.start) + 1
            # ActionControl also has dialog overloads, which have additional parameters.
            toolbar = frame.name == "MenuBitmapItem"
            if frame.name == "ActionControl" and len(frame.args) != 2:
                continue
            index = 2 if toolbar else 1
            if len(frame.args) <= index:
                continue
            label = _unquote(frame.args[0])
            binding = frame.args[index]
            symbol = binding if re.fullmatch(r"CM_\w+", binding) else None
            callback = _unquote(binding)
            number = ids.get(symbol)
            args = frame.args[index + 1:]
            if label is None:
                kind = "dynamic"
            elif callback and re.fullmatch(r"[A-Za-z_]\w*", callback):
                kind = "script"
            elif number is not None:
                kind = "native"
            else:
                kind = "dynamic"
            path_labels = [p.menu or "<dynamic menu>" for p in calls if p.name == "Menu"]
            macro = macros[line - 1]
            label = _label(label) if label is not None else frame.args[0]
            path_labels.append(label)
            local = list(conditions[line - 1])
            if kind == "native":
                operation = "native:" + symbol
            elif kind == "script":
                operation = "script:" + json.dumps([callback, *args], ensure_ascii=False, separators=(",", ":"))
            else:
                operation = None
            menu_owners = [p.owner for p in calls if p.name == "Menu" and p.owner]
            identity = [frame.name, path_labels, binding, args, macro, frame.owner, menu_owners, local]
            digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:20]
            duplicates[digest] += 1
            suffix = "" if duplicates[digest] == 1 else f".{duplicates[digest]}"
            action = dict(
                action_id=f"{path.stem}/{digest}{suffix}", kind=kind, command_symbol=symbol,
                command_id=number if kind == "native" else None, callback=callback, arguments=args,
                label=label, path=path_labels, surface="toolbar" if toolbar else "menu",
                source=source_name, line=line, conditions=local,
                include_conditions=[list(scope) for scope, _ in inherited],
                operation_key=operation, binding=binding, macro=macro, toolbar_views=[],
                bitmap_symbol=frame.args[1] if toolbar else None,
            )
            menu_paths = []
            for _, parent_path in inherited:
                full_path = [*parent_path, *path_labels]
                if full_path not in menu_paths:
                    menu_paths.append(full_path)
            action["menu_paths"] = menu_paths or [path_labels]
            action["path"] = action["menu_paths"][0]
            views = [p.menu for p in calls if _toolbar_object(p.owner) and p.menu]
            action["toolbar_views"] = views if toolbar else []
            actions.append((action, macro or frame.owner))
    for action, ref in actions:
        if action["surface"] == "toolbar" and ref:
            action["toolbar_views"] = sorted(set(action["toolbar_views"]) | toolbar_refs[ref])
    return [action for action, _ in actions]


def _manifest(install_root, profile):
    if profile not in ("studio", "all"):
        raise ValueError("profile must be 'studio' or 'all'")
    root = Path(install_root).resolve(strict=True)
    directory = (root / "include" / "tasks").resolve(strict=True)
    if not directory.is_relative_to(root):
        raise ValueError("include/tasks resolves outside the installation")
    files = []
    for path in sorted(directory.glob("*.hdo"), key=lambda p: p.name.casefold()):
        if path.resolve().parent == directory and path.is_file():
            stat = path.stat()
            files.append((path.name, stat.st_size, stat.st_mtime_ns))
    resource = None
    if os.name == "nt":
        for variant in ("BIN64GPU", "BIN64", "BIN32GPU", "BIN32"):
            candidate = (root / variant / "lt_en_US.dll").resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                stat = candidate.stat()
                resource = (candidate, stat.st_size, stat.st_mtime_ns)
                break
    return directory, tuple(files), resource


def _english_labels(resource, numbers):
    """Read the optional vendor tooltip table as Win32 resources, with no DLL initialization."""
    labels = {}
    if resource is None:
        return labels
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32")
    user = ctypes.WinDLL("user32")
    kernel.LoadLibraryExW.argtypes = (wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD)
    kernel.LoadLibraryExW.restype = wintypes.HMODULE
    kernel.FreeLibrary.argtypes = (wintypes.HMODULE,)
    kernel.FreeLibrary.restype = wintypes.BOOL
    user.LoadStringW.argtypes = (wintypes.HINSTANCE, wintypes.UINT, wintypes.LPWSTR, ctypes.c_int)
    user.LoadStringW.restype = ctypes.c_int
    # LOAD_LIBRARY_AS_DATAFILE | LOAD_LIBRARY_AS_IMAGE_RESOURCE
    handle = kernel.LoadLibraryExW(str(resource[0]), None, 0x22)
    if handle:
        try:
            buffer = ctypes.create_unicode_buffer(4096)
            for number in set(numbers):
                if user.LoadStringW(handle, number, buffer, len(buffer)):
                    lines = buffer.value.splitlines()
                    if lines:
                        labels[number] = lines
        finally:
            kernel.FreeLibrary(handle)
    return labels


@lru_cache(maxsize=4)
def _catalog(directory, manifest, resource, profile):
    installed = {name.lower(): name for name, _, _ in manifest}
    for required in ("mainmenu.hdo", "allcommandids.hdo"):
        if required not in installed:
            raise FileNotFoundError(f"Missing {required} in {directory}")
    definitions = _contents(directory / installed["allcommandids.hdo"])
    ids = {name: int(value, 16 if value.lower().startswith("0x") else 10) for name, value in _ID.findall(definitions)}
    sources = {}
    routes = defaultdict(list)
    unresolved = set()

    def visit(name, conditions, parent, visited):
        route = (conditions, parent)
        if name in visited or route in routes[name]:
            return
        routes[name].append(route)
        if name not in sources:
            sources[name] = _contents(directory / installed[name])
        for target, scope, menus in _includes(sources[name]):
            parts = target.replace("\\", "/").lower().split("/")
            if len(parts) == 2 and parts[0] == "tasks":
                parts = parts[1:]
            if len(parts) == 1 and parts[0] in installed:
                visit(parts[0], conditions + scope, parent + menus, visited | {name})
            else:
                unresolved.add(target)

    visit("mainmenu.hdo", (), (), set())
    if profile == "all":
        for name, original in installed.items():
            if name not in sources:
                sources[name] = _contents(directory / original)
    actions = []
    for name in sorted(sources):
        filename = installed[name]
        actions.extend(_read_actions(directory / filename, f"include/tasks/{filename}", sources[name], ids, routes[name]))
    labels = _english_labels(resource, ids.values())
    for action in actions:
        resource_id = ids.get(action["bitmap_symbol"]) if action["surface"] == "toolbar" else action["command_id"]
        lines = labels.get(resource_id, [])
        action["resource_labels"] = lines
    coverage = {
        "source_files": [f"include/tasks/{installed[name]}" for name in sorted(sources)],
        "command_definitions": len(ids),
        "declarations": len(actions),
        "by_kind": dict(Counter(a["kind"] for a in actions)),
        "by_surface": dict(Counter(a["surface"] for a in actions)),
        "unresolved_includes": sorted(unresolved),
        "resource_source": resource[0].relative_to(directory.parent.parent).as_posix() if resource and labels else None,
        "scope": "Static menu and bitmap toolbar declarations. Includes conditional and shared definitions. "
                 "Runtime-generated items, installed licenses, active views and enabled state require live evidence. "
                 "Dialog controls are outside this catalog.",
    }
    return actions, coverage


def action_inventory(install_root: Path, *, profile: str = "studio"):
    """Return a detached snapshot of all declarations and coverage for bulk consumers."""
    return deepcopy(_catalog(*_manifest(install_root, profile), profile))


def list_actions(install_root: Path, *, query: str = "", offset: int = 0, limit: int = 50, profile: str = "studio", eligible=None):
    """Search a page of installed menu/toolbar declarations in deterministic source order."""
    if offset < 0 or not 1 <= limit <= 200:
        raise ValueError("offset must be nonnegative and limit must be between 1 and 200")
    if not isinstance(query, str) or len(query) > 256:
        raise ValueError("query must be a string of at most 256 characters")
    actions, coverage = _catalog(*_manifest(install_root, profile), profile)
    terms = query.casefold().split()
    if terms:
        def matches(action):
            haystack = " ".join(str(action[key]) for key in (
                "action_id", "path", "command_symbol", "command_id", "callback", "arguments", "source",
                "kind", "surface", "toolbar_views", "macro", "resource_labels", "menu_paths",
            )).casefold()
            return all(term in haystack for term in terms)
        actions = [action for action in actions if matches(action)]
    if eligible is not None:
        actions = [action for action in actions if eligible(action)]
    return dict(profile=profile, total=len(actions), offset=offset, limit=limit,
                actions=deepcopy(actions[offset:offset + limit]), coverage=deepcopy(coverage))


def get_action(install_root: Path, action_id: str, *, profile: str = "studio"):
    """Resolve an exact catalog identifier against the current installation."""
    actions, _ = _catalog(*_manifest(install_root, profile), profile)
    for action in actions:
        if action["action_id"] == action_id:
            return deepcopy(action)
    raise ValueError(f"Unknown action_id in {profile} profile: {action_id}")
