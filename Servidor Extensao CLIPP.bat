@echo off
cd /d "%~dp0"
echo.
echo Servidor para a extensao Chrome (seu Chrome normal, ja logado).
echo Mantenha esta janela aberta enquanto usar a extensao.
echo.
py -3 importar_servidor.py
pause
