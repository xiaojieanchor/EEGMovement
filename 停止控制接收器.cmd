@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stop_receiver.ps1
if errorlevel 1 pause
