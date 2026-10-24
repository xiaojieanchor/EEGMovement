$ErrorActionPreference='Stop'
$unityRoot='G:\Unity'
$env:TEMP="$unityRoot\Temp"; $env:TMP=$env:TEMP
$env:UPM_CACHE_ROOT="$unityRoot\Caches\UPM"
$env:GRADLE_USER_HOME="$unityRoot\Caches\Gradle"
$env:ANDROID_USER_HOME="$unityRoot\AppData\Android"
$env:EEGMOVEMENT_ANDROID_BUILD_PATH='G:\EEGMovement\Builds\20261022-gnautilus\EEGMovement.apk'
$env:EEGMOVEMENT_BUILD_RECORD_PATH="C:\Users\19410\Desktop\EEGMovement\docs\gnautilus\integration_20261022\verification\android-build.json"
$editor="$unityRoot\Editors\6000.3.26f1\Editor\Unity.exe"
$project='C:\Users\19410\Desktop\EEGMovement\unity\EEGMovement'
$log="$unityRoot\Logs\EEGMovement-gNautilus-Android.log"
New-Item -ItemType Directory -Path 'G:\EEGMovement\Builds\20261022-gnautilus' -Force | Out-Null
$started=(Get-Date).ToString('o')
@{phase='building';started_at=$started;log=$log;success=$false}|ConvertTo-Json|Set-Content "C:\Users\19410\Desktop\EEGMovement\docs\gnautilus\integration_20261022\verification\android-build.json" -Encoding utf8
$process=Start-Process -FilePath $editor -ArgumentList @('-batchmode','-quit','-buildTarget','Android','-projectPath',('"'+$project+'"'),'-executeMethod','BuildSandbox.Android','-logFile',('"'+$log+'"')) -WindowStyle Hidden -PassThru
$process.WaitForExit()
$result=Get-Content "C:\Users\19410\Desktop\EEGMovement\docs\gnautilus\integration_20261022\verification\android-build.json" -Raw|ConvertFrom-Json -AsHashtable
$result.exit_code=$process.ExitCode;$result.started_at=$started;$result.log=$log
$result.phase=if($process.ExitCode -eq 0 -and $result.success){'succeeded'}else{'failed'}
$result|ConvertTo-Json -Depth 8|Set-Content "C:\Users\19410\Desktop\EEGMovement\docs\gnautilus\integration_20261022\verification\android-build.json" -Encoding utf8
if($result.phase -ne 'succeeded'){exit 1}
