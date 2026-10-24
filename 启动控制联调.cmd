@echo off
cd /d "%~dp0"
"C:\Python313\python.exe" scripts\start_control_lab.py
if errorlevel 1 pause
