import json
import tempfile
import unittest
from pathlib import Path

from lucidshape_mcp import ui_action_catalog, ui_help, ui_tools
from lucidshape_mcp.policy import Policy


class StudioUiToolsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / "LucidShape"
        tasks = self.root / "include" / "tasks"
        tasks.mkdir(parents=True)
        (tasks / "allCommandIDs.hdo").write_text("#define CM_UV_FILTER 3883\n#define CM_MISC 10\n")
        (tasks / "mainMenu.hdo").write_text('''
Menu("UVView", ActionControl("Basic Smoothing", CM_UV_FILTER), ActionControl("Unmapped", CM_MISC));
Menu("File", MenuItem("Open", "general_openFile", 0));
Menu("Analysis", MenuItem("Measure", "load_valuesFromLid"));
''')
        (tasks / "alternateMenu.hdo").write_text(
            'Menu("&Help", MenuItem("手册", "general_openTheHelpFile","$DOCPATH/Guide.pdf","Contents"));',
            encoding="utf-8")
        (self.root / "doc").mkdir()
        (self.root / "doc" / "Guide.pdf").write_bytes(b"%PDF-1.7\n")
        scripts = self.root / "tasks"
        scripts.mkdir()
        (scripts / "measure.do").write_text('DialogBox("Measurement", 0);\n')
        self.work = self.base / "work"
        self.full = Policy(self.work).enabled_tools
        self.read_only = Policy(self.work, read_only=True).enabled_tools

    def test_read_only_filter_applies_before_pagination_and_hides_disabled_presets(self):
        page = ui_tools.action_page(self.root, self.read_only, executable_only=True, limit=1)
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["actions"][0]["execution"]["tool"], "measure_photometry_region")
        self.assertEqual(ui_tools.action_page(self.root, self.read_only,
                                             executable_only=True, offset=1)["actions"], [])
        for label in ("Open", "Smoothing"):
            with self.subTest(label=label):
                row = ui_tools.action_page(self.root, self.read_only, query=label)["actions"][0]
                self.assertEqual(row["execution"]["status"], "disabled_by_policy")
                self.assertNotIn("tool", row["execution"])
                self.assertNotIn("preset_arguments", row["execution"])
        self.assertEqual(ui_tools.action_page(self.root, self.full, executable_only=True)["total"], 3)
        row = ui_tools.action_page(self.root, self.full, query="basic smoothing")["actions"][0]
        self.assertEqual(row["execution"]["preset_arguments"], {"command": "CM_UV_FILTER"})
        self.assertEqual(ui_tools.action_page(self.root, self.full, query="unmapped")["actions"][0]["execution"],
                         {"status": "catalog_only"})

    def test_alternate_help_preset_resolves_without_guessing_profile(self):
        self.assertEqual(ui_tools.action_page(self.root, self.read_only, query="手册")["total"], 0)
        row = ui_tools.action_page(self.root, self.read_only, query="手册", profile="all",
                                   executable_only=True)["actions"][0]
        self.assertEqual(row["execution"]["preset_arguments"], {"action_id": row["action_id"], "profile": "all"})
        resolved = ui_help.help_resource(self.root, **row["execution"]["preset_arguments"])
        self.assertEqual((resolved["status"], resolved["title"]), ("available", "手册"))
        detail = ui_action_catalog.get_action(self.root, row["action_id"], profile="all")
        self.assertEqual(ui_tools.describe(detail, self.read_only, "all"), row)

    def test_export_contains_every_listed_declaration_and_uses_same_policy(self):
        receipt = ui_tools.export_catalog(self.root, self.work, self.read_only, "all")
        path = Path(receipt["catalog_path"])
        self.assertEqual(path.parent, self.work)
        self.assertEqual(receipt["bytes"], path.stat().st_size)
        data = json.loads(path.read_text(encoding="utf-8"))
        page = ui_tools.action_page(self.root, self.read_only, profile="all")
        self.assertEqual(data["actions"], page["actions"])
        self.assertEqual(data["summary"], receipt["summary"])
        self.assertEqual(data["summary"]["declarations_by_execution"],
                         {"available": 2, "catalog_only": 1, "disabled_by_policy": 2})
        self.assertEqual(data["summary"]["dialog_control_declarations"], len(data["controls"]))
        self.assertTrue(any(row["label"] == "Measurement" for row in data["controls"]))
        self.assertIn("手册", path.read_text(encoding="utf-8"))
        self.assertFalse(list(self.work.glob("*.tmp")))
        second = ui_tools.export_catalog(self.root, self.work, self.full, "studio")
        self.assertNotEqual(path, Path(second["catalog_path"]))
        self.assertEqual(json.loads(path.read_bytes()), data)
