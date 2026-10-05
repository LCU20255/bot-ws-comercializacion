#!/usr/bin/env bash
set -e

echo "=== [1/2] Instalando dependencias de Python ==="
pip install -r requirements.txt

echo "=== [2/2] Instalando dependencias de Node.js para WhatsApp Baileys ==="
cd whatsapp_baileys
npm install --omit=dev --no-audit --no-fund
cd ..

echo "=== Build completado con éxito para Render ==="

