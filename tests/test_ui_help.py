import tempfile
import unittest
from pathlib import Path

from lucidshape_mcp.ui_action_catalog import action_inventory
from lucidshape_mcp.ui_help import help_resource, is_help_action


class InstalledHelpTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        self.root = base / "Studio"
        tasks = self.root / "include" / "tasks"
        tasks.mkdir(parents=True)
        (tasks / "allCommandIDs.hdo").write_text("#define CM_SIMPLE 7")
        (tasks / "mainMenu.hdo").write_text('''
Menu("&Help",
 Menu("Tutorials", MenuItem("Simulation", "general_openTheHelpFile","$DOCPATH/Tutorials/Simulation 1.pdf","Contents")),
 MenuItem("Missing","general_openTheHelpFile","$DOCPATH/Missing.pdf","Contents"),
 MenuItem("Text","general_openTheHelpFile","$DOCPATH/Text.pdf","Contents"),
 MenuItem("Outside","general_openTheHelpFile","$DOCPATH/../../outside.pdf","Contents"),
 MenuItem("Alternate stream","general_openTheHelpFile","$DOCPATH/file.pdf:x.pdf","Contents"),
 MenuItem("From variable","general_openTheHelpFile",docPath,"Contents"),
 MenuItem("Other callback","openFile","$DOCPATH/Missing.pdf","Contents"),
 MenuItem("Only link","general_openTheHelpFile","$DOCPATH/link.pdf","Contents"));
''')
        self.docs = self.root / "doc"
        (self.docs / "Tutorials").mkdir(parents=True)
        self.data = b"%PDF-1.7\nexample"
        (self.docs / "Tutorials" / "Simulation 1.pdf").write_bytes(self.data)
        (self.docs / "Text.pdf").write_text("This is plain text")
        self.outside = base / "outside.pdf"
        self.outside.write_bytes(self.data)
        actions, coverage = action_inventory(self.root)
        self.assertEqual(coverage["declarations"], len(actions))
        self.actions = {a["label"]: a for a in actions}

    def resource(self, label):
        return help_resource(self.root, self.actions[label]["action_id"])

    def test_literal_pdf_is_verified_and_titled(self):
        self.assertTrue(is_help_action(self.actions["Simulation"]))
        actual = self.resource("Simulation")
        self.assertEqual(actual["status"], "available")
        self.assertEqual(actual["size_bytes"], len(self.data))
        self.assertEqual(actual["pdf_signature"], "%PDF-1.7")
        self.assertEqual(Path(actual["path"]), self.docs / "Tutorials" / "Simulation 1.pdf")
        self.assertEqual(actual["topic"], "Contents")
        self.assertEqual(actual["title"], "Simulation")

    def test_unavailable_documents_and_indirect_arguments(self):
        self.assertEqual(self.resource("Missing")["reason"], "file_not_installed")
        self.assertEqual(self.resource("Text")["reason"], "invalid_pdf_signature")
        for label in ("Outside", "Alternate stream", "From variable", "Other callback"):
            self.assertFalse(is_help_action(self.actions[label]))
            self.assertEqual(self.resource(label)["status"], "unavailable")
        with self.assertRaises(ValueError):
            help_resource(self.root, str(self.outside))

    def test_symlink_outside_installation_is_rejected(self):
        try:
            (self.docs / "link.pdf").symlink_to(self.outside)
        except OSError as error:
            self.skipTest(f"symlinks unavailable: {error}")
        self.assertEqual(self.resource("Only link")["reason"], "outside_documentation_directory")
