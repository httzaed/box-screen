# Ajoute Box Screen au demarrage Windows
$ErrorActionPreference = "Stop"
$ws = New-Object -ComObject WScript.Shell
$startupFolder = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startupFolder "Box Screen.lnk"

$s = $ws.CreateShortcut($shortcutPath)
$gitRoot = Split-Path (Split-Path (Get-Command git.exe -CommandType Application | Select-Object -First 1).Source)
$s.TargetPath = (Get-Item (Join-Path $gitRoot "git-bash.exe")).FullName
$s.Arguments = '--cd="' + $PSScriptRoot + '" -c "python -u launcher.py; exec bash -i"'
$s.WindowStyle = 1
$s.WorkingDirectory = $PSScriptRoot
$s.Save()

Write-Host "Raccourci cree dans: $shortcutPath" -ForegroundColor Green
