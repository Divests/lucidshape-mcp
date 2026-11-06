import json
from pathlib import Path
import tempfile
import unittest
from lucidshape_mcp.results import read_report


class RayFileReportTests(unittest.TestCase):
    def test_native_count_and_spectral_format_must_agree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            def report(values):
                (root/"engine-result.json").write_text(json.dumps({"List":{f"p{i}":value for i,value in enumerate(values)}}))
                return read_report(root,"inspect_rayfile",{"rayfile_path":"source.ray"})[0]
            result=report([1,0,5000000,5000000])
            self.assertFalse(result["is_spectral"])
            self.assertEqual(result["ray_count"],5000000)
            for invalid in ([1,1,100,100],[6,0,100,100],[1,0,100,99],[1,0,0,0]):
                with self.assertRaises(ValueError):
                    report(invalid)


if __name__=="__main__":
    unittest.main()
