import copy
from pathlib import Path
import sys
import unittest
from lucidshape_mcp import boundary_paths


class BoundaryPathTests(unittest.TestCase):
    def setUp(self):
        self.spec={'regions':[{'label':'main_lens','faces':1,'sha256':'frozen'}],
            'materials':{'main_lens':{'index':1.473}},'start_rays':[{
                'segment_index_zero_based':12,'position_mm':[1,2,3],
                'direction':[1,0,0],'index':1.473}],
            'replay_attempts':1,'failure_sha256':'failure','main_lens_boundary_mode':'individual_surfaces'}

    def result(self,rows):
        return boundary_paths.read_result([[['main_lens',1,0,'Refractive']],[[12,0,rows]]],self.spec)

    def test_starting_inside_glass_requires_one_more_exit_than_entry(self):
        result=self.result([[[1,2,3],[1,0,0],2,1,1.473],[[3,2,3],[1,0,0],1e38,1,1]])
        self.assertEqual(result['inconsistent_escaping_paths'],0)
        self.assertEqual(result['paths'][0]['glass_exits'],1)
        self.assertTrue(result['paths'][0]['escaping_media_consistent'])

    def test_no_intersection_glass_escape_is_not_a_pass(self):
        result=self.result([[[1,2,3],[1,0,0],1e38,1,1.473]])
        self.assertEqual(result['inconsistent_escaping_paths'],1)

    def test_different_native_start_rejected(self):
        with self.assertRaisesRegex(ValueError,'input differs'):
            self.result([[[2,2,3],[1,0,0],1e38,1,1.473]])

    def test_PC_diagnostic_uses_PC_pairing_and_rejects_glass_index(self):
        spec=copy.deepcopy(self.spec)
        spec.update(lens_material_label='IN',scene_scope='inner_lens_only')
        spec['materials']={'IN':{'index':1.586}}
        spec['regions']=[{'label':'AL','faces':1,'sha256':'al'},{'label':'IN','faces':1,'sha256':'pc'}]
        spec['start_rays'][0]['index']=1
        region_rows=[['AL',1,0,'Specular'],['IN',1,0,'Refractive']]
        rows=[[[1,2,3],[1,0,0],1,1,1],[[2,2,3],[1,0,0],1e38,1,1.586]]
        result=boundary_paths.read_result([region_rows,[[12,0,rows]]],spec)
        self.assertEqual(result['inconsistent_escaping_paths'],1)
        self.assertEqual(result['paths'][0]['PC_entries'],1)
        self.assertNotIn('glass_entries',result['paths'][0])
        rows[-1][-1]=1.473
        with self.assertRaisesRegex(ValueError,'Unexpected selected-lens medium'):
            boundary_paths.read_result([region_rows,[[12,0,rows]]],spec)

    def test_scaled_paths_return_original_mm_and_verify_bounds(self):
        spec=copy.deepcopy(self.spec)
        spec['coordinate_scale']=1000
        spec['regions'][0]['mesh']={'vertex_bounds_mm':[0,10,0,10,0,10]}
        rows=[[[1000,2000,3000],[1,0,0],2000,1,1.473],[[3000,2000,3000],[1,0,0],1e38,1,1]]
        values=[[['main_lens',1,0,'Refractive']],[[12,0,rows]],[['main_lens',[0,10000,0,10000,0,10000]]]]
        result=boundary_paths.read_result(values,spec)
        self.assertEqual(result['paths'][0]['path'][0][0],[1,2,3])
        self.assertEqual(result['paths'][0]['path'][0][2],2)
        self.assertEqual(result['inconsistent_escaping_paths'],0)
        # Exercise the actual JSONStore parser, including the native bounds receipt.
        import json
        import tempfile
        from lucidshape_mcp.results import read_report
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder)
            (directory/'engine-result.json').write_text(json.dumps({'List':{f'p{i}':v for i,v in enumerate(values)}}),encoding='utf-8')
            checked,artifacts=read_report(directory,'replay_s9_boundary_paths',spec)
            self.assertEqual(checked['coordinate_scale'],1000)
            self.assertEqual(checked['inconsistent_escaping_paths'],0)
            self.assertEqual(artifacts,[])
        values[2][0][1][1]=11000
        with self.assertRaisesRegex(ValueError,'changed geometry bounds'):
            boundary_paths.read_result(values,spec)

    def test_scaled_paths_without_geometry_receipt_rejected(self):
        self.spec['coordinate_scale']=1000
        with self.assertRaisesRegex(ValueError,'requires native geometry bounds'):
            self.result([[[1000,2000,3000],[1,0,0],1e38,1,1.473]])

    def test_bounds_receipt_must_not_include_origin_outside_positive_solid(self):
        from lucidshape_mcp.s9_scene import validate_conditioned_bounds
        parameters={'coordinate_scale':1000,'regions':[{'label':'main_lens',
            'mesh':{'vertex_bounds_mm':[45,72,-39,39,-27,27]}}]}
        validate_conditioned_bounds([['main_lens',[45000,72000,-39000,39000,-27000,27000]]],parameters)
        with self.assertRaisesRegex(ValueError,'changed geometry bounds'):
            validate_conditioned_bounds([['main_lens',[0,72000,-39000,39000,-27000,27000]]],parameters)


class BoundaryGeometryFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from lucidshape_mcp.config import configure_geometry_runtime, geometry_available
        if not geometry_available():
            raise unittest.SkipTest('Optional geometry runtime unavailable')
        configure_geometry_runtime()

    def test_valid_box_local_edges_and_pcurves(self):
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from lucidshape_mcp.boundary_geometry import inspect_local_boundary
        report=inspect_local_boundary(BRepPrimAPI_MakeBox(1,1,1).Shape(),[[0,0,0]])
        self.assertTrue(report['local_BRepAlgoAPI_Check_valid'])
        self.assertEqual(report['local_check_problems'],[])
        self.assertEqual(report['sampling_errors'],[])
        self.assertEqual(report['global_non_degenerate_edges_not_owned_by_two_distinct_faces'],[])

    def test_disconnected_overlapping_faces_flagged(self):
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        from OCP.BRep import BRep_Builder
        from OCP.TopoDS import TopoDS_Compound
        from OCP.gp import gp_Pln,gp_Pnt,gp_Dir
        from lucidshape_mcp.boundary_geometry import inspect_local_boundary
        compound=TopoDS_Compound()
        builder=BRep_Builder()
        builder.MakeCompound(compound)
        for offset in (0,.1):
            face=BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(0,0,0),gp_Dir(0,0,1)),offset,1+offset,0,1).Face()
            builder.Add(compound,face)
        report=inspect_local_boundary(compound,[[.5,.5,0]])
        self.assertFalse(report['local_BRepAlgoAPI_Check_valid'])
        self.assertTrue(report['local_check_problems'])

    def test_mesh_check_detects_intersection_without_shared_vertices(self):
        from lucidshape_mcp.boundary_mesh import inspect_local_mesh
        import tempfile
        triangles=[((-1,0,0),(1,0,0),(0,1,0)),((0,.2,-1),(0,.2,1),(.5,.2,0)),
            ((10,0,0),(11,0,0),(10,1,0)),((20,0,0),(21,0,0),(20,1,0))]
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'crossing.stl'
            text=['solid test']
            for triangle in triangles:
                text.extend(['facet normal 0 0 1','outer loop'])
                text.extend('vertex '+' '.join(map(str,p)) for p in triangle)
                text.extend(['endloop','endfacet'])
            text.append('endsolid test')
            path.write_text('\n'.join(text),encoding='ascii')
            report=inspect_local_mesh(path,[[0,.2,0]])
        self.assertEqual(report['pair_check_errors'],[])
        self.assertEqual(len(report['intersections']),1)
        self.assertEqual(report['intersections'][0]['triangle_indices_zero_based'],[0,1])


if __name__=='__main__':
    unittest.main()
