import io
import os
import shutil
import json
import logging
from pathlib import Path
from fastapi import FastAPI, Request, Response, HTTPException, Query, UploadFile, File
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import xlsxwriter
try:
    import jinja2
except ImportError:
    jinja2 = None


from app.config import (
    BASE_DIR, DATA_DIR, PORT, HOST,
    SUPABASE_URL, SUPABASE_KEY, GEMINI_API_KEY
)
from app.database import (
    init_db, get_products, get_product_by_id,
    create_product, update_product, delete_product,
    get_orders, get_order_by_id, get_order_by_ticket, update_order, update_order_status, delete_order,
    mark_reminder_sent, export_orders_df,
    get_all_config, update_config, is_maintenance_active,
    get_inventory_movements, add_stock_batch, get_low_stock_products,
    get_available_catalog_products, get_financial_and_sales_metrics,
    get_waitlist, mark_waitlist_notified, update_waitlist_item,
    delete_waitlist_item, convert_waitlist_to_order, reset_database_to_virgin,
    get_daily_sales_report, get_order_status_history, reset_operational_data
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
    available_sizes: Optional[str] = ""
    keywords: Optional[str] = ""
    technical_specs: Optional[str] = ""
    fabric: Optional[str] = ""
    buttons_closures: Optional[str] = ""
    durability: Optional[str] = ""
    thickness_weight: Optional[str] = ""
    updated_by: Optional[str] = "ADMIN"

class OrderUpdateSchema(BaseModel):
    client_name: str
    cedula: str
    phone: str
    items_summary: str
    total_items: int = 1
    total_amount: float = 0.0
    amount_usd: Optional[float] = None
    amount_ves: Optional[float] = None
    payment_method: str = "EFECTIVO / DIVISAS"
    receipt_ref: Optional[str] = None
    receipt_bank: Optional[str] = None
    receipt_date: Optional[str] = None
    pickup_date: str
    pickup_time: str
    status: str = "PENDIENTE"
    notes: Optional[str] = ""

class StatusUpdateSchema(BaseModel):
    status: str
    changed_by: Optional[str] = "ADMIN"
    notes: Optional[str] = ""

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
    pagomovil_bank: Optional[str] = None
    pagomovil_phone: Optional[str] = None
    pagomovil_id: Optional[str] = None
    transfer_bank: Optional[str] = None
    transfer_account: Optional[str] = None
    transfer_holder: Optional[str] = None
    payment_methods_active: Optional[str] = None
    apply_iva: Optional[str] = None
    iva_rate: Optional[str] = None

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

# ----------------- FACTURACIÓN / COMPROBANTE OFICIAL DE ORDEN DE COMPRA CIT -----------------
import base64

def _generate_qr_base64(data: str) -> str:
    try:
        qr = qrcode.QRCode(version=1, box_size=4, border=1)
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#0f233a", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception as e:
        logger.warning(f"No se pudo generar QR para comprobante: {e}")
        return ""

def _render_standalone_invoice(order: Dict[str, Any]) -> str:
    template_path = BASE_DIR / "app" / "templates" / "invoice_template.html"
    if template_path.exists():
        with open(template_path, "r", encoding="utf-8") as f:
            content = f.read()
        import re
        for key, val in order.items():
            if isinstance(val, (str, int, float)):
                content = re.sub(r'\{\{\s*order\.' + re.escape(key) + r'[^}]*\}\}', str(val), content)
        content = re.sub(r'\{%[^%]*%\}', '', content)
        content = re.sub(r'\{\{[^}]*\}\}', '', content)
        return content
    return "<h1>Comprobante Oficial Complejo Industrial Tiuna C.A.</h1>"

def render_invoice_html(order_data: Dict[str, Any]) -> str:
    template_path = BASE_DIR / "app" / "templates" / "invoice_template.html"
    if jinja2 is not None and template_path.exists():
        try:
            jinja_env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(BASE_DIR / "app" / "templates")), autoescape=True)
            return jinja_env.get_template("invoice_template.html").render(order=order_data)
        except Exception as e:
            logger.error(f"Error renderizando comprobante con jinja2: {e}")
    return _render_standalone_invoice(order_data)

@app.get("/invoice/preview", response_class=HTMLResponse)
def invoice_preview(request: Request):
    config = get_all_config()
    bcv_rate = bcv_service.get_rate_for_date()
    
    mock_order = {
        "company_rif": "G-20011500-2",
        "company_address": config.get("pickup_address", "Sede de Intendencia — Fuerte Tiuna, El Valle, Caracas, D.C."),
        "company_phone": config.get("advisor_phone") or config.get("pagomovil_phone") or "0412-1234567",
        "ticket_code": "CIT-261006-001",
        "created_at": now_vet_str(),
        "pickup_date": now_vet_date_str(),
        "pickup_time": "09:30 AM",
        "status": "CONFIRMADA",
        "client_name": "CARLOS EDUARDO PÉREZ",
        "cedula": "V-18.456.123",
        "phone": "0414-1234567",
        "payment_method": "PAGO MÓVIL (BANCO DE VENEZUELA)",
        "receipt_bank": "BANCO DE VENEZUELA",
        "receipt_ref": "00984512",
        "bcv_rate_applied": bcv_rate,
        "items_summary": "1X CHAQUETA PATRIOTA TIUNA (TALLA: L) + 1X GORRA TÁCTICA PATRIOTA",
        "items_list": [
            {
                "name": "CHAQUETA PATRIOTA TIUNA (MODELO OFICIAL)",
                "size": "L",
                "qty": 1,
                "unit_price": 35.00,
                "subtotal": 35.00
            },
            {
                "name": "GORRA TÁCTICA PATRIOTA CIT",
                "size": None,
                "qty": 1,
                "unit_price": 8.00,
                "subtotal": 8.00
            }
        ],
        "total_items": 2,
        "subtotal_usd": 43.00,
        "iva_amount": 0.00,
        "amount_usd": 43.00,
        "amount_ves": round(43.00 * bcv_rate, 2),
        "qr_code_base64": _generate_qr_base64("CIT-261006-001-VALIDACION-DESPACHO")
    }
    return HTMLResponse(content=render_invoice_html(mock_order))

@app.get("/invoice/{order_id}", response_class=HTMLResponse)
def invoice_detail(order_id: int, request: Request):
    order = get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Orden no encontrada")
    
    config = get_all_config()
    bcv_rate = order.get("bcv_rate_applied") or bcv_service.get_rate_for_date()
    
    raw_items = order.get("items_detail")
    items_list = []
    if raw_items:
        try:
            if isinstance(raw_items, str):
                items_list = json.loads(raw_items)
            elif isinstance(raw_items, list):
                items_list = raw_items
        except Exception:
            items_list = []
            
    order_data = dict(order)
    order_data["company_rif"] = "G-20011500-2"
    order_data["company_address"] = config.get("pickup_address", "Sede de Intendencia — Fuerte Tiuna, El Valle, Caracas, D.C.")
    order_data["company_phone"] = config.get("advisor_phone") or config.get("pagomovil_phone") or "0412-1234567"
    order_data["items_list"] = items_list
    order_data["bcv_rate_applied"] = bcv_rate
    
    base_url = str(request.base_url).rstrip("/")
    verification_url = f"{base_url}/invoice/{order_id}"
    order_data["qr_code_base64"] = _generate_qr_base64(verification_url)
    
    return HTMLResponse(content=render_invoice_html(order_data))

@app.get("/invoice/ticket/{ticket_code}", response_class=HTMLResponse)
def invoice_by_ticket(ticket_code: str, request: Request):
    order = get_order_by_ticket(ticket_code)
    if not order:
        raise HTTPException(status_code=404, detail=f"Orden con ticket {ticket_code} no encontrada")
    return invoice_detail(order["id"], request)

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

async def _notify_payment_confirmation(order: Dict[str, Any], base_url: str) -> bool:
    phone = order.get("phone", "")
    client_name = order.get("client_name") if order.get("client_name") != "CONTACTO POR ATENDER" else "Estimado(a) Cliente"
    ticket = order.get("ticket_code", "")
    items = order.get("items_summary", "")
    usd = float(order.get("amount_usd") or order.get("total_amount") or 0.0)
    ves = float(order.get("amount_ves") or 0.0)
    ref = order.get("receipt_ref") or "Registrada"
    bank = order.get("receipt_bank") or "Verificado"
    date_pickup = format_date_dmy(order.get("pickup_date"))
    time_pickup = order.get("pickup_time") or "09:00 AM"

    invoice_link = f"{base_url}/invoice/{order['id']}"

    message = (
        f"✅ *¡PAGO CONFIRMADO CON ÉXITO!* 🎉\n\n"
        f"Estimado(a) *{client_name}*, le informamos que su pago para el pedido `{ticket}` ha sido verificado y aprobado satisfactoriamente por nuestro departamento de administración.\n\n"
        f"📦 *Artículos:* {items}\n"
        f"💵 *Monto Aprobado:* ${usd:.2f} REF *(Bs. {ves:,.2f})*\n"
        f"🏦 *Referencia:* `{ref}` | Banco: *{bank}*\n"
        f"📅 *Cita de Retiro Programada:* {date_pickup} a las {time_pickup}\n"
        f"📍 *Lugar:* Complejo Industrial Tiuna — Sede de Comercialización\n\n"
        f"📄 *Comprobante Oficial de Orden de Compra:*\n"
        f"Puede consultar o imprimir su documento con validación digital aquí:\n"
        f"{invoice_link}\n\n"
        f"¡Gracias por su compra! Le esperamos en la fecha pautada."
    )

    import re
    clean_digits = re.sub(r'\D', '', phone)
    wa_target = f"58{clean_digits[1:]}" if clean_digits.startswith("0") else (clean_digits if clean_digits.startswith("58") else f"58{clean_digits}")
    try:
        return await wa_service.send_message(to_phone=wa_target, text=message)
    except Exception as e:
        logger.warning(f"Error enviando confirmación WhatsApp a {wa_target}: {e}")
        return False

@app.put("/api/orders/{order_id}/status")
async def api_update_order_status(order_id: int, body: StatusUpdateSchema, request: Request):
    update_order_status(order_id, body.status, changed_by=body.changed_by or "ADMIN", notes=body.notes or "")
    if body.status.upper() == "CONFIRMADA":
        order = get_order_by_id(order_id)
        if order:
            base_url = str(request.base_url).rstrip("/")
            await _notify_payment_confirmation(order, base_url)
    return {"status": "ok", "message": f"Estado actualizado a {body.status.upper()}"}

@app.post("/api/orders/{order_id}/confirm-payment")
async def api_confirm_payment(order_id: int, request: Request):
    order = get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")

    update_order_status(order_id, "CONFIRMADA", changed_by="ADMIN", notes="Pago confirmado y verificado desde el panel administrativo")
    base_url = str(request.base_url).rstrip("/")
    sent_ok = await _notify_payment_confirmation(order, base_url)
    return {
        "status": "ok",
        "message": f"Pago confirmado exitosamente y notificación enviada a {order['client_name']}",
        "whatsapp_sent": sent_ok
    }

@app.get("/api/orders/{order_id}/history")
def api_get_order_history(order_id: int):
    return get_order_status_history(order_id)

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

# ----------------- REST API: REPORTE DIARIO DE VENTAS & DESGLOSE DE UNIFORMES -----------------
@app.get("/api/reports/daily")
def api_get_daily_report(date: Optional[str] = None):
    return get_daily_sales_report(date)

@app.get("/api/export/daily-report-excel")
def api_export_daily_report_excel(date: Optional[str] = None):
    report = get_daily_sales_report(date)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book

        title_fmt = workbook.add_format({
            'bold': True, 'font_size': 13, 'font_color': '#0F172A', 'align': 'left'
        })
        subtitle_fmt = workbook.add_format({
            'font_size': 9, 'font_color': '#475569', 'italic': True
        })
        header_fmt = workbook.add_format({
            'bold': True, 'fg_color': '#166534', 'font_color': '#FFFFFF',
            'border': 1, 'align': 'center', 'valign': 'vcenter'
        })
        curr_fmt = workbook.add_format({'num_format': '$#,##0.00', 'align': 'right'})
        ves_fmt = workbook.add_format({'num_format': 'Bs. #,##0.00', 'align': 'right'})
        center_fmt = workbook.add_format({'align': 'center'})

        # 1. RESUMEN DE CIERRE
        ws_resumen = workbook.add_worksheet('Cierre Diario')
        ws_resumen.write('A1', 'COMPLEJO INDUSTRIAL TIUNA — SIS-COMER', title_fmt)
        ws_resumen.write('A2', f"REPORTE OFICIAL DE CIERRE DIARIO DE VENTAS — {report['date_dmy']}", subtitle_fmt)
        ws_resumen.write('A3', f"Tasa Oficial BCV Aplicada: Bs. {report['bcv_rate']:,.2f} / $", subtitle_fmt)

        kpis = [
            ("TOTAL PEDIDOS CONCRETADOS", report['total_orders']),
            ("TOTAL UNIDADES DESPACHADAS", report['total_garments_sold']),
            ("TOTAL FACTURADO EN DIVISAS ($)", f"${report['total_usd']:,.2f} REF"),
            ("TOTAL FACTURADO EN BOLÍVARES (BS)", f"Bs. {report['total_ves']:,.2f}")
        ]
        ws_resumen.write('A5', 'CONCEPTO', header_fmt)
        ws_resumen.write('B5', 'VALOR CONSOLIDADO', header_fmt)
        for r_idx, (k, v) in enumerate(kpis, start=6):
            ws_resumen.write(f'A{r_idx}', k)
            ws_resumen.write(f'B{r_idx}', str(v), center_fmt)

        ws_resumen.write('A11', 'MÉTODO DE PAGO', header_fmt)
        ws_resumen.write('B11', 'TOTAL DIVISAS ($)', header_fmt)
        r_start = 12
        for pm, val in report['payment_methods_summary'].items():
            ws_resumen.write(f'A{r_start}', pm)
            ws_resumen.write(f'B{r_start}', val, curr_fmt)
            r_start += 1

        ws_resumen.set_column('A:A', 36)
        ws_resumen.set_column('B:B', 24)

        # 2. DESGLOSE DE UNIFORMES Y MODELOS
        ws_uniforms = workbook.add_worksheet('Desglose Uniformes')
        u_headers = ["ARTÍCULO / MODELO", "CATEGORÍA", "TALLAS VENDIDAS", "CANT. TOTAL", "TOTAL ($ REF)", "TOTAL (BS)"]
        for c_idx, h in enumerate(u_headers):
            ws_uniforms.write(0, c_idx, h, header_fmt)

        for u_idx, u in enumerate(report['uniforms_summary'], start=1):
            ws_uniforms.write(u_idx, 0, u['name'])
            ws_uniforms.write(u_idx, 1, u['category'], center_fmt)
            ws_uniforms.write(u_idx, 2, ", ".join(u['sizes_detail']))
            ws_uniforms.write(u_idx, 3, u['total_qty'], center_fmt)
            ws_uniforms.write(u_idx, 4, u['total_usd'], curr_fmt)
            ws_uniforms.write(u_idx, 5, u['total_ves'], ves_fmt)

        ws_uniforms.set_column('A:A', 32)
        ws_uniforms.set_column('B:B', 16)
        ws_uniforms.set_column('C:C', 35)
        ws_uniforms.set_column('D:D', 14)
        ws_uniforms.set_column('E:E', 18)
        ws_uniforms.set_column('F:F', 22)

        # 3. DETALLE ESPECÍFICO DE PEDIDOS
        ws_orders = workbook.add_worksheet('Detalle de Pedidos')
        o_headers = ["NRO. TICKET", "CLIENTE", "CÉDULA", "TELÉFONO", "HORA", "RESUMEN PRODUCTOS", "MÉTODO PAGO", "BANCO", "REFERENCIA", "TOTAL ($)", "TOTAL (BS)"]
        for c_idx, h in enumerate(o_headers):
            ws_orders.write(0, c_idx, h, header_fmt)

        for row_idx, o in enumerate(report['detailed_orders'], start=1):
            ws_orders.write(row_idx, 0, o['ticket_code'], center_fmt)
            ws_orders.write(row_idx, 1, o['client_name'])
            ws_orders.write(row_idx, 2, o['cedula'], center_fmt)
            ws_orders.write(row_idx, 3, o['phone'])
            ws_orders.write(row_idx, 4, o['created_time'], center_fmt)
            ws_orders.write(row_idx, 5, o['items_summary'])
            ws_orders.write(row_idx, 6, o['payment_method'])
            ws_orders.write(row_idx, 7, o['receipt_bank'])
            ws_orders.write(row_idx, 8, o['receipt_ref'], center_fmt)
            ws_orders.write(row_idx, 9, o['amount_usd'], curr_fmt)
            ws_orders.write(row_idx, 10, o['amount_ves'], ves_fmt)

        ws_orders.set_column('A:A', 16)
        ws_orders.set_column('B:B', 24)
        ws_orders.set_column('C:C', 14)
        ws_orders.set_column('D:D', 16)
        ws_orders.set_column('E:E', 10)
        ws_orders.set_column('F:F', 40)
        ws_orders.set_column('G:G', 18)
        ws_orders.set_column('H:H', 18)
        ws_orders.set_column('I:I', 16)
        ws_orders.set_column('J:J', 14)
        ws_orders.set_column('K:K', 18)

    output.seek(0)
    filename = f"cierre_ventas_{report['date']}.xlsx"
    headers = {'Content-Disposition': f'attachment; filename="{filename}"'}
    return StreamingResponse(output, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers=headers)

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

# ----------------- REST API: RESET SISTEMA VIRGEN & DATOS OPERATIVOS -----------------
@app.post("/api/admin/reset-operational-data")
def api_reset_operational_data():
    """
    Borrón y cuenta nueva de datos operativos:
    Elimina pedidos, historial kardex, clientes y lista de espera.
    CONSERVA intactos el Catálogo Militar de productos, tasas BCV y configuraciones.
    """
    res = reset_operational_data(preserve_catalog=True)
    return res

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

# ----------------- REST API: CIERRE DIARIO & REPORTES -----------------
def generate_daily_report_excel_bytes(report: dict) -> bytes:
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {'in_memory': True})
    
    # Estilos ejecutivos Tiuna
    header_fmt = workbook.add_format({
        'bold': True,
        'bg_color': '#166534',
        'font_color': '#FFFFFF',
        'border': 1,
        'align': 'center',
        'valign': 'vcenter',
        'font_name': 'Segoe UI',
        'font_size': 11
    })
    title_fmt = workbook.add_format({
        'bold': True,
        'font_size': 15,
        'font_color': '#0F172A',
        'font_name': 'Segoe UI'
    })
    subtitle_fmt = workbook.add_format({
        'italic': True,
        'font_size': 10,
        'font_color': '#64748B',
        'font_name': 'Segoe UI'
    })
    kpi_label_fmt = workbook.add_format({
        'bold': True,
        'font_color': '#334155',
        'bg_color': '#F1F5F9',
        'border': 1,
        'font_name': 'Segoe UI',
        'valign': 'vcenter'
    })
    kpi_val_usd_fmt = workbook.add_format({
        'bold': True,
        'font_color': '#166534',
        'bg_color': '#FFFFFF',
        'border': 1,
        'num_format': '$#,##0.00 "REF"',
        'font_name': 'Segoe UI',
        'valign': 'vcenter'
    })
    kpi_val_ves_fmt = workbook.add_format({
        'bold': True,
        'font_color': '#0369A1',
        'bg_color': '#FFFFFF',
        'border': 1,
        'num_format': '"Bs." #,##0.00',
        'font_name': 'Segoe UI',
        'valign': 'vcenter'
    })
    kpi_val_num_fmt = workbook.add_format({
        'bold': True,
        'font_color': '#0F172A',
        'bg_color': '#FFFFFF',
        'border': 1,
        'num_format': '#,##0',
        'align': 'center',
        'font_name': 'Segoe UI',
        'valign': 'vcenter'
    })
    data_fmt = workbook.add_format({
        'font_name': 'Segoe UI',
        'font_size': 10,
        'border': 1,
        'valign': 'vcenter'
    })
    data_center_fmt = workbook.add_format({
        'font_name': 'Segoe UI',
        'font_size': 10,
        'border': 1,
        'align': 'center',
        'valign': 'vcenter'
    })
    usd_cell_fmt = workbook.add_format({
        'font_name': 'Segoe UI',
        'font_size': 10,
        'border': 1,
        'num_format': '$#,##0.00',
        'valign': 'vcenter'
    })
    ves_cell_fmt = workbook.add_format({
        'font_name': 'Segoe UI',
        'font_size': 10,
        'border': 1,
        'num_format': '"Bs." #,##0.00',
        'valign': 'vcenter'
    })
    total_row_fmt = workbook.add_format({
        'bold': True,
        'bg_color': '#E2E8F0',
        'border': 1,
        'font_name': 'Segoe UI',
        'valign': 'vcenter'
    })
    total_row_usd_fmt = workbook.add_format({
        'bold': True,
        'bg_color': '#E2E8F0',
        'border': 1,
        'num_format': '$#,##0.00',
        'font_name': 'Segoe UI',
        'valign': 'vcenter'
    })
    total_row_ves_fmt = workbook.add_format({
        'bold': True,
        'bg_color': '#E2E8F0',
        'border': 1,
        'num_format': '"Bs." #,##0.00',
        'font_name': 'Segoe UI',
        'valign': 'vcenter'
    })
    
    # PESTAÑA 1: Cierre Diario
    ws1 = workbook.add_worksheet('Cierre Diario')
    ws1.set_tab_color('#166534')
    ws1.set_column('A:A', 34)
    ws1.set_column('B:B', 26)
    ws1.set_column('C:C', 26)

    ws1.write('A1', 'COMPLEJO INDUSTRIAL TIUNA — SIS-COMER', title_fmt)
    ws1.write('A2', f'CIERRE DE CAJA & REPORTE DIARIO DE VENTAS | Fecha: {report["date_dmy"]}', subtitle_fmt)

    ws1.write('A4', 'INDICADOR COMERCIAL / KPI', header_fmt)
    ws1.write('B4', 'VALOR DIVISAS / CANTIDAD', header_fmt)
    ws1.write('C4', 'VALOR BOLÍVARES (BCV)', header_fmt)

    ws1.write('A5', 'Total Facturado en Ventas', kpi_label_fmt)
    ws1.write_number('B5', report.get('total_usd', 0.0), kpi_val_usd_fmt)
    ws1.write_number('C5', report.get('total_ves', 0.0), kpi_val_ves_fmt)

    ws1.write('A6', 'Total Solicitudes Concretadas', kpi_label_fmt)
    ws1.write_number('B6', report.get('total_orders', 0), kpi_val_num_fmt)
    ws1.write('C6', '-', data_center_fmt)

    ws1.write('A7', 'Total Prendas y Uniformes Vendidos', kpi_label_fmt)
    ws1.write_number('B7', report.get('total_garments_sold', 0), kpi_val_num_fmt)
    ws1.write('C7', '-', data_center_fmt)

    ws1.write('A8', 'Tasa Oficial BCV Aplicada', kpi_label_fmt)
    ws1.write('B8', f"Bs. {report.get('bcv_rate', 0.0):,.2f} / $", data_center_fmt)
    ws1.write('C8', '-', data_center_fmt)

    ws1.write('A10', 'FORMA DE PAGO', header_fmt)
    ws1.write('B10', 'TOTAL FACTURADO ($ REF)', header_fmt)
    ws1.write('C10', 'TOTAL FACTURADO (BS)', header_fmt)

    row = 10
    pm_summary = report.get('payment_methods_summary', {})
    bcv_val = float(report.get('bcv_rate', 1.0))
    if not pm_summary:
        ws1.write(row, 0, 'Sin operaciones registradas', data_fmt)
        ws1.write_number(row, 1, 0, usd_cell_fmt)
        ws1.write_number(row, 2, 0, ves_cell_fmt)
        row += 1
    else:
        for pm, amt in pm_summary.items():
            ws1.write(row, 0, pm, data_fmt)
            ws1.write_number(row, 1, amt, usd_cell_fmt)
            ws1.write_number(row, 2, amt * bcv_val, ves_cell_fmt)
            row += 1

    # PESTAÑA 2: Desglose de Uniformes y Artículos
    ws2 = workbook.add_worksheet('Desglose Uniformes')
    ws2.set_tab_color('#0284C7')
    headers2 = ['Producto / Modelo', 'Categoría', 'Tallas Vendidas Desglosadas', 'Total Uds', 'Facturado ($ REF)', 'Facturado (Bs.)']
    widths2 = [36, 16, 45, 14, 18, 20]
    for col_idx, (h, w) in enumerate(zip(headers2, widths2)):
        ws2.set_column(col_idx, col_idx, w)
        ws2.write(0, col_idx, h, header_fmt)

    r2 = 1
    for u in report.get('uniforms_summary', []):
        sizes_str = ', '.join(u.get('sizes_detail', [])) if u.get('sizes_detail') else 'Talla Única'
        ws2.write(r2, 0, u.get('name', ''), data_fmt)
        ws2.write(r2, 1, u.get('category', 'UNIFORME'), data_center_fmt)
        ws2.write(r2, 2, sizes_str, data_fmt)
        ws2.write_number(r2, 3, u.get('total_qty', 0), data_center_fmt)
        ws2.write_number(r2, 4, u.get('total_usd', 0.0), usd_cell_fmt)
        ws2.write_number(r2, 5, u.get('total_ves', 0.0), ves_cell_fmt)
        r2 += 1

    if r2 > 1:
        ws2.write(r2, 0, 'TOTAL CONSOLIDADO', total_row_fmt)
        ws2.write(r2, 1, '', total_row_fmt)
        ws2.write(r2, 2, '', total_row_fmt)
        ws2.write_number(r2, 3, report.get('total_garments_sold', 0), total_row_fmt)
        ws2.write_number(r2, 4, report.get('total_usd', 0.0), total_row_usd_fmt)
        ws2.write_number(r2, 5, report.get('total_ves', 0.0), total_row_ves_fmt)

    # PESTAÑA 3: Detalle de Pedidos
    ws3 = workbook.add_worksheet('Detalle de Pedidos')
    ws3.set_tab_color('#D97706')
    headers3 = ['Ticket', 'Hora', 'Cliente', 'Cédula', 'Teléfono', 'Artículos y Tallas', 'Banco', 'Nro Referencia', 'Monto ($ REF)', 'Monto (Bs.)', 'Estatus']
    widths3 = [16, 10, 26, 14, 16, 45, 18, 18, 15, 18, 16]
    for col_idx, (h, w) in enumerate(zip(headers3, widths3)):
        ws3.set_column(col_idx, col_idx, w)
        ws3.write(0, col_idx, h, header_fmt)

    r3 = 1
    for o in report.get('detailed_orders', []):
        ws3.write(r3, 0, o.get('ticket_code', ''), data_center_fmt)
        ws3.write(r3, 1, o.get('created_time') or '-', data_center_fmt)
        ws3.write(r3, 2, o.get('client_name', ''), data_fmt)
        ws3.write(r3, 3, o.get('cedula', ''), data_center_fmt)
        ws3.write(r3, 4, o.get('phone', ''), data_center_fmt)
        ws3.write(r3, 5, o.get('items_summary', ''), data_fmt)
        ws3.write(r3, 6, o.get('receipt_bank') or 'N/A', data_center_fmt)
        ws3.write(r3, 7, o.get('receipt_ref') or 'N/A', data_center_fmt)
        ws3.write_number(r3, 8, float(o.get('amount_usd', 0.0)), usd_cell_fmt)
        ws3.write_number(r3, 9, float(o.get('amount_ves', 0.0)), ves_cell_fmt)
        ws3.write(r3, 10, o.get('status', ''), data_center_fmt)
        r3 += 1

    workbook.close()
    output.seek(0)
    return output.getvalue()


@app.get("/api/reports/daily")
def api_get_daily_report(date: Optional[str] = Query(default=None)):
    """
    Retorna el informe detallado de cierre diario de ventas,
    desglose por modelo/talla y tabla específica de pedidos.
    """
    try:
        report = get_daily_sales_report(date)
        return report
    except Exception as e:
        logger.error(f"Error generando reporte diario de ventas: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/export/daily-report-excel")
def api_export_daily_report_excel(date: Optional[str] = Query(default=None)):
    """
    Genera y descarga el archivo Excel multicapa oficial del Cierre Diario de SIS-COMER.
    """
    try:
        report = get_daily_sales_report(date)
        excel_bytes = generate_daily_report_excel_bytes(report)
        filename = f"Cierre_Diario_SISCOMER_{report['date_dmy'].replace('/', '-')}.xlsx"
        return Response(
            content=excel_bytes,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        logger.error(f"Error exportando Excel de cierre diario: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/export/excel")
def api_export_orders_excel():
    """Descarga general de pedidos en Excel"""
    try:
        df = export_orders_df()
        out = io.BytesIO()
        with pd.ExcelWriter(out, engine="xlsxwriter") as writer:
            df.to_excel(writer, index=False, sheet_name="Pedidos")
        out.seek(0)
        filename = f"Pedidos_SISCOMER_{now_vet_date_str()}.xlsx"
        return Response(
            content=out.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        logger.error(f"Error exportando pedidos a Excel: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/export/csv")
def api_export_orders_csv():
    """Descarga general de pedidos en CSV"""
    try:
        df = export_orders_df()
        csv_data = df.to_csv(index=False, encoding="utf-8-sig")
        filename = f"Pedidos_SISCOMER_{now_vet_date_str()}.csv"
        return Response(
            content=csv_data.encode("utf-8-sig"),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        logger.error(f"Error exportando pedidos a CSV: {e}")
        raise HTTPException(status_code=500, detail=str(e))

