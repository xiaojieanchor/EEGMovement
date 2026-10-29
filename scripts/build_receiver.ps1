param([string]$OutputDirectory='')
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskCompiler='C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\Roslyn\csc.exe'
if (-not (Test-Path -LiteralPath $taskCompiler)) {throw 'C# compiler not found; install Visual Studio Build Tools or build this core in Unity.'}
$taskOut=if($OutputDirectory){[IO.Path]::GetFullPath($OutputDirectory)}else{Join-Path $taskRoot 'receiver\bin'}
New-Item -ItemType Directory -Path $taskOut -Force | Out-Null
$taskSource=Join-Path $taskRoot 'unity\EEGMovement\Assets\Scripts\Core'
$taskFiles=@(Get-ChildItem -LiteralPath $taskSource -Filter '*.cs' -File | Sort-Object Name | ForEach-Object FullName)+(Join-Path $taskRoot 'receiver\Harness.cs')
& $taskCompiler /nologo /target:exe /langversion:latest /optimize+ /r:System.Web.Extensions.dll /r:System.Net.Http.dll /out:"$taskOut\EEGReceiver.exe" @taskFiles
if ($LASTEXITCODE -ne 0) {throw 'Receiver compilation failed'}
& $taskCompiler /nologo /target:exe /main:CoreTests /langversion:latest /optimize+ /r:System.Web.Extensions.dll /r:System.Net.Http.dll /out:"$taskOut\CoreTests.exe" @taskFiles (Join-Path $taskRoot 'receiver\CoreTests.cs')
if ($LASTEXITCODE -ne 0) {throw 'C# core tests compilation failed'}
Get-FileHash -Algorithm SHA256 -LiteralPath "$taskOut\EEGReceiver.exe"
