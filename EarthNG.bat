@echo off
rem EarthNG launcher window. Python is this checkout only: python\ or .venv.
rem Console fallback: run_world.ps1. The old v2 generator still needs an Earth2Road checkout.
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
start "" "%PYW%" "%~dp0earthng\launcher.py"
