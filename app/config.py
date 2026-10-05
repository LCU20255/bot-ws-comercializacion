import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# Database
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'commercial_bot.db'}")

# Supabase (Optional)
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# Gemini AI (Optional, smart fallback active if not provided)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Commercial Advisor Contact
ADVISOR_PHONE = os.getenv("ADVISOR_PHONE", "+584121234567")
ADVISOR_NAME = os.getenv("ADVISOR_NAME", "Asesor Comercial")

# Server
PORT = int(os.getenv("PORT", 8000))
HOST = os.getenv("HOST", "0.0.0.0")

# Security / Session
SECRET_KEY = os.getenv("SECRET_KEY", "super-secret-commercial-bot-key-2026")

# Meta WhatsApp Cloud API (Optional for official direct connection)
META_WA_TOKEN = os.getenv("META_WA_TOKEN", "")
META_WA_PHONE_NUMBER_ID = os.getenv("META_WA_PHONE_NUMBER_ID", "")
META_WA_VERIFY_TOKEN = os.getenv("META_WA_VERIFY_TOKEN", "commercial_bot_verify_token")
