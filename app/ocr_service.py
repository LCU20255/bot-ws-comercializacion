import os
import re
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
from PIL import Image

logger = logging.getLogger(__name__)

# Listado de bancos venezolanos comunes en comprobantes
VENEZUELAN_BANKS = [
    ("BANCO DE VENEZUELA", ["BANCO DE VENEZUELA", "BDV", "PAGOMOVILBDV", "BDVENLINEA"]),
    ("BANESCO", ["BANESCO", "BANESCO EN LINEA", "PAGO MOVIL BANESCO"]),
    ("MERCANTIL", ["MERCANTIL", "BANCO MERCANTIL", "TPAGO"]),
    ("BBVA PROVINCIAL", ["BBVA", "PROVINCIAL", "DINERO RAPIDO"]),
    ("BANCAMIGA", ["BANCAMIGA", "PAGO MOVIL BANCAMIGA"]),
    ("BNC", ["BNC", "BANCO NACIONAL DE CREDITO"]),
    ("BANCO DEL TESORO", ["BANCO DEL TESORO", "TESORO"]),
    ("BANCO BICENTENARIO", ["BICENTENARIO"]),
    ("BANCARIBE", ["BANCARIBE", "MIPAGO"])
]

class ReceiptOCRService:
    """
    Servicio de extracción y transcripción de comprobantes de pago.
    Garantiza la eliminación física inmediata de imágenes temporales
    para evitar saturación de disco en entornos cloud (Render).
    """

    @staticmethod
    def _parse_numeric_amount(val_str: str) -> float:
        """Parsea cadenas numéricas con soporte para formato venezolano (1.250,50) y estándar (1250.50)"""
        s = val_str.strip()
        if '.' in s and ',' in s:
            # Caso 1.250,50 -> punto miles, coma decimal
            s = s.replace('.', '').replace(',', '.')
            return float(s)
        elif ',' in s:
            # Caso 1250,50 -> coma decimal
            return float(s.replace(',', '.'))
        elif '.' in s:
            parts = s.split('.')
            if len(parts) == 2 and len(parts[1]) == 3 and int(parts[1]) % 10 == 0:
                # Probable separador de miles ej: 1.000
                return float(s.replace('.', ''))
            elif len(parts) == 2 and len(parts[1]) <= 2:
                # Decimal estándar ej: 950.00 o 15.5
                return float(s)
            else:
                return float(s.replace('.', ''))
        return float(s)

    @classmethod
    def parse_text_fields(cls, text: str) -> Dict[str, Any]:
        """
        Extrae Banco, Referencia, Monto, Moneda y Fecha a partir del texto extraído.
        """
        clean_text = text.upper()
        
        # 1. Detectar Banco
        detected_bank = "DESCONOCIDO"
        for bank_name, aliases in VENEZUELAN_BANKS:
            if any(alias in clean_text for alias in aliases):
                detected_bank = bank_name
                break
                
        # 2. Detectar Referencia (generalmente de 4 a 14 dígitos)
        ref = None
        ref_patterns = [
            r'(?:REFERENCIA|REF\.?|OPERACI[OÓ]N|NRO\.?\s*DOC|APROBACI[OÓ]N|CONTROL)\s*[:#\-]?\s*([0-9]{4,14})',
            r'(?:COMPROBANTE|SECUENCIA)\s*[:#\-]?\s*([0-9]{4,14})',
            r'\b([0-9]{6,12})\b'
        ]
        for pattern in ref_patterns:
            match = re.search(pattern, clean_text)
            if match:
                ref = match.group(1).strip()
                break

        # 3. Detectar Monto y Moneda
        amount = 0.0
        currency = "VES"
        
        ves_patterns = [
            r'(?:BS\.?|VES|BOL[IÍ]VARES)\s*[:#\-]?\s*([0-9]{1,3}(?:\.[0-9]{3})*(?:,[0-9]{2})|[0-9]+(?:[.,][0-9]{2})?)',
            r'(?:MONTO|IMPORTE|TOTAL)\s*[:#\-]?\s*(?:BS\.?|VES)?\s*([0-9]{1,3}(?:\.[0-9]{3})*(?:,[0-9]{2})|[0-9]+(?:[.,][0-9]{2})?)',
            r'([0-9]{1,3}(?:\.[0-9]{3})*(?:,[0-9]{2})|[0-9]+(?:[.,][0-9]{2})?)\s*(?:BS\.?|VES|BOL[IÍ]VARES)'
        ]
        for pattern in ves_patterns:
            m = re.search(pattern, clean_text)
            if m:
                try:
                    amount = cls._parse_numeric_amount(m.group(1))
                    currency = "VES"
                    break
                except ValueError:
                    pass

        # Si no hubo Bs, buscar si indica USD / $ / Divisas
        if amount == 0.0:
            usd_patterns = [
                r'(?:\$|USD|D[OÓ]LARES)\s*[:#\-]?\s*([0-9]{1,6}(?:[.,][0-9]{2})?)',
                r'([0-9]{1,6}(?:[.,][0-9]{2})?)\s*(?:\$|USD|D[OÓ]LARES)'
            ]
            for pattern in usd_patterns:
                m = re.search(pattern, clean_text)
                if m:
                    try:
                        amount = cls._parse_numeric_amount(m.group(1))
                        currency = "USD"
                        break
                    except ValueError:
                        pass

        # 4. Detectar Fecha del Comprobante
        # Formatos: 05/10/2026, 05-10-2026, 2026-10-05, o "05 DE OCTUBRE DE 2026"
        payment_date = datetime.now().strftime("%Y-%m-%d") # Default hoy
        
        date_pattern = r'(\b[0-3]?[0-9][/\-][0-1]?[0-9][/\-](?:20)?[2-3][0-9]\b)'
        date_match = re.search(date_pattern, clean_text)
        if date_match:
            d_str = date_match.group(1).replace('-', '/')
            parts = d_str.split('/')
            if len(parts) == 3:
                day, month, year = parts
                if len(year) == 2:
                    year = f"20{year}"
                try:
                    dt = datetime(int(year), int(month), int(day))
                    payment_date = dt.strftime("%Y-%m-%d")
                except ValueError:
                    pass

        return {
            "bank": detected_bank,
            "reference": ref or "S/REF",
            "amount": amount,
            "currency": currency,
            "payment_date": payment_date,
            "raw_text": text.strip()
        }

    @classmethod
    def extract_text_from_image(cls, image_path: str) -> str:
        """
        Intenta extraer texto usando:
        1. Google Gemini Vision (si hay API key)
        2. Tesseract OCR (si está instalado en el sistema)
        """
        from app.config import GEMINI_API_KEY
        
        # 1. Probar Gemini Vision si hay API Key configurada
        if GEMINI_API_KEY:
            try:
                import google.generativeai as genai
                genai.configure(api_key=GEMINI_API_KEY)
                model = genai.GenerativeModel("gemini-1.5-flash")
                img = Image.open(image_path)
                prompt = (
                    "Transcribe TODO el texto de este comprobante bancario o recibo de pago venezolano. "
                    "Asegúrate de incluir Banco, Número de Referencia o Aprobación, Monto y Fecha."
                )
                res = model.generate_content([prompt, img])
                if res and res.text:
                    logger.info("OCR exitoso mediante Gemini Vision")
                    return res.text
            except Exception as e:
                logger.warning(f"Error procesando imagen con Gemini: {e}")

        # 2. Probar Pytesseract si tesseract está instalado
        try:
            import pytesseract
            img = Image.open(image_path)
            extracted = pytesseract.image_to_string(img, lang="spa+eng")
            if extracted.strip():
                logger.info("OCR exitoso mediante Tesseract local")
                return extracted
        except Exception as e:
            logger.debug(f"Pytesseract no disponible o error: {e}")

        # Si no hay OCR disponible, retornar indicación para fallback manual
        return "COMPROBANTE BANCARIO CARGADO SATISFACTORIAMENTE"

    @classmethod
    def process_and_destroy_receipt(cls, image_path: str, simulated_hint_text: str = "") -> Dict[str, Any]:
        """
        Procesa la imagen temporal, transcribe sus datos, y DESTRUYE el archivo
        inmediatamente después para evitar acumulación en disco.
        """
        temp_path = Path(image_path)
        extracted_text = ""
        try:
            if temp_path.exists():
                extracted_text = cls.extract_text_from_image(str(temp_path))
            if not extracted_text or extracted_text == "COMPROBANTE BANCARIO CARGADO SATISFACTORIAMENTE":
                if simulated_hint_text:
                    extracted_text = simulated_hint_text
            
            parsed = cls.parse_text_fields(extracted_text)
            return parsed
        finally:
            # DESTRUCCIÓN INMEDIATA DEL ARCHIVO TEMPORAL
            if temp_path.exists():
                try:
                    os.unlink(str(temp_path))
                    logger.info(f"Archivo temporal destruido exitosamente de disco: {temp_path.name}")
                except Exception as e:
                    logger.error(f"Error destruyendo archivo temporal {temp_path}: {e}")
