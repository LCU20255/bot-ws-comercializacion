@echo off
title WhatsApp Baileys - Bot Comercializacion
set "PATH=%~dp0data\mingit\cmd;C:\Program Files\nodejs;%PATH%"
echo ========================================================
echo Conectando WhatsApp via Baileys...
echo ========================================================
cd whatsapp_baileys
node index.js
pause
