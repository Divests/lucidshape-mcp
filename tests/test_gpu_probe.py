import json
from pathlib import Path
import tempfile
import unittest
from lucidshape_mcp.results import read_report

class GpuProbeTests(unittest.TestCase):
    def test_material_fixture_requires_all_cases_and_real_gpu(self):
        expected=[1000,0,750,562.5,1000,1000*.96/1.04,750*.96/1.04,998,996.004,1000,1000,960,998,998,1000*2*.04/1.04,1000*(.04+.75*.04*.96/1.04),40]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            rows=[[mode,i,1,flux] for mode in (20,13) for i,flux in enumerate(expected)]
            def parse():
                (root/'engine-result.json').write_text(json.dumps({'List':{'p0':rows,'p1':[[20,1,1024],[13,1,0]]}}))
                return read_report(root,'validate_gpu_loss',{})[0]
            (root/'engine.log').write_text('nurbs intersection type is 13 (graphics card)\n'*17)
            good=parse()
            self.assertTrue(good['candidate_accepted'])
            self.assertFalse(good['gpu_ray_history_supported_in_fixture'])
            rows[33][3]=30 # Entry reflection must not receive traversal absorption.
            self.assertFalse(parse()['candidate_accepted'])
            rows[33][3]=40
            rows[26][3]=750 # Incorrect loss during TIR must fail despite good slabs.
            self.assertFalse(parse()['candidate_accepted'])
            rows[26][3]=1000
            (root/'engine.log').write_text('switching to CPU')
            self.assertFalse(parse()['candidate_accepted'])
            rows.pop()
            with self.assertRaises(ValueError):parse()

    def test_device_enumeration_cannot_certify_s9_or_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def parse(values):
                (root/'engine-result.json').write_text(json.dumps({'List':{f'p{i}':v for i,v in enumerate(values)}}))
                return read_report(root,'gpu_probe',{})[0]
            empty=parse([0,-1,[],0,0,[0,0,0]])
            self.assertFalse(empty['native_trace_returned_success'])
            known=parse([1,0,[['GPU','details']],1,1,[0,100,1000]])
            self.assertTrue(known['native_trace_returned_success'])
            self.assertFalse(known['native_GPU_smoke_verified'])
            self.assertFalse(known['S9_GPU_backend_validated'])
            self.assertFalse(known['GPU_SM_utilization_verified'])
            for bad in ([0,-1,[],0,1,[0,1,1]],[1,0,[],1,1,[0,100,700]],
                [1,0,[['GPU','details']],1,1,[0,0,0]], [1,0,[['GPU','details']],1,1,[0,100,1001]]):
                with self.assertRaises(ValueError):parse(bad)
            (root/'engine.log').write_text('nurbs intersection type is 13 (graphics card)\nGPU intersection time: 0.004')
            self.assertTrue(parse([1,0,[['GPU','details']],1,1,[0,100,1000]])['native_GPU_smoke_verified'])
            (root/'engine.log').write_text('no actors found in scene, switching to accurate(NURBS) ray trace mode')
            with self.assertRaisesRegex(ValueError,'fell back'):
                parse([1,0,[['GPU','details']],1,1,[0,100,1000]])

if __name__=='__main__':unittest.main()
