param([switch]$AllowTest)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskBinary='G:\EEGMovement\Windows\EEGMovement.exe'
$taskBuildRecord=Join-Path $taskRoot 'docs\gnautilus\integration_20261022\verification\windows-build.json'
$taskCurrentBuild=$false
if(Test-Path -LiteralPath $taskBuildRecord){
    $taskBuild=Get-Content -LiteralPath $taskBuildRecord -Raw | ConvertFrom-Json
    if(-not $taskBuild.success -or -not $taskBuild.source_snapshot_unchanged){throw 'Latest g.Nautilus Windows build is not verified.'}
    $taskBinary=$taskBuild.output
    if(-not(Test-Path -LiteralPath $taskBinary) -or (Get-FileHash -LiteralPath $taskBinary -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskBuild.output_sha256){throw 'Latest Windows Player hash does not match its build record.'}
    foreach($taskInput in $taskBuild.source_hashes.PSObject.Properties){
        $taskInputPath=Join-Path $taskRoot $taskInput.Name
        if(-not(Test-Path -LiteralPath $taskInputPath) -or (Get-FileHash -LiteralPath $taskInputPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskInput.Value){throw ('Unity source changed since build: '+$taskInput.Name)}
    }
    $taskCurrentBuild=$true
}
$taskPairing=Join-Path $taskRoot 'hub\runtime\vr_pairing.json'
if(-not(Test-Path -LiteralPath $taskBinary)){throw 'Windows Player missing; build with G:\Unity\Build-EEGMovement.ps1'}
if(-not(Test-Path -LiteralPath $taskPairing)){throw 'Start the local EEGMovement hub first.'}
if(-not $taskCurrentBuild){
    $taskStatus=Invoke-RestMethod http://127.0.0.1:8765/api/preparation -TimeoutSec 8
    if(-not $taskStatus.artifacts.unity.build_verified){throw 'Current Unity source/player verification failed; check the preparation dashboard.'}
}
$taskExisting=Get-CimInstance Win32_Process -Filter "Name='EEGMovement.exe'" | Where-Object {$_.ExecutablePath -eq $taskBinary}
if($taskExisting){Write-Output 'The project Unity Player is already running. TEST opt-in is controlled in its Link page.';exit 0}
$taskLog='G:\Unity\Logs\EEGMovement-Player.log'
$taskArgs=@('--eeg-config',('"'+$taskPairing+'"'),'--eeg-port','19772','-screen-fullscreen','0','-logFile',('"'+$taskLog+'"'))
if($AllowTest){$taskArgs+='--eeg-allow-test'}
$taskProcess=Start-Process -FilePath $taskBinary -ArgumentList $taskArgs -PassThru
$taskProcess.Id | Set-Content -LiteralPath (Join-Path $taskRoot 'hub\runtime\unity.pid') -Encoding ascii
Write-Output ('Unity Player started; PID='+$taskProcess.Id+'; TEST opt-in='+[bool]$AllowTest)
