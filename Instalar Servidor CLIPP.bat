@echo off
cd /d "%~dp0"
title Instalar AppPedidos CLIPP
echo.
echo === Instalar AppPedidos CLIPP neste PC ===
echo.
echo Isso vai:
echo   1. Instalar dependencias Python
echo   2. Criar atalho na Area de Trabalho
echo   3. Configurar inicio automatico com o Windows
echo   4. Abrir o servidor agora
echo.
pause

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalador\instalar.ps1" -Destino "%~dp0"
if errorlevel 1 (
  echo.
  echo Falha na instalacao.
  pause
  exit /b 1
)

echo.
echo Concluido. Use o atalho "AppPedidos CLIPP" na area de trabalho.
pause
