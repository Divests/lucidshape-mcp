import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from lucidshape_mcp import kernel_regions
from lucidshape_mcp.results import read_report

class KernelRegionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.manifest=self.root/'manifest.json'
        regions=[]
        for label in kernel_regions.LABELS:
            path=self.root/(label+'.stp')
            path.write_bytes(label.encode())
            regions.append({'label':label,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        self.manifest.write_text(json.dumps({'regions':regions}),encoding='utf-8')

    def test_topology_snapshot_requires_exact_region_hash_and_finite_points(self):
        params=kernel_regions.prepare(str(self.manifest),[[1,2,3]])
        target=self.root/'job'
        target.mkdir()
        self.assertEqual(len(kernel_regions.stage(target,params)),4)
        for point in ([1,2,float('nan')],[True,2,3],[1,2]):
            with self.assertRaises(ValueError):
                kernel_regions.prepare(str(self.manifest),[point])
        Path(params['regions'][0]['source']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'differs'):
            kernel_regions.stage(target,params)

    def test_topology_diagnosis_does_not_require_false_geometry_success(self):
        params=kernel_regions.prepare(str(self.manifest),[])
        raw={'backend':'independent OCCT topology inspection','read_only':True,'source_geometry_modified':False,
            'regions':{r['label']:{'source_sha256':r['sha256'],'faces':2,'valid':True,'solids':0,'shells':1} for r in params['regions']},
            'IN_sewing':{'free_edges':46,'multiple_edges':0,'faces':2},
            'AL_IN_sewing':{'free_edges':0,'multiple_edges':0,'faces':4}}
        def run(value,kind='inspect_optical_region_topology'):
            (self.root/'engine-result.json').write_text(json.dumps(value),encoding='utf-8')
            return read_report(self.root,kind,params)
        result,files=run(raw)
        self.assertEqual(result['IN_sewing']['free_edges'],46)
        self.assertEqual(files,[])
        for key,value in (('source_geometry_modified',True),('read_only',False)):
            bad=copy.deepcopy(raw)
            bad[key]=value
            with self.assertRaises(ValueError):
                run(bad)
        bad=copy.deepcopy(raw)
        bad['regions']['AL']['source_sha256']='wrong'
        with self.assertRaises(ValueError):
            run(bad)

    def test_prepared_report_resolves_job_directory_and_rejects_outside_outputs(self):
        params=kernel_regions.prepare(str(self.manifest),[])
        params['geometry_representation']='step'
        job=self.root/'job';job.mkdir()
        (job/'alias').mkdir()
        alias=job/'alias'/'..'
        canonical=job.resolve()
        raw={'backend':'independent OCCT topology inspection','read_only':False,'source_geometry_modified':False,
            'regions':{r['label']:{'source_sha256':r['sha256'],'faces':2,'valid':True,'solids':0,'shells':1} for r in params['regions']},
            'IN_sewing':{'free_edges':0,'multiple_edges':0,'faces':2},
            'AL_IN_sewing':{'free_edges':0,'multiple_edges':0,'faces':4},
            'optical_roles_preserved':True,'native_revalidation_required':True,
            'prepared_manifest':str(canonical/'prepared-region-manifest.json'),'prepared_regions':{}}
        for role in ('AL','IN'):
            output=canonical/(role+'-prepared.stp');output.write_bytes(role.encode())
            raw['prepared_regions'][role]={'path':str(output),'faces':2,
                                          'sha256':hashlib.sha256(output.read_bytes()).hexdigest()}
        def run(value):
            (job/'engine-result.json').write_text(json.dumps(value),encoding='utf-8')
            return read_report(alias,'prepare_optical_regions',params)
        result,files=run(raw)
        self.assertEqual(result['prepared_manifest'],raw['prepared_manifest'])
        self.assertEqual(set(files),{'prepared-region-manifest.json','AL-prepared.stp','IN-prepared.stp'})
        outside=self.root/'outside';outside.mkdir()
        bad=copy.deepcopy(raw)
        bad['prepared_manifest']=str(outside.resolve()/'prepared-region-manifest.json')
        with self.assertRaisesRegex(ValueError,'Prepared region scope differs'):
            run(bad)
        outside_output=outside/'AL-prepared.stp';outside_output.write_bytes(b'AL')
        bad=copy.deepcopy(raw)
        bad['prepared_regions']['AL']['path']=str(outside_output.resolve())
        with self.assertRaisesRegex(ValueError,'Prepared region path, face count or hash differs'):
            run(bad)

    def test_additional_solid_diagnosis_is_whitelisted_and_requires_snapshot(self):
        for values in (['AL'],['arbitrary'],['bracket','bracket'],'bracket',[True]):
            with self.assertRaises(ValueError):
                kernel_regions.prepare(str(self.manifest),[],values)
        with self.assertRaises(ValueError):
            kernel_regions.prepare(str(self.manifest),[],['bracket'])

    def test_local_checks_require_bounded_explicit_main_lens_probes(self):
        for points,additional,flag in (([],['main_lens'],True),([[0,0,0]],[],True),
                ([[0,0,0]]*9,['main_lens'],True),([[0,0,0]],['main_lens'],1)):
            with self.assertRaises(ValueError):
                kernel_regions.prepare(str(self.manifest),points,additional,flag)
        regions=json.loads(self.manifest.read_text())['regions']
        path=self.root/'main.stp'
        path.write_bytes(b'main')
        regions.append({'label':'main_lens','path':str(path),'sha256':hashlib.sha256(b'main').hexdigest()})
        self.manifest.write_text(json.dumps({'regions':regions}))
        params=kernel_regions.prepare(str(self.manifest),[[48,-31,15]],['main_lens'],True)
        self.assertIs(params['local_boundary_checks'],True)

if __name__=='__main__':
    unittest.main()
