@echo off
cd /d "%~dp0"
title Gerar pacote ZIP (sem Setup.exe)
echo.
echo === AppPedidos CLIPP - pacote ZIP ===
echo.
echo Use este metodo se o Windows Defender bloquear o Setup.exe
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0empacotar_zip.ps1"
pause
