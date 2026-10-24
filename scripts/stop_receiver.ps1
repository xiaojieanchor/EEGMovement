$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskPidPath=Join-Path $taskRoot 'hub\runtime\receiver.pid'
if (-not (Test-Path -LiteralPath $taskPidPath)) {Write-Output 'No owned receiver PID';exit 0}
$taskReceiverPid=[int](Get-Content -LiteralPath $taskPidPath)
$taskProcess=Get-CimInstance Win32_Process -Filter "ProcessId=$taskReceiverPid"
if ($null -eq $taskProcess) {Write-Output 'Receiver already stopped';exit 0}
$taskExpected=(Join-Path $taskRoot 'receiver\bin\EEGReceiver.exe')
if (-not [string]::Equals($taskProcess.ExecutablePath,$taskExpected,[StringComparison]::OrdinalIgnoreCase)) {throw 'PID ownership differs; no process was stopped'}
Stop-Process -Id $taskReceiverPid
Write-Output 'Owned C# receiver stopped. Unity Editor and other services were not touched.'
