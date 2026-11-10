"""Reject road requests/results that change the requested physical calculation."""
import copy
import csv
import json
import struct
from pathlib import Path
import tempfile
import unittest

from lucidshape_mcp.road_simulation import validate, finalize


class NativeRoadParameters(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "input.ies"
        self.source.write_text("IESNA:LM-63-2002\nTILT=NONE\n")
        self.base = dict(lid_path=str(self.source), lamp_positions_m=[[0, .756, .798], [0, -.756, .798]],
                         driver_position_m=[-1.5, .35, 1.2], road_bounds_m=[5, 6, -.5, .5],
                         road_cell_size_m=.5, road_reflectance=.15,
                         view_bounds_deg=[-1, 1, -1, 1], view_cell_size_deg=1,
                         view_angles_deg=[0, 0, 0])

    def test_zero_and_unit_reflectance_are_valid_without_mutating_inputs(self):
        for rho in (0, 1):
            request = copy.deepcopy(self.base)
            request["road_reflectance"] = rho
            before = copy.deepcopy(request)
            result = validate(request)
            self.assertEqual(result["road_reflectance"], rho)
            self.assertEqual(request, before)
            result["lamp_positions_m"][0][2] = 9
            self.assertEqual(request, before)

    def test_reject_invalid_physical_geometry_and_nonfinite_inputs(self):
        invalid = [
            {"lamp_positions_m": []},
            {"lamp_positions_m": [[0,0,.8]]*3},
            {"lamp_positions_m": [[0,0,0]]},
            {"lamp_positions_m": [[0,True,.8]]},
            {"lamp_positions_m": [[5.5,0,.8]]},
            {"driver_position_m": [-1,0,0]},
            {"driver_position_m": [-1,0,float("nan")]},
            {"road_reflectance": -0.1}, {"road_reflectance": 1.001},
            {"road_reflectance": float("inf")}, {"road_reflectance": True},
            {"view_angles_deg": [1,0,0]}, {"view_angles_deg": [0,0,1]},
            {"view_bounds_deg": []}, {"view_angles_deg": [0,0]},
            {"arbitrary_script": "run something"},
        ]
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate({**self.base, **change})

    def test_reject_ambiguous_or_unbounded_sampling(self):
        invalid = [
            {"road_bounds_m": [6,5,-.5,.5]},
            {"road_bounds_m": [5,6,.5,-.5]},
            {"road_cell_size_m": 0},
            {"road_cell_size_m": .3},
            {"road_bounds_m": [1,30,-15,15], "road_cell_size_m": .01},
            {"view_bounds_deg": [-80,80,-80,80], "view_cell_size_deg": .01},
            {"view_bounds_deg": [-81,1,-1,1]},
            {"view_cell_size_deg": float("nan")},
        ]
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate({**self.base, **change})

    def fixture(self):
        # Deliberately distinct axes and values make transpositions observable.
        return [1, [1,2,2,8.0],
                [2,2,3,3,[1,0],[5,6,-.5,.5],[5.25,5.75],[-.25,.25],[4,5.75,.25]],
                [2,2,5,3,[0,1],[-1,1,-1,1],[-.5,.5],[-.5,.5],[.04,.5,.5]], .15]

    def write_fixture(self, raw):
        (self.root / "engine-result.json").write_text(json.dumps(raw))
        # finalize validates the signature and IHDR dimensions without image decoding.
        for name, meta in (("road.png", raw[2]), ("driver.png", raw[3]), ("driver-color.png", raw[3])):
            header = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", meta[0], meta[1])
            (self.root / name).write_bytes(header)
        for name, meta, values in (("road",raw[2],[1,2,3,4]), ("driver",raw[3],[.01,.02,.03,.04])):
            with (self.root / f"{name}.csv").open("w",newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["u_index","v_index","u","v","value"])
                writer.writerows((i%2,i//2,meta[6][i%2],meta[7][i//2],val) for i,val in enumerate(values))

    def test_valid_receipt_preserves_units_and_statistics(self):
        self.write_fixture(self.fixture())
        result, _ = finalize(self.root, self.base)
        self.assertEqual(result["outputs"]["road"]["unit"], "lx")
        self.assertEqual(result["outputs"]["driver"]["unit"], "cd/m2")
        self.assertEqual(result["outputs"]["road"]["mean"], 2.5)
        self.assertAlmostEqual(result["outputs"]["driver"]["max"], .04)

    def test_reject_invalid_native_image_headers(self):
        for name, defect in (("road.png", "truncated"), ("driver.png", "signature"),
                             ("driver-color.png", "dimensions")):
            with self.subTest(name=name, defect=defect):
                self.write_fixture(self.fixture())
                path = self.root / name
                data = path.read_bytes()
                if defect == "truncated": data = data[:16]
                elif defect == "signature": data = b"not PNG!" + data[8:]
                else: data = data[:16] + struct.pack(">II", 99, 2)
                path.write_bytes(data)
                with self.assertRaisesRegex(ValueError, "PNG"):
                    finalize(self.root, self.base)

    def test_reject_peak_coordinates_that_do_not_point_to_a_maximum(self):
        raw = self.fixture()
        raw[2][8] = [4, 5.25, -.25]  # This valid cell exists, but its actual value is 1.
        self.write_fixture(raw)
        with self.assertRaises(ValueError):
            finalize(self.root, self.base)

    def test_reject_csv_data_damage(self):
        for bad in ("truncated", "duplicate", "nonfinite", "coordinate"):
            with self.subTest(bad=bad):
                self.write_fixture(self.fixture())
                path = self.root / "road.csv"
                lines = path.read_text().splitlines()
                if bad == "truncated": lines.pop()
                elif bad == "duplicate": lines[2] = lines[1]
                elif bad == "nonfinite": lines[1] = "0,0,5.25,-0.25,nan"
                else: lines[1] = "0,0,55.25,-0.25,1"
                path.write_text("\n".join(lines)+"\n")
                with self.assertRaises(ValueError): finalize(self.root, self.base)

    def test_reject_receipt_that_changes_requested_domain_or_units(self):
        for case in ("shifted_bounds", "wrong_sampling", "wrong_road_type", "wrong_driver_type",
                     "wrong_angle_type", "wrong_unit_flags", "peak_value", "peak_nan", "source_nan"):
            with self.subTest(case=case):
                raw = self.fixture()
                if case == "shifted_bounds": raw[2][5] = [10,11,-.5,.5]
                elif case == "wrong_sampling": raw[2][6] = [5.1,5.75]
                elif case == "wrong_road_type": raw[2][2] = 0
                elif case == "wrong_driver_type": raw[3][2] = 3
                elif case == "wrong_angle_type": raw[3][3] = 0
                elif case == "wrong_unit_flags": raw[2][4] = [0,1]
                elif case == "peak_value": raw[2][8][0] = 999
                elif case == "peak_nan": raw[3][8][0] = float("nan")
                else: raw[1][3] = float("nan")
                self.write_fixture(raw)
                with self.assertRaises(ValueError):
                    finalize(self.root, self.base)

