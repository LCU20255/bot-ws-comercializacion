import io
import os
import shutil
import logging
from pathlib import Path
from fastapi import FastAPI, Request, Response, HTTPException, Query, UploadFile, File
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Any, List

from app.config import (
    BASE_DIR, DATA_DIR, PORT, HOST,
    SUPABASE_URL, SUPABASE_KEY, GEMINI_API_KEY
)
from app.database import (
    init_db, get_products, get_product_by_id,
    create_product, update_product, delete_product,
    get_orders, get_order_by_id, update_order, update_order_status, delete_order,
    mark_reminder_sent, export_orders_df,
    get_all_config, update_config, is_maintenance_active
)
from app.bot_flow import bot_manager, reset_session
import qrcode
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Sistema de Comercialización Textil Militar", version="2.0.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Archivos estáticos
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")

import subprocess
import threading
import time

baileys_process = None
baileys_state = {
    "status": "DISCONNECTED",
    "qr": None,
    "qr_timestamp": 0,
    "phone": None
}

def read_baileys_output(proc):
    global baileys_state
    for line in iter(proc.stdout.readline, ''):
        if not line:
            break
        clean_line = line.strip()
        if "[BAILEYS_QR_DATA]" in clean_line and "[/BAILEYS_QR_DATA]" in clean_line:
            qr = clean_line.split("[BAILEYS_QR_DATA]")[1].split("[/BAILEYS_QR_DATA]")[0].strip()
            baileys_state["qr"] = qr
            baileys_state["qr_timestamp"] = time.time()
            baileys_state["status"] = "QR"
            logger.info("QR Code capturado en tiempo real desde Baileys stdout")
        elif "[BAILEYS_CONNECTED]" in clean_line and "[/BAILEYS_CONNECTED]" in clean_line:
            phone = clean_line.split("[BAILEYS_CONNECTED]")[1].split("[/BAILEYS_CONNECTED]")[0].strip()
            baileys_state["status"] = "CONNECTED"
            baileys_state["phone"] = phone
            baileys_state["qr"] = None
            logger.info(f"WhatsApp Baileys conectado exitosamente: {phone}")
        elif "[BAILEYS_STATUS]DISCONNECTED" in clean_line:
            baileys_state["status"] = "DISCONNECTED"
            logger.info("WhatsApp Baileys desconectado")

def start_baileys_process():
    global baileys_process, baileys_state
    baileys_dir = BASE_DIR / "whatsapp_baileys"
    script_path = baileys_dir / "index.js"
    if not script_path.exists():
        logger.warning(f"No se encontró index.js en {baileys_dir}")
        return

    node_modules = baileys_dir / "node_modules"
    if not node_modules.exists():
        logger.info("Instalando dependencias de Baileys (npm install)...")
        try:
            cmd = "npm install --omit=dev --no-audit --no-fund"
            res = subprocess.run(
                cmd,
                cwd=str(baileys_dir),
                shell=True,
                capture_output=True,
                text=True
            )
            if res.returncode != 0:
                logger.error(f"Error ejecutando npm install (código {res.returncode}): {res.stderr or res.stdout}")
            else:
                logger.info("Dependencias de Baileys instaladas correctamente.")
        except Exception as e:
            logger.error(f"Excepción ejecutando npm install en Baileys: {e}")

    try:
        if baileys_process and baileys_process.poll() is None:
            try:
                baileys_process.terminate()
            except Exception:
                pass

        logger.info("Iniciando subproceso de Baileys con Node...")
        env = os.environ.copy()
        port = env.get("PORT", "8000")
        env["PYTHON_API_URL"] = f"http://127.0.0.1:{port}/api/chat/simulate"
        node_cmd = "node index.js"
        baileys_process = subprocess.Popen(
            node_cmd,
            cwd=str(baileys_dir),
            env=env,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        threading.Thread(target=read_baileys_output, args=(baileys_process,), daemon=True).start()
    except Exception as e:
        logger.error(f"Error iniciando proceso de Baileys: {e}")

@app.on_event("startup")
def startup_event():
    init_db()
    logger.info("Base de datos de comercialización textil militar lista.")
    threading.Thread(target=start_baileys_process, daemon=True).start()

# ----------------- SCHEMAS -----------------
class ProductSchema(BaseModel):
    name: str
    description: Optional[str] = ""
    price: float = 0.0
    price_display: Optional[str] = ""
    category: Optional[str] = "MILITAR"
    image_url: Optional[str] = "/static/images/placeholder.png"
    stock: Optional[int] = 100
    is_active: Optional[int] = 1
    requires_size: Optional[int] = 0
    keywords: Optional[str] = ""
    updated_by: Optional[str] = "ADMIN"

class OrderUpdateSchema(BaseModel):
    client_name: str
    cedula: str
    phone: str
    items_summary: str
    total_items: int = 1
    total_amount: float = 0.0
    payment_method: str = "EFECTIVO / DIVISAS"
    pickup_date: str
    pickup_time: str
    status: str = "PENDIENTE"
    notes: Optional[str] = ""

class StatusUpdateSchema(BaseModel):
    status: str

class SimulateChatSchema(BaseModel):
    phone: str = "+584120000001"
    message: str

class ConfigSchema(BaseModel):
    maintenance_mode: Optional[str] = None
    maintenance_message: Optional[str] = None
    business_hours_start: Optional[str] = None
    business_hours_end: Optional[str] = None
    off_hours_message: Optional[str] = None
    advisor_phone: Optional[str] = None
    advisor_name: Optional[str] = None
    pickup_address: Optional[str] = None
    pickup_hours: Optional[str] = None
    whatsapp_bot_number: Optional[str] = None

# ----------------- VIEWS -----------------
@app.get("/favicon.ico", include_in_schema=False)
def favicon_endpoint():
    return Response(status_code=204)

@app.get("/", response_class=RedirectResponse)
def root_redirect():
    return RedirectResponse(url="/admin")

@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    template_path = BASE_DIR / "app" / "templates" / "admin.html"
    if not template_path.exists():
        return HTMLResponse("<h1>Panel no encontrado</h1>", status_code=404)
    with open(template_path, "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content)

# ----------------- REST API: PRODUCTS -----------------
@app.get("/api/products")
def api_get_products(active_only: bool = False):
    return get_products(only_active=active_only)

@app.post("/api/products")
def api_create_product(prod: ProductSchema):
    pid = create_product(prod.dict(), updated_by=prod.updated_by or "ADMIN")
    return {"status": "ok", "id": pid, "message": "Producto militar registrado con éxito"}

@app.put("/api/products/{product_id}")
def api_update_product(product_id: int, prod: ProductSchema):
    update_product(product_id, prod.dict(), updated_by=prod.updated_by or "ADMIN")
    return {"status": "ok", "message": "Producto militar actualizado con éxito"}

@app.delete("/api/products/{product_id}")
def api_delete_product(product_id: int):
    delete_product(product_id)
    return {"status": "ok", "message": "Producto eliminado con éxito"}

@app.post("/api/upload-image")
async def api_upload_image(file: UploadFile = File(...)):
    images_dir = BASE_DIR / "app" / "static" / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    safe_filename = f"prod_{int(pd.Timestamp.now().timestamp())}_{file.filename.replace(' ', '_')}"
    file_path = images_dir / safe_filename

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    return {"status": "ok", "url": f"/static/images/{safe_filename}"}

# ----------------- REST API: ORDERS / CITAS -----------------
@app.get("/api/orders")
def api_get_orders():
    return get_orders()

@app.get("/api/orders/{order_id}")
def api_get_order(order_id: int):
    ord_data = get_order_by_id(order_id)
    if not ord_data:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")
    return ord_data

@app.put("/api/orders/{order_id}")
def api_update_order(order_id: int, body: OrderUpdateSchema):
    update_order(order_id, body.dict())
    return {"status": "ok", "message": "Pedido actualizado con éxito"}

@app.put("/api/orders/{order_id}/status")
def api_update_order_status(order_id: int, body: StatusUpdateSchema):
    update_order_status(order_id, body.status)
    return {"status": "ok", "message": f"Estado actualizado a {body.status.upper()}"}

@app.delete("/api/orders/{order_id}")
def api_delete_order(order_id: int):
    delete_order(order_id)
    return {"status": "ok", "message": "Pedido eliminado de la base de datos"}

@app.post("/api/orders/{order_id}/retake")
def api_retake_order(order_id: int):
    order = get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")
    
    phone = order["phone"]
    client_name = order["client_name"] if order["client_name"] != "CONTACTO POR ATENDER" else "Estimado(a) Cliente"
    
    message = (
        f"👋 *Saludos cordiales {client_name}:*\n\n"
        "Le escribimos del *Área de Comercialización e Intendencia Militar*. "
        "Nos comunicamos para retomar su consulta y atención tras haber finalizado nuestro periodo de mantenimiento.\n\n"
        "¿En qué artículos militares o confección podemos asistirle el día de hoy?\n\n"
        "Escriba *0* para ver la lista de productos disponibles para entrega inmediata."
    )
    
    update_order_status(order_id, "PENDIENTE POR ATENCIÓN")
    return {"status": "ok", "message": f"Atención retomada para {client_name}", "sent_text": message}

@app.post("/api/orders/{order_id}/send-reminder")
async def api_send_reminder(order_id: int):
    """
    Envía un recordatorio formal por WhatsApp al cliente que agendó o quedó fuera de horario.
    """
    order = get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")

    phone = order["phone"]
    client_name = order["client_name"]
    items = order["items_summary"]
    ticket = order["ticket_code"]

    message = (
        f"👋 *Estimado(a) {client_name}:*\n\n"
        f"Le saludamos del *Área de Comercialización e Intendencia Militar*. "
        f"Nos comunicamos con respecto a su solicitud de *{items}* (Ticket: `{ticket}`).\n\n"
        f"Nos gustaría confirmar si aún se encuentra disponible para proceder con el retiro y despacho de su pedido en nuestra sede.\n\n"
        f"Quedamos atentos a su respuesta. ¡A su orden!"
    )

    # Intentar enviar vía Baileys o simular
    mark_reminder_sent(order_id)
    logger.info(f"Recordatorio enviado a {client_name} ({phone})")

    return {
        "status": "ok",
        "message": f"Recordatorio enviado exitosamente a {client_name}",
        "sent_text": message
    }

# ----------------- REST API: CONFIGURACIÓN & HORARIOS -----------------
@app.get("/api/config")
def api_get_config():
    return get_all_config()

@app.post("/api/config")
def api_update_config(cfg: ConfigSchema):
    data = {k: v for k, v in cfg.dict().items() if v is not None}
    update_config(data)
    return {"status": "ok", "message": "Configuración guardada exitosamente"}

@app.post("/api/config/toggle-maintenance")
def api_toggle_maintenance():
    current = is_maintenance_active()
    new_val = "0" if current else "1"
    update_config({"maintenance_mode": new_val})
    status_str = "ACTIVADO" if new_val == "1" else "DESACTIVADO"
    return {"status": "ok", "maintenance_mode": new_val, "message": f"Modo Mantenimiento {status_str}"}

# ----------------- REST API: EXPORTACIÓN EXCEL / CSV -----------------
@app.get("/api/export/excel")
def api_export_excel():
    df = export_orders_df()
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, sheet_name='Pedidos', index=False)
        workbook = writer.book
        worksheet = writer.sheets['Pedidos']

        # Formato de cabecera elegante
        header_format = workbook.add_format({
            'bold': True,
            'text_wrap': True,
            'valign': 'top',
            'fg_color': '#1E293B',
            'font_color': '#FFFFFF',
            'border': 1
        })
        for col_num, value in enumerate(df.columns.values):
            worksheet.write(0, col_num, value, header_format)

        for idx, col in enumerate(df.columns):
            max_len = max(df[col].astype(str).map(len).max(), len(col)) + 3
            worksheet.set_column(idx, idx, min(max_len, 45))

    output.seek(0)
    filename = f"pedidos_comercializacion_militar_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.xlsx"
    headers = {'Content-Disposition': f'attachment; filename="{filename}"'}
    return StreamingResponse(output, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers=headers)

@app.get("/api/export/csv")
def api_export_csv():
    df = export_orders_df()
    output = io.StringIO()
    df.to_csv(output, index=False, encoding='utf-8-sig')
    output.seek(0)
    filename = f"pedidos_comercializacion_militar_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.csv"
    headers = {'Content-Disposition': f'attachment; filename="{filename}"'}
    return StreamingResponse(io.BytesIO(output.getvalue().encode('utf-8-sig')), media_type='text/csv', headers=headers)

# ----------------- REST API: SIMULADOR INTERACTIVO -----------------
@app.post("/api/chat/simulate")
def api_simulate_chat(body: SimulateChatSchema):
    res = bot_manager.process_message(body.phone, body.message)
    return res

@app.post("/api/chat/reset")
def api_reset_chat(body: SimulateChatSchema):
    reset_session(body.phone)
    return {"status": "ok", "message": "Sesión reiniciada"}

# ----------------- REST API: BAILEYS QR & ESTADO -----------------
class BaileysInternalStatus(BaseModel):
    status: str
    qr: Optional[str] = None
    timestamp: Optional[int] = None
    phone: Optional[str] = None

@app.post("/api/baileys/internal-status")
def api_baileys_internal_status(body: BaileysInternalStatus):
    global baileys_state
    baileys_state["status"] = body.status
    if body.qr:
        baileys_state["qr"] = body.qr
        baileys_state["qr_timestamp"] = time.time()
    if body.phone:
        baileys_state["phone"] = body.phone
    logger.info(f"[Baileys Status Update] {body.status}")
    return {"status": "ok"}

@app.post("/api/baileys/restart")
def api_baileys_restart(clean_auth: bool = False):
    global baileys_state
    baileys_state["status"] = "RESTARTING"
    baileys_state["qr"] = None
    baileys_state["qr_timestamp"] = 0

    qr_img = BASE_DIR / "app" / "static" / "images" / "baileys_qr.png"
    if qr_img.exists():
        try:
            qr_img.unlink()
        except Exception:
            pass

    if clean_auth:
        auth_dir = BASE_DIR / "whatsapp_baileys" / "auth_info_baileys"
        if auth_dir.exists():
            try:
                shutil.rmtree(auth_dir, ignore_errors=True)
            except Exception:
                pass

    threading.Thread(target=start_baileys_process, daemon=True).start()
    return {"status": "ok", "message": "Baileys reiniciando y generando nuevo código QR"}

@app.get("/api/baileys/status")
def api_baileys_status():
    global baileys_state, baileys_process
    qr_path = BASE_DIR / "app" / "static" / "images" / "baileys_qr.png"
    has_qr_file = qr_path.exists()
    has_qr_mem = baileys_state["qr"] is not None

    now = time.time()
    qr_age = now - baileys_state.get("qr_timestamp", 0) if baileys_state.get("qr_timestamp") else 999
    is_expired = qr_age > 60

    is_connected = baileys_state["status"] == "CONNECTED"
    has_valid_qr = (has_qr_file or has_qr_mem) and not is_expired and not is_connected

    return {
        "status": baileys_state["status"],
        "connected": is_connected,
        "has_qr": has_valid_qr,
        "qr_url": f"/api/baileys/qr-image?t={int(now * 1000)}" if has_valid_qr else None,
        "phone": baileys_state.get("phone")
    }

@app.get("/api/baileys/qr-image")
def api_baileys_qr_image():
    global baileys_state
    if baileys_state.get("qr"):
        qr = qrcode.QRCode(version=1, box_size=8, border=2)
        qr.add_data(baileys_state["qr"])
        qr.make(fit=True)
        img = qr.make_image(fill_color="#0F172A", back_color="white")
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='PNG')
        img_byte_arr.seek(0)
        return StreamingResponse(
            img_byte_arr,
            media_type="image/png",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
        )

    qr_path = BASE_DIR / "app" / "static" / "images" / "baileys_qr.png"
    if qr_path.exists():
        with open(qr_path, "rb") as f:
            content = f.read()
        return Response(
            content=content,
            media_type="image/png",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
        )

    raise HTTPException(status_code=404, detail="Código QR aún no generado")

@app.get("/api/qr")
def api_qr_code(text: str = Query(default="https://wa.me/584121234567")):
    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0F172A", back_color="white")

    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)
    return StreamingResponse(img_byte_arr, media_type="image/png")
