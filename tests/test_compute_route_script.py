"""Exercise the Windows PowerShell 5.1 parser without mutating host routes."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which('powershell.exe') is None, reason='Windows PowerShell unavailable')
def test_route_helper_parses_with_windows_powershell_51():
    path=Path(__file__).resolve().parents[1]/'scripts/repair_compute_route.ps1'
    command="$tokens=$null;$errors=$null;[System.Management.Automation.Language.Parser]::ParseFile('"+str(path).replace("'","''")+"',[ref]$tokens,[ref]$errors)|Out-Null;@{errors=@($errors | ForEach-Object ToString);version=$PSVersionTable.PSVersion.Major}|ConvertTo-Json -Compress"
    result=subprocess.run(['powershell.exe','-NoProfile','-Command',command],capture_output=True,timeout=15)
    assert result.returncode==0,result.stderr
    report=json.loads(result.stdout.decode('utf8'))
    assert report['version']==5
    assert not report['errors'],report['errors']
