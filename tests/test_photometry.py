import copy
import json
from pathlib import Path
import tempfile
import unittest
from lucidshape_mcp.photometry import inspect_ies
from lucidshape_mcp.results import read_report


IES='IESNA:LM-63-2002\n[TEST] MCP validation fixture, not a real lamp\nTILT=NONE\n1 -1 2 3 2 2 2 0 0 0\n1 1 0\n-2 0 2\n-1 1\n10 20 30 40 50 60\n'


class PhotometryTests(unittest.TestCase):
    def test_binning_result_must_match_requested_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            values=[400,0,[120,30],[-60,60,-20,10],[0,123,17]]
            (root/'engine-result.json').write_text(json.dumps({'List':{f'p{i}':v for i,v in enumerate(values)}}))
            args={'rayfile_path':'known.ray','grid_uv':[120,30],'angular_resolution_deg':1}
            result=read_report(root,'bin_rayfile',args)[0]
            self.assertEqual(result['angular_resolution_deg'],1)
            self.assertEqual(result['flux_in_sensor_domain_lm'],17)
            with self.assertRaisesRegex(ValueError,'grid'):
                read_report(root,'bin_rayfile',{'rayfile_path':'known.ray'})

    def test_numeric_count_scale_and_axes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'fixture.ies'
            path.write_text(IES)
            value=inspect_ies(path)
            self.assertEqual(value['peak_cd'],120)
            self.assertEqual(value['photometric_type'],'B')
            self.assertEqual(value['vertical_range_deg'],[-2,2])
            for bad in (IES+'10',IES.replace('10 20 30','10 NaN 30'),IES.replace('-2 0 2','-2 -2 2'),IES.replace('10 20 30','10 -20 30'),IES.replace('TILT=NONE','TILT=other.ies')):
                path.write_text(bad)
                with self.assertRaises(ValueError):
                    inspect_ies(path)

    def test_readback_directions_and_absolute_candela_must_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'photometry.ies').write_text(IES)
            # The native IES storage flag changes, but range() still reports cell edges.
            valid=[0,[2,3],[-2,2,-3,3],0,[20,120,1],0,[2,3],[-2,2,-3,3],1,[20,120,1],0,0]
            def load(values):
                (root/'engine-result.json').write_text(json.dumps({'List':{f'p{i}':v for i,v in enumerate(values)}}))
                return read_report(root,'export_ies',{'lid_path':'source.lid'})[0]
            result=load(valid)
            self.assertTrue(result['native_readback_verified'])
            self.assertEqual(result['all_cells_compared'],6)
            for index,value in ((5,1),(6,[3,2]),(7,[-1.1,1,-2,2]),(9,[20,121,1]),(10,0.1),(10,-1),(11,1.01),(11,-1)):
                bad=copy.deepcopy(valid)
                bad[index]=value
                with self.assertRaises(ValueError):
                    load(bad)


if __name__=='__main__':
    unittest.main()
