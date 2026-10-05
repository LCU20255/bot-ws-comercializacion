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
        if not self.token or not self.phone_number_id:
            logger.info(f"[SIMULADO] WhatsApp Outbound a {to_phone}:\n{text}")
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

wa_service = WhatsAppService()
