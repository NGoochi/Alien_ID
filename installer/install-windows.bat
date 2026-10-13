@echo off
rem Alien installer for Rhino 8 on Windows: runs install-windows.ps1 (next to this file).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-windows.ps1"
pause
