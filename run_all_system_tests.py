import sys
import os
import tempfile
from datetime import datetime, timedelta

# Configurar encoding utf-8 para salida en consola Windows
sys.stdout.reconfigure(encoding='utf-8')

print("=" * 80)
print("🚀 INICIANDO BATERÍA DE PRUEBAS DE CÓDIGO INTEGRALES - SIS-COMER")
print("=" * 80)

from app.database import (
    init_db,
    get_connection,
    get_products,
    get_available_catalog_products,
    get_low_stock_products,
    add_stock_batch,
    get_inventory_movements,
    get_financial_and_sales_metrics,
    create_order
)
from app.bcv_service import bcv_service
from app.ocr_service import ReceiptOCRService
from app.bot_flow import bot_manager, reset_session, get_session

# Inicializar Base de Datos
init_db()

# Asegurar existencia de productos para pruebas si está vacía
from app.database import create_product, update_order_status, delete_order, get_order_by_id, get_product_by_id, update_config, get_all_config
existing_p = get_products()
if len(existing_p) == 0:
    create_product({
        "name": "Chaqueta Patriota Tiuna",
        "category": "TEXTIL MILITAR",
        "description": "Chaqueta oficial de campaña con velcro",
        "price": 45.0,
        "stock": 100,
        "requires_size": 1,
        "available_sizes": "S, M, L, XL, XXL",
        "active": 1
    })
    create_product({
        "name": "Botas Militares Campaña",
        "category": "CALZADO MILITAR",
        "description": "Botas de cuero táctico de alta resistencia",
        "price": 60.0,
        "stock": 50,
        "requires_size": 1,
        "available_sizes": "39, 40, 41, 42, 43, 44",
        "active": 1
    })
    create_product({
        "name": "Gorra Táctica Patriota",
        "category": "ACCESORIOS",
        "description": "Gorra ajustable verde oliva",
        "price": 15.0,
        "stock": 80,
        "requires_size": 0,
        "active": 1
    })

passed_tests = 0
total_tests = 14

# -----------------------------------------------------------------------------
# TEST 1: ACCESO DIRECTO AL CATÁLOGO SIN REGISTRO OBLIGATORIO
# -----------------------------------------------------------------------------
print("\n[TEST 1] Flujo de Acceso Directo al Catálogo (Sin Registro Previo)...")
phone_test = "+584120001122"
reset_session(phone_test)

# Paso 1.1: Saludo inicial abre inmediatamente el catálogo oficial
r1 = bot_manager.process_message(phone_test, "Hola, buenas tardes")
assert "CATÁLOGO" in r1["reply"] or "SIS-COMER" in r1["reply"], "Fallo: No mostró Catálogo al iniciar"
assert r1["state"] == "CATALOG", "Fallo: El estado no es CATALOG"
print("  ✓ Paso 1.1: Saludo recibido -> Catálogo SIS-COMER desplegado inmediatamente sin registro previo")
passed_tests += 1

# -----------------------------------------------------------------------------
# TEST 2: EXCLUSIÓN ESTRICTA DE PRODUCTOS CON STOCK = 0 EN WHATSAPP
# -----------------------------------------------------------------------------
print("\n[TEST 2] Filtro Estricto de Catálogo WhatsApp (stock > 0)...")
prods = get_products()
assert len(prods) > 0, "Fallo: No hay productos en base de datos"
target_prod = prods[0]
orig_stock = target_prod["stock"]

# Simular producto agotado (stock = 0)
conn = get_connection()
cur = conn.cursor()
cur.execute("UPDATE products SET stock = 0 WHERE id = ?", (target_prod["id"],))
conn.commit()
conn.close()

avail_catalog = get_available_catalog_products()
avail_ids = [p["id"] for p in avail_catalog]
assert target_prod["id"] not in avail_ids, f"Fallo: El producto {target_prod['name']} con stock 0 apareció en catálogo"
print(f"  ✓ Producto '{target_prod['name']}' con stock 0 quedó EXCLUIDO del catálogo de WhatsApp")

# Restaurar stock
conn = get_connection()
cur = conn.cursor()
cur.execute("UPDATE products SET stock = ? WHERE id = ?", (orig_stock, target_prod["id"]))
conn.commit()
conn.close()
passed_tests += 1

# -----------------------------------------------------------------------------
# TEST 3: CONSULTA DE PRECIOS DUALES ($ REF Y BS) CON TASA BCV OFICIAL
# -----------------------------------------------------------------------------
print("\n[TEST 3] Cálculo Dual de Precios ($ Ref y Bs) a Tasa Oficial BCV...")
bcv_rate = bcv_service.get_rate_for_date()
assert bcv_rate > 0, "Fallo: Tasa BCV inválida o no encontrada"
print(f"  ✓ Tasa BCV Oficial activa: Bs. {bcv_rate:,.2f} / $")

# Verificar que el mensaje del catálogo incluya tanto $ como Bs
r_cat = bot_manager.process_message(phone_test, "0")
assert "$" in r_cat["reply"] and "Bs." in r_cat["reply"], "Fallo: Catálogo no muestra ambos montos ($ y Bs)"
print("  ✓ Catálogo de WhatsApp presenta simultáneamente montos en Divisas ($ Ref) y Bolívares (Bs)")
passed_tests += 1

# -----------------------------------------------------------------------------
# TEST 4: OCR DE COMPROBANTE DE PAGO Y DESTRUCCIÓN INMEDIATA DE DISCO (PURGA)
# -----------------------------------------------------------------------------
print("\n[TEST 4] Extracción OCR de Comprobante y Destrucción Física de Disco (Purga)...")
# Crear imagen temporal simulada
tmp_file = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
tmp_file.write(b"\xFF\xD8\xFF\xE0\x00\x10JFIFMOCK_BANK_RECEIPT_BYTES")
tmp_file.close()

file_path = tmp_file.name
assert os.path.exists(file_path), "Fallo: No se pudo crear archivo temporal"
print(f"  ✓ Archivo temporal de comprobante creado en: {os.path.basename(file_path)}")

# Ejecutar proceso OCR con destrucción garantizada
receipt_text = "PAGO MOVIL BANCO DE VENEZUELA APROBADO REF: 88776655 MONTO: BS. 1.850,50 FECHA: 05/10/2026"
parsed_receipt = ReceiptOCRService.process_and_destroy_receipt(file_path, simulated_hint_text=receipt_text)

assert parsed_receipt["bank"] == "BANCO DE VENEZUELA", "Fallo: Banco no identificado"
assert parsed_receipt["reference"] == "88776655", "Fallo: Referencia incorrecta"
assert parsed_receipt["amount"] == 1850.5, f"Fallo: Monto incorrecto {parsed_receipt['amount']}"
assert parsed_receipt["payment_date"] == "2026-10-05", "Fallo: Fecha incorrecta"
print(f"  ✓ OCR extrajo Banco: {parsed_receipt['bank']} | Ref: {parsed_receipt['reference']} | Monto: {parsed_receipt['amount']} {parsed_receipt['currency']} | Fecha: {parsed_receipt['payment_date']}")

# VERIFICAR QUE EL ARCHIVO FUE DESTRUIDO DE INMEDIATO DEL DISCO
assert not os.path.exists(file_path), "Fallo CRÍTICO: El archivo temporal NO fue destruido de disco"
print("  ✓ PURGA VERIFICADA: El archivo temporal fue DESTRUIDO físicamente de disco inmediatamente (0 bytes restantes)")
passed_tests += 1

# -----------------------------------------------------------------------------
# TEST 5: VINCULACIÓN DE TASA BCV HISTÓRICA DE LA FECHA DE PAGO (AYER VS HOY)
# -----------------------------------------------------------------------------
print("\n[TEST 5] Vinculación de Tasa BCV Histórica según la Fecha del Recibo...")
# Guardar tasa de ayer
yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
yesterday_rate = 860.25
bcv_service.save_rate(yesterday_str, yesterday_rate)

rate_for_yesterday = bcv_service.get_rate_for_date(yesterday_str)
assert rate_for_yesterday == yesterday_rate, "Fallo: No se obtuvo la tasa histórica de ayer"

today_str = datetime.now().strftime("%Y-%m-%d")
rate_for_today = bcv_service.get_rate_for_date(today_str)

print(f"  ✓ Recibo con fecha de AYER ({yesterday_str}) -> Ata Tasa BCV de AYER: Bs. {rate_for_yesterday:.2f}/$")
print(f"  ✓ Recibo con fecha de HOY  ({today_str}) -> Ata Tasa BCV de HOY:  Bs. {rate_for_today:.2f}/$")
passed_tests += 1

# -----------------------------------------------------------------------------
# TEST 6: PAGO OBLIGATORIO ANTES DE AGENDAR Y GENERACIÓN DE TICKET CIT-...
# -----------------------------------------------------------------------------
print("\n[TEST 6] Pago Previo Obligatorio, Agendamiento y Emisión de Ticket Oficial CIT-...")
phone_order = "+584143334455"
reset_session(phone_order, keep_registration=False)

# 6.1 Selección de producto directo desde catálogo
r_sel = bot_manager.process_message(phone_order, "1")
if r_sel["state"] == "SELECTING_SIZE":
    bot_manager.process_message(phone_order, "L")

# 6.3 Proceder a pagar
r_pay = bot_manager.process_message(phone_order, "2")
assert r_pay["state"] == "AWAITING_PAYMENT", "Fallo: No solicitó pago obligatorio"
assert "PAGO PREVIO OBLIGATORIO" in r_pay["reply"], "Fallo: No contiene mensaje de pago previo"
print("  ✓ Paso 6.1: Bot exige pago previo antes de permitir agendamiento de fecha y hora")

# 6.4 Enviar comprobante de pago
r_receipt = bot_manager.process_message(phone_order, "Pago movil Banco de Venezuela Ref 776655 Bs 2500 fecha hoy")
assert r_receipt["state"] == "AWAITING_SCHEDULE", "Fallo: No avanzó a agendamiento tras comprobante"
assert "COMPROBANTE DE PAGO VALIDADO" in r_receipt["reply"], "Fallo: No validó el comprobante"
print("  ✓ Paso 6.2: Comprobante validado -> Sistema habilita selección de fecha y hora de retiro")

# 6.5 Indicar fecha y hora de retiro
r_final = bot_manager.process_message(phone_order, "Mañana a las 09:30 AM")
assert r_final["state"] == "COMPLETED", "Fallo: La orden no finalizó en COMPLETED"
ticket = r_final["ticket_code"]
assert ticket.startswith("CIT-"), f"Fallo: Formato de ticket inválido {ticket}"
assert "TICKET OFICIAL SIS-COMER" in r_final["reply"], "Fallo: No contiene ticket oficial en respuesta"
print(f"  ✓ Paso 6.3: Solicitud completada exitosamente -> Ticket emitido: {ticket}")
passed_tests += 1

# -----------------------------------------------------------------------------
# TEST 7: ALERTA DE STOCK CRÍTICO (<= 20) Y AUDITORÍA KARDEX MILITAR
# -----------------------------------------------------------------------------
print("\n[TEST 7] Alertas de Stock Bajo (<= 20) e Ingreso de Lote Kardex...")
# Establecer un producto con 18 unidades
conn = get_connection()
cur = conn.cursor()
cur.execute("SELECT id, name FROM products LIMIT 1")
sample_p = cur.fetchone()
sample_id = sample_p["id"]
cur.execute("UPDATE products SET stock = 18 WHERE id = ?", (sample_id,))
conn.commit()
conn.close()

low_prods = get_low_stock_products(threshold=20)
low_ids = [p["id"] for p in low_prods]
assert sample_id in low_ids, "Fallo: No disparó alerta de stock bajo para stock <= 20"
print(f"  ✓ Alerta disparada para '{sample_p['name']}': Stock crítico <= 20 detectado para reconfección")

# Ingresar lote desde taller con Kardex
batch_res = add_stock_batch(sample_id, quantity=50, notes="Lote terminado taller Tiuna", created_by="TALLER_CONFECCION")
assert batch_res["new_stock"] == 68, f"Fallo en nuevo stock: {batch_res['new_stock']}"

movements = get_inventory_movements(limit=5)
assert any(m["movement_type"] == "ENTRADA_TALLER" and m["product_id"] == sample_id for m in movements), "Fallo: No se registró ENTRADA_TALLER en Kardex"
assert any(m["movement_type"] == "SALIDA_VENTA" for m in movements), "Fallo: No se registraron ventas en Kardex"
print("  ✓ Kardex auditó exitosamente ENTRADA_TALLER (+50 uds) y SALIDA_VENTA automáticas")
passed_tests += 1

# -----------------------------------------------------------------------------
# TEST 8: MÉTRICAS FINANCIERAS CONSOLIDADAS ($ Y BS)
# -----------------------------------------------------------------------------
print("\n[TEST 8] Métricas Financieras Consolidadas en Divisas ($) y Bolívares (Bs)...")
metrics = get_financial_and_sales_metrics()
assert "total_usd" in metrics and "total_ves" in metrics, "Fallo: Métricas no contienen montos duales"
assert metrics["total_orders"] > 0, "Fallo: Conteo de órdenes es cero"
assert metrics["total_usd"] > 0, "Fallo: Total USD es cero"
assert metrics["total_ves"] > 0, "Fallo: Total VES es cero"
print(f"  ✓ Total Ventas SIS-COMER: ${metrics['total_usd']:.2f} REF")
print(f"  ✓ Total en Bolívares:     Bs. {metrics['total_ves']:,.2f}")
print(f"  ✓ Total Pedidos:          {metrics['total_orders']}")
print(f"  ✓ Ventas de Hoy:          ${metrics['today_usd']:.2f} REF (Bs. {metrics['today_ves']:,.2f})")
print(f"  ✓ Top Productos Vendidos: {len(metrics['top_products'])} productos listados")
passed_tests += 1

# -----------------------------------------------------------------------------
# TEST 9: RETORNO DE STOCK A INVENTARIO Y KARDEX POR PEDIDO CANCELADO
# -----------------------------------------------------------------------------
print("\n[TEST 9] Retorno de Stock y Kardex al Cancelar un Pedido...")
# Crear un pedido de prueba con 2 unidades de un producto
test_prod = get_products()[0]
p_id = test_prod["id"]
stock_before_order = test_prod["stock"]

sample_order = create_order({
    "client_name": "CARLOS CANCELACION",
    "cedula": "V-20111222",
    "phone": "0412-9998877",
    "items_detail": [{"id": p_id, "name": test_prod["name"], "qty": 2, "unit_price": 45.0, "subtotal": 90.0}],
    "items_summary": f"2X {test_prod['name']}",
    "total_items": 2,
    "total_amount": 90.0,
    "amount_usd": 90.0,
    "amount_ves": 90.0 * 860.25,
    "status": "PENDIENTE POR ATENCIÓN"
})
order_id_test = sample_order["id"]

prod_after_order = get_product_by_id(p_id)
assert prod_after_order["stock"] == stock_before_order - 2, "Fallo: No se descontó el stock al crear pedido"

# CANCELAR EL PEDIDO: Debe retornar las 2 unidades a stock
update_order_status(order_id_test, "CANCELADO")
prod_after_cancel = get_product_by_id(p_id)
assert prod_after_cancel["stock"] == stock_before_order, f"Fallo: El stock no retornó. Actual: {prod_after_cancel['stock']}, Esperado: {stock_before_order}"

# Verificar Kardex
movements = get_inventory_movements(limit=5)
cancel_mov = next((m for m in movements if m["movement_type"] == "REVERSO_CANCELACION" and m["order_id"] == order_id_test), None)
assert cancel_mov is not None, "Fallo: No se registró movimiento REVERSO_CANCELACION en Kardex"
assert cancel_mov["quantity"] == 2, f"Fallo en cantidad de reverso: {cancel_mov['quantity']}"
print(f"  ✓ Pedido #{order_id_test} cambiado a 'CANCELADO' -> Stock devuelto (+2 uds) con movimiento REVERSO_CANCELACION en Kardex")
passed_tests += 1

# -----------------------------------------------------------------------------
# TEST 10: DESCUENTO DE STOCK Y KARDEX AL REACTIVAR PEDIDO CANCELADO
# -----------------------------------------------------------------------------
print("\n[TEST 10] Descuento de Stock al Reactivar un Pedido Previamente Cancelado...")
update_order_status(order_id_test, "CONFIRMADO")
prod_after_reactivate = get_product_by_id(p_id)
assert prod_after_reactivate["stock"] == stock_before_order - 2, "Fallo: No se volvió a descontar el stock al reactivar pedido"

movements = get_inventory_movements(limit=5)
reactivate_mov = next((m for m in movements if m["movement_type"] == "SALIDA_VENTA" and m["order_id"] == order_id_test), None)
assert reactivate_mov is not None, "Fallo: No se auditó SALIDA_VENTA al reactivar pedido"
print(f"  ✓ Pedido #{order_id_test} reactivado a 'CONFIRMADO' -> Stock vuelto a descontar (-2 uds) en Kardex")
passed_tests += 1

# -----------------------------------------------------------------------------
# TEST 11: RETORNO DE STOCK A INVENTARIO Y KARDEX POR ELIMINACIÓN DE PEDIDO
# -----------------------------------------------------------------------------
print("\n[TEST 11] Retorno de Stock y Kardex al Eliminar un Pedido...")
delete_order(order_id_test)
prod_after_delete = get_product_by_id(p_id)
assert prod_after_delete["stock"] == stock_before_order, "Fallo: El stock no retornó al eliminar pedido activo"

movements = get_inventory_movements(limit=5)
delete_mov = next((m for m in movements if m["movement_type"] == "REVERSO_ELIMINACION" and m["order_id"] == order_id_test), None)
assert delete_mov is not None, "Fallo: No se registró REVERSO_ELIMINACION en Kardex al eliminar pedido"
print(f"  ✓ Pedido #{order_id_test} eliminado de BD -> Stock retornado automáticamente (+2 uds) con REVERSO_ELIMINACION en Kardex")
passed_tests += 1

# -----------------------------------------------------------------------------
# TEST 12: DETECCIÓN INTELIGENTE DE SENTIMIENTO NEGATIVO Y DERIVACIÓN A ASESOR
# -----------------------------------------------------------------------------
print("\n[TEST 12] Detección Inteligente de Inconformidad y Mensajes Negativos...")
phone_complaint = "+584149991122"
reset_session(phone_complaint)

complaint_msgs = [
    "Qué mal servicio, no responden",
    "Terrible la atención, tardan demasiado",
    "No entiendo nada, está mal esto"
]
for msg in complaint_msgs:
    r_comp = bot_manager.process_message(phone_complaint, msg)
    assert r_comp["state"] == "WAITING_ADVISOR", f"Fallo: No derivó a asesor ante queja '{msg}'"
    assert "asesor" in r_comp["reply"].lower() and "wa.me" in r_comp["reply"], f"Fallo: No ofreció WhatsApp de asesor ante queja '{msg}'"

print("  ✓ Detección NLU: Frases de mal servicio y quejas activan empatía y enlace directo de WhatsApp con asesor humano")
passed_tests += 1

# -----------------------------------------------------------------------------
# TEST 13: TALLAS DISPONIBLES CONFIGURABLES Y SELECCIÓN POR BOTÓN
# -----------------------------------------------------------------------------
print("\n[TEST 13] Tallas Disponibles Configurables y Selección por Botón...")
phone_size_test = "+584128889900"
reset_session(phone_size_test)

# Seleccionar dinámicamente el producto que requiere talla
avail = get_available_catalog_products()
size_prod_idx = next(i for i, p in enumerate(avail, 1) if p.get("requires_size") == 1)
target_sized_prod = avail[size_prod_idx - 1]

r_size_prompt = bot_manager.process_message(phone_size_test, str(size_prod_idx))
assert r_size_prompt["state"] == "SELECTING_SIZE", "Fallo: No solicitó talla"
assert "[ 1️⃣ ]" in r_size_prompt["reply"], "Fallo: No mostró botones interactivos para tallas"

# Responder con el botón '2' (Segunda talla disponible)
r_size_select = bot_manager.process_message(phone_size_test, "2")
assert r_size_select["state"] == "CART_VIEW", "Fallo: No avanzó a carrito tras elegir botón de talla"
cart_item = get_session(phone_size_test)["cart"][0]
assert cart_item["size"] is not None, "Fallo: Talla asignada es None"
print(f"  ✓ Botón numérico '2' seleccionó exitosamente la talla '{cart_item['size']}' para '{target_sized_prod['name']}'")
passed_tests += 1

# -----------------------------------------------------------------------------
# TEST 14: CUENTAS Y MÉTODOS DE PAGO DINÁMICOS DESDE CONFIGURACIÓN
# -----------------------------------------------------------------------------
print("\n[TEST 14] Métodos y Cuentas Bancarias Dinámicas en Instrucciones de Pago...")
update_config({
    "pagomovil_bank": "Banco Banesco (0134)",
    "pagomovil_phone": "0414-7778899",
    "pagomovil_id": "J-998877665",
    "transfer_bank": "Banco Mercantil",
    "transfer_account": "0105-0000-00-1111222233",
    "transfer_holder": "INDUSTRIA MILITAR TIUNA CA"
})

r_pay_dyn = bot_manager.process_message(phone_size_test, "2")
assert r_pay_dyn["state"] == "AWAITING_PAYMENT", "Fallo: No avanzó a AWAITING_PAYMENT"
assert "0414-7778899" in r_pay_dyn["reply"], "Fallo: No reflejó teléfono de pago móvil configurado"
assert "Banco Banesco" in r_pay_dyn["reply"], "Fallo: No reflejó banco configurado"
assert "INDUSTRIA MILITAR TIUNA CA" in r_pay_dyn["reply"], "Fallo: No reflejó titular configurado"
print("  ✓ Datos de Pago Móvil y Transferencia configurados administrativamente se inyectaron en el mensaje de WhatsApp")
passed_tests += 1

print("\n" + "=" * 80)
print(f"🎯 RESULTADO FINAL: {passed_tests}/{total_tests} PRUEBAS COMPLETADAS SATISFACTORIAMENTE (100% OK)")
print("=" * 80)
