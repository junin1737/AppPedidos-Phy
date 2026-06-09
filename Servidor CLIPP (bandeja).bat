@echo off
cd /d "%~dp0"
title AppPedidos CLIPP
echo.
echo AppPedidos CLIPP - Servidor visual + bandeja do Windows
echo.

where py >nul 2>&1 || (
  echo Python 3 nao encontrado. Instale de https://www.python.org/downloads/
  echo Marque "Add python.exe to PATH" e "py launcher".
  pause
  exit /b 1
)

py -3 -m pip install -q -r "%~dp0servidor-requirements.txt" 2>nul
start "" wscript.exe "%~dp0Iniciar Servidor CLIPP.vbs"
echo.
echo Aplicativo iniciado.
echo   - Janela de status do servidor
echo   - Icone azul perto do relogio (bandeja)
echo   - Inicia automaticamente com o Windows
echo.
timeout /t 4 >nul
