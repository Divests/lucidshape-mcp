"""Boundary tests for native UV export; engine integration is a separate opt-in run."""
from __future__ import annotations
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import AsyncMock,patch
import zlib
from lucidshape_mcp import uv_view_export as uv


def png(width,height):
    def chunk(kind,payload):
        return struct.pack('>I',len(payload))+kind+payload+struct.pack('>I',zlib.crc32(kind+payload)&0xffffffff)
    pixels=(b'\0'+bytes((1,2,3))*width)*height
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(pixels))+chunk(b'IEND',b'')


class NativeUVExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.source=self.root/'sample.ies'
        self.source.write_text('IESNA:LM-63-2002\nTILT=NONE\n',encoding='utf-8')
        self.params=uv.validate(lid_path=str(self.source),width_px=400,height_px=250)
        self.nonce='nonce-for-test'
        (self.root/'input.ies').write_bytes(self.source.read_bytes())
        self.manifest=[{'source':str(self.source),'source_sha256':hashlib.sha256(self.source.read_bytes()).hexdigest(),'staged_name':'input.ies','staged_sha256':hashlib.sha256(self.source.read_bytes()).hexdigest()}]
        (self.root/'native-job.lus').write_bytes(b'void main(){}')
        self.script_hash=hashlib.sha256(b'void main(){}').hexdigest()
        self.props=[0]*37
        self.props[0]=[0,100]
        self.props[2]=[-20,20,-10,10]
        for k,v in {7:2,9:0,11:0,12:1,16:1,17:1,19:1,20:1,21:''}.items():self.props[k]=v
        self.fields=[self.nonce,1,'succeeded','UVView',[400,250],[100,0,0],200,self.props,1,0,'completed']
        self.write_artifacts()

    def write_artifacts(self):
        raw={'List':{f'p{i}':value for i,value in enumerate(self.fields)}}
        (self.root/'native-receipt.json').write_text(json.dumps(raw),encoding='utf-8')
        (self.root/'photometry-native.png').write_bytes(png(400,250))

    def test_reject_bad_parameters(self):
        for changes in ({'width_px':True},{'height_px':249},{'timeout_seconds':0},{'quantity':'lux25'},
                        {'screen_distance_m':25},{'view_bounds':[0,0,1,2]},
                        {'view_bounds':[0,1,float('nan'),2]},{'value_range':[-1,2]},
                        {'contours':[5,3]},{'contours':[True]},{'contours':[float('inf')]}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                uv.validate(lid_path=str(self.source),**changes)

    def test_native_coordinates_are_not_limited_to_degrees(self):
        p=uv.validate(lid_path=str(self.source),quantity='native',view_bounds=[0,250,-30,30])
        self.assertEqual(p['view_bounds'],[0,250,-30,30])

    def test_png_validation_rejects_crc_truncation_and_dimensions(self):
        file=self.root/'photometry-native.png'
        verified=uv.verify_png(file,400,250)
        self.assertTrue(verified['native_png_crc_and_pixel_stream_verified'])
        with self.assertRaises(ValueError):uv.verify_png(file,401,250)
        original=file.read_bytes()
        for changed in (original[:-1],original[:20]+bytes([original[20]^1])+original[21:]):
            file.write_bytes(changed)
            with self.assertRaises(ValueError):uv.verify_png(file,400,250)

    def test_valid_receipt_and_original_unchanged(self):
        result=uv.finalize(self.root,self.params,self.nonce,self.manifest,self.script_hash)
        self.assertEqual(result['status'],'succeeded')
        self.assertTrue(result['source_files_unchanged'])
        self.assertTrue(result['axes'] and result['color_bar'] and result['contours'])
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(),self.manifest[0]['source_sha256'])

    def test_reject_nonce_status_quantity_or_source_change(self):
        for index,value in ((0,'old-nonce'),(2,'failed'),(3,'Geo3DView'),(8,0),(9,2),(10,'incomplete')):
            fields=copy.deepcopy(self.fields)
            self.fields[index]=value
            self.write_artifacts()
            with self.assertRaises(ValueError):uv.finalize(self.root,self.params,self.nonce,self.manifest,self.script_hash)
            self.fields=fields
        self.write_artifacts()
        self.source.write_text('changed')
        with self.assertRaisesRegex(ValueError,'Source changed'):
            uv.finalize(self.root,self.params,self.nonce,self.manifest,self.script_hash)

    def test_reject_missing_axis_or_mismatched_requested_range(self):
        self.fields[7][17]=0
        self.write_artifacts()
        with self.assertRaises(ValueError):uv.finalize(self.root,self.params,self.nonce,self.manifest,self.script_hash)
        self.fields[7][17]=1
        self.write_artifacts()
        self.params['view_bounds']=[0,100,-30,30]
        with self.assertRaises(ValueError):uv.finalize(self.root,self.params,self.nonce,self.manifest,self.script_hash)

    def test_native_units_not_mislabeled_as_candela(self):
        self.fields[8]=0
        self.params['quantity']='native'
        self.write_artifacts()
        result=uv.finalize(self.root,self.params,self.nonce,self.manifest,self.script_hash)
        self.assertEqual(result['quantity'],'native')
        self.assertIsNone(result['angular_domain_flux_lm'])
        self.assertIn('native planar/data coordinates',result['native_uv_semantics'])

    def test_changed_script_or_snapshot_is_rejected(self):
        (self.root/'native-job.lus').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'script changed'):
            uv.finalize(self.root,self.params,self.nonce,self.manifest,self.script_hash)
        (self.root/'native-job.lus').write_bytes(b'void main(){}')
        (self.root/'input.ies').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'snapshot changed'):
            uv.finalize(self.root,self.params,self.nonce,self.manifest,self.script_hash)

    def test_failed_launch_never_reports_success_or_closes_unverified_process(self):
        executable=self.root/'LucidShape.exe'
        executable.write_bytes(b'test engine')
        with patch.object(uv,'STUDIO_SHA256',hashlib.sha256(executable.read_bytes()).hexdigest()), \
             patch.object(uv,'make_script',return_value='void main(){}'), \
             patch.object(uv,'run_private_studio',AsyncMock(return_value={'status':'unverified','error':'no native receipt'})), \
             patch.object(uv,'close_private_studio',AsyncMock()) as close:
            result=asyncio.run(uv.export_view(self.root/'jobs',executable,lid_path=str(self.source),width_px=400,height_px=250))
        self.assertEqual(result['status'],'unverified')
        self.assertNotIn('image',result)
        close.assert_not_called()

class PrivateExportPolicyTests(unittest.TestCase):
    def test_existing_pid_refused_before_any_launch(self):
        with patch.object(uv,'run_private_studio',AsyncMock()) as launch:
            with self.assertRaisesRegex(ValueError,'private owned'):
                asyncio.run(uv.export_view(Path('.'),Path('unused.exe'),lid_path='unused.ies',process_id=1234))
        launch.assert_not_called()

if __name__=='__main__':unittest.main()
