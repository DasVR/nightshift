@echo off
REM das-dev PC init — run me and follow the output
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
pause