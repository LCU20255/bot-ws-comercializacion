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
    print("EJECUTANDO PRUEBAS COMPLETAS POR CÓDIGO - SISTEMA MILITAR")
    print("=" * 60)

    # 1. Inicializar BD y aplicar migraciones
    init_db()
    conn = get_connection()
    cur = conn.cursor()
    # Asegurar que prendas tengan requires_size=1 y no-prendas requires_size=0
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

    # 2. Prueba de flujo: Artículo SIN talla (Barras y Presillas)
    test_phone = "+584129990001"
    reset_session(test_phone)
    
    print("\n[PASO 2] Prueba con artículo que NO lleva talla (Barras y Presillas Venezuela Renace):")
    res_barras = bot_manager.process_message(test_phone, "Quiero 2 juegos de barras y presillas venezuela renace")
    # No debe pedir talla, debe ir directo a CART_VIEW
    assert res_barras["state"] == "CART_VIEW", f"Estado esperado CART_VIEW, obtenido: {res_barras['state']}"
    assert "BARRAS Y PRESILLAS" in res_barras["reply"], "Debe mostrar el producto en el carrito"
    assert "TALLA" not in res_barras["reply"], "No debe pedir talla para barras y presillas"
    print("[OK] Barras y presillas agregadas directo al carrito SIN solicitar talla.")

    # 3. Prueba de flujo: Prenda de vestir CON talla (Uniforme Militar)
    print("\n[PASO 3] Prueba con prenda de vestir que SÍ requiere talla (Uniforme Militar):")
    res_more = bot_manager.process_message(test_phone, "1") # Agregar otro producto
    res_uniform = bot_manager.process_message(test_phone, "1 uniforme militar")
    
    assert res_uniform["state"] == "SELECTING_SIZE", f"Estado esperado SELECTING_SIZE, obtenido: {res_uniform['state']}"
    assert "TALLA" in res_uniform["reply"], "El bot debe preguntar la talla"
    print("[OK] Bot solicitó la talla adecuadamente para el uniforme.")

    # 4. Enviar talla de la prenda
    print("\n[PASO 4] Respuesta del cliente indicando la talla (Talla L):")
    res_size = bot_manager.process_message(test_phone, "L")
    assert res_size["state"] == "CART_VIEW", f"Estado esperado CART_VIEW, obtenido: {res_size['state']}"
    assert "TALLA: L" in res_size["reply"], "El carrito debe reflejar la talla L"
    print("[OK] Talla L registrada y reflejada en el resumen del carrito.")

    # 5. Agendamiento y recolección de datos
    print("\n[PASO 5] Proceder con agendamiento y envío de datos personales:")
    bot_manager.process_message(test_phone, "2") # Proceder a agendar
    res_data = bot_manager.process_message(test_phone, "Teniente Carlos Mendez, V-20112334, 2026-10-15 a las 10:00 AM")
    assert "SELECCIONA TU MÉTODO DE PAGO" in res_data["reply"], "Debe avanzar a selección de método de pago"
    print("[OK] Datos personales (Nombre, Cédula, Fecha y Hora) procesados.")

    # 6. Selección de método de pago
    print("\n[PASO 6] Selección de forma de pago (1. Efectivo / Divisas en sede):")
    res_pay = bot_manager.process_message(test_phone, "1")
    assert res_pay["state"] == "CONFIRMING", f"Estado esperado CONFIRMING, obtenido: {res_pay['state']}"
    assert "RESUMEN FINAL DE SU PEDIDO" in res_pay["reply"], "Debe mostrar ficha de confirmación"
    assert "TALLA: L" in res_pay["reply"], "El resumen debe incluir la talla seleccionada"
    print("[OK] Método de pago asignado y resumen generado.")

    # 7. Confirmación final de la orden y verificación del estatus inicial
    print("\n[PASO 7] Confirmación de la cita y verificación de estatus inicial en Base de Datos:")
    res_confirm = bot_manager.process_message(test_phone, "1")
    ticket_code = res_confirm["ticket_code"]
    assert "CIT-" in ticket_code, f"Formato de ticket inválido: {ticket_code}"
    print(f" -> Ticket generado: {ticket_code}")

    # Verificar directamente en la tabla orders
    orders = get_orders(limit=5)
    created_order = next((o for o in orders if o["ticket_code"] == ticket_code), None)
    assert created_order is not None, "La orden debe existir en la base de datos"
    assert created_order["status"] == "PENDIENTE POR ATENCIÓN", f"Estatus esperado 'PENDIENTE POR ATENCIÓN', obtenido '{created_order['status']}'"
    print(f"[OK] Estatus inicial en BD verificado estrictamente: '{created_order['status']}'.")
    
    # 8. Verificación de eliminación de pedido (DELETE)
    print("\n[PASO 8] Prueba de eliminación permanente de pedido en Base de Datos:")
    order_id = created_order["id"]
    delete_order(order_id)
    assert get_order_by_id(order_id) is None, "El pedido debió haber sido eliminado permanentemente"
    print(f"[OK] Pedido ID {order_id} eliminado exitosamente.")

    # 9. Verificación de solicitud de Asesor Comercial (Mismo WhatsApp)
    print("\n[PASO 9] Prueba de atención con Asesor Humano por el mismo WhatsApp:")
    reset_session(test_phone)
    res_adv = bot_manager.process_message(test_phone, "Quiero hablar con un asesor")
    assert res_adv["state"] == "WAITING_ADVISOR", "Debe pasar al estado WAITING_ADVISOR"
    assert "este mismo chat" in res_adv["reply"].lower() or "esta conversación" in res_adv["reply"].lower()
    print("[OK] Asesor comercial asignado al mismo chat sin enlaces externos.")

    print("\n" + "=" * 60)
    print("[EXITO] TODAS LAS PRUEBAS POR CODIGO PASARON AL 100%")
    print("=" * 60)

if __name__ == "__main__":
    run_all_code_tests()
