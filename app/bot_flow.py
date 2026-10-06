import logging
import re
import os
from typing import Dict, Any, List, Optional
from datetime import datetime
from pathlib import Path

from app.nlu_engine import nlu
from app.bcv_service import bcv_service
from app.ocr_service import ReceiptOCRService
from app.database import (
    get_products,
    get_available_catalog_products,
    get_product_by_id,
    create_order,
    get_all_config,
    is_maintenance_active,
    is_within_business_hours,
    add_to_waitlist
)

from app.time_utils import now_vet, now_vet_date_str, now_vet_str, format_date_dmy, format_datetime_dmy

logger = logging.getLogger(__name__)

# Memoria de sesiones de usuario activas
user_sessions: Dict[str, Dict[str, Any]] = {}

def get_session(phone: str) -> Dict[str, Any]:
    if phone not in user_sessions:
        user_sessions[phone] = {
            "state": "CATALOG",        # Inicia directamente en el catálogo sin registro previo obligatorio
            "cart": [],                # [{"product_id", "name", "qty", "unit_price", "subtotal", "size"}]
            "client_name": None,       # MAYÚSCULAS
            "cedula": None,            # MAYÚSCULAS
            "contact_phone": None,     # Teléfono real de contacto (0412-1234567)
            "phone": phone,            # JID / ID de WhatsApp para envío
            "receipt_ref": None,
            "receipt_bank": None,
            "receipt_date": None,
            "bcv_rate_applied": 0.0,
            "amount_usd": 0.0,
            "amount_ves": 0.0,
            "ocr_raw_text": None,
            "pickup_date": None,       # YYYY-MM-DD
            "pickup_time": None,       # HH:MM AM/PM
            "payment_method": "PAGO MÓVIL / TRANSFERENCIA",
            "is_off_hours": 0,
            "last_interaction": now_vet()
        }
    return user_sessions[phone]

def reset_session(phone: str, keep_registration: bool = True):
    existing = user_sessions.get(phone, {})
    user_sessions[phone] = {
        "state": "CATALOG",
        "cart": [],
        "client_name": existing.get("client_name") if keep_registration else None,
        "cedula": existing.get("cedula") if keep_registration else None,
        "contact_phone": existing.get("contact_phone") if keep_registration else None,
        "phone": phone,
        "receipt_ref": None,
        "receipt_bank": None,
        "receipt_date": None,
        "bcv_rate_applied": 0.0,
        "amount_usd": 0.0,
        "amount_ves": 0.0,
        "ocr_raw_text": None,
        "pickup_date": None,
        "pickup_time": None,
        "payment_method": "PAGO MÓVIL / TRANSFERENCIA",
        "is_off_hours": 0,
        "last_interaction": datetime.now()
    }

class BotFlowManager:
    """
    Gestor del flujo conversacional para SIS-COMER (Complejo Industrial Tiuna).
    Flujo:
    1. Acceso directo al Catálogo Oficial (sin barreras de registro inicial).
    2. Catálogo dinámico exclusivo con Stock > 0 (con tallas si aplica y precios duales $ / Bs BCV).
    3. Carrito y cálculo de totales ($ y Bs).
    4. Pago previo obligatorio: Envío de comprobante de pago por imagen/texto.
    5. Procesamiento OCR del recibo, vinculación con tasa BCV histórica y purga de imagen temporal.
    6. Agendamiento de retiro (fecha y hora) post-pago y emisión de ticket CIT-YYMMDD-XXX.
    """

    def process_message(self, phone: str, text: str) -> Dict[str, Any]:
        clean_text = text.strip()
        session = get_session(phone)
        session["last_interaction"] = datetime.now()
        current_state = session["state"]

        config = get_all_config()
        advisor_name = config.get("advisor_name", "ASESOR COMERCIAL")
        pickup_address = config.get("pickup_address", "SEDE DE INTENDENCIA - COMPLEJO INDUSTRIAL TIUNA")
        pickup_hours = config.get("pickup_hours", "LUNES A VIERNES DE 8:00 AM A 5:00 PM")

        # 1. VERIFICAR MODO MANTENIMIENTO
        if is_maintenance_active():
            maint_msg = config.get(
                "maintenance_message",
                "¡Hola! En este momento nos encontramos en proceso de mantenimiento. Por favor comunícate con nosotros el día de mañana de 8:00 AM a 5:00 PM."
            )
            return {
                "reply": f"🛑 *SIS-COMER: AVISO DE MANTENIMIENTO*\n\n{maint_msg}",
                "image_url": None,
                "state": "MAINTENANCE"
            }

        # 2. HORARIO LABORAL
        is_off_hours = not is_within_business_hours()
        if is_off_hours:
            session["is_off_hours"] = 1

        # Extracción automática de datos del cliente (Nombre, Cédula, Teléfono) si se mencionan en el mensaje
        self._try_extract_all_registration_data(clean_text, session, phone)

        # Comandos globales de reinicio o volver al menú
        if clean_text.lower() in ["0", "menu", "menú", "inicio", "empezar", "reset", "cancelar"]:
            reset_session(phone, keep_registration=True)
            return self._build_catalog_menu(session, is_off_hours=is_off_hours)

        # Comando de Asesor Comercial
        if clean_text.lower() in ["asesor", "humano", "asesoria", "asesoría", "ayuda"]:
            return self._build_advisor_response(phone, advisor_name)

        # Analizar intención con NLU
        analysis = nlu.analyze_message(clean_text, current_state=current_state)
        matched_product = analysis["matched_product"]
        extracted = analysis["extracted_data"]

        # 3. DETECCIÓN INTELIGENTE DE INCONFORMIDAD / QUEJAS / MENSAJES NEGATIVOS
        if analysis["intent"] == "NEGATIVE_SENTIMENT":
            adv_phone = config.get("advisor_phone") or config.get("pagomovil_phone") or "0412-1234567"
            clean_digits = re.sub(r'\D', '', adv_phone)
            wa_digits = f"58{clean_digits[1:]}" if clean_digits.startswith("0") else (clean_digits if clean_digits.startswith("58") else f"58{clean_digits}")
            session["state"] = "WAITING_ADVISOR"
            return {
                "reply": (
                    "🤝 *Lamentamos sinceramente cualquier molestia o inconveniente.*\n\n"
                    "En *SIS-COMER* nos esforzamos por brindarte la mejor experiencia. "
                    "Si no estás satisfecho con la atención automatizada o tienes alguna queja o duda con el proceso, "
                    "puedes comunicarte de inmediato con nuestro asesor comercial humano:\n\n"
                    f"👨‍💼 *Asesor:* {advisor_name}\n"
                    f"📞 *Teléfono:* {adv_phone}\n"
                    f"💬 *WhatsApp directo:* https://wa.me/{wa_digits}?text=Hola%2C%20necesito%20asistencia%20con%20un%20asesor\n\n"
                    "👉 También puedes:\n"
                    "[ 1️⃣ ] Esperar atención de un asesor por este mismo chat\n"
                    "[ 0️⃣ ] Volver al menú principal"
                ),
                "image_url": None,
                "state": "WAITING_ADVISOR"
            }

        # Si el usuario está en espera de asesor
        if current_state == "WAITING_ADVISOR":
            if clean_text in ["0", "menu", "menú"]:
                reset_session(phone, keep_registration=True)
                return self._build_catalog_menu(session, is_off_hours=is_off_hours)
            return {
                "reply": "👍 *Mensaje recibido.*\n\nUn asesor comercial de nuestro equipo atenderá tu consulta por este mismo chat a la brevedad.\n\n*(Escribe 0 si deseas volver al menú de SIS-COMER)*",
                "image_url": None,
                "state": "WAITING_ADVISOR"
            }

        # Si menciona un producto directamente desde cualquier estado que no sea pago o fecha
        if matched_product and current_state in ["CATALOG", "INIT"]:
            if matched_product.get("stock", 0) <= 0:
                session["waitlist_product_name"] = matched_product["name"]
                session["waitlist_product_id"] = matched_product["id"]
                session["state"] = "WAITLIST_CONFIRM"
                return {
                    "reply": (
                        f"⚠️ El artículo *{matched_product['name']}* se encuentra actualmente *AGOTADO / SIN STOCK* en nuestro inventario.\n\n"
                        "¿Desea que le avisemos automáticamente apenas ingrese nuevo stock a nuestro almacén?\n\n"
                        "[ 1️⃣ ] *Sí, avisarme cuando esté disponible*\n"
                        "[ 2️⃣ ] *Ver productos disponibles en catálogo*\n\n"
                        "👉 Responda *1* para anotarse en la lista de espera o *2* para ver el catálogo."
                    ),
                    "image_url": None,
                    "state": "WAITLIST_CONFIRM"
                }
            qty = self._extract_quantity(clean_text)
            if self._product_needs_size(matched_product):
                session["pending_item"] = {"product": matched_product, "qty": qty}
                session["state"] = "SELECTING_SIZE"
                return self._prompt_for_size(matched_product, session)
            else:
                self._add_to_cart(session, matched_product, qty)
                session["state"] = "CART_VIEW"
                return self._build_cart_view(session)

        # -------------------------------------------------------------
        # ESTADO 2: CATÁLOGO DINÁMICO (Stock > 0 y Tasa BCV Oficial)
        # -------------------------------------------------------------
        elif current_state == "CATALOG":
            if analysis["intent"] == "CONNECT_ADVISOR":
                return self._build_advisor_response(phone, advisor_name)

            # Selección por número
            catalog_products = get_available_catalog_products()
            waitlist_idx = len(catalog_products) + 1
            advisor_idx = len(catalog_products) + 2

            if analysis["intent"] == "NUMERIC_OPTION":
                val = analysis.get("value")
                if val and 1 <= val <= len(catalog_products):
                    selected = catalog_products[val - 1]
                    if self._product_needs_size(selected):
                        session["pending_item"] = {"product": selected, "qty": 1}
                        session["state"] = "SELECTING_SIZE"
                        return self._prompt_for_size(selected, session)
                    else:
                        self._add_to_cart(session, selected, qty=1)
                        session["state"] = "CART_VIEW"
                        return self._build_cart_view(session)
                elif val == waitlist_idx:
                    session["state"] = "WAITLIST_PRODUCT"
                    return {
                        "reply": (
                            "📝 *LISTA DE ESPERA Y DISPONIBILIDAD*\n"
                            "*Complejo Industrial Tiuna — Equipo de Comercialización*\n\n"
                            "Indíquenos: *¿Cuál es el producto que está buscando o requiere?*\n"
                            "*(Ejemplo: Chaleco táctico, Chaqueta patriota, Botas, Condecoraciones, etc.)*"
                        ),
                        "image_url": None,
                        "state": "WAITLIST_PRODUCT"
                    }
                elif val == advisor_idx:
                    return self._build_advisor_response(phone, advisor_name)
                elif val == 0:
                    reset_session(phone, keep_registration=True)
                    return self._build_catalog_menu(session, is_off_hours=is_off_hours)
                else:
                    return {
                        "reply": "⚠️ Opción no válida. Por favor seleccione un número de la lista o escriba el nombre del artículo.",
                        "image_url": None,
                        "state": "CATALOG"
                    }

            # Si nombró un producto directamente
            if matched_product:
                # Verificar si el producto coincidente tiene stock <= 0
                if matched_product.get("stock", 0) <= 0:
                    session["waitlist_product_name"] = matched_product["name"]
                    session["waitlist_product_id"] = matched_product["id"]
                    session["state"] = "WAITLIST_CONFIRM"
                    return {
                        "reply": (
                            f"⚠️ El artículo *{matched_product['name']}* se encuentra actualmente *AGOTADO / SIN STOCK* en nuestro inventario.\n\n"
                            "¿Desea que le avisemos automáticamente apenas ingrese nuevo stock a nuestro almacén?\n\n"
                            "1️⃣ *Sí, avisarme cuando esté disponible*\n"
                            "2️⃣ *Ver productos disponibles en catálogo*\n\n"
                            "👉 Responda *1* para anotarse en la lista de espera o *2* para ver el catálogo."
                        ),
                        "image_url": None,
                        "state": "WAITLIST_CONFIRM"
                    }
                qty = self._extract_quantity(clean_text)
                if self._product_needs_size(matched_product):
                    session["pending_item"] = {"product": matched_product, "qty": qty}
                    session["state"] = "SELECTING_SIZE"
                    return self._prompt_for_size(matched_product, session)
                else:
                    self._add_to_cart(session, matched_product, qty)
                    session["state"] = "CART_VIEW"
                    return self._build_cart_view(session)

            # Si escribe texto indicando que no encuentra su producto o busca otro
            if any(w in clean_text.lower() for w in ["no encuentro", "no esta", "no está", "no aparece", "espera", "lista de espera", "otro"]):
                session["state"] = "WAITLIST_PRODUCT"
                return {
                    "reply": (
                        "📝 *LISTA DE ESPERA Y DISPONIBILIDAD*\n"
                        "*Complejo Industrial Tiuna — Equipo de Comercialización*\n\n"
                        "Indíquenos: *¿Cuál es el producto que está buscando o requiere?*\n"
                        "*(Ejemplo: Chaleco táctico, Chaqueta patriota, Botas, Condecoraciones, etc.)*"
                    ),
                    "image_url": None,
                    "state": "WAITLIST_PRODUCT"
                }

            return self._build_catalog_menu(session, is_off_hours=is_off_hours)

        # -------------------------------------------------------------
        # ESTADO: CONFIRMACIÓN DE LISTA DE ESPERA (PRODUCTO SIN STOCK)
        # -------------------------------------------------------------
        elif current_state == "WAITLIST_CONFIRM":
            if clean_text in ["1", "si", "sí", "avisar", "avisame", "avísame", "esperar", "lista"]:
                prod_name = session.get("waitlist_product_name", "Producto Solicitado")
                prod_id = session.get("waitlist_product_id")
                if session.get("client_name") and session["client_name"] != "CLIENTE":
                    add_to_waitlist(phone, session["client_name"], prod_name, prod_id)
                    session["state"] = "CATALOG"
                    return {
                        "reply": (
                            f"✅ *¡Perfecto! Nos estaremos comunicando con usted cuando el producto que requiera esté disponible.*\n\n"
                            f"Hemos registrado su solicitud para *{prod_name}* en el sistema de *Complejo Industrial Tiuna — Equipo de Comercialización*.\n\n"
                            "En cuanto ingrese stock o sea incorporado al inventario, el sistema le enviará un mensaje automático a este número de WhatsApp.\n\n"
                            "¡Gracias por contactarnos! (Escriba *0* si desea volver al catálogo)."
                        ),
                        "image_url": None,
                        "state": "CATALOG"
                    }
                else:
                    session["state"] = "WAITLIST_NAME"
                    return {
                        "reply": (
                            f"👍 Excelente. Solicitud para: *{prod_name}*.\n\n"
                            "✍️ *Por favor, indíquenos su Nombre y Apellido* para registrar su aviso en el sistema:"
                        ),
                        "image_url": None,
                        "state": "WAITLIST_NAME"
                    }
            elif clean_text in ["2", "no", "catalogo", "catálogo", "0"]:
                reset_session(phone, keep_registration=True)
                return self._build_catalog_menu(session, is_off_hours=is_off_hours)
            else:
                return {
                    "reply": "👉 Por favor responda *1* para registrarse en la lista de espera o *2* para volver al catálogo.",
                    "image_url": None,
                    "state": "WAITLIST_CONFIRM"
                }

        # -------------------------------------------------------------
        # ESTADO: NOMBRE DEL PRODUCTO BUSCADO (NO ENCONTRADO O SIN STOCK)
        # -------------------------------------------------------------
        elif current_state == "WAITLIST_PRODUCT":
            if clean_text in ["0", "menu", "menú", "cancelar"]:
                reset_session(phone, keep_registration=True)
                return self._build_catalog_menu(session, is_off_hours=is_off_hours)

            session["waitlist_product_name"] = clean_text.upper()
            if session.get("client_name") and session["client_name"] != "CLIENTE":
                add_to_waitlist(phone, session["client_name"], session["waitlist_product_name"])
                session["state"] = "CATALOG"
                return {
                    "reply": (
                        f"✅ *¡Perfecto! Nos estaremos comunicando con usted cuando el producto que requiera esté disponible.*\n\n"
                        f"Hemos registrado su solicitud para *{session['waitlist_product_name']}* en el sistema de *Complejo Industrial Tiuna — Equipo de Comercialización*.\n\n"
                        "En cuanto se reponga el stock o sea incorporado al inventario, recibirá un aviso automático a este WhatsApp.\n\n"
                        "¡Gracias por contactarnos! (Escriba *0* si desea volver al catálogo)."
                    ),
                    "image_url": None,
                    "state": "CATALOG"
                }
            else:
                session["state"] = "WAITLIST_NAME"
                return {
                    "reply": (
                        f"👍 Entendido, producto solicitado: *{session['waitlist_product_name']}*.\n\n"
                        "✍️ *Por favor, indíquenos su Nombre y Apellido* para registrar su solicitud en el sistema:"
                    ),
                    "image_url": None,
                    "state": "WAITLIST_NAME"
                }

        # -------------------------------------------------------------
        # ESTADO: CAPTURA DE NOMBRE PARA LISTA DE ESPERA
        # -------------------------------------------------------------
        elif current_state == "WAITLIST_NAME":
            if clean_text in ["0", "menu", "menú", "cancelar"]:
                reset_session(phone, keep_registration=True)
                return self._build_catalog_menu(session, is_off_hours=is_off_hours)

            session["client_name"] = clean_text.upper()
            prod_name = session.get("waitlist_product_name", "Producto Solicitado")
            prod_id = session.get("waitlist_product_id")
            add_to_waitlist(phone, session["client_name"], prod_name, prod_id)
            session["state"] = "CATALOG"
            return {
                "reply": (
                    f"✅ *¡Perfecto, {session['client_name']}! Nos estaremos comunicando con usted cuando el producto que requiera esté disponible.*\n\n"
                    f"Hemos registrado su solicitud para *{prod_name}* en el sistema de *Complejo Industrial Tiuna — Equipo de Comercialización*.\n\n"
                    "En cuanto ingrese stock o sea incorporado al inventario, el sistema le enviará un mensaje automático a este número de WhatsApp.\n\n"
                    "¡Gracias por contactarnos! (Escriba *0* si desea volver al catálogo)."
                ),
                "image_url": None,
                "state": "CATALOG"
            }

        # -------------------------------------------------------------
        # ESTADO 3: SELECCIÓN DE TALLA
        # -------------------------------------------------------------
        elif current_state == "SELECTING_SIZE":
            pending = session.get("pending_item")
            if not pending:
                session["state"] = "CATALOG"
                return self._build_catalog_menu(session, is_off_hours=is_off_hours)

            options = session.get("pending_size_options", [])
            chosen_size = clean_text.strip().upper()

            # Si el usuario responde con el número del botón (ej: 1, 2, 3)
            if clean_text.isdigit() and options:
                idx = int(clean_text)
                if 1 <= idx <= len(options):
                    chosen_size = options[idx - 1]

            # Quitar prefijo "TALLA" si lo escribió manualmente (ej: "TALLA 42" -> "42")
            chosen_size = re.sub(r'^TALLA\s*', '', chosen_size).strip()

            self._add_to_cart(session, pending["product"], pending["qty"], size=chosen_size)
            session["pending_item"] = None
            session["pending_size_options"] = None
            session["state"] = "CART_VIEW"
            return self._build_cart_view(session)

        # -------------------------------------------------------------
        # ESTADO 4: VISTA DE CARRITO (Montos duales $ y Bs BCV)
        # -------------------------------------------------------------
        elif current_state == "CART_VIEW":
            if clean_text in ["1", "otro", "agregar otro", "mas", "más"]:
                session["state"] = "ADDING_MORE"
                menu_resp = self._build_catalog_menu(session, is_off_hours=False)
                return {
                    "reply": menu_resp["reply"] + "\n\n👉 *Escriba el número del producto adicional o su nombre (ej: 2 parches):*",
                    "image_url": None,
                    "state": "ADDING_MORE"
                }

            elif clean_text in ["2", "pagar", "proceder", "si", "sí", "continuar", "comprar"]:
                if not session["cart"]:
                    session["state"] = "CATALOG"
                    return self._build_catalog_menu(session)
                session["state"] = "AWAITING_PAYMENT"
                return self._build_payment_instructions(session)

            elif clean_text in ["3", "vaciar", "cancelar", "borrar"]:
                session["cart"] = []
                session["state"] = "CATALOG"
                return {
                    "reply": "🗑️ Su selección ha sido vaciada.\n\nEscriba *0* para consultar el catálogo nuevamente.",
                    "image_url": None,
                    "state": "CATALOG"
                }
            else:
                return self._build_cart_view(session)

        # -------------------------------------------------------------
        # ESTADO 4.1: AGREGAR MÁS PRODUCTOS
        # -------------------------------------------------------------
        elif current_state == "ADDING_MORE":
            qty = self._extract_quantity(clean_text)
            catalog_products = get_available_catalog_products()
            target_prod = matched_product
            if not target_prod and clean_text.isdigit():
                val = int(clean_text)
                if 1 <= val <= len(catalog_products):
                    target_prod = catalog_products[val - 1]

            if target_prod:
                if target_prod.get("stock", 0) <= 0:
                    return {
                        "reply": f"⚠️ El producto *{target_prod['name']}* se encuentra agotado. Por favor elija otro producto disponible.",
                        "image_url": None,
                        "state": "ADDING_MORE"
                    }
                if self._product_needs_size(target_prod):
                    session["pending_item"] = {"product": target_prod, "qty": qty}
                    session["state"] = "SELECTING_SIZE"
                    return self._prompt_for_size(target_prod, session)
                else:
                    self._add_to_cart(session, target_prod, qty)
                    session["state"] = "CART_VIEW"
                    return self._build_cart_view(session)

            return {
                "reply": "⚠️ No pudimos identificar el producto adicional. Por favor indique el número de la lista o su nombre (ej: *1 gorra* o *2 parches*):",
                "image_url": None,
                "state": "ADDING_MORE"
            }

        # -------------------------------------------------------------
        # ESTADO 5: ESPERANDO PAGO / COMPROBANTE (OCR & Tasa Histórica)
        # -------------------------------------------------------------
        elif current_state == "AWAITING_PAYMENT":
            # El usuario puede ingresar datos de pago por texto (ej: "Pago movil banco de venezuela ref 1234567 monto 1500 bs fecha ayer")
            # O enviar la imagen directamente (procesada en process_receipt_image)
            text_receipt = ReceiptOCRService.parse_text_fields(clean_text)
            if text_receipt["reference"] != "S/REF" or text_receipt["bank"] != "DESCONOCIDO" or text_receipt["amount"] > 0:
                return self._apply_receipt_to_session(session, text_receipt)

            # Si no detectó formato de pago, reiterar instrucción de subir comprobante
            return {
                "reply": (
                    "⚠️ *COMPROBANTE REQUERIDO*\n\n"
                    "Para continuar y coordinar su fecha de retiro, es obligatorio consignar el comprobante de pago previo.\n\n"
                    "📸 *Por favor adjunte la foto/captura de su pago móvil o transferencia*, o escriba el mensaje con:\n"
                    "• *Banco*\n• *Nro. de Referencia*\n• *Monto cancelado*\n• *Fecha del pago*"
                ),
                "image_url": None,
                "state": "AWAITING_PAYMENT"
            }

        # -------------------------------------------------------------
        # ESTADO 6: AGENDAMIENTO DE RETIRO POST-PAGO (Fecha y Hora con Botones)
        # -------------------------------------------------------------
        elif current_state == "AWAITING_SCHEDULE":
            today_now = now_vet()
            from datetime import timedelta

            day_options = session.get("schedule_day_options") or [
                {"label": "Hoy", "date": today_now.strftime("%Y-%m-%d"), "dmy": format_date_dmy(today_now.strftime("%Y-%m-%d"))},
                {"label": "Mañana", "date": (today_now + timedelta(days=1)).strftime("%Y-%m-%d"), "dmy": format_date_dmy((today_now + timedelta(days=1)).strftime("%Y-%m-%d"))},
                {"label": "Pasado Mañana", "date": (today_now + timedelta(days=2)).strftime("%Y-%m-%d"), "dmy": format_date_dmy((today_now + timedelta(days=2)).strftime("%Y-%m-%d"))}
            ]

            time_slots = [
                "09:00 AM",
                "09:30 AM",
                "10:30 AM",
                "02:00 PM",
                "03:30 PM"
            ]

            # Caso 1: Si ya seleccionó la fecha y ahora elige el botón del horario
            if session.get("pickup_date") and not session.get("pickup_time"):
                if clean_text.isdigit() and 1 <= int(clean_text) <= len(time_slots):
                    session["pickup_time"] = time_slots[int(clean_text) - 1]
                    return self._finalize_order(session, phone)
                
                tm = nlu.extract_time(clean_text)
                if tm:
                    session["pickup_time"] = tm
                    return self._finalize_order(session, phone)

            # Caso 2: Selección de día por botón numérico (1, 2, 3)
            if not session.get("pickup_date") and clean_text.isdigit() and 1 <= int(clean_text) <= len(day_options):
                chosen_day = day_options[int(clean_text) - 1]
                session["pickup_date"] = chosen_day["date"]
                session["pickup_date_dmy"] = chosen_day["dmy"]
                
                return {
                    "reply": (
                        f"📅 *Día seleccionado:* {chosen_day['dmy']}\n\n"
                        "⏰ *SELECCIONA LA HORA ESTIMADA DE RETIRO:*\n\n"
                        "[ 1️⃣ ] 09:00 AM\n"
                        "[ 2️⃣ ] 09:30 AM\n"
                        "[ 3️⃣ ] 10:30 AM\n"
                        "[ 4️⃣ ] 02:00 PM\n"
                        "[ 5️⃣ ] 03:30 PM\n\n"
                        "👉 *Toca o responde con el número (1-5) o escribe la hora:*"
                    ),
                    "image_url": None,
                    "state": "AWAITING_SCHEDULE"
                }

            # Caso 3: Entrada natural en texto (ej: "Mañana a las 09:30 AM" o "12/10/2026 a las 10:00 AM")
            dt = nlu.extract_date(clean_text)
            tm = nlu.extract_time(clean_text)
            if dt:
                session["pickup_date"] = dt
            if tm:
                session["pickup_time"] = tm

            if "mañana" in clean_text.lower():
                session["pickup_date"] = (today_now + timedelta(days=1)).strftime("%Y-%m-%d")
                if not session.get("pickup_time"):
                    session["pickup_time"] = tm or "09:30 AM"
            elif "hoy" in clean_text.lower():
                session["pickup_date"] = today_now.strftime("%Y-%m-%d")
                if not session.get("pickup_time"):
                    session["pickup_time"] = tm or "02:00 PM"

            if session.get("pickup_date") and session.get("pickup_time"):
                return self._finalize_order(session, phone)

            # Si solo se extrajo fecha pero aún falta hora
            if session.get("pickup_date") and not session.get("pickup_time"):
                dmy_display = format_date_dmy(session["pickup_date"])
                return {
                    "reply": (
                        f"📅 *Día de retiro:* {dmy_display}\n\n"
                        "⏰ *SELECCIONA LA HORA ESTIMADA:*\n\n"
                        "[ 1️⃣ ] 09:00 AM\n"
                        "[ 2️⃣ ] 09:30 AM\n"
                        "[ 3️⃣ ] 10:30 AM\n"
                        "[ 4️⃣ ] 02:00 PM\n"
                        "[ 5️⃣ ] 03:30 PM\n\n"
                        "👉 *Toca o responde con el número (1-5):*"
                    ),
                    "image_url": None,
                    "state": "AWAITING_SCHEDULE"
                }

            # Si no reconoció ni fecha ni hora, mostrar botones de días
            days = day_options
            return {
                "reply": (
                    "📅 *SELECCIONA EL DÍA DE RETIRO (DD/MM/AAAA):*\n\n"
                    f"[ 1️⃣ ] Hoy ({days[0]['dmy']})\n"
                    f"[ 2️⃣ ] Mañana ({days[1]['dmy']})\n"
                    f"[ 3️⃣ ] Pasado Mañana ({days[2]['dmy']})\n"
                    "[ 4️⃣ ] Otra Fecha (DD/MM/AAAA)\n\n"
                    "👉 *Toca o responde con el número (1-4) o escribe fecha y hora:*"
                ),
                "image_url": None,
                "state": "AWAITING_SCHEDULE"
            }

        # Fallback general
        session["state"] = "CATALOG"
        return self._build_catalog_menu(session, is_off_hours=is_off_hours)

    # -------------------------------------------------------------
    # PROCESAMIENTO DE IMÁGENES DE COMPROBANTE CON PURGA DE DISCO
    # -------------------------------------------------------------
    def process_receipt_image(self, phone: str, image_path: str, caption: str = "") -> Dict[str, Any]:
        """
        Recibe la imagen descargada por Baileys, ejecuta OCR venezolano,
        DESTRUYE inmediatamente el archivo temporal del disco, y avanza el flujo.
        """
        session = get_session(phone)
        parsed = ReceiptOCRService.process_and_destroy_receipt(image_path, simulated_hint_text=caption)
        
        logger.info(f"OCR procesado para {phone}: Banco={parsed['bank']}, Ref={parsed['reference']}, Monto={parsed['amount']}, Fecha={parsed['payment_date']}")
        
        # Si el usuario no estaba en AWAITING_PAYMENT pero ya tiene un carrito, asumir que está pagando
        if session["state"] != "AWAITING_PAYMENT" and session["cart"]:
            session["state"] = "AWAITING_PAYMENT"

        return self._apply_receipt_to_session(session, parsed)

    def _apply_receipt_to_session(self, session: Dict[str, Any], receipt_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Vincula los datos del recibo con la tasa BCV oficial de la FECHA EXACTA del comprobante.
        """
        cart = session["cart"]
        total_usd = sum(item["subtotal"] for item in cart)
        
        p_date = receipt_data.get("payment_date") or now_vet_date_str()
        # CONSULTAR TASA BCV HISTÓRICA DE LA FECHA DE PAGO (si fue ayer, BCV de ayer)
        bcv_rate = bcv_service.get_rate_for_date(p_date)
        total_ves = total_usd * bcv_rate

        ref = receipt_data.get("reference") or "REC-" + now_vet().strftime("%H%M%S")
        bank = receipt_data.get("bank") or "BANCO VENEZOLANO"
        
        session["receipt_ref"] = str(ref).strip()
        session["receipt_bank"] = str(bank).strip()
        session["receipt_date"] = p_date
        session["bcv_rate_applied"] = bcv_rate
        session["amount_usd"] = round(total_usd, 2)
        session["amount_ves"] = round(total_ves, 2)
        session["ocr_raw_text"] = receipt_data.get("raw_text", "")
        session["state"] = "AWAITING_SCHEDULE"

        config = get_all_config()
        pickup_address = config.get("pickup_address", "SEDE DE INTENDENCIA - COMPLEJO INDUSTRIAL TIUNA")
        pickup_hours = config.get("pickup_hours", "LUNES A VIERNES DE 8:00 AM A 5:00 PM")
        formatted_receipt_date = format_date_dmy(session['receipt_date'])

        # Comprobación de tolerancia de monto
        rec_amount = receipt_data.get("amount", 0.0)
        amount_note = ""
        if rec_amount > 0 and rec_amount < (total_ves * 0.85):
            amount_note = (
                f"\n⚠️ *Aviso de Monto:* El comprobante refleja Bs. {rec_amount:,.2f} "
                f"(Cotizado: Bs. {total_ves:,.2f}). Su ticket quedará anotado para validación en taquilla.\n"
            )

        today_now = now_vet()
        from datetime import timedelta
        d1 = today_now
        d2 = today_now + timedelta(days=1)
        d3 = today_now + timedelta(days=2)
        session["schedule_day_options"] = [
            {"label": "Hoy", "date": d1.strftime("%Y-%m-%d"), "dmy": format_date_dmy(d1.strftime("%Y-%m-%d"))},
            {"label": "Mañana", "date": d2.strftime("%Y-%m-%d"), "dmy": format_date_dmy(d2.strftime("%Y-%m-%d"))},
            {"label": "Pasado Mañana", "date": d3.strftime("%Y-%m-%d"), "dmy": format_date_dmy(d3.strftime("%Y-%m-%d"))}
        ]

        days = session["schedule_day_options"]
        return {
            "reply": (
                "✅ *¡COMPROBANTE DE PAGO VALIDADO SATISFACTORIAMENTE!* 📸\n\n"
                f"🏦 *Banco:* {session['receipt_bank']}\n"
                f"🔢 *Nro. de Referencia:* `{session['receipt_ref']}`\n"
                f"📅 *Fecha de Pago Registrada:* {formatted_receipt_date}\n"
                f"💵 *Monto Total:* ${session['amount_usd']:.2f} REF\n"
                f"🇻🇪 *Equivalente en Bs:* Bs. {session['amount_ves']:,.2f}\n"
                f"📈 *Tasa BCV Aplicada ({formatted_receipt_date}):* Bs. {bcv_rate:.2f}/$"
                f"{amount_note}\n"
                "──────────────────────\n"
                "📅 *ÚLTIMO PASO: AGENDAMIENTO DE RETIRO*\n"
                f"📍 *Lugar:* {pickup_address}\n"
                f"⏰ *Horario:* {pickup_hours}\n\n"
                "Selecciona tu *DÍA DE RETIRO:*\n"
                f"[ 1️⃣ ] Hoy ({days[0]['dmy']})\n"
                f"[ 2️⃣ ] Mañana ({days[1]['dmy']})\n"
                f"[ 3️⃣ ] Pasado Mañana ({days[2]['dmy']})\n"
                "[ 4️⃣ ] Otra Fecha (DD/MM/AAAA)\n\n"
                "👉 *Toca una opción o indica tu fecha y hora (ej: Mañana a las 09:30 AM):*"
            ),
            "image_url": None,
            "state": "AWAITING_SCHEDULE"
        }

    def _finalize_order(self, session: Dict[str, Any], phone: str) -> Dict[str, Any]:
        """Crea la orden definitiva en BD con Ticket CIT-YYMMDD-XXX y descuenta Kardex"""
        cart = session["cart"]
        total_items = sum(item["qty"] for item in cart)
        items_summary = " + ".join([f"{item['qty']}X {item['name']}" for item in cart])
        config = get_all_config()
        pickup_address = config.get("pickup_address", "SEDE DE INTENDENCIA - COMPLEJO INDUSTRIAL TIUNA")

        final_phone = session.get("contact_phone")
        if not final_phone or "@" in final_phone:
            final_phone = self._format_phone(phone) or "POR ASIGNAR"

        order_data = {
            "client_name": session.get("client_name") or "CLIENTE GENERAL",
            "cedula": session.get("cedula") or "S/C",
            "phone": final_phone,
            "items_summary": items_summary,
            "items_detail": cart,
            "total_items": total_items,
            "total_amount": session.get("amount_usd", 0.0),
            "amount_usd": session.get("amount_usd", 0.0),
            "amount_ves": session.get("amount_ves", 0.0),
            "bcv_rate_applied": session.get("bcv_rate_applied", 0.0),
            "bcv_rate_date": session.get("receipt_date"),
            "payment_method": f"PAGO MÓVIL / TRANSF ({session.get('receipt_bank', 'BANCO')})",
            "receipt_ref": session.get("receipt_ref"),
            "receipt_bank": session.get("receipt_bank"),
            "receipt_date": session.get("receipt_date"),
            "ocr_raw_text": session.get("ocr_raw_text"),
            "pickup_date": session.get("pickup_date"),
            "pickup_time": session.get("pickup_time"),
            "status": "PENDIENTE POR ATENCIÓN",
            "is_off_hours": session.get("is_off_hours", 0),
            "notes": f"PAGO PREVIO VERIFICADO VÍA OCR (JID: {phone})"
        }

        saved = create_order(order_data)
        ticket_code = saved["ticket_code"]

        # Limpiar carrito pero mantener nombre y cédula en memoria
        client_name = session["client_name"]
        cedula = session["cedula"]
        contact_phone = session["contact_phone"]
        reset_session(phone, keep_registration=True)
        session["client_name"] = client_name
        session["cedula"] = cedula
        session["contact_phone"] = contact_phone

        return {
            "reply": (
                "🎉 *¡SOLICITUD Y PAGO CONFIRMADOS CON ÉXITO!* 🎉\n\n"
                f"🎫 *TICKET OFICIAL SIS-COMER:* `{ticket_code}`\n"
                f"👤 *CLIENTE:* {saved['client_name']}\n"
                f"🪪 *CÉDULA:* {saved['cedula']}\n"
                f"📞 *TELÉFONO DE CONTACTO:* {final_phone}\n"
                f"📦 *ARTÍCULOS:* {saved['items_summary']}\n"
                f"💵 *TOTAL PAGADO:* ${saved['amount_usd']:.2f} REF (Bs. {saved['amount_ves']:,.2f})\n"
                f"🔢 *REF. BANCARIA:* `{saved['receipt_ref']}` ({saved['receipt_bank']})\n"
                f"📅 *FECHA DE RETIRO:* {format_date_dmy(saved['pickup_date'])}\n"
                f"⏰ *HORA ASIGNADA:* {saved['pickup_time']}\n"
                f"📍 *SEDE DE RETIRO:* {pickup_address}\n\n"
                "📌 *INSTRUCCIONES PARA EL RETIRO:*\n"
                "1. Presentar su Cédula de Identidad física en la taquilla de atención.\n"
                f"2. Mostrar este ticket de atención: *{ticket_code}*.\n"
                "3. Su orden ya ha sido registrada en el sistema de confección y despacho.\n\n"
                "¡Gracias por su compra en SIS-COMER! Escriba *Menú* para realizar una nueva solicitud."
            ),
            "image_url": None,
            "state": "COMPLETED",
            "ticket_code": ticket_code
        }

    # -------------------------------------------------------------
    # REGISTRO Y PARSEO INICIAL
    # -------------------------------------------------------------
    def _prompt_initial_registration(self, session: Dict[str, Any], phone: str) -> Dict[str, Any]:
        return {
            "reply": (
                "👋 *¡Bienvenido! Soy SIS-COMER, tu asistente virtual.*\n"
                "🏭 *Complejo Industrial Tiuna — Equipo de Comercialización*\n\n"
                "Para gestionar su pedido, por favor indíquenos sus datos de identificación:\n\n"
                "✍️ *Por favor, escriba su NOMBRE Y APELLIDO COMPLETO:*"
            ),
            "image_url": None,
            "state": "REGISTER_NAME"
        }

    def _try_extract_all_registration_data(self, text: str, session: Dict[str, Any], phone: str):
        """Si el usuario envía todo en un bloque ej: 'Pedro Perez V-15432123 0414-1234567'"""
        ci = nlu.extract_cedula(text)
        if ci and not session.get("cedula"):
            session["cedula"] = ci.upper()

        ph = nlu.extract_phone(text)
        if ph and not session.get("contact_phone"):
            formatted = self._format_phone(ph)
            if formatted:
                session["contact_phone"] = formatted
        elif text.strip() == "1" and self._is_valid_phone(phone) and not session.get("contact_phone"):
            session["contact_phone"] = self._format_phone(phone)

        # Si el texto es solo saludos o comandos (con o sin puntuación), no asignarlo como nombre
        text_clean_words = re.sub(r'[^\w\s]', '', text).upper().split()
        greeting_words = {"HOLA", "BUENOS", "DIAS", "DÍAS", "TARDES", "NOCHES", "SALUDOS", "EPALE", "BUEN", "DIA", "DÍA", "INICIO", "MENU", "0", "AYUDA"}
        
        if text_clean_words and all(w in greeting_words for w in text_clean_words):
            return

        # Si no tiene nombre y el texto tiene palabras que no son saludos
        if not session.get("client_name"):
            valid_words = [w for w in text_clean_words if w not in greeting_words and len(w) >= 2 and not any(c.isdigit() for c in w)]
            if len(valid_words) >= 2:
                session["client_name"] = " ".join(valid_words[:4])
            elif len(valid_words) == 1 and session.get("state") == "REGISTER_NAME":
                session["client_name"] = valid_words[0]

    # -------------------------------------------------------------
    # MENÚS Y VISTAS
    # -------------------------------------------------------------
    def _build_catalog_menu(self, session: Dict[str, Any], is_off_hours: bool = False) -> Dict[str, Any]:
        # FILTRO ESTRICTO: ÚNICAMENTE PRODUCTOS CON STOCK > 0
        products = get_available_catalog_products()
        bcv_rate = bcv_service.get_rate_for_date()

        lines = []
        if is_off_hours:
            lines.append("🌙 *AVISO DE HORARIO:* Fuera de horario laboral presencial (8:00 AM a 5:00 PM). Puede realizar su solicitud y pago en este momento y su retiro quedará programado.")
            lines.append("──────────────────────")

        lines.append("👋 ¡Bienvenido! Soy *SIS-COMER*, tu asistente virtual.")
        lines.append("🏭 *Complejo Industrial Tiuna — Equipo de Comercialización*")
        lines.append(f"📊 *Tasa Oficial BCV:* Bs. {bcv_rate:,.2f} / $\n")

        if not products:
            session["state"] = "CATALOG"
            lines.append("⚠️ *En este momento todos nuestros productos se encuentran en proceso de reposición de inventario.*\n")
            lines.append("📝 *¿Qué producto o requerimiento está buscando?*")
            lines.append("Escriba el *nombre del producto que requiere* y tomaremos sus datos para avisarle automáticamente en cuanto esté disponible.")
            lines.append("\n[ 1️⃣ ] 👨‍💼 Hablar con Asesor")
            lines.append("[ 0️⃣ ] Reiniciar")
        else:
            lines.append("📦 *CATÁLOGO DE PRODUCTOS DISPONIBLES EN STOCK:*\n")
            for idx, p in enumerate(products, 1):
                price_val = self._safe_float(p.get("price", 0.0))
                price_ves = price_val * bcv_rate
                lines.append(f"[ {idx}️⃣ ] *{p['name']}* — ${price_val:.2f} Ref *(Bs. {price_ves:,.2f})*")

            waitlist_idx = len(products) + 1
            advisor_idx = len(products) + 2

            lines.append(f"\n[ {waitlist_idx}️⃣ ] 🔍 *¿Buscas otro producto o sin existencia? (Lista de espera)*")
            lines.append(f"[ {advisor_idx}️⃣ ] 👨‍💼 *Hablar con un Asesor Comercial*")
            lines.append("\n👉 *¿Qué artículo desea solicitar?*")
            lines.append(f"• Toca o responde con el *número del producto (1-{len(products)})* o su nombre.")
            lines.append(f"• Responde *{waitlist_idx}* si buscas un producto no listado o sin stock.")
            lines.append(f"• Responde *{advisor_idx}* para atención directa con un asesor.")
            lines.append("• Escribe *0* para reiniciar el menú.")

        return {
            "reply": "\n".join(lines),
            "image_url": None,
            "state": session.get("state", "CATALOG")
        }

    def _build_cart_view(self, session: Dict[str, Any]) -> Dict[str, Any]:
        cart = session["cart"]
        total_items = sum(item["qty"] for item in cart)
        total_usd = sum(item["subtotal"] for item in cart)
        bcv_rate = bcv_service.get_rate_for_date()
        total_ves = total_usd * bcv_rate

        lines = ["🛒 *RESUMEN DE SU PEDIDO EN SIS-COMER:*\n"]
        last_image = None
        for item in cart:
            lines.append(f"• *{item['qty']}x {item['name']}* — ${item['subtotal']:.2f} Ref *(Bs. {item['subtotal'] * bcv_rate:,.2f})*")
            if item.get("image_url"):
                last_image = item["image_url"]

        lines.append("──────────────────────")
        lines.append(f"📊 *Artículos:* {total_items}")
        lines.append(f"💵 *Monto Total:* ${total_usd:.2f} REF")
        lines.append(f"🇻🇪 *Total en Bolívares:* Bs. {total_ves:,.2f} *(Tasa BCV: {bcv_rate:,.2f})*\n")
        lines.append("👉 *Seleccione una opción para continuar:*")
        lines.append("[ 1️⃣ ] ➕ *Agregar otro producto al pedido*")
        lines.append("[ 2️⃣ ] 💳 *Proceder al Pago previo y Agendamiento*")
        lines.append("[ 3️⃣ ] 🗑️ *Vaciar selección / Cancelar*")

        return {
            "reply": "\n".join(lines),
            "image_url": last_image,
            "state": "CART_VIEW"
        }

    def _build_payment_instructions(self, session: Dict[str, Any]) -> Dict[str, Any]:
        cart = session["cart"]
        total_usd = sum(item["subtotal"] for item in cart)
        bcv_rate = bcv_service.get_rate_for_date()
        total_ves = total_usd * bcv_rate

        config = get_all_config()
        pm_bank = config.get("pagomovil_bank") or "Banco de Venezuela (0102)"
        pm_phone = config.get("pagomovil_phone") or "0412-1234567"
        pm_id = config.get("pagomovil_id") or "J-408123456"

        tr_bank = config.get("transfer_bank") or "Banco de Venezuela"
        tr_account = config.get("transfer_account") or "0102-0501-80-0000123456"
        tr_holder = config.get("transfer_holder") or "COMPLEJO INDUSTRIAL TIUNA"

        text = (
            "💳 *PAGO PREVIO OBLIGATORIO — SIS-COMER* 💳\n\n"
            "Para apartar su mercancía del inventario y asignarle fecha y hora de retiro, debe realizar el pago del monto exacto:\n\n"
            f"💵 *MONTO TOTAL:* ${total_usd:.2f} REF\n"
            f"🇻🇪 *MONTO EN BOLÍVARES:* Bs. {total_ves:,.2f}\n"
            f"📈 *TASA BCV APLICADA HOY:* Bs. {bcv_rate:,.2f}/$\n\n"
            "🏦 *CUENTAS BANCARIAS OFICIALES:*\n\n"
            "🔹 *PAGO MÓVIL:*\n"
            f"• Banco: {pm_bank}\n"
            f"• Teléfono: {pm_phone}\n"
            f"• RIF/Cédula: {pm_id}\n\n"
            "🔹 *TRANSFERENCIA BANCARIA:*\n"
            f"• Banco: {tr_bank}\n"
            f"• Cuenta: {tr_account}\n"
            f"• Titular: {tr_holder}\n\n"
            "📸 *POR FAVOR ADJUNTE LA FOTO O CAPTURA DE SU COMPROBANTE EN ESTE CHAT*\n"
            "*(Nuestro sistema OCR leerá la referencia, banco, monto y fecha de pago automáticamente)*\n\n"
            "*(O escriba los datos de su pago con Banco, Referencia y Monto)*"
        )
        return {
            "reply": text,
            "image_url": None,
            "state": "AWAITING_PAYMENT"
        }

    def _prompt_for_size(self, product: Dict[str, Any], session: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        avail_str = product.get("available_sizes") or ""
        if avail_str.strip():
            sizes = [s.strip().upper() for s in avail_str.split(",") if s.strip()]
        else:
            pname = str(product.get("name", "")).lower()
            if any(k in pname for k in ["bota", "calzado", "zapato"]):
                sizes = ["38", "39", "40", "41", "42", "43", "44"]
            else:
                sizes = ["S", "M", "L", "XL", "XXL"]

        if session is not None:
            session["pending_size_options"] = sizes

        lines = [
            f"📏 *SELECCIONA TU TALLA DISPONIBLE*",
            f"Producto: *{product['name']}*\n",
            "Tallas disponibles para este artículo:"
        ]
        for idx, sz in enumerate(sizes, 1):
            lines.append(f"[ {idx}️⃣ ] Talla {sz}")

        lines.append(f"\n👉 *Toca o responde con el número (1-{len(sizes)}) o escribe tu talla:*")
        return {
            "reply": "\n".join(lines),
            "image_url": product.get("image_url"),
            "state": "SELECTING_SIZE"
        }

    def _add_to_cart(self, session: Dict[str, Any], product: Dict[str, Any], qty: int = 1, size: Optional[str] = None):
        cart = session["cart"]
        pid = product["id"]
        unit_price = self._safe_float(product.get("price", 0.0))

        display_name = str(product["name"]).upper()
        if size:
            display_name = f"{display_name} (TALLA: {size.strip().upper()})"

        for item in cart:
            if item["product_id"] == pid and item.get("size") == size:
                item["qty"] += qty
                item["subtotal"] = item["qty"] * unit_price
                return

        cart.append({
            "product_id": pid,
            "name": display_name,
            "raw_name": str(product["name"]).upper(),
            "size": size.strip().upper() if size else None,
            "qty": qty,
            "unit_price": unit_price,
            "subtotal": qty * unit_price,
            "image_url": product.get("image_url")
        })

    def _extract_quantity(self, text: str) -> int:
        match = re.search(r'\b(\d+)\s*(?:uds?|unidades?|piezas?|pares?|juegos?|parches?|gorras?|uniformes?|barras?)?\b', text.lower())
        if match:
            try:
                q = int(match.group(1))
                return max(1, min(q, 100))
            except Exception:
                pass
        return 1

    def _safe_float(self, val) -> float:
        if isinstance(val, (int, float)):
            return float(val)
        if not val:
            return 0.0
        match = re.search(r'(\d+(?:\.\d+)?)', str(val))
        if match:
            return float(match.group(1))
        return 0.0

    def _product_needs_size(self, product: Dict[str, Any]) -> bool:
        req_size = product.get("requires_size")
        if req_size is not None and req_size in (0, 1):
            return bool(req_size == 1)

        name = str(product.get("name", "")).lower()
        cat = str(product.get("category", "")).lower()

        non_clothing = [
            "parche", "barra", "presilla", "condecoracion", "condecoración",
            "insignia", "distintivo", "escudo", "porta credencial", "banderín"
        ]
        if any(k in name for k in non_clothing) or any(k in cat for k in non_clothing):
            return False

        clothing_keywords = [
            "uniforme", "bota", "calzado", "camisa", "pantalon", "pantalón",
            "chemise", "zapato", "boina", "gorra", "franela", "chaqueta", "suéter", "traje", "guante"
        ]
        return any(k in name for k in clothing_keywords) or any(k in cat for k in ["textil", "calzado", "ropa", "uniforme"])

    def _is_valid_phone(self, val: Optional[str]) -> bool:
        if not val or "@" in val or "lid" in str(val).lower():
            return False
        clean = re.sub(r'\D', '', str(val))
        if len(clean) in (10, 11, 12) and any(code in clean for code in ("412", "414", "424", "416", "426", "212")):
            return True
        return False

    def _format_phone(self, val: Optional[str]) -> str:
        if not val or "@" in val or "lid" in str(val).lower():
            return ""
        clean = re.sub(r'\D', '', str(val))
        if clean.startswith("58") and len(clean) == 12:
            return f"0{clean[2:5]}-{clean[5:8]}-{clean[8:]}"
        elif len(clean) == 11 and clean.startswith("0"):
            return f"{clean[:4]}-{clean[4:7]}-{clean[7:]}"
        elif len(clean) == 10:
            return f"0{clean[:3]}-{clean[3:6]}-{clean[6:]}"
        return str(val)

    def _build_advisor_response(self, phone: str, advisor_name: str = "Asesor Comercial") -> Dict[str, Any]:
        return {
            "reply": (
                "👨‍💼 *TRANSFERENCIA A ASESOR COMERCIAL SIS-COMER:*\n\n"
                "Has solicitado atención con nuestro equipo comercial. "
                f"Nuestro asesor *{advisor_name}* tomará este mismo chat para atenderte a la brevedad posible.\n\n"
                "👉 *Por favor indícanos tu requerimiento o consulta aquí mismo.* Te responderemos directamente por esta conversación.\n\n"
                "*(Escribe 0 en cualquier momento para regresar al menú automatizado)*"
            ),
            "image_url": None,
            "state": "WAITING_ADVISOR"
        }

bot_manager = BotFlowManager()
