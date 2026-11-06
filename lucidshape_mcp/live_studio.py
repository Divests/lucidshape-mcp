"""MCP tasks that communicate only with an existing Studio DDE service."""
import asyncio
import json
import os
from pathlib import Path
import hashlib
import re
import sys
import time
import uuid

from .inputs import decode_text,input_file,snapshot
from .results import count,number,sequence,string


async def run_dde(directory:Path, request:dict, timeout_seconds:int=20):
    directory.mkdir(parents=True,exist_ok=True)
    path=directory/"dde-request.json"
    path.write_text(json.dumps(request),encoding="utf-8")
    env={**os.environ,"PYTHONPATH":str(Path(__file__).resolve().parents[1])+os.pathsep+os.environ.get("PYTHONPATH","")}
    with (directory/"dde-worker.log").open("wb") as log:
        process=await asyncio.create_subprocess_exec(sys.executable,"-m","lucidshape_mcp.dde_worker",str(path),cwd=directory,
            env=env,stdin=asyncio.subprocess.DEVNULL,stdout=log,stderr=asyncio.subprocess.STDOUT)
        try:
            await asyncio.wait_for(process.wait(),timeout_seconds)
        except (asyncio.TimeoutError,asyncio.CancelledError):
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
    if process.returncode!=0:
        raise RuntimeError("DDE worker failed; "+(directory/"dde-worker.log").read_text(encoding="utf-8",errors="replace")[-3000:])
    return json.loads((directory/"dde-result.json").read_text(encoding="utf-8"))


async def live_state(root:Path,transport=None,connect:bool=True):
    if not isinstance(connect,bool):
        raise ValueError("connect must be boolean")
    directory=root.resolve()/("live-probe-"+uuid.uuid4().hex)
    result=await (transport or run_dde)(directory,{"action":"probe" if connect else "process_inventory"})
    result.update(backend="existing LucidStudio DDE",new_studio_process_started=False,computer_use_calls=0,
                  record_directory=str(directory))
    (directory/"result.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    return result


def validate_pid(process_id):
    if process_id is not None and (isinstance(process_id,bool) or not isinstance(process_id,int) or not 1<=process_id<=0xFFFFFFFF):
        raise ValueError("process_id must be a positive Windows process ID")


def parse_receipt(path:Path,nonce:str):
    raw=json.loads(decode_text(path.read_bytes())[0])
    fields=raw.get("List",{})
    if set(fields) not in ({f"p{i}" for i in range(10)},{f"p{i}" for i in range(11)}):
        raise ValueError("Unexpected live Studio receipt structure")
    token,action,opened,has_model,name,total,surfaces,bounds_ok,bounds,model_count=[fields[f"p{i}"] for i in range(10)]
    if token!=nonce or action not in ("inspect","import"):
        raise ValueError("Studio receipt does not match this request")
    if opened not in (0,1) or has_model not in (0,1) or bounds_ok not in (0,1):
        raise ValueError("Invalid native receipt flags")
    total,surfaces,model_count=count(total),count(surfaces),count(model_count)
    # Model's default enumeration and filtered traversal have different scopes;
    # filtered surface references can outnumber the default object enumeration.
    if not has_model and (total or surfaces):
        raise ValueError("Invalid native object counts")
    for value in sequence(bounds,6):
        number(value)
    if bounds_ok and any(bounds[i]>bounds[i+1] for i in (0,2,4)):
        raise ValueError("Invalid native bounding box")
    result={"action":action,"open_file_returned_success":bool(opened) if action=="import" else None,"has_current_model":bool(has_model),
            "model_name":string(name),"object_count":total,"surface_object_count":surfaces,"open_model_count":model_count,
            "surface_count_scope":"Filtered Surface references including nested objects; not a unique CAD face count",
            "bounding_box_mm":bounds if bounds_ok else None,
            "dimensions_mm":[bounds[i+1]-bounds[i] for i in (0,2,4)] if bounds_ok else None}
    if "p10" in fields:
        groups={}
        for row in sequence(fields["p10"],3):
            label,size,members=sequence(row,3)
            if label not in ("emitters","sensors","actors") or label in groups:
                raise ValueError("Unexpected optics group")
            size=count(size)
            if len(sequence(members))>min(size,100):
                raise ValueError("Invalid optics sample count")
            groups[label]={"count":size,"objects":[{"name":string(sequence(item,2)[0]),"type":string(item[1])} for item in members],
                           "truncated":size>len(members)}
        result["optical_groups"]=groups
        result["minimum_optical_types_present"]=bool(has_model and groups["emitters"]["count"] and groups["sensors"]["count"])
        result["optics_verification_scope"]="Object types and names only; assignments, source data, material values and simulation target require separate verification"
    return result


async def native_request(directory:Path,process_id=None,model_path:Path|None=None,timeout_seconds=30,transport=None,include_optics:bool=False):
    validate_pid(process_id)
    if not isinstance(include_optics,bool):
        raise ValueError("include_optics must be boolean")
    if include_optics and model_path is not None:
        raise ValueError("Optics inspection is read-only and cannot open a model")
    directory.mkdir(parents=True,exist_ok=True)
    nonce=uuid.uuid4().hex
    receipt=directory/"native-receipt.json"
    action="import" if model_path else "inspect"
    open_statement="double mcpOpened=1;"
    if model_path:
        open_statement="double mcpOpened=mcpApp.openFile("+json.dumps(model_path.as_posix())+");"
    script=r"""#pragma NOPP
object mcpNull;
object mcpApp=getApplication();
@@OPEN@@
object mcpModels=mcpApp.getAllModels();
object mcpModel=mcpApp.getModel($,1);
double mcpHasModel=0,mcpTotal=0,mcpSurfaceCount=0,mcpBoundsOK=0;
string mcpName="";
double xmin=0,xmax=0,ymin=0,ymax=0,zmin=0,zmax=0;
if(mcpModel) {
    mcpHasModel=1;
    mcpName=mcpModel.getName();
    // LS2.0 JSONStore does not escape raw Windows backslashes in names.
    string mcpNormalizedName;
    regex_replace(mcpName,"\\\\","/",mcpNormalizedName);
    mcpName=mcpNormalizedName;
    object mcpObjects=mcpModel.getObjects();
    // Model uses the documented selectedFlag/types signature; ObjectVector has a different runtime overload.
    object mcpSurfaces=mcpModel.getObjects(0,$,("Surface"),());
    mcpTotal=mcpObjects.size();
    mcpSurfaceCount=mcpSurfaces.size();
    if(mcpSurfaceCount) {
        xmin=1e100; xmax=-1e100; ymin=1e100; ymax=-1e100; zmin=1e100; zmax=-1e100;
        mcpBoundsOK=mcpSurfaces.checkBoundingBox(xmin,xmax,ymin,ymax,zmin,zmax);
    }
}
@@OPTICS@@
object mcpReceipt=JSONStore(@@RECEIPT@@,0,1);
mcpReceipt << (@@NONCE@@,@@ACTION@@,mcpOpened,mcpHasModel,mcpName,mcpTotal,mcpSurfaceCount,mcpBoundsOK,(xmin,xmax,ymin,ymax,zmin,zmax),mcpModels.size()@@OPTICS_RESULT@@);
mcpReceipt=mcpNull;
""".replace("@@OPEN@@",open_statement).replace("@@RECEIPT@@",json.dumps(receipt.as_posix())).replace("@@NONCE@@",json.dumps(nonce)).replace("@@ACTION@@",json.dumps(action))
    optics=r"""
object mcpOpticGroups=();
if(mcpModel) {
    object mcpEmitters=mcpModel.getObjects(0,$,("EmitterMaterial"),());
    object mcpSensors=mcpModel.getObjects(0,$,("SensorMaterial"),());
    object mcpActors=mcpModel.getObjects(0,$,("ActorMaterial"),());
    object mcpCollections=(mcpEmitters,mcpSensors,mcpActors);
    object mcpLabels=("emitters","sensors","actors");
    double mcpI,mcpJ;
    for(mcpI=0;mcpI<3;mcpI++) {
        object mcpItems=mcpCollections[mcpI];
        object mcpRows=();
        for(mcpJ=0;mcpJ<mcpItems.size() && mcpJ<100;mcpJ++) {
            object mcpItem=mcpItems[mcpJ];
            string mcpRawName=mcpItem.getName();
            string mcpSafeName;
            regex_replace(mcpRawName,"\\\\","/",mcpSafeName);
            mcpRows << (mcpSafeName,mcpItem.nameOfClass());
        }
        mcpOpticGroups << (mcpLabels[mcpI],mcpItems.size(),mcpRows);
    }
} else {
    mcpOpticGroups << ("emitters",0,()) << ("sensors",0,()) << ("actors",0,());
}
"""
    script=script.replace("@@OPTICS@@",optics if include_optics else "").replace("@@OPTICS_RESULT@@",",mcpOpticGroups" if include_optics else "")
    (directory/"native-request.do").write_text(script,encoding="ascii")
    deadline=time.monotonic()+timeout_seconds
    transport_result=await (transport or run_dde)(directory,{"action":"execute_file","script_name":"native-request.do","receipt_name":"native-receipt.json","process_id":process_id,"timeout_ms":timeout_seconds*1000},timeout_seconds+5)
    if process_id is not None and transport_result.get("process_id")!=process_id:
        raise RuntimeError("Native request returned a different Studio process ID")
    if not transport_result.get("acknowledged"):
        raise RuntimeError(f"Studio did not acknowledge the native request; operation is unverified and must not be automatically resubmitted: {transport_result}")
    # Studio acknowledges DDE before its queued script finishes. Wait for complete JSON, without resubmitting.
    while True:
        try:
            result=parse_receipt(receipt,nonce)
            break
        except (FileNotFoundError,json.JSONDecodeError):
            if time.monotonic()>=deadline:
                raise RuntimeError(f"DDE command was acknowledged but native API receipt is missing or incomplete; operation is unverified; see {directory}")
            await asyncio.sleep(0.2)
    result.update(process_id=transport_result["process_id"],service=transport_result["service"],executable=transport_result["executable"],
                  persistent_dde_session=transport_result.get("persistent_dde_session",False),helper_process_id=transport_result.get("helper_process_id"),
                  native_receipt_file=str(receipt),record_directory=str(directory),new_studio_process_started=False,computer_use_calls=0,
                  backend="MCP -> DDE -> existing LucidStudio native API",status="succeeded")
    (directory/"result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    return result


async def inspect_current(root:Path,process_id=None,transport=None,include_optics:bool=False):
    return await native_request(root.resolve()/("live-inspect-"+uuid.uuid4().hex),process_id,transport=transport,include_optics=include_optics)


def model_matches(result:dict,path:Path):
    name=result["model_name"].replace("\\","/").lower()
    expected=path.as_posix().lower()
    return name==expected or name.rsplit("/",1)[-1]==path.name.lower()


async def import_step(root:Path,cad_path:str,process_id=None,timeout_seconds:int=180,observe_seconds:int=15,transport=None):
    validate_pid(process_id)
    for name,value,low,high in (("timeout_seconds",timeout_seconds,5,600),("observe_seconds",observe_seconds,1,60)):
        if isinstance(value,bool) or not isinstance(value,int) or not low<=value<=high:
            raise ValueError(f"{name} must be an integer from {low} to {high}")
    source=input_file(cad_path,{".stp",".step"})
    directory=root.resolve()/("live-import-"+uuid.uuid4().hex)
    directory.mkdir(parents=True)
    result={"status":"running","source_path":str(source),"record_directory":str(directory),
            "new_studio_process_started":False,"computer_use_calls":0,"geometry_only":True,"simulation_run":False,
            "stability_verified":False,"gui_responsiveness_verified":False,"checks":[]}
    def save():
        (directory/"result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    save()
    try:
        options={"transport":transport} if transport else {}
        before=await native_request(directory/"before",process_id,**options)
        process_id=before["process_id"]
        result["process_id"]=process_id
        result["before"]=before
        # Always use a unique private directory so no reopen dialog can be triggered by this tool.
        filename=re.sub(r"[^A-Za-z0-9_.-]","_",source.stem)[:100] or "model"
        result["inputs"]=snapshot(source,directory,filename+source.suffix.lower())
        staged=directory/(filename+source.suffix.lower())
        result["loaded_file"]=str(staged)
        save()
        loaded=await native_request(directory/"load",process_id,staged,timeout_seconds,**options)
        result["load_receipt"]=loaded
        result["loaded_model_identity_verified"]=model_matches(loaded,staged)
        if not loaded["open_file_returned_success"] or not loaded["surface_object_count"] or loaded["bounding_box_mm"] is None or not result["loaded_model_identity_verified"]:
            raise RuntimeError("Native receipt did not verify the requested STEP model and its geometry")
        started=time.monotonic()
        # Separate post-load DDE roundtrips require the existing GUI event loop to keep servicing requests.
        for index,delay in enumerate((min(5,observe_seconds),observe_seconds)):
            await asyncio.sleep(max(0,started+delay-time.monotonic()))
            checked=await native_request(directory/f"check-{index+1}",process_id,timeout_seconds=15,**options)
            if not model_matches(checked,staged) or checked["surface_object_count"]!=loaded["surface_object_count"] or checked["bounding_box_mm"] is None or max(abs(a-b) for a,b in zip(checked["bounding_box_mm"],loaded["bounding_box_mm"]))>0.000001:
                raise RuntimeError("Current model identity or geometry changed during the post-load checks")
            result["checks"].append(checked)
            save()
        result["original_source_unchanged"]=hashlib.sha256(source.read_bytes()).hexdigest()==result["inputs"][0]["source_sha256"]
        if not result["original_source_unchanged"]:
            raise RuntimeError("Original STEP changed during the import")
        result.update(status="succeeded",gui_responsiveness_verified=True,
                      responsiveness_observation_seconds=round(time.monotonic()-started,3),
                      surface_object_count=loaded["surface_object_count"],bounding_box_mm=loaded["bounding_box_mm"],dimensions_mm=loaded["dimensions_mm"],
                      verification_scope="Requested STEP identity, native geometry and two post-load API roundtrips in the same existing process; future stability and CAD topology completeness are not certified")
    except Exception as error:
        result.update(status="unverified",error=f"{type(error).__name__}: {error}",
                      operation_may_still_be_running=True,automatic_resubmission_allowed=False)
    finally:
        if result["status"]=="running":
            result.update(status="interrupted",automatic_resubmission_allowed=False)
        save()
    return result
