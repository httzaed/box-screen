# Ajoute Box Screen au demarrage Windows
$ws = New-Object -ComObject WScript.Shell
$startupFolder = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup"
$shortcutPath = Join-Path $startupFolder "Box Screen.lnk"

$s = $ws.CreateShortcut($shortcutPath)
$s.TargetPath = "C:\Users\hedik\Documents\Claude\Projects\Box Screen\startup_launcher.bat"
$s.WorkingDirectory = "C:\Users\hedik\Documents\Claude\Projects\Box Screen"
$s.Save()

Write-Host "Raccourci cree dans: $shortcutPath" -ForegroundColor Green
