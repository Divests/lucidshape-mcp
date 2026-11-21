import json
import tempfile
import unittest
from pathlib import Path

from lucidshape_mcp import extensions, ui_measure


class PhotometryRegionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.source = self.root / "光度输入.ies"
        self.source.write_bytes(b"IESNA:LM-63-2002\n")
        self.bounds = [-5, 5, -4, 4]
        self.zone = [-2, 2, -1, 1]

    def params(self, mode, coordinates=None, unit=None):
        return extensions.validate(ui_measure.OPERATION, {"lid_path": str(self.source), "mode": mode,
                                                         "coordinates_deg": coordinates, "unit": unit})

    def read(self, params, mode_id, values, positions):
        fields = {"p0": mode_id, "p1": self.bounds, "p2": values, "p3": positions}
        (self.root / "engine-result.json").write_text(json.dumps({"List": fields}), encoding="ascii")
        return extensions.read_report(self.root, ui_measure.OPERATION, params)

    def test_all_seven_modes_keep_native_units_and_extrema_positions(self):
        cases = [
            ("point", [1, 1], None, [250], [[1, 1]], [250]),
            ("zone_extrema", self.zone, "lx_25m", [125, 1250], [[-2, -1], [2, 1]], [.2, 2]),
            ("minimum", None, "cd", [8], [[-5, -4]], [8]),
            ("maximum", None, "lx_10m", [1250], [[0, 1]], [12.5]),
            ("zone_mean", self.zone, "cd", [15.5], [], [15.5]),
            ("flux", None, None, [199.5], [], [199.5]),
            ("zone_flux", self.zone, "lm", [2.5], [], [2.5]),
        ]
        for mode_id, (mode, coords, unit, raw, points, converted) in enumerate(cases):
            with self.subTest(mode=mode):
                result, artifacts = self.read(self.params(mode, coords, unit), mode_id, raw, points)
                self.assertEqual(result["native_unit"], "lm" if mode in ("flux", "zone_flux") else "cd")
                self.assertEqual(result["native_values"], raw)
                self.assertEqual(result["values"], converted)
                self.assertEqual(result["positions_deg"], points)
                self.assertEqual(result["coordinates_deg"], coords or [])
                self.assertEqual(artifacts, [])

    def test_parameter_validation_rejects_wrong_units_shapes_and_nonfinite_angles(self):
        invalid = [
            ("maximum", [0, 0], "cd"), ("flux", [], "cd"), ("minimum", None, "lm"),
            ("zone_flux", [0, 0], "lm"), ("zone_mean", [0, 1, 2, 1], "cd"),
            ("zone_extrema", [0, 1, False, 1], "cd"), ("point", [float("inf"), 1], "cd"),
            ("point", [400, 1], "cd"), ("unknown", [], None),
        ]
        for mode, coords, unit in invalid:
            with self.subTest(mode=mode, coordinates=coords):
                with self.assertRaises(ValueError):
                    self.params(mode, coords, unit)

    def test_staging_selects_measurement_and_private_snapshot(self):
        job = self.root / "job"
        job.mkdir()
        params = self.params("zone_flux", self.zone, "lm")
        manifest, expected = extensions.stage(job, ui_measure.OPERATION, params)
        text = (job / "job.do").read_text(encoding="ascii")
        self.assertNotIn("@@", text)
        self.assertNotIn(str(self.source), text)
        self.assertIn("mode=6, u0=-2, u1=2, y0=-1, y1=1", text)
        self.assertIn("zoneIntegral", text)
        self.assertEqual(expected, ("engine-result.json",))
        self.assertEqual(manifest[0]["staged_name"], "input.ies")
        self.assertEqual((job / "input.ies").read_bytes(), self.source.read_bytes())

    def test_rejects_mismatched_mode_impossible_values_and_out_of_zone_extrema(self):
        p = self.params("zone_extrema", self.zone)
        cases = [
            (3, [10, 20], [[0, 0], [1, 1]]),
            (1, [21, 20], [[0, 0], [1, 1]]),
            (1, [-1, 20], [[0, 0], [1, 1]]),
            (1, [10, 20], [[4, 0], [1, 1]]),
            (1, [10, 20], [[0, 0]]),
        ]
        for mode_id, values, points in cases:
            with self.subTest(mode_id=mode_id, values=values, points=points):
                with self.assertRaises(ValueError):
                    self.read(p, mode_id, values, points)
        with self.assertRaises(ValueError):
            self.read(self.params("zone_mean", [-5, 9, -1, 1]), 4, [10], [])
        with self.assertRaises(ValueError):
            self.read(self.params("maximum"), 3, [55], [[0, 6]])
        with self.assertRaises(ValueError):
            self.read(self.params("point", [0, 0]), 0, [10], [[0, 1]])
