"""Launch one private Studio job. Does not attach to or close any existing instance."""
from __future__ import annotations
import asyncio,json,subprocess,time
from pathlib import Path

_PROCESSES = {}

async def run_private_studio(executable:Path,directory:Path,script_text:str,receipt_name:str,timeout_seconds:int=120)->dict:
    executable=Path(executable).resolve();directory=Path(directory).resolve()
    if not executable.is_file():raise ValueError('LucidShape.exe not found')
    if isinstance(timeout_seconds,bool) or not isinstance(timeout_seconds,int) or not 1<=timeout_seconds<=600:raise ValueError('timeout_seconds must be 1..600')
    if Path(receipt_name).name!=receipt_name:raise ValueError('receipt_name must be a basename')
    directory.mkdir(parents=True,exist_ok=True)
    receipt=directory/receipt_name
    if receipt.exists():raise ValueError('Private receipt already exists; refuse replay')
    script=directory/'native-job.lus';script.write_text(script_text,encoding='utf-8')
    startup=None
    if hasattr(subprocess,'STARTUPINFO'):
        startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
    with (directory/'launch.log').open('wb') as log:
        process=subprocess.Popen([str(executable),str(script)],cwd=executable.parent,stdin=subprocess.DEVNULL,stdout=log,stderr=log,startupinfo=startup)
    _PROCESSES[process.pid] = process
    result={'status':'unverified','launcher_pid':process.pid,'executable':str(executable),'script':str(script),'receipt_file':str(receipt),'private_process_started':True,'existing_processes_modified':False,'computer_use_calls':0,'process_left_open':True,'automatic_resubmission_allowed':False}
    (directory/'launch.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    deadline=time.monotonic()+timeout_seconds
    try:
        while time.monotonic()<deadline:
            if receipt.is_file() and 0<receipt.stat().st_size<=1048576:
                try:json.loads(receipt.read_text(encoding='utf-8-sig'))
                except (ValueError,UnicodeError):pass
                else:
                    if process.poll() is not None:
                        result['error']='Launching Studio exited; ownership or future rendering is unverified'
                    else:result['status']='receipt_ready'
                    break
            if process.poll() is not None:
                result['error']='Studio exited before producing a native receipt';result['exit_code']=process.returncode;break
            await asyncio.sleep(.1)
        else:result['error']='No complete native receipt before deadline; launched private process left open'
    except asyncio.CancelledError:
        result['status']='interrupted';result['error']='Caller interrupted; private process left open, do not automatically resubmit'
        raise
    finally:(directory/'launch.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result

async def close_private_studio(launch:dict)->dict:
    """Close only the still-owned Popen handle; never attach to arbitrary PIDs."""
    pid=launch.get('launcher_pid')
    process=_PROCESSES.get(pid)
    if process is None or not launch.get('private_process_started'):
        return {'closed':False,'reason':'No owned Popen handle; left untouched'}
    if process.poll() is None:
        try:
            process.terminate()
            await asyncio.to_thread(process.wait,timeout=10)
        except (OSError,subprocess.TimeoutExpired) as exc:
            return {'closed':False,'pid':pid,'reason':str(exc)}
    _PROCESSES.pop(pid,None)
    return {'closed':True,'pid':pid,'exit_code':process.returncode,'method':'owned Popen handle terminate after native artifact verification','existing_processes_modified':False}
