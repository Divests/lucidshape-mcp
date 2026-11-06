from pathlib import Path
import sys
import tempfile
import unittest

from lucidshape_mcp.dde_session import DDESession


class PersistentSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_reused_and_only_owned_helper_stopped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            session=DDESession(root,(sys.executable,str(Path(__file__).with_name("mock_dde_session.py"))))
            try:
                first=await session.request(root/"first",{"action":"probe"})
                second=await session.request(root/"second",{"action":"probe"})
                self.assertEqual(first["helper_process_id"],second["helper_process_id"])
                self.assertEqual((first["fixture_counter"],second["fixture_counter"]),(1,2))
            finally:
                await session.close()
            self.assertEqual(session.process.returncode,0)

    async def test_stale_response_id_rejected_without_resending(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            session=DDESession(root,(sys.executable,str(Path(__file__).with_name("mock_dde_session.py"))))
            try:
                with self.assertRaisesRegex(RuntimeError,"different request"):
                    await session.request(root/"first",{"action":"probe","mock_wrong_id":True})
            finally:
                await session.close()


if __name__=="__main__":
    unittest.main()
