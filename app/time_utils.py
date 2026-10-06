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
