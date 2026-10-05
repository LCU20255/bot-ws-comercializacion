import logging
import re
from typing import Dict, Any, List, Optional
from datetime import datetime
from app.nlu_engine import nlu
from app.database import (
    get_products,
    get_product_by_id,
    create_order,
    get_all_config,
    is_maintenance_active,
    is_within_business_hours
)

logger = logging.getLogger(__name__)

# Memoria de sesiones de usuario activas
user_sessions: Dict[str, Dict[str, Any]] = {}

def get_session(phone: str) -> Dict[str, Any]:
    if phone not in user_sessions:
        user_sessions[phone] = {
            "state": "IDLE",
            "cart": [],              # Lista de productos: [{"id", "name", "qty", "price", "subtotal"}]
            "client_name": None,     # En mayúsculas
            "cedula": None,          # En mayúsculas
            "phone": phone,
            "pickup_date": None,     # YYYY-MM-DD
            "pickup_time": None,     # HH:MM AM/PM
            "payment_method": None,  # EFECTIVO / DIVISAS, TRANSFERENCIA, PAGO MÓVIL
            "is_off_hours": 0,
            "last_interaction": datetime.now()
        }
    return user_sessions[phone]

def reset_session(phone: str):
    user_sessions[phone] = {
        "state": "IDLE",
        "cart": [],
        "client_name": None,
        "cedula": None,
        "phone": phone,
        "pickup_date": None,
        "pickup_time": None,
        "payment_method": None,
        "is_off_hours": 0,
        "last_interaction": datetime.now()
    }

class BotFlowManager:
    def __init__(self):
        pass

    def process_message(self, phone: str, text: str) -> Dict[str, Any]:
        """
        Punto principal de procesamiento de mensajes entrantes.
        """
        clean_text = text.strip()
        session = get_session(phone)
        session["last_interaction"] = datetime.now()
        current_state = session["state"]

        config = get_all_config()
        advisor_phone = config.get("advisor_phone", "+584121234567")
        advisor_name = config.get("advisor_name", "ASESOR COMERCIAL")
        pickup_address = config.get("pickup_address", "SEDE PRINCIPAL DE COMERCIALIZACIÓN E INTENDENCIA")
        pickup_hours = config.get("pickup_hours", "LUNES A VIERNES DE 8:00 AM A 5:00 PM")

        # 1. VERIFICAR MODO MANTENIMIENTO
        if is_maintenance_active():
            maint_msg = config.get(
                "maintenance_message",
                "¡Hola! En este momento nos encontramos en proceso de mantenimiento. Por favor comunícate con nosotros el día de mañana de 8:00 AM a 5:00 PM."
            )
            # Guardar el contacto para poder retomar la atención luego desde el panel
            try:
                create_order({
                    "client_name": "CONTACTO POR ATENDER",
                    "cedula": "SIN CÉDULA",
                    "phone": phone,
                    "items_summary": "CONSULTA RECIBIDA EN MODO MANTENIMIENTO",
                    "items_detail": [{"name": "CONSULTA EN MANTENIMIENTO", "qty": 1, "subtotal": 0.0}],
                    "total_items": 1,
                    "total_amount": 0.0,
                    "payment_method": "POR DEFINIR",
                    "pickup_date": datetime.now().strftime('%Y-%m-%d'),
                    "pickup_time": datetime.now().strftime('%I:%M %p'),
                    "status": "EN ESPERA POR MANTENIMIENTO",
                    "is_off_hours": 0,
                    "notes": f"Mensaje recibido del cliente: '{clean_text}'"
                })
            except Exception as e:
                logger.error(f"Error registrando contacto en mantenimiento: {e}")

            return {
                "reply": f"🛑 *AVISO DE MANTENIMIENTO*\n\n{maint_msg}\n\n📲 *Contacto directo con Asesor:* {advisor_phone}",
                "image_url": None,
                "state": "MAINTENANCE"
            }

        # 2. VERIFICAR HORARIO LABORAL (8:00 AM a 5:00 PM)
        is_off_hours = not is_within_business_hours()
        if is_off_hours:
            session["is_off_hours"] = 1

        # Comandos globales de reinicio o volver al menú (0 o menú)
        if clean_text in ["0", "menu", "menú", "inicio", "empezar", "reset", "cancelar"]:
            reset_session(phone)
            return self._build_catalog_menu(is_off_hours=is_off_hours)

        # Comando de Asesor Comercial
        if clean_text.lower() in ["asesor", "humano", "asesoria", "asesoría", "ayuda"]:
            return self._build_advisor_response(phone, advisor_name)

        # Analizar intención mediante NLU
        analysis = nlu.analyze_message(clean_text, current_state=current_state)
        intent = analysis["intent"]
        matched_product = analysis["matched_product"]
        extracted = analysis["extracted_data"]

        # Si el cliente menciona un producto directamente en cualquier momento
        if matched_product and current_state not in ["COLLECTING_DATA", "SELECT_PAYMENT", "CONFIRMING", "SELECTING_SIZE", "WAITING_ADVISOR"]:
            qty = self._extract_quantity(clean_text)
            if self._product_needs_size(matched_product):
                session["pending_item"] = {"product": matched_product, "qty": qty}
                session["state"] = "SELECTING_SIZE"
                return self._prompt_for_size(matched_product)
            else:
                self._add_to_cart(session, matched_product, qty)
                session["state"] = "CART_VIEW"
                return self._build_cart_view(session)

        # MÁQUINA DE ESTADOS
        if current_state == "WAITING_ADVISOR":
            return {
                "reply": "👍 *Mensaje recibido.*\n\nUn asesor comercial de nuestro equipo atenderá tu consulta por este mismo chat a la brevedad.\n\n*(Escribe 0 si deseas volver al menú automatizado)*",
                "image_url": None,
                "state": "WAITING_ADVISOR"
            }

        elif current_state == "IDLE":
            if intent == "CONNECT_ADVISOR":
                return self._build_advisor_response(phone, advisor_name)

            if intent == "NUMERIC_OPTION":
                val = analysis.get("value")
                products = get_products(only_active=True)
                if val and 1 <= val <= len(products):
                    selected = products[val - 1]
                    if self._product_needs_size(selected):
                        session["pending_item"] = {"product": selected, "qty": 1}
                        session["state"] = "SELECTING_SIZE"
                        return self._prompt_for_size(selected)
                    else:
                        self._add_to_cart(session, selected, qty=1)
                        session["state"] = "CART_VIEW"
                        return self._build_cart_view(session)
                elif val == len(products) + 1:
                    return self._build_advisor_response(phone, advisor_name)
                elif val == 0:
                    reset_session(phone)
                    return self._build_catalog_menu(is_off_hours=is_off_hours)
                else:
                    return {
                        "reply": "⚠️ Opción no válida. Por favor selecciona el número de la lista o escribe el producto que deseas solicitar.",
                        "image_url": None,
                        "state": "IDLE"
                    }

            return self._build_catalog_menu(is_off_hours=is_off_hours)

        elif current_state == "SELECTING_SIZE":
            pending = session.get("pending_item")
            if not pending:
                session["state"] = "IDLE"
                return self._build_catalog_menu(is_off_hours=is_off_hours)

            size_str = clean_text.strip().upper()
            self._add_to_cart(session, pending["product"], pending["qty"], size=size_str)
            session["pending_item"] = None
            session["state"] = "CART_VIEW"
            return self._build_cart_view(session)

        elif current_state == "CART_VIEW":
            # Opciones del carrito: 1 = Agregar otro producto, 2 = Proceder a agendar, 3 = Modificar cantidades, 4 = Vaciar
            if clean_text in ["1", "otro", "agregar otro", "mas", "más"]:
                return {
                    "reply": self._build_catalog_menu(is_off_hours=False)["reply"] + "\n\n👉 *Escribe el número del producto adicional o su nombre y cantidad (ej: 2 parches):*",
                    "image_url": None,
                    "state": "ADDING_MORE"
                }

            elif clean_text in ["2", "agendar", "proceder", "si", "sí", "continuar"]:
                if not session["cart"]:
                    return self._build_catalog_menu()
                session["state"] = "COLLECTING_DATA"
                return self._start_scheduling(session, extracted)

            elif clean_text in ["3", "vaciar", "cancelar", "borrar"]:
                session["cart"] = []
                session["state"] = "IDLE"
                return {
                    "reply": "🗑️ Tu solicitud ha sido vaciada.\n\nEscribe *0* para consultar el catálogo nuevamente.",
                    "image_url": None,
                    "state": "IDLE"
                }
            else:
                return self._build_cart_view(session)

        elif current_state == "ADDING_MORE":
            qty = self._extract_quantity(clean_text)
            target_prod = matched_product
            if not target_prod and clean_text.isdigit():
                val = int(clean_text)
                products = get_products(only_active=True)
                if 1 <= val <= len(products):
                    target_prod = products[val - 1]

            if target_prod:
                if self._product_needs_size(target_prod):
                    session["pending_item"] = {"product": target_prod, "qty": qty}
                    session["state"] = "SELECTING_SIZE"
                    return self._prompt_for_size(target_prod)
                else:
                    self._add_to_cart(session, target_prod, qty)
                    session["state"] = "CART_VIEW"
                    return self._build_cart_view(session)

            return {
                "reply": "⚠️ No pudimos identificar el producto adicional. Por favor escribe el nombre o número de la lista (ej: *3 parches* o *1 gorra*):",
                "image_url": None,
                "state": "ADDING_MORE"
            }

        elif current_state == "COLLECTING_DATA":
            # Parseo inteligente de datos personales (Nombre, Cédula, Fecha/Hora)
            self._parse_data_block(clean_text, session, extracted)

            if not session["client_name"]:
                if not any(char.isdigit() for char in clean_text) and len(clean_text) > 3:
                    session["client_name"] = clean_text.strip().upper()
                else:
                    return {
                        "reply": (
                            "✍️ *DATOS DE AGENDAMIENTO Y RETIRO*\n\n"
                            "Por favor, indícanos tu *NOMBRE Y APELLIDO COMPLETO*:"
                        ),
                        "image_url": None,
                        "state": "COLLECTING_DATA"
                    }

            if not session["cedula"]:
                return {
                    "reply": (
                        f"Atendido, *{session['client_name']}*.\n\n"
                        "🪪 Indícanos tu *NÚMERO DE CÉDULA DE IDENTIDAD* (Solo números o formato V-12345678):"
                    ),
                    "image_url": None,
                    "state": "COLLECTING_DATA"
                }

            if not session["pickup_date"] or not session["pickup_time"]:
                return {
                    "reply": (
                        f"👤 *CLIENTE:* {session['client_name']}\n"
                        f"🪪 *CÉDULA:* {session['cedula']}\n\n"
                        f"📍 *SEDE DE RETIRO:* {pickup_address}\n"
                        f"⏰ *HORARIO:* {pickup_hours}\n\n"
                        "📅 Indícanos la *FECHA Y HORA ESTIMADA* en la que vendrás a retirar tu pedido\n"
                        "*(Ejemplo: 2026-10-12 a las 09:30 AM o Mañana a las 10:00 AM)*:"
                    ),
                    "image_url": None,
                    "state": "COLLECTING_DATA"
                }

            # Datos completados, pasar a selección de Método de Pago
            session["state"] = "SELECT_PAYMENT"
            return self._build_payment_menu()

        elif current_state == "SELECT_PAYMENT":
            payment_map = {
                "1": "EFECTIVO / DIVISAS (PAGO AL RETIRAR)",
                "2": "TRANSFERENCIA BANCARIA",
                "3": "PAGO MÓVIL"
            }
            clean_choice = clean_text.strip()
            if clean_choice in payment_map:
                session["payment_method"] = payment_map[clean_choice]
                session["state"] = "CONFIRMING"
                return self._build_confirmation_card(session)
            elif "efectivo" in clean_choice.lower() or "divisa" in clean_choice.lower():
                session["payment_method"] = "EFECTIVO / DIVISAS (PAGO AL RETIRAR)"
                session["state"] = "CONFIRMING"
                return self._build_confirmation_card(session)
            elif "transferencia" in clean_choice.lower():
                session["payment_method"] = "TRANSFERENCIA BANCARIA"
                session["state"] = "CONFIRMING"
                return self._build_confirmation_card(session)
            elif "movil" in clean_choice.lower() or "móvil" in clean_choice.lower():
                session["payment_method"] = "PAGO MÓVIL"
                session["state"] = "CONFIRMING"
                return self._build_confirmation_card(session)
            else:
                return self._build_payment_menu(error=True)

        elif current_state == "CONFIRMING":
            if clean_text in ["1", "si", "sí", "confirmar", "correcto"]:
                # Generar orden en Base de Datos
                cart = session["cart"]
                total_items = sum(item["qty"] for item in cart)
                total_amount = sum(item["subtotal"] for item in cart)
                items_summary = " + ".join([f"{item['qty']}X {item['name']}" for item in cart])

                order_data = {
                    "client_name": session["client_name"],
                    "cedula": session["cedula"],
                    "phone": session["phone"] or phone,
                    "items_summary": items_summary,
                    "items_detail": cart,
                    "total_items": total_items,
                    "total_amount": total_amount,
                    "payment_method": session["payment_method"],
                    "pickup_date": session["pickup_date"],
                    "pickup_time": session["pickup_time"],
                    "status": "PENDIENTE POR ATENCIÓN",
                    "is_off_hours": session["is_off_hours"],
                    "notes": "GENERADO VÍA WHATSAPP BAILEYS"
                }

                saved = create_order(order_data)
                ticket_code = saved["ticket_code"]

                # Limpiar sesión
                reset_session(phone)

                return {
                    "reply": (
                        "🎉 *¡PEDIDO Y CITA AGENDADOS CON ÉXITO!* 🎉\n\n"
                        f"🎫 *NRO. DE TICKET:* `{ticket_code}`\n"
                        f"👤 *CLIENTE:* {saved['client_name']}\n"
                        f"🪪 *CÉDULA:* {saved['cedula']}\n"
                        f"📦 *PRODUCTOS:* {saved['items_summary']}\n"
                        f"📊 *TOTAL ARTÍCULOS:* {saved['total_items']}\n"
                        f"💰 *MONTO TOTAL:* ${saved['total_amount']:.2f} REF\n"
                        f"💳 *MÉTODO DE PAGO:* {saved['payment_method']}\n"
                        f"📅 *FECHA DE RETIRO:* {saved['pickup_date']}\n"
                        f"⏰ *HORA ESTIMADA:* {saved['pickup_time']}\n"
                        f"📍 *LUGAR DE ENTREGA:* {pickup_address}\n\n"
                        "📌 *INSTRUCCIONES DE RETIRO:*\n"
                        "1. Presentar cédula de identidad laminada en recepción.\n"
                        f"2. Indicar su ticket de atención: *{ticket_code}*.\n"
                        "3. Si seleccionó pago en divisas, cancela directamente al recibir su mercancía.\n\n"
                        "¡Gracias por su confianza! Escriba *Menú* para realizar una nueva solicitud."
                    ),
                    "image_url": None,
                    "state": "COMPLETED",
                    "ticket_code": ticket_code
                }

            elif clean_text in ["2", "modificar", "corregir"]:
                session["state"] = "COLLECTING_DATA"
                session["client_name"] = None
                session["cedula"] = None
                session["pickup_date"] = None
                session["pickup_time"] = None
                return {
                    "reply": "Entendido. Registraremos los datos nuevamente.\n\nPor favor indícanos tu *NOMBRE Y APELLIDO COMPLETO*:",
                    "image_url": None,
                    "state": "COLLECTING_DATA"
                }

            elif clean_text in ["3", "cancelar"]:
                reset_session(phone)
                return {
                    "reply": "❌ El proceso de solicitud ha sido cancelado.\n\nEscribe *Menú* para consultar nuestros productos nuevamente.",
                    "image_url": None,
                    "state": "IDLE"
                }
            else:
                return self._build_confirmation_card(session)

        # Fallback
        reset_session(phone)
        return self._build_catalog_menu()

    # MÉTODOS AUXILIARES
    def _safe_float(self, val) -> float:
        if isinstance(val, (int, float)):
            return float(val)
        if not val:
            return 0.0
        # Extraer primer número decimal o entero de un string tipo "18.00 Ref / Juego"
        match = re.search(r'(\d+(?:\.\d+)?)', str(val))
        if match:
            return float(match.group(1))
        return 0.0

    def _product_needs_size(self, product: Dict[str, Any]) -> bool:
        # 1. Si está definido explícitamente en la base de datos (0 o 1)
        req_size = product.get("requires_size")
        if req_size is not None and req_size in (0, 1):
            return bool(req_size == 1)

        name = str(product.get("name", "")).lower()
        cat = str(product.get("category", "")).lower()

        # 2. Artículos no portables o que nunca llevan talla (parches, barras, presillas, etc.)
        non_clothing = [
            "parche", "barra", "presilla", "condecoracion", "condecoración",
            "insignia", "distintivo", "escudo", "porta credencial", "banderín"
        ]
        if any(k in name for k in non_clothing) or any(k in cat for k in non_clothing):
            return False

        # 3. Artículos que son prendas de ropa, uniformes o calzado
        clothing_keywords = [
            "uniforme", "bota", "calzado", "camisa", "pantalon", "pantalón",
            "chemise", "zapato", "boina", "gorra", "franela", "chaqueta", "suéter", "traje", "guante"
        ]
        return any(k in name for k in clothing_keywords) or any(k in cat for k in ["textil", "calzado", "ropa", "uniforme"])

    def _prompt_for_size(self, product: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "reply": (
                f"📏 Para confeccionar y apartar *{product['name']}*, por favor indícanos tu *TALLA*:\n\n"
                "• *Para Uniformes o Ropa:* S, M, L, XL, XXL (o talla de pantalón ej. 30, 32, 34, 36)\n"
                "• *Para Botas o Calzado:* 38, 39, 40, 41, 42, 43, 44, 45\n\n"
                "👉 *Responde con tu talla a continuación:*"
            ),
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

        # Si ya existe en el carrito con la misma talla, sumar cantidad
        for item in cart:
            if item["product_id"] == pid and item.get("size") == size:
                item["qty"] += qty
                item["subtotal"] = item["qty"] * unit_price
                return

        # Si no existe, agregar nuevo
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

    def _parse_data_block(self, text: str, session: Dict[str, Any], extracted: Dict[str, Any]):
        if extracted.get("cedula") and not session["cedula"]:
            session["cedula"] = str(extracted["cedula"]).upper()
        if extracted.get("date") and not session["pickup_date"]:
            session["pickup_date"] = extracted["date"]
        if extracted.get("time") and not session["pickup_time"]:
            session["pickup_time"] = extracted["time"]

        chunks = [c.strip() for c in re.split(r'[\n,]', text) if c.strip()]
        for c in chunks:
            ci = nlu.extract_cedula(c)
            if ci and not session["cedula"]:
                session["cedula"] = ci.upper()
                continue

            dt = nlu.extract_date(c)
            if dt and not session["pickup_date"]:
                session["pickup_date"] = dt

            tm = nlu.extract_time(c)
            if tm and not session["pickup_time"]:
                session["pickup_time"] = tm

            if not session["client_name"] and len(c.split()) >= 2 and not any(ch.isdigit() for ch in c):
                session["client_name"] = c.strip().upper()

    def _build_catalog_menu(self, is_off_hours: bool = False) -> Dict[str, Any]:
        products = get_products(only_active=True)
        config = get_all_config()

        lines = []
        if is_off_hours:
            lines.append("🌙 *AVISO DE HORARIO:* Nos encontramos fuera de nuestro horario de atención presencial (8:00 AM a 5:00 PM). Sin embargo, *puedes autogestionar tu pedido ahora mismo* y quedará resguardado para retiro.")
            lines.append("──────────────────────")

        lines.append("👋 *¡Hola! Bienvenido al Sistema de Atención Automatizada e Intendencia Militar.*\n")
        lines.append("Actualmente disponemos de los siguientes productos para *entrega inmediata*:\n")
        for idx, p in enumerate(products, 1):
            price_val = self._safe_float(p.get("price", 0.0))
            price_str = p.get("price_display") or f"${price_val:.2f} Ref"
            lines.append(f"{idx}. *{p['name']}* — {price_str}")

        lines.append(f"\n{len(products) + 1}. 👨‍💼 *Hablar con un Asesor Comercial*")
        lines.append("\n👉 *¿Qué deseas realizar?*")
        lines.append("• Responde con el *número o nombre del producto* que deseas adquirir.")
        lines.append(f"• Responde *{len(products) + 1}* o escribe *'Asesor'* si deseas atención personalizada.")
        lines.append("• Escribe *0* en cualquier momento para regresar al menú principal.")

        return {
            "reply": "\n".join(lines),
            "image_url": None,
            "state": "IDLE"
        }

    def _build_cart_view(self, session: Dict[str, Any]) -> Dict[str, Any]:
        cart = session["cart"]
        total_items = sum(item["qty"] for item in cart)
        total_amount = sum(item["subtotal"] for item in cart)

        lines = ["🛒 *TU SOLICITUD ACTUAL:*\n"]
        last_image = None
        for item in cart:
            lines.append(f"• *{item['qty']}x {item['name']}* — ${item['subtotal']:.2f} Ref")
            if item.get("image_url"):
                last_image = item["image_url"]

        lines.append("──────────────────────")
        lines.append(f"📊 *Total Artículos:* {total_items}")
        lines.append(f"💰 *Monto Estimado:* ${total_amount:.2f} Ref\n")
        lines.append("¿Deseas agregar más productos o proceder con el agendamiento?")
        lines.append("1️⃣ *Agregar otro producto*")
        lines.append("2️⃣ *Proceder con el Agendamiento de Retiro*")
        lines.append("3️⃣ *Vaciar carrito / Cancelar*")

        return {
            "reply": "\n".join(lines),
            "image_url": last_image,
            "state": "CART_VIEW"
        }

    def _start_scheduling(self, session: Dict[str, Any], extracted: Dict[str, Any]) -> Dict[str, Any]:
        if extracted.get("cedula"):
            session["cedula"] = str(extracted["cedula"]).upper()
        if extracted.get("date"):
            session["pickup_date"] = extracted["date"]
        if extracted.get("time"):
            session["pickup_time"] = extracted["time"]

        cart = session["cart"]
        summary = " + ".join([f"{i['qty']}X {i['name']}" for i in cart])

        return {
            "reply": (
                f"📝 *AGENDAMIENTO DE RETIRO*\n"
                f"📦 *Productos:* {summary}\n\n"
                "Para preparar tu pedido y coordinar el retiro en sede, facilítanos tus datos:\n\n"
                "👉 Indícanos tu *NOMBRE Y APELLIDO COMPLETO*:\n"
                "*(O puedes enviar en un solo mensaje: Nombre, Cédula y Fecha/Hora deseada)*"
            ),
            "image_url": None,
            "state": "COLLECTING_DATA"
        }

    def _build_payment_menu(self, error: bool = False) -> Dict[str, Any]:
        err_msg = "⚠️ Opción no válida. Por favor selecciona 1, 2 o 3.\n\n" if error else ""
        text = (
            f"{err_msg}💳 *SELECCIONA TU MÉTODO DE PAGO:*\n"
            "*(Recuerda que el pago se valida o entrega al momento de retirar tu pedido en sede)*\n\n"
            "1️⃣ *Efectivo / Divisas (Cancelas al retirar en sede)*\n"
            "2️⃣ *Transferencia Bancaria Nacional*\n"
            "3️⃣ *Pago Móvil*\n\n"
            "👉 *Responde con el número de la opción (1, 2 o 3):*"
        )
        return {
            "reply": text,
            "image_url": None,
            "state": "SELECT_PAYMENT"
        }

    def _build_confirmation_card(self, session: Dict[str, Any]) -> Dict[str, Any]:
        cart = session["cart"]
        total_items = sum(item["qty"] for item in cart)
        total_amount = sum(item["subtotal"] for item in cart)
        summary = " + ".join([f"{i['qty']}X {i['name']}" for i in cart])
        config = get_all_config()
        pickup_address = config.get("pickup_address", "SEDE DE INTENDENCIA MILITAR")

        text = (
            "📋 *RESUMEN FINAL DE SU PEDIDO:*\n\n"
            f"👤 *CLIENTE:* {session['client_name']}\n"
            f"🪪 *CÉDULA:* {session['cedula']}\n"
            f"📞 *TELÉFONO:* {session['phone']}\n"
            f"📦 *PEDIDO:* {summary}\n"
            f"📊 *TOTAL ARTÍCULOS:* {total_items}\n"
            f"💰 *MONTO A PAGAR:* ${total_amount:.2f} REF\n"
            f"💳 *FORMA DE PAGO:* {session['payment_method']}\n"
            f"📅 *FECHA DE RETIRO:* {session['pickup_date']}\n"
            f"⏰ *HORA:* {session['pickup_time']}\n"
            f"📍 *SEDE:* {pickup_address}\n\n"
            "¿Todos los datos son correctos?\n"
            "1️⃣ *Sí, confirmar pedido y generar ticket*\n"
            "2️⃣ *Corregir datos*\n"
            "3️⃣ *Cancelar pedido*"
        )
        return {
            "reply": text,
            "image_url": None,
            "state": "CONFIRMING"
        }

    def _build_advisor_response(self, phone: str, advisor_name: str = "Asesor Comercial") -> Dict[str, Any]:
        # Registrar contacto en la base de datos como PENDIENTE POR ATENCIÓN
        try:
            create_order({
                "client_name": "CONTACTO POR ATENDER",
                "cedula": "SIN CÉDULA",
                "phone": phone,
                "items_summary": "SOLICITUD DE ASESOR HUMANO (MISMO WHATSAPP)",
                "items_detail": [{"name": "SOLICITUD ASESOR HUMANO", "qty": 1, "subtotal": 0.0}],
                "total_items": 1,
                "total_amount": 0.0,
                "payment_method": "POR DEFINIR",
                "pickup_date": datetime.now().strftime('%Y-%m-%d'),
                "pickup_time": datetime.now().strftime('%I:%M %p'),
                "status": "PENDIENTE POR ATENCIÓN",
                "is_off_hours": 0,
                "notes": "Cliente solicitó hablar con un asesor comercial por esta misma línea de WhatsApp."
            })
        except Exception as e:
            logger.error(f"Error registrando solicitud de asesor: {e}")

        return {
            "reply": (
                "👨‍💼 *TRANSFERENCIA A ASESOR COMERCIAL:*\n\n"
                "Has solicitado atención con nuestro equipo comercial. "
                f"Nuestro asesor *{advisor_name}* tomará este mismo chat para atenderte a la brevedad posible.\n\n"
                "👉 *Por favor indícanos tu requerimiento o consulta aquí mismo.* Te responderemos directamente por esta conversación.\n\n"
                "*(Escribe 0 en cualquier momento para regresar al menú automatizado)*"
            ),
            "image_url": None,
            "state": "WAITING_ADVISOR"
        }

bot_manager = BotFlowManager()
