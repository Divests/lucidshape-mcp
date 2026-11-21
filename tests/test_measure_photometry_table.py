import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lucidshape_mcp import photometry_tables as tables


class NativeMeasurementTableTests(unittest.TestCase):
    def high_beam(self):
        entries = []
        for rule in tables.rules("gb4599_2024_hb"):
            name = rule["test_id"]
            value = {"Imax": 50000, "HV": 39000}.get(name, 35000)
            low = 40000 if name == "HV" else rule["min_cd"]
            entries.append([name, value, low, 215000, int(value < low),
                            rule["positions_deg"] or [[-1, -1]], []])
        return ["gb4599_2024_hb", [-60, 60, -20, 20], [50000, -1, -1], entries]

    def test_fixed_table_and_reported_relative_failure(self):
        fields = self.high_beam()
        report, _ = tables.verify(Path("."), fields, "gb4599_2024_hb")
        self.assertEqual(len(report["rows"]), 11)
        hv = next(row for row in report["rows"] if row["test_id"] == "HV")
        self.assertEqual((hv["status"], hv["evaluated_min_cd"]), ("fail", 40000))
        self.assertFalse(report["regulatory_certification"])
        for index, invalid in ((0, "B50L"), (1, float("nan")), (2, 32000), (4, 0), (5, [[3, 4]])):
            broken = copy.deepcopy(fields)
            broken[3][1][index] = invalid
            with self.subTest(index=index), self.assertRaises(ValueError):
                tables.verify(Path("."), broken, "gb4599_2024_hb")

    def test_stage_fixed_native_program_with_coverage_guard(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source = directory / "beam.ies"
            source.write_bytes(b"IESNA snapshot")
            job = directory / "job"
            job.mkdir()
            _, outputs = tables.stage(job, {"table": "gb4599_2024_lb", "lid_path": str(source)})
            script = (job / "job.do").read_text(encoding="ascii")
            self.assertIn("table.measure(data,1,0,0,result)", script)
            self.assertIn("new LidMeasureBoundary", script)
            self.assertIn("new LidMeasure3Point", script)
            self.assertIn("new LidMeasureZoneRelativeToValueAt", script)
            self.assertNotIn("@@", script)
            self.assertIn("outside sampled grid", script)
            self.assertIn("native-measurements.txt", outputs)
            self.assertEqual((job / "input.ies").read_bytes(), source.read_bytes())
        with self.assertRaisesRegex(ValueError, "Select"):
            tables.validate({"table": "../example.lug", "lid_path": "none"})

    def test_partial_lower_field_can_fail_but_cannot_establish_pass(self):
        key = "gb4599_2024_lb"
        rules = [r for r in tables.rules(key) if r["test_id"] in ("50R", "LINE10_AND_BELOW")]
        fields = [key, [-60,60,-20,20], [40000,0,0], [
            ["50R",30000,10100,-1,0,[[1.72,-0.86]],[]],
            ["LINE10_AND_BELOW",21000,-1,24000,0,[[-4.5,-20],[2,-4]],[-1.9,-4]]]]
        with patch.object(tables, "rules", return_value=rules):
            partial = tables.verify(Path("."), fields, key)[0]["rows"][-1]
            self.assertEqual((partial["sampled_status"], partial["status"]), ("pass", "not_evaluated"))
            fields[3][1][1], fields[3][1][4] = 25000, 1
            self.assertEqual(tables.verify(Path("."), fields, key)[0]["rows"][-1]["status"], "fail")
