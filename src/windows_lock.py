"""Windows process identity and exclusive stale-lock removal.

Recovery checks PID plus creation FILETIME. The lock file remains held without
sharing while it is checked and marked for deletion, avoiding pathname replacement.
API references: Microsoft GetProcessTimes, CreateFileW, SetFileInformationByHandle.
"""
import contextlib,ctypes,json,os,time,uuid
from pathlib import Path


def kernel():
    if os.name!='nt':raise ValueError('Verified library lock recovery currently requires Windows')
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    handle=ctypes.c_void_p;dword=ctypes.c_uint32;boolean=ctypes.c_int
    api.OpenProcess.argtypes=[dword,boolean,dword];api.OpenProcess.restype=handle
    api.CloseHandle.argtypes=[handle];api.CloseHandle.restype=boolean
    api.WaitForSingleObject.argtypes=[handle,dword];api.WaitForSingleObject.restype=dword
    api.GetProcessTimes.argtypes=[handle,*([ctypes.POINTER(ctypes.c_uint64)]*4)];api.GetProcessTimes.restype=boolean
    api.CreateFileW.argtypes=[ctypes.c_wchar_p,dword,dword,handle,dword,dword,handle];api.CreateFileW.restype=handle
    api.ReadFile.argtypes=[handle,handle,dword,ctypes.POINTER(dword),handle];api.ReadFile.restype=boolean
    api.GetFileInformationByHandleEx.argtypes=[handle,ctypes.c_int,handle,dword];api.GetFileInformationByHandleEx.restype=boolean
    api.SetFileInformationByHandle.argtypes=[handle,ctypes.c_int,handle,dword];api.SetFileInformationByHandle.restype=boolean
    return api


def process_state(pid):
    if type(pid) is not int or not 0<pid<2**32:raise ValueError('Invalid lock owner PID')
    api=kernel();handle=api.OpenProcess(0x00101000,False,pid)  # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
    if not handle:
        error=ctypes.get_last_error()
        if error==87:return {'running':False,'creation_filetime':None}
        raise ctypes.WinError(error)
    try:
        wait=api.WaitForSingleObject(handle,0)
        if wait==0:return {'running':False,'creation_filetime':None}
        if wait!=258:raise ctypes.WinError(ctypes.get_last_error())
        values=[ctypes.c_uint64() for _ in range(4)]
        if not api.GetProcessTimes(handle,*[ctypes.byref(x) for x in values]):raise ctypes.WinError(ctypes.get_last_error())
        return {'running':True,'creation_filetime':values[0].value}
    finally:api.CloseHandle(handle)


def new_owner():
    state=process_state(os.getpid())
    if not state['running']:raise ValueError('Cannot establish current library lock owner')
    return dict(schema=2,pid=os.getpid(),creation_filetime=state['creation_filetime'],time=time.time(),token=uuid.uuid4().hex)


def owner_has_exited(owner):
    if owner.get('schema')!=2 or type(owner.get('creation_filetime')) is not int or owner['creation_filetime']<=0:
        raise ValueError('Legacy/unknown lock owner identity; automatic recovery refused')
    state=process_state(owner['pid'])
    return not state['running'] or state['creation_filetime']!=owner['creation_filetime']


@contextlib.contextmanager
def exclusive_file(path):
    """Yield (JSON contents, delete-on-close callback) for this exact held file."""
    api=kernel()
    handle=api.CreateFileW(str(Path(path)),0x80010000,0,None,3,0x00200080,None)  # READ|DELETE, OPEN_EXISTING, OPEN_REPARSE_POINT
    if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    try:
        class Attributes(ctypes.Structure):_fields_=[('attributes',ctypes.c_uint32),('tag',ctypes.c_uint32)]
        info=Attributes()
        if not api.GetFileInformationByHandleEx(handle,9,ctypes.byref(info),ctypes.sizeof(info)):raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & (0x400|0x10):raise ValueError('Lock is a link or directory')
        buffer=ctypes.create_string_buffer(16384);count=ctypes.c_uint32()
        if not api.ReadFile(handle,buffer,len(buffer),ctypes.byref(count),None):raise ctypes.WinError(ctypes.get_last_error())
        if count.value==len(buffer):raise ValueError('Oversized lock record')
        owner=json.loads(buffer.raw[:count.value].decode('utf-8'))
        def discard():
            disposition=ctypes.c_ubyte(1)  # FILE_DISPOSITION_INFO.DeleteFile is BOOLEAN.
            if not api.SetFileInformationByHandle(handle,4,ctypes.byref(disposition),ctypes.sizeof(disposition)):
                raise ctypes.WinError(ctypes.get_last_error())
        yield owner,discard
    finally:api.CloseHandle(handle)
