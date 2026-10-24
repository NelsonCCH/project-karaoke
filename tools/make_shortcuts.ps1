# Creates two desktop shortcuts: "Project Karaoke" (opens the karaoke window; closing it gives Spotify back)
# and "Spotify Normal Audio" (manual fallback that puts Spotify back on the speakers), plus the always-on
# helper in Startup (phones can start karaoke without touching the PC). -NoHelper skips it. tools\uninstall.ps1 removes all three.
param([switch]$NoHelper)
$root = Split-Path -Parent $PSScriptRoot
$pyw = Join-Path $root ".venv\Scripts\pythonw.exe"
$desktop = [Environment]::GetFolderPath("Desktop")
$shell = New-Object -ComObject WScript.Shell

$s = $shell.CreateShortcut((Join-Path $desktop "Project Karaoke.lnk"))
$s.TargetPath = $pyw
$s.Arguments = "-m karaoke"
$s.WorkingDirectory = $root
$s.IconLocation = "$env:SystemRoot\System32\imageres.dll,103"
$s.Description = "Karaoke: removes the singer from Spotify while this window is open"
$s.Save()

$s = $shell.CreateShortcut((Join-Path $desktop "Spotify Normal Audio.lnk"))
$s.TargetPath = $pyw
$s.Arguments = "`"$(Join-Path $root 'tools\restore_spotify_audio.py')`""
$s.WorkingDirectory = $root
$s.IconLocation = "$env:SystemRoot\System32\imageres.dll,109"
$s.Description = "Put Spotify back on the normal speakers (only needed if Project Karaoke crashed)"
$s.Save()

# The always-on helper: idles with nothing loaded until a phone (or the optional
# Mac player) starts a session. pythonw = no window.
if ($NoHelper) {
    "Shortcuts created in $desktop (no always-on helper)"
    return
}
$startup = [Environment]::GetFolderPath("Startup")
$s = $shell.CreateShortcut((Join-Path $startup "Project Karaoke Helper.lnk"))
$s.TargetPath = $pyw
$s.Arguments = "-m karaoke.agent"
$s.WorkingDirectory = $root
$s.Description = "Project Karaoke helper: lets phones start karaoke (port 8765)"
$s.Save()

"Shortcuts created in $desktop and $startup"
