#Requires -Version 5.1
param(
    [Parameter(Mandatory = $true)]
    [string]$AppDir
)

$ErrorActionPreference = "Continue"
$LogDir = Join-Path $env:LOCALAPPDATA "AppPedidosCLIPP"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir "instalacao.log"

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Add-Content -Path $Log -Value $line -Encoding UTF8
    Write-Host $line
}

Log "=== Pos-instalacao AppPedidos CLIPP ==="
Log "AppDir: $AppDir"

$py = Join-Path $AppDir "python\python.exe"
$pyw = Join-Path $AppDir "python\pythonw.exe"

if (-not (Test-Path $py)) {
    Log "ERRO: python.exe nao encontrado em $py"
    exit 1
}
Log "Python OK: $py"

$cfg = Join-Path $AppDir "config.ini"
if (-not (Test-Path $cfg)) {
    Copy-Item (Join-Path $AppDir "config.ini.exemplo") $cfg
    Log "config.ini criado"
}

Log "Testando dependencias..."
& $py -c "import tkinter; import pystray; import fdb; print('imports OK')" 2>&1 | ForEach-Object { Log $_ }
if ($LASTEXITCODE -ne 0) {
    Log "Reinstalando dependencias..."
    & $py -m pip install --no-warn-script-location -r (Join-Path $AppDir "servidor-requirements.txt")
}

Log "Iniciando servidor..."
$bat = Join-Path $AppDir "AppPedidos CLIPP.bat"
if (Test-Path $bat) {
    Start-Process -FilePath $bat -WorkingDirectory $AppDir -WindowStyle Hidden
    Start-Sleep -Seconds 3
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8765/ping" -UseBasicParsing -TimeoutSec 5
        Log "Servidor respondeu: $($r.StatusCode)"
    } catch {
        Log "AVISO: servidor ainda nao respondeu em /ping — abra AppPedidos CLIPP manualmente"
    }
}

Log "Concluido."
exit 0
