#!/usr/bin/env bash
set -e

echo "=== Iniciando Sistema de Comercialización Textil Militar en Render ==="
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"

