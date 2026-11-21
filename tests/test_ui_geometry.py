import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from lucidshape_mcp import ui_geometry as geometry


class PrimitiveTests(unittest.TestCase):
    def test_parameters_and_model_readback(self):
        p = geometry.validate({"primitive": "plane", "dimensions_mm": [12, 20], "center_mm": [10, -7, 13]})
        receipt = [1, 1, "primitive", "PlanarSurface", 1, [4, 16, -17, 3, 13, 13], [0,"","",-1], []]
        self.assertEqual(geometry.verify(receipt, p)[0]["native_class"], "PlanarSurface")
        for bad in ([*receipt[:5], [4, 16, -17, 3, 12, 12], *receipt[6:]], [0, *receipt[1:]],
                    [*receipt[:3], "BoxShape", *receipt[4:]]):
            with self.assertRaises(ValueError):
                geometry.verify(bad, p)
        for bad in ({"primitive": "unknown", "dimensions_mm": [1]},
                    {"primitive": "cylinder", "dimensions_mm": [1, -2]},
                    {"primitive": "box", "dimensions_mm": [2, True, 4]},
                    {"primitive": "box", "dimensions_mm": [2, 3, 4], "as_shape": False},
                    {"primitive": "disk", "dimensions_mm": [1, 2]},
                    {"primitive": "cone", "dimensions_mm": [2, 2, 4]},
                    {"primitive": "sphere", "dimensions_mm": [1], "center_mm": ["1", 0, 0]}):
            with self.assertRaises(ValueError):
                geometry.validate(bad)

    def test_menu_variants_follow_actual_callback_arguments(self):
        def preset(callback, args):
            return geometry.menu_preset({"kind": "script", "callback": callback, "arguments": args})

        self.assertEqual(preset("create_Box", []), {"primitive": "box", "as_shape": True})
        self.assertEqual(preset("create_DiskSurface", ["0"]), {"primitive": "disk", "as_shape": False})
        self.assertEqual(preset("create_DiskSurface", ["1"]), {"primitive": "disk", "as_shape": True})
        self.assertIsNone(preset("create_Box", ["1"]))
        self.assertIsNone(preset("create_DiskSurface", []))

    @unittest.skipUnless(os.environ.get("LUCIDSHAPE_PRIMITIVE_TEST_EXE"), "requires configured native engine")
    def test_native_geometry_roundtrips(self):
        exe = Path(os.environ["LUCIDSHAPE_PRIMITIVE_TEST_EXE"]).resolve()
        sizes = {"box": [12,20,10], "plane": [12,20], "sphere": [8], "cylinder": [8,10],
                 "disk": [8,2], "cone": [8,1,10]}
        cases = [(kind, wrapped) for kind in sizes for wrapped in ((True,) if kind == "box" else (False, True))]
        for primitive, wrapped in cases:
            with self.subTest(primitive=primitive, as_shape=wrapped), tempfile.TemporaryDirectory() as path:
                directory = Path(path)
                parameters = geometry.validate({"primitive": primitive, "dimensions_mm": sizes[primitive],
                                                "center_mm": [10, -7, 13], "as_shape": wrapped})
                _, outputs = geometry.stage(directory, parameters)
                proc = subprocess.run([str(exe), "-nopp", str(directory / "job.do")], cwd=directory,
                                      stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self.assertEqual(proc.returncode, 0, proc.stdout)
                self.assertIn(b"LUCIDSHAPE_MCP_OK", proc.stdout, proc.stdout)
                for name in outputs:
                    self.assertGreater((directory / name).stat().st_size, 0)
                fields = json.loads((directory / "engine-result.json").read_bytes())["List"]
                receipt = [fields[f"p{i}"] for i in range(len(fields))]
                try:
                    result, _ = geometry.verify(receipt, parameters)
                except ValueError as exc:
                    self.fail(f"{exc}: {receipt}")
                self.assertEqual(result["primitive"], primitive)
