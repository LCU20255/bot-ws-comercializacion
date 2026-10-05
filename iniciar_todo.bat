@echo off
title Bot Comercializacion - Launcher
echo ========================================================
echo   INICIANDO BOT DE COMERCIALIZACION Y WHATSAPP BAILEYS
echo ========================================================

:: 1. Iniciar Servidor Backend (Python + FastAPI + Dashboard)
start "Servidor Backend & Panel Web" cmd /k "python run.py"

:: Esperar 3 segundos a que levante el backend
timeout /t 3 /nobreak >nul

:: 2. Iniciar Cliente WhatsApp Baileys (Muestra el codigo QR)
start "WhatsApp Baileys (Escanear QR)" cmd /k "iniciar_baileys.bat"

:: 3. Abrir el panel en el navegador
timeout /t 2 /nobreak >nul
start http://localhost:8000/admin

echo.
echo [LISTO]
echo - Se abrio la ventana de WhatsApp Baileys para escanear el QR.
echo - Se abrio el Panel Web en tu navegador: http://localhost:8000/admin
echo.
pause
