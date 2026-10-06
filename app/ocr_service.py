import os
import re
import logging
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from PIL import Image, ImageOps

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
}

# Palabras clave que identifican la línea del número de referencia (por prioridad)
REF_KEYWORDS = [
    "NUMERO DE REFERENCIA", "NRO DE REFERENCIA", "NRO. DE REFERENCIA", "N DE REFERENCIA",
    "NUMERO DE OPERACION", "NRO DE OPERACION", "NRO. DE OPERACION", "N° DE OPERACION",
    "N° OPERACION", "NRO OPERACION", "NO. OPERACION", "REFERENCIA", "OPERACION",
    "NRO. REF", "NRO REF", "REF.", "REF", "COMPROBANTE", "APROBACION", "SECUENCIA",
    "TRANSACCION", "CODIGO", "NUMERO DE CONTROL", "NRO CONTROL", "CONFIRMACION"
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
        # 06 de octubre de 2026 / 06 OCT 2026 / 06-OCT-2026
        m = re.search(r'\b([0-3]?\d)\s*(?:DE\s+|[\-/\s])\s*([A-Z]{3,10})\.?\s*(?:DE\s+|[\-/\s])\s*((?:20)?\d{2})\b', text)
        if m:
            d, mon, y = m.groups()
            month = MONTHS_ES.get(mon) or MONTHS_ES.get(mon[:3])
            if month:
                if len(y) == 2:
                    y = "20" + y
                try:
                    return datetime(int(y), month, int(d)).strftime("%Y-%m-%d")
                except ValueError:
                    pass
        return None

    # ------------------------------------------------------------------
    # PARSEO DE REFERENCIA
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_reference(lines: List[str]) -> Optional[str]:
        # 1) Línea con palabra clave de referencia; valor en la misma línea o la siguiente
        for kw in REF_KEYWORDS:
            for i, line in enumerate(lines):
                if re.search(r'(?<![A-Z])' + re.escape(kw) + r'(?![A-Z])', line):
                    after = line.split(kw, 1)[1] if kw in line else line
                    m = re.search(r'([0-9][0-9\s\-]{3,24}[0-9])', after)
                    if m:
                        digits = re.sub(r'\D', '', m.group(1))
                        if 4 <= len(digits) <= 20:
                            return digits
                    if i + 1 < len(lines):
                        nxt = lines[i + 1]
                        if not any(nk in nxt for nk in NON_REF_KEYWORDS):
                            m = re.search(r'^\s*[#:\-]?\s*([0-9][0-9\s\-]{3,24}[0-9])\s*$', nxt)
                            if m:
                                digits = re.sub(r'\D', '', m.group(1))
                                if 4 <= len(digits) <= 20:
                                    return digits
        # 2) Respaldo: número más largo (8-20 dígitos) en líneas que no sean cédula/teléfono/cuenta/fecha
        candidates = []
        for line in lines:
            if any(nk in line for nk in NON_REF_KEYWORDS):
                continue
            for m in re.finditer(r'(?<![\d*])(\d{8,20})(?![\d*])', line):
                num = m.group(1)
                if re.match(r'^0?4(12|14|16|22|24|26)\d{7}$', num):   # teléfono
                    continue
                candidates.append(num)
        if candidates:
            return max(candidates, key=len)
        return None

    # ------------------------------------------------------------------
    # PARSEO DE MONTO
    # ------------------------------------------------------------------
    @classmethod
    def _extract_amount(cls, lines: List[str], full: str) -> Tuple[float, str]:
        def _try(val: str) -> float:
            try:
                return cls._parse_numeric_amount(val)
            except ValueError:
                return 0.0

        # 1) Monto seguido de BS / VES  (ej: 19.544,40 Bs)
        for line in lines:
            if any(k in line for k in ["IDENTIFICACION", "CEDULA", "ORIGEN", "DESTINO", "TELEFONO"]):
                continue
            m = re.search(AMOUNT_RE + r'\s*(?:BS\.?S?|VES|BOLIVARES)\b', line)
            if m and _try(m.group(1)) > 0:
                return _try(m.group(1)), "VES"
        # 2) BS / VES seguido del monto  (ej: Bs. 19.544,40)
        for line in lines:
            m = re.search(r'\b(?:BS\.?S?|VES)\s*[:.]?\s*' + AMOUNT_RE, line)
            if m and _try(m.group(1)) > 0:
                return _try(m.group(1)), "VES"
        # 3) Línea con palabra MONTO / IMPORTE / TOTAL
        for i, line in enumerate(lines):
            if re.search(r'\b(MONTO|IMPORTE|TOTAL|CANTIDAD)\b', line):
                tail = line + " " + (lines[i + 1] if i + 1 < len(lines) else "")
                m = re.search(r'(?:MONTO|IMPORTE|TOTAL|CANTIDAD)[^0-9]*' + AMOUNT_RE, tail)
                if m and _try(m.group(1)) > 0:
                    cur = "USD" if ("$" in tail or "USD" in tail) else "VES"
                    return _try(m.group(1)), cur
        # 4) Dólares
        m = re.search(r'(?:\$|USD|DOLARES)\s*' + AMOUNT_RE, full) or re.search(AMOUNT_RE + r'\s*(?:\$|USD|DOLARES)', full)
        if m and _try(m.group(1)) > 0:
            return _try(m.group(1)), "USD"
        return 0.0, "VES"

    # ------------------------------------------------------------------
    # PARSEO DE BANCO
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_bank(lines: List[str], full: str) -> str:
        # Prioridad: línea "BANCO: 0102 - BANCO DE VENEZUELA" o "BANCO EMISOR"
        for line in lines:
            if re.match(r'^\s*BANCO(\s+EMISOR|\s+ORIGEN)?\s*[:\-]', line) or line.startswith("BANCO "):
                for name, aliases, code in VENEZUELAN_BANKS:
                    if code in line or any(a in line for a in aliases):
                        return name
        for name, aliases, code in VENEZUELAN_BANKS:
            if any(re.search(r'(?<![A-Z])' + re.escape(a) + r'(?![A-Z])', full) for a in aliases):
                return name
        for name, aliases, code in VENEZUELAN_BANKS:
            if re.search(r'\b' + code + r'\s*[-–]', full):
                return name
        return "DESCONOCIDO"

    @staticmethod
    def _extract_field(lines: List[str], keys: List[str]) -> Optional[str]:
        for line in lines:
            for k in keys:
                if line.startswith(k):
                    val = re.sub(r'^' + re.escape(k) + r'\s*[:\-]?\s*', '', line).strip()
                    if val:
                        return val
        return None

    # ------------------------------------------------------------------
    # PARSEO PRINCIPAL
    # ------------------------------------------------------------------
    @classmethod
    def parse_text_fields(cls, text: str) -> Dict[str, Any]:
        """Extrae Banco, Referencia, Monto, Moneda y Fecha del texto del comprobante."""
        norm = _strip_accents(text or "").upper()
        lines = [re.sub(r'\s+', ' ', l).strip() for l in norm.splitlines() if l.strip()]
        full = " \n".join(lines)

        payment_date = cls._extract_date(full)
        reference = cls._extract_reference(lines)
        amount, currency = cls._extract_amount(lines, full)
        bank = cls._extract_bank(lines, full)
        payer_id = cls._extract_field(lines, ["IDENTIFICACION", "CEDULA", "C.I."])
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
            "raw_text": (text or "").strip(),
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
        # Ampliar imágenes pequeñas para mejorar lectura
        if max(img.size) < 1000:
            factor = 1000 / max(img.size)
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
