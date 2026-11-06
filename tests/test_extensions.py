import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest

from lucidshape_mcp.extensions import compare, validate, stage, verify_edit, read_report


def meta(values, u=None):
    return ["fixture", 0, 2, 2, [-10, 10, -10, 10], u or [-5, 5], [-5, 5],
            [max(values), 5, 5], sum(values) / 10]


def write_grid(root, index, values):
    with (root / f"grid_{index}.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream); writer.writerow(["u_index", "v_index", "intensity_cd"])
        writer.writerows((i % 2, i // 2, value) for i, value in enumerate(values))


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_signed_loss_zero_reference_and_input_preservation(self):
        av, bv = [100, 0, 100, 200], [0, 50, 200, 100]
        write_grid(self.root, 0, av); write_grid(self.root, 1, bv)
        before = (self.root / "grid_0.csv").read_bytes()
        result, artifacts = compare(self.root, [meta(av), meta(bv)])
        self.assertEqual((result["min_delta_cd"], result["max_delta_cd"]), (-100, 100))
        self.assertEqual(result["decreased_cells"], 2)
        self.assertEqual(result["relative_error_zero_reference_cells"], 1)
        self.assertEqual(result["relative_l1"], 350 / 400)
        self.assertAlmostEqual(result["rmse_cd"], (32500 / 4) ** .5)
        self.assertEqual(before, (self.root / "grid_0.csv").read_bytes())
        with (self.root / artifacts[0]).open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(float(rows[0]["candidate_minus_reference_cd"]), -100)
        self.assertEqual(rows[1]["relative_change_pct"], "")

    def test_zero_reference_has_no_relative_l1(self):
        write_grid(self.root, 0, [0] * 4); write_grid(self.root, 1, [1] * 4)
        result, _ = compare(self.root, [meta([0] * 4), meta([1] * 4)])
        self.assertIsNone(result["relative_l1"])

    def test_reject_actual_sample_shift_even_with_same_bounds(self):
        with self.assertRaisesRegex(ValueError, "sampling"):
            compare(self.root, [meta([1] * 4), meta([1] * 4, [-4, 6])])

    def test_reject_different_angle_convention(self):
        a = meta([1] * 4); b = copy.deepcopy(a); b[1] = 1
        with self.assertRaisesRegex(ValueError, "angle"):
            compare(self.root, [a, b])

    def test_reject_truncated_nonfinite_and_duplicate_grid(self):
        write_grid(self.root, 0, [1] * 4)
        for bad in ([1, 2, 3], [1, 2, float("nan"), 4], [1, 2, -1, 4]):
            write_grid(self.root, 1, bad)
            with self.assertRaises(ValueError): compare(self.root, [meta([1] * 4), meta([1] * 4)])
        (self.root / "grid_1.csv").write_text("u_index,v_index,intensity_cd\n0,0,1\n0,0,1\n0,1,1\n1,1,1\n")
        with self.assertRaisesRegex(ValueError, "coverage"):
            compare(self.root, [meta([1] * 4), meta([1] * 4)])


class ExtensionInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.model = self.root / "fixture.lue"; self.model.write_text('ObjectVector("fixture",());')
        self.base = {"model_path": str(self.model), "source_name": "source A", "cases": [{"flux_lm": 100}],
                     "rays": 1000, "seed": 12345, "intensity_threshold": .1, "recursion_limit": 16}

    def test_reject_unbounded_invalid_or_empty_sweeps(self):
        for change in ({"cases": []}, {"cases": [{}]}, {"cases": [{"code": 1}]}, {"seed": True},
                       {"rays": 1000.0}, {"seed": 0}, {"cases": [{"flux_lm": float("nan")}]},
                       {"cases": [{"flux_lm": 100}] * 17}, {"rays": 1000000, "cases": [{"flux_lm": 100}] * 5}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate("run_parameter_sweep", {**self.base, **change})

    def test_trim_can_be_less_than_half_angle(self):
        result = validate("set_emitter_parameters", {"model_path": str(self.model), "source_name": "source A",
                                                    "changes": {"half_angle_deg": 60, "trim_angle_deg": 30}})
        self.assertEqual(result["changes"]["trim_angle_deg"], 30)

    def test_single_case_is_an_explicit_outer_list_and_name_cannot_inject(self):
        name = 'source "quoted" @@RAYS@@'
        p = validate("run_parameter_sweep", {**self.base, "source_name": name})
        dest = self.root / "job"; dest.mkdir(); stage(dest, "run_parameter_sweep", p)
        text = (dest / "job.do").read_text()
        self.assertIn('cases << (100,-1,-1);', text)
        self.assertIn(json.dumps(name), text)
        self.assertIn('tracer.traceRays(1000,1,0,0,0)', text)

    def test_comparison_dependencies_with_same_filename_stay_distinct(self):
        files = []
        for index in range(2):
            folder = self.root / str(index); folder.mkdir()
            (folder / "same.lid").write_text(f'LIDData("fixture {index}",());')
            file = folder / "main.lid"; file.write_text('LIDFile("input","same.lid");'); files.append(file)
        p = validate("compare_photometry", dict(reference_path=str(files[0]), candidate_path=str(files[1])))
        dest = self.root / "job"; dest.mkdir(); inputs, _ = stage(dest, "compare_photometry", p)
        self.assertEqual(len({r["staged_name"] for r in inputs}), 4)
        self.assertIn('reference_asset_0001.lid', (dest / "reference.lid").read_text())
        self.assertIn('candidate_asset_0001.lid', (dest / "candidate.lid").read_text())


class EditReceiptTests(unittest.TestCase):
    def setUp(self):
        self.before = {"name": "source A", "origin_mm": [0, 0, 0], "efficacy": {"value": 1},
                       "emitter": {"name": "shared", "status": "available", "flux": {"unit": "lm", "value": 100},
                                   "half_angle_deg": {"status": "explicit", "value": 45},
                                   "trim_angle_deg": {"status": "explicit", "value": 70},
                                   "start_medium": {"name": "glass"}}}

    def test_modified_flux_with_preserved_angles(self):
        after = copy.deepcopy(self.before); after["emitter"]["flux"]["value"] = 200
        verify_edit(self.before, after, {"flux_lm": 200}, "source A")
        for mutate in (lambda r: r["origin_mm"].__setitem__(0, 1),
                       lambda r: r["emitter"]["half_angle_deg"].__setitem__("value", 60),
                       lambda r: r["emitter"]["start_medium"].__setitem__("name", "air")):
            bad = copy.deepcopy(after); mutate(bad)
            with self.assertRaises(ValueError): verify_edit(self.before, bad, {"flux_lm": 200}, "source A")

    def test_watt_input_cannot_silently_become_lumens(self):
        self.before["emitter"]["flux"]["unit"] = "W"
        with self.assertRaisesRegex(ValueError, "radiometric"):
            verify_edit(self.before, self.before, {"flux_lm": 200}, "source A")


if __name__ == "__main__": unittest.main()
