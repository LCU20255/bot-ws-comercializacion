import logging
import httpx
from typing import Dict, Any, Optional
from app.config import META_WA_TOKEN, META_WA_PHONE_NUMBER_ID, META_WA_VERIFY_TOKEN
from app.bot_flow import bot_manager

logger = logging.getLogger(__name__)

class WhatsAppService:
    def __init__(self):
        self.token = META_WA_TOKEN
        self.phone_number_id = META_WA_PHONE_NUMBER_ID
        self.verify_token = META_WA_VERIFY_TOKEN

    def verify_webhook(self, mode: Optional[str], token: Optional[str], challenge: Optional[str]) -> Optional[str]:
        if mode == "subscribe" and token == self.verify_token:
            logger.info("Webhook WhatsApp verificado correctamente.")
            return challenge
        return None

    async def handle_meta_webhook(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Processes standard WhatsApp Meta Cloud API webhook events.
        """
        try:
            entries = payload.get("entry", [])
            for entry in entries:
                changes = entry.get("changes", [])
                for change in changes:
                    value = change.get("value", {})
                    messages = value.get("messages", [])
                    contacts = value.get("contacts", [])
                    
                    user_name = "Cliente"
                    if contacts:
                        user_name = contacts[0].get("profile", {}).get("name", "Cliente")

                    for msg in messages:
                        from_number = msg.get("from")
                        msg_type = msg.get("type")
                        
                        body_text = ""
                        if msg_type == "text":
                            body_text = msg.get("text", {}).get("body", "")
                        elif msg_type == "interactive":
                            interactive = msg.get("interactive", {})
                            if interactive.get("type") == "button_reply":
                                body_text = interactive.get("button_reply", {}).get("title", "")
                            elif interactive.get("type") == "list_reply":
                                body_text = interactive.get("list_reply", {}).get("id", "")

                        if from_number and body_text:
                            # Process with bot flow
                            bot_response = bot_manager.process_message(from_number, body_text)
                            # Send response back to user
                            await self.send_message(
                                to_phone=from_number,
                                text=bot_response["reply"],
                                image_url=bot_response.get("image_url")
                            )

            return {"status": "success"}
        except Exception as e:
            logger.error(f"Error procesando webhook de WhatsApp: {e}")
            return {"status": "error", "message": str(e)}

    async def send_message(self, to_phone: str, text: str, image_url: Optional[str] = None) -> bool:
        """
        Sends an outbound message to a WhatsApp number.
        If Meta Cloud API credentials are configured, sends via official API.
        Otherwise logs the outbound message.
        """
        # 1. Intentar enviar a través del puente local de Baileys si está activo
        try:
            async with httpx.AsyncClient(timeout=3.0) as b_client:
                b_res = await b_client.post("http://127.0.0.1:3001/send", json={"phone": to_phone, "text": text})
                if b_res.status_code == 200:
                    logger.info(f"Mensaje WhatsApp enviado exitosamente vía Baileys a {to_phone}")
                    return True
        except Exception:
            pass

        # 2. Si no hay Baileys y tampoco Meta Cloud API configurado, registrar en log
        if not self.token or not self.phone_number_id:
            logger.info(f"[SIMULADO / LOG] WhatsApp Outbound a {to_phone}:\n{text}")
            return True

        url = f"https://graph.facebook.com/v21.0/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

        # If there is an image
        if image_url and (image_url.startswith("http://") or image_url.startswith("https://")):
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to_phone,
                "type": "image",
                "image": {
                    "link": image_url,
                    "caption": text[:1024]
                }
            }
        else:
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to_phone,
                "type": "text",
                "text": {"preview_url": True, "body": text}
            }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, json=payload, headers=headers)
                if res.status_code in [200, 201]:
                    logger.info(f"Mensaje WhatsApp enviado a {to_phone}")
                    return True
                else:
                    logger.error(f"Error enviando WhatsApp: {res.status_code} - {res.text}")
                    return False
        except Exception as e:
            logger.error(f"Excepción enviando mensaje WhatsApp a {to_phone}: {e}")
            return False

    def send_message_sync(self, to_phone: str, text: str, image_url: Optional[str] = None) -> bool:
        """Envío síncrono o despachado en hilo de fondo para facilitar llamadas desde handlers síncronos"""
        import asyncio
        import threading
        try:
            loop = asyncio.get_running_loop()
            asyncio.create_task(self.send_message(to_phone, text, image_url))
            return True
        except RuntimeError:
            t = threading.Thread(target=lambda: asyncio.run(self.send_message(to_phone, text, image_url)))
            t.daemon = True
            t.start()
            return True

    async def send_document(
        self,
        to_phone: str,
        document_path: Optional[str] = None,
        document_bytes: Optional[bytes] = None,
        file_name: str = "Comprobante_Orden_Compra.pdf",
        caption: str = ""
    ) -> bool:
        """
        Envía un documento PDF formal al número de WhatsApp del cliente.
        Prioriza puente Baileys local (http://127.0.0.1:3001/send), y fallback a log.
        """
        import os
        import base64

        # 1. Intentar enviar a través del puente Baileys local
        try:
            payload = {
                "phone": to_phone,
                "text": caption,
                "file_name": file_name
            }
            if document_path and os.path.exists(document_path):
                payload["document_path"] = str(document_path)
            elif document_bytes:
                payload["document_base64"] = base64.b64encode(document_bytes).decode("utf-8")

            async with httpx.AsyncClient(timeout=10.0) as b_client:
                b_res = await b_client.post("http://127.0.0.1:3001/send", json=payload)
                if b_res.status_code == 200:
                    logger.info(f"Documento PDF '{file_name}' enviado exitosamente vía Baileys a {to_phone}")
                    return True
        except Exception as e:
            logger.warning(f"No se pudo enviar PDF vía Baileys a {to_phone}: {e}")

        # 2. Si no hay conexión Baileys, registrar simulación
        logger.info(f"[SIMULADO / LOG] Documento PDF '{file_name}' despachado a WhatsApp {to_phone} con texto:\n{caption}")
        return True

    def send_document_sync(
        self,
        to_phone: str,
        document_path: Optional[str] = None,
        document_bytes: Optional[bytes] = None,
        file_name: str = "Comprobante_Orden_Compra.pdf",
        caption: str = ""
    ) -> bool:
        import asyncio
        import threading
        try:
            loop = asyncio.get_running_loop()
            asyncio.create_task(self.send_document(to_phone, document_path, document_bytes, file_name, caption))
            return True
        except RuntimeError:
            t = threading.Thread(target=lambda: asyncio.run(self.send_document(to_phone, document_path, document_bytes, file_name, caption)))
            t.daemon = True
            t.start()
            return True

wa_service = WhatsAppService()

def notify_waitlist_stock_available(product_id: int, product_name: str) -> int:
    """
    Detecta automáticamente si hay clientes en lista de espera para este producto
    y les envía el mensaje de aviso de reposición inmediata.
    """
    from app.database import get_pending_waitlist_for_product, mark_waitlist_notified
    from app.time_utils import now_vet

    pending = get_pending_waitlist_for_product(product_id, product_name)
    if not pending:
        return 0

    now_hour = now_vet().hour
    greeting = "Buenas tardes" if 12 <= now_hour < 19 else ("Buenos días" if now_hour < 12 else "Buenas noches")

    count = 0
    for item in pending:
        client_name = item.get("client_name") or "Estimado Cliente"
        phone = item["phone"]
        msg = (
            f"👋 ¡Hola, {client_name}! {greeting}.\n\n"
            "Nos estamos comunicando de *Complejo Industrial Tiuna — Equipo de Comercialización*.\n\n"
            f"📦 Le informamos que el producto *{product_name}* que estaba esperando ya se encuentra *DISPONIBLE* en nuestro inventario.\n\n"
            "Puede responder a este mensaje en cualquier momento para coordinar y procesar su solicitud. ¡Estamos a su entera orden!"
        )
        wa_service.send_message_sync(phone, msg)
        mark_waitlist_notified(item["id"])
        count += 1
        logger.info(f"Cliente {client_name} ({phone}) notificado por reposición de stock de {product_name}")

    return count

