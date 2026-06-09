#Requires -Version 5.1
<#
  Monta Python 3.12 completo (tkinter + pip + deps) em instalador\payload\python.

  Requer Python 3.12 com tkinter na maquina de build (winget instala automaticamente).

  Uso:
    powershell -ExecutionPolicy Bypass -File instalador\preparar_payload.ps1
#>
param(
    [string]$PythonVersion = "3.12.7",
    [string]$FontePython = "",
    [switch]$SemCompilar,
    [switch]$ForcarPython
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Payload = Join-Path $PSScriptRoot "payload"
$PythonDir = Join-Path $Payload "python"
$Dist = Join-Path $PSScriptRoot "dist"
$Req = Join-Path $Root "servidor-requirements.txt"

New-Item -ItemType Directory -Force -Path $Payload, $Dist | Out-Null

if (-not (Test-Path "$Root\app_icon.ico")) {
    throw "app_icon.ico nao encontrado em $Root."
}

function Test-PythonCompleto([string]$Dir) {
    return (Test-Path "$Dir\python.exe") -and (Test-Path "$Dir\Lib\tkinter\__init__.py")
}

function Achar-Python312 {
    param([string]$Preferido)
    $candidatos = @()
    if ($Preferido) { $candidatos += $Preferido }
    $candidatos += @(
        "C:\Python312AppPedidos",
        "${env:ProgramFiles}\Python312",
        "${env:LocalAppData}\Programs\Python\Python312"
    )
    try {
        $p = & py -3.12 -c "import sys, tkinter; print(sys.base_prefix)" 2>$null
        if ($p) { $candidatos += $p.Trim() }
    } catch { }

    foreach ($c in $candidatos | Select-Object -Unique) {
        if ($c -and (Test-PythonCompleto $c)) {
            return (Resolve-Path $c).Path
        }
    }
    return $null
}

function Instalar-Python312Winget {
    Write-Host "Instalando Python 3.12 via winget (necessario para tkinter)..." -ForegroundColor Yellow
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "Python 3.12 com tkinter nao encontrado e winget indisponivel. Instale Python 3.12 manualmente."
    }
    & winget install Python.Python.3.12 --accept-package-agreements --accept-source-agreements --silent | Out-Null
    Start-Sleep -Seconds 3
}

Write-Host ""
Write-Host "=== Preparar instalador AppPedidos CLIPP ===" -ForegroundColor Cyan

$precisaMontar = $ForcarPython -or -not (Test-Path "$PythonDir\pythonw.exe") -or -not (Test-PythonCompleto $PythonDir)
if ($precisaMontar) {
    $fonte = Achar-Python312 -Preferido $FontePython
    if (-not $fonte) {
        Instalar-Python312Winget
        $fonte = Achar-Python312 -Preferido $FontePython
    }
    if (-not $fonte) {
        throw "Nao foi possivel localizar Python 3.12 com tkinter."
    }
    Write-Host "Fonte Python: $fonte" -ForegroundColor Yellow

    if (Test-Path $PythonDir) { Remove-Item -Recurse -Force $PythonDir }
    New-Item -ItemType Directory -Force -Path $PythonDir | Out-Null
    robocopy $fonte $PythonDir /E /XD "__pycache__" /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null

    if (-not (Test-PythonCompleto $PythonDir)) {
        throw "Payload incompleto apos copia (sem tkinter)."
    }

    Write-Host "Instalando bibliotecas no payload..." -ForegroundColor Yellow
    & "$PythonDir\python.exe" -m pip install --upgrade pip --quiet
    & "$PythonDir\python.exe" -m pip install --no-warn-script-location -r $Req

    Write-Host "Validando..." -ForegroundColor Yellow
    & "$PythonDir\python.exe" -c "import tkinter; import pystray; import fdb; print('OK')"
    if ($LASTEXITCODE -ne 0) { throw "Validacao de dependencias falhou" }

    Write-Host "Payload Python pronto: $PythonDir" -ForegroundColor Green
} else {
    Write-Host "Payload Python OK (use -ForcarPython para remontar)"
}

if ($SemCompilar) { exit 0 }

$iscc = $null
foreach ($c in @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)) {
    if (Test-Path $c) { $iscc = $c; break }
}
if (-not $iscc) {
    $cmd = Get-Command iscc -ErrorAction SilentlyContinue
    if ($cmd) { $iscc = $cmd.Source }
}
if (-not $iscc) {
    Write-Host "Inno Setup 6 nao encontrado. Payload em instalador\payload\" -ForegroundColor Yellow
    exit 2
}

Write-Host "Compilando setup.iss..." -ForegroundColor Yellow
& $iscc (Join-Path $PSScriptRoot "setup.iss")
if ($LASTEXITCODE -ne 0) { throw "Falha ao compilar setup.iss" }

Write-Host ""
Write-Host "Instalador gerado:" -ForegroundColor Green
Write-Host "  $(Join-Path $Dist 'AppPedidosCLIPP-Setup.exe')"
Write-Host ""
