#Requires -Version 5.1
<#
.SYNOPSIS
  Instala AppPedidos CLIPP (servidor na bandeja + extensão Chrome).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File instalar.ps1
  powershell -ExecutionPolicy Bypass -File instalar.ps1 -Destino "D:\AppPedidos CLIPP"
#>
param(
    [string]$Destino = "$env:ProgramFiles\AppPedidos CLIPP"
)

$ErrorActionPreference = "Stop"
$Origem = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if (-not (Test-Path "$Origem\servidor_app.py")) {
    throw "Pasta do projeto invalida: $Origem"
}

function Test-Python {
    try {
        $v = & py -3 -c "import sys; print(sys.version_info[:2])" 2>$null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}

Write-Host ""
Write-Host "=== AppPedidos CLIPP - Instalador ===" -ForegroundColor Cyan
Write-Host "Origem:  $Origem"
Write-Host "Destino: $Destino"
Write-Host ""

if (-not (Test-Python)) {
    Write-Host "Python 3 nao encontrado (comando py -3)." -ForegroundColor Red
    Write-Host "Instale Python 3.10+ de https://www.python.org/downloads/"
    Write-Host "Marque 'Add python.exe to PATH' e 'py launcher'."
    exit 1
}

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $admin -and $Destino -like "$env:ProgramFiles*") {
    Write-Host "Reexecutando como Administrador para instalar em Program Files..." -ForegroundColor Yellow
    Start-Process powershell -Verb RunAs -ArgumentList @(
        "-ExecutionPolicy", "Bypass",
        "-File", $MyInvocation.MyCommand.Path,
        "-Destino", "`"$Destino`""
    )
    exit 0
}

New-Item -ItemType Directory -Force -Path $Destino | Out-Null

function Stop-AppPedidosProcessos {
    param([string[]]$Pastas)

    Write-Host "Encerrando AppPedidos CLIPP em execucao..." -ForegroundColor Yellow
    $ids = [System.Collections.Generic.HashSet[int]]::new()
    $pastas = @($Pastas | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique)

    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | ForEach-Object {
        $cmd = $_.CommandLine
        if (-not $cmd) { return }
        $fecha = $false
        if ($cmd -match 'servidor_app\.py|importar_servidor\.py') { $fecha = $true }
        foreach ($pasta in $pastas) {
            if ($cmd -like "*$pasta*") { $fecha = $true; break }
        }
        if (-not $fecha) { return }
        try {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop
            [void]$ids.Add([int]$_.ProcessId)
        } catch {}
    }

    foreach ($pasta in $pastas) {
        foreach ($nome in @("pythonw", "python")) {
            $exe = Join-Path $pasta "python\$nome.exe"
            if (-not (Test-Path $exe)) { continue }
            Get-Process -Name $nome -ErrorAction SilentlyContinue | ForEach-Object {
                try {
                    if ($_.Path -and ($_.Path -ieq $exe)) {
                        Stop-Process -Id $_.Id -Force -ErrorAction Stop
                        [void]$ids.Add($_.Id)
                    }
                } catch {}
            }
        }
    }

    if ($ids.Count -gt 0) {
        Write-Host "  $($ids.Count) processo(s) encerrado(s). Aguardando liberar arquivos..." -ForegroundColor Gray
        Start-Sleep -Seconds 3
    } else {
        Write-Host "  Nenhum processo do AppPedidos em execucao." -ForegroundColor Gray
    }
}

Stop-AppPedidosProcessos -Pastas @($Destino, $Origem)

$ignorar = @(
    "__pycache__", ".cursor", ".rpa_profile", ".git", "instalador",
    "EstudoProjetoPedidos.pdf", "gerar_pdf_estudo.py", "_reparar_visibilidade.py"
)
$incluirExt = @(".py", ".dll", ".txt", ".md", ".json", ".html", ".js", ".css", ".vbs", ".bat", ".exemplo", ".ini", ".png", ".ico")

Get-ChildItem -Path $Origem -Recurse | ForEach-Object {
    $rel = $_.FullName.Substring($Origem.Length + 1)
    foreach ($ig in $ignorar) {
        if ($rel -like "$ig*" -or $rel -like "*\$ig\*") { return }
    }
    if ($_.PSIsContainer) { return }
    if ($rel -eq "config.ini") { return }
    $ext = $_.Extension.ToLower()
    if ($rel -like "extensao_chrome\*" -or $incluirExt -contains $ext -or $rel -eq "fbclient64.dll") {
        $alvo = Join-Path $Destino $rel
        if ($_.FullName -eq $alvo) { return }
        $pasta = Split-Path $alvo -Parent
        if (-not (Test-Path $pasta)) { New-Item -ItemType Directory -Force -Path $pasta | Out-Null }
        Copy-Item -Force $_.FullName $alvo
    }
}

if (-not (Test-Path "$Destino\config.ini")) {
    Copy-Item "$Destino\config.ini.exemplo" "$Destino\config.ini"
    Write-Host "Criado config.ini a partir do exemplo."
}

Write-Host "Instalando dependencias do servidor..." -ForegroundColor Yellow
Push-Location $Destino
$pyBundled = Join-Path $Destino "python\python.exe"
if (Test-Path $pyBundled) {
    & $pyBundled -m pip install -q -r servidor-requirements.txt
} else {
    & py -3 -m pip install -q -r servidor-requirements.txt
}
Pop-Location

$launcher = Join-Path $Destino "AppPedidos CLIPP.bat"
if (-not (Test-Path $launcher)) { $launcher = Join-Path $Destino "Iniciar Servidor CLIPP.vbs" }
$iconPath = Join-Path $Destino "app_icon.ico"
$startup = [Environment]::GetFolderPath("Startup")
$lnkStartup = Join-Path $startup "AppPedidos CLIPP.lnk"
$shell = New-Object -ComObject WScript.Shell
$s = $shell.CreateShortcut($lnkStartup)
$s.TargetPath = $launcher
$s.WorkingDirectory = $Destino
$s.Description = "Servidor importador Tiao Cards -> CLIPP"
if (Test-Path $iconPath) { $s.IconLocation = "$iconPath,0" }
$s.Save()

$desktop = [Environment]::GetFolderPath("Desktop")
$lnkDesktop = Join-Path $desktop "AppPedidos CLIPP.lnk"
$d = $shell.CreateShortcut($lnkDesktop)
$d.TargetPath = $launcher
$d.WorkingDirectory = $Destino
$d.Description = "Servidor importador Tiao Cards -> CLIPP"
if (Test-Path $iconPath) { $d.IconLocation = "$iconPath,0" }
$d.Save()

$lnkExt = Join-Path $desktop "Instalar extensao Chrome CLIPP.lnk"
$e = $shell.CreateShortcut($lnkExt)
$e.TargetPath = "explorer.exe"
$e.Arguments = "`"$Destino\extensao_chrome`""
$e.Description = "Pasta da extensao Chrome - Carregar sem compactacao"
$e.Save()

$lnkCfg = Join-Path $desktop "Configurar AppPedidos CLIPP.lnk"
$c = $shell.CreateShortcut($lnkCfg)
$c.TargetPath = "notepad.exe"
$c.Arguments = "`"$Destino\config.ini`""
$c.Description = "Editar config.ini do importador"
$c.Save()

Write-Host ""
Write-Host "Instalacao concluida!" -ForegroundColor Green
Write-Host "  Servidor: inicia com o Windows (pasta Inicializar)"
Write-Host "  Atalho: Instalar extensao Chrome CLIPP (area de trabalho)"
Write-Host "  Configure config.ini antes da primeira importacao."
Write-Host ""
Write-Host "Iniciando servidor na bandeja agora..."
Start-Process -FilePath $launcher -WorkingDirectory $Destino

Write-Host "Pronto. Icone perto do relogio do Windows." -ForegroundColor Cyan
Write-Host ""
