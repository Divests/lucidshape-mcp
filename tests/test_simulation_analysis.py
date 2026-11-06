import math
import json
from pathlib import Path
import tempfile
import unittest
from lucidshape_mcp.path_sampling import write_ray_file
from lucidshape_mcp.simulation_analysis import read_rays,angular_histogram,sample_cd,road_lux,pool_traces,sha

class SimulationAnalysisTests(unittest.TestCase):
    def test_pooling_preserves_source_power_and_rejects_stale_or_duplicate_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);reports=[]
            for k,n,flux in ((1,100,10),(2,300,20)):
                ray=root/f'{k}.ray';write_ray_file(ray,[(0,0,0,1,0,0,flux)])
                info={'weighted_rayfile':str(ray),'escaped_ray_count':1,'escaped_flux_estimate_lm':flux,'sha256':sha(ray)}
                result={'history_weights_independently_verified':True,'weighted_rayfile_position_unit':'original model mm',
                    'sampling':{'sample_index_sha256':str(k),'sampling_seed':k},'coordinate_scale':1000,
                    'rays_per_led':n,'total_input_rays':n*12,'beams':{'low':info,'high':info}}
                r={'requirements_sha256':'requirements','effective_requirements_sha256':'effective',
                    'canonical_requirements_modified':False,'trace_job':{'status':'succeeded','template':'trace_s9_paths',
                        'job_id':str(k),'result':result,'parameters':{'regions':[{'label':'IN','sha256':'same-geometry'}]}}}
                path=root/f'{k}.json';path.write_text(json.dumps(r));reports.append(path)
            pooled=pool_traces(reports,root/'pooled')
            self.assertEqual(pooled['total_input_rays'],4800)
            self.assertAlmostEqual(pooled['beams']['low']['escaped_flux_lm'],17.5)
            with self.assertRaisesRegex(ValueError,'Duplicate'):
                pool_traces([reports[0],reports[0]],root/'duplicate')
            r=json.loads(reports[1].read_text());r['trace_job']['parameters']['regions'][0]['sha256']='changed'
            reports[1].write_text(json.dumps(r))
            with self.assertRaisesRegex(ValueError,'mismatch'):
                pool_traces(reports,root/'changed')
            r['trace_job']['parameters']['regions'][0]['sha256']='same-geometry';reports[1].write_text(json.dumps(r))
            (root/'2.ray').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'artifact changed'):
                pool_traces(reports,root/'stale')

    def test_ray_flux_and_direction_mapping(self):
        h,v=math.radians(15.25),math.radians(-2.25)
        row=(0,0,0,math.cos(h)*math.cos(v),-math.sin(h),math.cos(h)*math.sin(v),7)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'x.ray';write_ray_file(path,[row])
            rows,flux=read_rays(path)
            hist=angular_histogram(rows,1)
            k=75*30+17
            self.assertEqual(hist['counts'][k],1)
            self.assertAlmostEqual(flux,7)
            self.assertAlmostEqual(sum(hist['flux']),7)
            self.assertAlmostEqual(hist['cd'][k]*hist['solid_angle_sr'][k],7)

    def test_isotropic_lamps_follow_inverse_square_and_cosine(self):
        field={'h':[-60,60],'v':[-20,10],'cd':[1000]*4}
        for x in (10,50,100):
            expected=sum(1000*.798/(x*x+.756**2+.798**2)**1.5 for _ in range(2))
            self.assertAlmostEqual(road_lux(field,x,0),expected,places=12)

    def test_pitch_places_optical_axis_on_road(self):
        field={'h':[-1,0,1],'v':[-1,0,1],'cd':[0,0,0,0,1000,0,0,0,0]}
        x=.798/.01;r=math.hypot(x,.798)
        self.assertAlmostEqual(road_lux(field,x,0,offsets=(0,)),1000*.798/r**3,places=10)
        self.assertIsNone(sample_cd(field,2,0))

if __name__=='__main__':unittest.main()
