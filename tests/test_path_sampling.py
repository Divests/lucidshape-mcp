import copy
import hashlib
import json
import math
from pathlib import Path
import struct
import tempfile
import unittest
from lucidshape_mcp.path_sampling import HEADER,RAY,stage_samples,read_terminals
from lucidshape_mcp.results import read_report

class PathSamplingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def test_sample_preserves_source_flux_and_applies_whole_frame(self):
        source=self.root/'source.ray'
        source.write_bytes(HEADER.pack(0,0,0,0,25,25,-1)+RAY.pack(.1,.2,.3,0,0,1,1)*25)
        params={'ray_count':25,'rays_per_led':2,'sampling_seed':42,
            'positions_mm':[[-45,i,5] for i in range(12)],'rayfile_path':str(source)}
        staged=stage_samples(self.root,params)
        before=(self.root/'sampled-rays.lue').read_bytes()
        self.assertEqual(staged['derived_sampling']['source_header_flux_per_led_lm'],25)
        self.assertEqual(staged['derived_sampling']['sampling_scale'],12.5)
        script=before.decode('ascii')
        self.assertEqual(script.count('LRay('),24)
        self.assertIn('(1,0,0),1000000,12.5,',script)
        self.assertTrue(script.startswith('#pragma NOPP\n@1='))
        self.assertTrue(script.endswith('));\n'))
        stage_samples(self.root,params)
        self.assertEqual(before,(self.root/'sampled-rays.lue').read_bytes())
        scaled=stage_samples(self.root,{**params,'coordinate_scale':10})
        after=(self.root/'sampled-rays.lue').read_text(encoding='ascii')
        import re
        original_rays=re.findall(r'LRay\("sample",\(([^)]+)\),\(([^)]+)\),1000000,([^,]+),',script)
        scaled_rays=re.findall(r'LRay\("sample",\(([^)]+)\),\(([^)]+)\),1000000,([^,]+),',after)
        self.assertEqual(len(original_rays),len(scaled_rays))
        for old,new in zip(original_rays,scaled_rays):
            for a,b in zip(map(float,old[0].split(',')),map(float,new[0].split(','))):
                self.assertAlmostEqual(a*10,b)
            self.assertEqual(old[1:],new[1:])
        self.assertEqual(staged['derived_sampling']['sample_index_sha256'],scaled['derived_sampling']['sample_index_sha256'])
        source.write_bytes(HEADER.pack(0,0,0,0,26,25,-1)+RAY.pack(.1,.2,.3,0,0,1,1)*25)
        with self.assertRaisesRegex(ValueError,'flux'):
            stage_samples(self.root,params)

    def write_terminals(self,rows):
        (self.root/'terminal-rays.json').write_text(json.dumps({'List':{'p0':rows,'p1':len(rows)}}),encoding='utf-8')
        (self.root/'sampling.json').write_text(json.dumps({'source_header_flux_per_led_lm':1}),encoding='utf-8')

    def test_native_weights_rechecked_and_beams_separated_without_rescaling(self):
        rows=[[2,70,0,0,1,0,0,2.5,1,1,2.5*.998**2],
            [9,70,0,0,1,0,0,.2,2,0,.2*.998**2]]
        self.write_terminals(rows)
        beams,counts,info,n=read_terminals(self.root,{'rays_per_led':100})
        self.assertEqual(counts,{'PC':3,'glass':1})
        self.assertEqual(beams['low']['escaped_ray_count'],1)
        self.assertEqual(beams['high']['escaped_ray_count'],2)
        self.assertAlmostEqual(beams['low']['escaped_flux_estimate_lm'],2.5*.998**2)
        data=(self.root/'low-weighted.ray').read_bytes()
        header=HEADER.unpack_from(data)
        self.assertEqual(header[5],1)
        self.assertAlmostEqual(RAY.unpack_from(data,HEADER.size)[6],2.5*.998**2,places=6)
        for index,value in ((10,2.5),(8,.5),(4,2),(0,14),(7,float('nan'))):
            bad=copy.deepcopy(rows)
            bad[0][index]=value
            self.write_terminals(bad)
            with self.assertRaises(ValueError):
                read_terminals(self.root,{'rays_per_led':100})

    def test_conditioned_terminals_restore_model_mm_without_flux_change(self):
        rows=[[2,700,20,30,1,0,0,2.5,1,1,2.5*.998**2]]
        self.write_terminals(rows)
        (self.root/'sampling.json').write_text(json.dumps({'source_header_flux_per_led_lm':1,'coordinate_scale':10}),encoding='utf-8')
        beams,counts,info,n=read_terminals(self.root,{'rays_per_led':100,'coordinate_scale':10})
        native=RAY.unpack_from((self.root/'low-weighted.ray').read_bytes(),HEADER.size)
        self.assertEqual(native[:6],(70,2,3,1,0,0))
        self.assertAlmostEqual(beams['low']['escaped_flux_estimate_lm'],2.5*.998**2)
        with self.assertRaisesRegex(ValueError,'scale differs'):
            read_terminals(self.root,{'rays_per_led':100,'coordinate_scale':100})

    def test_fixture_cannot_certify_deterministic_or_tir_attenuated_paths(self):
        forward,entered=18460,19200
        valid=[20000,forward,20000-forward,forward*.998,
            20000-forward-.002*(entered-forward),entered,800,0,0,[2,-.5,1,1.5],
            [2,math.sin(math.radians(20)),0,math.cos(math.radians(20))]]
        def run(values):
            (self.root/'engine-result.json').write_text(json.dumps({'List':{f'p{i}':v for i,v in enumerate(values)}}),encoding='utf-8')
            return read_report(self.root,'validate_traversal_absorption',{})[0]
        result=run(valid)
        self.assertTrue(result['native_stochastic_path_weighting_fixture_verified'])
        self.assertFalse(result['s9_simulation_run'])
        for i,value in ((1,20000),(6,0),(7,1),(9,[2,-.5,.998,1.5])):
            bad=copy.deepcopy(valid)
            bad[i]=value
            with self.assertRaises(ValueError):
                run(bad)

        cases=[]
        for local in range(2):
            for closed in range(2):
                for blocked in range(2):
                    escapes=not blocked or bool(local)
                    cases.append([local,closed,blocked,3 if escapes else 2,1e38 if escapes else 5,1,1 if escapes else 1.5,1])
        result=run(valid+[cases])
        self.assertTrue(result['closed_boundary_fixture'][-1]['embedded_absorber_ignored'])
        bad=copy.deepcopy(cases)
        bad[3][4]=1e38
        with self.assertRaisesRegex(ValueError,'ignored'):
            run(valid+[bad])
        bad=copy.deepcopy(cases)
        bad[0][3]=1
        with self.assertRaisesRegex(ValueError,'did not intersect'):
            run(valid+[bad])

if __name__=='__main__':
    unittest.main()
