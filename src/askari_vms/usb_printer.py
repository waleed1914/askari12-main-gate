"""Direct USB access to a printer-class device, bypassing the Windows spooler.

An 80mm POS printer enumerates under usbprint.sys and exposes a device interface that
accepts ESC/POS bytes with no queue and no driver installed. That matters for the gate
PCs: plug the printer in and it works, with nothing to configure per machine.

ctypes note: every Win32 call here declares restype/argtypes. Without them ctypes
assumes c_int and silently truncates 64-bit handles, which makes the enumeration return
nothing and looks exactly like "no printer attached".
"""

import ctypes
from ctypes import wintypes

setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

# Without explicit restypes ctypes assumes c_int and truncates 64-bit handles, which
# makes every later call fail silently.
setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
setupapi.SetupDiGetClassDevsW.argtypes = [
    ctypes.c_void_p, wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
kernel32.WriteFile.argtypes = [
    wintypes.HANDLE, ctypes.c_char_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", wintypes.DWORD), ("Reserved", ctypes.POINTER(wintypes.ULONG))]


class SP_DEVICE_INTERFACE_DETAIL_DATA_W(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("DevicePath", wintypes.WCHAR * 1024)]


# GUID_DEVINTERFACE_USBPRINT
USBPRINT = GUID(0x28D78FAD, 0x5A12, 0x11D1,
                (ctypes.c_ubyte * 8)(0xAE, 0x5B, 0x00, 0x00, 0xF8, 0x03, 0xA8, 0xC2))


def device_paths():
    handle = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(USBPRINT), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if handle == INVALID_HANDLE_VALUE:
        raise OSError("SetupDiGetClassDevs failed: %d" % ctypes.get_last_error())
    found = []
    interface = SP_DEVICE_INTERFACE_DATA()
    interface.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
    index = 0
    while setupapi.SetupDiEnumDeviceInterfaces(
            handle, None, ctypes.byref(USBPRINT), index, ctypes.byref(interface)):
        detail = SP_DEVICE_INTERFACE_DETAIL_DATA_W()
        detail.cbSize = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
        required = wintypes.DWORD()
        setupapi.SetupDiGetDeviceInterfaceDetailW(
            handle, ctypes.byref(interface), ctypes.byref(detail),
            ctypes.sizeof(detail), ctypes.byref(required), None)
        found.append(detail.DevicePath)
        index += 1
    setupapi.SetupDiDestroyDeviceInfoList(handle)
    return found


def write_direct(path: str, payload: bytes) -> int:
    handle = kernel32.CreateFileW(
        path, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
        None, OPEN_EXISTING, 0, None)
    if handle == INVALID_HANDLE_VALUE:
        raise OSError("CreateFile failed: %d" % ctypes.get_last_error())
    try:
        written = wintypes.DWORD()
        ok = kernel32.WriteFile(handle, payload, len(payload), ctypes.byref(written), None)
        if not ok:
            raise OSError("WriteFile failed: %d" % ctypes.get_last_error())
        return written.value
    finally:
        kernel32.CloseHandle(handle)


