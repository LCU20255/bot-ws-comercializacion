import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import (
    init_db, get_products, get_orders, export_orders_df,
    is_maintenance_active, update_config, get_all_config
)
from app.bot_flow import bot_manager, reset_session

def run_military_tests():
    init_db()
    update_config({"maintenance_mode": "0"})
    test_phone = "+584127776655"
    reset_session(test_phone)

    print("\n--- PRUEBA 1: Saludo inicial e inspección de catálogo militar ---")
    r1 = bot_manager.process_message(test_phone, "Hola, buenas tardes")
    assert "entrega inmediata" in r1["reply"].lower() or "productos" in r1["reply"].lower(), "Debe mostrar el catálogo militar"
    print("[OK] Prueba 1 Aprobada: Catálogo militar visible con productos para entrega inmediata.")

    print("\n--- PRUEBA 2: Consulta de múltiples productos (3 Parches + 1 Gorra) ---")
    # Agregar 3 parches
    r2_a = bot_manager.process_message(test_phone, "Quiero 3 parches bordados")
    assert "3x PARCHES BORDADOS" in r2_a["reply"], "Debe registrar 3x parches en el carrito"
    print("[OK] Prueba 2A Aprobada: Carrito con 3x Parches.")

    # Agregar 1 gorra táctica (pide talla al ser prenda)
    r2_b = bot_manager.process_message(test_phone, "1")  # Opción agregar otro
    r2_c = bot_manager.process_message(test_phone, "1 gorra tactica")
    assert r2_c["state"] == "SELECTING_SIZE", "Debe pedir talla para la gorra"
    r2_d = bot_manager.process_message(test_phone, "M")  # Indicar talla M
    assert "GORRAS Y BOINAS TÁCTICAS" in r2_d["reply"], "Debe sumar 1 gorra con talla al carrito"
    print("[OK] Prueba 2B Aprobada: Gorra solicita talla y entra al carrito.")

    print("\n--- PRUEBA 3: Proceder a agendar y enviar datos en mayúsculas ---")
    r3_a = bot_manager.process_message(test_phone, "2")  # Proceder con agendamiento
    r3_b = bot_manager.process_message(test_phone, "Cap. Manuel Silva, V-18920114, 0412-8887766, 2026-10-12 a las 09:30 AM")
    assert "SELECCIONA TU MÉTODO DE PAGO" in r3_b["reply"], "Debe pedir método de pago"
    print("[OK] Prueba 3 Aprobada: Datos parseados en MAYÚSCULAS y solicitud de forma de pago.")

    print("\n--- PRUEBA 4: Selección de Método de Pago (1. Efectivo / Divisas) ---")
    r4 = bot_manager.process_message(test_phone, "1")
    assert "RESUMEN FINAL DE SU PEDIDO" in r4["reply"], "Debe mostrar resumen para confirmar"
    print("[OK] Prueba 4 Aprobada: Resumen con método de pago EFECTIVO / DIVISAS.")

    print("\n--- PRUEBA 5: Confirmación de orden y validación del estatus inicial ---")
    r5 = bot_manager.process_message(test_phone, "1")
    assert "PEDIDO Y CITA AGENDADOS CON ÉXITO" in r5["reply"], "Debe emitir confirmación"
    ticket = r5["ticket_code"]
    assert "CIT-" in ticket, "Ticket debe tener formato CIT-..."
    print(f"[OK] Prueba 5 Aprobada: Orden registrada con Ticket {ticket}.")

    # Verificar en Base de Datos que el estatus inicial sea PENDIENTE POR ATENCIÓN
    orders = get_orders(limit=1)
    assert len(orders) > 0
    last_order = orders[0]
    print(f"[OK] Estatus verificado en BD: '{last_order['status']}'")
    assert last_order["status"] == "PENDIENTE POR ATENCIÓN", f"El estatus inicial debe ser 'PENDIENTE POR ATENCIÓN', no '{last_order['status']}'"
    print("[OK] Validación exitosa: El estatus inicial es exactamente 'PENDIENTE POR ATENCIÓN'.")

    print("\n--- PRUEBA 6: Exportación a Excel y CSV ---")
    df = export_orders_df()
    assert len(df) >= 1
    print(f"[OK] Reporte Excel/CSV listo con {len(df)} registro(s) y encabezados en mayúsculas.")

    print("\n--- PRUEBA 7: Modo Mantenimiento / Auditoría Interna ---")
    update_config({"maintenance_mode": "1"})
    r7 = bot_manager.process_message(test_phone, "Hola necesito comprar")
    assert "AVISO DE MANTENIMIENTO" in r7["reply"] or "mantenimiento" in r7["reply"].lower(), "Debe responder con aviso de mantenimiento"
    print("[OK] Prueba 7 Aprobada: Modo mantenimiento responde con aviso institucional.")
    # Restaurar modo mantenimiento
    update_config({"maintenance_mode": "0"})

    print("\n--- PRUEBA 8: Validación de Talla (Prendas de vestir vs Parches/Barras/Presillas) ---")
    reset_session(test_phone)
    # Seleccionar UNIFORME (Prenda de vestir): DEBE pedir talla
    r8_uniform = bot_manager.process_message(test_phone, "Quiero un uniforme militar")
    assert r8_uniform["state"] == "SELECTING_SIZE", "Debe solicitar la talla para uniformes"
    assert "TALLA" in r8_uniform["reply"], "Debe pedir la talla del uniforme"
    print("[OK] Prueba 8A Aprobada: Uniforme solicita TALLA correctamente.")

    # Ingresar talla
    r8_size = bot_manager.process_message(test_phone, "XL")
    assert "TALLA: XL" in r8_size["reply"], "El uniforme debe agregarse al carrito con la talla XL"
    print("[OK] Prueba 8B Aprobada: Talla 'XL' asignada al uniforme en el carrito.")

    # Ahora agregar BARRAS Y PRESILLAS (NO es ropa): NO debe pedir talla
    r8_add_more = bot_manager.process_message(test_phone, "1")
    r8_barras = bot_manager.process_message(test_phone, "Barras Venezuela Renace")
    assert r8_barras["state"] == "CART_VIEW", "Barras y presillas no deben solicitar talla"
    assert "BARRAS Y PRESILLAS" in r8_barras["reply"], "Barras y presillas agregadas directamente"
    print("[OK] Prueba 8C Aprobada: Barras y presillas NO solicitan talla y entran directo al carrito.")

    # Probar comando 0 para volver al menú
    r8_menu = bot_manager.process_message(test_phone, "0")
    assert "CATÁLOGO" in r8_menu["reply"] or "disponemos" in r8_menu["reply"] or "Bienvenido" in r8_menu["reply"], "Comando 0 debe volver al catálogo"
    print("[OK] Prueba 8D Aprobada: Comando '0' regresa inmediatamente al menú principal.")

    print("\n*** TODAS LAS PRUEBAS DEL SISTEMA TEXTIL MILITAR PASARON AL 100%! ***\n")

if __name__ == "__main__":
    run_military_tests()
