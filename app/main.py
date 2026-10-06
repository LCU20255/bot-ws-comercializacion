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
    get_all_config, update_config, is_maintenance_active,
    get_inventory_movements, add_stock_batch, get_low_stock_products,
    get_available_catalog_products, get_financial_and_sales_metrics,
    get_waitlist, mark_waitlist_notified, update_waitlist_item,
    delete_waitlist_item, convert_waitlist_to_order, reset_database_to_virgin
)
from app.time_utils import now_vet, now_vet_str, now_vet_date_str
from app.bot_flow import bot_manager, reset_session
from app.bcv_service import bcv_service
from app.whatsapp_service import notify_waitlist_stock_available, wa_service
import qrcode
import pandas as pd
import base64
import uuid

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
            encoding="utf-8",
            errors="replace",
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
    message: Optional[str] = ""
    image_base64: Optional[str] = None
    jid: Optional[str] = None

class StockBatchSchema(BaseModel):
    product_id: int
    quantity: int
    notes: Optional[str] = ""
    created_by: Optional[str] = "TALLER_CONFECCION"

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
    if prod.stock and prod.stock > 0:
        notify_waitlist_stock_available(pid, prod.name)
    return {"status": "ok", "id": pid, "message": "Producto militar registrado con éxito"}

@app.put("/api/products/{product_id}")
def api_update_product(product_id: int, prod: ProductSchema):
    update_product(product_id, prod.dict(), updated_by=prod.updated_by or "ADMIN")
    if prod.stock and prod.stock > 0:
        notify_waitlist_stock_available(product_id, prod.name)
    return {"status": "ok", "message": "Producto militar actualizado con éxito"}

@app.delete("/api/products/{product_id}")
def api_delete_product(product_id: int):
    delete_product(product_id)
    return {"status": "ok", "message": "Producto eliminado con éxito"}

@app.post("/api/upload-image")
async def api_upload_image(file: UploadFile = File(...)):
    images_dir = BASE_DIR / "app" / "static" / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    timestamp = int(now_vet().timestamp())
    raw_stem = Path(file.filename or "upload").stem
    safe_stem = "".join([c if c.isalnum() or c in ("-", "_") else "_" for c in raw_stem])[:40]

    try:
        from PIL import Image, ImageOps
        image = Image.open(file.file)

        # Corregir orientación basada en EXIF (fotos tomadas desde teléfonos móviles)
        try:
            image = ImageOps.exif_transpose(image)
        except Exception:
            pass

        # Determinar formato óptimo manteniendo transparencias si existen
        target_format = "JPEG"
        ext = "jpg"
        if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
            target_format = "PNG"
            ext = "png"
        elif image.mode != "RGB":
            image = image.convert("RGB")

        # Redimensionar proporcionalmente a una resolución estándar máxima de 800x800
        # Esto adapta cualquier imagen sin romper la interfaz ni las proporciones
        image.thumbnail((800, 800), Image.Resampling.LANCZOS)

        safe_filename = f"prod_{timestamp}_{safe_stem}.{ext}"
        file_path = images_dir / safe_filename

        if target_format == "JPEG":
            image.save(file_path, format="JPEG", quality=85, optimize=True)
        else:
            image.save(file_path, format="PNG", optimize=True)

        return {"status": "ok", "url": f"/static/images/{safe_filename}"}
    except Exception as e:
        logger.error(f"Error optimizando imagen con Pillow: {e}")
        file.file.seek(0)
        fallback_name = f"prod_{timestamp}_{safe_stem}_{uuid.uuid4().hex[:6]}.jpg"
        file_path = images_dir / fallback_name
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        return {"status": "ok", "url": f"/static/images/{fallback_name}"}

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
        "Le escribimos de *Complejo Industrial Tiuna — Equipo de Comercialización*. "
        "Nos comunicamos para retomar su consulta y atención tras haber finalizado nuestro periodo de mantenimiento.\n\n"
        "¿En qué artículos o confección podemos asistirle el día de hoy?\n\n"
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
        f"Le saludamos de *Complejo Industrial Tiuna — Equipo de Comercialización*. "
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
    filename = f"pedidos_sis_comer_{now_vet().strftime('%Y%m%d_%H%M')}.xlsx"
    headers = {'Content-Disposition': f'attachment; filename="{filename}"'}
    return StreamingResponse(output, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers=headers)

@app.get("/api/export/csv")
def api_export_csv():
    df = export_orders_df()
    output = io.StringIO()
    df.to_csv(output, index=False, encoding='utf-8-sig')
    output.seek(0)
    filename = f"pedidos_sis_comer_{now_vet().strftime('%Y%m%d_%H%M')}.csv"
    headers = {'Content-Disposition': f'attachment; filename="{filename}"'}
    return StreamingResponse(io.BytesIO(output.getvalue().encode('utf-8-sig')), media_type='text/csv', headers=headers)

# ----------------- REST API: SIMULADOR & OCR COMPROBANTES -----------------
@app.post("/api/chat/simulate")
def api_simulate_chat(body: SimulateChatSchema):
    # Si viene con imagen de comprobante adjunta
    if body.image_base64:
        temp_img_name = f"temp_rcpt_{uuid.uuid4().hex[:8]}.jpg"
        temp_img_path = DATA_DIR / temp_img_name
        try:
            with open(temp_img_path, "wb") as f:
                f.write(base64.b64decode(body.image_base64))
            # process_receipt_image procesa el OCR y destruye inmediatamente el archivo temporal
            res = bot_manager.process_receipt_image(body.phone, str(temp_img_path), caption=body.message or "")
            return res
        except Exception as e:
            logger.error(f"Error decodificando comprobante de pago: {e}")
            if temp_img_path.exists():
                try:
                    temp_img_path.unlink()
                except Exception:
                    pass
            return {"reply": "⚠️ Ocurrió un error al procesar la imagen del comprobante. Por favor envíe el número de referencia y banco por texto.", "state": "AWAITING_PAYMENT"}

    # Mensaje de texto normal
    res = bot_manager.process_message(body.phone, body.message or "")
    return res

@app.post("/api/chat/process-receipt")
async def api_process_receipt(
    phone: str = Query(...),
    caption: Optional[str] = Query(default=""),
    file: UploadFile = File(...)
):
    """Sube una imagen de comprobante bancario vía multipart, extrae datos por OCR y purga el archivo del disco"""
    temp_img_name = f"upload_rcpt_{uuid.uuid4().hex[:8]}.jpg"
    temp_img_path = DATA_DIR / temp_img_name
    with open(temp_img_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    # Procesa y destruye el archivo de inmediato
    res = bot_manager.process_receipt_image(phone, str(temp_img_path), caption=caption or "")
    return res

@app.post("/api/chat/reset")
def api_reset_chat(body: SimulateChatSchema):
    reset_session(body.phone, keep_registration=False)
    return {"status": "ok", "message": "Sesión reiniciada"}

# ----------------- REST API: MÉTRICAS FINANCIERAS & BCV -----------------
@app.get("/api/metrics/financial")
def api_get_financial_metrics():
    """Ventas totales y de hoy en $ y Bs, conteo de stock bajo, top productos y estados"""
    return get_financial_and_sales_metrics()

@app.get("/api/bcv/today")
def api_get_bcv_today():
    """Obtiene la tasa oficial BCV del día en curso"""
    rate = bcv_service.get_rate_for_date()
    return {"rate": rate, "currency": "VES/USD", "status": "ok"}

# ----------------- REST API: INVENTARIO & KARDEX MILITAR -----------------
@app.get("/api/inventory/movements")
def api_get_inventory_movements(limit: int = 100):
    return get_inventory_movements(limit=limit)

@app.get("/api/inventory/low-stock")
def api_get_low_stock():
    return get_low_stock_products(threshold=20)

@app.post("/api/inventory/batch-add")
def api_add_stock_batch(body: StockBatchSchema):
    try:
        res = add_stock_batch(
            product_id=body.product_id,
            quantity=body.quantity,
            notes=body.notes or "",
            created_by=body.created_by or "ALMACEN_TIUNA"
        )
        
        # Notificar automáticamente a clientes en lista de espera si hay solicitudes pendientes
        prod = get_product_by_id(body.product_id)
        notified_count = 0
        if prod:
            notified_count = notify_waitlist_stock_available(body.product_id, prod["name"])
            if notified_count > 0:
                logger.info(f"Se notificaron automáticamente {notified_count} cliente(s) en espera de {prod['name']}")
                
        return {
            "status": "ok", 
            "message": f"Se ingresaron {body.quantity} unidades al inventario", 
            "notified_waitlist": notified_count,
            "data": res
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ----------------- REST API: LISTA DE ESPERA (WAITLIST) -----------------
class WaitlistUpdateSchema(BaseModel):
    status: str
    client_name: Optional[str] = None
    phone: Optional[str] = None
    notes: Optional[str] = None

class WaitlistConvertSchema(BaseModel):
    client_name: Optional[str] = None
    phone: Optional[str] = None
    cedula: Optional[str] = None
    qty: Optional[int] = 1
    total_amount: Optional[float] = 0.0
    amount_ves: Optional[float] = 0.0
    bcv_rate_applied: Optional[float] = 0.0
    payment_method: Optional[str] = "EFECTIVO / DIVISAS"
    pickup_date: Optional[str] = None
    pickup_time: Optional[str] = "09:00 AM"
    status: Optional[str] = "PENDIENTE POR ATENCIÓN"

@app.get("/api/waitlist")
def api_get_waitlist(status: Optional[str] = None):
    return get_waitlist(status=status)

@app.put("/api/waitlist/{waitlist_id}")
def api_update_waitlist(waitlist_id: int, body: WaitlistUpdateSchema):
    update_waitlist_item(
        waitlist_id=waitlist_id,
        status=body.status,
        notes=body.notes,
        client_name=body.client_name,
        phone=body.phone
    )
    return {"status": "ok", "message": f"Lista de espera #{waitlist_id} actualizada a {body.status.upper()}"}

@app.delete("/api/waitlist/{waitlist_id}")
def api_delete_waitlist(waitlist_id: int):
    delete_waitlist_item(waitlist_id)
    return {"status": "ok", "message": f"Registro #{waitlist_id} eliminado de la lista de espera"}

@app.post("/api/waitlist/{waitlist_id}/convert-to-order")
def api_convert_waitlist(waitlist_id: int, body: WaitlistConvertSchema):
    try:
        new_order = convert_waitlist_to_order(waitlist_id, body.dict())
        return {
            "status": "ok",
            "message": f"Cliente convertido exitosamente en Pedido {new_order['ticket_code']}",
            "order": new_order
        }
    except Exception as e:
        logger.error(f"Error convirtiendo lista de espera #{waitlist_id} a pedido: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/waitlist/{waitlist_id}/notify")
def api_notify_single_waitlist(waitlist_id: int):
    items = get_waitlist()
    target = next((item for item in items if item["id"] == waitlist_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Solicitud de espera no encontrada")
    
    prod_name = target["product_name"]
    prod_id = target.get("product_id")
    count = notify_waitlist_stock_available(prod_id or 0, prod_name)
    if count == 0 and target["status"] == "PENDIENTE":
        # Forzar notificación individual directa con hora legal de Venezuela
        now_hour = now_vet().hour
        greeting = "Buenas tardes" if 12 <= now_hour < 19 else ("Buenos días" if now_hour < 12 else "Buenas noches")
        msg = (
            f"👋 ¡Hola, {target.get('client_name') or 'Cliente'}! {greeting}.\n\n"
            "Nos estamos comunicando de *Complejo Industrial Tiuna — Equipo de Comercialización*.\n\n"
            f"📦 Le informamos que el producto *{prod_name}* ya se encuentra *DISPONIBLE* en nuestro inventario.\n\n"
            "Puede responder a este mensaje para coordinar su solicitud. ¡Estamos a su entera orden!"
        )
        wa_service.send_message_sync(target["phone"], msg)
        mark_waitlist_notified(waitlist_id)
    return {"status": "ok", "message": f"Notificación enviada al cliente {target['phone']}"}

# ----------------- REST API: RESET SISTEMA VIRGEN -----------------
@app.post("/api/system/reset-virgin")
def api_reset_virgin():
    reset_database_to_virgin()
    return {"status": "ok", "message": "Sistema y bases de datos reseteados a estado 100% virgen"}

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
