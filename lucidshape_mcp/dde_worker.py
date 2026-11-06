"""Owned DDE client process. Never launches or closes a LucidStudio instance."""
from __future__ import annotations

import ctypes as c
from ctypes import wintypes as w
import json
from pathlib import Path
import sys
import time
import queue
import threading
import os


def process_integrity(process):
    kernel = c.WinDLL("kernel32", use_last_error=True)
    advapi = c.WinDLL("advapi32", use_last_error=True)
    advapi.OpenProcessToken.argtypes = [c.c_void_p, w.DWORD, c.POINTER(c.c_void_p)]
    advapi.GetTokenInformation.argtypes = [c.c_void_p, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)]
    advapi.GetSidSubAuthorityCount.argtypes = [c.c_void_p]
    advapi.GetSidSubAuthorityCount.restype = c.POINTER(c.c_ubyte)
    advapi.GetSidSubAuthority.argtypes = [c.c_void_p, w.DWORD]
    advapi.GetSidSubAuthority.restype = c.POINTER(w.DWORD)
    kernel.CloseHandle.argtypes = [c.c_void_p]
    token = c.c_void_p()
    if not advapi.OpenProcessToken(process, 8, c.byref(token)):
        return None
    try:
        needed = w.DWORD()
        advapi.GetTokenInformation(token, 25, None, 0, c.byref(needed))
        if not needed.value:
            return None
        data = c.create_string_buffer(needed.value)
        if not advapi.GetTokenInformation(token, 25, data, needed, c.byref(needed)):
            return None
        sid = c.cast(data, c.POINTER(c.c_void_p))[0]
        count = advapi.GetSidSubAuthorityCount(sid)[0]
        return advapi.GetSidSubAuthority(sid, count - 1)[0] if count else None
    finally:
        kernel.CloseHandle(token)


def studio_processes():
    """Process metadata only; no windows, input, or UI inspection."""
    class ProcessEntry(c.Structure):
        _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ProcessID", w.DWORD),
                    ("th32DefaultHeapID", c.c_size_t), ("th32ModuleID", w.DWORD), ("cntThreads", w.DWORD),
                    ("th32ParentProcessID", w.DWORD), ("pcPriClassBase", w.LONG), ("dwFlags", w.DWORD),
                    ("szExeFile", w.WCHAR * 260)]
    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = c.c_void_p
    kernel.Process32FirstW.argtypes = [c.c_void_p, c.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes = [c.c_void_p, c.POINTER(ProcessEntry)]
    kernel.CloseHandle.argtypes = [c.c_void_p]
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = c.c_void_p
    kernel.QueryFullProcessImageNameW.argtypes = [c.c_void_p, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)]
    kernel.GetProcessTimes.argtypes = [c.c_void_p, c.POINTER(w.FILETIME), c.POINTER(w.FILETIME), c.POINTER(w.FILETIME), c.POINTER(w.FILETIME)]
    kernel.ProcessIdToSessionId.argtypes = [w.DWORD, c.POINTER(w.DWORD)]
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle in (None, c.c_void_p(-1).value):
        raise RuntimeError("Cannot read the Studio process inventory")
    result = []
    try:
        entry = ProcessEntry(dwSize=c.sizeof(ProcessEntry))
        found = kernel.Process32FirstW(handle, c.byref(entry))
        while found:
            if entry.szExeFile.casefold() == "lucidshape.exe":
                item = {"process_id": entry.th32ProcessID, "executable_name": entry.szExeFile}
                process = kernel.OpenProcess(0x1000, False, entry.th32ProcessID)
                if process:
                    try:
                        item["integrity_rid"] = process_integrity(process)
                        session_id = w.DWORD()
                        if kernel.ProcessIdToSessionId(entry.th32ProcessID, c.byref(session_id)):
                            item["session_id"] = session_id.value
                        image = c.create_unicode_buffer(32768)
                        length = w.DWORD(len(image))
                        if kernel.QueryFullProcessImageNameW(process, 0, image, c.byref(length)):
                            item["executable"] = image.value
                        times = [w.FILETIME() for _ in range(4)]
                        if kernel.GetProcessTimes(process, *(c.byref(value) for value in times)):
                            ticks = [(value.dwHighDateTime << 32) | value.dwLowDateTime for value in times]
                            item["creation_time_windows_ticks"] = ticks[0]
                            item["cpu_seconds"] = (ticks[2] + ticks[3]) / 10000000
                    finally:
                        kernel.CloseHandle(process)
                result.append(item)
            found = kernel.Process32NextW(handle, c.byref(entry))
    finally:
        kernel.CloseHandle(handle)
    return result


def direct_dde_probe(process_id):
    """Bounded, read-only DDE handshake with the selected process; no UI input."""
    user = c.WinDLL("user32", use_last_error=True)
    kernel = c.WinDLL("kernel32", use_last_error=True)
    wndproc_type = c.WINFUNCTYPE(c.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
    enum_type = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

    class WindowClass(c.Structure):
        _fields_ = [("style", w.UINT), ("lpfnWndProc", wndproc_type), ("cbClsExtra", c.c_int),
                    ("cbWndExtra", c.c_int), ("hInstance", c.c_void_p), ("hIcon", c.c_void_p),
                    ("hCursor", c.c_void_p), ("hbrBackground", c.c_void_p),
                    ("lpszMenuName", w.LPCWSTR), ("lpszClassName", w.LPCWSTR)]

    kernel.GetModuleHandleW.argtypes = [w.LPCWSTR]
    kernel.GetModuleHandleW.restype = c.c_void_p
    kernel.GlobalAddAtomW.argtypes = [w.LPCWSTR]
    kernel.GlobalAddAtomW.restype = w.WORD
    kernel.GlobalDeleteAtom.argtypes = [w.WORD]
    kernel.GlobalGetAtomNameW.argtypes = [w.WORD, w.LPWSTR, c.c_int]
    kernel.GlobalGetAtomNameW.restype = w.UINT
    user.RegisterClassW.argtypes = [c.POINTER(WindowClass)]
    user.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, c.c_int, c.c_int, c.c_int, c.c_int,
                                    w.HWND, c.c_void_p, c.c_void_p, c.c_void_p]
    user.CreateWindowExW.restype = w.HWND
    user.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user.DefWindowProcW.restype = c.c_ssize_t
    user.EnumWindows.argtypes = [enum_type, w.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
    user.SendMessageTimeoutW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM, w.UINT, w.UINT, c.POINTER(c.c_size_t)]
    user.SendMessageTimeoutW.restype = c.c_size_t
    user.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user.DestroyWindow.argtypes = [w.HWND]
    user.UnregisterClassW.argtypes = [w.LPCWSTR, c.c_void_p]
    acknowledgements = []

    @wndproc_type
    def callback(hwnd, message, sender, parameter):
        if message == 0x3E4:  # WM_DDE_ACK for WM_DDE_INITIATE uses atom pairs.
            service = c.create_unicode_buffer(256)
            topic = c.create_unicode_buffer(256)
            kernel.GlobalGetAtomNameW(parameter & 0xFFFF, service, len(service))
            kernel.GlobalGetAtomNameW((parameter >> 16) & 0xFFFF, topic, len(topic))
            acknowledgements.append({"endpoint": int(sender), "service": service.value, "topic": topic.value})
            kernel.GlobalDeleteAtom(parameter & 0xFFFF)
            kernel.GlobalDeleteAtom((parameter >> 16) & 0xFFFF)
            return 0
        if message == 0x3E1:  # Complete the protocol termination.
            return 0
        return user.DefWindowProcW(hwnd, message, sender, parameter)

    instance = kernel.GetModuleHandleW(None)
    class_name = "LucidMCPDDEProbe_" + str(os.getpid())
    window_class = WindowClass(lpfnWndProc=callback, hInstance=instance, lpszClassName=class_name)
    if not user.RegisterClassW(c.byref(window_class)):
        raise RuntimeError("Could not create the private DDE diagnostic endpoint")
    client = user.CreateWindowExW(0, class_name, "", 0, 0, 0, 0, 0, None, None, instance, None)
    app_atom = kernel.GlobalAddAtomW("LucidShape.exe")
    topic_atom = kernel.GlobalAddAtomW("System")
    targets = []

    @enum_type
    def collect(hwnd, unused):
        pid = w.DWORD()
        user.GetWindowThreadProcessId(hwnd, c.byref(pid))
        if pid.value == process_id:
            targets.append(hwnd)
        return True

    try:
        if not client or not app_atom or not topic_atom:
            raise RuntimeError("Could not initialize the private DDE diagnostic endpoint")
        user.EnumWindows(collect, 0)
        results = []
        for hwnd in targets[:16]:
            response = c.c_size_t()
            responsive = bool(user.SendMessageTimeoutW(hwnd, 0, 0, 0, 2, 500, c.byref(response)))
            count_before = len(acknowledgements)
            accepted = False
            if responsive:
                user.SendMessageTimeoutW(hwnd, 0x3E0, client, app_atom | (topic_atom << 16), 2, 750, c.byref(response))
                accepted = len(acknowledgements) > count_before
                if not accepted:
                    # Standard wildcard initiation asks the target for its own
                    # service/topic, rather than guessing another command API.
                    user.SendMessageTimeoutW(hwnd, 0x3E0, client, 0, 2, 750, c.byref(response))
                    accepted = len(acknowledgements) > count_before
            results.append({"endpoint": int(hwnd), "responsive": responsive, "dde_acknowledged": accepted})
        for peer in {item["endpoint"] for item in acknowledgements}:
            user.PostMessageW(peer, 0x3E1, client, 0)
        return {"process_id": process_id, "endpoints_checked": len(results), "endpoints": results,
                "acknowledged": bool(acknowledgements), "advertised_services": acknowledgements, "command_sent": False}
    finally:
        if app_atom:
            kernel.GlobalDeleteAtom(app_atom)
        if topic_atom:
            kernel.GlobalDeleteAtom(topic_atom)
        if client:
            user.DestroyWindow(client)
        user.UnregisterClassW(class_name, instance)


class SecurityQos(c.Structure):
    _fields_=[("Length",w.DWORD),("ImpersonationLevel",c.c_int),("ContextTrackingMode",c.c_byte),("EffectiveOnly",c.c_byte)]


class ConvContext(c.Structure):
    _fields_=[("cb",w.UINT),("wFlags",w.UINT),("wCountryID",w.UINT),("iCodePage",c.c_int),("dwLangID",w.DWORD),("dwSecurity",w.DWORD),("qos",SecurityQos)]


class ConvInfo(c.Structure):
    _fields_=[("cb",w.DWORD),("hUser",c.c_size_t),("hConvPartner",c.c_void_p),
              ("hszSvcPartner",c.c_void_p),("hszServiceReq",c.c_void_p),("hszTopic",c.c_void_p),("hszItem",c.c_void_p),
              ("wFmt",w.UINT),("wType",w.UINT),("wStatus",w.UINT),("wConvst",w.UINT),("wLastError",w.UINT),
              ("hConvList",c.c_void_p),("ConvCtxt",ConvContext),("hwnd",w.HWND),("hwndPartner",w.HWND)]


class DDE:
    def __init__(self):
        self.user=c.WinDLL("user32",use_last_error=True)
        self.callback_type=c.WINFUNCTYPE(c.c_void_p,w.UINT,w.UINT,c.c_void_p,c.c_void_p,c.c_void_p,c.c_void_p,c.c_size_t,c.c_size_t)
        self.callback=self.callback_type(lambda *args:None)
        signatures={
            "DdeInitializeW":([c.POINTER(w.DWORD),self.callback_type,w.DWORD,w.DWORD],w.UINT),
            "DdeCreateStringHandleW":([w.DWORD,w.LPCWSTR,c.c_int],c.c_void_p),
            "DdeFreeStringHandle":([w.DWORD,c.c_void_p],w.BOOL),
            "DdeConnect":([w.DWORD,c.c_void_p,c.c_void_p,c.c_void_p],c.c_void_p),
            "DdeDisconnect":([c.c_void_p],w.BOOL),
            "DdeUninitialize":([w.DWORD],w.BOOL),
            "DdeGetLastError":([w.DWORD],w.UINT),
            "DdeQueryConvInfo":([c.c_void_p,w.DWORD,c.POINTER(ConvInfo)],w.UINT),
            "DdeConnectList":([w.DWORD,c.c_void_p,c.c_void_p,c.c_void_p,c.c_void_p],c.c_void_p),
            "DdeQueryNextServer":([c.c_void_p,c.c_void_p],c.c_void_p),
            "DdeDisconnectList":([c.c_void_p],w.BOOL),
            "DdeQueryStringW":([w.DWORD,c.c_void_p,w.LPWSTR,w.DWORD,c.c_int],w.DWORD),
            "DdeClientTransaction":([c.c_void_p,w.DWORD,c.c_void_p,c.c_void_p,w.UINT,w.UINT,w.DWORD,c.POINTER(w.DWORD)],c.c_void_p),
            "GetWindowThreadProcessId":([w.HWND,c.POINTER(w.DWORD)],w.DWORD),
            "PeekMessageW":([c.POINTER(w.MSG),w.HWND,w.UINT,w.UINT,w.UINT],w.BOOL),
            "TranslateMessage":([c.POINTER(w.MSG)],w.BOOL),
            "DispatchMessageW":([c.POINTER(w.MSG)],c.c_ssize_t),
        }
        for name,(arguments,result) in signatures.items():
            function=getattr(self.user,name)
            function.argtypes=arguments
            function.restype=result
        self.instance=w.DWORD()
        error=self.user.DdeInitializeW(c.byref(self.instance),self.callback,0x10,0)
        if error:
            raise RuntimeError(f"DdeInitialize failed: {error}")
        self.conversation=None
        self.handles=[]
        self.connection_state=None

    def connect(self):
        if self.conversation and self.connection_state:
            info=ConvInfo(cb=c.sizeof(ConvInfo))
            if self.user.DdeQueryConvInfo(self.conversation,0xFFFFFFFF,c.byref(info)) and info.wStatus&1:
                return dict(self.connection_state)
            self.user.DdeDisconnect(self.conversation)
            self.conversation=None
            self.connection_state=None
        attempts=[]
        kernel=c.WinDLL("kernel32",use_last_error=True)
        kernel.GetCurrentThreadId.restype=w.DWORD
        kernel.ProcessIdToSessionId.argtypes=[w.DWORD,c.POINTER(w.DWORD)]
        self.user.GetThreadDesktop.argtypes=[w.DWORD]
        self.user.GetThreadDesktop.restype=c.c_void_p
        self.user.GetProcessWindowStation.restype=c.c_void_p
        self.user.GetUserObjectInformationW.argtypes=[c.c_void_p,c.c_int,c.c_void_p,w.DWORD,c.POINTER(w.DWORD)]
        def object_name(handle):
            value=c.create_unicode_buffer(256)
            needed=w.DWORD()
            if self.user.GetUserObjectInformationW(handle,2,value,c.sizeof(value),c.byref(needed)):
                return value.value
            return None
        session_id=w.DWORD()
        kernel.ProcessIdToSessionId(os.getpid(),c.byref(session_id))
        context={"pid":os.getpid(),"session_id":session_id.value,"desktop":object_name(self.user.GetThreadDesktop(kernel.GetCurrentThreadId())),"window_station":object_name(self.user.GetProcessWindowStation())}
        kernel.GlobalGetAtomNameW.argtypes=[w.WORD,w.LPWSTR,c.c_int]
        kernel.GlobalGetAtomNameW.restype=w.UINT
        atom_names=[]
        text=c.create_unicode_buffer(256)
        for atom in range(0xC000,0x10000):
            if kernel.GlobalGetAtomNameW(atom,text,256) and "lucid" in text.value.lower():
                atom_names.append(text.value)
        # Installed binary includes LucidStudio, LucidShape and their DDE shell registrations.
        for service_name in dict.fromkeys(("LucidShape.exe","LucidShape","LucidStudio","Lucid",*atom_names)):
            service=self.user.DdeCreateStringHandleW(self.instance,service_name,1200)
            topic=self.user.DdeCreateStringHandleW(self.instance,"System",1200)
            self.handles.extend((service,topic))
            self.conversation=self.user.DdeConnect(self.instance,service,topic,None)
            attempts.append({"service":service_name,"topic":"System","dde_error":self.user.DdeGetLastError(self.instance)})
            if self.conversation:
                break
        # A previous DDE disconnect is asynchronous in Studio. Retry only the read-only handshake;
        # never retry a command which may already have executed.
        if not self.conversation:
            service_name="LucidShape.exe"
            service=self.user.DdeCreateStringHandleW(self.instance,service_name,1200)
            topic=self.user.DdeCreateStringHandleW(self.instance,"System",1200)
            self.handles.extend((service,topic))
            for retry in range(10):
                time.sleep(0.3)
                self.conversation=self.user.DdeConnect(self.instance,service,topic,None)
                if self.conversation:
                    break
        if not self.conversation:
            candidates=[]
            conversation_list=self.user.DdeConnectList(self.instance,None,None,None,None)
            if conversation_list:
                conversation=None
                try:
                    while True:
                        conversation=self.user.DdeQueryNextServer(conversation_list,conversation)
                        if not conversation:
                            break
                        info=ConvInfo(cb=c.sizeof(ConvInfo))
                        self.user.DdeQueryConvInfo(conversation,0xFFFFFFFF,c.byref(info))
                        service_text=c.create_unicode_buffer(256)
                        topic_text=c.create_unicode_buffer(256)
                        self.user.DdeQueryStringW(self.instance,info.hszSvcPartner,service_text,256,1200)
                        self.user.DdeQueryStringW(self.instance,info.hszTopic,topic_text,256,1200)
                        if "lucid" in service_text.value.lower():
                            candidates.append({"service":service_text.value,"topic":topic_text.value})
                finally:
                    self.user.DdeDisconnectList(conversation_list)
            return {"connected":False,"attempts":attempts,"discovered_lucid_services":candidates,"lucid_global_atoms":atom_names,"client_context":context}
        info=ConvInfo(cb=c.sizeof(ConvInfo))
        if not self.user.DdeQueryConvInfo(self.conversation,0xFFFFFFFF,c.byref(info)):
            raise RuntimeError("Cannot verify the connected DDE partner")
        pid=w.DWORD()
        self.user.GetWindowThreadProcessId(info.hwndPartner,c.byref(pid))
        kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD]
        kernel.OpenProcess.restype=c.c_void_p
        kernel.QueryFullProcessImageNameW.argtypes=[c.c_void_p,w.DWORD,w.LPWSTR,c.POINTER(w.DWORD)]
        kernel.CloseHandle.argtypes=[c.c_void_p]
        process=kernel.OpenProcess(0x1000,False,pid.value)
        image=c.create_unicode_buffer(32768)
        length=w.DWORD(len(image))
        try:
            if not process or not kernel.QueryFullProcessImageNameW(process,0,image,c.byref(length)) or Path(image.value).name.lower()!="lucidshape.exe":
                raise RuntimeError("DDE partner is not a verifiable LucidShape.exe process")
        finally:
            if process:
                kernel.CloseHandle(process)
        self.connection_state={"connected":True,"service":service_name,"topic":"System","process_id":pid.value,"executable":image.value,"conversation_state":info.wConvst}
        return dict(self.connection_state)

    def execute_file(self,path:Path,timeout_ms:int):
        text='[execute("'+path.as_posix()+'")]'
        data=(text+"\0").encode("utf-16le")
        buffer=c.create_string_buffer(data)
        status=w.DWORD()
        ok=self.user.DdeClientTransaction(buffer,len(data),self.conversation,None,0,0x4050,timeout_ms,c.byref(status))
        return {"acknowledged":bool(ok),"dde_status":status.value,"dde_error":self.user.DdeGetLastError(self.instance)}

    def pump(self):
        message=w.MSG()
        while self.user.PeekMessageW(c.byref(message),None,0,0,1):
            self.user.TranslateMessage(c.byref(message))
            self.user.DispatchMessageW(c.byref(message))

    def wait_for_receipt(self,path:Path,deadline:float):
        # The execute ACK precedes Studio's queued native script. Keep this DDE conversation
        # and its client message loop alive until the native report is fully written.
        while time.monotonic()<deadline:
            self.pump()
            try:
                json.loads(path.read_text(encoding="utf-8-sig"))
                settle=time.monotonic()+0.2
                while time.monotonic()<settle:
                    self.pump()
                    time.sleep(0.01)
                return True
            except (FileNotFoundError,json.JSONDecodeError):
                time.sleep(0.05)
        return False

    def close(self):
        if self.conversation:
            self.user.DdeDisconnect(self.conversation)
        for handle in self.handles:
            if handle:
                self.user.DdeFreeStringHandle(self.instance,handle)
        self.user.DdeUninitialize(self.instance)


def perform(client:DDE,data:dict,directory:Path):
    if data["action"]=="process_inventory":
        result={"connected":None,"connection_probed":False,"running_studio_processes":studio_processes(),"application_command_sent":False}
        (directory/"dde-result.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
        return result
    result=client.connect()
    if data["action"]=="probe":
        result["running_studio_processes"]=studio_processes()
        kernel=c.WinDLL("kernel32",use_last_error=True)
        kernel.GetCurrentProcess.restype=c.c_void_p
        result["client_integrity_rid"]=process_integrity(kernel.GetCurrentProcess())
        if not result["connected"] and len(result["running_studio_processes"])==1:
            result["direct_dde_probe"]=direct_dde_probe(result["running_studio_processes"][0]["process_id"])
    if data["action"]=="execute_file":
        if not result["connected"]:
            raise RuntimeError(f"No existing LucidStudio DDE server: {result}")
        if data.get("process_id") and result["process_id"]!=data["process_id"]:
            raise RuntimeError("DDE partner differs from the selected LucidStudio process")
        script=(directory/data["script_name"]).resolve(strict=True)
        if script.parent!=directory or script.suffix!=".do":
            raise ValueError("DDE script must be a fixed .do file in this private job")
        deadline=time.monotonic()+data.get("timeout_ms",30000)/1000
        result.update(client.execute_file(script,data.get("timeout_ms",30000)))
        if result["acknowledged"] and data.get("receipt_name"):
            receipt=(directory/data["receipt_name"]).resolve()
            if receipt.parent!=directory:
                raise ValueError("Receipt must stay in the private request directory")
            result["native_receipt_completed"]=client.wait_for_receipt(receipt,deadline)
    elif data["action"]!="probe":
        raise ValueError("Unknown DDE action")
    (directory/"dde-result.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    return result


def serve(root:Path):
    messages=queue.Queue()
    def read_commands():
        for line in sys.stdin:
            messages.put(line)
        messages.put(None)
    threading.Thread(target=read_commands,daemon=True).start()
    client=DDE()
    try:
        while True:
            client.pump()
            try:
                line=messages.get(timeout=0.02)
            except queue.Empty:
                continue
            if line is None:
                break
            command=json.loads(line)
            if command.get("action")=="shutdown":
                break
            try:
                directory=Path(command["directory"]).resolve(strict=True)
                if directory!=root and root not in directory.parents:
                    raise ValueError("DDE request directory is outside the configured root")
                result=perform(client,command["request"],directory)
                response={"id":command["id"],"result":result}
            except Exception as error:
                response={"id":command["id"],"error":f"{type(error).__name__}: {error}"}
            print(json.dumps(response),flush=True)
    finally:
        client.close()


def main():
    if sys.argv[1]=="--serve":
        serve(Path(sys.argv[2]).resolve(strict=True))
        return
    request=Path(sys.argv[1]).resolve(strict=True)
    data=json.loads(request.read_text(encoding="utf-8"))
    client=DDE()
    try:
        print(json.dumps(perform(client,data,request.parent)),flush=True)
    finally:
        client.close()


if __name__=="__main__":
    main()
