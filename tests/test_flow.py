import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import init_db, get_products, get_appointments, export_appointments_df
from app.bot_flow import bot_manager, reset_session

def run_tests():
    init_db()
    test_phone = "+584129998877"
    reset_session(test_phone)

    print("\n--- TEST 1: Saludo inicial ---")
    r1 = bot_manager.process_message(test_phone, "Hola, buenas tardes")
    assert "Catálogo de Productos" in r1["reply"], "El saludo inicial debe mostrar el catálogo"
    print("[OK] Test 1 Aprobado: Catalogo y opciones mostradas correctamente.")

    print("\n--- TEST 2: Pregunta por producto libre (Uniformes) ---")
    r2 = bot_manager.process_message(test_phone, "Buenos días, quisiera saber cuándo puedo retirar los uniformes")
    assert "Uniformes" in r2["reply"], "El bot debe detectar los uniformes en lenguaje natural"
    print("[OK] Test 2 Aprobado: Deteccion inteligente de producto y solicitud de datos.")

    print("\n--- TEST 3: Envío de datos completos en un solo bloque ---")
    r3 = bot_manager.process_message(test_phone, "Carlos Rodriguez, V-24123456, mañana a las 10:30 AM")
    assert "Carlos Rodriguez" in r3["reply"] and "V-24123456" in r3["reply"], "Debe parsear nombre, cédula y fecha/hora"
    print("[OK] Test 3 Aprobado: Parseo de datos completos (Nombre, Cedula, Fecha/Hora).")

    print("\n--- TEST 4: Confirmación de cita ---")
    r4 = bot_manager.process_message(test_phone, "1")
    assert "¡CITA AGENDADA CON ÉXITO!" in r4["reply"] or "CITA AGENDADA" in r4["reply"], "Debe emitir confirmación y ticket"
    assert "CIT-" in r4["ticket_code"], "Debe generar código de ticket"
    print(f"[OK] Test 4 Aprobado: Ticket generado exitosamente -> {r4['ticket_code']}.")

    print("\n--- TEST 5: Consulta a Base de Datos y Exportación Excel/CSV ---")
    citas = get_appointments()
    assert len(citas) >= 1, "Debe haber al menos 1 cita en la base de datos"
    c = citas[0]
    print(f"[OK] Cita en BD: Ticket={c['ticket_code']}, Cliente={c['client_name']}, CI={c['cedula']}")

    df = export_appointments_df()
    assert len(df) >= 1, "El dataframe de exportación debe contener registros"
    print(f"[OK] Test 5 Aprobado: Exportacion a Excel/CSV lista con {len(df)} registro(s).")

    print("\n--- TEST 6: Asesor Comercial ---")
    reset_session(test_phone)
    r6 = bot_manager.process_message(test_phone, "Quiero hablar con un asesor")
    assert "wa.me" in r6["reply"], "Debe retornar enlace directo de WhatsApp al asesor"
    print("[OK] Test 6 Aprobado: Enlace directo al asesor comercial generado.")

    print("\n*** TODAS LAS PRUEBAS UNITARIAS Y DE FLUJO FUERON EXITOSAS! ***")

if __name__ == "__main__":
    run_tests()
