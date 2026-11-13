import csv
import json
import shutil
import struct
import zlib
import tempfile
import unittest
from pathlib import Path
from lucidshape_mcp.static_road_export import validate, verify_csv, verify_camera, stage_static_road, finalize_static_road, generate_views, _png


class StaticRoadValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.source = self.directory / "source.ies"
        self.source.write_text("placeholder: only parameter validation here", encoding="utf-8")
        self.args = dict(lid_path=str(self.source), lamp_positions_m=[[0, .756, .798], [0, -.756, .798]],
                         road_bounds_m=[0, 200, -30, 30])
    def tearDown(self):
        self.temp.cleanup()
    def test_actual_height_and_camera_defaults(self):
        p = validate(self.args)
        self.assertEqual(p["driver_position_m"], [0, 0, 1.2])
        self.assertEqual(p["top_position_m"], [-25, 0, 20])
        self.assertEqual(p["illumination_type"], "radial")
        self.assertEqual(p["road_bounds_m"][0], 0)
    def test_existing_process_rejected_before_staging_or_native_action(self):
        operation = generate_views(self.directory / "must-not-exist", self.directory / "absent.exe", process_id=123)
        try:
            with self.assertRaisesRegex(ValueError, "Existing-Studio export is not supported"):
                operation.send(None)
        finally:
            operation.close()
        self.assertFalse((self.directory / "must-not-exist").exists())
    def test_reject_unsupported_quantity_and_invalid_bounds(self):
        for change in ({"illumination_type": "luminance"}, {"road_bounds_m": [0, 200.1, -30, 30]},
                       {"lamp_positions_m": [[0, 0, 0], [0, 1, 1]]}, {"road_cell_size_m": float("nan")},
                       {"driver_target_m": 0}, {"timeout_seconds": 30.5}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate({**self.args, **change})
    def test_stage_is_private_and_has_no_unresolved_tokens(self):
        job = self.directory / "private-job"
        manifest = stage_static_road(job, validate(self.args))
        script = (job / "job.lus").read_text(encoding="utf-8")
        self.assertNotIn("@@", script)
        self.assertIn("void main()", script)
        self.assertIn("snapshotBitmap()", script)
        self.assertEqual(manifest["road_z_m"], 0)
        self.assertEqual((job / "input.ies").read_bytes(), self.source.read_bytes())
        with self.assertRaises(FileExistsError):
            stage_static_road(job, validate(self.args))
    def write_csv(self, kind="valid"):
        p = self.directory / "grid.csv"
        rows = [[i, j, i * .5, -1 + j * .5, 1 + i + j * 3] for j in range(3) for i in range(3)]
        if kind == "short": rows.pop()
        if kind == "nonfinite": rows[2][4] = float("nan")
        if kind == "wrong_index": rows[2][0] = 0
        if kind == "wrong_coordinate": rows[4][2] += .1
        if kind == "negative": rows[3][4] = -1
        with p.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f); w.writerow(["u_index", "v_index", "u", "v", "value"]); w.writerows(rows)
        return p
    def test_native_csv_metrics_and_fail_closed(self):
        meta = [3, 3, 3, 3, 1, [0, 1, -1, 0], [9, 1, 0]]
        got = verify_csv(self.write_csv(), meta, [0, 1, -1, 0], .5)
        self.assertEqual(got["unit"], "radial_lx")
        self.assertEqual(got["mean"], 5)
        for kind in ("short", "nonfinite", "wrong_index", "wrong_coordinate", "negative"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                verify_csv(self.write_csv(kind), meta, [0, 1, -1, 0], .5)
        with self.assertRaises(ValueError):
            verify_csv(self.write_csv(), [3, 3, 5, 3, 0, [0, 1, -1, 0], [9, 1, 0]], [0, 1, -1, 0], .5)
        with self.assertRaises(ValueError):
            verify_csv(self.write_csv(), [3, 3, 3, 3, 1, [0, 1, -1, 0], [9, .5, 0]], [0, 1, -1, 0], .5)

    def test_native_camera_readback_must_match(self):
        good = [[0, 0, 1.2], 20, [0, 0, 1.2], [1, 0, -.06], 40, [0, 0, 1], "Perspective"]
        self.assertEqual(verify_camera(good, [0, 0, 1.2], 20)["target_forward_m"], 20)
        for index, wrong in ((2, [0, 0, 2]), (3, [1, 0, .06]), (4, 60), (6, "Ortho"),
                             (5, [0, 0, -1]), (5, [0, 1, 1]), (5, [1, 0, -.06]),
                             (5, [0, 0, 0]), (5, [float("nan"), 0, 1])):
            changed = good.copy(); changed[index] = wrong
            with self.subTest(index=index), self.assertRaises(ValueError):
                verify_camera(changed, [0, 0, 1.2], 20)
        orthogonal = good.copy(); orthogonal[5] = [.06, 0, 1]
        verify_camera(orthogonal, [0, 0, 1.2], 20)

    def test_png_requires_valid_crc_and_pixel_stream(self):
        def chunk(kind, value):
            return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value) & 0xffffffff)
        signature = b"\x89PNG\r\n\x1a\n"
        ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 128, 100, 8, 2, 0, 0, 0))
        image = self.directory / "test.png"
        raw = (b"\0" + bytes(range(128)) * 3) * 100
        valid = signature + ihdr + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
        image.write_bytes(valid)
        self.assertTrue(_png(image)["native_png_crc_and_pixel_stream_verified"])
        bad_crc = bytearray(valid); bad_crc[30] ^= 1
        bad_pixels = signature + ihdr + chunk(b"IDAT", zlib.compress(b"too short")) + chunk(b"IEND", b"")
        bad_compression = signature + ihdr + chunk(b"IDAT", b"not zlib") + chunk(b"IEND", b"")
        for broken in (bad_crc, bad_pixels, bad_compression, b"BM" + b"\0" * 2048, valid[:-8]):
            with self.subTest(kind=bytes(broken)[:20]), self.assertRaises(ValueError):
                image.write_bytes(broken); _png(image)

    def test_reject_original_source_or_script_changes(self):
        p = validate(self.args)
        directory = self.directory / "hash-test"
        staged = stage_static_road(directory, p)
        shutil.copyfile(directory / "job.lus", directory / "native-job.lus")
        receipt = [1, staged["nonce"], [], [], [], [], [], 1, 0, 1, "model", "view"]
        (directory / "native-result.json").write_text(json.dumps(receipt), encoding="utf-8")
        original = self.source.read_bytes()
        self.source.write_bytes(original + b"changed")
        with self.assertRaisesRegex(ValueError, "Original input changed"):
            finalize_static_road(directory, p)
        self.source.write_bytes(original)
        with (directory / "job.lus").open("a", encoding="utf-8") as stream:
            stream.write("\n// altered after launch\n")
        with self.assertRaisesRegex(ValueError, "Native job script changed"):
            finalize_static_road(directory, p)


if __name__ == "__main__":
    unittest.main()
