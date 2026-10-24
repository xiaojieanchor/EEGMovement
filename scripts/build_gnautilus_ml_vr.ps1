$ErrorActionPreference='Stop'
$unityRoot='G:\Unity'
$env:TEMP="$unityRoot\Temp"; $env:TMP=$env:TEMP
$env:UPM_CACHE_ROOT="$unityRoot\Caches\UPM"
$env:GRADLE_USER_HOME="$unityRoot\Caches\Gradle"
$env:ANDROID_USER_HOME="$unityRoot\AppData\Android"
$env:EEGMOVEMENT_ANDROID_BUILD_PATH='G:\EEGMovement\Builds\20261023-ml\EEGMovement-2.1.3.apk'
$env:EEGMOVEMENT_BUILD_RECORD_PATH='C:\Users\19410\Desktop\EEGMovement\docs\gnautilus\ml_20261023\android-build.json'
$editor="$unityRoot\Editors\6000.3.26f1\Editor\Unity.exe"
$project='C:\Users\19410\Desktop\EEGMovement\unity\EEGMovement'
$log="$unityRoot\Logs\EEGMovement-ML-Android.log"
New-Item -ItemType Directory -Path 'G:\EEGMovement\Builds\20261023-ml' -Force | Out-Null
$process=Start-Process -FilePath $editor -ArgumentList @('-batchmode','-quit','-buildTarget','Android','-projectPath',('"'+$project+'"'),'-executeMethod','BuildSandbox.Android','-logFile',('"'+$log+'"')) -WindowStyle Hidden -PassThru
$process.WaitForExit()
if($process.ExitCode -ne 0){throw "Unity build failed: $log"}
Get-Content -LiteralPath $env:EEGMOVEMENT_BUILD_RECORD_PATH -Raw | ConvertFrom-Json | Select-Object build_id,success,errors,warnings,output_path,output_sha256
