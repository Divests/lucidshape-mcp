import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock,patch

from lucidshape_mcp.live_studio import import_step,native_request,parse_receipt,validate_pid


def raw_receipt(nonce="token",action="inspect",name="model.stp",bounds=None):
    values=[nonce,action,1,1,name,10,4,1,bounds or [-1,2,-2,3,0,5],1]
    return {"List":{f"p{i}":value for i,value in enumerate(values)}}


class LiveReceiptTests(unittest.TestCase):
    def test_receipt_rejects_stale_nonce_invalid_counts_and_nonfinite_bounds(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/"native.json"
            path.write_text(json.dumps(raw_receipt()))
            with self.assertRaises(ValueError):
                parse_receipt(path,"another-token")
            raw=raw_receipt()
            raw["List"]["p6"]=-1
            path.write_text(json.dumps(raw))
            with self.assertRaises(ValueError):
                parse_receipt(path,"token")
            path.write_text(json.dumps(raw_receipt(bounds=[0,float("inf"),0,1,0,1])))
            with self.assertRaises(ValueError):
                parse_receipt(path,"token")

    def test_read_only_receipt_does_not_claim_file_open(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/"native.json"
            path.write_text(json.dumps(raw_receipt()))
            result=parse_receipt(path,"token")
            self.assertIsNone(result["open_file_returned_success"])
            self.assertEqual(result["dimensions_mm"],[3,5,5])

    def test_filtered_traversal_can_outnumber_default_enumeration(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/"native.json"
            raw=raw_receipt(name="C:/models/model.lug")
            raw["List"]["p6"]=20
            path.write_text(json.dumps(raw))
            result=parse_receipt(path,"token")
            self.assertEqual(result["surface_object_count"],20)
            self.assertEqual(result["object_count"],10)

    def test_invalid_pid_is_rejected(self):
        for value in (True,0,-1,"20908",2**32):
            with self.assertRaises(ValueError):
                validate_pid(value)

    def test_optics_report_zero_objects_is_not_ready_and_malformed_rows_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/"native.json"
            raw=raw_receipt()
            raw["List"]["p10"]=[[label,0,[]] for label in ("emitters","sensors","actors")]
            path.write_text(json.dumps(raw))
            result=parse_receipt(path,"token")
            self.assertFalse(result["minimum_optical_types_present"])
            self.assertEqual(result["optical_groups"]["sensors"]["count"],0)
            raw["List"]["p10"][0]=["emitters",0,[["source","PointLight"]]]
            path.write_text(json.dumps(raw))
            with self.assertRaises(ValueError):
                parse_receipt(path,"token")

    def test_optics_report_retains_names_types_without_claiming_parameter_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/"native.json"
            raw=raw_receipt()
            raw["List"]["p10"]=[["emitters",1,[["LED","PointLight"]]],["sensors",1,[["far field","CandelaSensor"]]],["actors",0,[]]]
            path.write_text(json.dumps(raw))
            result=parse_receipt(path,"token")
            self.assertTrue(result["minimum_optical_types_present"])
            self.assertEqual(result["optical_groups"]["sensors"]["objects"][0]["type"],"CandelaSensor")
            self.assertIn("require separate verification",result["optics_verification_scope"])


class LiveRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_delayed_receipt_waits_without_resending_execute_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            writers=[]
            async def execute(job,request,timeout_seconds):
                script=(job/request["script_name"]).read_text()
                # The per-request nonce is the first JSON string in the report tuple.
                nonce=script.split("mcpReceipt << (",1)[1].split(",",1)[0]
                nonce=json.loads(nonce)
                (job/"native-receipt.json").write_text('{"List":')
                async def finish():
                    await asyncio.sleep(0.01)
                    (job/"native-receipt.json").write_text(json.dumps(raw_receipt(nonce)))
                writers.append(asyncio.create_task(finish()))
                return {"acknowledged":True,"process_id":123,"service":"LucidShape.exe","executable":r"D:\LucidShape\LucidShape.exe"}
            with patch("lucidshape_mcp.live_studio.run_dde",side_effect=execute) as worker:
                result=await native_request(directory,123,timeout_seconds=2)
            await asyncio.gather(*writers)
            self.assertEqual(worker.call_count,1)
            self.assertEqual(result["process_id"],123)
            self.assertFalse(result["new_studio_process_started"])

    async def test_ack_without_receipt_is_never_success_or_resubmitted(self):
        with tempfile.TemporaryDirectory() as temporary:
            transport={"acknowledged":True,"process_id":123,"service":"LucidShape.exe","executable":"LucidShape.exe"}
            with patch("lucidshape_mcp.live_studio.run_dde",new=AsyncMock(return_value=transport)) as worker:
                with self.assertRaisesRegex(RuntimeError,"unverified"):
                    await native_request(Path(temporary),123,timeout_seconds=0.01)
            self.assertEqual(worker.call_count,1)

    async def test_returned_process_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            transport={"acknowledged":True,"process_id":999}
            with patch("lucidshape_mcp.live_studio.run_dde",new=AsyncMock(return_value=transport)):
                with self.assertRaisesRegex(RuntimeError,"different Studio process"):
                    await native_request(Path(temporary),123)

    async def test_import_pins_instance_and_checks_geometry_after_load(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            source=root/"part.step"
            source.write_bytes(b"explicit mock STEP fixture")
            loaded=None
            async def query(directory,process_id=None,model_path=None,timeout_seconds=30):
                nonlocal loaded
                if model_path:
                    loaded=model_path
                return {"process_id":123,"model_name":str(loaded) if loaded else "", "surface_object_count":4,
                        "open_file_returned_success":True,"bounding_box_mm":[0,1,0,2,0,3],"dimensions_mm":[1,2,3]}
            with patch("lucidshape_mcp.live_studio.native_request",side_effect=query) as requests:
                result=await import_step(root,str(source),123,observe_seconds=1)
            self.assertEqual(result["status"],"succeeded")
            self.assertEqual(len(result["checks"]),2)
            self.assertEqual(requests.call_count,4)
            self.assertTrue(all(call.args[1]==123 for call in requests.call_args_list))
            self.assertTrue(result["gui_responsiveness_verified"])
            self.assertFalse(result["stability_verified"])
            self.assertTrue(result["original_source_unchanged"])
            self.assertEqual(source.read_bytes(),b"explicit mock STEP fixture")

    async def test_import_wrong_model_is_unverified_and_not_reopened(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            source=root/"part.step"
            source.write_text("mock STEP fixture")
            reply={"process_id":123,"model_name":"unrelated.step","surface_object_count":4,
                   "open_file_returned_success":True,"bounding_box_mm":[0,1,0,2,0,3]}
            with patch("lucidshape_mcp.live_studio.native_request",new=AsyncMock(return_value=reply)) as requests:
                result=await import_step(root,str(source),123,observe_seconds=1)
            self.assertEqual(result["status"],"unverified")
            self.assertEqual(requests.call_count,2)
            self.assertFalse(result["automatic_resubmission_allowed"])


if __name__=="__main__":
    unittest.main()
