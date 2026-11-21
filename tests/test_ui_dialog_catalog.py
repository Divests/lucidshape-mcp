from pathlib import Path
import os
import tempfile
import unittest

from lucidshape_mcp.ui_dialog_catalog import dialog_inventory, list_dialog_controls


class DialogCatalogTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        windows = self.root / "include" / "windows"
        windows.mkdir(parents=True)
        (windows / "dialog.hdo").write_text(
            '#define CBD_APPLYFU(FN) ActionControl("Apply", 7, FN)\n'
            '#define CBD_CANCEL ActionControl("Cancel", 8, "")\n'
            '#define CBD_R(TEXT) BoolRadioCheckControl(TEXT, 9)\n'
            '#define CBD_DDF2(LABEL,U,V) CBD_F(LABEL, U, V)\n'
            '#define CBD_EXAMPLE \\\n    ActionControl("Ignore this definition", 4)\n',
            encoding="utf-8")
        scripts = self.root / "tasks"
        scripts.mkdir()
        self.dialog = scripts / "measure.do"
        self.dialog.write_text(
            '// ActionControl("Ignore comment", 0)\n'
            'object d = DialogBox("測定", 0);\n'
            '/*\nCBD_CANCEL\n*/\n'
            'CBD_DDF2("u,v", u, v), CBD_R("candela");\n'
            'MenuItem("point measure"), MenuItem(gpuName);\n'
            'CBD_APPLYFU("callbackThatIsNotACaption"), CBD_CANCEL;\n'
            'string info = "CBD_CANCEL";\n'
            'gfc_setupDirTree(folder, options);\n',
            encoding="utf-8")

    def test_labels_locations_and_unresolved_data(self):
        result = list_dialog_controls(self.root, limit=50)
        self.assertEqual(result["source_files_scanned"], 2)
        self.assertEqual(result["total_controls"], 8)
        self.assertEqual(result["matched_controls"], 8)
        self.assertTrue(result["macro_definitions_available"])
        self.assertEqual(result["kind_counts"]["button"], 2)
        rows = result["controls"]
        self.assertEqual([row["label"] for row in rows],
                         ["測定", "u,v", "candela", "point measure", None, "Apply", "Cancel", None])
        dynamic = [row for row in rows if row["label"] is None]
        self.assertEqual([row["label_source"] for row in dynamic], ["runtime_expression", "data_dependent"])
        self.assertEqual((rows[5]["line"], rows[5]["column"], rows[5]["source"]), (8, 1, "tasks/measure.do"))
        self.assertEqual(rows[5]["label_source"], "macro_literal")
        self.assertEqual(result["skipped_files"], [])

    def test_query_pagination_and_invalidation(self):
        page = list_dialog_controls(self.root, query="MEASURE.DO button", limit=1)
        self.assertEqual((page["matched_controls"], page["next_offset"]), (2, 1))
        second = list_dialog_controls(self.root, query="measure.do button", offset=page["next_offset"], limit=1)
        self.assertEqual(second["controls"][0]["label"], "Cancel")
        self.assertIsNone(second["next_offset"])
        with self.dialog.open("a", encoding="utf-8") as stream:
            stream.write('CBD_R("new option");\n')
        self.assertEqual(list_dialog_controls(self.root, query="new option")["matched_controls"], 1)

    def test_export_matches_pages_and_is_an_independent_snapshot(self):
        rows, meta = dialog_inventory(self.root)
        self.assertEqual(rows, list_dialog_controls(self.root)["controls"])
        self.assertEqual(meta["total_controls"], len(rows))
        rows[0]["label"] = "local edit"
        self.assertEqual(dialog_inventory(self.root)[0][0]["label"], "測定")

    def test_scope_excludes_menu_only_files_and_binary(self):
        (self.root / "tasks" / "menu.hdo").write_text('MenuItem("main menu option");', encoding="utf-8")
        (self.root / "tasks" / "packed.luf").write_bytes(b"x\0y")
        result = list_dialog_controls(self.root)
        self.assertEqual(result["source_files_scanned"], 3)
        self.assertEqual(result["skipped_files"], ["tasks/packed.luf"])
        self.assertNotIn("main menu option", [row["label"] for row in result["controls"]])
        with self.assertRaises(ValueError):
            list_dialog_controls(self.root, limit=201)
        with self.assertRaises(ValueError):
            list_dialog_controls(self.root, offset=-1)

    @unittest.skipUnless(os.environ.get("LUCIDSHAPE_DIALOG_TEST_ROOT"), "Studio installation is optional")
    def test_installed_lid_measure_dialog(self):
        install = Path(os.environ["LUCIDSHAPE_DIALOG_TEST_ROOT"])
        result = list_dialog_controls(install, query="valuesFromLID", limit=200)
        labels = {row["label"] for row in result["controls"]}
        self.assertTrue({"u,v-pos:", "point measure", "candela", "Apply"} <= labels)
        self.assertTrue(result["macro_definitions_available"])
        for row in result["controls"]:
            self.assertGreater(row["line"], 0)
            self.assertTrue((install / row["source"]).is_file())
