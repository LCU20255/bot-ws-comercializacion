"""
Prueba integral de:
1. Nuevo Branding: "Complejo Industrial Tiuna — Equipo de Comercialización" y "Soy SIS-COMER, tu asistente virtual"
2. Solicitud de producto sin stock / lista de espera
3. Mensaje de confirmación: "Perfecto, nos estaremos comunicando con usted cuando el producto que requiera esté disponible"
4. Disparo automático de notificación al ingresar nuevo stock
"""
import unittest
from datetime import datetime
from app.database import (
    init_db, get_products, create_product, update_product,
    add_to_waitlist, get_waitlist, get_pending_waitlist_for_product,
    mark_waitlist_notified, add_stock_batch
)
from app.bot_flow import bot_manager, reset_session
from app.whatsapp_service import notify_waitlist_stock_available

class TestWaitlistAndBranding(unittest.TestCase):
    def setUp(self):
        init_db()

    def test_branding_in_welcome_menu(self):
        """Verifica el nuevo saludo institucional exacto"""
        phone = "+584128887766"
        reset_session(phone)
        res = bot_manager.process_message(phone, "Hola")
        
        reply = res["reply"]
        self.assertIn("Soy *SIS-COMER*, tu asistente virtual", reply)
        self.assertIn("Complejo Industrial Tiuna — Equipo de Comercialización", reply)
        self.assertNotIn("intendencia militar", reply.lower())
        print("  -> [OK] Saludo institucional exacto validado correctamente.")

    def test_out_of_stock_triggers_waitlist_offer(self):
        """Verifica que si un producto tiene stock = 0, el bot ofrezca la lista de espera"""
        phone = "+584147778899"
        reset_session(phone)

        # Crear o buscar producto con stock 0
        prods = get_products(only_active=False)
        test_prod = None
        for p in prods:
            if p["stock"] == 0:
                test_prod = p
                break
        
        if not test_prod:
            pid = create_product({
                "name": "CAMISA CORPORATIVA TIUNA",
                "price": 25.0,
                "price_display": "$25.00 Ref",
                "category": "TEXTIL",
                "requires_size": 1,
                "stock": 0,
                "image_url": "/static/images/placeholder.png",
                "description": "Camisa corporativa sin stock",
                "keywords": "camisa, corporativa",
                "updated_by": "ADMIN"
            })
            test_prod = {"id": pid, "name": "CAMISA CORPORATIVA TIUNA", "stock": 0}

        # Cliente busca el producto agotado
        r_search = bot_manager.process_message(phone, test_prod["name"])
        self.assertEqual(r_search["state"], "WAITLIST_CONFIRM")
        self.assertIn("AGOTADO / SIN STOCK", r_search["reply"])
        self.assertIn("Sí, avisarme cuando esté disponible", r_search["reply"])

        # Cliente responde que sí desea que le avisen
        r_yes = bot_manager.process_message(phone, "1")
        self.assertEqual(r_yes["state"], "WAITLIST_NAME")

        # Cliente proporciona su nombre
        r_name = bot_manager.process_message(phone, "Mayor Fernando Lugo")
        self.assertIn("Nos estaremos comunicando con usted cuando el producto que requiera esté disponible", r_name["reply"])
        self.assertIn("Complejo Industrial Tiuna — Equipo de Comercialización", r_name["reply"])

        # Verificar que se guardó en BD en estado PENDIENTE
        pending = get_pending_waitlist_for_product(test_prod["id"], test_prod["name"])
        matching = [w for w in pending if w["phone"] == phone]
        self.assertTrue(len(matching) > 0, "Debe existir registro en lista de espera")
        print("  -> [OK] Flujo de registro en lista de espera completado con el mensaje solicitado.")

    def test_automatic_notification_when_stock_replenished(self):
        """Verifica que al reponer stock el sistema notifique automáticamente al cliente"""
        phone = "+584123332211"
        client_name = "Capitan Maria Rodriguez"
        prod_name = "CHALECO REFLECTIVO DE SEGURIDAD"

        # Registrar cliente en waitlist
        wid = add_to_waitlist(phone, client_name, prod_name)
        pending_before = get_pending_waitlist_for_product(None, prod_name)
        self.assertTrue(any(w["id"] == wid for w in pending_before))

        # Notificar por reposición de stock (simulando ingreso de lote)
        notified = notify_waitlist_stock_available(product_id=0, product_name=prod_name)
        self.assertGreaterEqual(notified, 1, "Debe haber notificado al menos al cliente registrado")

        # Verificar que pasó a NOTIFICADO
        all_wl = get_waitlist()
        updated_item = next((w for w in all_wl if w["id"] == wid), None)
        self.assertIsNotNone(updated_item)
        self.assertEqual(updated_item["status"], "NOTIFICADO")
        self.assertIsNotNone(updated_item["notified_at"])
        print("  -> [OK] Notificación automática disparada y registrada como NOTIFICADO con éxito.")

if __name__ == "__main__":
    unittest.main()
