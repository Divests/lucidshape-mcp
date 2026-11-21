import tempfile
import unittest
from pathlib import Path

from lucidshape_mcp.ui_action_catalog import get_action, list_actions


class UiActionCatalogTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.tasks = self.root / "include" / "tasks"
        self.tasks.mkdir(parents=True)
        self.put("allCommandIDs.hdo", "#define CM_TOOL 0x2A\n#define CM_RUN 7\n")
        self.put("mainMenu.hdo", '''
#ifndef MAIN_HDO
#define MAIN_HDO
#include <tasks/allCommandIDs.hdo>
object menu = Menu("&View", Menu("Filter",
#pragma if GPUtrace
#include <tasks/extra.hdo>
#pragma endif
   ActionControl("Again", CM_RUN)));
#define COMMON_TOOLS \\
    ,MenuBitmapItem("Folder",CM_TOOL,"openFile",0)
object viewBarMenu_uv = Menu("uvdata" COMMON_TOOLS, MenuBitmapItem("Run", CM_TOOL, CM_RUN));
// ActionControl("Commented out", CM_RUN)
/* ActionControl("Also hidden", CM_TOOL) */
#endif
''')
        self.put("extra.hdo", '''
MenuItem("Open, \\"quoted\\" (file)", "openFile", 1, List(2,3)),
ActionControl("Again", CM_TOOL),
MenuItem("R&&D", dynamicCallback),
''')
        self.put("alternative.hdo", 'Menu("Other", ActionControl("Alternate", CM_RUN));')

    def put(self, name, code):
        (self.tasks / name).write_text(code, encoding="utf-8")

    def test_native_command_does_not_use_bitmap_id(self):
        action = list_actions(self.root, query="uvdata Run")["actions"][0]
        self.assertEqual(action["command_id"], 7)
        self.assertEqual(action["command_symbol"], "CM_RUN")
        self.assertEqual(action["bitmap_symbol"], "CM_TOOL")
        self.assertEqual(action["toolbar_views"], ["uvdata"])
        self.assertEqual(get_action(self.root, action["action_id"]), action)
        shared = list_actions(self.root, query="Folder")["actions"][0]
        self.assertEqual(shared["kind"], "script")
        self.assertIsNone(shared["command_id"])
        self.assertEqual(shared["callback"], "openFile")
        self.assertEqual(shared["arguments"], ["0"])
        self.assertEqual(shared["toolbar_views"], ["uvdata"])

    def test_nested_invocation_conditional_include_and_strings(self):
        action = list_actions(self.root, query="quoted")["actions"][0]
        self.assertEqual(action["path"], ["View", "Filter", 'Open, "quoted" (file)'])
        self.assertEqual(action["arguments"], ["1", "List(2,3)"])
        self.assertEqual(action["include_conditions"], [["if GPUtrace"]])
        self.assertEqual(action["source"], "include/tasks/extra.hdo")
        self.assertEqual(action["line"], 2)
        self.assertEqual(list_actions(self.root, query="commented")["total"], 0)
        dynamic = list_actions(self.root, query="R&D")["actions"][0]
        self.assertEqual(dynamic["kind"], "dynamic")
        self.assertIsNone(dynamic["command_id"])
        self.assertIsNone(dynamic["operation_key"])

    def test_profiles_pagination_identity_and_cache(self):
        first = list_actions(self.root, query="Again", limit=1)
        second = list_actions(self.root, query="Again", offset=1, limit=1)
        self.assertEqual(first["total"], 2)
        self.assertNotEqual(first["actions"][0]["action_id"], second["actions"][0]["action_id"])
        action_id = first["actions"][0]["action_id"]
        first["actions"][0]["path"].clear()
        self.assertEqual(get_action(self.root, action_id)["path"][-1], "Again")
        self.assertEqual(list_actions(self.root, query="Alternate")["total"], 0)
        self.assertEqual(list_actions(self.root, query="Alternate", profile="all")["total"], 1)
        before = (self.tasks / "extra.hdo").read_text(encoding="utf-8")
        self.put("extra.hdo", "// new intro\n" + before)
        moved = get_action(self.root, action_id)
        self.assertEqual(moved["line"], 4)
        self.assertEqual(list_actions(self.root)["coverage"]["command_definitions"], 2)
        for options in (dict(limit=0), dict(offset=-1), dict(profile="missing")):
            with self.assertRaises(ValueError):
                list_actions(self.root, **options)
        with self.assertRaises(ValueError):
            get_action(self.root, "unknown")

    def test_include_cycle_external_file_and_missing_installation(self):
        self.put("extra.hdo", '#include <tasks/mainMenu.hdo>\n#include "../outside.hdo"\n')
        (self.tasks.parent / "outside.hdo").write_text('ActionControl("Outside", CM_RUN)')
        result = list_actions(self.root, query="Outside")
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["coverage"]["unresolved_includes"], ["../outside.hdo"])
        (self.tasks / "allCommandIDs.hdo").unlink()
        with self.assertRaises(FileNotFoundError):
            list_actions(self.root)
