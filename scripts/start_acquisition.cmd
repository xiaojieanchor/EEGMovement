@echo off
pushd "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo Missing .venv. Follow docs/Windows development instructions first.
  pause
  popd
  exit /b 1
)
".venv\Scripts\python.exe" -m gnautilus_acquisition %*
set "EEGMOVEMENT_EXIT=%ERRORLEVEL%"
if not "%EEGMOVEMENT_EXIT%"=="0" pause
popd
exit /b %EEGMOVEMENT_EXIT%
