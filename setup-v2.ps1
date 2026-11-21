$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& (Join-Path $PSScriptRoot 'setup.ps1')
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw 'Install a compatible Node.js first (see web/package.json engines). No system install is performed.'
}
$taskCache = Join-Path $PSScriptRoot '.cache'
$oldTemp = $env:TEMP
$oldTmp = $env:TMP
try {
    $env:TEMP = Join-Path $taskCache 'tmp'
    $env:TMP = $env:TEMP
    Push-Location -LiteralPath (Join-Path $PSScriptRoot 'web')
    try {
        & npm ci --cache (Join-Path $taskCache 'npm') --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'Web dependency installation failed.' }
        & npm run build
        if ($LASTEXITCODE -ne 0) { throw 'Web build failed.' }
    } finally { Pop-Location }
} finally {
    $env:TEMP = $oldTemp
    $env:TMP = $oldTmp
}
Write-Host 'V2 ready. launch.cmd opens desktop; launch-server.cmd serves Web/API on loopback.'
