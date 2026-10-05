#!/usr/bin/env bash
set -e

echo "=== [1/2] Instalando paquetes de Python ==="
pip install -r requirements.txt

echo "=== [2/2] Instalando paquetes de Node.js para WhatsApp Baileys ==="
cd whatsapp_baileys
npm install --production
cd ..

echo "=== Build completado con éxito para Render ==="
