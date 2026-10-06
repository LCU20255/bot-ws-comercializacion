import logging
import sqlite3
from datetime import datetime, date
from typing import Optional, Dict
import httpx
from app.config import DATABASE_PATH

logger = logging.getLogger(__name__)

# Tasa de respaldo por defecto en caso de falla de conexión externa
FALLBACK_BCV_RATE = 54.20

class BCVService:
    def __init__(self, db_path: str = str(DATABASE_PATH)):
        self.db_path = db_path

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_table(self):
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS bcv_rates (
                    rate_date TEXT PRIMARY KEY,
                    rate REAL NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.commit()

    def get_rate_for_date(self, target_date: Optional[str] = None) -> float:
        """
        Obtiene la tasa oficial del BCV para una fecha específica (formato YYYY-MM-DD).
        Si no se especifica fecha, consulta o actualiza la tasa del día de hoy.
        Si la fecha es anterior, consulta el histórico en la base de datos.
        """
        self.init_table()
        today_str = date.today().strftime("%Y-%m-%d")
        query_date = target_date.strip() if target_date else today_str

        # 1. Buscar en histórico de la base de datos
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT rate FROM bcv_rates WHERE rate_date = ?", (query_date,))
            row = cur.fetchone()
            if row:
                return float(row["rate"])

        # 2. Si es la fecha de hoy y no está registrada, consultar en vivo
        if query_date == today_str:
            live_rate = self.fetch_live_rate()
            if live_rate:
                self.save_rate(query_date, live_rate)
                return live_rate

        # 3. Si es una fecha pasada no registrada, buscar la tasa más cercana anterior
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT rate FROM bcv_rates 
                WHERE rate_date <= ? 
                ORDER BY rate_date DESC LIMIT 1
            """, (query_date,))
            row = cur.fetchone()
            if row:
                return float(row["rate"])

            # Si no hay ninguna anterior, buscar la más reciente registrada
            cur.execute("SELECT rate FROM bcv_rates ORDER BY rate_date DESC LIMIT 1")
            row = cur.fetchone()
            if row:
                return float(row["rate"])

        # 4. Fallback final
        self.save_rate(query_date, FALLBACK_BCV_RATE)
        return FALLBACK_BCV_RATE

    def fetch_live_rate(self) -> Optional[float]:
        """
        Consulta la tasa oficial del BCV a través de APIs públicas o fuentes verificadas.
        """
        urls = [
            "https://pydolarvenezuela-api.vercel.app/api/v1/dollar?page=bcv",
            "https://ve.dolarapi.com/v1/dolares/oficial"
        ]

        for url in urls:
            try:
                with httpx.Client(timeout=4.0) as client:
                    resp = client.get(url)
                    if resp.status_code == 200:
                        data = resp.json()
                        # Formato pydolarvenezuela
                        if "monitors" in data and "usd" in data["monitors"]:
                            rate = float(data["monitors"]["usd"].get("price", 0))
                            if rate > 0:
                                logger.info(f"[BCV Service] Tasa en vivo obtenida de {url}: {rate} Bs/$")
                                return round(rate, 4)
                        # Formato dolarapi
                        elif "promedio" in data:
                            rate = float(data.get("promedio", 0))
                            if rate > 0:
                                logger.info(f"[BCV Service] Tasa en vivo obtenida de {url}: {rate} Bs/$")
                                return round(rate, 4)
            except Exception as e:
                logger.debug(f"[BCV Service] Error consultando {url}: {e}")

        logger.warning("[BCV Service] No se pudo obtener tasa en vivo de APIs, usando fallback.")
        return None

    def save_rate(self, rate_date: str, rate: float):
        self.init_table()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO bcv_rates (rate_date, rate, updated_at)
                VALUES (?, ?, ?)
            """, (rate_date, rate, now_str))
            conn.commit()

bcv_service = BCVService()
