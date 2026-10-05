import uvicorn
import os
import sys

# Ensure UTF-8 output on Windows terminal
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from app.config import PORT, HOST

if __name__ == "__main__":
    print("=" * 60)
    print(">> INICIANDO BOT DE COMERCIALIZACION Y AGENDAMIENTO")
    print(f"🌐 Panel Administrativo: http://localhost:{PORT}/admin")
    print(f"📱 Simulador WhatsApp:   http://localhost:{PORT}/admin (Pestaña Simulador)")
    print(f"📊 Exportar a Excel:     http://localhost:{PORT}/api/export/excel")
    print("=" * 60)
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=True)
