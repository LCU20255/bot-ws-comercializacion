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

        # 1. Detección Inteligente de Inconformidad o Mensajes Negativos (Quejas, mal servicio, retraso)
        negative_keywords = [
            "mal servicio", "pésimo servicio", "pesimo servicio", "terrible", "horrible", "que porquería",
            "que porqueria", "no sirve", "tardan mucho", "tardan demasiado", "demasiado lento", "lento",
            "no responden", "no entiendo nada", "no entiendo", "está mal", "esta mal", "muy mal", "estafa",
            "fraude", "engañan", "engaño", "desastre", "queja", "molesto", "molesta", "porqueria",
            "no me gusta", "atención pésima", "atencion pesima", "mala atencion", "mala atención"
        ]
        if any(neg in lower_text for neg in negative_keywords):
            return {
                "intent": "NEGATIVE_SENTIMENT",
                "matched_product": None,
                "confidence": 0.98,
                "extracted_data": extracted_data
            }

        # 2. Check if user wants an advisor or expresses dissatisfaction
        advisor_keywords = [
            "asesor", "asesoria", "asesoría", "humano", "persona", "operador", "agente",
            "hablar con alguien", "duda", "dudas", "pregunta", "preguntas",
            "prefiero hablar", "quiero hablar", "no me convence", "no me queda claro",
            "insatisfecho", "insatisfecha", "no me sirve", "no responde mi pregunta"
        ]
        if any(w in lower_text for w in advisor_keywords):
            return {
                "intent": "CONNECT_ADVISOR",
                "matched_product": None,
                "confidence": 0.95,
                "extracted_data": extracted_data
            }

        # 2.1 Check shipping / questions (VCV, envíos, despachos)
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

        # 3.1 Detección de Consultas de Ficha Técnica / Características Reglamentarias (Tela, Botones, Grosor, Duración)
        specs_keywords = [
            "caracteristica", "caracteristicas", "característica", "características",
            "especificacion", "especificaciones", "especificación", "especificaciones",
            "ficha tecnica", "ficha técnica", "descripcion tecnica", "descripción técnica",
            "reglamentario", "reglamentaria", "reglamentarios", "reglamentarias",
            "tela", "telas", "tipo de tela", "material", "materiales", "composicion", "composición",
            "cuanto dura", "cuánto dura", "duracion", "duración", "durabilidad", "resistencia",
            "grosor", "gramaje", "calibre", "espesor", "gruesa", "grueso",
            "boton", "botones", "cuantos botones", "cuántos botones",
            "cierre", "cierres", "cremallera", "velcro", "velcros",
            "costura", "costuras", "bolsillo", "bolsillos", "acabado", "acabados",
            "cuentame de", "cuéntame de", "detalles de", "como es", "cómo es",
            "de que esta hecho", "de qué está hecho", "que modelo es", "ficha"
        ]
        has_specs_query = any(re.search(r'\b' + re.escape(w) + r'\b', lower_text) for w in specs_keywords)

        if has_specs_query:
            if matched_prod:
                return {
                    "intent": "PRODUCT_SPECS",
                    "matched_product": matched_prod,
                    "confidence": 0.96,
                    "extracted_data": extracted_data
                }
            else:
                return {
                    "intent": "PRODUCT_SPECS_MENU",
                    "matched_product": None,
                    "confidence": 0.92,
                    "extracted_data": extracted_data
                }

        # 4. Check if user intends to schedule or confirms
        schedule_keywords = ["agendar", "retirar", "cita", "apartar", "quiero retirar", "cuando puedo retirar", "cuando retiro", "voy a buscar", "pasar buscando"]
        if any(w in lower_text for w in schedule_keywords):
            return {
                "intent": "SCHEDULE_PICKUP",
                "matched_product": matched_prod,
                "confidence": 0.88,
                "extracted_data": extracted_data
            }

        # 5. If a product was clearly identified for purchase / selection
        if matched_prod:
            return {
                "intent": "PRODUCT_SELECTED",
                "matched_product": matched_prod,
                "confidence": 0.85,
                "extracted_data": extracted_data
            }

        # 6. Basic greetings
        greeting_words = [
            "hola", "buenas", "buenos dias", "buenos días", "buenas tardes", "buenas noches",
            "saludos", "que tal", "qué tal", "epale", "épale", "inicio", "empezar", "menu", "menú",
            "como estas", "cómo estás", "como esta", "cómo está", "hola como estas", "hola cómo estás",
            "buenas como estas", "buenas cómo estás", "hola buenas", "hola amigo", "saludo", "buen dia", "buen día"
        ]
        if any(w == lower_text or lower_text.startswith(w) or f" {w} " in f" {lower_text} " for w in greeting_words):
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

    def match_product(self, text: str, products_list: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
        """
        Fuzzy matches user text with products in the database.
        Soporta plurales, singulares, raíces y palabras clave militares (tiunas, parches, patriota, etc.).
        """
        products = products_list if products_list is not None else get_products(only_active=True)
        if not products:
            return None

        import unicodedata
        def normalize_str(s: str) -> str:
            return ''.join(c for c in unicodedata.normalize('NFD', s.lower()) if unicodedata.category(c) != 'Mn')

        norm_text = normalize_str(text)
        text_words = [w.rstrip('s') for w in re.findall(r'\b[a-z0-9_-]{3,}\b', norm_text)]

        # 1. Búsqueda por palabras significativas distintivas del producto (singular/plural)
        best_p = None
        best_score = 0.0

        stop_words = {"para", "este", "esta", "como", "quiero", "necesito", "dame", "trae", "talla", "negro", "verde", "azul", "por"}

        for p in products:
            p_name_norm = normalize_str(p["name"])

            # Chequeo directo de subcadena exacta
            if p_name_norm in norm_text:
                return p

            # Palabras clave explícitas
            keywords = [normalize_str(k.strip()) for k in (p.get("keywords") or "").split(",") if k.strip()]
            for kw in keywords:
                if kw and kw in norm_text:
                    return p

            # Tokens significativos del nombre del producto (sin 's' final)
            p_words = [w.rstrip('s') for w in re.findall(r'\b[a-z0-9_-]{4,}\b', p_name_norm) if w not in stop_words]
            if not p_words:
                p_words = [w.rstrip('s') for w in re.findall(r'\b[a-z0-9_-]{3,}\b', p_name_norm) if w not in stop_words]

            # Contar coincidencias
            matches = sum(1 for pw in p_words if pw in text_words)
            if matches > 0 and len(p_words) > 0:
                score = (matches / len(p_words)) * 100.0
                if matches >= 2:
                    score += 50.0
                if score > best_score:
                    best_score = score
                    best_p = p

        if best_p and best_score >= 30.0:
            return best_p

        # 2. Fuzzy matching token_set_ratio y partial_ratio
        product_names = [p["name"] for p in products]
        best_token_set = process.extractOne(text, product_names, scorer=fuzz.token_set_ratio)
        if best_token_set and best_token_set[1] >= 65:
            matched_name = best_token_set[0]
            for p in products:
                if p["name"] == matched_name:
                    return p

        best_partial = process.extractOne(text, product_names, scorer=fuzz.partial_ratio)
        if best_partial and best_partial[1] >= 68:
            matched_name = best_partial[0]
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
        Detecta cualquier hora específica (ej: 9:01, 09:15 AM, 10:30, 2:15 PM, 14:00, etc.)
        Permite personalización exacta al minuto sin restringir a bloques fijos de 30 minutos.
        """
        # Formato HH:MM o H:MM con o sin AM/PM
        match_hm = re.search(r'\b([01]?\d|2[0-3])[:.]([0-5]\d)\s*(am|pm|a\.m\.|p\.m\.)?\b', text, re.IGNORECASE)
        if match_hm:
            h = int(match_hm.group(1))
            m = int(match_hm.group(2))
            period = match_hm.group(3)
            if period:
                is_pm = "p" in period.lower()
                if is_pm and h < 12:
                    h += 12
                elif not is_pm and h == 12:
                    h = 0
            # Convertir a formato 12h con AM/PM
            if h == 0:
                return f"12:{m:02d} AM"
            elif h < 12:
                return f"{h:02d}:{m:02d} AM"
            elif h == 12:
                return f"12:{m:02d} PM"
            else:
                return f"{h - 12:02d}:{m:02d} PM"

        # Formato simple 9am, 10 pm, 2pm
        match_h = re.search(r'\b([01]?\d|2[0-3])\s*(am|pm|a\.m\.|p\.m\.)\b', text, re.IGNORECASE)
        if match_h:
            h = int(match_h.group(1))
            period = match_h.group(2)
            is_pm = "p" in period.lower()
            if is_pm and h < 12:
                h += 12
            elif not is_pm and h == 12:
                h = 0
            if h == 0:
                return "12:00 AM"
            elif h < 12:
                return f"{h:02d}:00 AM"
            elif h == 12:
                return "12:00 PM"
            else:
                return f"{h - 12:02d}:00 PM"

        return None

    def extract_order_items(self, text: str, catalog_products: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        """
        Extrae de forma inteligente múltiples productos, cantidades (en dígitos o palabras) y tallas
        a partir de un único mensaje.
        Ejemplos soportados:
        - "quiero 25 tiunas y 10 parches de venezuela"
        - "3 patriota tiuna talla SR y 2 talla MR"
        - "un tiuna talla M"
        - "5 patriotas tiuna: 3 talla sr y 2 talla mr"
        - "una gorra táctica y 2 parches"
        """
        if catalog_products is None:
            catalog_products = get_products(only_active=True)
        if not catalog_products:
            return []

        spanish_numbers = {
            "un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
            "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
            "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
            "dieciseis": 16, "dieciséis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19,
            "veinte": 20, "veintiun": 21, "veintiuno": 21, "veintiuna": 21, "veintidos": 22, "veintidós": 22,
            "veintitres": 23, "veintitrés": 23, "veinticuatro": 24, "veinticinco": 25,
            "veintiseis": 26, "veintiséis": 26, "veintisiete": 27, "veintiocho": 28, "veintinueve": 29,
            "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80,
            "noventa": 90, "cien": 100
        }

        # Tallas militares venezolanas y estándares
        size_pattern = r'\b(?:talla\s*)?(SR|MR|LR|XLR|SL|ML|LL|XLL|SS|MS|LS|XLS|XXL|2XL|3XL|XS|XL|S|M|L|3[6-9]|4[0-8])\b'

        clean = text.strip()
        # Si tiene desglose con dos puntos (ej: "5 patriotas tiuna: 3 talla sr y 2 talla mr")
        colon_header_prod = None
        if ":" in clean:
            parts = clean.split(":", 1)
            header_prod = self.match_product(parts[0], catalog_products)
            if header_prod:
                colon_header_prod = header_prod
                clean = parts[1]

        # Dividir por separadores de lista: comas, ' y ', ' e ', saltos de línea, '+'
        raw_segments = re.split(r'[\n;+]|\s+y\s+|\s+e\s+|,', clean, flags=re.IGNORECASE)
        segments = [s.strip() for s in raw_segments if s.strip()]

        items: List[Dict[str, Any]] = []
        last_product = colon_header_prod

        for seg in segments:
            seg_lower = seg.lower()

            # 1. Extraer cantidad (en números o en palabras)
            qty = None
            digit_match = re.search(r'\b(\d+)\b', seg)
            if digit_match:
                try:
                    qty = int(digit_match.group(1))
                except Exception:
                    qty = None

            if qty is None:
                for word, num in spanish_numbers.items():
                    if re.search(rf'\b{word}\b', seg_lower):
                        qty = num
                        break

            # 2. Extraer talla
            size = None
            talla_explicit = re.search(r'\btalla\s*([a-zA-Z0-9_-]+)\b', seg, re.IGNORECASE)
            if talla_explicit:
                size = talla_explicit.group(1).upper()
            else:
                m_size = re.search(size_pattern, seg, re.IGNORECASE)
                if m_size:
                    size = m_size.group(1).upper()

            # 3. Identificar producto en el segmento
            matched = self.match_product(seg, catalog_products)
            if matched:
                last_product = matched
            elif last_product:
                # Si no menciona el producto pero sí talla o cantidad, hereda el último producto
                matched = last_product

            if matched:
                final_qty = max(1, qty) if qty is not None else 1
                items.append({
                    "product": matched,
                    "qty": final_qty,
                    "size": size
                })

        return items

# Singleton instance
nlu = NLUEngine()
