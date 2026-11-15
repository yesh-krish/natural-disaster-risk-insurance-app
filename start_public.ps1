# Start NDApp on port 8001 for sharing through a tunnel, protected by HTTP Basic auth.
# The password lives in .env.tunnel (git-ignored). Delete that file to get a new password.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$secretFile = Join-Path $PSScriptRoot ".env.tunnel"
if (-not (Test-Path $secretFile)) {
    $chars = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789".ToCharArray()
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $bytes = New-Object byte[] 16
    $rng.GetBytes($bytes)
    $password = -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
    Set-Content -Path $secretFile -Value "NDAPP_USER=ndapp`nNDAPP_PASSWORD=$password" -Encoding ascii
    Write-Host "Created a new password in .env.tunnel"
}
foreach ($line in Get-Content $secretFile) {
    if ($line -match '^\s*(NDAPP_[A-Z_]+)\s*=\s*(.+?)\s*$') {
        Set-Item -Path "env:$($Matches[1])" -Value $Matches[2]
    }
}
if (-not $env:NDAPP_PASSWORD) { throw "NDAPP_PASSWORD missing from .env.tunnel" }
Write-Host "Password protection on (user: $env:NDAPP_USER). Password is in .env.tunnel"
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
# Port 8001 keeps the shared copy separate from a local dev server on 8000.
& $python server.py --host 127.0.0.1 --port 8001
