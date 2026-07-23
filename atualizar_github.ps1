#Requires -Version 5.1
<#
  Atualiza o AppPedidos CLIPP baixando o código direto do GitHub (ZIP).
  Não abre navegador. Não altera config.ini, pedidos_rpa.json nem logs.

  Chamado pelo botão «Atualizar do GitHub» em servidor_app.py — o app encerra
  antes deste script copiar os arquivos e reiniciar o launcher.

  Uso manual:
    powershell -ExecutionPolicy Bypass -File atualizar_github.ps1
    powershell -ExecutionPolicy Bypass -File atualizar_github.ps1 -Destino "D:\AppPedidos CLIPP"
#>
param(
    [string]$Destino = "",
    [string]$Repositorio = "junin1737/AppPedidos-Phy",
    [string]$Branch = "main",
    [int]$AguardarSegundos = 8
)

$ErrorActionPreference = "Stop"

$ExcluirPy = @(
    "gerar_pdf_estudo.py", "_reparar_visibilidade.py", "aplicacao_vendas.py",
    "importar_site.py", "comparar_vendas_clipp.py", "extrator_ocr.py",
    "teste_prepostagem.py"
)
$Pastas = @("extensao_chrome")
$LogFile = Join-Path $env:TEMP "AppPedidosCLIPP-atualizar.log"

function Write-Log {
    param([string]$Msg)
    $linha = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Msg"
    Add-Content -Path $LogFile -Value $linha -Encoding UTF8
    Write-Host $Msg
}

function Find-Destino {
    param([string]$Informado)
    if ($Informado) { return $Informado }
    $here = $PSScriptRoot
    if (Test-Path (Join-Path $here "servidor_app.py")) { return $here }
    $candidatos = @(
        "$env:ProgramFiles\AppPedidos CLIPP",
        "${env:ProgramFiles(x86)}\AppPedidos CLIPP",
        "$env:LOCALAPPDATA\AppPedidos CLIPP",
        "C:\AppPedidos CLIPP",
        "D:\AppPedidos CLIPP"
    )
    foreach ($c in $candidatos) {
        if ($c -and (Test-Path (Join-Path $c "servidor_app.py"))) { return $c }
    }
    return $here
}

$Destino = Find-Destino $Destino

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $admin -and $Destino -like "$env:ProgramFiles*") {
    Write-Log "Reexecutando como Administrador..."
    Start-Process powershell -Verb RunAs -ArgumentList @(
        "-ExecutionPolicy", "Bypass",
        "-File", $MyInvocation.MyCommand.Path,
        "-Destino", "`"$Destino`"",
        "-Repositorio", $Repositorio,
        "-Branch", $Branch,
        "-AguardarSegundos", $AguardarSegundos
    )
    exit 0
}

if (-not (Test-Path (Join-Path $Destino "servidor_app.py"))) {
    throw "Instalação não encontrada em: $Destino"
}

Write-Log "=== AppPedidos CLIPP — atualização GitHub ==="
Write-Log "Repositório: $Repositorio ($Branch)"
Write-Log "Destino: $Destino"
Write-Log "Log: $LogFile"

function Stop-AppPedidosProcessos {
    param([string[]]$PastasProc)
    Write-Log "Encerrando processos do AppPedidos..."
    $ids = [System.Collections.Generic.HashSet[int]]::new()
    $pastas = @($PastasProc | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique)
    $meuPid = $PID

    if ($AguardarSegundos -gt 0) {
        Write-Log "Aguardando ${AguardarSegundos}s para o app fechar..."
        Start-Sleep -Seconds $AguardarSegundos
    }

    for ($t = 0; $t -lt 15; $t++) {
        $rodando = $false
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | ForEach-Object {
            $cmd = $_.CommandLine
            if (-not $cmd) { return }
            if ($_.ProcessId -eq $meuPid) { return }
            # Nunca encerrar o próprio atualizador
            if ($cmd -match 'atualizar_github\.(ps1|py)') { return }
            if ($cmd -match 'AppPedidosCLIPP-git-') { return }

            $fecha = $false
            if ($cmd -match 'servidor_app\.py|importar_servidor\.py') { $fecha = $true }
            elseif ($cmd -match 'AppPedidos CLIPP\.bat') { $fecha = $true }
            else {
                foreach ($pasta in $pastas) {
                    if (
                        ($cmd -like "*$pasta*") -and
                        ($cmd -match 'pythonw?\.exe') -and
                        ($cmd -notmatch 'atualizar_github')
                    ) {
                        $fecha = $true
                        break
                    }
                }
            }
            if (-not $fecha) { return }
            $rodando = $true
            if ($t -ge 3) {
                try {
                    Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop
                    [void]$ids.Add([int]$_.ProcessId)
                    Write-Log "  Encerrado PID $($_.ProcessId)"
                } catch {}
            }
        }
        if (-not $rodando) { break }
        Start-Sleep -Seconds 1
    }

    foreach ($pasta in $pastas) {
        foreach ($nome in @("pythonw", "python")) {
            $exe = Join-Path $pasta "python\$nome.exe"
            if (-not (Test-Path $exe)) { continue }
            Get-Process -Name $nome -ErrorAction SilentlyContinue | ForEach-Object {
                try {
                    if ($_.Id -eq $meuPid) { return }
                    if ($_.Path -and ($_.Path -ieq $exe)) {
                        Stop-Process -Id $_.Id -Force -ErrorAction Stop
                        [void]$ids.Add($_.Id)
                    }
                } catch {}
            }
        }
    }

    if ($ids.Count -gt 0) {
        Write-Log "$($ids.Count) processo(s) encerrado(s). Aguardando liberar arquivos..."
        Start-Sleep -Seconds 2
    }
}

Stop-AppPedidosProcessos -PastasProc @($Destino)

$repoNome = ($Repositorio -split '/')[-1]
$zipUrl = "https://github.com/$Repositorio/archive/refs/heads/$Branch.zip"
$tempBase = Join-Path $env:TEMP "AppPedidosCLIPP-git-$(Get-Date -Format 'yyyyMMddHHmmss')"
$zipPath = Join-Path $tempBase "repo.zip"
$extractRoot = Join-Path $tempBase "extract"

New-Item -ItemType Directory -Force -Path $tempBase, $extractRoot | Out-Null

try {
    Write-Log "Baixando $zipUrl ..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing

    Write-Log "Extraindo arquivo..."
    Expand-Archive -Path $zipPath -DestinationPath $extractRoot -Force

    $origem = Join-Path $extractRoot "$repoNome-$Branch"
    if (-not (Test-Path $origem)) {
        $origem = Get-ChildItem -Path $extractRoot -Directory | Select-Object -First 1 -ExpandProperty FullName
    }
    if (-not $origem -or -not (Test-Path (Join-Path $origem "servidor_app.py"))) {
        throw "Pacote do GitHub inválido (servidor_app.py não encontrado)."
    }

    Write-Log "Copiando arquivos de $origem ..."
    $pyOrigem = Get-ChildItem -Path $origem -Filter *.py -File |
        Where-Object { $ExcluirPy -notcontains $_.Name -and $_.Name -notlike "_tmp_*" }
    foreach ($f in $pyOrigem) {
        Copy-Item -Force $f.FullName (Join-Path $Destino $f.Name)
        Write-Log "  + $($f.Name)"
    }
    foreach ($p in $Pastas) {
        $src = Join-Path $origem $p
        $dst = Join-Path $Destino $p
        if (Test-Path $src) {
            if (Test-Path $dst) {
                Remove-Item -Recurse -Force $dst -ErrorAction SilentlyContinue
            }
            Copy-Item -Recurse -Force $src $dst
            Write-Log "  + $p\"
        }
    }
    foreach ($extra in @("atualizar_github.ps1", "atualizar_github.py")) {
        $srcExtra = Join-Path $origem $extra
        if (Test-Path $srcExtra) {
            Copy-Item -Force $srcExtra (Join-Path $Destino $extra)
            Write-Log "  + $extra"
        }
    }

    $pycache = Join-Path $Destino "__pycache__"
    if (Test-Path $pycache) {
        Remove-Item -Recurse -Force $pycache -ErrorAction SilentlyContinue
    }

    Write-Log "Atualização concluída."
}
finally {
    if (Test-Path $tempBase) {
        Remove-Item -Recurse -Force $tempBase -ErrorAction SilentlyContinue
    }
}

$launcher = Join-Path $Destino "AppPedidos CLIPP.bat"
if (Test-Path $launcher) {
    Write-Log "Reiniciando AppPedidos CLIPP..."
    Start-Process -FilePath $launcher -WorkingDirectory $Destino
} else {
    $pyw = Join-Path $Destino "python\pythonw.exe"
    if (Test-Path $pyw) {
        Start-Process -FilePath $pyw -ArgumentList "`"$(Join-Path $Destino 'servidor_app.py')`"" -WorkingDirectory $Destino
    } else {
        Write-Log "Launcher não encontrado — abra o AppPedidos manualmente."
    }
}

Write-Log "Concluído. Recarregue a extensão Chrome em chrome://extensions se necessário."
