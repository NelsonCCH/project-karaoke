# Undoes what Project Karaoke set up on this PC: stops the helper, gives Spotify back to the speakers, and
# removes the shortcuts made by make_shortcuts.ps1. It leaves the VB-Audio cable driver, the firewall rule and
# this folder alone; README.md "Uninstall" says how to remove those.
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"

Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
    Where-Object { $_.CommandLine -match '-m karaoke(\.agent)?\s*$' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force; "Stopped process $($_.ProcessId)" }

if (Test-Path $py) {
    & $py -c "from karaoke import approuting; approuting.route_spotify(False); print('Spotify routing reset (it must be open for this to apply)')"
}

$desktop = [Environment]::GetFolderPath("Desktop")
$startup = [Environment]::GetFolderPath("Startup")
foreach ($f in @((Join-Path $desktop "Project Karaoke.lnk"), (Join-Path $desktop "Spotify Normal Audio.lnk"),
                 (Join-Path $startup "Project Karaoke Helper.lnk"))) {
    if (Test-Path $f) { Remove-Item $f; "Removed $f" }
}
