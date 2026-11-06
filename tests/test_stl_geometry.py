from pathlib import Path
import struct
import tempfile
import unittest
from lucidshape_mcp.stl_geometry import inspect_stl,write_explicit_facet_stl

class STLGeometryTests(unittest.TestCase):
    def test_explicit_normals_follow_a_tilted_facet_and_preserve_native_positions(self):
        points=[(10.1,20.2,30.3),(11.1,20.2,30.3),(10.1,21.2,31.3)]
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'facet.stl'
            self.assertEqual(write_explicit_facet_stl(path,points,[(0,1,2)]),0)
            row=struct.unpack_from('<12fH',path.read_bytes(),84)
            self.assertEqual(row[3:12],struct.unpack('<9f',struct.pack('<9f',*[v for p in points for v in p])))
            self.assertAlmostEqual(row[0],0)
            self.assertAlmostEqual(row[1],-2**-.5,places=5)
            self.assertAlmostEqual(row[2],2**-.5,places=5)

    def test_float32_collapsed_facets_are_reported_and_retained(self):
        points=[(40,0,0),(40+1e-8,0,0),(40,1,0)]
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'collapsed.stl'
            self.assertEqual(write_explicit_facet_stl(path,points,[(0,1,2)]),1)
            self.assertEqual(struct.unpack_from('<I',path.read_bytes(),80)[0],1)

    def test_closed_tetrahedron_volume_and_missing_face_are_distinguished(self):
        a,b,c,d=(0.,0.,0.),(1.,0.,0.),(0.,1.,0.),(0.,0.,1.)
        faces=[(a,c,b),(a,b,d),(a,d,c),(b,c,d)]
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'test.stl'
            def write(rows):
                path.write_bytes(b'solid binary header is still binary'.ljust(80,b' ')+struct.pack('<I',len(rows))+
                    b''.join(struct.pack('<12fH',0,0,0,*[v for p in row for v in p],0) for row in rows))
            write(faces)
            result=inspect_stl(path)
            self.assertTrue(result['closed_oriented_two_manifold'])
            self.assertAlmostEqual(result['signed_volume_mm3'],1/6)
            self.assertEqual(result['bounding_box_mm'],[0,1,0,1,0,1])
            write(faces[:-1])
            with self.assertRaises(ValueError):
                inspect_stl(path)
            write([faces[0],faces[1],faces[2],faces[0]])
            self.assertFalse(inspect_stl(path)['closed_oriented_two_manifold'])
            write([tuple(reversed(row)) for row in faces])
            self.assertLess(inspect_stl(path)['signed_volume_mm3'],0)
