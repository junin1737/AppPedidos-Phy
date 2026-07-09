@echo off
title Corrigir Postagens (instalar requests) - AppPedidos CLIPP

REM ---------------------------------------------------------------------------
REM Instala o pacote "requests" no Python embutido da instalacao de producao.
REM Resolve o erro: "Aba Postagens indisponivel: No module named 'requests'".
REM Basta dar duplo clique (ele se eleva para Administrador sozinho).
REM ---------------------------------------------------------------------------

REM --- Auto-elevar para Administrador ---
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Solicitando privilegios de Administrador...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

setlocal EnableExtensions

REM --- Localiza a instalacao (ajuste INSTALL se estiver em outra pasta) ---
set "INSTALL=C:\Program Files\AppPedidos CLIPP"
if not exist "%INSTALL%\python\python.exe" set "INSTALL=%LOCALAPPDATA%\AppPedidos CLIPP"
if not exist "%INSTALL%\python\python.exe" set "INSTALL=C:\AppPedidos CLIPP"
if not exist "%INSTALL%\python\python.exe" set "INSTALL=D:\AppPedidos CLIPP"

if not exist "%INSTALL%\python\python.exe" (
    echo.
    echo [ERRO] Nao encontrei a instalacao do AppPedidos CLIPP.
    echo Edite este .bat e ajuste a variavel INSTALL para a pasta correta.
    echo.
    pause
    exit /b 1
)

echo.
echo Instalacao: %INSTALL%
echo.

echo [1/4] Encerrando servidor em execucao...
taskkill /f /im pythonw.exe >nul 2>&1
taskkill /f /im python.exe  >nul 2>&1
timeout /t 2 /nobreak >nul

echo [2/4] Atualizando pip...
"%INSTALL%\python\python.exe" -m pip install --upgrade pip

echo [3/4] Instalando requests...
"%INSTALL%\python\python.exe" -m pip install requests

echo [4/4] Reiniciando o AppPedidos CLIPP...
start "" /D "%INSTALL%" "%INSTALL%\AppPedidos CLIPP.bat"

echo.
echo ===========================================================
echo Concluido. Abra a aba "Postagens (Correios)" para conferir.
echo ===========================================================
echo.
pause
