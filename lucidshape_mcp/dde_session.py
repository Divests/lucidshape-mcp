"""One owned, message-pumping DDE worker for one MCP server lifetime."""
import asyncio
import json
import os
from pathlib import Path
import sys
import uuid


class DDESession:
    def __init__(self,root:Path,worker_command=None):
        self.root=root.resolve()
        self.command=worker_command or (sys.executable,"-m","lucidshape_mcp.dde_worker","--serve",str(self.root))
        self.process=None
        self.log=None
        self.lock=asyncio.Lock()

    async def start(self):
        if self.process is not None:
            if self.process.returncode is not None:
                raise RuntimeError("DDE session worker exited; completion of any pending command is unverified")
            return
        self.root.mkdir(parents=True,exist_ok=True)
        self.log=(self.root/("dde-session-"+uuid.uuid4().hex+".log")).open("wb")
        env={**os.environ,"PYTHONPATH":str(Path(__file__).resolve().parents[1])+os.pathsep+os.environ.get("PYTHONPATH","")}
        self.process=await asyncio.create_subprocess_exec(*self.command,cwd=self.root,env=env,
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=self.log)

    async def request(self,directory:Path,request:dict,timeout_seconds:int=30):
        async with self.lock:
            await self.start()
            directory.mkdir(parents=True,exist_ok=True)
            token=uuid.uuid4().hex
            command={"id":token,"directory":str(directory.resolve()),"request":request}
            (directory/"dde-request.json").write_text(json.dumps(request),encoding="utf-8")
            self.process.stdin.write((json.dumps(command)+"\n").encode("utf-8"))
            await self.process.stdin.drain()
            try:
                line=await asyncio.wait_for(self.process.stdout.readline(),timeout_seconds)
            except (asyncio.TimeoutError,asyncio.CancelledError):
                # Kill only our helper. Never restart/resend an uncertain native command.
                if self.process.returncode is None:
                    self.process.kill()
                    await self.process.wait()
                raise
            if not line:
                raise RuntimeError("DDE session worker disconnected; no command will be automatically resubmitted")
            response=json.loads(line)
            if response.get("id")!=token:
                raise RuntimeError("DDE worker response belongs to a different request")
            if response.get("error"):
                raise RuntimeError(response["error"])
            result=response["result"]
            result["persistent_dde_session"]=True
            result["helper_process_id"]=self.process.pid
            (directory/"dde-result.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
            return result

    async def close(self):
        async with self.lock:
            if self.process is not None and self.process.returncode is None:
                try:
                    self.process.stdin.write(b'{"action":"shutdown"}\n')
                    await self.process.stdin.drain()
                    await asyncio.wait_for(self.process.wait(),3)
                except (BrokenPipeError,ConnectionResetError,asyncio.TimeoutError):
                    if self.process.returncode is None:
                        self.process.kill()
                        await self.process.wait()
            if self.log:
                self.log.close()
