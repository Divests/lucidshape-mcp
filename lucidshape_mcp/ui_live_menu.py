"""Inspect the verified Studio window's current standard Windows menu."""
from __future__ import annotations

from pathlib import Path
import sys

from .dde_worker import studio_processes
from .live_studio import validate_pid
from .private_studio import _WindowTextReader


class _MenuReader(_WindowTextReader):
    def __init__(self):
        super().__init__()
        from ctypes import wintypes as w
        c = self.ctypes

        class MenuItemInfo(c.Structure):
            _fields_ = [("size", w.UINT), ("mask", w.UINT), ("type", w.UINT),
                        ("state", w.UINT), ("id", w.UINT), ("submenu", w.HMENU),
                        ("checked_bitmap", w.HBITMAP), ("unchecked_bitmap", w.HBITMAP),
                        ("item_data", c.c_size_t), ("text", c.c_void_p),
                        ("text_size", w.UINT), ("bitmap", w.HBITMAP)]

        self.info_type = MenuItemInfo
        signatures = {
            "GetMenu": ([w.HWND], w.HMENU), "GetMenuItemCount": ([w.HMENU], c.c_int),
            "IsWindowEnabled": ([w.HWND], w.BOOL),
            "GetMenuItemInfoW": ([w.HMENU, w.UINT, w.BOOL, c.POINTER(MenuItemInfo)], w.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.user32, name)
            function.argtypes, function.restype = arguments, result

    def windows(self, process_id):
        found = []
        enumerated = 0

        def collect(hwnd):
            nonlocal enumerated
            enumerated += 1
            if self.pid(hwnd) == process_id and self.visible(hwnd):
                found.append((hwnd, self.user32.GetMenu(hwnd), self.class_name(hwnd)))
            return enumerated < 4096 and len(found) < 64

        self.enumerate(collect)
        return found, enumerated >= 4096 or len(found) >= 64

    def count(self, menu):
        return self.user32.GetMenuItemCount(menu)

    def item(self, menu, position):
        # MIIM_FTYPE | STRING | SUBMENU | ID | STATE. The first read also
        # distinguishes custom-drawn entries from text-backed labels.
        c = self.ctypes
        info = self.info_type(size=c.sizeof(self.info_type), mask=0x147)
        if not self.user32.GetMenuItemInfoW(menu, position, True, c.byref(info)):
            return None
        original_length = info.text_size
        label = ""
        if original_length:
            buffer = c.create_unicode_buffer(min(original_length, 512) + 1)
            info.text = c.addressof(buffer)
            info.text_size = len(buffer)
            if not self.user32.GetMenuItemInfoW(menu, position, True, c.byref(info)):
                return None
            label = buffer.value
        return {"text": label, "truncated_label": original_length > 512,
                "id": info.id, "submenu": info.submenu, "type": info.type, "state": info.state}


def _menu_snapshot(reader, menu, window_enabled):
    rows, warnings = [], set()
    inspected = 0

    def walk(handle, parents, positions, ancestors, enabled):
        nonlocal inspected
        if handle in ancestors or len(ancestors) >= 16:
            warnings.add("submenu_cycle_or_depth_limit")
            return
        count = reader.count(handle)
        if count < 0:
            warnings.add("unreadable_submenu")
            return
        for pos in range(count):
            if inspected >= 4096:
                warnings.add("item_limit")
                return
            inspected += 1
            item = reader.item(handle, pos)
            if item is None:
                warnings.add("menu_changed_or_unreadable_item")
                continue
            if item["type"] & 0x800:  # MFT_SEPARATOR
                continue
            text, _, shortcut = item["text"].partition("\t")
            label = text.replace("&&", "\0").replace("&", "").replace("\0", "&")
            path = [*parents, label or None]
            location = [*positions, pos]
            submenu = item["submenu"]
            item_enabled = not bool(item["state"] & 3)
            if item["truncated_label"]:
                warnings.add("label_length_limit")
            rows.append({"path": path, "positions": location, "label": label or None,
                         "command_id": None if submenu else item["id"], "shortcut": shortcut or None,
                         "submenu": bool(submenu), "owner_drawn": bool(item["type"] & 0x100),
                         "enabled": enabled and item_enabled, "checked": bool(item["state"] & 8)})
            if submenu:
                walk(submenu, path, location, ancestors | {handle}, enabled and item_enabled)

    walk(menu, [], [], set(), window_enabled)
    return rows, sorted(warnings)


def list_live_menu(engine_exe, *, process_id=None, query="", offset=0, limit=50):
    """Read HMENU metadata only; dynamic items reflect the latest state published by Studio."""
    validate_pid(process_id)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("offset must be nonnegative and limit must be 1..200")
    if not isinstance(query, str) or len(query) > 256:
        raise ValueError("query must be a string of at most 256 characters")
    result = {"backend": "win32_hmenu", "process_id": process_id, "offset": offset, "limit": limit,
              "total_items": 0, "matched_items": 0, "next_offset": None, "items": [],
              "scope": "Currently published HMENU state; excludes detached popups, toolbar and dialog controls."}
    if sys.platform != "win32":
        return dict(result, status="unavailable", reason="windows_required")
    if not engine_exe or not Path(engine_exe).is_file():
        return dict(result, status="unavailable", reason="installation_unavailable")
    expected = Path(engine_exe).resolve().with_name("LucidShape.exe")
    if not expected.is_file():
        return dict(result, status="unavailable", reason="studio_executable_missing")
    try:
        processes = studio_processes()
        matches = [p for p in processes if p.get("executable")
                   and Path(p["executable"]).resolve() == expected
                   and (process_id is None or p["process_id"] == process_id)]
        if not matches:
            return dict(result, status="unavailable", reason="no_verified_matching_process")
        if len(matches) != 1:
            return dict(result, status="unavailable", reason="multiple_matching_processes",
                        process_ids=[p["process_id"] for p in matches])
        process = matches[0]
        pid = process["process_id"]
        result["process_id"] = pid
        creation = process.get("creation_time_windows_ticks")
        if not creation:
            return dict(result, status="unavailable", reason="process_identity_unverified")
        reader = _MenuReader()
        windows, truncated = reader.windows(pid)
        if truncated:
            return dict(result, status="unavailable", reason="window_enumeration_limit")
        frames = [(hwnd, menu, name) for hwnd, menu, name in windows if menu]
        if not frames:
            reason = "no_published_hmenu" if windows else "no_visible_window"
            return dict(result, status="unavailable", reason=reason)
        if len(frames) > 1:
            return dict(result, status="unavailable", reason="multiple_menu_windows",
                        window_classes=[f[2] for f in frames])
        hwnd, menu, frame_class = frames[0]
        rows, warnings = _menu_snapshot(reader, menu, bool(reader.user32.IsWindowEnabled(hwnd)))
        still_owned = any(p["process_id"] == pid and p.get("creation_time_windows_ticks") == creation
                          and p.get("executable") and Path(p["executable"]).resolve() == expected
                          for p in studio_processes())
        if not still_owned or reader.pid(hwnd) != pid or reader.user32.GetMenu(hwnd) != menu:
            return dict(result, status="unavailable", reason="window_or_menu_changed")
    except (AttributeError, OSError, RuntimeError):
        return dict(result, status="unavailable", reason="windows_query_failed")

    words = query.casefold().split()
    matches = []
    for row in rows:
        fields = [part for part in row["path"] if part]
        fields.append(str(row["command_id"]))
        if all(word in " ".join(fields).casefold() for word in words):
            matches.append(row)
    if rows and all(row["label"] is None for row in rows):
        warnings.append("menu_labels_unavailable")
    end = offset + limit
    return dict(result, status="partial" if warnings else "ok", window_class=frame_class,
                warnings=warnings, total_items=len(rows), matched_items=len(matches),
                next_offset=end if end < len(matches) else None, items=matches[offset:end])
