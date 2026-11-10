"""Per-app playback device, the same setting as Settings > Sound > Volume mixer > <app> > Output device.

Windows has no public API for this. Like EarTrumpet, this uses the internal WinRT factory
"Windows.Media.Internal.AudioPolicyConfig" (interface ab3d4648-..., Windows 10 21H2 and later). Its vtable
after IInspectable holds 19 unrelated methods, then SetPersistedDefaultAudioEndpoint,
GetPersistedDefaultAudioEndpoint and ClearAllPersistedApplicationDefaultEndpoints. Only Spotify's own
routing is touched; an empty device id means "Default".
"""

import ctypes
import subprocess
from ctypes import wintypes

from pycaw.pycaw import AudioUtilities   # importing comtypes also initialises COM on this thread

E_RENDER = 0
ROLES = (0, 1)                       # eConsole, eMultimedia: what the Volume mixer sets together
RENDER_IFACE = "{e6327cad-dcec-4949-ae8a-991e976a79d2}"
MMDEV_PREFIX = "\\\\?\\SWD#MMDEVAPI#"
VTBL_SET, VTBL_GET = 6 + 19, 6 + 20

_combase = ctypes.WinDLL("combase")
_combase.WindowsCreateString.argtypes = [wintypes.LPCWSTR, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p)]
_combase.WindowsGetStringRawBuffer.restype = wintypes.LPCWSTR
_combase.WindowsGetStringRawBuffer.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.UINT)]
_combase.WindowsDeleteString.argtypes = [ctypes.c_void_p]
_combase.RoGetActivationFactory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]


class _GUID(ctypes.Structure):
    _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, s):
        import uuid
        u = uuid.UUID(s)
        g = cls(u.time_low, u.time_mid, u.time_hi_version)
        g.d4[:] = list(u.bytes[8:])
        return g


def _hstring(s):
    h = ctypes.c_void_p()
    if s:
        hr = _combase.WindowsCreateString(s, len(s), ctypes.byref(h))
        if hr:
            raise OSError(f"WindowsCreateString failed 0x{hr & 0xFFFFFFFF:08x}")
    return h


_factory = None


def _get_factory():
    global _factory
    if _factory is None:
        name = _hstring("Windows.Media.Internal.AudioPolicyConfig")
        iid = _GUID.parse("ab3d4648-e242-459f-b02f-541c70306324")
        ptr = ctypes.c_void_p()
        hr = _combase.RoGetActivationFactory(name, ctypes.byref(iid), ctypes.byref(ptr))
        _combase.WindowsDeleteString(name)
        if hr:
            raise OSError(f"AudioPolicyConfig factory unavailable (0x{hr & 0xFFFFFFFF:08x})")
        _factory = ptr
    return _factory


def _method(index, *argtypes):
    vtbl = ctypes.cast(_get_factory(), ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(vtbl[index])


def get_app_device(pid):
    """Device id the app (by process id) is routed to, '' for Default, None for a helper process
    (Spotify runs several; only the one that plays audio is accepted by the API)."""
    get = _method(VTBL_GET, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))
    h = ctypes.c_void_p()
    try:
        get(_get_factory(), pid, E_RENDER, 1, ctypes.byref(h))
    except OSError:
        return None
    if not h:
        return ""
    s = _combase.WindowsGetStringRawBuffer(h, None) or ""
    _combase.WindowsDeleteString(h)
    return s


def set_app_device(pid, mmdevice_id):
    """Route the app to an MMDevice id like '{0.0.0.00000000}.{guid}', or '' / None for Default."""
    full = f"{MMDEV_PREFIX}{mmdevice_id}#{RENDER_IFACE}" if mmdevice_id else ""
    put = _method(VTBL_SET, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_void_p)
    for role in ROLES:
        h = _hstring(full)
        try:
            put(_get_factory(), pid, E_RENDER, role, h)
        finally:
            if h:
                _combase.WindowsDeleteString(h)


def pids(image="Spotify.exe"):
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, creationflags=0x08000000).stdout
    return [int(line.split('","')[1]) for line in out.splitlines() if line.startswith('"')]


def device_id(name_part):
    for d in AudioUtilities.GetAllDevices():
        if d.FriendlyName and name_part.lower() in d.FriendlyName.lower() and d.id.startswith("{0.0.0."):
            return d.id
    return None


def route_spotify(to_cable):
    """Send every running Spotify process to CABLE Input (True) or back to Default (False). Returns pids touched."""
    target = device_id("CABLE Input") if to_cable else ""
    if to_cable and not target:
        raise OSError("CABLE Input not found")
    touched = []
    for pid in pids():
        try:
            set_app_device(pid, target)
            touched.append(pid)
        except OSError:
            pass                      # helper process without audio
    return touched


def spotify_on_cable():
    """True / False for the audio-playing Spotify process, None if Spotify is not running."""
    states = [get_app_device(p) for p in pids()]
    states = [s for s in states if s is not None]
    if not states:
        return None
    cable = device_id("CABLE Input")
    return bool(cable) and any(cable in s for s in states)
