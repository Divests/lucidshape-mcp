from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lucidshape_mcp.inputs import input_file, snapshot


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lucid model ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.job = self.root / "job"
        self.job.mkdir()

    def test_references_are_copied_but_names_and_comments_are_preserved(self):
        (self.source / "sub").mkdir()
        asset = self.source / "sub" / "result.lid"
        asset.write_text('@1=LIDData("data",("missing.lid"),0);')
        model = self.source / "scene.lue"
        text = '// "missing.lid"\n@1=LIDFile("name.lid", "sub/result.lid");'
        model.write_text(text)
        entries = snapshot(model, self.job, "input.lue")
        self.assertEqual(len(entries), 2)
        self.assertEqual(model.read_text(), text)
        copied = (self.job / "input.lue").read_text()
        self.assertIn('"name.lid"', copied)
        self.assertIn('// "missing.lid"', copied)
        self.assertIn('"asset_0001.lid"', copied)
        self.assertEqual((self.job / "asset_0001.lid").read_text(), asset.read_text())

    def test_absolute_and_parent_references_stay_in_job_copy(self):
        asset = self.root / "external.lid"
        asset.write_text('@1=LIDData("data");')
        model = self.source / "scene.lue"
        model.write_text(f'@1=LIDFile("a","{asset.as_posix()}"); @2=LIDFile("b","../external.lid");')
        entries = snapshot(model, self.job, "input.lue")
        self.assertEqual(len(entries), 2)
        copied = (self.job / "input.lue").read_text()
        self.assertEqual(copied.count("asset_0001.lid"), 2)
        self.assertNotIn("../", copied)

    def test_missing_asset_and_oversize_inputs_fail(self):
        model = self.source / "scene.lue"
        model.write_text('@1=LIDFile("a","missing.lid");')
        with self.assertRaisesRegex(ValueError, "Missing referenced asset"):
            snapshot(model, self.job, "input.lue")
        with patch("lucidshape_mcp.inputs.MAX_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "exceeds"):
                snapshot(model, self.job, "input.lue")

    def test_input_path_validation(self):
        with self.assertRaises(ValueError):
            input_file("relative.lue", {".lue"})
        with self.assertRaises(ValueError):
            input_file(None, {".lue"})
