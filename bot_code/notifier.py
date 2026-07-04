"""
notifier.py — Gửi thông báo qua đúng bot
"""
import os
import logging
import requests

log = logging.getLogger("Notifier")

_REPORT_TOKEN = os.getenv("TELEGRAM_REPORT_TOKEN", "")
_ADMIN_CHAT   = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")
_TG_BASE      = "https://api.telegram.org"

_REG_TOKEN    = os.getenv("TELEGRAM_REGISTER_TOKEN", "")


def _send(token: str, chat_id, text: str, parse_mode: str = "HTML") -> bool:
    if not token or not chat_id:
        log.warning("⚠️  Thiếu token/chat_id, bỏ qua thông báo.")
        return False
    try:
        r = requests.post(
            f"{_TG_BASE}/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": str(text)[:4096],
                  "parse_mode": parse_mode, "disable_web_page_preview": True},
            timeout=8,
        )
        return r.ok
    except Exception as e:
        log.error("Telegram send error: %s", e)
        return False


# ══════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════

def send_telegram_alert(message: str) -> bool:
    return _send(_REPORT_TOKEN, _ADMIN_CHAT, message)


def notify_tp_hit(symbol: str, direction: str, pnl_pct: float,
                  pnl_usd: float, confidence: float, user_id: str = None):
    msg = (
        f"🎯 <b>TP HIT — {symbol}</b>\n"
        f"📈 {direction} | PnL: <b>+${pnl_usd:.2f} (+{pnl_pct:.2f}%)</b>\n"
        f"🎯 Signal Confidence: {confidence:.1f}%\n"
        f"👤 User: <code>{user_id or 'system'}</code>"
    )
    _send(_REPORT_TOKEN, _ADMIN_CHAT, msg)
    if user_id:
        _send(_REG_TOKEN, user_id,
              f"🎯 <b>CHỐT LỜI {symbol}!</b>\n"
              f"📈 {direction} → <b>+${pnl_usd:.2f} (+{pnl_pct:.2f}%)</b> ✅")


def notify_sl_hit(symbol: str, direction: str, pnl_pct: float,
                  pnl_usd: float, confidence: float,
                  lesson: str = "", user_id: str = None):
    msg = (
        f"🛑 <b>SL HIT — {symbol}</b>\n"
        f"📉 {direction} | PnL: <b>${pnl_usd:.2f} ({pnl_pct:.2f}%)</b>\n"
        f"🎯 Signal Confidence lúc vào: {confidence:.1f}%\n"
        f"👤 User: <code>{user_id or 'system'}</code>\n"
        + (f"\n💡 <i>Bài học: {lesson[:300]}</i>" if lesson else "")
    )
    _send(_REPORT_TOKEN, _ADMIN_CHAT, msg)
    if user_id:
        _send(_REG_TOKEN, user_id,
              f"🛑 <b>Cắt lỗ {symbol}</b>\n"
              f"📉 {direction} → <b>${pnl_usd:.2f} ({pnl_pct:.2f}%)</b>\n"
              + (f"💡 {lesson[:200]}" if lesson else "Bot đang học từ lệnh này."))


def notify_system_error(component: str, error: str):
    _send(_REPORT_TOKEN, _ADMIN_CHAT,
          f"⚠️ <b>LỖI HỆ THỐNG — {component}</b>\n<code>{str(error)[:500]}</code>")


def notify_user_registered(telegram_id: str, tier: str, capital: float):
    _send(_REPORT_TOKEN, _ADMIN_CHAT,
          f"🆕 <b>User mới!</b>\n"
          f"👤 <code>{telegram_id}</code>\n"
          f"💰 ${capital:.2f} | Tier: {tier}")


def notify_user_tier_change(telegram_id: str, old_tier: str,
                             new_tier: str, capital: float):
    arrow = "⬆️" if new_tier > old_tier else "⬇️"
    _send(_REPORT_TOKEN, _ADMIN_CHAT,
          f"{arrow} <b>Tier thay đổi</b>\n"
          f"👤 <code>{telegram_id}</code>\n"
          f"💰 ${capital:.2f}\n"
          f"📊 {old_tier} → {new_tier}")
