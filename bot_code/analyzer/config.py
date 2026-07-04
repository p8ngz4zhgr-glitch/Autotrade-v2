# ═══════════════════════════════════════════════════════════
# CONFIGURATION — SignalBot v6.1
# ═══════════════════════════════════════════════════════════
import os
import logging
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("Config")


class Config:
    def __init__(self):
        self.TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
        self.TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

        self.TELEGRAM_REGISTER_TOKEN = os.getenv("TELEGRAM_REGISTER_TOKEN", "")

        self.TELEGRAM_REPORT_TOKEN  = os.getenv("TELEGRAM_REPORT_TOKEN", "")
        self.TELEGRAM_ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")

        self.TELEGRAM_ALERT_TOKEN = self.TELEGRAM_REPORT_TOKEN
        self.ALERT_CHAT_ID        = self.TELEGRAM_ADMIN_CHAT_ID

        self.GROQ_API_KEY    = os.getenv("GROQ_API_KEY", "")
        self.GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY", "")
        self.MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
        self.NVIDIA_API_KEY  = os.getenv("NVIDIA_API_KEY", "")

        self.RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "")
        self.ADMIN_SECRET        = os.getenv("ADMIN_SECRET", "admin123")

        self._validate()

    def _validate(self):
        warnings = []
        required = {
            "TELEGRAM_TOKEN":          self.TELEGRAM_TOKEN,
            "TELEGRAM_CHAT_ID":        self.TELEGRAM_CHAT_ID,
            "TELEGRAM_REGISTER_TOKEN": self.TELEGRAM_REGISTER_TOKEN,
            "TELEGRAM_REPORT_TOKEN":   self.TELEGRAM_REPORT_TOKEN,
            "GROQ_API_KEY":            self.GROQ_API_KEY,
        }
        for name, val in required.items():
            if not val:
                warnings.append(name)

        if warnings:
            log.warning("⚠️  Thiếu env vars: %s", ", ".join(warnings))

        if self.ADMIN_SECRET == "admin123":
            log.warning("⚠️  ADMIN_SECRET đang dùng giá trị mặc định!")
