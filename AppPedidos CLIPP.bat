@echo off
cd /d "%~dp0"
if exist "%~dp0python\pythonw.exe" (
  start "" "%~dp0python\pythonw.exe" "%~dp0servidor_app.py"
  exit /b 0
)
if exist "%~dp0Iniciar Servidor CLIPP.vbs" (
  start "" wscript.exe "%~dp0Iniciar Servidor CLIPP.vbs"
  exit /b 0
)
echo AppPedidos CLIPP: Python embutido nao encontrado.
echo Reinstale com AppPedidosCLIPP-Setup.exe
pause
exit /b 1
