@echo off
rem Country pack window. Python is this checkout only: python\ or .venv.
set "PYW=%~dp0python\pythonw.exe"
if not exist "%PYW%" set "PYW=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%PYW%" (
  echo Python for this repository was not found.
  echo   %~dp0python\pythonw.exe
  echo   %~dp0.venv\Scripts\pythonw.exe
  echo Create the environment in the EarthNG root:
  echo   py -3.13 -m venv .venv
  echo   .venv\Scripts\python.exe -m pip install -r requirements-fast.txt
  pause
  exit /b 1
)
start "" "%PYW%" "%~dp0earthng\country_ui.py"
