"""Read (never change) Windows playback settings.

Measured on this machine (tools/cable_gain_check.py): the 'CABLE Input' endpoint volume does NOT scale the
audio passing through the cable, so its level never matters. What does matter is the default playback
device: the keyboard volume keys act on it, so it must be the speakers, not the cable.
"""

from pycaw.pycaw import AudioUtilities


class EndpointReader:
    def __init__(self, name_part):
        self.name_part = name_part.lower()
        self.vol = None

    def gain_db(self):
        """The endpoint's reported level in dB; None if the device is missing."""
        try:
            if self.vol is None:
                for dev in AudioUtilities.GetAllDevices():
                    if dev.FriendlyName and self.name_part in dev.FriendlyName.lower() and dev.state.name == "Active":
                        self.vol = dev.EndpointVolume
                        break
            return None if self.vol is None else self.vol.GetMasterVolumeLevel()
        except Exception:
            self.vol = None
            return None


def default_playback_name():
    try:
        return AudioUtilities.GetSpeakers().FriendlyName
    except Exception:
        return None
