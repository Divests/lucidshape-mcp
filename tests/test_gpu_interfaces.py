import json,tempfile,unittest
from pathlib import Path
from lucidshape_mcp.results import read_report

class InterfaceTests(unittest.TestCase):
    def test_controls_do_not_certify_signed_direction_or_s9(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);rows=[]
            for mode in (20,13):
                for p in range(4):
                    for side in range(2):
                        for a in (0,30):
                            flux=750 if p==1 else 1000
                            rows.append([mode,0,p,side,a,0,1,flux])
                for p in range(2):
                    for t in (10,20):rows.append([mode,1,p,0,0,t,1,1000*(.75 if p==0 else .998)**(t/10)])
            def parse():
                (root/'engine-result.json').write_text(json.dumps({'List':{'p0':rows,'p1':0}}))
                return read_report(root,'probe_gpu_interfaces',{})[0]
            (root/'engine.log').write_text('nurbs intersection type is 13 (graphics card)\n'*20)
            good=parse()
            self.assertTrue(good['controls_passed'])
            self.assertFalse(good['signed_theta_side_selective_observed'])
            self.assertFalse(good['S9_physics_accepted'])
            rows[-1][-1]=1000
            self.assertFalse(parse()['controls_passed'])
            (root/'engine.log').write_text('switching to CPU')
            self.assertFalse(parse()['native_GPU_execution_verified'])
            rows[0][2]=99
            with self.assertRaises(ValueError):parse()

if __name__=='__main__':unittest.main()
