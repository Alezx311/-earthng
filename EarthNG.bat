@echo off
rem EarthNG launcher window (settings, mod install, companion, game). Console fallback: run_world.ps1
if "%EARTH2ROAD%"=="" set "EARTH2ROAD=C:\js\Earth2Road"
start "" "%EARTH2ROAD%\.venv\Scripts\pythonw.exe" "%~dp0earthng\launcher.py"
