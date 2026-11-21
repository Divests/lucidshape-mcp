"""Native analysis previews must not change the existing numeric report contract."""
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from lucidshape_mcp.results import read_report


def png(width=4, height=3):
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))
    pixels = (b"\0" + b"\x10\x80\xff" * width) * height
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


class AnalysisBitmapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="analysis bitmap ")
        self.root = Path(self.temporary.name).resolve()
        self.parameters = {"lid_path": "source.ies", "region_deg": [-1, 1, -1, 1]}
        # Preserve the original eight-field JSONStore protocol, including regional flux.
        values = ["fixture", 1, [4, 3, 1, 1], [-2, 2, -1.5, 1.5], [250, .5, -.5], 0, 800, 25]
        report = {"List": {f"p{i}": value for i, value in enumerate(values)}}
        (self.root / "engine-result.json").write_text(json.dumps(report), encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_old_numeric_reports_remain_readable_without_preview(self):
        result, artifacts = read_report(self.root, "analyze_lid", self.parameters)
        self.assertEqual(result["peak"], {"intensity_cd": 250, "u_deg": .5, "v_deg": -.5})
        self.assertEqual(result["flux_in_data_domain_lm"], 800)
        self.assertEqual(result["region"]["flux_lm"], 25)
        self.assertNotIn("native_png_export", result)
        self.assertEqual(artifacts, [])

    def test_verified_bitmap_adds_metadata_without_changing_numeric_fields(self):
        original, _ = read_report(self.root, "analyze_lid", self.parameters)
        image = self.root / "photometry-native.png"
        data = png()
        image.write_bytes(data)
        result, artifacts = read_report(self.root, "analyze_lid", self.parameters)
        preview = result.pop("native_png_export")
        self.assertEqual(result, original)
        self.assertEqual(artifacts, [image.name])
        self.assertEqual(preview["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual([preview["width_px"], preview["height_px"]], [4, 3])
        self.assertTrue(preview["native_png_crc_and_pixel_stream_verified"])
        self.assertEqual(preview["method"], "UVData.saveUVAsBMP")
        self.assertEqual(preview["native_color_type"], 5)
        self.assertEqual(preview["range_mode"], "native_auto")
        self.assertFalse(preview["axes"])
        self.assertFalse(preview["color_bar"])

    def test_preview_dimensions_must_match_numeric_grid(self):
        (self.root / "photometry-native.png").write_bytes(png(5, 3))
        with self.assertRaisesRegex(ValueError, "PNG dimensions"):
            read_report(self.root, "analyze_lid", self.parameters)

    def test_corrupted_or_incomplete_preview_is_not_advertised(self):
        valid = png()
        corrupted = bytearray(valid)
        corrupted[45] ^= 1
        for data in (b"not a PNG", valid[:-12], bytes(corrupted)):
            with self.subTest(data=data[:12]):
                (self.root / "photometry-native.png").write_bytes(data)
                with self.assertRaisesRegex(ValueError, "PNG"):
                    read_report(self.root, "analyze_lid", self.parameters)


if __name__ == "__main__":
    unittest.main()
