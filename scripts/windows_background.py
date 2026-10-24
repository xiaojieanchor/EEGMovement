"""Create Windows-owned workers via CIM; pass secrets only via a private local pipe."""
import re
import json
import subprocess
import time
import uuid


def validate_pipe_name(name):
    if not isinstance(name,str) or not re.fullmatch('EEGMovement-[a-f0-9]{32}',name):raise ValueError('Invalid private local pipe name')
    return name


def spawn(command,cwd):
    # CIM avoids pywin32 COM dynamic-dispatch crashes on Python 3.13.
    def literal(value):return "'"+str(value).replace("'","''")+"'"
    script="$ErrorActionPreference='Stop';$startup=New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ShowWindow=[UInt16]0};"
    script+='$result=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='+literal(subprocess.list2cmdline(command))+';CurrentDirectory='+literal(cwd)+';ProcessStartupInformation=$startup};$result | Select-Object ProcessId,ReturnValue | ConvertTo-Json -Compress'
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],capture_output=True,text=True,timeout=20,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise RuntimeError('Windows CIM launch failed: '+result.stderr[-250:])
    output=json.loads(result.stdout)
    if output['ReturnValue']:raise RuntimeError(f"Windows background process creation failed ({output['ReturnValue']})")
    return int(output['ProcessId'])


def spawn_with_secret(command,cwd,secret,timeout=20):
    import pywintypes
    import win32api
    import win32file
    import win32pipe
    import win32security
    token=win32security.OpenProcessToken(win32api.GetCurrentProcess(),win32security.TOKEN_QUERY)
    try:sid=win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token,win32security.TokenUser)[0])
    finally:token.Close()
    attributes=pywintypes.SECURITY_ATTRIBUTES()
    attributes.SECURITY_DESCRIPTOR=win32security.ConvertStringSecurityDescriptorToSecurityDescriptor('D:P(A;;GA;;;SY)(A;;GA;;;'+sid+')',1)
    name='EEGMovement-'+uuid.uuid4().hex
    handle=win32pipe.CreateNamedPipe('\\\\.\\pipe\\'+name,win32pipe.PIPE_ACCESS_OUTBOUND|0x80000,
        win32pipe.PIPE_TYPE_MESSAGE|win32pipe.PIPE_READMODE_MESSAGE|win32pipe.PIPE_NOWAIT|8,1,4096,4096,0,attributes)
    try:
        pid=spawn(command+['--password-pipe',name],cwd)
        deadline=time.monotonic()+timeout
        while True:
            try:win32pipe.ConnectNamedPipe(handle,None);break
            except pywintypes.error as error:
                if error.winerror==535:break
                if error.winerror!=536:raise
                if time.monotonic()>=deadline:raise TimeoutError('Background worker did not connect to its private pipe')
                time.sleep(.05)
        win32file.WriteFile(handle,(secret+'\n').encode('utf-8'))
        win32file.FlushFileBuffers(handle)
        return pid
    finally:win32file.CloseHandle(handle)


def receive_secret(name):
    import win32file
    import win32pipe
    path='\\\\.\\pipe\\'+validate_pipe_name(name)
    win32pipe.WaitNamedPipe(path,20000)
    handle=win32file.CreateFile(path,win32file.GENERIC_READ,0,None,win32file.OPEN_EXISTING,0,None)
    try:
        _,data=win32file.ReadFile(handle,4096)
        if not data.endswith(b'\n'):raise ValueError('Incomplete credential message')
        return data.decode('utf-8').rstrip('\r\n')
    finally:win32file.CloseHandle(handle)
