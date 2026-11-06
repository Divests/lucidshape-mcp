import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from lucidshape_mcp import s9_scene
from lucidshape_mcp.results import read_report

class LegacyProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.req_path=self.root/'requirements.json'
        self.man_path=self.root/'regions.json'
        self.man={'regions':[]}
        for label in (*s9_scene.LABELS,'inner_lens_solid_reference'):
            p=self.root/(label+'.stp')
            p.write_bytes(label.encode())
            self.man['regions'].append({'label':label,'path':str(p),
                'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
                'cad_face_count':2,'native_top_level_object_count':2,'native_import_verified':True,
                'include_in_optical_scene':label!='inner_lens_solid_reference'})
        ray=self.root/'test.ray'
        ray.write_bytes(b'fixture rays')
        axes=self.root/'axes.csv'
        self.fields=['name',*[f'origin_{d}_mm' for d in 'xyz'],*[f'{a}dir_{d}' for a in 'xyz' for d in 'xyz']]
        with axes.open('w',encoding='utf-8',newline='') as f:
            w=csv.writer(f)
            w.writerow(self.fields)
            for i in range(12):
                w.writerow([f'轴系.{i+2}',-45,i,5,0,1,0,0,0,1,1,0,0])
        self.req={'inner_lens':{'aluminized_region_geometry_mapping_verified':True,'refractive_index':1.586,'aluminized_surface_reflectance':.8},
            'main_lens':{'refractive_index':1.473},'model':{'axes_inventory':str(axes)},
            'approved_source':{'path':str(ray),'sha256_from_native_inspection':hashlib.sha256(ray.read_bytes()).hexdigest(),'ray_count':5000000,'flux_per_led_lm':452.18},
            'surface_scattering_choice':{'status':'user_confirmed'},
            'other_surface_parameters':{'Bracket':{'reflectance':.05},'HeatSink':{'reflectance':.3},
                'HeatShield':{'reflectance':0,'transmission':0},'PCBA':{'reflectance':0,'transmission':0}}}
        self.save()

    def save(self):
        self.req_path.write_text(json.dumps(self.req),encoding='utf-8')
        self.man_path.write_text(json.dumps(self.man),encoding='utf-8')

    def prepare(self):
        self.save()
        return s9_scene.prepare(str(self.req_path),str(self.man_path))

    def test_stages_exactly_seven_regions_and_ray_without_duplicate_solid(self):
        p=self.prepare()
        target=self.root/'job'
        target.mkdir()
        rows=s9_scene.stage(target,p)
        self.assertEqual(len(rows),8)
        self.assertFalse(p['absorption_applied'])
        script=s9_scene.render('@@REGIONS@@\n@@SOURCES@@',p)
        self.assertNotIn('inner_lens_solid_reference',script)
        self.assertEqual(script.count('addSource('),12)
        self.assertIn('Medium("IN",1.5860000000000001,0),1,1)',script)

    def test_rejects_reference_solid_traced_alongside_partition(self):
        self.man['regions'][-1]['include_in_optical_scene']=True
        with self.assertRaisesRegex(ValueError,'reference solid'):
            self.prepare()

    def test_closed_volume_requires_matching_valid_solid_receipt(self):
        self.req['native_diagnostics']={'main_lens_boundary_mode':'closed_global'}
        with self.assertRaisesRegex(ValueError,'independently checked'):
            self.prepare()
        evidence_path=self.root/'topology.json'
        checked={'source_sha256':self.man['regions'][0]['sha256'],'valid':True,'solids':1,'shell_closed_flags':[True]}
        evidence={'status':'succeeded','job':{'status':'succeeded','template':'inspect_optical_region_topology','result':{'regions':{'main_lens':checked}}}}
        evidence_path.write_text(json.dumps(evidence),encoding='utf-8')
        self.man['fresh_main_topology_report']=str(evidence_path)
        params=self.prepare()
        self.assertEqual(params['main_lens_boundary_mode'],'closed_global')
        self.assertIsNotNone(params['main_lens_boundary_evidence'])
        self.assertEqual(s9_scene.render('@@BOUNDARY_MODE@@',params),'1')
        rendered=s9_scene.render('@@REGIONS@@',params)
        calls=rendered.splitlines()
        self.assertTrue(calls[0].endswith(',1)) return;'))
        self.assertTrue(all(line.endswith(',0)) return;') for line in calls[1:]))
        checked['shell_closed_flags']=[False]
        evidence_path.write_text(json.dumps(evidence),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'does not certify'):
            self.prepare()

    def test_rejects_changed_geometry_and_unconfirmed_mapping(self):
        Path(self.man['regions'][0]['path']).write_bytes(b'changed geometry')
        with self.assertRaisesRegex(ValueError,'differs'):
            self.prepare()
        self.req['inner_lens']['aluminized_region_geometry_mapping_verified']=False
        with self.assertRaisesRegex(ValueError,'identity'):
            self.prepare()

    def test_rejects_nonfinite_index_and_invalid_source_frame(self):
        self.req['main_lens']['refractive_index']=float('nan')
        with self.assertRaisesRegex(ValueError,'number'):
            self.prepare()
        self.req['main_lens']['refractive_index']=1.473
        axes=Path(self.req['model']['axes_inventory'])
        text=axes.read_text(encoding='utf-8').replace(',0,1,0,0,0,1,1,0,0',',1,0,0,0,0,1,0,1,0')
        axes.write_text(text,encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'basis'):
            self.prepare()

    def test_result_never_certifies_alignment_or_photometry(self):
        p=self.prepare()
        types={'refractive':'Refractive','specular':'Specular','Lambertian':'LambertianReflective','absorber':'Absorber'}
        regions=[[r['label'],2,0,types[p['materials'][r['label']]['kind']]] for r in p['regions']]
        sources=[[i+2,pos,[1,0,0],[0,1,0]] for i,pos in enumerate(p['positions_mm'])]
        paths=[[i+2,1,[[pos,[1,0,0],1000,1,1]]] for i,pos in enumerate(p['positions_mm'])]
        def write(values):
            (self.root/'engine-result.json').write_text(json.dumps({'List':{f'p{i}':v for i,v in enumerate(values)}}),encoding='utf-8')
        write([regions,sources,15,5000000,paths])
        result,_=read_report(self.root,'preflight_s9_optics',p)
        self.assertFalse(result['photometric_simulation_run'])
        self.assertFalse(result['source_alignment_verified'])
        self.assertFalse(result['extra_per_traversal_absorption_applied'])
        paths[0][2][0][4]=float('nan')
        write([regions,sources,15,5000000,paths])
        with self.assertRaisesRegex(ValueError,'non-finite'):
            read_report(self.root,'preflight_s9_optics',p)

if __name__=='__main__':
    unittest.main()
