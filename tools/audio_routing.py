"""Read-only report of Windows playback routing: default device and each endpoint's master volume.

usage: python tools/audio_routing.py
"""

from pycaw.pycaw import AudioUtilities

default = AudioUtilities.GetSpeakers()
print("Default playback device:", default.FriendlyName)
for dev in AudioUtilities.GetAllDevices():
    if dev.state.name != "Active" or dev.FriendlyName is None:
        continue
    try:
        vol = dev.EndpointVolume
        print(f"  {dev.FriendlyName:55} volume {vol.GetMasterVolumeLevelScalar():5.0%}  muted={bool(vol.GetMute())}")
    except Exception:
        pass
