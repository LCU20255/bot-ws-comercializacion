import sqlite3
import json
import logging
from datetime import datetime, time
from pathlib import Path
from typing import List, Dict, Any, Optional
import pandas as pd
from app.config import DATA_DIR, SUPABASE_URL, SUPABASE_KEY

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
        stock INTEGER DEFAULT 100,
        is_active INTEGER DEFAULT 1,
        requires_size INTEGER DEFAULT 0,
        keywords TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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

    conn.commit()

    # Sembrar productos militares iniciales si está vacía
    cursor.execute("SELECT COUNT(*) as count FROM products")
    if cursor.fetchone()["count"] == 0:
        military_products = [
            (
                "BARRAS Y PRESILLAS VENEZUELA RENACE",
                "barras-presillas-venezuela-renace",
                "Barras de condecoración militar Venezuela Renace y presillas de oro con acabado reglamentario para oficiales y tropas.",
                18.0,
                "18.00 Ref / Juego",
                "CONDECORACIONES",
                "/static/images/barras_venezuela.png",
                150,
                1,
                0,
                "barras, barra, venezuela renace, presillas, presillas de oro, condecoracion, barra militar, insignia",
                "ADMIN"
            ),
            (
                "UNIFORMES MILITARES Y TÁCTICOS",
                "uniformes-militares-tacticos",
                "Confección textil de alta resistencia: Uniformes patriotas, de campaña, faena militar y camisas corporativas con costuras reforzadas.",
                35.0,
                "35.00 Ref / Uniforme",
                "TEXTIL & UNIFORMES",
                "/static/images/uniformes_militares.png",
                200,
                1,
                1,
                "uniformes, uniforme militar, patriota, camuflaje, faena, ropa militar, uniforme tactico",
                "ADMIN"
            ),
            (
                "PARCHES BORDADOS E IDENTIFICADORES",
                "parches-bordados-militares",
                "Parches institucionales con hilo de alta definición: Escudos de unidades, jerarquías, grados militares y porta-nombres.",
                5.0,
                "5.00 Ref / Unidad",
                "BORDADOS",
                "/static/images/parches_militares.png",
                500,
                1,
                0,
                "parches, parche militar, bordados, identificadores, escudo, nombres militares, jerarquia",
                "ADMIN"
            ),
            (
                "BOTAS TÁCTICAS Y CALZADO MILITAR",
                "botas-tacticas-militares",
                "Botas de campaña en cuero legítimo y lona técnica, caña alta, suela antiresbalante de alto impacto.",
                45.0,
                "45.00 Ref / Par",
                "CALZADO",
                "/static/images/botas_tacticas.png",
                120,
                1,
                1,
                "botas, bota militar, botas tacticas, calzado militar, zapatos",
                "ADMIN"
            ),
            (
                "GORRAS Y BOINAS TÁCTICAS",
                "gorras-boinas-tacticas",
                "Gorras tácticas reglamentarias con velcro y bordado personalizado, boinas militares en paño fino.",
                12.0,
                "12.00 Ref / Unidad",
                "ACCESORIOS",
                "/static/images/gorras_boinas.png",
                250,
                1,
                1,
                "gorras, gorra, boina, boinas, quepe, boina militar, gorra bordada",
                "ADMIN"
            )
        ]
        cursor.executemany("""
            INSERT INTO products (name, slug, description, price, price_display, category, image_url, stock, is_active, requires_size, keywords, updated_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, military_products)
        conn.commit()

    # Configuraciones de Sistema por defecto
    default_config = {
        "bot_name": "SISTEMA DE COMERCIALIZACIÓN TEXTIL MILITAR",
        "maintenance_mode": "0",  # 0 = Activo normal, 1 = Modo mantenimiento activado
        "maintenance_message": "¡Hola! En este momento nos encontramos en proceso de auditoría interna / mantenimiento. Por favor comunícate con nosotros el día de mañana de 8:00 AM a 5:00 PM.",
        "business_hours_start": "08:00",
        "business_hours_end": "17:00",
        "off_hours_message": "Hola. En este momento nos encontramos fuera de nuestro horario laboral (Lunes a Viernes de 8:00 AM a 5:00 PM). Sin embargo, tu solicitud quedará guardada en el sistema para ser atendida a primera hora hábil.",
        "advisor_phone": "+584121234567",
        "advisor_name": "ASESOR COMERCIAL MILITAR",
        "pickup_address": "SEDE PRINCIPAL DE COMERCIALIZACIÓN E INTENDENCIA MILITAR",
        "pickup_hours": "LUNES A VIERNES DE 8:00 AM A 5:00 PM"
    }
    for k, v in default_config.items():
        cursor.execute("INSERT OR IGNORE INTO system_config (key, value) VALUES (?, ?)", (k, v))
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

    cursor.execute("""
        INSERT INTO products (name, slug, description, price, price_display, category, image_url, stock, is_active, requires_size, keywords, updated_at, updated_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
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

    cursor.execute("""
        UPDATE products
        SET name = ?, description = ?, price = ?, price_display = ?, category = ?, image_url = ?, stock = ?, is_active = ?, requires_size = ?, keywords = ?, updated_at = CURRENT_TIMESTAMP, updated_by = ?
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
        cursor.execute("INSERT INTO clients (name, cedula, phone) VALUES (?, ?, ?)", (clean_name, clean_cedula, clean_phone))
        client_id = cursor.lastrowid

    conn.commit()
    conn.close()
    return client_id

# ----------------- ORDERS CRUD -----------------
def create_order(data: Dict[str, Any]) -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor()

    # Formato de Ticket exacto pedido por el usuario: CIT-YYMMDD-XXX
    now = datetime.now()
    date_code = now.strftime('%y%m%d')
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
    bcv_rate_date = str(data.get("bcv_rate_date", now.strftime('%Y-%m-%d')))
    receipt_ref = data.get("receipt_ref")
    receipt_bank = data.get("receipt_bank")
    receipt_date = data.get("receipt_date")
    ocr_raw_text = data.get("ocr_raw_text")

    cursor.execute("""
        INSERT INTO orders (
            ticket_code, client_name, cedula, phone,
            items_summary, items_detail, total_items, total_amount,
            amount_usd, amount_ves, bcv_rate_applied, bcv_rate_date,
            payment_method, receipt_ref, receipt_bank, receipt_date, ocr_raw_text,
            pickup_date, pickup_time, status, is_off_hours, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        ticket_code, client_name, cedula, phone,
        items_summary, items_detail_json,
        int(data.get("total_items", 1)),
        total_amount,
        amount_usd, amount_ves, bcv_rate_applied, bcv_rate_date,
        payment_method, receipt_ref, receipt_bank, receipt_date, ocr_raw_text,
        data.get("pickup_date", now.strftime('%Y-%m-%d')),
        data.get("pickup_time", "09:00 AM"),
        status,
        int(data.get("is_off_hours", 0)),
        data.get("notes", "")
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
                        cursor.execute("UPDATE products SET stock = ? WHERE id = ?", (new_stock, prod_id))
                        cursor.execute("""
                            INSERT INTO inventory_movements (
                                product_id, movement_type, quantity, previous_stock,
                                new_stock, order_id, client_name, created_by, notes
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            prod_id, "SALIDA_VENTA", -qty, prev_stock,
                            new_stock, order_id, client_name, "BOT_VENTAS",
                            f"Venta con ticket {ticket_code}"
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
    """Verifica si la hora actual está dentro del horario laboral (ej: 08:00 a 17:00)"""
    config = get_all_config()
    start_str = config.get("business_hours_start", "08:00")
    end_str = config.get("business_hours_end", "17:00")

    try:
        sh, sm = map(int, start_str.split(":"))
        eh, em = map(int, end_str.split(":"))
        start_time = time(sh, sm)
        end_time = time(eh, em)

        now_time = datetime.now().time()
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
    """Ingreso de lote terminado de confección al inventario con registro en Kardex"""
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
    cursor.execute("UPDATE products SET stock = ?, updated_at = CURRENT_TIMESTAMP, updated_by = ? WHERE id = ?", (new_stock, created_by, product_id))
    
    cursor.execute("""
        INSERT INTO inventory_movements (
            product_id, movement_type, quantity, previous_stock, new_stock, created_by, notes
        ) VALUES (?, 'ENTRADA_TALLER', ?, ?, ?, ?, ?)
    """, (product_id, quantity, prev_stock, new_stock, created_by, notes or "Ingreso de lote terminado desde taller"))
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
    
    # Ventas de hoy
    today_str = datetime.now().strftime('%Y-%m-%d')
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


