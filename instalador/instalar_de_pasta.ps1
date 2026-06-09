#Requires -Version 5.1
<#
  Instala a partir de uma pasta ja extraida (ZIP) com python\ embutido.
  Nao precisa Python instalado no PC de destino.

  Uso (como Administrador):
    powershell -ExecutionPolicy Bypass -File instalar_de_pasta.ps1
    powershell -ExecutionPolicy Bypass -File instalar_de_pasta.ps1 -Destino "D:\AppPedidos CLIPP"
#>
param(
    [string]$Destino = "$env:ProgramFiles\AppPedidos CLIPP"
)

$ErrorActionPreference = "Stop"
$Origem = $PSScriptRoot

if (-not (Test-Path "$Origem\servidor_app.py")) {
    throw "Execute este script na pasta extraida do ZIP (onde esta servidor_app.py)."
}
if (-not (Test-Path "$Origem\python\pythonw.exe")) {
    throw "Pasta python\ nao encontrada. Use o ZIP gerado por empacotar_zip.ps1."
}

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

Write-Host ""
Write-Host "=== AppPedidos CLIPP - Instalar de pasta/ZIP ===" -ForegroundColor Cyan
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

$ignorar = @("__pycache__", ".git", ".cursor", ".rpa_profile", "instalador", "pedidos_rpa.json")
$incluirExt = @(".py", ".dll", ".txt", ".md", ".json", ".html", ".js", ".css", ".vbs", ".bat", ".ps1", ".exemplo", ".ini", ".png", ".ico")

New-Item -ItemType Directory -Force -Path $Destino | Out-Null

function Copiar-Recursivo($baseOrigem, $baseDestino) {
    Get-ChildItem -Path $baseOrigem -Force | ForEach-Object {
        $nome = $_.Name
        foreach ($ig in $ignorar) {
            if ($nome -eq $ig) { return }
        }
        $alvo = Join-Path $baseDestino $nome
        if ($_.PSIsContainer) {
            if ($nome -eq "python" -or $nome -eq "extensao_chrome" -or $_.Extension -eq "") {
                New-Item -ItemType Directory -Force -Path $alvo | Out-Null
                Copiar-Recursivo $_.FullName $alvo
            }
            return
        }
        if ($nome -eq "config.ini") { return }
        $ext = $_.Extension.ToLower()
        if ($baseOrigem -like "*\extensao_chrome*" -or $incluirExt -contains $ext -or $nome -eq "fbclient64.dll") {
            Copy-Item -Force $_.FullName $alvo
        }
    }
}

Copiar-Recursivo $Origem $Destino

if (-not (Test-Path "$Destino\config.ini")) {
    Copy-Item "$Destino\config.ini.exemplo" "$Destino\config.ini"
    Write-Host "Criado config.ini a partir do exemplo."
}

$iconPath = Join-Path $Destino "app_icon.ico"
$launcher = Join-Path $Destino "AppPedidos CLIPP.bat"
$shell = New-Object -ComObject WScript.Shell

$startup = [Environment]::GetFolderPath("Startup")
$s = $shell.CreateShortcut((Join-Path $startup "AppPedidos CLIPP.lnk"))
$s.TargetPath = $launcher
$s.WorkingDirectory = $Destino
$s.Description = "Servidor importador Tiao Cards -> CLIPP"
if (Test-Path $iconPath) { $s.IconLocation = "$iconPath,0" }
$s.Save()

$desktop = [Environment]::GetFolderPath("Desktop")
$d = $shell.CreateShortcut((Join-Path $desktop "AppPedidos CLIPP.lnk"))
$d.TargetPath = $launcher
$d.WorkingDirectory = $Destino
$d.Description = "Servidor importador Tiao Cards -> CLIPP"
if (Test-Path $iconPath) { $d.IconLocation = "$iconPath,0" }
$d.Save()

$e = $shell.CreateShortcut((Join-Path $desktop "Extensao Chrome CLIPP.lnk"))
$e.TargetPath = "explorer.exe"
$e.Arguments = "`"$Destino\extensao_chrome`""
$e.Description = "Pasta da extensao Chrome"
if (Test-Path $iconPath) { $e.IconLocation = "$iconPath,0" }
$e.Save()

Write-Host ""
Write-Host "Instalacao concluida!" -ForegroundColor Green
Write-Host "  Destino: $Destino"
Write-Host "  Configure config.ini e carregue a extensao no Chrome."
Write-Host ""
Write-Host "Iniciando servidor..."
Start-Process -FilePath $launcher -WorkingDirectory $Destino
Write-Host "Pronto." -ForegroundColor Cyan
