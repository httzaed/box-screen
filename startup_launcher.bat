@echo off
REM Box Screen - Auto-start au demarrage Windows
REM Ce fichier lance le panel automatiquement

echo Starting Box Screen...
cd /d "%~dp0"
python launcher.py

pause
