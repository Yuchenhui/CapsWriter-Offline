param(
    [Parameter(Mandatory)][string]$DllPath,
    [Parameter(Mandatory)][string]$ExpectedUserSid
)
& "$PSScriptRoot/registration.ps1" -Operation register -DllPath $DllPath -ExpectedUserSid $ExpectedUserSid
