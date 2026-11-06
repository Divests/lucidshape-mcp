import json,math,struct,tempfile,unittest
from pathlib import Path
import numpy as np
from lucidshape_mcp import ray_replay

class RayReplayTest(unittest.TestCase):
    def test_rotated_sensor_and_non_square_cells_match_independent_flux(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);src=root/'source.ray'
            src.write_bytes(struct.pack('<5fIf',0,0,0,0,2,1,-1)+struct.pack('<7f',3,2,-1,1,0,0,2))
            sensor={'bounds_deg':[-10,10,-6,6],'grid_uv':[4,4],
                    'frame':{'x':[0,0,-1],'y':[0,1,0],'z':[1,0,0]}}
            p=ray_replay.prepare(str(src),0,1,1,0,sensor)
            ray_replay.stage(root,p)
            got=np.frombuffer((root/'input.ray').read_bytes()[28:],dtype='<f4')
            np.testing.assert_allclose(got,[1,2,3,0,0,1,2])
            histogram=np.load(root/'independent-flux.npy');self.assertEqual(histogram.shape,(4,4))
            self.assertEqual(histogram[2,2],2)
            h=np.array([-7.5,-2.5,2.5,7.5]);v=np.array([-4.5,-1.5,1.5,4.5])
            omega=math.radians(5)*(np.sin(np.deg2rad(v+1.5))-np.sin(np.deg2rad(v-1.5)))
            cd=histogram/omega
            ies='IESNA:LM-63-2002\nTILT=NONE\n1 -1 1 4 4 3 2 0 0 0 1 1 1\n'
            (root/'photometry.ies').write_text(ies+' '.join(map(str,[*v,*h,*cd.flatten()])),encoding='ascii')
            (root/'photometry.lid').write_bytes(b'fixture')
            (root/'engine.log').write_text('start raytrace every recorded ray exactly once with 1 rays\nstart 1 threads for source')
            result,_=ray_replay.result(root,p,[1,1,[4,4],[-10,10,-6,6],[0,float(cd.max()),2],20])
            self.assertLess(result['independent_relative_L1'],1e-12)
            self.assertEqual(result['angle_type'],'A')

    def test_bounded_slice_weight_and_physical_pitch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);src=root/'source.ray';dest=root/'job';dest.mkdir()
            a=np.array([[1,2,3,0,0,1,2],[1,2,3,0,0,1,4],[1,2,3,0,0,1,8]],dtype='<f4')
            raw=struct.pack('<5fIf',0,0,0,0,14,3,-1)+a.tobytes();src.write_bytes(raw)
            p=ray_replay.prepare(str(src),1,1,.5,-1)
            ray_replay.stage(dest,p)
            got=np.frombuffer((dest/'input.ray').read_bytes()[28:],dtype='<f4')
            self.assertEqual(got[6],2);self.assertAlmostEqual(got[4],-math.sin(math.radians(1)),places=7)
            self.assertAlmostEqual(got[5],math.cos(math.radians(1)),places=7)
            self.assertEqual(src.read_bytes(),raw)
            c=json.loads((dest/'coverage.json').read_text());self.assertEqual(c['source_stop_exclusive'],2)
            self.assertAlmostEqual(c['expected_sensor_flux_lm'],2)
            for start,count,scale,pitch in [(3,1,1,0),(0,4,1,0),(0,1,float('nan'),0),(0,1,1,6),(0,True,1,0)]:
                with self.assertRaises(ValueError):ray_replay.prepare(str(src),start,count,scale,pitch)

    def test_refuse_changed_source_before_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);src=root/'source.ray'
            src.write_bytes(struct.pack('<5fIf',0,0,0,0,1,1,-1)+struct.pack('<7f',0,0,0,0,0,1,1))
            p=ray_replay.prepare(str(src),0,1,1,0);src.write_bytes(src.read_bytes()+b'x')
            with self.assertRaises(ValueError):ray_replay.stage(root,p)
