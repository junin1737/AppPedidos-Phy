@echo off
cd /d "%~dp0"
echo.
echo ============================================================
echo   PASSO 2 de 2 — IMPORTAR PEDIDO (Chrome ja deve estar aberto)
echo ============================================================
echo.
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:9222/json/version' -TimeoutSec 2 -UseBasicParsing; exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
  echo [AVISO] Chrome RPA nao detectado na porta 9222.
  echo.
  echo Rode PRIMEIRO: Abrir Chrome RPA.bat
  echo Faca login la e depois volte aqui.
  echo.
  pause
  exit /b 1
)
echo Chrome RPA detectado — OK.
echo.
if "%~1"=="" (
  set /p NUM=Numero do pedido no site: 
) else (
  set NUM=%~1
)
if "%NUM%"=="" (
  echo Nenhum numero informado.
  pause
  exit /b 1
)
py -3 importar_site.py %NUM%
pause
