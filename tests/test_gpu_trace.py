import hashlib,json,struct,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from lucidshape_mcp import gpu_s9
from lucidshape_mcp.path_sampling import HEADER,RAY

class GpuTraceTests(unittest.TestCase):
    def test_zero_loss_control_requires_opt_in_and_preserves_requirements(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'requirements.json'
            source.write_text(json.dumps({name:{'absorption_fraction':.002,'absorption_definition_confirmed':True}
                for name in ('main_lens','inner_lens')}))
            before=source.read_bytes()
            with patch.object(gpu_s9.s9_scene,'prepare',return_value={'ray_count':5000000}):
                p=gpu_s9.prepare(str(source),'manifest',10000,0,True,20261021,False,True)
                self.assertEqual(p['absorption_backend'],gpu_s9.CONTROL_BACKEND)
                self.assertFalse(p['absorption_applied'])
                self.assertEqual(p['applied_extra_absorption_fraction'],0)
                self.assertTrue(p['fresnel_retained'])
                self.assertFalse(p['matches_approved_fixed_traversal_model'])
                self.assertEqual(source.read_bytes(),before)
                with self.assertRaisesRegex(ValueError,'entry Fresnel'):
                    gpu_s9.prepare(str(source),'manifest',10000,0,True,20261021,False)

    def test_rejected_material_cannot_launch_another_scene(self):
        with self.assertRaisesRegex(ValueError,'entry Fresnel'):
            gpu_s9.prepare('unused','unused',100,0,False,1,False)

    def test_disjoint_batches_preserve_flux_positions_and_absolute_weights(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'original.ray'
            payload=b''.join(RAY.pack(i*.01,2,3,0,0,1,.125) for i in range(1000))
            source.write_bytes(HEADER.pack(0,0,0,0,125,1000,-1)+payload)
            original=source.read_bytes();reports=[]
            for start in (0,500):
                out=root/str(start);out.mkdir()
                p={'rayfile_path':str(source),'rayfile_sha256':hashlib.sha256(original).hexdigest(),
                   'ray_count':1000,'rays_per_led':500,'source_start':start,'sample':False,'sampling_seed':1,'coordinate_scale':1000}
                with patch.object(gpu_s9.s9_scene,'stage',return_value=[]):gpu_s9.stage(out,p)
                info=json.loads((out/'coverage.json').read_text());reports.append(info)
                raw=(out/'source.ray').read_bytes()
                self.assertEqual(HEADER.unpack_from(raw)[4:6],(62.5,500))
                x,y,z,dx,dy,dz,w=RAY.unpack_from(raw,28)
                self.assertAlmostEqual(x,start*10,places=3)
                self.assertEqual((y,z,dx,dy,dz,w),(2000,3000,0,0,1,.125))
            self.assertEqual(sum(r['batch_flux_per_led_lm'] for r in reports),125)
            self.assertEqual(reports[0]['source_stop_exclusive'],reports[1]['source_start'])
            self.assertNotEqual(reports[0]['index_sha256'],reports[1]['index_sha256'])
            self.assertEqual(source.read_bytes(),original)
            modified=bytearray(original);struct.pack_into('<f',modified,28+24,.25);source.write_bytes(modified)
            with patch.object(gpu_s9.s9_scene,'stage',return_value=[]),self.assertRaisesRegex(ValueError,'changed'):
                gpu_s9.stage(root,p)

    def test_result_rejects_fallback_source_count_and_flux_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            labels=['main_lens','bracket','heat_sink','heat_shield','pcba','AL','IN']
            p={'trace_mode':13,'coordinate_scale':1000,'rays_per_led':100,'source_start':0,'sample':False,
               'regions':[{'label':x,'faces':1} for x in labels],
               'materials':{x:{'kind':'specular'} for x in labels}}
            rows=[[x,1,0,'ComboActor' if x in ('main_lens','IN') else 'Specular'] for x in labels]
            values=[rows,[],1,[[0,700,0,100,4],[1,500,0,100,3]],13]
            (root/'coverage.json').write_text(json.dumps({'batch_flux_per_led_lm':1,'source_count':1000}))
            for name in ('low','additional'):(root/(name+'.lid')).write_bytes(b'fixture')
            log='nurbs intersection type is 13 (graphics card)\nGPU intersection time\n'*2
            log+='\n'.join(f'source[{i}]: "LED axis {i}" with 100 rays' for i in range(2,14))
            (root/'engine.log').write_text(log)
            with patch.object(gpu_s9.s9_scene,'validate_conditioned_bounds'):
                self.assertTrue(gpu_s9.read_result(root,p,values)[0]['native_GPU_execution_verified'])
                self.assertFalse(gpu_s9.read_result(root,p,values)[0]['full_source_coverage'])
                p.update(absorption_backend=gpu_s9.CONTROL_BACKEND)
                for label in ('main_lens','IN'):p['materials'][label]['kind']='refractive'
                for row in rows:
                    if row[0] in ('main_lens','IN'):row[3]='Refractive'
                control=gpu_s9.read_result(root,p,values)[0]
                self.assertTrue(control['zero_extra_absorption_control'])
                self.assertEqual(control['per_complete_traversal_absorption_fraction'],0)
                self.assertFalse(control['matches_approved_fixed_traversal_model'])
                rows[0][3]='ComboActor'
                with self.assertRaisesRegex(ValueError,'material differs'):gpu_s9.read_result(root,p,values)
                rows[0][3]='Refractive'
                values[2]=1.01
                with self.assertRaisesRegex(ValueError,'normalization'):gpu_s9.read_result(root,p,values)
                values[2]=1
                (root/'engine.log').write_text(log+'\nswitching to CPU')
                with self.assertRaisesRegex(ValueError,'fell back'):gpu_s9.read_result(root,p,values)
                (root/'engine.log').write_text(log.replace('with 100 rays','with 99 rays',1))
                with self.assertRaisesRegex(ValueError,'launch counts'):gpu_s9.read_result(root,p,values)

if __name__=='__main__':unittest.main()
