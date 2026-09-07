param(
    [switch]$NoN8n,
    [switch]$NoCompanion
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$venvPythonw = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
$python = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { 'python.exe' }
$pythonw = if (Test-Path -LiteralPath $venvPythonw) { $venvPythonw } else { 'pythonw.exe' }

function Test-Port([int]$Port) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $result = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $result.AsyncWaitHandle.WaitOne(500)) { return $false }
        $client.EndConnect($result)
        return $true
    }
    catch { return $false }
    finally { $client.Dispose() }
}

if (-not (Test-Port 8000)) {
    Start-Process -FilePath $python -ArgumentList @('servidor_controles_becca.py') `
        -WorkingDirectory $projectRoot -WindowStyle Hidden | Out-Null
}

if (-not $NoN8n -and -not (Test-Port 5678)) {
    $n8n = Get-Command 'n8n.cmd' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($n8n) {
        Start-Process -FilePath $n8n.Source -ArgumentList @('start') `
            -WorkingDirectory $projectRoot -WindowStyle Hidden | Out-Null
    }
    else {
        Write-Warning 'n8n no está instalado; Companion podrá usar comandos locales, pero no el chat.'
    }
}

if (-not $NoCompanion) {
    Start-Process -FilePath $pythonw -ArgumentList @('-m', 'rebecca_companion.launcher') `
        -WorkingDirectory $projectRoot -WindowStyle Hidden | Out-Null
}

Write-Host 'Rebecca iniciada localmente. No se abrió ningún túnel público.' -ForegroundColor Green
