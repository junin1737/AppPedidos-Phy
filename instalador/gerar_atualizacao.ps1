#Requires -Version 5.1
<#
  Gera ZIP de ATUALIZACAO (somente arquivos de codigo alterados; NAO embute o
  Python). Use quando a maquina de producao JA tem o AppPedidos instalado e voce
  so quer atualizar o codigo.

  Uso:
    powershell -ExecutionPolicy Bypass -File instalador\gerar_atualizacao.ps1
#>
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Dist = Join-Path $PSScriptRoot "dist"
$Staging = Join-Path $PSScriptRoot "staging_update"
$ZipPath = Join-Path $Dist "AppPedidosCLIPP-Update.zip"

# Espelha o setup.iss: leva TODOS os *.py de runtime (inclui schema_app,
# correios_api, tela_postagens, servidor_app, limites_campos...). Sem isso, uma
# producao anterior aos Correios fica sem os modulos novos.
$ExcluirPy = @(
    "gerar_pdf_estudo.py", "_reparar_visibilidade.py", "aplicacao_vendas.py",
    "importar_site.py", "comparar_vendas_clipp.py", "extrator_ocr.py",
    "teste_prepostagem.py"
)
$ArquivosPy = Get-ChildItem -Path $Root -Filter *.py -File |
    Where-Object { $ExcluirPy -notcontains $_.Name -and $_.Name -notlike "_tmp_*" } |
    Select-Object -ExpandProperty Name
$Pastas = @("extensao_chrome")

if (Test-Path $Staging) { Remove-Item -Recurse -Force $Staging }
New-Item -ItemType Directory -Force -Path $Staging, $Dist | Out-Null

Write-Host "Montando pacote de atualizacao..." -ForegroundColor Cyan
foreach ($a in $ArquivosPy) {
    Copy-Item -Force (Join-Path $Root $a) (Join-Path $Staging $a)
    Write-Host "  + $a"
}
foreach ($p in $Pastas) {
    Copy-Item -Recurse -Force (Join-Path $Root $p) (Join-Path $Staging $p)
    Write-Host "  + $p\"
    Remove-Item -Recurse -Force (Join-Path $Staging "$p\__pycache__") -ErrorAction SilentlyContinue
}
Copy-Item -Force (Join-Path $PSScriptRoot "atualizar_app.ps1") (Join-Path $Staging "atualizar_app.ps1")
$ps1Git = Join-Path $Root "atualizar_github.ps1"
if (Test-Path $ps1Git) {
    Copy-Item -Force $ps1Git (Join-Path $Staging "atualizar_github.ps1")
    Write-Host "  + atualizar_github.ps1"
}

@"
@echo off
cd /d "%~dp0"
title Atualizar AppPedidos CLIPP
echo.
echo Atualizacao - AppPedidos CLIPP (somente codigo)
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0atualizar_app.ps1"
pause
"@ | Set-Content -Path (Join-Path $Staging "ATUALIZAR.bat") -Encoding ASCII

@"
ATUALIZACAO AppPedidos CLIPP
============================

Na maquina de producao (que JA tem o AppPedidos instalado):

  1. Extraia este ZIP em qualquer pasta.
  2. Execute ATUALIZAR.bat (como Administrador se estiver em Arquivos de Programas).
     - Ele encerra o servidor, copia os arquivos novos e reinicia.
     - NAO mexe em config.ini nem nos pedidos ja importados.
  3. No Chrome: abra chrome://extensions e clique em ATUALIZAR (Reload)
     na extensao "Tiao Cards -> CLIPP" (versao 1.6.4).

Se a instalacao nao estiver na pasta padrao, rode pelo PowerShell:
  powershell -ExecutionPolicy Bypass -File atualizar_app.ps1 -Destino "CAMINHO\DA\INSTALACAO"

IMPORTANTE (primeira atualizacao com Correios):
  Se a producao e ANTERIOR a integracao dos Correios, edite o config.ini da
  instalacao e adicione a secao [correios] com as credenciais e o remetente
  (copie do config.ini de referencia). As tabelas/trigger da etiqueta sao
  criadas sozinhas quando o servidor sobe. A procedure XX_INC_PDV_PEDV tambem
  e atualizada automaticamente ao subir o servidor (OBS -> TB_PEDIDO_VENDA).
"@ | Set-Content -Path (Join-Path $Staging "LEIA-ME.txt") -Encoding UTF8

if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
Push-Location $Staging
try {
    & tar.exe -a -cf $ZipPath *
} finally {
    Pop-Location
}
Remove-Item -Recurse -Force $Staging

$kb = [math]::Round((Get-Item $ZipPath).Length / 1KB, 0)
Write-Host ""
Write-Host "ZIP de atualizacao gerado: $ZipPath ($kb KB)" -ForegroundColor Green
Write-Host ""
Write-Host "Na maquina de producao: extrair e rodar ATUALIZAR.bat" -ForegroundColor Cyan
