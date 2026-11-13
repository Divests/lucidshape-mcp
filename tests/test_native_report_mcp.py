"""Policy/schema boundary tests for native report exports; no CAD launch."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from lucidshape_mcp.engine import BatchEngine
from lucidshape_mcp.policy import Policy
from lucidshape_mcp.server import create_server

ENV={'LUCIDSHAPE_TOOLSETS':'core,analysis','LUCIDSHAPE_TOOLS':'',
     'LUCIDSHAPE_READ_ONLY':'false','LUCIDSHAPE_ALLOWED_ROOTS':'[]',
     'LUCIDSHAPE_ALLOW_SOURCE_WRITES':'false'}
TOOLS={'studio_export_photometry_view','studio_generate_static_road_views'}

class NativeReportMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_contract_and_readonly_exclusion(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,ENV):
            engine=BatchEngine(Path(tmp)/'jobs')
            try:
                server=create_server(engine)
                by_name={t.name:t for t in await server.list_tools()}
                for name in TOOLS:
                    self.assertIn(name,by_name)
                    self.assertFalse(by_name[name].annotations.readOnlyHint)
                    self.assertFalse(by_name[name].annotations.destructiveHint)
                    self.assertIn('lid_path',by_name[name].inputSchema['required'])
                engine.policy=Policy(engine.root,toolsets=('core','analysis'),read_only=True)
                self.assertTrue(TOOLS.isdisjoint({t.name for t in await create_server(engine).list_tools()}))
            finally:
                await engine.close()

    async def test_input_root_enforced_before_native_launch(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,ENV):
            root=Path(tmp); allowed=root/'allowed'; allowed.mkdir()
            outside=root/'outside.ies';outside.write_text('input')
            engine=BatchEngine(root/'jobs',('fake-lush.exe',))
            engine.policy=Policy(engine.root,toolsets=('core','analysis'),allowed_roots=(allowed,))
            try:
                server=create_server(engine)
                with patch('lucidshape_mcp.server.export_photometry_view',new_callable=AsyncMock) as invoke:
                    answer=await server.call_tool('studio_export_photometry_view',{'lid_path':str(outside)})
                    invoke.assert_not_called()
                    self.assertIn('ACCESS_DENIED',str(answer))
            finally:
                await engine.close()

    async def test_explicit_empty_vectors_are_not_replaced_by_defaults(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,ENV):
            root=Path(tmp);source=root/'input.ies';source.write_text('input')
            engine=BatchEngine(root/'jobs',('fake-lush.exe',))
            try:
                server=create_server(engine)
                arguments={'lid_path':str(source),'lamp_positions_m':[[0,.75,.8]],
                           'road_bounds_m':[0,200,-30,30], 'driver_position_m':[], 'top_position_m':[]}
                with patch('lucidshape_mcp.server.generate_static_road_views',new_callable=AsyncMock,
                           return_value={'status':'unverified'}) as invoke:
                    answer=await server.call_tool('studio_generate_static_road_views',arguments)
                    self.assertEqual(invoke.call_args.kwargs['driver_position_m'],[])
                    self.assertEqual(invoke.call_args.kwargs['top_position_m'],[])
                    self.assertIn('unverified',str(answer))
            finally:
                await engine.close()

if __name__=='__main__':unittest.main()
