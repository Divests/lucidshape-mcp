"""Resolve installed Help-menu PDFs by catalog ID."""

import os
import re
from pathlib import Path, PureWindowsPath

from .ui_action_catalog import get_action


def _help_target(action):
    if (action.get("kind") != "script" or action.get("callback") != "general_openTheHelpFile"
            or action.get("surface") != "menu" or action.get("path", [])[:1] != ["Help"]):
        return None
    arguments = action.get("arguments", [])
    if len(arguments) != 2:
        return None
    literal = re.fullmatch(r'"\$DOCPATH/([^"\\]+\.pdf)"', arguments[0], flags=re.I)
    topic = re.fullmatch(r'"([^"\\\r\n]+)"', arguments[1])
    if not literal or not topic:
        return None
    parts = literal[1].split("/")
    if any(part in ("", ".", "..") or part.endswith((".", " ")) or
           re.search(r'[\x00-\x1f<>:"|?*$]', part) or
           PureWindowsPath(part).is_reserved() for part in parts):
        return None
    return parts, topic[1]


def is_help_action(action):
    """True for a literal Help-menu PDF declaration, regardless of file availability."""
    return _help_target(action) is not None


def help_resource(install_root: Path, action_id: str, *, profile: str = "studio"):
    """Return the installed PDF path and verified header for a fixed catalog action."""
    action = get_action(install_root, action_id, profile=profile)
    return installed_pdf(install_root, action)


def installed_pdf(install_root, action):
    result = {"status": "unavailable", "action_id": action["action_id"], "title": action["label"]}
    target = _help_target(action)
    if target is None:
        return {**result, "reason": "not_a_fixed_help_pdf"}
    parts, topic = target
    root = Path(install_root).resolve()
    try:
        docs = (root / "doc").resolve()
        if not docs.is_relative_to(root) or docs == root:
            return {**result, "reason": "outside_documentation_directory"}
        path = docs.joinpath(*parts).resolve()
        if not path.is_relative_to(docs) or path == docs:
            return {**result, "reason": "outside_documentation_directory"}
        if not path.is_file():
            return {**result, "reason": "file_not_installed"}
        with path.open("rb") as handle:
            header = handle.read(10)
            size = os.fstat(handle.fileno()).st_size
    except (OSError, RuntimeError):
        return {**result, "reason": "file_unavailable"}
    signature = re.match(rb"%PDF-\d\.\d", header)
    if not signature:
        return {**result, "reason": "invalid_pdf_signature"}
    return {**result, "status": "available", "path": str(path), "topic": topic,
            "size_bytes": size, "pdf_signature": signature[0].decode("ascii")}
