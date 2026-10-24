param([string]$DeviceSerial='', [switch]$Launch)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskAdb='G:\Unity\Editors\6000.3.26f1\Editor\Data\PlaybackEngines\AndroidPlayer\SDK\platform-tools\adb.exe'
$taskManifest=Get-Content -LiteralPath (Join-Path $taskRoot 'outputs\relay_20261023\release\quest-arm64.json') -Raw | ConvertFrom-Json
$taskRelease=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($taskManifest.payload)) | ConvertFrom-Json
$taskName=[IO.Path]::GetFileName($taskRelease.path)
if($taskName -notmatch '^EEGMovement-Quest-[0-9.]+\.apk$'){throw '本地发布清单中的APK名称无效。'}
$taskApk=Join-Path $taskRoot ('outputs\relay_20261023\release\'+$taskName)
if((Get-FileHash -LiteralPath $taskApk -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskRelease.sha256){throw 'APK与本地发布清单的SHA256不一致。'}
$taskArgs=@();if($DeviceSerial){$taskArgs=@('-s',$DeviceSerial)}
& $taskAdb @taskArgs get-state
if($LASTEXITCODE -ne 0){throw '没有已授权的Quest连接，请先接USB并在头显授权。'}
& $taskAdb @taskArgs install -r $taskApk
if($LASTEXITCODE -ne 0){throw 'APK安装未完成，保留原安装，不执行卸载。'}
if($Launch){
    & $taskAdb @taskArgs shell monkey -p org.eegmovement.workspace -c android.intent.category.LAUNCHER 1
    if($LASTEXITCODE -ne 0){throw 'APK已安装，但启动未确认，请在头显打开。'}
}
Write-Output ('Quest '+$taskRelease.version+' 已安装并保留应用数据。联网后自动连接，无需配对文件；动作保持停止。')
