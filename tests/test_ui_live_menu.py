from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from lucidshape_mcp import ui_live_menu as menu


def entry(label, identifier=0, *, submenu=None, kind=0, state=0):
    return {"text": label, "id": identifier, "submenu": submenu, "type": kind, "state": state,
            "truncated_label": False}


class LiveMenuTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = Path(directory.name) / "lush.exe"
        self.engine.touch()
        studio = self.engine.with_name("LucidShape.exe")
        studio.touch()
        self.process = {"process_id": 5, "executable": str(studio), "creation_time_windows_ticks": 30}
        self.api = Mock()
        self.api.windows.return_value = ([(88, 123, "Studio")], False)
        self.api.user32.IsWindowEnabled.return_value = True
        self.api.user32.GetMenu.return_value = 123
        self.api.pid.return_value = 5
        self.items = {123: [entry("&View", submenu=234)], 234: [
            entry("Checked && visible\tF8", 339, state=8), entry("", kind=0x800), entry("", 122, kind=0x100)]}
        self.api.count.side_effect = lambda h: len(self.items[h])
        self.api.item.side_effect = lambda h, p: self.items[h][p]

    def read(self, processes=None, **kwargs):
        with (patch.object(menu.sys, "platform", "win32"),
              patch.object(menu, "studio_processes", return_value=processes if processes is not None else [self.process]),
              patch.object(menu, "_MenuReader", return_value=self.api)):
            return menu.list_live_menu(self.engine, **kwargs)

    def test_verified_hierarchy_state_paging_and_owner_draw(self):
        first = self.read(limit=1, query="view")
        self.assertEqual((first["total_items"], first["matched_items"], first["next_offset"]), (3, 3, 1))
        second = self.read(query="Fictional")
        self.assertEqual(second["items"], [])
        checked = self.read(query="339")["items"][0]
        self.assertEqual(checked["path"], ["View", "Checked & visible"])
        self.assertEqual(checked["shortcut"], "F8")
        self.assertTrue(checked["checked"])
        self.api.user32.IsWindowEnabled.return_value = False
        self.assertFalse(self.read(query="339")["items"][0]["enabled"])
        custom = self.read(query="122")["items"][0]
        self.assertTrue(custom["owner_drawn"])
        self.assertIsNone(custom["label"])

    def test_no_matching_pid_multiple_processes_and_windows(self):
        other = dict(self.process, process_id=6)
        hidden = dict(self.process, executable=str(self.engine))
        self.assertEqual(self.read([hidden])["reason"], "no_verified_matching_process")
        self.assertEqual(self.read([self.process, other])["reason"], "multiple_matching_processes")
        self.assertEqual(self.read([self.process, other], process_id=5)["status"], "ok")
        self.api.windows.return_value = ([], False)
        self.assertEqual(self.read()["reason"], "no_visible_window")
        self.api.windows.return_value = ([(8, 0, "Studio")], False)
        self.assertEqual(self.read()["reason"], "no_published_hmenu")
        self.api.windows.return_value = ([(8, 2, "A"), (3, 4, "B")], False)
        self.assertEqual(self.read()["reason"], "multiple_menu_windows")

    def test_changed_window_cycle_and_unsupported_platform(self):
        self.items[234].append(entry("cycle", submenu=123))
        self.assertEqual(self.read()["warnings"], ["submenu_cycle_or_depth_limit"])
        self.api.user32.GetMenu.return_value = 5
        self.assertEqual(self.read()["reason"], "window_or_menu_changed")
        with patch.object(menu.sys, "platform", "linux"):
            self.assertEqual(menu.list_live_menu(None)["reason"], "windows_required")
        with self.assertRaises(ValueError):
            self.read(process_id=True)

    @unittest.skipUnless(sys.platform == "win32", "requires User32")
    def test_real_win32_menu_query_on_unattached_test_menu(self):
        import ctypes as c
        from ctypes import wintypes as w
        reader = menu._MenuReader()
        user = reader.user32
        user.CreatePopupMenu.argtypes, user.CreatePopupMenu.restype = [], w.HMENU
        user.AppendMenuW.argtypes = [w.HMENU, w.UINT, c.c_size_t, w.LPCWSTR]
        user.DestroyMenu.argtypes = [w.HMENU]
        handle = user.CreatePopupMenu()
        self.assertTrue(handle)
        try:
            self.assertTrue(user.AppendMenuW(handle, 8, 101, "測定"))
            self.assertTrue(user.AppendMenuW(handle, 3, 102, "Disabled"))
            rows, warnings = menu._menu_snapshot(reader, handle, True)
            self.assertFalse(warnings)
            self.assertEqual(rows[0]["label"], "測定")
            self.assertTrue(rows[0]["checked"])
            self.assertFalse(rows[1]["enabled"])
        finally:
            user.DestroyMenu(handle)
