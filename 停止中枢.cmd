@echo off
cd /d "%~dp0"
"C:\Python313\python.exe" scripts\stop_hub.py
if errorlevel 1 pause
