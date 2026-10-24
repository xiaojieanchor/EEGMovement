"""Bounded immutable JSON snapshots with Windows delete-sharing for atomic writers."""
import json
import os
import time
from pathlib import Path


def open_shared_read(path):
    path=Path(path)
    if os.name!='nt':return path.open('rb')
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    create=kernel.CreateFileW
    create.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,
                     wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    create.restype=wintypes.HANDLE
    close=kernel.CloseHandle;close.argtypes=[wintypes.HANDLE];close.restype=wintypes.BOOL
    # All readers see their complete old snapshot while a writer atomically replaces its name.
    handle=create(str(path.resolve()),0x80000000,0x00000007,None,3,0x80,None)
    if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    try:descriptor=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY)
    except BaseException:close(handle);raise
    return os.fdopen(descriptor,'rb')


def read_shared_json(path,max_bytes=262144):
    for attempt in range(8):
        try:
            with open_shared_read(path) as stream:content=stream.read(max_bytes+1)
            break
        except (FileNotFoundError,PermissionError):
            if attempt==7:raise
            time.sleep(.001*(attempt+1))
    if len(content)>max_bytes:raise ValueError('JSON snapshot exceeds size bound')
    return json.loads(content.decode('utf-8-sig'))


def write_atomic_snapshot(path,content):
    """Preserve the prior local snapshot if an ordinary Windows reader is busy."""
    path=Path(path);temporary=path.with_name('.'+path.name+'.'+str(os.getpid())+'.tmp')
    temporary.write_bytes(content)
    for attempt in range(6):
        try:temporary.replace(path);return True
        except PermissionError:
            if attempt==5:return False
            time.sleep(.005*(attempt+1))
