import copy
import json
from pathlib import Path
import tempfile
import unittest
from lucidshape_mcp.results import read_report


class StepInspectionTests(unittest.TestCase):
    def test_invalid_geometry_receipt_cannot_certify_region_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            valid=[3,2,1,[-1,2,-3,4,-5,6],[['AL','TrimmedSurface'],['IN','SSurface']],2,[['AL','TrimmedSurface'],['IN','SSurface']],2,[['AL','TrimmedSurface'],['IN','SSurface']]]
            def load(values):
                (root/'engine-result.json').write_text(json.dumps({'List':{f'p{i}':v for i,v in enumerate(values)}}))
                return read_report(root,'inspect_cad_geometry',{'cad_path':'source.stp','max_objects':2})
            result,files=load(valid)
            self.assertEqual(files,[])
            self.assertEqual(result['named_region_match_count'],2)
            self.assertFalse(result['optical_region_mapping_verified'])
            self.assertFalse(result['simulation_run'])
            for index,value in ((0,0),(2,3),(3,[2,1,-3,4,-5,6]),(3,[0,float('nan'),0,1,0,1]),(5,4),(6,[['AL',0]]),(7,0)):
                bad=copy.deepcopy(valid)
                bad[index]=value
                with self.assertRaises(ValueError):
                    load(bad)
