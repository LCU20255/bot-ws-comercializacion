import unittest
import os
import tempfile
from datetime import datetime, timedelta
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
from app.bot_flow import bot_manager, reset_session

class TestSisComerSystem(unittest.TestCase):
    def setUp(self):
        init_db()

    def test_initial_registration_flow(self):
        """Prueba que el flujo inicie obligatoriamente solicitando Nombre, Cédula y Teléfono"""
        phone = "+584125556677"
        reset_session(phone, keep_registration=False)

        # 1. Saludo inicial debe pedir nombre y apellido
        r1 = bot_manager.process_message(phone, "Hola")
        self.assertIn("NOMBRE Y APELLIDO COMPLETO", r1["reply"])
        self.assertEqual(r1["state"], "REGISTER_NAME")

        # 2. Enviar nombre debe solicitar cédula
        r2 = bot_manager.process_message(phone, "Teniente Jose Gregorio Hernandez")
        self.assertIn("CÉDULA DE IDENTIDAD", r2["reply"])
        self.assertEqual(r2["state"], "REGISTER_CEDULA")

        # 3. Enviar cédula debe solicitar teléfono de contacto directo
        r3 = bot_manager.process_message(phone, "V-17890123")
        self.assertIn("TELÉFONO DE CONTACTO", r3["reply"])
        self.assertEqual(r3["state"], "REGISTER_PHONE")

        # 4. Enviar teléfono debe culminar registro y mostrar catálogo oficial SIS-COMER
        r4 = bot_manager.process_message(phone, "0412-9876543")
        self.assertIn("SIS-COMER", r4["reply"])
        self.assertIn("PRODUCTOS DISPONIBLES", r4["reply"])
        self.assertEqual(r4["state"], "CATALOG")

    def test_catalog_excludes_zero_stock(self):
        """Garantiza que productos con stock = 0 no aparezcan en el catálogo de WhatsApp"""
        prods = get_products()
        if not prods:
            return
        target_id = prods[0]["id"]
        original_stock = prods[0]["stock"]

        conn = get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE products SET stock = 0 WHERE id = ?", (target_id,))
        conn.commit()
        conn.close()

        avail = get_available_catalog_products()
        ids_avail = [p["id"] for p in avail]
        self.assertNotIn(target_id, ids_avail)

        # Restaurar stock
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE products SET stock = ? WHERE id = ?", (original_stock, target_id))
        conn.commit()
        conn.close()

    def test_ocr_receipt_and_file_purge(self):
        """Valida la extracción OCR y que el archivo temporal sea destruido físicamente de inmediato"""
        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        tmp.write(b"MOCK_IMAGE_DATA_RECEIPT")
        tmp.close()
        
        self.assertTrue(os.path.exists(tmp.name))

        hint = "BANCO DE VENEZUELA PAGO MOVIL APROBADO REF: 00987654 MONTO: BS. 2.450,00 FECHA: 05/10/2026"
        res = ReceiptOCRService.process_and_destroy_receipt(tmp.name, simulated_hint_text=hint)

        self.assertEqual(res["bank"], "BANCO DE VENEZUELA")
        self.assertEqual(res["reference"], "00987654")
        self.assertEqual(res["amount"], 2450.0)
        self.assertEqual(res["currency"], "VES")
        self.assertEqual(res["payment_date"], "2026-10-05")

        # Verificar destrucción inmediata en disco
        self.assertFalse(os.path.exists(tmp.name))

    def test_historical_bcv_rate_binding(self):
        """Prueba que si un comprobante dice fecha anterior (ayer), se vincule la tasa de esa fecha"""
        yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        yesterday_rate = 850.50
        bcv_service.save_rate(yesterday_str, yesterday_rate)

        rate_obtained = bcv_service.get_rate_for_date(yesterday_str)
        self.assertEqual(rate_obtained, yesterday_rate)

    def test_low_stock_and_kardex_logging(self):
        """Prueba que el Kardex audite ENTRADA_TALLER y la alerta de stock <= 20"""
        prods = get_products()
        if not prods:
            return
        target_id = prods[0]["id"]

        add_stock_batch(product_id=target_id, quantity=15, notes="Lote prueba taller", created_by="TEST_TALLER")
        
        movements = get_inventory_movements(limit=5)
        self.assertTrue(len(movements) > 0)
        self.assertTrue(any(m["movement_type"] == "ENTRADA_TALLER" for m in movements))

        # Forzar stock a 15 y verificar alerta
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE products SET stock = 15 WHERE id = ?", (target_id,))
        conn.commit()
        conn.close()

        low_stock = get_low_stock_products(threshold=20)
        low_ids = [p["id"] for p in low_stock]
        self.assertIn(target_id, low_ids)

    def test_full_order_payment_and_ticket_generation(self):
        """Prueba el ciclo completo de orden: registro, selección, pago verificado por OCR y agendamiento"""
        phone = "+584147778899"
        reset_session(phone, keep_registration=False)

        # Registro
        bot_manager.process_message(phone, "Mayor Rafael Urdaneta")
        bot_manager.process_message(phone, "V-12345678")
        bot_manager.process_message(phone, "0414-7778899")

        # Selección producto 1
        r_prod = bot_manager.process_message(phone, "1")
        if r_prod["state"] == "SELECTING_SIZE":
            bot_manager.process_message(phone, "XL")

        # Proceder a pagar
        r_pay = bot_manager.process_message(phone, "2")
        self.assertEqual(r_pay["state"], "AWAITING_PAYMENT")

        # Enviar comprobante
        r_receipt = bot_manager.process_message(phone, "PAGO MOVIL BANCO PROVINCIAL REF: 44556677 MONTO: BS 1800 FECHA HOY")
        self.assertEqual(r_receipt["state"], "AWAITING_SCHEDULE")
        self.assertIn("COMPROBANTE DE PAGO VALIDADO", r_receipt["reply"])

        # Agendar cita
        r_final = bot_manager.process_message(phone, "Mañana a las 09:00 AM")
        self.assertEqual(r_final["state"], "COMPLETED")
        self.assertIn("CIT-", r_final["ticket_code"])
        self.assertIn("SOLICITUD Y PAGO CONFIRMADOS CON ÉXITO", r_final["reply"])

if __name__ == "__main__":
    unittest.main()
