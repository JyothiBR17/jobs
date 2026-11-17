# Run from Windows PowerShell: .\scripts\start-local.ps1
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker Desktop is required for the local Job Hunter pilot."
}

if (-not (Test-Path ".env")) {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $token = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
    $boardJson = Get-Content "config/public-boards.json" -Raw | ConvertFrom-Json | ConvertTo-Json -Compress -Depth 8
    $config = "LOCAL_ACCESS_TOKEN=$token`nJOB_BOARDS_JSON=$boardJson`n"
    [System.IO.File]::WriteAllText((Join-Path $root ".env"), $config, [System.Text.UTF8Encoding]::new($false))
    Write-Host "Generated local access token in .env. Keep this file private."
}

docker compose up --build --detach
if ($LASTEXITCODE -ne 0) { throw "Docker Compose did not start successfully." }
Write-Host ""
Write-Host "Job Hunter: http://localhost:8000"
Write-Host "Use LOCAL_ACCESS_TOKEN from .env to sign in."
Write-Host "Your job data and uploaded PDFs persist in the isolated Docker volume jobhunter_data."
Write-Host "AegisSOC infrastructure is not accessed."
