"""Non-project fixtures prove configurable counts, frames, materials and modes."""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lucidshape_mcp import optical_scene, path_sampling, gpu_trace, gpu_merge, kernel_regions, boundary_paths
from lucidshape_mcp.engine import BatchEngine, TEMPLATES
from lucidshape_mcp.results import read_report
from lucidshape_mcp.server import create_server
from lucidshape_mcp.catia import parameters as catia_parameters

IDENTITY={'x':[1,0,0],'y':[0,1,0],'z':[0,0,1]}
ROTATED={'x':[0,0,-1],'y':[0,1,0],'z':[1,0,0]}


class SceneFixture:
    def make(self, root):
        self.root=Path(root)
        ray=self.root/'source.ray'
        ray.write_bytes(path_sampling.HEADER.pack(0,0,0,0,200,200,-1)+path_sampling.RAY.pack(1,2,3,0,0,1,1)*200)
        self.manifest={'regions':[]}
        for name in ('cover_optic','secondary_optic','housing','reference_only'):
            path=self.root/(name+'.step');path.write_bytes(name.encode())
            self.manifest['regions'].append({'label':name,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'cad_face_count':1,'native_top_level_object_count':1,'native_import_verified':True,
                'include_in_optical_scene':name!='reference_only'})
        self.config={'schema_version':'optical-scene/1','name':'synthetic_project','length_unit':'mm',
            'materials':{'polymer':{'kind':'refractive','index':1.5,'traversal_absorption_fraction':.1},
                         'glass':{'kind':'refractive','index':1.7,'traversal_absorption_fraction':.03},
                         'black':{'kind':'absorber'}},
            'region_materials':{'cover_optic':'polymer','secondary_optic':'glass','housing':'black'},
            'rayfile':{'path':str(ray),'sha256':hashlib.sha256(ray.read_bytes()).hexdigest(),'ray_count':200,'flux_lm':200},
            'sources':[{'id':'left','position_mm':[10,20,30],'frame':copy.deepcopy(IDENTITY)},
                       {'id':'rotated','position_mm':[-10,0,5],'frame':copy.deepcopy(ROTATED)}],
            'modes':{'marker':['left'],'boost':['left','rotated']},
            'sensor':{'bounds_deg':[-10,10,-5,5],'grid_uv':[20,10],'frame':copy.deepcopy(IDENTITY)}}
        self.scene_path=self.root/'scene.json';self.manifest_path=self.root/'geometry.json'
        self.save()

    def save(self):
        self.scene_path.write_text(json.dumps(self.config),encoding='utf-8')
        self.manifest_path.write_text(json.dumps(self.manifest),encoding='utf-8')

    def prepare(self, emitters=False):
        self.save()
        return optical_scene.prepare(str(self.scene_path),str(self.manifest_path),emitters)


class OpticalSceneTests(SceneFixture, unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.make(temp.name)

    def test_arbitrary_region_names_source_count_modes_and_frames(self):
        p=self.prepare(True)
        self.assertEqual(len(p['regions']),3)
        self.assertEqual(len(optical_scene.sources(p)),2)
        self.assertEqual(optical_scene.modes(p),self.config['modes'])
        script=optical_scene.render((TEMPLATES/'preflight_optical_scene.do').read_text(),p)
        self.assertNotIn('@@',script)
        self.assertIn('addSource(2,(-10,0,5),(1,0,0),(0,0,-1))',script)
        self.assertIn('0,20,10,(-10,10,-5,5)',script)
        self.assertNotIn('reference_only',script)

    def test_single_source_does_not_confuse_ray_group_with_scene_root(self):
        self.config['sources']=self.config['sources'][:1]
        self.config['modes']={'only':['left']}
        p=self.prepare();p.update(rays_per_led=100,sampling_seed=1)
        script=path_sampling.render((TEMPLATES/'trace_optical_paths.do').read_text(),p)
        self.assertIn('root.size()==1',script)
        self.assertIn('sampled.size()!=1',script)
        out=self.root/'single';out.mkdir();path_sampling.stage_samples(out,p)
        self.assertEqual((out/'sampled-rays.lue').read_text().count('LRay('),100)

    def test_sampling_applies_arbitrary_rotation_and_keeps_flux(self):
        p=self.prepare();p.update(rays_per_led=100,sampling_seed=7)
        out=self.root/'samples';out.mkdir()
        path_sampling.stage_samples(out,p)
        text=(out/'sampled-rays.lue').read_text()
        self.assertIn('(11,22,33),(0,0,1),1000000,2,',text)
        self.assertIn('(-7,2,4),(1,0,0),1000000,2,',text)
        self.assertEqual(text.count('LRay('),200)
        setup=optical_scene.condition_setup((TEMPLATES/'preflight_optical_scene.do').read_text().split('object data=LIDData')[0],1)
        script=path_sampling.render((TEMPLATES/'trace_optical_paths.do').read_text().replace('@@SCENE_SETUP@@',setup),p)
        script=optical_scene.render(script,p)
        self.assertNotIn('@@',script)
        self.assertIn('sampled.size()!=2',script)
        self.assertIn('entries1',script)
        self.assertIn('direct medium transition unsupported',script)
        self.assertIn('weight=weight*0.90000000000000002',script)

    def test_terminal_weights_and_output_modes_use_configured_materials(self):
        p=self.prepare();p.update(rays_per_led=100,sampling_seed=7)
        out=self.root/'results';out.mkdir();path_sampling.stage_samples(out,p)
        terminal=[[1,0,0,5,0,0,1,2,1,2,2*.9*.97**2], [2,0,0,5,1,0,0,2,2,0,2*.9**2]]
        (out/'terminal-rays.json').write_text(json.dumps({'List':{'p0':terminal,'p1':2}}))
        regions=[[r['label'],r['faces'],0,optical_scene.MATERIAL_CLASSES[p['materials'][r['label']]['kind']]] for r in p['regions']]
        values=[regions,[[1,100,1,99,1,2,100],[2,100,1,99,2,0,100]],2]
        (out/'engine-result.json').write_text(json.dumps({'List':{f'p{i}':value for i,value in enumerate(values)}}))
        result,files=read_report(out,'trace_optical_paths',p)
        self.assertEqual(result['total_input_rays'],200)
        self.assertEqual(result['material_traversal_absorption'],{'polymer':.1,'glass':.03})
        self.assertEqual(set(files),{'marker-weighted.ray','boost-weighted.ray'})
        self.assertEqual(result['beams']['marker']['escaped_ray_count'],1)
        self.assertEqual(result['beams']['boost']['escaped_ray_count'],2)
        self.assertAlmostEqual(result['beams']['boost']['escaped_flux_estimate_lm'],2*.9*.97**2+2*.9**2)
        terminal[0][-1]=2*.998**3
        (out/'terminal-rays.json').write_text(json.dumps({'List':{'p0':terminal,'p1':2}}))
        with self.assertRaisesRegex(ValueError,'loss differs'):
            path_sampling.read_terminals(out,p)

    def test_gpu_modes_frames_grid_and_native_count_receipts(self):
        p=gpu_trace.prepare(str(self.scene_path),str(self.manifest_path),100,0,False,9,False,True)
        out=self.root/'gpu';out.mkdir();gpu_trace.stage(out,p)
        script=gpu_trace.render((TEMPLATES/'trace_optical_gpu.do').read_text(),p,TEMPLATES)
        self.assertNotIn('@@',script)
        self.assertIn('object groupSizes=(1,2)',script)
        self.assertIn('object groupNames=("marker","boost")',script)
        self.assertIn('0,20,10,(-10,10,-5,5)',script)
        self.assertEqual(script.count('if(group=='),3)
        for name in ('marker','boost'):(out/(name+'.lid')).write_bytes(b'fixture')
        log='nurbs intersection type is 13 (graphics card)\nGPU intersection time\n'*2
        log+='\n'.join(f'source[{i}]: "LED axis {i}" with 100 rays' for i in (1,1,2))
        (out/'engine.log').write_text(log)
        rows=[[r['label'],r['faces'],0,optical_scene.MATERIAL_CLASSES[p['materials'][r['label']]['kind']]] for r in p['regions']]
        bounds=[[r['label'],[0,1,0,1,0,1]] for r in p['regions']]
        result,files=gpu_trace.read_result(out,p,[rows,bounds,100,[[0,100,0,1,1],[1,200,0,1,2]],13])
        self.assertEqual(result['total_input_rays'],300)
        self.assertEqual(result['source_count'],2)
        self.assertEqual(set(result['beams']),{'marker','boost'})
        self.assertIn('boost.lid',files)
        self.assertTrue(result['zero_extra_absorption_control'])
        self.assertFalse(result['matches_approved_fixed_traversal_model'])
        with self.assertRaisesRegex(ValueError,'entry Fresnel'):
            gpu_trace.prepare(str(self.scene_path),str(self.manifest_path),100,0,False,9,False)

    def test_merge_uses_declared_modes_coverage_and_configured_sensor(self):
        p=gpu_trace.prepare(str(self.scene_path),str(self.manifest_path),100,0,False,9,False,True)
        jobs=[]
        for batch in range(2):
            beams={}
            for mode in ('marker','boost'):
                path=self.root/f'{mode}-{batch}.lid';path.write_bytes(f'{mode}-{batch}'.encode())
                beams[mode]={'lid_file':str(path),'lid_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'flux_in_sensor_domain_lm':2}
            jobs.append({'status':'succeeded','template':'trace_optical_gpu','parameters':copy.deepcopy(p),
                         'result':{'native_GPU_execution_verified':True,'zero_extra_absorption_control':True,
                                   'per_complete_traversal_absorption_fraction':0,'total_input_rays':300,'beams':beams,
                                   'coverage':{'source_sha256':p['rayfile_sha256'],'source_start':batch*100,'source_stop_exclusive':(batch+1)*100}}})
        report=self.root/'batches.json'
        report.write_text(json.dumps({'status':'succeeded_provisional','canonical_requirements_unchanged':True,'jobs':jobs}))
        merged=gpu_merge.prepare(str(report))
        self.assertEqual(merged['expected_flux'],{'marker':4,'boost':4})
        self.assertEqual(merged['input_rays_by_mode'],{'marker':200,'boost':400})
        script=gpu_merge.render((TEMPLATES/'merge_optical_batches.do').read_text(),merged)
        self.assertNotIn('@@',script)
        self.assertIn('("marker","boost")',script)
        self.assertIn('20,10',script)
        for mode in ('marker','boost'):(self.root/(mode+'.lid')).write_bytes(b'fixture merged')
        result,_=gpu_merge.read_result(self.root,merged,[[['marker',4],['boost',4]],0])
        self.assertEqual(result['total_input_rays'],600)
        self.assertTrue(result['full_source_coverage'])
        report.write_text(json.dumps({'status':'failed','canonical_requirements_unchanged':True,'jobs':jobs[:1]}))
        partial=gpu_merge.prepare(str(report),allow_incomplete=True)
        result,_=gpu_merge.read_result(self.root,partial,[[['marker',2],['boost',2]],0])
        self.assertFalse(result['full_source_coverage'])
        jobs[0]['parameters']['sensor']['grid_uv']=[10,20]
        report.write_text(json.dumps({'status':'succeeded_provisional','canonical_requirements_unchanged':True,'jobs':jobs}))
        with self.assertRaisesRegex(ValueError,'physics'):gpu_merge.prepare(str(report))

    def test_boundary_replay_isolates_explicit_refractive_region(self):
        failure=self.root/'failure.json'
        failure.write_text(json.dumps({'List':{'p0':1,'p1':0,'p7':[[[0,0,0],[0,0,1],1,1,1.5]]}}))
        p=boundary_paths.prepare(str(self.scene_path),str(self.manifest_path),str(failure),[0],1,
                                 boundary_region='cover_optic',region_labels=['cover_optic','housing'])
        self.assertEqual([r['label'] for r in p['regions']],['cover_optic','housing'])
        script=boundary_paths.render((TEMPLATES/'replay_optical_boundary_paths.do').read_text(),p)
        self.assertNotIn('@@',script)
        self.assertIn('object replayLens=Medium("cover_optic",1.5,0)',script)
        result=boundary_paths.read_result([[['cover_optic',1],['housing',1]],[[0,0,[
            [[0,0,.05],[0,0,1],1,1,1.5],[[0,0,1.05],[0,0,1],1e38,1,1]]]]],p)
        self.assertEqual(result['inconsistent_escaping_paths'],0)
        self.assertEqual(result['paths'][0]['lens_exits'],1)
        self.assertNotIn('PC_exits',result['paths'][0])
        with self.assertRaisesRegex(ValueError,'one refractive medium'):
            boundary_paths.prepare(str(self.scene_path),str(self.manifest_path),str(failure),[0],1,
                                   boundary_region='cover_optic',region_labels=['cover_optic','secondary_optic'])

    def test_schema_rejects_ambiguous_frames_bindings_names_and_media(self):
        original=copy.deepcopy(self.config)
        cases=[]
        c=copy.deepcopy(original);c['sources'][0]['frame']['x']=[-1,0,0];cases.append(c)
        c=copy.deepcopy(original);c['sources'][0]['id']='x");evil(';cases.append(c)
        c=copy.deepcopy(original);c['region_materials']['reference_only']='black';cases.append(c)
        c=copy.deepcopy(original);c['modes']['marker']=['missing'];cases.append(c)
        c=copy.deepcopy(original);c['materials']['polymer']['traversal_absorption_fraction']=float('nan');cases.append(c)
        c=copy.deepcopy(original);c['sensor']['grid_uv']=[4096,4096];cases.append(c)
        c=copy.deepcopy(original);c['length_unit']='inch';cases.append(c)
        c=copy.deepcopy(original);c['unknown_setting']=1;cases.append(c)
        c=copy.deepcopy(original);c['rayfile']['flux_lm']=300;cases.append(c)
        c=copy.deepcopy(original);c['rayfile']['ray_count']=100;cases.append(c)
        c=copy.deepcopy(original);c['sensor']['grid_uv']=[1,10];cases.append(c)
        for config in cases:
            self.config=config
            with self.assertRaises(ValueError):self.prepare()
        self.config=original;self.config['materials']['glass']['index']=1.5
        with self.assertRaisesRegex(ValueError,'cannot distinguish'):
            optical_scene.validate_history_media(self.prepare())

    def test_geometry_tools_accept_named_selection_and_body(self):
        p=kernel_regions.prepare(str(self.manifest_path),[],region_labels=['housing'])
        self.assertEqual([r['label'] for r in p['regions']],['housing'])
        self.assertIsNone(p['region_roles'])
        p=kernel_regions.prepare(str(self.manifest_path),[],region_roles={
            'reference':'reference_only','coating':'housing','transmitting':'cover_optic'})
        self.assertEqual(p['region_roles']['transmitting'],'cover_optic')
        part=self.root/'fixture.CATPart';part.write_bytes(b'not opened')
        self.assertEqual(catia_parameters('catia_export_body_stl',catia_path=str(part),body_name='Arbitrary body')['body_name'],'Arbitrary body')
        with self.assertRaises(ValueError):catia_parameters('catia_export_body_stl',catia_path=str(part),body_name='')

    def test_three_media_and_air_only_histories_have_dynamic_width(self):
        self.config['materials']['black']={'kind':'refractive','index':1.9,'traversal_absorption_fraction':.2}
        p=self.prepare();p.update(rays_per_led=100,sampling_seed=8)
        out=self.root/'three_media';out.mkdir();path_sampling.stage_samples(out,p)
        rows=[[1,0,0,2,0,0,1,2,1,2,3,2*.9*.97**2*.8**3],
              [2,0,0,2,0,0,1,2,0,0,0,2]]
        (out/'terminal-rays.json').write_text(json.dumps({'List':{'p0':rows,'p1':2}}))
        _,counts,_,_=path_sampling.read_terminals(out,p)
        self.assertEqual(counts,{'polymer':1,'glass':2,'black':3})
        script=path_sampling.render((TEMPLATES/'trace_optical_paths.do').read_text(),p)
        self.assertIn('entries2',script)
        self.assertNotIn('entries3',script)
        self.config['materials']={name:{'kind':'absorber'} for name in self.config['materials']}
        p=self.prepare();p.update(rays_per_led=100,sampling_seed=8)
        path_sampling.stage_samples(out,p)
        rows=[[i,0,0,2,0,0,1,2,2] for i in (1,2)]
        (out/'terminal-rays.json').write_text(json.dumps({'List':{'p0':rows,'p1':2}}))
        self.assertEqual(path_sampling.read_terminals(out,p)[1],{})

    def test_occt_uses_arbitrary_partition_names_and_no_partition_inspection(self):
        from lucidshape_mcp.config import configure_geometry_runtime
        configure_geometry_runtime()
        try:
            from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
            from OCP.BRep import BRep_Builder
            from OCP.TopoDS import TopoDS_Compound
            from OCP.TopAbs import TopAbs_FACE
            from OCP.TopExp import TopExp_Explorer
            from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
            from OCP.IFSelect import IFSelect_RetDone
        except ImportError:
            self.skipTest('optional geometry dependency unavailable')
        shape=BRepPrimAPI_MakeBox(1,2,3).Shape();faces=[];explorer=TopExp_Explorer(shape,TopAbs_FACE)
        while explorer.More():faces.append(explorer.Current());explorer.Next()
        compounds=[]
        for selected in (faces[:1],faces[1:]):
            builder=BRep_Builder();compound=TopoDS_Compound();builder.MakeCompound(compound)
            for face in selected:builder.Add(compound,face)
            compounds.append(compound)
        self.manifest={'regions':[]}
        for name,item in zip(('solid_original','reflector_piece','clear_piece'),(shape,*compounds)):
            path=self.root/(name+'.step');writer=STEPControl_Writer()
            self.assertEqual(writer.Transfer(item,STEPControl_AsIs),IFSelect_RetDone)
            self.assertEqual(writer.Write(str(path)),IFSelect_RetDone)
            self.manifest['regions'].append({'label':name,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        self.save()
        for action in ('inspect_optical_region_topology','prepare_optical_regions'):
            roles={'reference':'solid_original','coating':'reflector_piece','transmitting':'clear_piece'} if action.startswith('prepare') else None
            params=kernel_regions.prepare(str(self.manifest_path),[],region_labels=None if roles else ['solid_original'],region_roles=roles)
            if roles:params.update(geometry_representation='step',mesh_deflection_mm=.001,mesh_angle_deg=.05)
            out=self.root/action;out.mkdir();kernel_regions.stage(out,params)
            request=out/'request.json';request.write_text(json.dumps({'action':action,'parameters':params}))
            process=subprocess.run([sys.executable,'-B','-m','lucidshape_mcp.kernel_worker',str(request)],
                cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.assertEqual(process.returncode,0,process.stderr+process.stdout[-2000:])
            result,files=read_report(out,action,params)
            if roles:
                self.assertEqual(set(result['prepared_regions']),{'reflector_piece','clear_piece'})
                self.assertIn('reflector_piece-prepared.stp',files)
            else:
                self.assertEqual(set(result['regions']),{'solid_original'})
                self.assertEqual(result['regions']['solid_original']['faces'],6)


class GeneralizedProtocolTests(SceneFixture, unittest.IsolatedAsyncioTestCase):
    async def test_binning_and_replay_keep_explicit_sensor_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            self.make(folder)
            engine=BatchEngine(self.root/'jobs',(sys.executable,str(Path(__file__).with_name('mock_engine.py')),'missing'))
            try:
                for operation in ('bin_rayfile','replay_rayfile_batch'):
                    job=await engine.submit(operation,rayfile_path=self.config['rayfile']['path'],rays=100,
                                            sensor_config=self.config['sensor'])
                    script=(engine.root/job['job_id']/'job.do').read_text()
                    self.assertNotIn('@@',script)
                    self.assertIn('20,10,(-10,10,-5,5)',script)
                    self.assertEqual(job['parameters']['grid_uv'],[20,10])
            finally:
                await engine.close()

    async def test_canonical_and_legacy_argument_contracts_submit_same_operation(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{
                'LUCIDSHAPE_TOOLSETS':'all','LUCIDSHAPE_TOOLS':'','LUCIDSHAPE_READ_ONLY':'false',
                'LUCIDSHAPE_ALLOWED_ROOTS':'[]'}):
            self.make(folder)
            engine=BatchEngine(self.root/'jobs',(sys.executable,str(Path(__file__).with_name('mock_engine.py')),'missing'))
            server=create_server(engine)
            try:
                tools={t.name:t for t in await server.list_tools()}
                self.assertIn('scene_path',tools['trace_optical_paths'].inputSchema['properties'])
                self.assertIn('requirements_path',tools['trace_s9_paths'].inputSchema['properties'])
                self.assertIn('rays_per_led',tools['trace_s9_paths'].inputSchema['properties'])
                common={'region_manifest_path':str(self.manifest_path)}
                _,new=await server.call_tool('trace_optical_paths',{**common,'scene_path':str(self.scene_path),'rays_per_source':100})
                _,old=await server.call_tool('trace_s9_paths',{**common,'requirements_path':str(self.scene_path),'rays_per_led':100})
                self.assertEqual(new['template'],old['template'])
                self.assertEqual(new['template'],'trace_optical_paths')
                for job in (new,old):
                    script=(engine.root/job['job_id']/'job.do').read_text()
                    self.assertNotIn('@@',script)
                    self.assertIn('sampled.size()!=2',script)
            finally:
                await engine.close()


if __name__=='__main__':unittest.main()
