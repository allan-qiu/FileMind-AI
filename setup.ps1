param([string]$PythonExe = '')

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

# Dependency downloads, build isolation and temporary files stay in this checkout.
$taskCache = Join-Path $PSScriptRoot '.cache'
$taskTemp = Join-Path $taskCache 'tmp'
New-Item -ItemType Directory -Force -Path $taskTemp | Out-Null
$previousTemp = $env:TEMP
$previousTmp = $env:TMP
$previousPipCache = $env:PIP_CACHE_DIR
try {
    $env:TEMP = $taskTemp
    $env:TMP = $taskTemp
    $env:PIP_CACHE_DIR = Join-Path $taskCache 'pip'
    if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        $pythonCandidates = @()
        if ($PythonExe) {
            $pythonCandidates += $PythonExe
        } else {
            $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
            if ($pythonCommand) { $pythonCandidates += $pythonCommand.Source }
            $pyCommand = Get-Command py -ErrorAction SilentlyContinue
            if ($pyCommand) {
                # Includes provider-qualified runtimes (e.g. uv) that py -3.12 may miss.
                $runtimeLines = & $pyCommand.Source -0p
                foreach ($runtimeLine in $runtimeLines) {
                    if ($runtimeLine -match '([A-Za-z]:\\.+\\python.exe)\s*$') {
                        $pythonCandidates += $Matches[1]
                    }
                }
            }
        }
        $selectedPython = $null
        foreach ($candidate in $pythonCandidates) {
            if (-not (Test-Path -LiteralPath $candidate)) { continue }
            $candidateVersion = & $candidate -c 'import sys; print("%d.%d" % sys.version_info[:2])'
            if ($LASTEXITCODE -eq 0 -and $candidateVersion -eq '3.12') {
                $selectedPython = $candidate
                break
            }
        }
        if (-not $selectedPython) {
            throw 'Python 3.12 not found. Run setup.ps1 -PythonExe C:\path\to\python.exe'
        }
        & $selectedPython -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the project virtual environment.' }
    }
    $taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    & $taskPython -c "import sys; assert sys.version_info[:2] == (3, 12), 'Python 3.12 required'"
    if ($LASTEXITCODE -ne 0) { throw 'The existing .venv must use Python 3.12.' }
    & $taskPython -m pip install -r requirements.lock
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    & $taskPython -m pip install --no-deps -e .
    if ($LASTEXITCODE -ne 0) { throw 'Editable project installation failed.' }
    Write-Host 'Ready. Double-click launch.cmd or run .\.venv\Scripts\python.exe run.py'
} finally {
    $env:TEMP = $previousTemp
    $env:TMP = $previousTmp
    $env:PIP_CACHE_DIR = $previousPipCache
}
