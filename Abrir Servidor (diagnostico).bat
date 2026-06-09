@echo off
cd /d "%~dp0"
title AppPedidos CLIPP - Diagnostico
echo.
echo === AppPedidos CLIPP - modo diagnostico (mostra erros) ===
echo Pasta: %CD%
echo.

if exist "%~dp0python\python.exe" (
  set "PYEXE=%~dp0python\python.exe"
) else (
  where py >nul 2>&1 || (
    echo Python nao encontrado.
    pause
    exit /b 1
  )
  set "PYEXE=py"
  set "PYARG=-3"
)

echo Testando interface grafica (tkinter)...
if defined PYARG (
  %PYEXE% %PYARG% -c "import tkinter; r=tkinter.Tk(); r.withdraw(); r.destroy(); print('tkinter OK')"
) else (
  "%PYEXE%" -c "import tkinter; r=tkinter.Tk(); r.withdraw(); r.destroy(); print('tkinter OK')"
)
if errorlevel 1 (
  echo.
  echo ERRO: tkinter nao funciona nesta maquina/sessao.
  echo Windows Server precisa de Experiencia de Desktop ou sessao RDP com GUI.
  pause
  exit /b 1
)

echo.
echo Iniciando servidor (janela + console)...
echo Log: %LOCALAPPDATA%\AppPedidosCLIPP\servidor_clipp.log
echo.
if defined PYARG (
  %PYEXE% %PYARG% servidor_app.py
) else (
  "%PYEXE%" servidor_app.py
)
echo.
echo Encerrado com codigo %ERRORLEVEL%
pause
