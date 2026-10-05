import re
import logging
from typing import Dict, Any, Optional, List
from rapidfuzz import fuzz, process
from app.config import GEMINI_API_KEY
from app.database import get_products

logger = logging.getLogger(__name__)

class NLUEngine:
    def __init__(self):
        self.api_key = GEMINI_API_KEY

    def analyze_message(self, text: str, current_state: str = "IDLE") -> Dict[str, Any]:
        """
        Analyzes inbound user text.
        Returns:
            {
                "intent": str,
                "matched_product": Optional[Dict],
                "confidence": float,
                "extracted_data": {
                    "cedula": Optional[str],
                    "name": Optional[str],
                    "phone": Optional[str],
                    "date": Optional[str],
                    "time": Optional[str]
                }
            }
        """
        clean_text = text.strip()
        lower_text = clean_text.lower()

        extracted_data = {
            "cedula": self.extract_cedula(clean_text),
            "phone": self.extract_phone(clean_text),
            "date": self.extract_date(clean_text),
            "time": self.extract_time(clean_text),
            "name": None
        }

        # 1. Check if user wants an advisor
        advisor_keywords = ["asesor", "asesoria", "asesoría", "humano", "persona", "operador", "agente", "hablar con alguien", "duda", "dudas", "pregunta"]
        if any(w in lower_text for w in advisor_keywords) and current_state != "COLLECTING_DATA":
            return {
                "intent": "CONNECT_ADVISOR",
                "matched_product": None,
                "confidence": 0.95,
                "extracted_data": extracted_data
            }

        # 2. Check shipping / questions (VCV, envíos, despachos)
        shipping_keywords = ["envio", "envíos", "envios", "vcv", "flete", "delivery", "hacen envios", "precio de envio"]
        if any(w in lower_text for w in shipping_keywords):
            return {
                "intent": "SHIPPING_INFO",
                "matched_product": self.match_product(clean_text),
                "confidence": 0.90,
                "extracted_data": extracted_data
            }

        # 3. Match product directly in the text
        matched_prod = self.match_product(clean_text)

        # 4. Check if user intends to schedule or confirms
        schedule_keywords = ["agendar", "retirar", "cita", "apartar", "quiero retirar", "cuando puedo retirar", "cuando retiro", "voy a buscar", "pasar buscando"]
        if any(w in lower_text for w in schedule_keywords):
            return {
                "intent": "SCHEDULE_PICKUP",
                "matched_product": matched_prod,
                "confidence": 0.88,
                "extracted_data": extracted_data
            }

        # 5. If a product was clearly identified
        if matched_prod:
            return {
                "intent": "PRODUCT_SELECTED",
                "matched_product": matched_prod,
                "confidence": 0.85,
                "extracted_data": extracted_data
            }

        # 6. Basic greetings
        greeting_words = ["hola", "buenas", "buenos dias", "buenos días", "buenas tardes", "buenas noches", "saludos", "que tal", "epale", "inicio", "empezar", "menu", "menú"]
        if any(w == lower_text or lower_text.startswith(w) for w in greeting_words):
            return {
                "intent": "GREETING",
                "matched_product": None,
                "confidence": 0.95,
                "extracted_data": extracted_data
            }

        # 7. Check if user sent structured data during form fill
        if extracted_data["cedula"] or (current_state == "COLLECTING_DATA" and len(clean_text) > 3):
            return {
                "intent": "DATA_SUBMISSION",
                "matched_product": None,
                "confidence": 0.80,
                "extracted_data": extracted_data
            }

        # 8. Numeric selection (e.g., "1", "2", "3")
        if clean_text.isdigit():
            return {
                "intent": "NUMERIC_OPTION",
                "value": int(clean_text),
                "matched_product": None,
                "confidence": 1.0,
                "extracted_data": extracted_data
            }

        # Default fallback
        return {
            "intent": "GENERAL_QUERY",
            "matched_product": None,
            "confidence": 0.50,
            "extracted_data": extracted_data
        }

    def match_product(self, text: str) -> Optional[Dict[str, Any]]:
        """
        Fuzzy matches user text with products in the database.
        """
        products = get_products(only_active=True)
        if not products:
            return None

        lower_text = text.lower()

        # Direct keyword checks first
        for p in products:
            # Check keywords
            keywords = [k.strip().lower() for k in (p.get("keywords") or "").split(",") if k.strip()]
            keywords.append(p["name"].lower())
            for kw in keywords:
                if kw in lower_text:
                    return p

        # Fuzzy matching against product names
        product_names = [p["name"] for p in products]
        best_match = process.extractOne(text, product_names, scorer=fuzz.partial_ratio)
        if best_match and best_match[1] >= 68:
            matched_name = best_match[0]
            for p in products:
                if p["name"] == matched_name:
                    return p

        return None

    def extract_cedula(self, text: str) -> Optional[str]:
        """
        Extracts Venezuelan / standard national IDs (V-12345678, E-12345678, or plain 6-9 digit numbers).
        """
        # Format V-12.345.678 or V12345678 or CI 12345678
        pattern_ci = r'(?:[VvEeJjGg][-\s]?)?(\d{1,2}(?:\.\d{3}){2}|\d{6,9})\b'
        match = re.search(pattern_ci, text)
        if match:
            raw = match.group(0).upper().replace(".", "").strip()
            # If it's just numbers, prepend V- if 6-9 digits
            if raw.isdigit() and 6 <= len(raw) <= 9:
                return f"V-{raw}"
            if raw.startswith(('V', 'E', 'J', 'G')) and len(raw) > 2:
                prefix = raw[0]
                digits = raw[1:].replace("-", "").strip()
                return f"{prefix}-{digits}"
            return raw
        return None

    def extract_phone(self, text: str) -> Optional[str]:
        """
        Extracts mobile phone numbers (e.g., 04121234567, +584141234567, 0424-1234567).
        """
        pattern = r'(?:\+?58\s?|0)?(412|414|424|416|426|212)[\s\.-]?(\d{3})[\s\.-]?(\d{4})\b'
        match = re.search(pattern, text)
        if match:
            return match.group(0).replace(" ", "").replace("-", "")
        return None

    def extract_date(self, text: str) -> Optional[str]:
        """
        Detects dates in text (DD/MM/YYYY, or words like mañana, lunes, viernes).
        """
        pattern = r'\b(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)\b'
        match = re.search(pattern, text)
        if match:
            return match.group(0)

        # Day words
        days = ["hoy", "mañana", "lunes", "martes", "miércoles", "miercoles", "jueves", "viernes", "sábado", "sabado"]
        lower = text.lower()
        for d in days:
            if d in lower:
                return d.capitalize()
        return None

    def extract_time(self, text: str) -> Optional[str]:
        """
        Detects time (e.g. 10:00 am, 2:30 pm, 10am, 3pm, 11:00).
        """
        pattern = r'\b(\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM|a\.m\.|p\.m\.|de la mañana|de la tarde))\b'
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(0)
        return None

# Singleton instance
nlu = NLUEngine()
