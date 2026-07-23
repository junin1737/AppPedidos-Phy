#Requires -Version 5.1
<#
  Atualiza uma instalacao EXISTENTE do AppPedidos CLIPP (somente os arquivos
  de codigo que mudaram). NAO mexe em config.ini, pedidos_rpa.json, logs nem
  na pasta python\ embutida.

  Uso (clique em ATUALIZAR.bat) ou:
    powershell -ExecutionPolicy Bypass -File atualizar_app.ps1
    powershell -ExecutionPolicy Bypass -File atualizar_app.ps1 -Destino "D:\AppPedidos CLIPP"
#>
param(
    [string]$Destino = ""
)

$ErrorActionPreference = "Stop"
$Origem = $PSScriptRoot

# Copia todos os *.py que vieram no pacote (a lista e definida no gerador).
$ArquivosPy = Get-ChildItem -Path $Origem -Filter *.py -File |
    Select-Object -ExpandProperty Name
$Pastas = @("extensao_chrome")

if (-not (Test-Path (Join-Path $Origem "importar_servidor.py"))) {
    throw "Execute na pasta extraida do ZIP de atualizacao (onde esta importar_servidor.py)."
}

function Find-Destino {
    param([string]$Informado)
    if ($Informado) { return $Informado }
    $candidatos = @(
        "$env:ProgramFiles\AppPedidos CLIPP",
        "${env:ProgramFiles(x86)}\AppPedidos CLIPP",
        "$env:LOCALAPPDATA\AppPedidos CLIPP",
        "C:\AppPedidos CLIPP",
        "D:\AppPedidos CLIPP"
    )
    foreach ($c in $candidatos) {
        if ($c -and (Test-Path (Join-Path $c "importar_servidor.py"))) { return $c }
    }
    return "$env:ProgramFiles\AppPedidos CLIPP"
}

$Destino = Find-Destino $Destino

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $admin -and $Destino -like "$env:ProgramFiles*") {
    Write-Host "Reexecutando como Administrador..." -ForegroundColor Yellow
    Start-Process powershell -Verb RunAs -ArgumentList @(
        "-ExecutionPolicy", "Bypass",
        "-File", $MyInvocation.MyCommand.Path,
        "-Destino", "`"$Destino`""
    )
    exit 0
}

if (-not (Test-Path (Join-Path $Destino "importar_servidor.py"))) {
    throw "Instalacao nao encontrada em: $Destino`nUse: atualizar_app.ps1 -Destino `"<pasta instalada>`""
}

Write-Host ""
Write-Host "=== AppPedidos CLIPP - Atualizacao ===" -ForegroundColor Cyan
Write-Host "Origem:  $Origem"
Write-Host "Destino: $Destino"
Write-Host ""

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

Write-Host "Copiando arquivos atualizados..." -ForegroundColor Cyan
foreach ($a in $ArquivosPy) {
    $src = Join-Path $Origem $a
    if (Test-Path $src) {
        Copy-Item -Force $src (Join-Path $Destino $a)
        Write-Host "  - $a"
    }
}
foreach ($p in $Pastas) {
    $src = Join-Path $Origem $p
    $dst = Join-Path $Destino $p
    if (Test-Path $src) {
        if (Test-Path $dst) {
            Remove-Item -Recurse -Force $dst -ErrorAction SilentlyContinue
        }
        Copy-Item -Recurse -Force $src $dst
        Write-Host "  - $p\ (extensao)"
    }
}
foreach ($extra in @("atualizar_github.ps1", "atualizar_github.py")) {
    $srcExtra = Join-Path $Origem $extra
    if (Test-Path $srcExtra) {
        Copy-Item -Force $srcExtra (Join-Path $Destino $extra)
        Write-Host "  - $extra"
    }
}

# Limpa cache compilado para garantir que o novo codigo rode.
$pycache = Join-Path $Destino "__pycache__"
if (Test-Path $pycache) { Remove-Item -Recurse -Force $pycache -ErrorAction SilentlyContinue }

Write-Host ""
Write-Host "Atualizacao concluida!" -ForegroundColor Green

$launcher = Join-Path $Destino "AppPedidos CLIPP.bat"
if (Test-Path $launcher) {
    Write-Host "Reiniciando servidor..."
    Start-Process -FilePath $launcher -WorkingDirectory $Destino
}

Write-Host ""
Write-Host "IMPORTANTE (extensao Chrome):" -ForegroundColor Yellow
Write-Host "  Abra chrome://extensions e clique em ATUALIZAR (Reload) na"
Write-Host "  extensao 'Tiao Cards -> CLIPP' para pegar a versao 1.6.6."
Write-Host ""
