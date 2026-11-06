import copy,hashlib,json,tempfile,unittest
from pathlib import Path
from lucidshape_mcp import gpu_merge

class MergeTests(unittest.TestCase):
    def test_rejects_missing_overlapping_sampled_or_changed_batches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);jobs=[]
            for i in range(2):
                beams={}
                for g in ('low','additional'):
                    p=root/f'{g}_{i}.lid';p.write_bytes(f'{g}{i}'.encode())
                    beams[g]={'lid_file':str(p),'lid_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'flux_in_sensor_domain_lm':1}
                jobs.append({'status':'succeeded','template':'trace_s9_gpu',
                    'parameters':{'regions':[],'materials':{},'positions_mm':[[0,i,0] for i in range(12)],'coordinate_scale':1000,'rayfile_sha256':'abc','trace_mode':13,'sample':False,'rays_per_led':500,'ray_count':1000},
                    'result':{'native_GPU_execution_verified':True,'coverage':{'source_sha256':'abc','source_start':i*500,'source_stop_exclusive':(i+1)*500},'total_input_rays':6000,'beams':beams}})
            base={'status':'succeeded_provisional','canonical_requirements_unchanged':True,'jobs':jobs}
            target=root/'report.json'
            def run(report):
                target.write_text(json.dumps(report));return gpu_merge.prepare(str(target))
            p=run(base)
            self.assertEqual(p['expected_flux'],{'low':2,'high':4})
            self.assertEqual(p['total_input_rays'],12000)
            control=copy.deepcopy(base)
            for job in control['jobs']:
                job['parameters'].update(absorption_backend='zero_extra_absorption_control',zero_extra_absorption_control=True,
                    applied_extra_absorption_fraction=0,fresnel_retained=True)
                job['result'].update(zero_extra_absorption_control=True,per_complete_traversal_absorption_fraction=0)
            self.assertTrue(run(control)['zero_extra_absorption_control'])
            control['jobs'][1]['result']['per_complete_traversal_absorption_fraction']=.002
            with self.assertRaisesRegex(ValueError,'loss flags'):run(control)
            control['jobs'][1]['parameters']['absorption_backend']='native outgoing-air absorbing mixture; 0.998 survival per exit'
            with self.assertRaisesRegex(ValueError,'physics'):run(control)
            variants=[]
            b=copy.deepcopy(base);b['jobs'].pop();variants.append(b)
            b=copy.deepcopy(base);b['jobs'][1]['result']['coverage']['source_start']=0;variants.append(b)
            b=copy.deepcopy(base);b['jobs'][0]['parameters']['sample']=True;variants.append(b)
            b=copy.deepcopy(base);b['jobs'][1]['parameters']['coordinate_scale']=1;variants.append(b)
            b=copy.deepcopy(base);b['jobs'][0]['result']['native_GPU_execution_verified']=False;variants.append(b)
            for b in variants:
                with self.assertRaises(ValueError):run(b)
            rejected=copy.deepcopy(base)
            rejected['jobs'][0]['parameters']['absorption_backend']='native outgoing-air absorbing mixture; 0.998 survival per exit'
            with self.assertRaisesRegex(ValueError,'backward Fresnel'):run(rejected)
            rejected['status']='failed'
            target.write_text(json.dumps(rejected))
            with self.assertRaisesRegex(ValueError,'backward Fresnel'):
                gpu_merge.prepare(str(target),allow_incomplete=True)
            partial=copy.deepcopy(base);partial['status']='failed';partial['jobs'].pop()
            target.write_text(json.dumps(partial))
            validated=gpu_merge.prepare(str(target),allow_incomplete=True)
            self.assertFalse(validated['full_source_coverage'])
            self.assertEqual(validated['covered_rays_per_led'],500)
            (root/'low_0.lid').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'changed'):run(base)

if __name__=='__main__':unittest.main()
