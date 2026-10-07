import logging
import re
import os
import json
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
    add_to_waitlist,
    upsert_client,
    get_client_by_phone,
    format_product_technical_sheet,
    get_product_technical_sheet
)

from app.time_utils import now_vet, now_vet_date_str, now_vet_str, format_date_dmy, format_datetime_dmy

logger = logging.getLogger(__name__)

# Memoria de sesiones de usuario activas
user_sessions: Dict[str, Dict[str, Any]] = {}

def get_session(phone: str) -> Dict[str, Any]:
    if phone not in user_sessions:
        existing_client = get_client_by_phone(phone)
        is_reg = bool(existing_client and existing_client.get("name") and existing_client.get("cedula"))
        user_sessions[phone] = {
            "state": "CATALOG" if is_reg else "REGISTER_NAME",  # Si ya existe en BD va a catálogo, si no pide datos
            "is_registered": is_reg,
            "welcome_sent": is_reg,    # Flag para saber si ya se le envió el saludo inicial
            "cart": [],                # [{"product_id", "name", "qty", "unit_price", "subtotal", "size"}]
            "client_name": existing_client.get("name") if is_reg else None,       # MAYÚSCULAS
            "cedula": existing_client.get("cedula") if is_reg else None,            # MAYÚSCULAS
            "contact_phone": existing_client.get("phone") if is_reg else None,     # Teléfono real de contacto
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
    existing_client = get_client_by_phone(phone) if not existing.get("client_name") else None
    name = existing.get("client_name") or (existing_client.get("name") if existing_client else None)
    ci = existing.get("cedula") or (existing_client.get("cedula") if existing_client else None)
    c_phone = existing.get("contact_phone") or (existing_client.get("phone") if existing_client else None)
    is_reg = bool(name and ci) if keep_registration else False
    user_sessions[phone] = {
        "state": "CATALOG" if is_reg else "REGISTER_NAME",
        "is_registered": is_reg,
        "welcome_sent": is_reg,
        "cart": [],
        "client_name": name if is_reg else None,
        "cedula": ci if is_reg else None,
        "contact_phone": c_phone if is_reg else None,
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

        # Analizar intención con NLU
        analysis = nlu.analyze_message(clean_text, current_state=current_state)
        matched_product = analysis["matched_product"]
        extracted = analysis["extracted_data"]

        # Detección de notas de voz enviadas por el usuario
        if clean_text in ["[NOTA_DE_VOZ]", "NOTA_DE_VOZ", "[AUDIO]"] or analysis.get("intent") == "VOICE_NOTE":
            adv_phone = config.get("advisor_phone") or config.get("pagomovil_phone") or "0412-1234567"
            clean_digits = re.sub(r'\D', '', adv_phone)
            wa_digits = f"58{clean_digits[1:]}" if clean_digits.startswith("0") else (clean_digits if clean_digits.startswith("58") else f"58{clean_digits}")
            return {
                "reply": (
                    "🎙️ *Nota de voz recibida.*\n\n"
                    "En este momento nuestro asistente virtual procesa solicitudes por *mensaje de texto escrito* y *fotos de comprobantes*.\n\n"
                    "✍️ *Por favor, escriba su requerimiento o mensaje en texto* para poder atenderle de inmediato. 🙏\n\n"
                    "[ 1️⃣ ] 👨‍💼 *O escriba 1 para comunicarse con un asesor comercial humano.*"
                ),
                "image_url": None,
                "state": session.get("state", "CATALOG")
            }

        # Si el usuario solicita reiniciar su registro o empezar de cero
        if clean_text.lower() in ["reset registro", "reiniciar registro", "cambiar datos", "nuevo registro"]:
            reset_session(phone, keep_registration=False)
            session = get_session(phone)
            session["is_registered"] = False
            session["welcome_sent"] = True
            session["state"] = "REGISTER_NAME"
            return self._prompt_initial_registration(session, phone)

        # 2.5 DETECCIÓN PRIORITARIA DE INCONFORMIDAD / QUEJAS / MENSAJES NEGATIVOS
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

        # Saludo prioritario: si el cliente no está registrado
        if not session.get("is_registered"):
            # 1. Verificar si ya existe en base de datos por teléfono
            existing_client = get_client_by_phone(phone)
            if existing_client and existing_client.get("name") and existing_client.get("cedula"):
                session["client_name"] = existing_client["name"]
                session["cedula"] = existing_client["cedula"]
                session["contact_phone"] = existing_client.get("phone") or phone
                session["is_registered"] = True
                session["welcome_sent"] = True
            else:
                # 2. O si envió todos sus datos en un solo bloque (ej: "Soy Juan Pérez CI 15432123 tlf 04143334455")
                self._try_extract_all_registration_data(clean_text, session, phone)
                if session.get("client_name") and session.get("cedula") and session.get("contact_phone"):
                    session["is_registered"] = True
                    session["welcome_sent"] = True
                    upsert_client(session["client_name"], session["cedula"], session["contact_phone"])
                    welcome_header = (
                        f"✅ *¡Registro completado exitosamente!*\n\n"
                        f"👋 *Bienvenido(a), {session['client_name']}*\n"
                        f"🪪 *Cédula:* {session['cedula']}\n"
                        f"📱 *Teléfono:* {session['contact_phone']}\n"
                        "──────────────────────\n"
                    )
                    session["state"] = "CATALOG"
                    catalog_resp = self._build_catalog_menu(session, is_off_hours=is_off_hours)
                    catalog_resp["reply"] = welcome_header + catalog_resp["reply"]
                    return catalog_resp

            # Si aún no se le ha dado la bienvenida oficial (cualquier mensaje inicial: ".", "aguacate", "hola", etc.)
            if not session.get("is_registered") and not session.get("welcome_sent"):
                session["welcome_sent"] = True
                session["state"] = "REGISTER_NAME"
                return self._prompt_initial_registration(session, phone)

        # Saludo prioritario: si el cliente ya está registrado, saludar por su nombre y mostrar catálogo de inmediato
        if analysis["intent"] == "GREETING" and session.get("is_registered"):
            client_name = session.get("client_name", "Cliente")
            session["state"] = "CATALOG"
            catalog_resp = self._build_catalog_menu(session, is_off_hours=is_off_hours, skip_header=True)
            greeting_prefix = (
                f"👋 *¡Hola, {client_name}! ¿Cómo estás?*\n"
                f"Bienvenido nuevamente a *SIS-COMER* (*Complejo Industrial Tiuna*).\n\n"
            )
            catalog_resp["reply"] = greeting_prefix + catalog_resp["reply"]
            return catalog_resp

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

        # Comando directo o intención de Asesor Comercial
        if analysis["intent"] == "CONNECT_ADVISOR" or clean_text.lower() in ["asesor", "humano", "asesoria", "asesoría", "ayuda"]:
            return self._build_advisor_response(phone, advisor_name)

        # 3.1 DETECCIÓN DIRECTA DE CONSULTAS TÉCNICAS REGLAMENTARIAS (Tela, Botones, Grosor, Duración)
        if analysis["intent"] == "PRODUCT_SPECS" and matched_product:
            return self._build_product_specs_response(session, matched_product)

        if analysis["intent"] == "PRODUCT_SPECS_MENU":
            return self._build_product_specs_menu(session)

        # 3.2 ESTADO: VIENDO FICHA TÉCNICA REGLAMENTARIA
        if current_state == "VIEWING_PRODUCT_SPECS":
            product = session.get("last_specs_product")

            # Opción 1: Solicitar / Adquirir este producto
            if clean_text in ["1", "comprar", "solicitar", "adquirir", "lo quiero", "pedir", "si", "sí"]:
                if not product:
                    session["state"] = "CATALOG"
                    return self._build_catalog_menu(session, is_off_hours=is_off_hours)

                # Si aún no está registrado, solicitar registro previo antes de configurar pedido
                if not session.get("is_registered"):
                    session["pending_specs_purchase"] = product
                    session["state"] = "REGISTER_NAME"
                    return self._prompt_initial_registration(session, phone)

                if self._product_needs_size(product):
                    session["pending_item"] = {"product": product, "qty": 1}
                    session["state"] = "SELECTING_SIZE"
                    return self._prompt_for_size(product, session)
                else:
                    session["pending_item"] = {"product": product, "size": None}
                    session["state"] = "SELECTING_QUANTITY"
                    return {
                        "reply": (
                            f"📦 Ha seleccionado: *{product['name']}* (${self._safe_float(product.get('price', 0)):.2f} Ref)\n\n"
                            "🔢 *¿Cuántas unidades desea solicitar?*\n"
                            "*(Escriba el número deseado, por ejemplo: 1, 2, 3...)*"
                        ),
                        "image_url": None,
                        "state": "SELECTING_QUANTITY"
                    }

            # Opción 2: Asesor comercial humano / Cliente insatisfecho / Dudas
            elif clean_text in ["2", "asesor", "humano", "persona", "agente", "duda", "dudas"] or any(
                w in clean_text.lower() for w in [
                    "no me convence", "no me queda claro", "insatisfecho", "insatisfecha",
                    "no me gusta", "no es lo que busco", "tengo dudas", "prefiero hablar",
                    "quiero hablar", "no responde", "no me sirve", "no estoy seguro", "no estoy conforme"
                ]
            ):
                return self._build_advisor_response(phone, advisor_name)

            # Opción 3: Consultar ficha de otro producto
            elif clean_text in ["3", "otro", "otra", "otra ficha", "mas productos", "consultar otro"]:
                return self._build_product_specs_menu(session)

            # Opción 0: Menú principal
            elif clean_text in ["0", "menu", "menú", "volver", "catalogo", "catálogo"]:
                reset_session(phone, keep_registration=True)
                return self._build_catalog_menu(session, is_off_hours=is_off_hours)

            # Si menciona otro producto explícitamente
            if matched_product:
                return self._build_product_specs_response(session, matched_product)

            # Si pregunta sobre detalles técnicos del producto actual (botones, tela, grosor)
            if product and any(w in clean_text.lower() for w in ["tela", "boton", "botones", "grosor", "durabilidad", "dura", "cuanto", "cuánto", "resistencia", "color", "medida"]):
                return self._build_product_specs_response(session, product)

            # Dudas generales o insatisfacción
            if any(w in clean_text.lower() for w in ["no se", "no sé", "pensaba", "calidad", "garantia", "garantía"]):
                return self._build_advisor_response(phone, advisor_name)

            return {
                "reply": (
                    "👉 Por favor seleccione una opción para continuar:\n\n"
                    "[ 1️⃣ ] 🛒 *Solicitar / Adquirir este producto*\n"
                    "[ 2️⃣ ] 👨‍💼 *Hablar con un Asesor Comercial Humano (atención personalizada)*\n"
                    "[ 3️⃣ ] 📋 *Consultar ficha de otro producto*\n"
                    "[ 0️⃣ ] 🔙 *Volver al catálogo principal*"
                ),
                "image_url": None,
                "state": "VIEWING_PRODUCT_SPECS"
            }

        # 3.3 ESTADO: SELECCIONANDO QUÉ FICHA CONSULTAR
        if current_state == "SELECTING_SPECS_PRODUCT":
            catalog_products = get_available_catalog_products()
            if clean_text.isdigit():
                idx = int(clean_text)
                if 1 <= idx <= len(catalog_products):
                    selected = catalog_products[idx - 1]
                    return self._build_product_specs_response(session, selected)
                elif idx == 0:
                    reset_session(phone, keep_registration=True)
                    return self._build_catalog_menu(session, is_off_hours=is_off_hours)

            if matched_product:
                return self._build_product_specs_response(session, matched_product)

            if clean_text in ["0", "menu", "menú", "volver"]:
                reset_session(phone, keep_registration=True)
                return self._build_catalog_menu(session, is_off_hours=is_off_hours)

            if clean_text.lower() in ["asesor", "humano", "persona", "ayuda"]:
                return self._build_advisor_response(phone, advisor_name)

            return {
                "reply": f"⚠️ Por favor responda con un número válido del 1 al {len(catalog_products)} o escriba 0 para volver al catálogo.",
                "image_url": None,
                "state": "SELECTING_SPECS_PRODUCT"
            }

        # 4. VERIFICACIÓN OBLIGATORIA DE REGISTRO INICIAL (Nombre, Cédula, Teléfono)
        if not session.get("is_registered"):
            # 4.1 Intentar recuperar cliente previo de la base de datos por teléfono
            existing_client = get_client_by_phone(phone)
            if existing_client and existing_client.get("name") and existing_client.get("cedula"):
                session["client_name"] = existing_client["name"]
                session["cedula"] = existing_client["cedula"]
                session["contact_phone"] = existing_client.get("phone") or phone
                session["is_registered"] = True
                session["state"] = "CATALOG"

            # Intentar extracción en un solo bloque si el cliente envió sus datos completos
            # Ej: "Buenas tardes soy Carlos Pérez V-18456123 tlf 0414-1234567"
            self._try_extract_all_registration_data(clean_text, session, phone)
            if session.get("client_name") and session.get("cedula") and session.get("contact_phone"):
                session["is_registered"] = True
                upsert_client(session["client_name"], session["cedula"], session["contact_phone"])
                welcome_header = (
                    f"✅ *¡Registro completado exitosamente!*\n\n"
                    f"👋 *Bienvenido(a), {session['client_name']}*\n"
                    f"🪪 *Cédula:* {session['cedula']}\n"
                    f"📱 *Teléfono:* {session['contact_phone']}\n"
                    "──────────────────────\n"
                )

                pending_prod = session.pop("pending_specs_purchase", None)
                if pending_prod:
                    if self._product_needs_size(pending_prod):
                        session["pending_item"] = {"product": pending_prod, "qty": 1}
                        session["state"] = "SELECTING_SIZE"
                        size_resp = self._prompt_for_size(pending_prod, session)
                        size_resp["reply"] = welcome_header + size_resp["reply"]
                        return size_resp
                    else:
                        session["pending_item"] = {"product": pending_prod, "size": None}
                        session["state"] = "SELECTING_QUANTITY"
                        return {
                            "reply": welcome_header + (
                                f"📦 Ha seleccionado: *{pending_prod['name']}* (${self._safe_float(pending_prod.get('price', 0)):.2f} Ref)\n\n"
                                "🔢 *¿Cuántas unidades desea solicitar?*\n"
                                "*(Escriba el número deseado, por ejemplo: 1, 2, 3...)*"
                            ),
                            "image_url": None,
                            "state": "SELECTING_QUANTITY"
                        }

                session["state"] = "CATALOG"
                catalog_resp = self._build_catalog_menu(session, is_off_hours=is_off_hours)
                catalog_resp["reply"] = welcome_header + catalog_resp["reply"]
                return catalog_resp

            # Palabras conversacionales, de charlar, de productos y de cortesía que NO son nombres
            NON_NAME_WORDS = {
                "HOLA", "BUENOS", "BUENAS", "BUENO", "BUEN", "DIAS", "DÍAS", "TARDES", "NOCHES",
                "SALUDOS", "SALUDO", "EPALE", "ÉPALE", "CORDIAL", "ESTIMADO", "ESTIMADA",
                "DIA", "DÍA", "INICIO", "MENU", "MENÚ", "0", "EMPEZAR", "START", "RESET", "REINICIAR",
                "POR", "FAVOR", "GRACIAS", "OK", "VALE", "LISTO", "COMO", "CÓMO", "ESTAS", "ESTÁS",
                "ESTA", "ESTÁ", "USTED", "TU", "TÚ", "QUE", "QUÉ", "TAL", "AMIGO", "AMIGA", "HERMANO",
                "PERO", "DIGO", "DICE", "DECIR", "DIJE", "HAHAHA", "JAJAJA", "JAJA", "HAHA", "JEJE", "XD",
                "PUES", "NADA", "ALGO", "AQUI", "AQUÍ", "ALLI", "ALLÍ", "ALLA", "ALLÁ",
                "MENSAJE", "ENVIAS", "ENVÍAS", "ENVIA", "ENVÍO", "ENVIO", "MANDAS", "MANDE", "HORA", "HORAS",
                "TIEMPO", "CUANDO", "CUÁNDO", "DONDE", "DÓNDE", "PORQUE", "PORQUÉ", "ENTONCES", "SABES", "SABER",
                "TIENDA", "AGUACATE", "MANZANA", "CARRO", "CASA", "PANA", "BROTHER", "COMPA", "CHAMO",
                "PATRIOTA", "TIUNA", "CHAQUETA", "CHAQUETAS", "GORRA", "GORRAS", "BOTA", "BOTAS",
                "MILITAR", "MILITARES", "PARCHE", "PARCHES", "TACTICA", "TACTICO", "TACTICAS", "TACTICOS",
                "CAMPAÑA", "CAMPANA", "CAMUFLAJE", "VERDE", "NEGRO", "AZUL", "TALLA", "TALLAS",
                "COMPRAR", "QUIERO", "PRECIO", "PRECIOS", "COSTO", "COSTOS", "CUANTO", "CUÁNTO",
                "VALE", "TIENEN", "HAY", "STOCK", "DISPONIBLE", "CATALOGO", "CATÁLOGO", "PEDIDO",
                "ASESOR", "HUMANO", "AYUDA", "OPCION", "OPCIÓN", "UNIDADES", "CANTIDAD", "DESPACHO",
                "DESDE", "HASTA", "PARTIR", "APARTIR"
            }
            text_clean_words = [w for w in re.sub(r'[^\w\s]', '', clean_text).upper().split() if w]

            # Paso 1: Solicitar Nombre y Apellido
            if current_state in ["REGISTER_NAME", "INIT"]:
                # Si el mensaje contiene signos de pregunta o risas conversacionales
                has_conversational_cues = bool(re.search(r'[?¿]|(jaj|hah|jeje|xd)', clean_text.lower()))
                
                # Limpiar prefijos de presentación ("Soy Carlos Perez", "Me llamo Juan", etc.)
                clean_name = clean_text
                clean_name = re.sub(r'^(?:¡?hola!?\s*)?(?:buenas\s*(?:tardes|dias|días|noches)?\s*,?\s*)?(?:soy|me\s+llamo|mi\s+nombre\s+es|yo\s+soy)\s+', '', clean_name, flags=re.IGNORECASE)
                clean_name = re.sub(r'^(?:¡?hola!?\s*)?(?:buenas\s*(?:tardes|dias|días|noches)?\s*,?\s*)?', '', clean_name, flags=re.IGNORECASE).strip()

                name_tokens = [w for w in re.findall(r'[a-zA-ZáéíóúÁÉÍÓÚñÑ]+', clean_name) if len(w) >= 2]
                valid_name_words = [
                    w for w in name_tokens 
                    if w.upper() not in NON_NAME_WORDS and not any(c.isdigit() for c in w)
                ]

                # Se requiere que sean al menos dos nombres/apellidos válidos para evitar capturar frases
                if not has_conversational_cues and len(valid_name_words) >= 2:
                    session["client_name"] = " ".join(valid_name_words[:4]).title()
                    session["state"] = "REGISTER_CEDULA"
                    return {
                        "reply": (
                            f"👍 ¡Hola, *{session['client_name']}*! Encantado de atenderle.\n\n"
                            "🪪 *¿Me indicas tu número de Cédula de Identidad?*\n"
                            "*(Ejemplo: V-12345678 o 12345678)*"
                        ),
                        "image_url": None,
                        "state": "REGISTER_CEDULA"
                    }
                else:
                    session["state"] = "REGISTER_NAME"
                    return {
                        "reply": (
                            "👋 Para iniciar formalmente su registro y atención, por favor indíquenos su *Nombre y Apellido* completo:\n"
                            "*(Por ejemplo: Carlos Pérez o María Rodríguez)*"
                        ),
                        "image_url": None,
                        "state": "REGISTER_NAME"
                    }

            # Paso 2: Solicitar Cédula de Identidad
            elif current_state == "REGISTER_CEDULA":
                ci = nlu.extract_cedula(clean_text)
                if ci:
                    session["cedula"] = ci.upper()
                    session["state"] = "REGISTER_PHONE"
                    return {
                        "reply": (
                            f"🪪 Cédula registrada: *{session['cedula']}*.\n\n"
                            "📱 *¿Me indicas tu número de teléfono de contacto?*\n"
                            "*(Ejemplo: 0414-1234567 o 0412-1234567)*"
                        ),
                        "image_url": None,
                        "state": "REGISTER_PHONE"
                    }
                else:
                    return {
                        "reply": (
                            f"⚠️ *{session.get('client_name', 'Estimado cliente')}*, por favor ingrese un número de cédula válido.\n"
                            "*(Ejemplo: V-12345678 o 12345678)*"
                        ),
                        "image_url": None,
                        "state": "REGISTER_CEDULA"
                    }

            # Paso 3: Solicitar Teléfono de Contacto
            elif current_state == "REGISTER_PHONE":
                if clean_text.strip() == "1" and self._is_valid_phone(phone):
                    contact_ph = self._format_phone(phone)
                else:
                    extracted_ph = nlu.extract_phone(clean_text)
                    contact_ph = self._format_phone(extracted_ph) if extracted_ph else None
                
                if contact_ph:
                    session["contact_phone"] = contact_ph
                    session["is_registered"] = True
                    upsert_client(session["client_name"], session["cedula"], session["contact_phone"])

                    welcome_header = (
                        f"✅ *¡Registro completado exitosamente!*\n\n"
                        f"👋 *Bienvenido(a), {session['client_name']}*\n"
                        f"🪪 *Cédula:* {session['cedula']}\n"
                        f"📱 *Teléfono:* {session['contact_phone']}\n"
                        "──────────────────────\n"
                    )

                    pending_prod = session.pop("pending_specs_purchase", None)
                    if pending_prod:
                        if self._product_needs_size(pending_prod):
                            session["pending_item"] = {"product": pending_prod, "qty": 1}
                            session["state"] = "SELECTING_SIZE"
                            size_resp = self._prompt_for_size(pending_prod, session)
                            size_resp["reply"] = welcome_header + size_resp["reply"]
                            return size_resp
                        else:
                            session["pending_item"] = {"product": pending_prod, "size": None}
                            session["state"] = "SELECTING_QUANTITY"
                            return {
                                "reply": welcome_header + (
                                    f"📦 Ha seleccionado: *{pending_prod['name']}* (${self._safe_float(pending_prod.get('price', 0)):.2f} Ref)\n\n"
                                    "🔢 *¿Cuántas unidades desea solicitar?*\n"
                                    "*(Escriba el número deseado, por ejemplo: 1, 2, 3...)*"
                                ),
                                "image_url": None,
                                "state": "SELECTING_QUANTITY"
                            }

                    session["state"] = "CATALOG"
                    catalog_resp = self._build_catalog_menu(session, is_off_hours=is_off_hours)
                    catalog_resp["reply"] = welcome_header + catalog_resp["reply"]
                    return catalog_resp
                else:
                    return {
                        "reply": "⚠️ Por favor ingrese su número telefónico de contacto venezolano por escrito (ejemplo: *0414-1234567* o *0412-1234567*).",
                        "image_url": None,
                        "state": "REGISTER_PHONE"
                    }
            else:
                session["state"] = "REGISTER_NAME"
                return self._prompt_initial_registration(session, phone)

        # -------------------------------------------------------------
        # CLIENTE REGISTRADO — COMANDOS GLOBALES Y ATENCIÓN
        # -------------------------------------------------------------
        # Comandos globales de reinicio o volver al menú
        if clean_text.lower() in ["0", "menu", "menú", "inicio", "empezar", "reset", "cancelar"]:
            reset_session(phone, keep_registration=True)
            return self._build_catalog_menu(session, is_off_hours=is_off_hours)

        # Detección inteligente directa de uno o varios artículos, cantidades y tallas (multilínea o compuesto)
        if current_state in ["CATALOG", "INIT", "ADDING_MORE"]:
            catalog_products = get_available_catalog_products()
            order_items = nlu.extract_order_items(clean_text, catalog_products)
            if order_items:
                added_any = False
                needs_size_item = None
                for itm in order_items:
                    prod = itm["product"]
                    qty = itm["qty"]
                    sz = itm.get("size")
                    if prod.get("stock", 0) <= 0:
                        session["waitlist_product_name"] = prod["name"]
                        session["waitlist_product_id"] = prod["id"]
                        session["state"] = "WAITLIST_CONFIRM"
                        return {
                            "reply": (
                                f"⚠️ El artículo *{prod['name']}* se encuentra actualmente *AGOTADO / SIN STOCK* en nuestro inventario.\n\n"
                                "¿Desea que le avisemos automáticamente apenas ingrese nuevo stock a nuestro almacén?\n\n"
                                "[ 1️⃣ ] *Sí, avisarme cuando esté disponible*\n"
                                "[ 2️⃣ ] *Ver productos disponibles en catálogo*\n\n"
                                "👉 Responda *1* para anotarse en la lista de espera o *2* para ver el catálogo."
                            ),
                            "image_url": None,
                            "state": "WAITLIST_CONFIRM"
                        }

                    if self._product_needs_size(prod):
                        if sz:
                            self._add_to_cart(session, prod, qty, size=sz)
                            added_any = True
                        else:
                            needs_size_item = {"product": prod, "qty": qty}
                    else:
                        self._add_to_cart(session, prod, qty)
                        added_any = True

                if needs_size_item and not added_any:
                    session["pending_item"] = needs_size_item
                    session["state"] = "SELECTING_SIZE"
                    return self._prompt_for_size(needs_size_item["product"], session)

                if added_any:
                    session["state"] = "CART_VIEW"
                    return self._build_cart_view(session)

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
                if qty > 1:
                    self._add_to_cart(session, matched_product, qty)
                    session["state"] = "CART_VIEW"
                    return self._build_cart_view(session)
                else:
                    session["pending_item"] = {"product": matched_product, "size": None}
                    session["state"] = "SELECTING_QUANTITY"
                    return {
                        "reply": (
                            f"📦 Ha seleccionado: *{matched_product['name']}* (${self._safe_float(matched_product.get('price', 0)):.2f} Ref)\n\n"
                            "🔢 *¿Cuántas unidades desea solicitar?*\n"
                            "*(Escriba el número deseado, por ejemplo: 1, 2, 3...)*"
                        ),
                        "image_url": None,
                        "state": "SELECTING_QUANTITY"
                    }

        # -------------------------------------------------------------
        # ESTADO 2: CATÁLOGO DINÁMICO (Stock > 0 y Tasa BCV Oficial)
        # -------------------------------------------------------------
        elif current_state == "CATALOG":
            if analysis["intent"] == "CONNECT_ADVISOR":
                return self._build_advisor_response(phone, advisor_name)

            catalog_products = get_available_catalog_products()
            waitlist_idx = len(catalog_products) + 1
            advisor_idx = len(catalog_products) + 2

            num_matches = [int(n) for n in re.findall(r'\b\d+\b', clean_text)]
            valid_prod_nums = [n for n in num_matches if 1 <= n <= len(catalog_products)]

            # Multi-selección (ej: "1 y 2", "1, 3")
            if len(valid_prod_nums) > 1:
                for num_val in valid_prod_nums:
                    p = catalog_products[num_val - 1]
                    self._add_to_cart(session, p, qty=1)
                session["state"] = "CART_VIEW"
                return self._build_cart_view(session)

            if analysis["intent"] == "NUMERIC_OPTION" or len(valid_prod_nums) == 1:
                val = valid_prod_nums[0] if valid_prod_nums else analysis.get("value")
                if val and 1 <= val <= len(catalog_products):
                    selected = catalog_products[val - 1]
                    if self._product_needs_size(selected):
                        session["pending_item"] = {"product": selected, "qty": 1}
                        session["state"] = "SELECTING_SIZE"
                        return self._prompt_for_size(selected, session)
                    else:
                        session["pending_item"] = {"product": selected, "size": None}
                        session["state"] = "SELECTING_QUANTITY"
                        return {
                            "reply": (
                                f"📦 Ha seleccionado: *{selected['name']}* (${self._safe_float(selected.get('price', 0)):.2f} Ref)\n\n"
                                "🔢 *¿Cuántas unidades desea solicitar?*\n"
                                "*(Escriba el número deseado, por ejemplo: 1, 2, 3...)*"
                            ),
                            "image_url": None,
                            "state": "SELECTING_QUANTITY"
                        }
                elif val == waitlist_idx:
                    session["state"] = "WAITLIST_PRODUCT"
                    return {
                        "reply": (
                            "📋 *SOLICITUD ESPECIAL Y LISTA DE ESPERA*\n"
                            "🏭 *Complejo Industrial Tiuna — Equipo de Comercialización*\n\n"
                            "Indíquenos: *¿Cuál es el modelo o producto especial que está buscando?*\n"
                            "*(Ejemplo: Chaleco táctico, Chaqueta de gala, Botas de campaña, Condecoraciones, etc.)*"
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
                    if qty > 1:
                        self._add_to_cart(session, matched_product, qty)
                        session["state"] = "CART_VIEW"
                        return self._build_cart_view(session)
                    else:
                        session["pending_item"] = {"product": matched_product, "size": None}
                        session["state"] = "SELECTING_QUANTITY"
                        return {
                            "reply": (
                                f"📦 Ha seleccionado: *{matched_product['name']}* (${self._safe_float(matched_product.get('price', 0)):.2f} Ref)\n\n"
                                "🔢 *¿Cuántas unidades desea solicitar?*\n"
                                "*(Escriba el número deseado, por ejemplo: 1, 2, 3...)*"
                            ),
                            "image_url": None,
                            "state": "SELECTING_QUANTITY"
                        }

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

            pending["size"] = chosen_size
            session["pending_size_options"] = None

            # Si ya se especificó cantidad previa mayor a 1, añadir directamente
            if pending.get("qty", 0) > 1:
                self._add_to_cart(session, pending["product"], pending["qty"], size=chosen_size)
                session["pending_item"] = None
                session["state"] = "CART_VIEW"
                return self._build_cart_view(session)

            # Si no, solicitar la cantidad
            session["state"] = "SELECTING_QUANTITY"
            prod_name = pending["product"]["name"]
            return {
                "reply": (
                    f"📏 Talla seleccionada: *{chosen_size}*.\n\n"
                    f"🔢 *¿Cuántas unidades de {prod_name} desea solicitar?*\n"
                    "*(Escriba el número deseado, por ejemplo: 1, 2, 3...)*"
                ),
                "image_url": None,
                "state": "SELECTING_QUANTITY"
            }

        # -------------------------------------------------------------
        # ESTADO 3.1: SELECCIÓN DE CANTIDAD
        # -------------------------------------------------------------
        elif current_state == "SELECTING_QUANTITY":
            pending = session.get("pending_item")
            if not pending:
                session["state"] = "CATALOG"
                return self._build_catalog_menu(session, is_off_hours=is_off_hours)

            qty = self._extract_quantity(clean_text)
            if qty <= 0:
                qty = 1

            self._add_to_cart(session, pending["product"], qty, size=pending.get("size"))
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
                    if qty > 1:
                        self._add_to_cart(session, target_prod, qty)
                        session["state"] = "CART_VIEW"
                        return self._build_cart_view(session)
                    else:
                        session["pending_item"] = {"product": target_prod, "size": None}
                        session["state"] = "SELECTING_QUANTITY"
                        return {
                            "reply": (
                                f"📦 Ha seleccionado: *{target_prod['name']}* (${self._safe_float(target_prod.get('price', 0)):.2f} Ref)\n\n"
                                "🔢 *¿Cuántas unidades desea solicitar?*\n"
                                "*(Escriba el número deseado, por ejemplo: 1, 2, 3...)*"
                            ),
                            "image_url": None,
                            "state": "SELECTING_QUANTITY"
                        }

            return {
                "reply": "⚠️ No pudimos identificar el producto adicional. Por favor indique el número de la lista o su nombre (ej: *1 gorra* o *2 parches*):",
                "image_url": None,
                "state": "ADDING_MORE"
            }

        # -------------------------------------------------------------
        # ESTADO 5: ESPERANDO PAGO / COMPROBANTE
        # -------------------------------------------------------------
        elif current_state == "AWAITING_PAYMENT":
            if clean_text in ["0", "menu", "menú", "cancelar"]:
                reset_session(phone, keep_registration=True)
                return self._build_catalog_menu(session, is_off_hours=is_off_hours)

            # El usuario puede ingresar datos de pago por texto (ej: "Pago movil banco mercantil ref 1234567 monto 1500 bs")
            text_receipt = ReceiptOCRService.parse_text_fields(clean_text)
            if text_receipt["reference"] != "S/REF" or text_receipt["bank"] != "DESCONOCIDO" or text_receipt["amount"] > 0:
                session["manual_payment_data"] = clean_text
                return self._apply_receipt_to_session(session, text_receipt)

            # Si no detectó formato de pago, reiterar instrucción de subir foto o escribir datos
            return {
                "reply": (
                    "📸 *CONSIGNACIÓN DE COMPROBANTE*\n\n"
                    "Para coordinar la entrega y fecha de retiro, por favor *adjunte la foto o captura de su pago móvil o transferencia*.\n\n"
                    "✍️ *O si lo prefiere, escriba los datos de su operación:* Banco emisor, Nro. de Referencia y Monto cancelado.\n"
                    "*(Escriba 0 si desea volver al menú)*"
                ),
                "image_url": None,
                "state": "AWAITING_PAYMENT"
            }

        # -------------------------------------------------------------
        # ESTADO 5.1: CORROBORACIÓN DE DATOS DE PAGO (Híbrido)
        # -------------------------------------------------------------
        elif current_state == "CONFIRMING_PAYMENT_DATA":
            if clean_text in ["0", "menu", "menú", "cancelar"]:
                reset_session(phone, keep_registration=True)
                return self._build_catalog_menu(session, is_off_hours=is_off_hours)

            text_receipt = ReceiptOCRService.parse_text_fields(clean_text)
            pending_data = session.get("pending_receipt_data", {})

            # Extraer referencia: del texto analizado, o cualquier secuencia de 4 a 16 dígitos en el texto del cliente
            final_ref = text_receipt["reference"] if text_receipt["reference"] != "S/REF" else pending_data.get("reference", "S/REF")
            if final_ref == "S/REF":
                digits_match = re.search(r'\b\d{4,16}\b', clean_text)
                if digits_match:
                    final_ref = digits_match.group(0)
                else:
                    final_ref = clean_text.strip()[:20]

            final_bank = text_receipt["bank"] if text_receipt["bank"] != "DESCONOCIDO" else pending_data.get("bank", "BANCO NACIONAL")
            if final_bank == "DESCONOCIDO":
                final_bank = "BANCO NACIONAL"

            final_amount = text_receipt["amount"] if text_receipt["amount"] > 0 else pending_data.get("amount", 0.0)
            final_date = text_receipt["payment_date"] if text_receipt.get("date_detected") else pending_data.get("payment_date", now_vet_date_str())

            combined_receipt = {
                "bank": final_bank,
                "reference": final_ref,
                "amount": final_amount,
                "currency": text_receipt.get("currency", "VES"),
                "payment_date": final_date,
                "date_detected": True,
                "payer_id": text_receipt.get("payer_id") or pending_data.get("payer_id"),
                "concept": text_receipt.get("concept") or pending_data.get("concept"),
                "raw_text": (pending_data.get("raw_text", "") + "\nReportado por cliente: " + clean_text).strip(),
                "ocr_ok": True
            }
            session["manual_payment_data"] = clean_text
            session.pop("pending_receipt_data", None)
            return self._apply_receipt_to_session(session, combined_receipt)

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
                        "👉 *Toca o responde con el número (1-5) o escribe tu hora personalizada (ej: 09:15 AM o 9:01):*"
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
                        "👉 *Toca o responde con el número (1-5) o escribe tu hora personalizada (ej: 09:15 AM o 9:01):*"
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
                    "👉 *Toca o responde con el número (1-4) o escribe fecha y hora personalizada (ej: Mañana a las 9:01 AM):*"
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
        Recibe la imagen del comprobante, ejecuta análisis venezolano,
        purga inmediatamente el archivo de disco, y avanza el flujo sin tecnicismos.
        """
        session = get_session(phone)
        parsed = ReceiptOCRService.process_and_destroy_receipt(image_path, simulated_hint_text=caption)
        
        logger.info(f"Comprobante recibido para {phone}: Banco={parsed['bank']}, Ref={parsed['reference']}, Monto={parsed['amount']}")
        
        if session["state"] != "AWAITING_PAYMENT" and session["cart"]:
            session["state"] = "AWAITING_PAYMENT"

        # Si el análisis extrajo exitosamente tanto la referencia como el banco
        if parsed.get("reference") != "S/REF" and parsed.get("bank") != "DESCONOCIDO":
            return self._apply_receipt_to_session(session, parsed)

        # Si requiere corroborar número de referencia o banco emisor (sin tecnicismos al cliente)
        session["pending_receipt_data"] = parsed
        session["state"] = "CONFIRMING_PAYMENT_DATA"
        return {
            "reply": (
                "📸 *Hemos recibido la imagen de su comprobante.*\n\n"
                "Para garantizar la rápida y exacta conciliación de su pago, por favor facilítenos a continuación el *número de referencia* y el *banco emisor* desde el que realizó la operación:\n"
                "*(Por ejemplo: Ref 12345678 Banco Mercantil)*"
            ),
            "image_url": None,
            "state": "CONFIRMING_PAYMENT_DATA"
        }

    def _apply_receipt_to_session(self, session: Dict[str, Any], receipt_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Vincula los datos del recibo con la tasa BCV oficial de la FECHA EXACTA del comprobante.
        """
        cart = session["cart"]
        total_usd = sum(item["subtotal"] for item in cart)
        
        p_date = receipt_data.get("payment_date") or now_vet_date_str()
        # CONSULTAR TASA BCV HISTÓRICA DE LA FECHA DE PAGO (si fue ayer, BCV de ayer)
        bcv_rate = bcv_service.get_rate_for_date(p_date)

        config = get_all_config()
        apply_iva = config.get("apply_iva", "0") == "1"
        iva_rate = float(config.get("iva_rate", "16"))
        if apply_iva:
            iva_amount = round(total_usd * (iva_rate / 100.0), 2)
            total_usd_final = round(total_usd + iva_amount, 2)
        else:
            iva_amount = 0.0
            total_usd_final = round(total_usd, 2)

        total_ves = round(total_usd_final * bcv_rate, 2)

        ref = receipt_data.get("reference") or "REC-" + now_vet().strftime("%H%M%S")
        bank = receipt_data.get("bank") or "BANCO VENEZOLANO"
        
        session["receipt_ref"] = str(ref).strip()
        session["receipt_bank"] = str(bank).strip()
        session["receipt_date"] = p_date
        session["bcv_rate_applied"] = bcv_rate
        session["subtotal_usd"] = round(total_usd, 2)
        session["iva_amount"] = iva_amount
        session["amount_usd"] = total_usd_final
        session["amount_ves"] = total_ves
        session["ocr_raw_text"] = receipt_data.get("raw_text", "")
        session["ocr_data_json"] = json.dumps(receipt_data, ensure_ascii=False)
        session["state"] = "AWAITING_SCHEDULE"

        pickup_address = config.get("pickup_address", "SEDE DE INTENDENCIA - COMPLEJO INDUSTRIAL TIUNA")
        pickup_hours = config.get("pickup_hours", "LUNES A VIERNES DE 8:00 AM A 5:00 PM")
        formatted_receipt_date = format_date_dmy(session['receipt_date'])

        # Comprobación de tolerancia de monto
        rec_amount = receipt_data.get("amount", 0.0)
        amount_note = ""
        if rec_amount > 0 and rec_amount < (total_ves * 0.85):
            amount_note = (
                f"\n⚠️ *Aviso de Monto:* El comprobante refleja Bs. {rec_amount:,.2f} "
                f"(Cotizado: Bs. {total_ves:,.2f}). Su ticket quedará anotado para validación contable.\n"
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
        iva_line = f"🧾 *IVA ({iva_rate:g}%):* ${iva_amount:.2f} REF\n" if iva_amount > 0 else ""
        return {
            "reply": (
                "✅ *¡COMPROBANTE DE PAGO VALIDADO SATISFACTORIAMENTE!* 📸\n\n"
                f"🏦 *Banco:* {session['receipt_bank']}\n"
                f"🔢 *Nro. de Referencia:* `{session['receipt_ref']}`\n"
                f"📅 *Fecha de Pago Registrada:* {formatted_receipt_date}\n"
                f"{iva_line}"
                f"💵 *Monto Total:* ${session['amount_usd']:.2f} REF\n"
                f"🇻🇪 *Equivalente en Bs:* Bs. {session['amount_ves']:,.2f}\n"
                f"📈 *Tasa BCV Aplicada ({formatted_receipt_date}):* Bs. {bcv_rate:.2f}/$"
                f"{amount_note}\n"
                "⏱️ *Nota de Seguridad:* La conciliación bancaria toma hasta *24 horas hábiles* por administración. Su requerimiento y agendamiento quedan formalmente registrados.\n"
                "──────────────────────\n"
                "📅 *ÚLTIMO PASO: AGENDAMIENTO DE RETIRO*\n"
                f"📍 *Lugar:* {pickup_address}\n"
                f"⏰ *Horario:* {pickup_hours}\n\n"
                "Selecciona tu *DÍA DE RETIRO:*\n"
                f"[ 1️⃣ ] Hoy ({days[0]['dmy']})\n"
                f"[ 2️⃣ ] Mañana ({days[1]['dmy']})\n"
                f"[ 3️⃣ ] Pasado Mañana ({days[2]['dmy']})\n"
                "[ 4️⃣ ] Otra Fecha (DD/MM/AAAA)\n\n"
                "👉 *Toca una opción o indica tu fecha y hora (ej: Mañana a las 09:15 AM o 9:01):*"
            ),
            "image_url": None,
            "state": "AWAITING_SCHEDULE"
        }

    def _finalize_order(self, session: Dict[str, Any], phone: str) -> Dict[str, Any]:
        """Crea la orden definitiva en BD con Ticket CIT-YYMMDD-XXX y estado PENDIENTE POR CONFIRMAR PAGO"""
        cart = session["cart"]
        total_items = sum(item["qty"] for item in cart)
        items_summary = " + ".join([f"{item['qty']}X {item.get('raw_name') or item['name']}" for item in cart])
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
            "iva_amount": session.get("iva_amount", 0.0),
            "bcv_rate_applied": session.get("bcv_rate_applied", 0.0),
            "bcv_rate_date": session.get("receipt_date"),
            "payment_method": f"PAGO MÓVIL / TRANSF ({session.get('receipt_bank', 'BANCO')})",
            "receipt_ref": session.get("receipt_ref"),
            "receipt_bank": session.get("receipt_bank"),
            "receipt_date": session.get("receipt_date"),
            "ocr_raw_text": session.get("ocr_raw_text"),
            "ocr_data_json": session.get("ocr_data_json"),
            "manual_payment_data": session.get("manual_payment_data"),
            "pickup_date": session.get("pickup_date"),
            "pickup_time": session.get("pickup_time"),
            "status": "PENDIENTE POR CONFIRMAR PAGO",
            "is_off_hours": session.get("is_off_hours", 0),
            "notes": f"PAGO PREVIO REGISTRADO — EN VALIDACIÓN CONTABLE 24H (JID: {phone})"
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

        iva_line = f"🧾 *IVA:* ${saved.get('iva_amount', 0.0):.2f} REF\n" if saved.get('iva_amount', 0.0) > 0 else ""

        return {
            "reply": (
                "📋 *SOLICITUD Y PAGO REGISTRADOS EN EL SISTEMA* 📋\n\n"
                f"🎫 *TICKET OFICIAL SIS-COMER:* `{ticket_code}`\n"
                f"👤 *CLIENTE:* {saved['client_name']}\n"
                f"🪪 *CÉDULA:* {saved['cedula']}\n"
                f"📞 *TELÉFONO DE CONTACTO:* {final_phone}\n"
                f"📦 *ARTÍCULOS:* {saved['items_summary']}\n"
                f"{iva_line}"
                f"💵 *MONTO TOTAL:* ${saved['amount_usd']:.2f} REF (Bs. {saved['amount_ves']:,.2f})\n"
                f"🔢 *REF. DECLARADA:* `{saved['receipt_ref']}` ({saved['receipt_bank']})\n"
                f"📅 *FECHA DE RETIRO AGENDADA:* {format_date_dmy(saved['pickup_date'])}\n"
                f"⏰ *HORA ASIGNADA:* {saved['pickup_time']}\n"
                f"📍 *SEDE DE RETIRO:* {pickup_address}\n\n"
                "⏳ *ESTATUS ACTUAL:* 🟡 *PENDIENTE POR CONFIRMAR PAGO*\n\n"
                "⚠️ *AVISO DE VERIFICACIÓN DE PAGO:*\n"
                "Su comprobante ha sido registrado y se encuentra en proceso de revisión por nuestro departamento contable y administrativo.\n"
                "⏱️ *Los pagos toman hasta 24 horas hábiles en validarse y conciliarse en cuenta bancaria.* "
                "No se preocupe: su requerimiento está asegurado y en cuanto el personal valide los fondos, "
                "recibirá automáticamente por este mismo chat su notificación de *PAGO CONFIRMADO*.\n\n"
                "📌 *INSTRUCCIONES PARA EL RETIRO:*\n"
                "1. Al llegar a nuestra sede, diríjase al área de *Recepción* e indique que se dirige con el *Equipo de Comercialización (Piso 1)*.\n"
                f"2. En recepción notifique que viene a retirar su orden de: *{saved['items_summary']}*.\n"
                f"3. En el Piso 1 presente su Cédula de Identidad (*{saved['cedula']}*) y muestre este ticket oficial: *{ticket_code}* para validar su atención y hacerle entrega inmediata de su producto.\n\n"
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
                "👋 *¡Hola! ¿Cómo estás? Bienvenido a SIS-COMER.*\n"
                "🏭 *Complejo Industrial Tiuna — Equipo de Comercialización*\n\n"
                "Para poder atenderle y registrar su solicitud de compra, por favor indíquenos:\n\n"
                "✍️ *¿Me puedes indicar tu Nombre y Apellido completo?*"
            ),
            "image_url": None,
            "state": "REGISTER_NAME"
        }

    def _try_extract_all_registration_data(self, text: str, session: Dict[str, Any], phone: str):
        """Si el usuario envía todo en un bloque ej: 'Pedro Perez V-15432123 0414-1234567'"""
        ci = nlu.extract_cedula(text)
        ph = nlu.extract_phone(text)

        # Solo intentar extracción en bloque si al menos envió Cédula o Teléfono
        if not ci and not ph:
            return

        if ci and not session.get("cedula"):
            session["cedula"] = ci.upper()

        if ph and not session.get("contact_phone"):
            formatted = self._format_phone(ph)
            if formatted:
                session["contact_phone"] = formatted
        elif text.strip() == "1" and self._is_valid_phone(phone) and not session.get("contact_phone"):
            session["contact_phone"] = self._format_phone(phone)

        # Palabras de saludo y catálogo que no deben ser tomadas como nombre de persona
        greeting_words = {
            "HOLA", "BUENOS", "BUENAS", "BUENO", "BUEN", "DIAS", "DÍAS", "TARDES", "NOCHES",
            "SALUDOS", "SALUDO", "EPALE", "ÉPALE", "CORDIAL", "ESTIMADO", "ESTIMADA",
            "DIA", "DÍA", "INICIO", "MENU", "MENÚ", "0", "AYUDA", "POR", "FAVOR", "GRACIAS",
            "COMO", "CÓMO", "ESTAS", "ESTÁS", "ESTA", "ESTÁ", "USTED", "TU", "TÚ", "QUE", "QUÉ", "TAL",
            "AMIGO", "AMIGA", "HERMANO", "HERMANA",
            "SOY", "ME", "LLAMO", "MI", "NOMBRE", "ES", "YO",
            "CI", "CEDULA", "CÉDULA", "TLF", "TELEFONO", "TELÉFONO", "CEL", "CELULAR", "WHATSAPP", "NUMERO", "NÚMERO"
        }
        product_blacklist = {
            "PATRIOTA", "TIUNA", "CHAQUETA", "CHAQUETAS", "GORRA", "GORRAS", "BOTA", "BOTAS",
            "MILITAR", "MILITARES", "PARCHE", "PARCHES", "TACTICA", "TACTICO", "TACTICAS", "TACTICOS",
            "CAMPAÑA", "CAMPANA", "CAMUFLAJE", "VERDE", "NEGRO", "AZUL", "TALLA", "TALLAS",
            "COMPRAR", "QUIERO", "PRECIO", "PRECIOS", "COSTO", "COSTOS", "CUANTO", "CUÁNTO",
            "VALE", "TIENEN", "HAY", "STOCK", "DISPONIBLE", "CATALOGO", "CATÁLOGO", "PEDIDO",
            "ASESOR", "HUMANO", "AYUDA", "OPCION", "OPCIÓN", "UNIDADES", "CANTIDAD", "DESPACHO",
            "RETIRAR", "RETIRO", "PAGO", "TRANSFERENCIA", "PAGOMOVIL"
        }
        
        cleaned_for_name = text
        if ci:
            ci_raw = re.sub(r'^[VEJPGvejpg]-?', '', ci)
            cleaned_for_name = re.sub(r'\b(?:CI|C\.I\.|CEDULA|CÉDULA)?\s*' + re.escape(ci_raw) + r'\b', '', cleaned_for_name, flags=re.IGNORECASE)
            cleaned_for_name = re.sub(re.escape(ci), '', cleaned_for_name, flags=re.IGNORECASE)
        if ph:
            ph_raw = re.sub(r'\D', '', ph)
            cleaned_for_name = re.sub(r'\b(?:TLF|TELEFONO|TELÉFONO|CEL|CELULAR|WHATSAPP)?\s*' + re.escape(ph) + r'\b', '', cleaned_for_name, flags=re.IGNORECASE)
            cleaned_for_name = re.sub(re.escape(ph_raw), '', cleaned_for_name, flags=re.IGNORECASE)

        cleaned_for_name = re.sub(r'^(?:¡?hola!?\s*)?(?:buenas\s*(?:tardes|dias|días|noches)?\s*,?\s*)?(?:soy|me\s+llamo|mi\s+nombre\s+es)\s+', '', cleaned_for_name, flags=re.IGNORECASE)

        text_clean_words = re.sub(r'[^\w\s]', '', cleaned_for_name).upper().split()
        if text_clean_words and all(w in (greeting_words | product_blacklist) for w in text_clean_words):
            return

        # Si no tiene nombre y el texto tiene palabras válidas de persona
        if not session.get("client_name"):
            valid_words = [w for w in text_clean_words if w not in greeting_words and w not in product_blacklist and len(w) >= 2 and not any(c.isdigit() for c in w)]
            if len(valid_words) >= 2:
                session["client_name"] = " ".join(valid_words[:4]).title()
            elif len(valid_words) == 1 and session.get("state") == "REGISTER_NAME":
                session["client_name"] = valid_words[0].title()

    # -------------------------------------------------------------
    # MENÚS Y VISTAS
    # -------------------------------------------------------------
    def _build_catalog_menu(self, session: Dict[str, Any], is_off_hours: bool = False, skip_header: bool = False) -> Dict[str, Any]:
        # FILTRO ESTRICTO: ÚNICAMENTE PRODUCTOS CON STOCK > 0
        products = get_available_catalog_products()
        bcv_rate = bcv_service.get_rate_for_date()

        lines = []
        if is_off_hours:
            lines.append("🌙 *AVISO:* Nuestra sede física se encuentra cerrada en este momento. Sin embargo, nuestro sistema automatizado puede tomar su pedido y agendar su retiro en horario hábil.")
            lines.append("──────────────────────")

        if not skip_header:
            client_name = session.get("client_name")
            if client_name and client_name != "CLIENTE":
                lines.append(f"👋 ¡Hola, *{client_name}*! Bienvenido(a) a *SIS-COMER*.")
            else:
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

            lines.append(f"\n[ {waitlist_idx}️⃣ ] 📋 *¿Buscas otro modelo o artículo especial?*")
            lines.append(f"[ {advisor_idx}️⃣ ] 👨‍💼 *Hablar con un Asesor Comercial*")
            lines.append(f"\n👉 *Elige el número (1-{len(products)}) o escribe el producto que deseas:*")

        return {
            "reply": "\n".join(lines),
            "image_url": None,
            "state": session.get("state", "CATALOG")
        }

    def _build_cart_view(self, session: Dict[str, Any]) -> Dict[str, Any]:
        cart = session["cart"]
        lines = ["✅ *Usted ha elegido:*"]
        last_image = None
        for item in cart:
            sz_str = f" (Talla: {item['size']})" if item.get("size") else ""
            item_name = item.get("raw_name") or item.get("name")
            lines.append(f"• *{item['qty']}x {item_name}*{sz_str}")
            if item.get("image_url"):
                last_image = item["image_url"]

        lines.append("\n¿Desea agregar otro producto o proceder al pago?\n")
        lines.append("[ 1️⃣ ] ➕ *Agregar otro producto al pedido*")
        lines.append("[ 2️⃣ ] 💳 *Proceder al Pago previo*")
        lines.append("[ 3️⃣ ] 🗑️ *Vaciar selección / Cancelar*")
        lines.append("\n👉 *Responda 2 para pagar o 1 para agregar otro artículo.*")

        return {
            "reply": "\n".join(lines),
            "image_url": last_image,
            "state": "CART_VIEW"
        }

    def _build_payment_instructions(self, session: Dict[str, Any]) -> Dict[str, Any]:
        cart = session["cart"]
        subtotal_usd = sum(item["subtotal"] for item in cart)
        bcv_rate = bcv_service.get_rate_for_date()
        subtotal_ves = round(subtotal_usd * bcv_rate, 2)

        config = get_all_config()
        apply_iva = config.get("apply_iva", "0") == "1"
        iva_rate = float(config.get("iva_rate", "16"))
        if apply_iva:
            iva_usd = round(subtotal_usd * (iva_rate / 100.0), 2)
            total_usd = round(subtotal_usd + iva_usd, 2)
        else:
            iva_usd = 0.0
            total_usd = round(subtotal_usd, 2)

        iva_ves = round(iva_usd * bcv_rate, 2)
        total_ves = round(total_usd * bcv_rate, 2)

        session["subtotal_usd"] = subtotal_usd
        session["iva_amount"] = iva_usd
        session["amount_usd"] = total_usd
        session["amount_ves"] = total_ves
        session["bcv_rate_applied"] = bcv_rate

        total_items = sum(item["qty"] for item in cart)

        pm_bank = config.get("pagomovil_bank") or "BANCO DE VENEZUELA (0102)"
        pm_phone = config.get("pagomovil_phone") or "0412-1234567"
        pm_id = config.get("pagomovil_id") or "J-408123456"

        tr_bank = config.get("transfer_bank") or "BANCO DE VENEZUELA"
        tr_account = config.get("transfer_account") or "0102-0501-80-0000123456"
        tr_holder = config.get("transfer_holder") or "COMPLEJO INDUSTRIAL TIUNA"

        # Mensaje 1: Resumen y montos ($ y Bs)
        items_lines = []
        for it in cart:
            sz_str = f" (Talla: {it['size']})" if it.get("size") else ""
            item_name = it.get("raw_name") or it.get("name")
            items_lines.append(f"• *{it['qty']}x {item_name}*{sz_str} — ${it['subtotal']:.2f} Ref")

        iva_line = f"🧾 *IVA ({iva_rate:g}%):* ${iva_usd:.2f} REF *(Bs. {iva_ves:,.2f})*\n" if apply_iva else ""
        msg1_resumen = (
            "💳 *PAGO PREVIO OBLIGATORIO*\n\n"
            "🛒 *RESUMEN DE SU PEDIDO:*\n"
            + "\n".join(items_lines) + "\n"
            "──────────────────────\n"
            f"📊 *Total Artículos:* {total_items}\n"
            f"💵 *Subtotal:* ${subtotal_usd:.2f} REF *(Bs. {subtotal_ves:,.2f})*\n"
            f"{iva_line}"
            f"💰 *MONTO TOTAL:* ${total_usd:.2f} REF *(Bs. {total_ves:,.2f})*\n"
            f"📈 *Tasa Oficial BCV Hoy:* Bs. {bcv_rate:,.2f}/$"
        )

        # Mensaje 2: Pago Móvil limpio (con datos separados listos para copiar)
        msg2_pagomovil = (
            "📲 *PAGO MÓVIL (Toca los datos para copiar):*\n\n"
            f"🏦 *Banco:* {pm_bank.strip()}\n"
            f"📱 *Teléfono:* {pm_phone.strip()}\n"
            f"🪪 *RIF / C.I:* {pm_id.strip()}"
        )

        # Mensaje 3: Transferencia Bancaria limpia
        msg3_transferencia = (
            "🏛️ *TRANSFERENCIA BANCARIA NACIONAL:*\n\n"
            f"🏦 *Banco:* {tr_bank.strip()}\n"
            f"🔢 *Cuenta:* {tr_account.strip()}\n"
            f"👤 *Titular:* {tr_holder.strip()}\n"
            f"🪪 *RIF:* {pm_id.strip()}"
        )

        # Mensaje 4: Solicitud de comprobante y aviso de conciliación
        msg4_comprobante = (
            "📸 *CONSIGNACIÓN DE COMPROBANTE*\n\n"
            "Una vez realizado su pago, por favor *adjunte la foto o captura de su comprobante* en este chat para registrar su pedido y coordinar su retiro.\n\n"
            "⏱️ *Tiempo de Conciliación:* La verificación bancaria toma hasta *24 horas hábiles* por administración. Su requerimiento y agendamiento quedan garantizados y reservados inmediatamente al consignar el comprobante."
        )

        full_reply = f"{msg1_resumen}\n\n{msg2_pagomovil}\n\n{msg3_transferencia}\n\n{msg4_comprobante}"
        return {
            "reply": full_reply,
            "messages": [msg1_resumen, msg2_pagomovil, msg3_transferencia, msg4_comprobante],
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
        config = get_all_config()
        adv_phone = config.get("advisor_phone") or config.get("pagomovil_phone") or "0412-1234567"
        clean_digits = re.sub(r'\D', '', adv_phone)
        wa_digits = f"58{clean_digits[1:]}" if clean_digits.startswith("0") else (clean_digits if clean_digits.startswith("58") else f"58{clean_digits}")
        session = get_session(phone)
        session["state"] = "WAITING_ADVISOR"
        return {
            "reply": (
                "👨‍💼 *TRANSFERENCIA A ASESOR COMERCIAL SIS-COMER:*\n\n"
                "Para brindarle la mejor orientación y resolver cualquier duda técnica o requerimiento especial, "
                f"nuestro asesor comercial *{advisor_name}* se encuentra a su completa disposición:\n\n"
                f"📞 *Teléfono:* {adv_phone}\n"
                f"💬 *WhatsApp directo:* https://wa.me/{wa_digits}?text=Hola%2C%20necesito%20atenci%C3%B3n%20personalizada%20con%20un%20asesor\n\n"
                "👉 *También puede escribir su consulta técnica aquí mismo* y un agente de comercialización le responderá directamente por esta conversación.\n\n"
                "*(Escriba 0 en cualquier momento para regresar al menú principal)*"
            ),
            "image_url": None,
            "state": "WAITING_ADVISOR"
        }

    def _build_product_specs_response(self, session: Dict[str, Any], product: Dict[str, Any]) -> Dict[str, Any]:
        bcv_rate = bcv_service.get_rate_for_date()
        sheet_text = format_product_technical_sheet(product, bcv_rate=bcv_rate)
        session["last_specs_product"] = product
        session["state"] = "VIEWING_PRODUCT_SPECS"
        return {
            "reply": sheet_text,
            "image_url": product.get("image_url"),
            "state": "VIEWING_PRODUCT_SPECS"
        }

    def _build_product_specs_menu(self, session: Dict[str, Any]) -> Dict[str, Any]:
        products = get_available_catalog_products()
        lines = [
            "📋 *CONSULTA DE FICHAS TÉCNICAS REGLAMENTARIAS*",
            "🏭 *Complejo Industrial Tiuna — Confección Militar*",
            "──────────────────────",
            "Seleccione el artículo del cual desea conocer las especificaciones reglamentarias (tela, grosor, botones, durabilidad):\n"
        ]
        for idx, p in enumerate(products, 1):
            lines.append(f"[ {idx}️⃣ ] *{p['name']}*")

        lines.append("\n[ 0️⃣ ] 🔙 Volver al catálogo principal")
        lines.append(f"\n👉 *Responda con el número (1-{len(products)}) o escriba el nombre del artículo:*")
        session["state"] = "SELECTING_SPECS_PRODUCT"
        return {
            "reply": "\n".join(lines),
            "image_url": None,
            "state": "SELECTING_SPECS_PRODUCT"
        }


bot_manager = BotFlowManager()
