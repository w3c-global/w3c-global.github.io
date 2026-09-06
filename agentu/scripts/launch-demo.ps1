$ErrorActionPreference = 'Stop'
$agentuRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$agentuUrl = 'http://127.0.0.1:4322/agentu/demo/'
$agentuReady = $false
try {
    $agentuHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:4322/api/health' -TimeoutSec 2
    $agentuReady = $agentuHealth.service -eq 'agentu-demo'
} catch { }
if (-not $agentuReady) {
    $agentuPython = (Get-Command python -ErrorAction Stop).Source
    $agentuScript = Join-Path $agentuRoot 'agentu\backend\local.py'
    $agentuProcess = Start-Process -FilePath $agentuPython -ArgumentList @(('"' + $agentuScript + '"'), '--port', '4322') -WorkingDirectory $agentuRoot -WindowStyle Hidden -PassThru
    $agentuBuild = Join-Path $agentuRoot '.build'
    New-Item -ItemType Directory -Force -Path $agentuBuild | Out-Null
    Set-Content -LiteralPath (Join-Path $agentuBuild 'local-server.pid') -Value $agentuProcess.Id
    for ($agentuAttempt = 0; $agentuAttempt -lt 15; $agentuAttempt++) {
        try {
            $agentuHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:4322/api/health' -TimeoutSec 1
            if ($agentuHealth.service -eq 'agentu-demo') { $agentuReady = $true; break }
        } catch { }
        Start-Sleep -Milliseconds 300
    }
}
if (-not $agentuReady) { throw 'The Agentu rehearsal could not start. Check whether port 4322 is already in use.' }
Start-Process $agentuUrl
Write-Output ('Agentu rehearsal is open at ' + $agentuUrl)
