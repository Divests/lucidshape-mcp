import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lucidshape_mcp.studio import open_file


class StudioTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_receipt_required_and_source_is_snapshotted(self):
        with tempfile.TemporaryDirectory(prefix="studio test ") as temporary:
            root=Path(temporary)
            source=root/"part.step"
            source.write_bytes(b"fixture STEP bytes")
            executable=root/"LucidShape.exe"
            executable.touch()

            def launch(arguments, **kwargs):
                directory=Path(arguments[1]).parent
                (directory/"studio-receipt.json").write_text(json.dumps({"List":{"p0":1,"p1":10,"p2":1,"p3":[0,1,0,2,0,3]}}))
                return SimpleNamespace(pid=123)

            with patch("lucidshape_mcp.studio.subprocess.Popen",side_effect=launch):
                result=await open_file(str(source),executable,root,1)
            self.assertTrue(result["studio_loaded_verified"])
            self.assertFalse(result["stability_verified"])
            self.assertFalse(result["gui_responsiveness_verified"])
            self.assertEqual(result["dimensions_mm"],[1,2,3])
            self.assertNotEqual(result["loaded_file"],str(source))
            self.assertEqual(source.read_bytes(),b"fixture STEP bytes")

    async def test_process_launch_is_not_treated_as_load_success(self):
        with tempfile.TemporaryDirectory(prefix="studio test ") as temporary:
            root=Path(temporary)
            source=root/"part.step"
            source.write_text("fixture")
            executable=root/"LucidShape.exe"
            executable.touch()
            with patch("lucidshape_mcp.studio.subprocess.Popen",return_value=SimpleNamespace(pid=123)):
                result=await open_file(str(source),executable,root,1)
            self.assertFalse(result["studio_loaded_verified"])
            self.assertEqual(result["status"],"unverified")


if __name__ == "__main__":
    unittest.main()
