$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $root

python -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install --upgrade pip
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt

if (Get-Command npm.cmd -ErrorAction SilentlyContinue) {
    Push-Location '.\puente-whatsapp'
    try { npm.cmd install }
    finally { Pop-Location }
}

Write-Host 'Instalación terminada. Copiá .env.example como .env antes de iniciar.' -ForegroundColor Green
