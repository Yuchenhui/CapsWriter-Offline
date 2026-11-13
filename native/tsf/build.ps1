param(
    [string]$VsDevCmd = 'C:/Program Files/Microsoft Visual Studio/18/Enterprise/Common7/Tools/VsDevCmd.bat'
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $VsDevCmd -PathType Leaf)) { throw 'Installed MSVC VsDevCmd.bat not found.' }
$source = $PSScriptRoot
$out = Join-Path $source 'build'
New-Item -ItemType Directory -Force -Path $out | Out-Null
$commands = @"
@echo off
call "$VsDevCmd" -arch=x64 -host_arch=x64 -winsdk=10.0.26100.0
if errorlevel 1 exit /b 1
cd /d "$out"
cl /nologo /std:c++17 /EHsc /MT /W4 /permissive- /DUNICODE /D_UNICODE /LD "$source/service.cpp" /Fe:CapsWriterTsf.dll /link /DEF:"$source/CapsWriterTsf.def" /MACHINE:X64 ole32.lib user32.lib uuid.lib
if errorlevel 1 exit /b 1
cl /nologo /std:c++17 /EHsc /MT /W4 /permissive- /DUNICODE /D_UNICODE "$source/profile.cpp" /Fe:TsfProfile.exe /link /MACHINE:X64 ole32.lib user32.lib uuid.lib
if errorlevel 1 exit /b 1
cl /nologo /std:c++17 /EHsc /MT /W4 /permissive- /DUNICODE /D_UNICODE "$source/probe.cpp" /Fe:TsfProbe.exe /link /MACHINE:X64 ole32.lib user32.lib uuid.lib
exit /b %errorlevel%
"@
$batch = Join-Path $out 'compile.cmd'
[IO.File]::WriteAllText($batch, $commands, [Text.Encoding]::ASCII)
& $env:ComSpec /d /c "`"$batch`""
if ($LASTEXITCODE -ne 0) { throw "Native build failed (exit $LASTEXITCODE)." }
Write-Output "Built $out/CapsWriterTsf.dll, TsfProfile.exe, TsfProbe.exe. No registration or probe was run."
