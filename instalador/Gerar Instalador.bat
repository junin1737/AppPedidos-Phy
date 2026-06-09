@echo off
cd /d "%~dp0"
title Gerar instalador AppPedidos CLIPP
echo.
echo === Gerar AppPedidosCLIPP-Setup.exe ===
echo.
echo 1. Monta Python 3.12 + bibliotecas no instalador
echo 2. Compila AppPedidosCLIPP-Setup.exe (Inno Setup 6)
echo.
echo Requer Python 3.12 com tkinter na maquina de build.
echo (winget instala automaticamente se faltar)
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0preparar_payload.ps1"
set ERR=%ERRORLEVEL%
if %ERR%==2 (
  echo.
  echo Payload preparado. Instale Inno Setup e rode este .bat de novo.
  pause
  exit /b 2
)
if %ERR% neq 0 (
  echo Falha.
  pause
  exit /b 1
)

echo.
echo Copie para outras maquinas:
echo   instalador\dist\AppPedidosCLIPP-Setup.exe
echo.
pause
