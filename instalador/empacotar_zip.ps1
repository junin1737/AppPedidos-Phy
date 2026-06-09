#Requires -Version 5.1
<#
  Gera ZIP para instalacao (evita falso positivo do Defender no Setup.exe).

  Uso:
    powershell -ExecutionPolicy Bypass -File instalador\empacotar_zip.ps1
#>
param(
    [switch]$ForcarPython
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PayloadPython = Join-Path $PSScriptRoot "payload\python"
$Dist = Join-Path $PSScriptRoot "dist"
$Staging = Join-Path $PSScriptRoot "staging_zip"
$ZipPath = Join-Path $Dist "AppPedidosCLIPP.zip"

if (-not (Test-Path $PayloadPython) -or $ForcarPython) {
    & (Join-Path $PSScriptRoot "preparar_payload.ps1") -SemCompilar -ForcarPython:$ForcarPython
}

if (-not (Test-Path "$PayloadPython\pythonw.exe")) {
    throw "payload\python ausente. Rode instalador\Gerar Instalador.bat primeiro."
}

Write-Host "Montando pacote ZIP..." -ForegroundColor Cyan
if (Test-Path $Staging) { Remove-Item -Recurse -Force $Staging }
New-Item -ItemType Directory -Force -Path $Staging, $Dist | Out-Null

$ignorar = @(
    "__pycache__", ".cursor", ".rpa_profile", ".git", "instalador\dist",
    "instalador\staging_zip", "instalador\payload", "instalador\redist",
    "EstudoProjetoPedidos.pdf", "gerar_pdf_estudo.py", "_reparar_visibilidade.py",
    "config.ini", "pedidos_rpa.json", "servidor_clipp.log"
)
$incluirExt = @(".py", ".dll", ".txt", ".md", ".json", ".html", ".js", ".css", ".vbs", ".bat", ".ps1", ".exemplo", ".png", ".ico")

Get-ChildItem -Path $Root -Force | ForEach-Object {
    $rel = $_.Name
    if ($ignorar -contains $rel) { return }
    if ($_.PSIsContainer) {
        if ($rel -in @("extensao_chrome", "instalador")) {
            if ($rel -eq "instalador") {
                Copy-Item "$Root\instalador\instalar_de_pasta.ps1" (Join-Path $Staging "instalar_app.ps1")
                return
            }
            Copy-Item -Recurse -Force $_.FullName (Join-Path $Staging $rel)
        }
        return
    }
    $ext = $_.Extension.ToLower()
    if ($incluirExt -contains $ext) {
        Copy-Item -Force $_.FullName (Join-Path $Staging $rel)
    }
}

robocopy $PayloadPython (Join-Path $Staging "python") /E /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null

@"
@echo off
cd /d "%~dp0"
title Instalar AppPedidos CLIPP
echo.
echo Instalador ZIP - AppPedidos CLIPP
echo (Python ja incluso - nao precisa instalar Python no PC)
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar_app.ps1"
pause
"@ | Set-Content -Path (Join-Path $Staging "INSTALAR.bat") -Encoding ASCII

if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
Push-Location $Staging
try {
    & tar.exe -a -cf $ZipPath *
} finally {
    Pop-Location
}
Remove-Item -Recurse -Force $Staging

$mb = [math]::Round((Get-Item $ZipPath).Length / 1MB, 1)
Write-Host ""
Write-Host "ZIP gerado: $ZipPath ($mb MB)" -ForegroundColor Green
Write-Host ""
Write-Host "Na outra maquina:"
Write-Host "  1. Extrair o ZIP"
Write-Host "  2. Executar INSTALAR.bat como Administrador"
Write-Host ""
