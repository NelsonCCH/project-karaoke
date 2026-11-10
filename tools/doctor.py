"""Check that this PC is ready for Project Karaoke, and say how to fix what is not. Changes nothing.

usage: .venv\\Scripts\\python tools\\doctor.py [--speed [model-key]]

  --speed   also load the default model (downloads 1.7 GB the first time) and time it on this GPU at every
            delay setting, so you know which delays this machine can run live. Name a model key from
            karaoke/models.py to time that one instead.

Each line is PASS, WARN (works, but read the note) or FAIL (fix before using). Exit code 1 if anything FAILs.
"""

import os
import shutil
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

failed = False


def report(level, what, note=""):
    global failed
    failed |= level == "FAIL"
    print(f"{level:4}  {what}" + (f"\n      {note}" if note else ""))


def check_python():
    v = sys.version_info
    if v[:2] != (3, 11):
        report("WARN", f"Python {v.major}.{v.minor}", "requirements.lock.txt was resolved on Python 3.11.")
    else:
        report("PASS", f"Python {v.major}.{v.minor}.{v.micro}")
    if sys.prefix == sys.base_prefix:
        report("WARN", "not running inside a virtual environment", r"Run it as .venv\Scripts\python tools\doctor.py")


def check_folder():
    if "onedrive" in ROOT.lower():
        report("WARN", f"the project is inside OneDrive ({ROOT})",
               "OneDrive would sync ~6 GB of packages and models. Move the folder (e.g. to C:\\Projects) and "
               "recreate .venv there.")
    free = shutil.disk_usage(ROOT).free / 2 ** 30
    models = os.path.join(ROOT, "models")
    have = os.path.isdir(models) and any(f.endswith(".ckpt") for f in os.listdir(models))
    if free < (2 if have else 4):
        report("FAIL" if free < 1 else "WARN", f"only {free:.1f} GB free on this drive",
               "The default model is 1.7 GB, and every other model you pick downloads 0.2-0.9 GB more.")
    else:
        report("PASS", f"{free:.0f} GB free on this drive")


def check_packages():
    try:
        import torch  # noqa: F401
        import audio_separator  # noqa: F401
        from karaoke import approuting, pipeline, session, transport  # noqa: F401
    except Exception as e:
        report("FAIL", f"packages: {type(e).__name__}: {e}",
               r"pip install -r requirements.lock.txt --extra-index-url https://download.pytorch.org/whl/cu128")
        return False
    report("PASS", "packages import")
    return True


def check_gpu():
    import torch
    if not torch.cuda.is_available():
        report("FAIL", f"no CUDA GPU visible to torch {torch.__version__}",
               "Needs an NVIDIA GPU, a current driver, and the CUDA build of torch (the cu128 index above).")
        return False
    p = torch.cuda.get_device_properties(0)
    arch = f"sm_{p.major}{p.minor}"
    try:
        # is_available() is also True on cards this torch build has no kernels for; only running one tells.
        assert (torch.ones(4, device="cuda") * 2).sum().item() == 8
    except Exception as e:
        report("FAIL", f"GPU {p.name} ({arch}) cannot run this torch build: {str(e)[:120]}",
               f"This build supports {', '.join(torch.cuda.get_arch_list())} (GTX 16xx / RTX 20xx and newer). "
               "Older cards are not supported. Also update the NVIDIA driver (570 or newer for CUDA 12.8).")
        return False
    gb = p.total_memory / 2 ** 30
    note = "" if gb >= 6 else "Under 6 GB of VRAM: use a short delay and check with --speed."
    report("PASS" if gb >= 6 else "WARN", f"GPU {p.name}, {gb:.0f} GB, torch {torch.__version__}", note)
    return True


def check_audio():
    from karaoke.pipeline import default_speakers, find_device, wasapi_devices
    from karaoke.winvol import default_playback_name
    cable_in, cable_out = find_device("output", "CABLE Input"), find_device("input", "CABLE Output")
    if cable_in is None or cable_out is None:
        report("FAIL", "VB-Audio Virtual Cable not found",
               "Install it from vb-audio.com (the free 'VB-CABLE Driver'), as administrator, then reboot.")
    else:
        report("PASS", "VB-Audio Virtual Cable (CABLE Input / CABLE Output)")
    default = default_playback_name() or "?"
    if "CABLE" in default:
        report("FAIL", f"Windows default output is the cable ({default})",
               "Settings > System > Sound > Output: pick your speakers or headphones. The cable installer switches it.")
    else:
        report("PASS", f"Windows default output: {default}")
    spk = default_speakers()
    names = dict(wasapi_devices("output"))
    if spk is None:
        report("FAIL", "no speakers or headphones found")
    else:
        report("PASS", f"karaoke will play on: {names[spk]}",
               "This follows Windows' default output; change it in the app under Engine settings.")


def check_spotify():
    from karaoke import approuting
    running = approuting.pids()
    store = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "(Get-AppxPackage *Spotify*).Name"], capture_output=True, text=True).stdout.strip()
    classic = os.path.exists(os.path.expandvars(r"%APPDATA%\Spotify\Spotify.exe"))
    if not (running or store or classic):
        report("FAIL", "Spotify desktop app not found", "Install it from spotify.com or the Microsoft Store and sign in.")
        return
    kind = "Microsoft Store" if store else "spotify.com installer" if classic else "running"
    report("PASS", f"Spotify desktop app ({kind}), {'running' if running else 'not running now'}",
           "" if running else "Project Karaoke opens it by itself when karaoke starts. Sign in once if you "
                              "have not: a free account works.")


def check_port():
    s = socket.socket()
    try:
        s.bind(("0.0.0.0", 8765))
        report("PASS", "port 8765 free (Project Karaoke is not running)")
    except OSError:
        try:
            import json
            import urllib.request
            st = json.load(urllib.request.urlopen("http://127.0.0.1:8765/state", timeout=2))
            report("PASS", "Project Karaoke is running", f"Phone page: {st.get('phone_url', '')}")
        except Exception:
            report("FAIL", "port 8765 is taken by another program", "Project Karaoke needs it: close that program.")
    finally:
        s.close()


def check_speed():
    import torch
    from karaoke.engine import SR, StreamingSeparator
    from karaoke.models import DEFAULT_MODEL, MODELS, WINDOWS
    args = sys.argv[sys.argv.index("--speed") + 1:]
    key = args[0] if args and args[0] in MODELS else DEFAULT_MODEL
    print(f"\nTiming {key} on this GPU (load = share of real time; must stay well under 100%):")
    for label, secs in WINDOWS.items():
        sep = StreamingSeparator(key, chunk_seconds=secs)
        part = torch.randn(2, sep.chunk, device="cuda") * 0.1
        sep._run(part)
        torch.cuda.synchronize()
        t = time.perf_counter()
        for _ in range(3):
            sep._run(part)
        torch.cuda.synchronize()
        load = (time.perf_counter() - t) / 3 / (sep.step / SR)
        level = "PASS" if load < 0.6 else "WARN" if load < 0.85 else "FAIL"
        report(level, f"delay {label:34} load {load:5.0%}",
               "" if level == "PASS" else "Too close to real time for this GPU: pick a different delay.")
        del sep
        torch.cuda.empty_cache()


def main():
    print("Project Karaoke setup check\n")
    check_python()
    check_folder()
    if check_packages():
        gpu = check_gpu()
        check_audio()
        check_spotify()
        check_port()
        if gpu and "--speed" in sys.argv:
            check_speed()
    print("\nAll good." if not failed else "\nFix the FAIL lines above, then run this again.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
