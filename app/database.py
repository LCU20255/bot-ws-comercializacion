import sqlite3
import json
import logging
from datetime import datetime, time
from pathlib import Path
from typing import List, Dict, Any, Optional
import pandas as pd
from app.config import DATA_DIR, SUPABASE_URL, SUPABASE_KEY
from app.time_utils import now_vet, now_vet_str, now_vet_date_str, now_vet_time_str

logger = logging.getLogger(__name__)
DB_FILE = DATA_DIR / "commercial_bot.db"

def get_connection():
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # 1. Tabla: products (Productos con auditoría)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        slug TEXT UNIQUE,
        description TEXT,
        price REAL NOT NULL DEFAULT 0.0,
        price_display TEXT,
        category TEXT DEFAULT 'MILITAR',
        image_url TEXT,
        stock INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 1,
        requires_size INTEGER DEFAULT 0,
        keywords TEXT,
        updated_at TIMESTAMP DEFAULT (datetime('now', '-4 hours')),
        updated_by TEXT DEFAULT 'ADMIN'
    );
    """)

    # Migraciones seguras para products
    for col, col_type in [("requires_size", "INTEGER DEFAULT 0"), ("min_stock_alert", "INTEGER DEFAULT 20")]:
        try:
            cursor.execute(f"ALTER TABLE products ADD COLUMN {col} {col_type}")
            conn.commit()
        except Exception:
            pass

    # 2. Tabla: clients (Clientes parametrizados en MAYÚSCULAS)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS clients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        cedula TEXT UNIQUE NOT NULL,
        phone TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT (datetime('now', '-4 hours'))
    );
    """)

    # 3. Tabla: orders (Pedidos / Citas con desglose multiproducto, pagos y mayúsculas)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_code TEXT UNIQUE NOT NULL,
        client_name TEXT NOT NULL,
        cedula TEXT NOT NULL,
        phone TEXT NOT NULL,
        items_summary TEXT NOT NULL,       -- ej: "3X PARCHE MILITAR + 1X GORRA TÁCTICA"
        items_detail TEXT NOT NULL,        -- JSON con lista detallada [{name, qty, unit_price, subtotal}]
        total_items INTEGER NOT NULL DEFAULT 1,
        total_amount REAL NOT NULL DEFAULT 0.0,
        amount_usd REAL NOT NULL DEFAULT 0.0,
        amount_ves REAL NOT NULL DEFAULT 0.0,
        bcv_rate_applied REAL NOT NULL DEFAULT 0.0,
        bcv_rate_date TEXT,
        payment_method TEXT NOT NULL,      -- EFECTIVO / DIVISAS, TRANSFERENCIA, PAGO MÓVIL
        receipt_ref TEXT,
        receipt_bank TEXT,
        receipt_date TEXT,
        ocr_raw_text TEXT,
        pickup_date TEXT NOT NULL,         -- YYYY-MM-DD
        pickup_time TEXT NOT NULL,         -- 09:00 AM
        status TEXT DEFAULT 'PENDIENTE POR ATENCIÓN',   -- PENDIENTE POR ATENCIÓN, CONFIRMADA, POR RETIRAR, RETIRADA, CANCELADA
        is_off_hours INTEGER DEFAULT 0,    -- 1 si fue realizado fuera de horario laboral
        reminder_sent INTEGER DEFAULT 0,   -- 1 si ya se le envió recordatorio
        notes TEXT,
        created_at TIMESTAMP DEFAULT (datetime('now', '-4 hours')),
        synced_to_supabase INTEGER DEFAULT 0
    );
    """)

    # Migraciones seguras para orders existentes
    order_cols = [
        ("amount_usd", "REAL DEFAULT 0.0"),
        ("amount_ves", "REAL DEFAULT 0.0"),
        ("bcv_rate_applied", "REAL DEFAULT 0.0"),
        ("bcv_rate_date", "TEXT"),
        ("receipt_ref", "TEXT"),
        ("receipt_bank", "TEXT"),
        ("receipt_date", "TEXT"),
        ("ocr_raw_text", "TEXT")
    ]
    for col_name, col_type in order_cols:
        try:
            cursor.execute(f"ALTER TABLE orders ADD COLUMN {col_name} {col_type}")
            conn.commit()
        except Exception:
            pass

    # 4. Tabla: inventory_movements (Kardex Histórico Militar)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS inventory_movements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        movement_type TEXT NOT NULL,       -- ENTRADA_TALLER, SALIDA_VENTA, AJUSTE
        quantity INTEGER NOT NULL,
        previous_stock INTEGER NOT NULL,
        new_stock INTEGER NOT NULL,
        order_id INTEGER,
        client_name TEXT,
        created_by TEXT DEFAULT 'SISTEMA',
        notes TEXT,
        created_at TIMESTAMP DEFAULT (datetime('now', '-4 hours')),
        FOREIGN KEY(product_id) REFERENCES products(id)
    );
    """)

    # 5. Tabla: bcv_rates (Histórico Oficial de Tasas BCV por Fecha)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bcv_rates (
        rate_date TEXT PRIMARY KEY,
        rate REAL NOT NULL,
        updated_at TEXT NOT NULL
    );
    """)

    # 6. Tabla: system_config (Mantenimiento, Horarios y Mensajes)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS system_config (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """)

    # 7. Tabla: product_waitlist (Lista de Espera cuando no hay stock)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS product_waitlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phone TEXT NOT NULL,
        client_name TEXT,
        product_id INTEGER,
        product_name TEXT NOT NULL,
        status TEXT DEFAULT 'PENDIENTE', -- PENDIENTE, EN CONTACTO, NOTIFICADO, CANCELADO, CONVERTIDO EN PEDIDO
        notes TEXT,
        created_at TIMESTAMP DEFAULT (datetime('now', '-4 hours')),
        notified_at TEXT,
        FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE SET NULL
    );
    """)

    for col, col_type in [("notes", "TEXT")]:
        try:
            cursor.execute(f"ALTER TABLE product_waitlist ADD COLUMN {col} {col_type}")
            conn.commit()
        except Exception:
            pass

    conn.commit()

    # NOTA: Sistema 100% virgen: No se precargan productos iniciales automáticos.
    # El usuario administrará el catálogo desde el panel administrativo.

    # Configuraciones de Sistema por defecto
    default_config = {
        "bot_name": "SIS-COMER - Equipo de Comercialización",
        "maintenance_mode": "0",  # 0 = Activo normal, 1 = Modo mantenimiento activado
        "maintenance_message": "¡Hola! En este momento nos encontramos en proceso de mantenimiento. Por favor comunícate con nosotros el día de mañana de 8:00 AM a 5:00 PM.",
        "business_hours_start": "08:00",
        "business_hours_end": "17:00",
        "off_hours_message": "Hola. En este momento nos encontramos fuera de nuestro horario laboral (Lunes a Viernes de 8:00 AM a 5:00 PM). Sin embargo, tu solicitud quedará guardada en el sistema para ser atendida a primera hora hábil.",
        "advisor_phone": "+584121234567",
        "advisor_name": "ASESOR COMERCIAL - COMPLEJO INDUSTRIAL TIUNA",
        "pickup_address": "SEDE PRINCIPAL - COMPLEJO INDUSTRIAL TIUNA",
        "pickup_hours": "LUNES A VIERNES DE 8:00 AM A 5:00 PM"
    }
    for k, v in default_config.items():
        cursor.execute("INSERT OR IGNORE INTO system_config (key, value) VALUES (?, ?)", (k, v))
        
    # Sanitizar textos viejos que puedan haber quedado guardados
    cursor.execute("""
        UPDATE system_config 
        SET value = 'SEDE PRINCIPAL - COMPLEJO INDUSTRIAL TIUNA' 
        WHERE key = 'pickup_address' AND (value LIKE '%INTENDENCIA MILITAR%' OR value LIKE '%COMERCIALIZACIÓN E INTENDENCIA%')
    """)
    cursor.execute("""
        UPDATE system_config 
        SET value = 'ASESOR COMERCIAL - COMPLEJO INDUSTRIAL TIUNA' 
        WHERE key = 'advisor_name' AND value LIKE '%MILITAR%'
    """)
    cursor.execute("""
        UPDATE system_config 
        SET value = 'SIS-COMER - Equipo de Comercialización' 
        WHERE key = 'bot_name' AND value LIKE '%TEXTIL MILITAR%'
    """)
    conn.commit()

    conn.close()

# ----------------- PRODUCT CRUD -----------------
def get_products(only_active=True) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    if only_active:
        cursor.execute("SELECT * FROM products WHERE is_active = 1 ORDER BY id ASC")
    else:
        cursor.execute("SELECT * FROM products ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_product_by_id(product_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def create_product(data: Dict[str, Any], updated_by: str = "ADMIN") -> int:
    conn = get_connection()
    cursor = conn.cursor()
    name = str(data["name"]).strip().upper()
    slug = name.lower().replace(" ", "-")
    price = float(data.get("price", 0.0))
    price_display = data.get("price_display") or f"${price:.2f} Ref"
    category = str(data.get("category", "MILITAR")).strip().upper()
    requires_size = int(data.get("requires_size", 0))
    current_time_vet = now_vet_str()

    cursor.execute("""
        INSERT INTO products (name, slug, description, price, price_display, category, image_url, stock, is_active, requires_size, keywords, updated_at, updated_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        name,
        slug,
        data.get("description", ""),
        price,
        price_display,
        category,
        data.get("image_url", "/static/images/placeholder.png"),
        int(data.get("stock", 0)),
        int(data.get("is_active", 1)),
        requires_size,
        data.get("keywords", "").lower(),
        current_time_vet,
        updated_by.upper()
    ))
    conn.commit()
    pid = cursor.lastrowid
    conn.close()
    return pid

def update_product(product_id: int, data: Dict[str, Any], updated_by: str = "ADMIN") -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    name = str(data["name"]).strip().upper()
    price = float(data.get("price", 0.0))
    price_display = data.get("price_display") or f"${price:.2f} Ref"
    category = str(data.get("category", "MILITAR")).strip().upper()
    requires_size = int(data.get("requires_size", 0))
    current_time_vet = now_vet_str()

    cursor.execute("""
        UPDATE products
        SET name = ?, description = ?, price = ?, price_display = ?, category = ?, image_url = ?, stock = ?, is_active = ?, requires_size = ?, keywords = ?, updated_at = ?, updated_by = ?
        WHERE id = ?
    """, (
        name,
        data.get("description", ""),
        price,
        price_display,
        category,
        data.get("image_url", ""),
        int(data.get("stock", 0)),
        int(data.get("is_active", 1)),
        requires_size,
        data.get("keywords", "").lower(),
        current_time_vet,
        updated_by.upper(),
        product_id
    ))
    conn.commit()
    conn.close()
    return True

def delete_product(product_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
    conn.commit()
    conn.close()
    return True

# ----------------- CLIENT CRUD -----------------
def upsert_client(name: str, cedula: str, phone: str) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    clean_name = str(name).strip().upper()
    clean_cedula = str(cedula).strip().upper()
    clean_phone = str(phone).strip()

    cursor.execute("SELECT id FROM clients WHERE cedula = ?", (clean_cedula,))
    row = cursor.fetchone()
    if row:
        cursor.execute("UPDATE clients SET name = ?, phone = ? WHERE id = ?", (clean_name, clean_phone, row["id"]))
        client_id = row["id"]
    else:
        cursor.execute("INSERT INTO clients (name, cedula, phone, created_at) VALUES (?, ?, ?, ?)", (clean_name, clean_cedula, clean_phone, now_vet_str()))
        client_id = cursor.lastrowid

    conn.commit()
    conn.close()
    return client_id

# ----------------- ORDERS CRUD -----------------
def create_order(data: Dict[str, Any]) -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor()

    # Formato de Ticket exacto: CIT-YYMMDD-XXX con hora exacta legal de Venezuela
    vet_now = now_vet()
    date_code = vet_now.strftime('%y%m%d')
    cursor.execute("SELECT ticket_code FROM orders WHERE ticket_code LIKE ?", (f"CIT-{date_code}-%",))
    existing_codes = {r[0] for r in cursor.fetchall()}
    idx = len(existing_codes) + 1
    while f"CIT-{date_code}-{idx:03d}" in existing_codes:
        idx += 1
    ticket_code = f"CIT-{date_code}-{idx:03d}"

    # Parametrizar en mayúsculas
    client_name = str(data["client_name"]).strip().upper()
    cedula = str(data["cedula"]).strip().upper()
    phone = str(data["phone"]).strip()
    payment_method = str(data.get("payment_method", "EFECTIVO / DIVISAS")).strip().upper()
    status = str(data.get("status", "PENDIENTE POR ATENCIÓN")).strip().upper()

    items_detail = data.get("items_detail", [])
    if isinstance(items_detail, list):
        items_detail_json = json.dumps(items_detail, ensure_ascii=False)
    else:
        items_detail_json = str(items_detail)

    items_summary = str(data.get("items_summary", "")).strip().upper()
    total_amount = float(data.get("total_amount", 0.0))
    amount_usd = float(data.get("amount_usd", total_amount))
    amount_ves = float(data.get("amount_ves", 0.0))
    bcv_rate_applied = float(data.get("bcv_rate_applied", 0.0))
    bcv_rate_date = str(data.get("bcv_rate_date", now_vet_date_str()))
    receipt_ref = data.get("receipt_ref")
    receipt_bank = data.get("receipt_bank")
    receipt_date = data.get("receipt_date")
    ocr_raw_text = data.get("ocr_raw_text")
    created_at_vet = now_vet_str()

    cursor.execute("""
        INSERT INTO orders (
            ticket_code, client_name, cedula, phone,
            items_summary, items_detail, total_items, total_amount,
            amount_usd, amount_ves, bcv_rate_applied, bcv_rate_date,
            payment_method, receipt_ref, receipt_bank, receipt_date, ocr_raw_text,
            pickup_date, pickup_time, status, is_off_hours, notes, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        ticket_code, client_name, cedula, phone,
        items_summary, items_detail_json,
        int(data.get("total_items", 1)),
        total_amount,
        amount_usd, amount_ves, bcv_rate_applied, bcv_rate_date,
        payment_method, receipt_ref, receipt_bank, receipt_date, ocr_raw_text,
        data.get("pickup_date", now_vet_date_str()),
        data.get("pickup_time", "09:00 AM"),
        status,
        int(data.get("is_off_hours", 0)),
        data.get("notes", ""),
        created_at_vet
    ))
    conn.commit()
    order_id = cursor.lastrowid

    # Descontar stock automáticamente y registrar en Kardex si hay productos
    if isinstance(items_detail, list):
        for it in items_detail:
            prod_id = it.get("id") or it.get("product_id")
            qty = int(it.get("qty", 1))
            if prod_id:
                try:
                    cursor.execute("SELECT stock, name FROM products WHERE id = ?", (prod_id,))
                    prow = cursor.fetchone()
                    if prow:
                        prev_stock = prow["stock"]
                        new_stock = max(0, prev_stock - qty)
                        cursor.execute("UPDATE products SET stock = ?, updated_at = ? WHERE id = ?", (new_stock, created_at_vet, prod_id))
                        cursor.execute("""
                            INSERT INTO inventory_movements (
                                product_id, movement_type, quantity, previous_stock,
                                new_stock, order_id, client_name, created_by, notes, created_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            prod_id, "SALIDA_VENTA", -qty, prev_stock,
                            new_stock, order_id, client_name, "BOT_VENTAS",
                            f"Venta con ticket {ticket_code}",
                            created_at_vet
                        ))
                        conn.commit()
                except Exception as e:
                    logger.error(f"Error descontando inventario para producto {prod_id}: {e}")

    # Actualizar o guardar cliente
    upsert_client(client_name, cedula, phone)

    cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
    order = dict(cursor.fetchone())
    conn.close()

    return order

def get_orders(limit: int = 500) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_order_by_id(order_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_order_by_ticket(ticket_code: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders WHERE ticket_code = ?", (ticket_code.strip().upper(),))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def update_order(order_id: int, data: Dict[str, Any]) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE orders
        SET client_name = ?, cedula = ?, phone = ?, items_summary = ?,
            total_items = ?, total_amount = ?, payment_method = ?,
            pickup_date = ?, pickup_time = ?, status = ?, notes = ?
        WHERE id = ?
    """, (
        str(data.get("client_name", "")).strip().upper(),
        str(data.get("cedula", "")).strip().upper(),
        str(data.get("phone", "")).strip(),
        str(data.get("items_summary", "")).strip().upper(),
        int(data.get("total_items", 1)),
        float(data.get("total_amount", 0.0)),
        str(data.get("payment_method", "EFECTIVO / DIVISAS")).strip().upper(),
        str(data.get("pickup_date", "")),
        str(data.get("pickup_time", "")),
        str(data.get("status", "PENDIENTE")).strip().upper(),
        data.get("notes", ""),
        order_id
    ))
    conn.commit()
    conn.close()
    return True

def update_order_status(order_id: int, status: str) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE orders SET status = ? WHERE id = ?", (status.strip().upper(), order_id))
    conn.commit()
    conn.close()
    return True

def delete_order(order_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM orders WHERE id = ?", (order_id,))
    conn.commit()
    conn.close()
    return True

def mark_reminder_sent(order_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE orders SET reminder_sent = 1 WHERE id = ?", (order_id,))
    conn.commit()
    conn.close()

# ----------------- SYSTEM CONFIG -----------------
def get_all_config() -> Dict[str, str]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT key, value FROM system_config")
    rows = cursor.fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}

def update_config(config_dict: Dict[str, str]):
    conn = get_connection()
    cursor = conn.cursor()
    for k, v in config_dict.items():
        cursor.execute("INSERT OR REPLACE INTO system_config (key, value) VALUES (?, ?)", (k, str(v)))
    conn.commit()
    conn.close()

def is_maintenance_active() -> bool:
    config = get_all_config()
    return config.get("maintenance_mode", "0") == "1"

def is_within_business_hours() -> bool:
    """Verifica si la hora actual está dentro del horario laboral (ej: 08:00 a 17:00) en hora de Venezuela"""
    config = get_all_config()
    start_str = config.get("business_hours_start", "08:00")
    end_str = config.get("business_hours_end", "17:00")

    try:
        sh, sm = map(int, start_str.split(":"))
        eh, em = map(int, end_str.split(":"))
        start_time = time(sh, sm)
        end_time = time(eh, em)

        now_time = now_vet().time()
        return start_time <= now_time <= end_time
    except Exception:
        return True

# ----------------- EXPORTS EXCEL / CSV -----------------
def export_orders_df() -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql_query("""
        SELECT ticket_code, client_name, cedula, phone, items_summary,
               total_items, total_amount, payment_method, pickup_date,
               pickup_time, status, is_off_hours, reminder_sent, created_at, notes
        FROM orders
        ORDER BY id DESC
    """, conn)
    conn.close()

    df.columns = [
        "NRO. TICKET", "CLIENTE (NOMBRE Y APELLIDO)", "CÉDULA", "TELÉFONO", "DETALLE DE PRODUCTOS",
        "CANT. ARTÍCULOS", "MONTO TOTAL ($)", "MÉTODO DE PAGO", "FECHA RETIRO",
        "HORA RETIRO", "ESTADO", "FUERA DE HORARIO", "RECORDATORIO ENVIADO", "FECHA REGISTRO", "OBSERVACIONES"
    ]
    return df

# ----------------- INVENTORY & KARDEX ADVANCED -----------------
def get_available_catalog_products() -> List[Dict[str, Any]]:
    """Devuelve únicamente productos activos con stock > 0 para el catálogo de WhatsApp"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM products 
        WHERE is_active = 1 AND stock > 0 
        ORDER BY category ASC, name ASC
    """)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def get_low_stock_products(threshold: int = 20) -> List[Dict[str, Any]]:
    """Productos con stock crítico (<= threshold) que requieren confección en taller"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM products 
        WHERE is_active = 1 AND stock <= COALESCE(min_stock_alert, ?)
        ORDER BY stock ASC
    """, (threshold,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def add_stock_batch(product_id: int, quantity: int, notes: str = "", created_by: str = "TALLER_CONFECCION") -> Dict[str, Any]:
    """Ingreso de lote terminado de confección al inventario con registro en Kardex en hora de Venezuela"""
    if quantity <= 0:
        raise ValueError("La cantidad debe ser mayor a 0")
        
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT stock, name FROM products WHERE id = ?", (product_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise ValueError(f"Producto {product_id} no encontrado")
        
    prev_stock = row["stock"]
    new_stock = prev_stock + quantity
    current_time_vet = now_vet_str()
    cursor.execute("UPDATE products SET stock = ?, updated_at = ?, updated_by = ? WHERE id = ?", (new_stock, current_time_vet, created_by, product_id))
    
    cursor.execute("""
        INSERT INTO inventory_movements (
            product_id, movement_type, quantity, previous_stock, new_stock, created_by, notes, created_at
        ) VALUES (?, 'ENTRADA_TALLER', ?, ?, ?, ?, ?, ?)
    """, (product_id, quantity, prev_stock, new_stock, created_by, notes or "Ingreso de lote al inventario", current_time_vet))
    conn.commit()
    conn.close()
    return {"product_id": product_id, "name": row["name"], "previous_stock": prev_stock, "new_stock": new_stock}

def get_inventory_movements(limit: int = 100) -> List[Dict[str, Any]]:
    """Historial de movimientos Kardex con datos del producto"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT m.*, p.name as product_name, p.slug as product_slug
        FROM inventory_movements m
        LEFT JOIN products p ON m.product_id = p.id
        ORDER BY m.id DESC
        LIMIT ?
    """, (limit,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def get_financial_and_sales_metrics() -> Dict[str, Any]:
    """Métricas financieras exactas ($ y Bs) y análisis de ventas"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Total ventas $ y Bs
    cursor.execute("""
        SELECT 
            COUNT(*) as total_orders,
            COALESCE(SUM(amount_usd), 0.0) as total_usd,
            COALESCE(SUM(amount_ves), 0.0) as total_ves
        FROM orders 
        WHERE status != 'CANCELADA'
    """)
    totals = dict(cursor.fetchone())
    
    # Ventas de hoy en hora oficial de Venezuela
    today_str = now_vet_date_str()
    cursor.execute("""
        SELECT 
            COUNT(*) as today_orders,
            COALESCE(SUM(amount_usd), 0.0) as today_usd,
            COALESCE(SUM(amount_ves), 0.0) as today_ves
        FROM orders 
        WHERE status != 'CANCELADA' AND date(created_at) = ?
    """, (today_str,))
    today_totals = dict(cursor.fetchone())
    
    # Conteo stock bajo
    cursor.execute("SELECT COUNT(*) as count FROM products WHERE is_active = 1 AND stock <= 20")
    low_stock_count = cursor.fetchone()["count"]
    
    # Top 5 productos vendidos
    cursor.execute("""
        SELECT p.name, ABS(SUM(m.quantity)) as units_sold
        FROM inventory_movements m
        JOIN products p ON m.product_id = p.id
        WHERE m.movement_type = 'SALIDA_VENTA'
        GROUP BY p.id
        ORDER BY units_sold DESC
        LIMIT 5
    """)
    top_products = [dict(r) for r in cursor.fetchall()]
    
    # Desglose de estados
    cursor.execute("SELECT status, COUNT(*) as count FROM orders GROUP BY status")
    status_breakdown = {r["status"]: r["count"] for r in cursor.fetchall()}
    
    conn.close()
    return {
        "total_orders": totals["total_orders"],
        "total_usd": round(totals["total_usd"], 2),
        "total_ves": round(totals["total_ves"], 2),
        "today_orders": today_totals["today_orders"],
        "today_usd": round(today_totals["today_usd"], 2),
        "today_ves": round(today_totals["today_ves"], 2),
        "low_stock_count": low_stock_count,
        "top_products": top_products,
        "status_breakdown": status_breakdown
    }

# Compatibilidad hacia atrás
get_appointments = get_orders
export_appointments_df = export_orders_df

# ----------------- PRODUCT WAITLIST (LISTA DE ESPERA POR STOCK) -----------------
def add_to_waitlist(phone: str, client_name: str, product_name: str, product_id: Optional[int] = None, notes: str = "") -> int:
    """Registra a un cliente en lista de espera cuando un producto no tiene stock disponible"""
    conn = get_connection()
    cursor = conn.cursor()
    now_str = now_vet_str()
    cursor.execute("""
        INSERT INTO product_waitlist (phone, client_name, product_id, product_name, status, notes, created_at)
        VALUES (?, ?, ?, ?, 'PENDIENTE', ?, ?)
    """, (phone, client_name.strip().upper() if client_name else "CLIENTE", product_id, product_name.strip().upper(), notes, now_str))
    waitlist_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return waitlist_id

def get_waitlist(status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Obtiene la lista de clientes en espera por reposición de stock"""
    conn = get_connection()
    cursor = conn.cursor()
    if status:
        cursor.execute("SELECT * FROM product_waitlist WHERE status = ? ORDER BY id DESC", (status.strip().upper(),))
    else:
        cursor.execute("SELECT * FROM product_waitlist ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_pending_waitlist_for_product(product_id: Optional[int], product_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Busca clientes pendientes de aviso para un producto en específico"""
    conn = get_connection()
    cursor = conn.cursor()
    if product_id and product_name:
        clean_name = f"%{product_name.strip().lower()}%"
        cursor.execute("""
            SELECT * FROM product_waitlist 
            WHERE status = 'PENDIENTE' AND (product_id = ? OR LOWER(product_name) LIKE ?)
        """, (product_id, clean_name))
    elif product_id:
        cursor.execute("SELECT * FROM product_waitlist WHERE status = 'PENDIENTE' AND product_id = ?", (product_id,))
    elif product_name:
        clean_name = f"%{product_name.strip().lower()}%"
        cursor.execute("SELECT * FROM product_waitlist WHERE status = 'PENDIENTE' AND LOWER(product_name) LIKE ?", (clean_name,))
    else:
        conn.close()
        return []
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def mark_waitlist_notified(waitlist_id: int):
    """Marca un registro de lista de espera como ya notificado con fecha y hora de Venezuela"""
    conn = get_connection()
    cursor = conn.cursor()
    now_str = now_vet_str()
    cursor.execute("UPDATE product_waitlist SET status = 'NOTIFICADO', notified_at = ? WHERE id = ?", (now_str, waitlist_id))
    conn.commit()
    conn.close()

def update_waitlist_item(waitlist_id: int, status: str, notes: Optional[str] = None, client_name: Optional[str] = None, phone: Optional[str] = None) -> bool:
    """Permite al administrador modificar el estatus y datos del cliente en lista de espera"""
    conn = get_connection()
    cursor = conn.cursor()
    fields = ["status = ?"]
    params = [status.strip().upper()]
    if client_name is not None:
        fields.append("client_name = ?")
        params.append(client_name.strip().upper())
    if phone is not None:
        fields.append("phone = ?")
        params.append(phone.strip())
    if notes is not None:
        fields.append("notes = ?")
        params.append(notes.strip())
    params.append(waitlist_id)
    cursor.execute(f"UPDATE product_waitlist SET {', '.join(fields)} WHERE id = ?", tuple(params))
    conn.commit()
    conn.close()
    return True

def delete_waitlist_item(waitlist_id: int) -> bool:
    """Elimina un registro de la lista de espera"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM product_waitlist WHERE id = ?", (waitlist_id,))
    conn.commit()
    conn.close()
    return True

def convert_waitlist_to_order(waitlist_id: int, order_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convierte una persona en lista de espera directamente a un cliente en pedidos y citas.
    Crea la orden en la tabla orders y actualiza el registro en la lista de espera como 'CONVERTIDO EN PEDIDO'.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM product_waitlist WHERE id = ?", (waitlist_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise ValueError(f"Registro en lista de espera #{waitlist_id} no encontrado")
    
    waitlist_item = dict(row)
    
    # Completar datos con los de la lista de espera si no vienen en order_data
    client_name = order_data.get("client_name") or waitlist_item.get("client_name") or "CLIENTE GENERAL"
    phone = order_data.get("phone") or waitlist_item.get("phone")
    cedula = order_data.get("cedula") or "S/C"
    
    prod_name = waitlist_item.get("product_name") or "PRODUCTO"
    prod_id = waitlist_item.get("product_id")
    qty = int(order_data.get("qty", 1))
    
    # Obtener precio del producto si no viene en order_data
    price = float(order_data.get("total_amount", 0.0))
    if price <= 0 and prod_id:
        cursor.execute("SELECT price FROM products WHERE id = ?", (prod_id,))
        prow = cursor.fetchone()
        if prow:
            price = float(prow["price"]) * qty

    items_detail = order_data.get("items_detail") or [
        {"id": prod_id, "name": prod_name, "qty": qty, "unit_price": price / qty if qty > 0 else price, "subtotal": price}
    ]
    items_summary = order_data.get("items_summary") or f"{qty}X {prod_name}"

    order_payload = {
        "client_name": client_name,
        "cedula": cedula,
        "phone": phone,
        "items_summary": items_summary,
        "items_detail": items_detail,
        "total_items": qty,
        "total_amount": price,
        "amount_usd": price,
        "amount_ves": float(order_data.get("amount_ves", 0.0)),
        "bcv_rate_applied": float(order_data.get("bcv_rate_applied", 0.0)),
        "bcv_rate_date": order_data.get("bcv_rate_date", now_vet_date_str()),
        "payment_method": order_data.get("payment_method", "EFECTIVO / DIVISAS"),
        "pickup_date": order_data.get("pickup_date", now_vet_date_str()),
        "pickup_time": order_data.get("pickup_time", "09:00 AM"),
        "status": order_data.get("status", "PENDIENTE POR ATENCIÓN"),
        "notes": f"Convertido desde Lista de Espera #{waitlist_id}"
    }
    
    # Crear la orden oficial
    new_order = create_order(order_payload)
    
    # Marcar waitlist como CONVERTIDO EN PEDIDO
    now_str = now_vet_str()
    cursor.execute("""
        UPDATE product_waitlist 
        SET status = 'CONVERTIDO EN PEDIDO', notified_at = ? 
        WHERE id = ?
    """, (now_str, waitlist_id))
    conn.commit()
    conn.close()
    
    return new_order

# ----------------- RESET SISTEMA A ESTADO VIRGEN -----------------
def reset_database_to_virgin():
    """
    Limpia completamente las tablas de datos para dejar el sistema 100% virgen para su primer uso real:
    - Vence pedidos y citas (orders)
    - Limpia movimientos kardex (inventory_movements)
    - Limpia lista de espera (product_waitlist)
    - Limpia catálogo de productos (products)
    - Limpia clientes (clients)
    Conserva las configuraciones del sistema (system_config) y el histórico de tasas BCV (bcv_rates).
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM orders")
    cursor.execute("DELETE FROM inventory_movements")
    cursor.execute("DELETE FROM product_waitlist")
    cursor.execute("DELETE FROM products")
    cursor.execute("DELETE FROM clients")
    try:
        cursor.execute("DELETE FROM sqlite_sequence WHERE name IN ('orders', 'inventory_movements', 'product_waitlist', 'products', 'clients')")
    except Exception:
        pass
    conn.commit()
    conn.close()
    logger.info("Base de datos de SIS-COMER reseteada a estado VIRGEN (sin registros previos)")



