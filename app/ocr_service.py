import os
import re
import logging
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from PIL import Image, ImageOps, ImageEnhance, ImageFilter

from app.time_utils import now_vet_date_str

logger = logging.getLogger(__name__)

# Listado de bancos venezolanos (nombre oficial, alias, código bancario)
VENEZUELAN_BANKS = [
    ("BANCO DE VENEZUELA", ["BANCO DE VENEZUELA", "PAGOMOVILBDV", "BDVENLINEA", "BDV", "B.D.V", "VENEZUELA"], "0102"),
    ("BANESCO", ["BANESCO", "BANESCO ON LINE", "BANESCONLINE"], "0134"),
    ("MERCANTIL", ["MERCANTIL", "TPAGO", "T-PAGO", "BANCO MERCANTIL"], "0105"),
    ("BBVA PROVINCIAL", ["PROVINCIAL", "BBVA", "DINERO RAPIDO", "PROVINCIAL BBVA"], "0108"),
    ("BANCAMIGA", ["BANCAMIGA", "PAGO MOVIL BANCAMIGA", "BANCO BANCAMIGA"], "0172"),
    ("BNC", ["BANCO NACIONAL DE CREDITO", "BNC", "NACIONAL DE CREDITO"], "0191"),
    ("BANCO DEL TESORO", ["BANCO DEL TESORO", "TESORO"], "0163"),
    ("BANCO BICENTENARIO", ["BICENTENARIO", "BANCO DIGITAL DE LOS TRABAJADORES"], "0175"),
    ("BANCARIBE", ["BANCARIBE", "MIPAGO", "MI PAGO"], "0114"),
    ("BANCO EXTERIOR", ["BANCO EXTERIOR", "EXTERIOR"], "0115"),
    ("BANCO PLAZA", ["BANCO PLAZA", "PLAZA"], "0138"),
    ("BANCO ACTIVO", ["BANCO ACTIVO", "ACTIVO"], "0171"),
    ("BANPLUS", ["BANPLUS"], "0174"),
    ("BANCO CARONI", ["CARONI", "BANCO CARONÍ"], "0128"),
    ("BANCO SOFITASA", ["SOFITASA"], "0137"),
    ("100% BANCO", ["100% BANCO", "100 BANCO"], "0156"),
    ("BANCO VENEZOLANO DE CREDITO", ["VENEZOLANO DE CREDITO", "BVC"], "0104"),
    ("DEL SUR", ["DEL SUR"], "0157"),
    ("BANFANB", ["BANFANB", "BANCO DE LA FUERZA ARMADA"], "0177"),
    ("BANCRECER", ["BANCRECER"], "0168"),
    ("MI BANCO", ["MI BANCO"], "0169"),
    ("BANCO AGRICOLA", ["AGRICOLA", "BANCO AGRÍCOLA"], "0166"),
]

MONTHS_ES = {
    "ENE": 1, "ENERO": 1, "FEB": 2, "FEBRERO": 2, "MAR": 3, "MARZO": 3,
    "ABR": 4, "ABRIL": 4, "MAY": 5, "MAYO": 5, "JUN": 6, "JUNIO": 6,
    "JUL": 7, "JULIO": 7, "AGO": 8, "AGOSTO": 8, "SEP": 9, "SEPT": 9, "SEPTIEMBRE": 9,
    "SETIEMBRE": 9, "OCT": 10, "OCTUBRE": 10, "NOV": 11, "NOVIEMBRE": 11,
    "DIC": 12, "DICIEMBRE": 12,
    "ene": 1, "enero": 1, "feb": 2, "febrero": 2, "mar": 3, "marzo": 3,
    "abr": 4, "abril": 4, "may": 5, "mayo": 5, "jun": 6, "junio": 6,
    "jul": 7, "julio": 7, "ago": 8, "agosto": 8, "sep": 9, "sept": 9, "septiembre": 9,
    "setiembre": 9, "oct": 10, "octubre": 10, "nov": 11, "noviembre": 11,
    "dic": 12, "diciembre": 12,
}

# Palabras clave que identifican la línea del número de referencia (por prioridad)
REF_KEYWORDS = [
    "NUMERO DE REFERENCIA", "NRO DE REFERENCIA", "NRO. DE REFERENCIA", "N DE REFERENCIA",
    "NUMERO DE OPERACION", "NRO DE OPERACION", "NRO. DE OPERACION", "N° DE OPERACION",
    "N° OPERACION", "NRO OPERACION", "NO. OPERACION", "REFERENCIA", "OPERACION",
    "NRO. REF", "NRO REF", "REF.", "REF", "COMPROBANTE", "APROBACION", "SECUENCIA",
    "TRANSACCION", "CODIGO", "NUMERO DE CONTROL", "NRO CONTROL", "CONFIRMACION",
    "DOC", "DOCUMENTO", "RECIBO", "CONTROL", "APROBACIÓN", "ID", "ID DE PAGO", "SEQ"
]

# Líneas que NO contienen la referencia (cédula, teléfonos, cuentas)
NON_REF_KEYWORDS = [
    "IDENTIFICACION", "IDENTIFICACIÓN", "CEDULA", "CÉDULA", "C.I", "RIF", "ORIGEN", "DESTINO", "TELEFONO",
    "TELÉFONO", "CELULAR", "CUENTA", "BENEFICIARIO", "TITULAR", "FECHA", "HORA", "MONTO", "CONCEPTO",
]

AMOUNT_RE = r'([0-9]{1,3}(?:[.\s][0-9]{3})+(?:,[0-9]{1,2})?|[0-9]+,[0-9]{1,2}|[0-9]+\.[0-9]{1,2}|[0-9]+)'

_ocr_engine = None


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


class ReceiptOCRService:
    """
    Servicio de extracción y transcripción de comprobantes de pago venezolanos.
    - Motor principal: RapidOCR (ONNX, local, sin internet ni API keys).
    - Respaldo: Gemini Vision (si hay API key) o Tesseract (si está instalado).
    - Destruye la imagen temporal inmediatamente después de procesarla.
    """

    # ------------------------------------------------------------------
    # PARSEO DE MONTOS
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_numeric_amount(val_str: str) -> float:
        """Soporta formato venezolano (19.544,40) y estándar (19544.40)."""
        s = val_str.strip().replace(" ", "")
        if "." in s and "," in s:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")   # 19.544,40
            else:
                s = s.replace(",", "")                     # 19,544.40
            return float(s)
        if "," in s:
            parts = s.split(",")
            if len(parts) == 2 and len(parts[1]) <= 2:
                return float(s.replace(",", "."))          # 1250,50
            return float(s.replace(",", ""))               # 1,250
        if "." in s:
            parts = s.split(".")
            if len(parts) > 2:
                if len(parts[-1]) <= 2:
                    return float("".join(parts[:-1]) + "." + parts[-1])
                return float("".join(parts))
            elif len(parts) == 2 and len(parts[1]) == 3:
                return float(s.replace(".", ""))           # 19.544 (miles)
            return float(s)                                # 950.50
        return float(s)

    # ------------------------------------------------------------------
    # PARSEO DE FECHA
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_date(text: str) -> Optional[str]:
        # dd/mm/yyyy, dd-mm-yyyy, dd.mm.yyyy, dd/mm/yy
        m = re.search(r'\b([0-3]?\d)\s*[/\-.]\s*([0-1]?\d)\s*[/\-.]\s*((?:19|20)?\d{2})\b', text)
        if m:
            d, mo, y = m.groups()
            if len(y) == 2:
                y = "20" + y
            try:
                return datetime(int(y), int(mo), int(d)).strftime("%Y-%m-%d")
            except ValueError:
                pass
        # yyyy-mm-dd
        m = re.search(r'\b(20\d{2})\s*[/\-.]\s*([0-1]?\d)\s*[/\-.]\s*([0-3]?\d)\b', text)
        if m:
            y, mo, d = m.groups()
            try:
                return datetime(int(y), int(mo), int(d)).strftime("%Y-%m-%d")
            except ValueError:
                pass
        # 06 de octubre de 2026 / 06 OCT 2026 / 06-OCT-2026 (insensible a mayúsculas/minúsculas)
        norm = _strip_accents(text).lower()
        m = re.search(r'\b([0-3]?\d)\s*(?:de\s+|[\-/\s])\s*([a-zA-Z]{3,12})\.?\s*(?:de\s+|[\-/\s])\s*((?:20)?\d{2})\b', norm, re.IGNORECASE)
        if m:
            d, mon, y = m.groups()
            mon_lower = mon.lower()
            month = MONTHS_ES.get(mon_lower) or MONTHS_ES.get(mon_lower[:3])
            if month:
                if len(y) == 2:
                    y = "20" + y
                try:
                    return datetime(int(y), month, int(d)).strftime("%Y-%m-%d")
                except ValueError:
                    pass
        return None

    # ------------------------------------------------------------------
    # PARSEO DE REFERENCIA (Insensible a mayúsculas/minúsculas)
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_reference(lines: List[str]) -> Optional[str]:
        clean_lines = [_strip_accents(l) for l in lines]

        # 1) Búsqueda por palabras clave explícitas
        for kw in REF_KEYWORDS:
            kw_clean = _strip_accents(kw).lower()
            pattern = r'(?<![a-zA-Z0-9])' + re.escape(kw_clean) + r'(?![a-zA-Z0-9])'
            for i, raw_line in enumerate(clean_lines):
                line_low = raw_line.lower()
                if re.search(pattern, line_low, re.IGNORECASE):
                    # Extraer dígitos posteriores en la misma línea
                    after_parts = re.split(pattern, line_low, maxsplit=1, flags=re.IGNORECASE)
                    after = after_parts[1] if len(after_parts) > 1 else line_low
                    m = re.search(r'([0-9][0-9\s\-]{3,24}[0-9])', after)
                    if m:
                        digits = re.sub(r'\D', '', m.group(1))
                        if 4 <= len(digits) <= 20:
                            return digits
                    # O verificar la siguiente línea si no es clave excluida
                    if i + 1 < len(clean_lines):
                        nxt = clean_lines[i + 1].lower()
                        if not any(_strip_accents(nk).lower() in nxt for nk in NON_REF_KEYWORDS):
                            m_nxt = re.search(r'^\s*[#:\-]?\s*([0-9][0-9\s\-]{3,24}[0-9])\s*$', nxt)
                            if m_nxt:
                                digits = re.sub(r'\D', '', m_nxt.group(1))
                                if 4 <= len(digits) <= 20:
                                    return digits

        # 2) Buscar patrones directos tipo "Ref: 123456" o "#123456" o "Operación: 123456"
        for raw_line in clean_lines:
            m = re.search(r'(?:ref|nro|num|op|sec|doc|recibo|control)[.:#\s]+([0-9]{4,16})', raw_line, re.IGNORECASE)
            if m:
                return m.group(1)

        # 3) Respaldo: número de 5 a 20 dígitos en líneas limpias (excluyendo teléfonos y cédulas)
        candidates = []
        for raw_line in clean_lines:
            line_low = raw_line.lower()
            if any(_strip_accents(nk).lower() in line_low for nk in NON_REF_KEYWORDS):
                continue
            for m in re.finditer(r'(?<![\d*])(\d{5,20})(?![\d*])', raw_line):
                num = m.group(1)
                if re.match(r'^0?4(12|14|16|22|24|26)\d{7}$', num):   # teléfono
                    continue
                candidates.append(num)
        if candidates:
            ideal = [c for c in candidates if 6 <= len(c) <= 12]
            if ideal:
                return ideal[0]
            return max(candidates, key=len)
        return None

    # ------------------------------------------------------------------
    # PARSEO DE MONTO (Insensible a mayúsculas/minúsculas)
    # ------------------------------------------------------------------
    @classmethod
    def _extract_amount(cls, lines: List[str], full: str) -> Tuple[float, str]:
        def _try(val: str) -> float:
            try:
                return cls._parse_numeric_amount(val)
            except ValueError:
                return 0.0

        clean_lines = [_strip_accents(l) for l in lines]
        full_clean = _strip_accents(full)

        # 1) Monto seguido o precedido de BS / VES (ej: 19.544,40 Bs o Bs. 1.250,00)
        for raw_line in clean_lines:
            line_low = raw_line.lower()
            if any(k in line_low for k in ["identificacion", "cedula", "origen", "destino", "telefono", "cuenta"]):
                continue
            m = re.search(AMOUNT_RE + r'\s*(?:bs\.?s?|bs\.?d?|ves|bolivares)\b', line_low, re.IGNORECASE)
            if m and _try(m.group(1)) > 0:
                return _try(m.group(1)), "VES"
            m = re.search(r'\b(?:bs\.?s?|bs\.?d?|ves)\s*[:.]?\s*' + AMOUNT_RE, line_low, re.IGNORECASE)
            if m and _try(m.group(1)) > 0:
                return _try(m.group(1)), "VES"

        # 2) Línea con palabra MONTO / IMPORTE / TOTAL / DEBITADO / TRANSFERIDO
        for i, raw_line in enumerate(clean_lines):
            line_low = raw_line.lower()
            if re.search(r'\b(monto|importe|total|cantidad|debitado|transferido|pagado)\b', line_low, re.IGNORECASE):
                tail = raw_line + " " + (clean_lines[i + 1] if i + 1 < len(clean_lines) else "")
                tail_low = tail.lower()
                m = re.search(r'(?:monto|importe|total|cantidad|debitado|transferido|pagado)[^0-9]*' + AMOUNT_RE, tail_low, re.IGNORECASE)
                if m and _try(m.group(1)) > 0:
                    cur = "USD" if ("$" in tail or "usd" in tail_low or "dolar" in tail_low) else "VES"
                    return _try(m.group(1)), cur

        # 3) Dólares
        m = re.search(r'(?:\$|usd|dolares)\s*' + AMOUNT_RE, full_clean, re.IGNORECASE) or re.search(AMOUNT_RE + r'\s*(?:\$|usd|dolares)', full_clean, re.IGNORECASE)
        if m and _try(m.group(1)) > 0:
            return _try(m.group(1)), "USD"

        return 0.0, "VES"

    # ------------------------------------------------------------------
    # PARSEO DE BANCO (Insensible a mayúsculas/minúsculas - Prioridad BANCO EMISOR)
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_bank(lines: List[str], full: str) -> str:
        clean_lines = [_strip_accents(l) for l in lines]
        full_clean = _strip_accents(full).lower()

        # Palabras indicadoras de ORIGEN / EMISOR (banco desde donde pagó el cliente)
        ORIGIN_WORDS = ["emisor", "origen", "debito", "debitado", "desde", "cuenta debito", "banco emisor", "banco origen", "debitar"]
        # Palabras indicadoras de DESTINO / RECEPTOR (banco de la empresa)
        DEST_WORDS = ["destino", "receptor", "beneficiario", "acreditado", "hacia", "cuenta destino", "banco receptor", "banco destino"]

        # Prioridad 1: Línea que mencione explícitamente banco emisor u origen
        for raw_line in clean_lines:
            line_low = raw_line.lower()
            if any(ow in line_low for ow in ORIGIN_WORDS) and not any(dw in line_low for dw in DEST_WORDS):
                for name, aliases, code in VENEZUELAN_BANKS:
                    if code in line_low or any(_strip_accents(a).lower() in line_low for a in aliases):
                        return name

        # Prioridad 2: Buscar en las primeras 4 líneas (logo/encabezado de app bancaria del cliente)
        header_lines = clean_lines[:4]
        for raw_line in header_lines:
            line_low = raw_line.lower()
            if any(dw in line_low for dw in DEST_WORDS):
                continue
            for name, aliases, code in VENEZUELAN_BANKS:
                if any(_strip_accents(a).lower() in line_low for a in aliases):
                    return name

        # Prioridad 3: Buscar en todas las líneas excluyendo aquellas marcadas como destino
        for raw_line in clean_lines:
            line_low = raw_line.lower()
            if any(dw in line_low for dw in DEST_WORDS):
                continue
            for name, aliases, code in VENEZUELAN_BANKS:
                if code in line_low or any(_strip_accents(a).lower() in line_low for a in aliases):
                    return name

        # Prioridad 4: Búsqueda en todo el texto; si hay varios y uno es BDV (común destino), preferir el otro
        detected_banks = []
        for name, aliases, code in VENEZUELAN_BANKS:
            for a in aliases:
                a_norm = _strip_accents(a).lower()
                pattern = r'(?<![a-zA-Z0-9])' + re.escape(a_norm) + r'(?![a-zA-Z0-9])'
                if re.search(pattern, full_clean, re.IGNORECASE):
                    if name not in detected_banks:
                        detected_banks.append(name)
                    break

        if detected_banks:
            if len(detected_banks) > 1 and "BANCO DE VENEZUELA" in detected_banks:
                non_bdv = [b for b in detected_banks if b != "BANCO DE VENEZUELA"]
                if non_bdv:
                    return non_bdv[0]
            return detected_banks[0]

        # Prioridad 5: Si menciona pago móvil o transferencia genérica
        if any(w in full_clean for w in ["pago movil", "pagomovil", "transferencia", "bancario"]):
            return "BANCO NACIONAL (PAGO MÓVIL)"

        return "DESCONOCIDO"

    @staticmethod
    def _extract_field(lines: List[str], keys: List[str]) -> Optional[str]:
        keys_low = [_strip_accents(k).lower() for k in keys]
        for line in lines:
            line_clean = _strip_accents(line).strip()
            line_low = line_clean.lower()
            for k in keys_low:
                if line_low.startswith(k):
                    val = re.sub(r'^' + re.escape(k) + r'\s*[:\-]?\s*', '', line_clean, flags=re.IGNORECASE).strip()
                    if val:
                        return val
        return None

    # ------------------------------------------------------------------
    # PARSEO PRINCIPAL
    # ------------------------------------------------------------------
    @classmethod
    def parse_text_fields(cls, text: str) -> Dict[str, Any]:
        """Extrae Banco, Referencia, Monto, Moneda y Fecha del texto del comprobante con soporte mayúsculas/minúsculas."""
        raw_text_clean = (text or "").strip()
        # Normalizar espacios OCR y saltos de línea
        pre_processed = re.sub(r'([a-zA-Z])([:\-])([a-zA-Z0-9])', r'\1\2 \3', raw_text_clean)
        # Separar palabras unidas comunes por OCR como "Bancoemisor:" o "Bancode"
        pre_processed = re.sub(r'\bBanco([a-z]+):', r'Banco \1:', pre_processed, flags=re.IGNORECASE)
        pre_processed = re.sub(r'\bBanco([a-z]+)\s', r'Banco \1 ', pre_processed, flags=re.IGNORECASE)

        lines = [re.sub(r'\s+', ' ', l).strip() for l in pre_processed.splitlines() if l.strip()]
        full = " \n".join(lines)

        payment_date = cls._extract_date(full)
        reference = cls._extract_reference(lines)
        amount, currency = cls._extract_amount(lines, full)
        bank = cls._extract_bank(lines, full)
        payer_id = cls._extract_field(lines, ["IDENTIFICACION", "CEDULA", "C.I.", "C.I", "RIF"])
        concept = cls._extract_field(lines, ["CONCEPTO", "DESCRIPCION", "MOTIVO"])

        return {
            "bank": bank,
            "reference": reference or "S/REF",
            "amount": round(amount, 2),
            "currency": currency,
            "payment_date": payment_date or now_vet_date_str(),
            "date_detected": bool(payment_date),
            "payer_id": payer_id,
            "concept": concept,
            "raw_text": raw_text_clean,
        }

    # ------------------------------------------------------------------
    # MOTORES OCR
    # ------------------------------------------------------------------
    @staticmethod
    def _get_rapidocr():
        global _ocr_engine
        if _ocr_engine is None:
            try:
                from rapidocr import RapidOCR
            except ImportError:
                from rapidocr_onnxruntime import RapidOCR  # versión anterior
            _ocr_engine = RapidOCR()
        return _ocr_engine

    @staticmethod
    def _group_into_lines(items: List[Tuple[List, str]]) -> str:
        """Agrupa cajas de texto por fila (misma altura) para reconstruir 'Clave  Valor'."""
        boxes = []
        for box, txt in items:
            if not txt or not str(txt).strip():
                continue
            ys = [p[1] for p in box]
            xs = [p[0] for p in box]
            boxes.append({"y": (min(ys) + max(ys)) / 2, "h": max(ys) - min(ys), "x": min(xs), "t": str(txt).strip()})
        boxes.sort(key=lambda b: (b["y"], b["x"]))
        rows: List[List[Dict]] = []
        for b in boxes:
            if rows:
                row = rows[-1]
                ref_y = sum(r["y"] for r in row) / len(row)
                ref_h = max(r["h"] for r in row)
                if abs(b["y"] - ref_y) <= max(ref_h, b["h"]) * 0.55:
                    row.append(b)
                    continue
            rows.append([b])
        return "\n".join(" ".join(r["t"] for r in sorted(row, key=lambda r: r["x"])) for row in rows)

    @classmethod
    def _ocr_rapid(cls, image_path: str) -> str:
        engine = cls._get_rapidocr()
        img = Image.open(image_path)
        img = ImageOps.exif_transpose(img).convert("RGB")
        # Preprocesamiento avanzado: optimizar nitidez, contraste y resolución para comprobantes de WhatsApp
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.35)
        sharpener = ImageEnhance.Sharpness(img)
        img = sharpener.enhance(1.3)
        # Ampliar imágenes pequeñas para mejorar lectura
        if max(img.size) < 1200:
            factor = 1200 / max(img.size)
            img = img.resize((int(img.width * factor), int(img.height * factor)), Image.Resampling.LANCZOS)
        import numpy as np
        result = engine(np.array(img))
        items = []
        if hasattr(result, "txts") and result.txts is not None:        # rapidocr >= 2.x
            items = list(zip(result.boxes.tolist() if hasattr(result.boxes, "tolist") else result.boxes, result.txts))
        elif isinstance(result, tuple) and result and result[0]:         # rapidocr_onnxruntime
            items = [(r[0], r[1]) for r in result[0]]
        return cls._group_into_lines(items)

    @classmethod
    def extract_text_from_image(cls, image_path: str) -> str:
        # 1. RapidOCR local (principal)
        try:
            txt = cls._ocr_rapid(image_path)
            if txt.strip():
                logger.info("OCR exitoso mediante RapidOCR local")
                return txt
        except Exception as e:
            logger.warning(f"RapidOCR no disponible o error: {e}")

        # 2. Gemini Vision (si hay API key)
        try:
            from app.config import GEMINI_API_KEY
            if GEMINI_API_KEY:
                import google.generativeai as genai
                genai.configure(api_key=GEMINI_API_KEY)
                model = genai.GenerativeModel("gemini-1.5-flash")
                res = model.generate_content([
                    "Transcribe línea por línea TODO el texto de este comprobante bancario venezolano, "
                    "respetando el formato 'Etiqueta: Valor'.", Image.open(image_path)])
                if res and res.text:
                    logger.info("OCR exitoso mediante Gemini Vision")
                    return res.text
        except Exception as e:
            logger.warning(f"Error procesando imagen con Gemini: {e}")

        # 3. Tesseract (si está instalado)
        try:
            import pytesseract
            extracted = pytesseract.image_to_string(Image.open(image_path), lang="spa+eng")
            if extracted.strip():
                logger.info("OCR exitoso mediante Tesseract local")
                return extracted
        except Exception as e:
            logger.debug(f"Pytesseract no disponible o error: {e}")

        return ""

    @classmethod
    def process_and_destroy_receipt(cls, image_path: str, simulated_hint_text: str = "") -> Dict[str, Any]:
        """Procesa la imagen temporal, transcribe sus datos y DESTRUYE el archivo inmediatamente."""
        temp_path = Path(image_path)
        extracted_text = ""
        try:
            if temp_path.exists():
                extracted_text = cls.extract_text_from_image(str(temp_path))
            if simulated_hint_text:
                extracted_text = (extracted_text + "\n" + simulated_hint_text).strip()
            parsed = cls.parse_text_fields(extracted_text)
            parsed["ocr_ok"] = bool(extracted_text.strip())
            return parsed
        finally:
            if temp_path.exists():
                try:
                    os.unlink(str(temp_path))
                    logger.info(f"Archivo temporal destruido exitosamente de disco: {temp_path.name}")
                except Exception as e:
                    logger.error(f"Error destruyendo archivo temporal {temp_path}: {e}")
