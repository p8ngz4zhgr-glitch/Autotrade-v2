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

        self.RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "") or os.getenv("APP_URL", "")
        self.ADMIN_SECRET        = os.getenv("ADMIN_SECRET", "admin123")

        # ── Cấu hình Scalping M5 & Quản trị rủi ro ($20 - $50 / ngày) ──
        self.M5_SCALP_MODE            = os.getenv("M5_SCALP_MODE", "true").lower() == "true"
        self.DAILY_PROFIT_TARGET_USD  = float(os.getenv("DAILY_PROFIT_TARGET_USD", "30.0"))
        self.DAILY_MAX_LOSS_USD       = float(os.getenv("DAILY_MAX_LOSS_USD", "15.0"))
        self.MAX_RISK_PER_TRADE_PCT   = float(os.getenv("MAX_RISK_PER_TRADE_PCT", "1.0"))
        self.MAX_SAFE_LEVERAGE        = int(os.getenv("MAX_SAFE_LEVERAGE", "15"))
        self.CONSECUTIVE_LOSS_LIMIT   = int(os.getenv("CONSECUTIVE_LOSS_LIMIT", "3"))
        self.AUTO_BREAKEVEN_ON_TP1    = os.getenv("AUTO_BREAKEVEN_ON_TP1", "true").lower() == "true"

        # ── Tích hợp Chiến lược Các Huyền Thoại (Famous Traders Framework) ──
        self.ENABLE_ELDER_TRIPLE_SCREEN = os.getenv("ENABLE_ELDER_TRIPLE_SCREEN", "true").lower() == "true"
        self.ENABLE_ICT_SWEEP_FILTER    = os.getenv("ENABLE_ICT_SWEEP_FILTER", "true").lower() == "true"
        self.ENABLE_MINERVINI_VCP       = os.getenv("ENABLE_MINERVINI_VCP", "true").lower() == "true"
        self.ENABLE_WILLIAMS_SCALING    = os.getenv("ENABLE_WILLIAMS_SCALING", "true").lower() == "true"

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
