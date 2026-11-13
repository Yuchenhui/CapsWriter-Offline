param(
    [Parameter(Mandatory)][ValidateSet('register','unregister')][string]$Operation,
    [Parameter(Mandatory)][string]$DllPath,
    [Parameter(Mandatory)][ValidatePattern('^S-1-5-21-[0-9-]+$')][string]$ExpectedUserSid
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'Use PowerShell 7 or newer.' }
if (-not [Environment]::Is64BitProcess) { throw 'Use x64 PowerShell 7.' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if ($identity.User.Value -ne $ExpectedUserSid) { throw 'Current identity differs from the explicitly expected user SID.' }
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script explicitly elevated as the SAME user. This script never elevates itself.'
}
if (-not [IO.Path]::IsPathFullyQualified($DllPath)) { throw 'DllPath must be absolute.' }
$DllPath = (Resolve-Path -LiteralPath $DllPath).ProviderPath
if ([IO.Path]::GetFileName($DllPath) -ne 'CapsWriterTsf.dll') { throw 'Unexpected DLL filename.' }
if ($DllPath -match '(?i)[\\/]build[\\/]') { throw 'Registration of build output is forbidden. Copy to the installed native/tsf directory first.' }
$helper = Join-Path (Split-Path -LiteralPath $DllPath) 'TsfProfile.exe'
if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) { throw 'Installed TsfProfile.exe must be beside the DLL.' }
$clsid = '{B96C3F71-DB8A-4FBA-9F27-8F2D7F819D7A}'
$comPath = "Software\Classes\CLSID\$clsid"
$tipPath = "SOFTWARE\Microsoft\CTF\TIP\$clsid"
$user = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::CurrentUser, [Microsoft.Win32.RegistryView]::Registry64)
$machine = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, [Microsoft.Win32.RegistryView]::Registry64)
function Assert-Names($Actual, $Expected, [string]$Label) {
    if (@(Compare-Object -ReferenceObject @($Expected) -DifferenceObject @($Actual)).Count) { throw "Unknown registration content: $Label" }
}
try {
    $systemCom = $machine.OpenSubKey($comPath)
    if ($null -ne $systemCom) { $systemCom.Dispose(); throw 'Refusing existing HKLM COM mapping; this service uses HKCU COM only.' }
    $existing = $user.OpenSubKey($comPath)
    $owned = $null -ne $existing
    if ($owned) {
        try {
            Assert-Names $existing.GetValueNames() @('', 'CapsWriterOwnerSid', 'CapsWriterDllPath') 'CLSID values'
            Assert-Names $existing.GetSubKeyNames() @('InprocServer32') 'CLSID children'
            if ($existing.GetValue('') -ne 'CapsWriter Voice' -or
                $existing.GetValue('CapsWriterOwnerSid') -ne $ExpectedUserSid -or
                $existing.GetValue('CapsWriterDllPath') -ne $DllPath) { throw 'Existing COM registration is not owned by this user/path.' }
            $server = $existing.OpenSubKey('InprocServer32')
            try {
                Assert-Names $server.GetValueNames() @('', 'ThreadingModel') 'server values'
                if ($server.SubKeyCount -ne 0 -or $server.GetValue('') -ne $DllPath -or $server.GetValue('ThreadingModel') -ne 'Apartment') {
                    throw 'Unknown InprocServer32 registration.'
                }
            } finally { if ($server) { $server.Dispose() } }
        } finally { $existing.Dispose() }
    }
    foreach ($root in @($user, $machine)) {
        $tip = $root.OpenSubKey($tipPath)
        if ($tip) {
            try {
                if (-not $owned) { throw 'Refusing pre-existing TSF metadata without our ownership marker.' }
                $languages = $tip.OpenSubKey('LanguageProfile')
                if ($languages) {
                    try {
                        foreach ($language in $languages.GetSubKeyNames()) {
                            if ($language -notmatch '^(?i:0x00000804)$') { throw 'Unknown existing TSF profile language.' }
                            $languageKey = $languages.OpenSubKey($language)
                            try {
                                foreach ($profile in $languageKey.GetSubKeyNames()) {
                                    if ($profile -ne '{A41CCB7E-34BB-4206-A4A7-F18100418359}') { throw 'Unknown existing TSF profile GUID.' }
                                }
                            } finally { $languageKey.Dispose() }
                        }
                    } finally { $languages.Dispose() }
                }
            } finally { $tip.Dispose() }
        }
    }
    if ($Operation -eq 'register' -and $owned) { throw 'An owned registration already exists. Explicitly unregister it before an update; no automatic re-registration is performed.' }
    if ($Operation -eq 'unregister' -and -not $owned) { throw 'No owned registration; nothing removed.' }
    if ($Operation -eq 'register' -and -not $owned) {
        $key = $user.CreateSubKey($comPath)
        try {
            $key.SetValue('', 'CapsWriter Voice')
            $key.SetValue('CapsWriterOwnerSid', $ExpectedUserSid)
            $key.SetValue('CapsWriterDllPath', $DllPath)
            $server = $key.CreateSubKey('InprocServer32')
            try { $server.SetValue('', $DllPath); $server.SetValue('ThreadingModel', 'Apartment') }
            finally { $server.Dispose() }
        } finally { $key.Dispose() }
    }
    & $helper $Operation $DllPath
    if ($LASTEXITCODE -ne 0) {
        throw 'TSF metadata operation failed. Owned HKCU COM marker retained for explicit recovery; do not assume registration/unregistration completed.'
    }
    if ($Operation -eq 'unregister') { $user.DeleteSubKeyTree($comPath, $false) }
    Write-Output "$Operation completed for the fixed CapsWriter Voice profile (zh-CN). No activation/default/language-order change requested."
} finally { $user.Dispose(); $machine.Dispose() }
