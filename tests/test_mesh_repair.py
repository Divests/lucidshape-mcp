import unittest
from lucidshape_mcp.mesh_repair import project_candidate


class SharedVertexRepairTests(unittest.TestCase):
    def test_incident_triangles_use_identical_repaired_vertex(self):
        vertex=(-1e-5,0,0)
        triangles=[(vertex,(1,0,0),(0,1,1)),(vertex,(0,-1,1),(1,0,0))]
        candidate,metrics=project_candidate(triangles,(0,0,0),(1,0,0),(0,0,0),.1,5e-5)
        self.assertEqual(candidate[0][0],candidate[1][0])
        self.assertEqual(candidate[0][0],(0,0,0))
        self.assertEqual(len(metrics['moved_vertices']),1)
        self.assertLessEqual(metrics['max_vertex_displacement_mm'],5e-5)
        self.assertEqual(triangles[0][0],vertex)

    def test_outside_radius_or_displacement_bound_is_unchanged(self):
        triangles=[((-.001,0,0),(-1e-5,10,0),(1,0,0))]
        candidate,metrics=project_candidate(triangles,(0,0,0),(1,0,0),(0,0,0),1,5e-5)
        self.assertEqual(candidate,triangles)
        self.assertEqual(metrics['moved_vertices'],[])

    def test_winding_distinguishes_solid_overlap_and_air(self):
        from lucidshape_mcp.mesh_cut_repair import TriangleIndex
        # Outward oriented tetrahedron, duplicated with an X translation.
        points=[(0,0,0),(2,0,0),(0,2,0),(0,0,2)]
        faces=[(0,2,1),(0,1,3),(0,3,2),(1,2,3)]
        triangles=[tuple(points[i] for i in face) for face in faces]
        triangles.extend(tuple((p[0]+.2,p[1],p[2]) for p in triangle) for triangle in list(triangles))
        index=TriangleIndex(triangles)
        self.assertEqual(index.stable_winding((.5,.3,.3)),2)
        self.assertEqual(index.stable_winding((.1,.3,.3)),1)
        self.assertEqual(index.stable_winding((3,3,3)),0)
        self.assertAlmostEqual(index.solid_angle_winding((.5,.3,.3)),2,places=8)
        self.assertAlmostEqual(index.solid_angle_winding((.1,.3,.3)),1,places=8)
        self.assertAlmostEqual(index.solid_angle_winding((3,3,3)),0,places=8)

    def test_cut_restores_closed_union_of_overlapping_tetrahedra(self):
        from pathlib import Path
        import sys
        import tempfile
        from lucidshape_mcp.config import configure_geometry_runtime, geometry_available
        if not geometry_available():self.skipTest('Optional OCCT runtime unavailable')
        configure_geometry_runtime()
        from lucidshape_mcp.mesh_cut_repair import split_union_candidate
        from lucidshape_mcp.mesh_repair import write_ascii_stl
        from lucidshape_mcp.stl_geometry import inspect_stl
        points=[(0,0,0),(2,0,0),(0,2,0),(0,0,2)]
        faces=[(0,2,1),(0,1,3),(0,3,2),(1,2,3)]
        triangles=[tuple(points[i] for i in face) for face in faces]
        triangles.extend(tuple((p[0]+.2,p[1]+.1,p[2]+.1) for p in triangle) for triangle in list(triangles))
        candidate,metrics=split_union_candidate(triangles,list(range(8)),(0,0,0),10)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'union.stl'
            write_ascii_stl(path,candidate)
            checked=inspect_stl(path)
        self.assertTrue(checked['closed_oriented_two_manifold'])
        self.assertAlmostEqual(checked['signed_volume_mm3'],2*8/6-1.6**3/6,places=6)
        self.assertLess(metrics['max_geometric_normal_change_deg'],.001)


if __name__=='__main__':
    unittest.main()
