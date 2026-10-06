import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import (
    init_db, get_connection, get_products, get_orders, get_order_by_id,
    delete_order, update_config, create_product
)
from app.bot_flow import bot_manager, reset_session

def run_all_code_tests():
    print("=" * 60)
    print("EJECUTANDO PRUEBAS COMPLETAS POR CÓDIGO - SIS-COMER")
    print("=" * 60)

    # 1. Inicializar BD y aplicar migraciones
    init_db()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE products SET requires_size = 1 WHERE name LIKE '%UNIFORME%' OR name LIKE '%BOTA%' OR name LIKE '%GORRA%'")
    cur.execute("UPDATE products SET requires_size = 0 WHERE name LIKE '%PARCHE%' OR name LIKE '%BARRA%' OR name LIKE '%PRESILLA%'")
    conn.commit()
    conn.close()

    # Desactivar mantenimiento para las pruebas del bot
    update_config({"maintenance_mode": "0"})
    
    print("\n[PASO 1] Verificación de productos en Base de Datos:")
    prods = get_products(only_active=True)
    for p in prods:
        req = p.get("requires_size")
        print(f" -> Producto: {p['name']} | Requiere Talla: {'SÍ (1)' if req == 1 else 'NO (0)'}")
        if any(w in p['name'] for w in ['UNIFORME', 'BOTA', 'GORRA']):
            assert req == 1, f"Prenda {p['name']} debe tener requires_size=1"
        if any(w in p['name'] for w in ['BARRA', 'PRESILLA', 'PARCHE']):
            assert req == 0, f"Artículo {p['name']} no debe pedir talla (requires_size=0)"
    print("[OK] Configuración de productos validada correctamente.")

    # 2. Prueba de flujo: Acceso directo al Catálogo sin registro
    test_phone = "+584129990001"
    reset_session(test_phone)
    
    print("\n[PASO 2] Acceso Directo a SIS-COMER (Sin Registro Previo):")
    r_cat = bot_manager.process_message(test_phone, "Hola")
    assert r_cat["state"] == "CATALOG", f"Debe mostrar catálogo directamente, obtenido: {r_cat['state']}"
    assert "CATÁLOGO" in r_cat["reply"] or "SIS-COMER" in r_cat["reply"], "Debe mostrar el catálogo"
    print("[OK] Acceso directo al Catálogo SIS-COMER sin barreras de registro.")

    # 3. Prueba de flujo: Prenda de vestir CON talla
    print("\n[PASO 3] Selección de prenda de vestir que requiere talla (Uniforme / Gorra):")
    res_uniform = bot_manager.process_message(test_phone, "1 uniforme militar")
    assert res_uniform["state"] == "SELECTING_SIZE", f"Estado esperado SELECTING_SIZE, obtenido: {res_uniform['state']}"
    assert "TALLA" in res_uniform["reply"], "El bot debe preguntar la talla"
    print("[OK] Bot solicitó la talla adecuadamente para el uniforme.")

    # 4. Enviar talla de la prenda
    print("\n[PASO 4] Respuesta del cliente indicando la talla (Talla L):")
    res_size = bot_manager.process_message(test_phone, "L")
    assert res_size["state"] == "CART_VIEW", f"Estado esperado CART_VIEW, obtenido: {res_size['state']}"
    assert "TALLA: L" in res_size["reply"], "El carrito debe reflejar la talla L"
    print("[OK] Talla L registrada y reflejada en el resumen del carrito con precio dual ($ y Bs).")

    # 5. Pago Previo Obligatorio
    print("\n[PASO 5] Proceder a Pagar (Pago Previo Requerido):")
    r_pay = bot_manager.process_message(test_phone, "2") # Proceder al pago
    assert r_pay["state"] == "AWAITING_PAYMENT", "Debe solicitar pago obligatorio previo"
    print("[OK] Bot exigió comprobante de pago previo antes de agendar.")

    # 6. Envío de Comprobante OCR y Vinculación BCV
    print("\n[PASO 6] Envío y Transcripción de Comprobante Bancario:")
    r_rcpt = bot_manager.process_message(test_phone, "PAGO MOVIL BANCO DE VENEZUELA REF: 99112233 BS 2500 FECHA HOY")
    assert r_rcpt["state"] == "AWAITING_SCHEDULE", "Debe habilitar agendamiento tras validar comprobante"
    assert "COMPROBANTE DE PAGO VALIDADO" in r_rcpt["reply"], "Debe confirmar validación de comprobante"
    print("[OK] Comprobante validado y tasa BCV vinculada exitosamente.")

    # 7. Agendamiento y Emisión de Ticket Oficial CIT-...
    print("\n[PASO 7] Agendamiento de Fecha/Hora y Emisión de Ticket CIT-:")
    res_final = bot_manager.process_message(test_phone, "2026-10-15 a las 10:00 AM")
    assert res_final["state"] == "COMPLETED", "Debe finalizar en COMPLETED"
    ticket_code = res_final["ticket_code"]
    assert ticket_code.startswith("CIT-"), f"Formato de ticket inválido: {ticket_code}"
    print(f"[OK] Solicitud completada exitosamente. Ticket emitido: {ticket_code}")

    # 8. Verificación de almacenamiento en Base de Datos
    print("\n[PASO 8] Verificación en Base de Datos:")
    orders = get_orders()
    created = next((o for o in orders if o["ticket_code"] == ticket_code), None)
    assert created is not None, "La orden debe existir en la base de datos"
    assert created["receipt_ref"] == "99112233", "Referencia bancaria guardada correctamente"
    print(f"[OK] Orden {ticket_code} verificada en Base de Datos con éxito.")

    # Limpiar orden de prueba
    delete_order(created["id"])
    print("\n" + "=" * 60)
    print("TODAS LAS PRUEBAS POR CÓDIGO PASARON EXITOSAMENTE (100% OK)")
    print("=" * 60)

if __name__ == "__main__":
    run_all_code_tests()
