# Start Jarvis when you log in to Windows (a minimised window running run_jarvis.cmd).
#   powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1            turn on
#   powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 remove     turn off
# It's a normal shortcut in your Startup folder (Win+R, shell:startup), nothing else is changed.
param([ValidateSet('install', 'remove')][string]$Action = 'install')

$root = Split-Path -Parent $PSScriptRoot
$link = Join-Path ([Environment]::GetFolderPath('Startup')) 'Jarvis.lnk'

if ($Action -eq 'remove') {
    if (Test-Path $link) { Remove-Item $link; Write-Output "Autostart off: removed $link" }
    else { Write-Output 'Autostart was not on.' }
    return
}

$shell = New-Object -ComObject WScript.Shell
$s = $shell.CreateShortcut($link)
$s.TargetPath = Join-Path $root 'run_jarvis.cmd'
$s.WorkingDirectory = $root
$s.WindowStyle = 7          # minimised
$s.Description = 'Jarvis voice assistant'
$s.Save()
Write-Output "Autostart on: Jarvis starts (minimised) when you log in. Shortcut: $link"
