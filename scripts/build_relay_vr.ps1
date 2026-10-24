$ErrorActionPreference='Stop'
$taskUnity='G:\Unity'
$env:TEMP="$taskUnity\Temp"; $env:TMP=$env:TEMP
$env:UPM_CACHE_ROOT="$taskUnity\Caches\UPM"; $env:GRADLE_USER_HOME="$taskUnity\Caches\Gradle"; $env:ANDROID_USER_HOME="$taskUnity\AppData\Android"
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskOut='G:\EEGMovement\Builds\20261023-relay'
New-Item -ItemType Directory -Path $taskOut -Force | Out-Null
$env:EEGMOVEMENT_ANDROID_BUILD_PATH="$taskOut\EEGMovement.apk"
$env:EEGMOVEMENT_BUILD_RECORD_PATH="$taskRoot\docs\gnautilus\relay_20261023_followup\android-build.json"
$taskLog="$taskUnity\Logs\EEGMovement-relay-Android.log"
$taskProcess=Start-Process -FilePath "$taskUnity\Editors\6000.3.26f1\Editor\Unity.exe" -ArgumentList @('-batchmode','-quit','-buildTarget','Android','-projectPath',('"'+$taskRoot+'\unity\EEGMovement"'),'-executeMethod','BuildSandbox.Android','-logFile',('"'+$taskLog+'"')) -WindowStyle Hidden -PassThru
$taskProcess.WaitForExit()
if($taskProcess.ExitCode -ne 0){throw "Unity build failed. See $taskLog"}
& C:\Python313\python.exe "$taskRoot\scripts\sign_android_debug.py" --build-record $env:EEGMOVEMENT_BUILD_RECORD_PATH
if($LASTEXITCODE -ne 0){throw 'APK signature verification failed'}
