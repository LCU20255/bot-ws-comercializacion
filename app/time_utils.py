"""
Módulo de Utilidades de Tiempo y Zona Horaria para Venezuela (UTC-4)
Garantiza que todas las fechas y horas registradas en BD, tickets y logs
correspondan con la hora legal venezolana exacta sin adelantos de +4 horas (UTC).
"""
from datetime import datetime, timezone, timedelta

# Zona Horaria Oficial de Venezuela (UTC-4)
VET_TZ = timezone(timedelta(hours=-4))

def now_vet() -> datetime:
    """Retorna objeto datetime con la hora exacta de Venezuela (UTC-4)"""
    return datetime.now(timezone.utc).astimezone(VET_TZ)

def now_vet_str(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Retorna timestamp formateado en hora de Venezuela"""
    return now_vet().strftime(fmt)

def now_vet_date_str() -> str:
    """Retorna la fecha actual en formato YYYY-MM-DD en hora de Venezuela"""
    return now_vet().strftime("%Y-%m-%d")

def now_vet_time_str() -> str:
    """Retorna la hora actual en formato HH:MM AM/PM en hora de Venezuela"""
    return now_vet().strftime("%I:%M %p")

def format_date_dmy(date_val: str) -> str:
    """Convierte fechas (YYYY-MM-DD o ISO) a formato día/mes/año (DD/MM/YYYY)"""
    if not date_val:
        return ""
    s = str(date_val).strip()
    if not s:
        return ""
    # Si ya viene en formato DD/MM/YYYY
    if len(s) == 10 and s[2] == "/" and s[5] == "/":
        return s
    # Si viene como YYYY-MM-DD
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        parts = s[:10].split("-")
        return f"{parts[2]}/{parts[1]}/{parts[0]}"
    return s

def format_datetime_dmy(dt_val: str) -> str:
    """Convierte timestamps 'YYYY-MM-DD HH:MM:SS' a 'DD/MM/YYYY HH:MM:SS'"""
    if not dt_val:
        return ""
    s = str(dt_val).strip()
    if not s:
        return ""
    if " " in s:
        date_part, time_part = s.split(" ", 1)
        return f"{format_date_dmy(date_part)} {time_part}"
    return format_date_dmy(s)
