@echo off
cd /d "%~dp0"
echo.
echo ============================================================
echo   PASSO 1 de 2 — SOMENTE ABRE O CHROME (nao importa pedido)
echo ============================================================
echo.
echo Esta janela preta NAO faz a importacao.
echo Depois de logar, use: Importar do Site.bat
echo.
set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" (
  echo Chrome nao encontrado.
  pause
  exit /b 1
)
start "" "%CHROME%" ^
  --remote-debugging-port=9222 ^
  --user-data-dir="%~dp0.rpa_profile" ^
  "https://www.tiaocards.com.br/"
echo Chrome aberto.
echo.
echo AGORA NO CHROME:
echo   1) Login + 2FA
echo   2) Minha conta ^> Dashboard administrativo ^> Pedidos
echo   3) Deixe o Chrome aberto
echo.
echo DEPOIS, EM OUTRA JANELA:
echo   Importar do Site.bat
echo   ou: py -3 importar_site.py NUMERO_PEDIDO
echo.
echo Pressione uma tecla para FECHAR SO ESTA JANELA.
echo O Chrome continua aberto.
pause >nul
