""" API"""
import os
import uvicorn
import sys
import json
import asyncio
import threading
import time
import logging
import gc
import schedule
import requests as _req
import redis
import redis.asyncio as aioredis
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core_api.models import SessionLocal, User, TradeJournal, SignalStats, AppConfig
from core_api.security import encrypt_api_secret, decrypt_api_secret
from analyzer.main_scanner import SignalBot
from worker.bingx_trader import BingXExchange
from worker.meta_labeling import MetaLabelFilter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("MainAPI")

app = FastAPI(title="SignalBot v6.1")

REGISTER_TOKEN = (
    os.getenv("TELEGRAM_REGISTER_TOKEN", "")
    or os.getenv("TELEGRAM_BOT_TOKEN", "")
    or os.getenv("TELEGRAM_TOKEN", "")
    or os.getenv("TELEGRAM_REPORT_TOKEN", "")
)
REDIS_URL = (os.getenv("REDIS_URL") or "").strip()
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "admin123")
redis_client = None
if REDIS_URL and any(REDIS_URL.startswith(prefix) for prefix in ("redis://", "rediss://", "unix://")):
    try:
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        redis_client.ping()
    except Exception:
        redis_client = None

BOT_GLOBAL_AUTO = True
BOT_KILL_SWITCH = False
_POS_LOCK = threading.Lock()
LIVE_POSITIONS = {}
LAST_SIGNALS = []
_LAST_REVERSAL_EVAL = {}
_SENT_NOTIFICATIONS = {}
_USER_SYM_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_USER_SYM_LOCKS_GUARD = threading.Lock()


def _clean_sym(sym: str) -> str:
    return str(sym or "").replace("-", "").upper().strip()


def _sym_variants(sym: str) -> list[str]:
    raw = str(sym or "").strip().upper()
    if not raw:
        return []
    norm = BingXExchange._normalize_symbol(raw)
    clean = norm.replace("-", "").upper()
    out = []
    for cand in (raw, norm, clean):
        if cand and cand not in out:
            out.append(cand)
    return out


def _to_scanner_sym(sym: str) -> str:
    clean = _clean_sym(sym)
    if "NCCOGOLD2USD" in clean:
        return "NCCOGOLD2USD-USDT"
    if "NCCOXAG2USD" in clean:
        return "NCCOXAG2USD-USDT"
    return clean


def _get_user_sym_lock(user_id: str, sym: str) -> threading.Lock:
    key = (str(user_id), _clean_sym(sym))
    with _USER_SYM_LOCKS_GUARD:
        lock = _USER_SYM_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _USER_SYM_LOCKS[key] = lock
        return lock

# ══════════════════════════════════════════════════════════════════
# TIER CONFIG
# ══════════════════════════════════════════════════════════════════
TIER_CONFIG = {
    "TIER1": {
        "label": "Ca Con", "min_capital": 0, "max_capital": 500,
        "min_confidence": 68.0, "max_risk_pct": 2.0,
        "max_positions": 7, "leverage": 10, "target_monthly": "8-12%",
    },
    "TIER2": {
        "label": "Tieu Chuan", "min_capital": 500, "max_capital": 2000,
        "min_confidence": 70.0, "max_risk_pct": 1.5,
        "max_positions": 7, "leverage": 10, "target_monthly": "6-10%",
    },
    "TIER3": {
        "label": "Ca Map", "min_capital": 2000, "max_capital": float("inf"),
        "min_confidence": 75.0, "max_risk_pct": 1.0,
        "max_positions": 7, "leverage": 10, "target_monthly": "5-8%",
    },
}
MIN_CAPITAL_TO_TRADE = 20.0

def get_tier(capital: float) -> Optional[str]:
    if capital < MIN_CAPITAL_TO_TRADE:
        return None
    for tier, cfg in TIER_CONFIG.items():
        if cfg["min_capital"] <= capital < cfg["max_capital"]:
            return tier
    return "TIER3"

def apply_tier(user: User, tier: str):
    if tier not in TIER_CONFIG:
        return
    cfg = TIER_CONFIG[tier]
    user.tier           = tier
    user.min_confidence = cfg["min_confidence"]
    user.max_risk_pct   = cfg["max_risk_pct"]
    user.max_positions  = cfg["max_positions"]
    user.leverage       = cfg["leverage"]


#══════════════════════════════════════════════════════════════════
# ENV & REDIS
# ══════════════════════════════════════════════════════════════════
REDIS_URL       = (os.getenv("REDIS_URL") or "").strip()
ADMIN_SECRET    = os.getenv("ADMIN_SECRET", "admin123")
RENDER_URL      = os.getenv("RENDER_EXTERNAL_URL", "") or os.getenv("APP_URL", "")
ADMIN_CHAT_ID   = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")
REPORT_TOKEN    = os.getenv("TELEGRAM_REPORT_TOKEN", "") or os.getenv("TELEGRAM_TOKEN", "")
REGISTER_TOKEN  = (
    os.getenv("TELEGRAM_REGISTER_TOKEN", "")
    or os.getenv("TELEGRAM_BOT_TOKEN", "")
    or os.getenv("TELEGRAM_TOKEN", "")
    or os.getenv("TELEGRAM_REPORT_TOKEN", "")
)
TG_BASE         = "https://api.telegram.org"

from core_api.local_store import local_store

redis_client = None
if REDIS_URL and any(REDIS_URL.startswith(p) for p in ("redis://", "rediss://", "unix://")):
    try:
        _rc_kwargs = {"decode_responses": True}
        if REDIS_URL.startswith("rediss://"):
            _rc_kwargs["ssl_cert_reqs"] = "none"
        redis_client = redis.from_url(REDIS_URL, **_rc_kwargs)
        redis_client.ping()
        log.info("🟢 Redis Cloud kết nối OK")
    except Exception as e:
        redis_client = None
        log.warning("⚠️ Lỗi kết nối Redis Cloud: %s -> Tự động chuyển sang LocalStore (0%% Quota/RAM)", e)
else:
    log.info("ℹ️ Không khai báo REDIS_URL hợp lệ -> Sử dụng LocalStore In-Memory (0%% Quota/RAM)")


def _get_active_redis():
    if redis_client:
        try:
            redis_client.ping()
            return redis_client
        except Exception:
            pass
    return local_store


def _redis_get(key, default=None):
    client = _get_active_redis()
    try:
        v = client.get(key)
        if v is None:
            return default
        if isinstance(v, (dict, list)):
            return v
        return json.loads(v) if isinstance(v, str) and (v.startswith("{") or v.startswith("[")) else v
    except Exception:
        return default


def _redis_set(key, value, ex=86400 * 30):
    client = _get_active_redis()
    try:
        if client == redis_client:
            client.set(key, json.dumps(value), ex=ex)
        else:
            client.setex(key, ex, value)
    except Exception as e:
        log.error("Redis set %s: %s", key, e)


def get_bx(user: User) -> BingXExchange:
    secret = decrypt_api_secret(user.api_secret_encrypted) if user.api_secret_encrypted else ""
    return BingXExchange(user.api_key, secret)

def _update_user_balance_and_tier(user: User, balance: float, db: Session):
    try:
        user.capital = balance
        tier = get_tier(balance)
        if tier and user.tier != tier:
            apply_tier(user, tier)
        db.commit()
    except Exception as e:
        log.warning("Update balance error: %s", e)

def _sanitize_telegram_html(text: str) -> str:
    """
    Escape an toàn các ký tự < và > không thuộc thẻ HTML hợp lệ của Telegram
    (ví dụ: 'P(win) < 35%', 'Conf < 70%') để tránh lỗi HTTP 400 'can't parse entities'.
    """
    if not text:
        return ""
    import re
    tag_regex = re.compile(r'(</?(?:b|strong|i|em|u|ins|s|strike|del|span|tg-spoiler|a|code|pre|blockquote)(?:\s+[^<>]*)?>)', re.IGNORECASE)
    parts = tag_regex.split(text)
    sanitized = []
    for i, p in enumerate(parts):
        if i % 2 == 1:
            sanitized.append(p)
        else:
            p = re.sub(r'&(?!(?:amp|lt|gt|quot|apos);)', '&amp;', p)
            p = p.replace('<', '&lt;').replace('>', '&gt;')
            sanitized.append(p)
    return ''.join(sanitized)

def _tg_send(token: str, chat_id: str, text: str):
    effective_token = (
        token
        or REGISTER_TOKEN
        or os.getenv("TELEGRAM_REGISTER_TOKEN", "")
        or os.getenv("TELEGRAM_BOT_TOKEN", "")
        or os.getenv("TELEGRAM_TOKEN", "")
        or os.getenv("TELEGRAM_REPORT_TOKEN", "")
    )
    if not effective_token or not chat_id:
        log.warning("⚠️ _tg_send bỏ qua: Thiếu token (%s) hoặc chat_id (%s)", bool(effective_token), chat_id)
        return False
    try:
        url = f"https://api.telegram.org/bot{effective_token}/sendMessage"
        safe_html = _sanitize_telegram_html(text)
        resp = _req.post(url, json={"chat_id": str(chat_id), "text": safe_html, "parse_mode": "HTML"}, timeout=5)
        if resp.status_code != 200:
            log.warning("⚠️ Telegram _tg_send lỗi HTTP %d: %s. Thử lại dạng plain text...", resp.status_code, resp.text)
            import re
            clean_text = re.sub(r'<[^>]+>', '', text)
            resp2 = _req.post(url, json={"chat_id": str(chat_id), "text": clean_text}, timeout=5)
            if resp2.status_code == 200:
                log.info("📲 [TELEGRAM] Đã gửi fallback plain text thành công tới %s", chat_id)
                return True
            return False
        else:
            summary = text.replace("\n", " ")[:70]
            log.info("📲 [TELEGRAM] Đã gửi thông báo thành công tới %s: %s", chat_id, summary)
            return True
    except Exception as e:
        log.warning("_tg_send exception: %s", e)
        return False

def run_signal_bot():
    try:
        bot = SignalBot()
        bot.start()
    except Exception as e:
        log.error("run_signal_bot: %s", e)

def run_trade_worker():
    pass

def _tp1_monitor():
    global LIVE_POSITIONS
    while True:
        try:
            with _POS_LOCK:
                positions = LIVE_POSITIONS.copy()
            
            db = SessionLocal()
            user_positions_map = {}
            for p in positions:
                if not isinstance(p, dict): continue
                uid = p.get("user_id")
                if uid:
                    if uid not in user_positions_map:
                        user_positions_map[uid] = []
                    user_positions_map[uid].append(p)
                    
            for user_id, user_positions in user_positions_map.items():
                if not user_positions: continue
                user = db.query(User).filter_by(telegram_id=user_id).first()
                if not user or not user.api_key: continue
                
                bx = get_bx(user)
                
                for p in user_positions:
                    sym = p.get("symbol")
                    direction = p.get("direction")
                    cur_price = p.get("current_price", 0)
                    tp1 = p.get("tp1", 0)
                    tp2 = p.get("tp2", 0)
                    entry = p.get("entry", 0)
                    qty = p.get("qty", 0)
                    
                    if not sym or not cur_price or not tp1 or qty <= 0:
                        continue
                        
                    # [FIX v6.12] RETIRE: với 4 mốc TP, duy trì 2 luồng xử lý
                    # song song (đây + evaluate_reversal_for_position/30s) ngày
                    # càng dễ lệch nhau — đúng kiểu lỗi double-close đã sửa
                    # trước đây. Giờ vòng lặp này CHỈ còn vai trò cảnh báo nếu
                    # phát hiện giá đã qua TP1 khá lâu mà luồng chính (30s) vẫn
                    # chưa xử lý — không tự hành động để tránh trùng lặp logic.
                    cache = _get_active_redis()
                    redis_key = f"SCALE_OUT_{user_id}_{sym}_{direction}"
                    already_handled = bool(cache.exists(redis_key)) if cache else False
                    if already_handled:
                        continue

                    is_hit = False
                    if direction == "LONG" and cur_price >= tp1 and tp1 > entry:
                        is_hit = True
                    elif direction == "SHORT" and cur_price <= tp1 and tp1 < entry:
                        is_hit = True

                    if is_hit:
                        stale_key = f"TP1_STALE_WARN_{user_id}_{sym}_{direction}"
                        already_warned = bool(cache.exists(stale_key)) if cache else False
                        if not already_warned:
                            log.warning("⚠️ [TP-MONITOR] %s %s đã qua TP1 (%.4f) nhưng luồng chính (30s) "
                                        "chưa xử lý — kiểm tra sync_bingx_positions có đang chạy không.",
                                        sym, direction, tp1)
                            if cache:
                                try: cache.setex(stale_key, 300, "1")  # cảnh báo tối đa 1 lần/5 phút
                                except Exception: pass
            db.close()
        except Exception as e:
            log.error("_tp1_monitor error: %s", e)
        time.sleep(15)

def _build_pnl_report_text(db: Session, user_id: str = None, period: str = "24H") -> str:
    try:
        days = 1 if "24" in period else 7
        user = db.query(User).filter(User.telegram_id == user_id).first() if user_id else None
        
        # Thống kê giai đoạn ngắn (24H hoặc 7 ngày)
        stats_period = _compute_win_stats(db, user_id=user_id, days=days)
        # Thống kê tổng hợp toàn thời gian (90 ngày)
        stats_all = _compute_win_stats(db, user_id=user_id, days=90)
        
        equity = user.capital if user else sum(u.capital or 0 for u in db.query(User).filter(User.is_active == True).all())
        tier_label = TIER_CONFIG.get(user.tier, {}).get("label", "Standard") if user else "Hệ Thống Tổng"
        
        # Lấy PnL thực tế của các vị thế đang mở
        with _POS_LOCK:
            if user_id:
                user_pos = [p for p in LIVE_POSITIONS if str(p.get("user_id")) == str(user_id)]
            else:
                user_pos = list(LIVE_POSITIONS)
                
        unrealized_pnl_usd = sum(float(p.get("pnl", 0)) for p in user_pos)
        
        wr = stats_all.get("win_rate", 0)
        pf = stats_all.get("profit_factor", 0)
        total_trades = stats_all.get("total_trades", 0)
        wins = stats_all.get("wins", 0)
        losses = stats_all.get("losses", 0)
        total_pnl_usd = stats_all.get("total_pnl_usd", 0.0)
        total_pnl_pct = stats_all.get("total_pnl_pct", 0.0)
        
        # Đánh giá khả năng sinh lời thực tế dựa trên PnL USD thực và Win Rate
        if total_pnl_usd < 0:
            rating = "🛡️ PHÒNG THỦ & PHỤC HỒI (Vốn đang bị sụt giảm, Bot siết SL & giảm 50% size để bảo vệ vốn)"
            proj_monthly = "Tối ưu quản trị rủi ro phục hồi vốn"
        elif wr >= 60 and pf >= 1.3 and total_pnl_usd > 0:
            rating = "🔥 RẤT CAO (Tăng trưởng vốn thực tế xuất sắc)"
            proj_monthly = "+10% ~ +22% / tháng"
        elif total_pnl_usd >= 0:
            rating = "⚡ ỔN ĐỊNH (Tăng trưởng bền vững & bảo toàn vốn)"
            proj_monthly = "+5% ~ +12% / tháng"
        else:
            rating = "⚖️ THEO DÕI THỊ TRƯỜNG"
            proj_monthly = "+3% ~ +8% / tháng"
            
        pos_str = ""
        if user_pos:
            pos_str += f"\n\n📌 <b>Vị Thế Đang Chạy ({len(user_pos)}):</b>\n"
            for p in user_pos:
                sym = p.get("symbol", "N/A")
                dir_ = p.get("direction", "LONG")
                roe = float(p.get("pnl_pct", 0))
                pnl_u = float(p.get("pnl", 0))
                icon = "🟢" if roe >= 0 else "🔴"
                pos_str += f"  • <b>{sym}</b> ({dir_}): {icon} <b>{roe:+.2f}% ROE</b> (${pnl_u:+.2f})\n"
        else:
            pos_str += "\n\n📌 <b>Vị Thế Đang Chạy:</b> Chưa có lệnh mở (Đang scan điểm vào an toàn)"

        period_pnl_u = stats_period.get("total_pnl_usd", 0.0)
        period_pnl_p = stats_period.get("total_pnl_pct", 0.0)

        msg = (
            f"📊 <b>BÁO CÁO PHÂN TÍCH HIỆU SUẤT & LỜI LỖ ({period})</b>\n"
            f"👤 Tài khoản: <b>{user_id or 'HỆ THỐNG TỔNG'}</b> [{tier_label}]\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>Tài Sản & PnL Thực Tế:</b>\n"
            f"  • Vốn Equity Hiện Tại: <b>${equity:.2f}</b>\n"
            f"  • PnL lệnh đang mở: <b>${unrealized_pnl_usd:+.2f}</b>\n"
            f"  • PnL chốt trong {period}: <b>${period_pnl_u:+.2f} ({period_pnl_p:+.2f}% Vốn)</b>\n\n"
            f"📈 <b>Thống Kê Lịch Sử (90 Ngày):</b>\n"
            f"  • Tăng trưởng vốn (Real ROI): <b>${total_pnl_usd:+.2f} ({total_pnl_pct:+.2f}% Vốn)</b>\n"
            f"  • Win Rate (Tỷ lệ thắng): <b>{wr}%</b> ({wins} Thắng / {losses} Thua)\n"
            f"  • Profit Factor (Hệ số lời/lỗ): <b>{pf}</b>\n"
            f"  • Tổng số lệnh đã đóng: <b>{total_trades} lệnh</b>\n\n"
            f"🔮 <b>ĐÁNH GIÁ KHẢ NĂNG SINH LỜI BOT:</b>\n"
            f"  • Đánh giá hiệu suất: <b>{rating}</b>\n"
            f"  • Dự báo mục tiêu: <b>{proj_monthly}</b>\n"
            f"  • Cơ chế bảo mật: <i>Kalman Filter + HMM Regime + SL Floor (>=1.5%) + Noise Buffer</i>"
            f"{pos_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⏰ <i>Thời gian báo cáo: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</i>"
        )
        return msg
    except Exception as e:
        log.error("_build_pnl_report_text error: %s", e)
        return f"📊 <b>BÁO CÁO PNL</b>\n❌ Không thể tạo báo cáo: {e}"


def _send_daily_report(target_chat_id: str = None):
    try:
        db = SessionLocal()
        users = db.query(User).filter(User.is_active == True).all()
        target_ids = [target_chat_id] if target_chat_id else [u.telegram_id for u in users if u.telegram_id]
        if not target_ids and ADMIN_CHAT_ID:
            target_ids = [ADMIN_CHAT_ID]
            
        for tid in set(target_ids):
            if not tid: continue
            report_msg = _build_pnl_report_text(db, user_id=tid, period="24H")
            _tg_send(REGISTER_TOKEN, tid, report_msg)
        db.close()
    except Exception as e:
        log.error("_send_daily_report error: %s", e)


def _send_weekly_report(target_chat_id: str = None):
    try:
        db = SessionLocal()
        users = db.query(User).filter(User.is_active == True).all()
        target_ids = [target_chat_id] if target_chat_id else [u.telegram_id for u in users if u.telegram_id]
        if not target_ids and ADMIN_CHAT_ID:
            target_ids = [ADMIN_CHAT_ID]
            
        for tid in set(target_ids):
            if not tid: continue
            report_msg = _build_pnl_report_text(db, user_id=tid, period="7 NGÀY")
            _tg_send(REGISTER_TOKEN, tid, report_msg)
        db.close()
    except Exception as e:
        log.error("_send_weekly_report error: %s", e)


def _schedule_weekly_report():
    """Tự động gửi báo cáo hiệu suất lời lỗ định kỳ mỗi ngày"""
    while True:
        try:
            time.sleep(86400) # Mỗi 24 giờ
            log.info("📋 Bắt đầu gửi báo cáo PnL định kỳ hàng ngày...")
            _send_daily_report()
        except Exception as e:
            log.error("_schedule_weekly_report loop error: %s", e)
            time.sleep(3600)

def _register_telegram_webhook():
    try:
        url = f"https://api.telegram.org/bot{REGISTER_TOKEN}/setWebhook"
        webhook_url = f"{os.getenv('RENDER_URL', 'https://auto-trade-v6.onrender.com')}/telegram/webhook"
        r = _req.get(url, params={"url": webhook_url}, timeout=10)
        if r.status_code == 200:
            log.info("✅ Auto register Telegram Webhook success: %s", webhook_url)
    except Exception as e:
        log.warning("⚠️ Error registering Telegram Webhook: %s", e)


def _is_precious_metal(symbol: str = "", asset_type: str = "") -> bool:
    atype = str(asset_type or "").upper()
    if atype in ("GOLD", "SILVER"):
        return True
    s = str(symbol or "").upper()
    return any(k in s for k in ("NCCOGOLD", "NCCOXAG", "XAU", "XAG", "GOLD", "SILVER"))


def calculate_dynamic_leverage(signal: dict, user_base_leverage: int = 10, sl_pct: float = 0.02) -> tuple[int, str, dict]:
    """
    TÍNH TOÁN ĐÒN BẨY LINH ĐỘNG (10x - 20x; RIÊNG VÀNG/BẠC CỐ ĐỊNH 50x CHO 2 CHIỀU LONG/SHORT):
    - Vàng / Bạc (NCCOGOLD, NCCOXAG, XAU, XAG): cố định 50x cho cả 2 chiều LONG và SHORT.
    - Các mã khác (Crypto / Stock):
      + Khi ở vùng giữa Range / Chưa có SFP: giữ đòn bẩy nền 10x.
      + Khi có Deviation / SFP chuẩn trên 1H/4H: 12x - 15x.
      + Khi A+ Setup (HTF Confluence + R:R >= 2.5 + Conf >= 84%): 18x - 20x.
      + Anti-Liquidation Shield: giới hạn trong [10, 20].
    """
    symbol = signal.get("symbol", "")
    asset_type = signal.get("asset_type", "")
    direction = signal.get("direction") or signal.get("final") or "WAIT"
    trade_regime = signal.get("trade_regime", "RANGE_DEVIATION")
    tsz = signal.get("traderssz", {})
    setup = tsz.get("setup", {}) if isinstance(tsz, dict) else {}
    is_htf_aligned = bool(tsz.get("is_htf_aligned", False)) if isinstance(tsz, dict) else False
    rr_ratio = float(signal.get("rr_ratio", 2.0))
    conf = float(signal.get("confidence", 70.0))
    setup_tf = str(setup.get("tf", ""))

    if _is_precious_metal(symbol, asset_type):
        effective_leverage = 50
        mode = "PRECIOUS_METAL_50X"
        reason = "Vàng/Bạc cố định 50x (2 chiều LONG/SHORT)"
        meta = {
            "mode": mode,
            "target_lev": 50,
            "effective_leverage": 50,
            "is_htf_aligned": is_htf_aligned,
            "setup_tf": setup_tf,
            "shield_applied": False,
            "reason": reason,
        }
        return effective_leverage, reason, meta

    if trade_regime == "NO_TRADE_RANGE" or direction == "WAIT":
        target_lev = 10
        mode = "NO_TRADE_RANGE"
        reason = "Vùng giữa Range / Chưa có SFP (Giữ đòn bẩy nền 10x)"
    elif trade_regime == "TREND_FOLLOWING" or (is_htf_aligned and setup_tf in ("1d", "4h", "1h") and rr_ratio >= 2.5):
        if conf >= 88.0 and rr_ratio >= 3.0:
            target_lev = 20
            mode = "TRADERSZ_A_PLUS"
            reason = f"TraderSZ A+ HTF Deviation + Opens Confluence (Conf: {conf:.1f}%, R:R 1:{rr_ratio:.1f}) -> 20x"
        else:
            target_lev = 18
            mode = "TRADERSZ_HTF_ALIGNED"
            reason = f"TraderSZ SFP đồng thuận HTF & Opens (Conf: {conf:.1f}%, R:R 1:{rr_ratio:.1f}) -> 18x"
    elif setup_tf in ("4h", "1h") and conf >= 78.0:
        target_lev = 15 if is_htf_aligned else 12
        mode = "RANGE_DEVIATION"
        reason = f"TraderSZ {setup_tf.upper()} Range Deviation/SFP -> {target_lev}x"
    else:
        target_lev = max(10, min(20, int(user_base_leverage or 10)))
        mode = "RANGE_BASE"
        reason = f"Range Sweep nền ({target_lev}x)"

    # Lớp bảo vệ chống thanh lý (Anti-Liquidation Shield) trong biên [10, 20]
    shield_applied = False
    if sl_pct and sl_pct > 0.001:
        max_safe_by_sl = max(10, int(round(0.60 / sl_pct)))
        if target_lev > max_safe_by_sl:
            shield_applied = True
            prev_lev = target_lev
            target_lev = max_safe_by_sl
            reason += f" [Shield: Hạ từ {prev_lev}x về {target_lev}x do SL {sl_pct*100:.1f}%]"

    effective_leverage = max(10, min(20, target_lev))

    meta = {
        "mode": mode,
        "target_lev": target_lev,
        "effective_leverage": effective_leverage,
        "is_htf_aligned": is_htf_aligned,
        "setup_tf": setup_tf,
        "shield_applied": shield_applied,
        "reason": reason
    }
    return effective_leverage, reason, meta


log = logging.getLogger("ReversalEvaluator")

ORDER_MARGIN_MIN_USD = float(os.getenv("ORDER_MARGIN_MIN_USD", "20.0"))
ORDER_MARGIN_MAX_USD = float(os.getenv("ORDER_MARGIN_MAX_USD", "30.0"))


def _calc_order_sizing(
    capital: float,
    max_risk_pct: float,
    entry: float,
    sl_pct: float,
    leverage: int,
    confidence: float = 75.0,
    size_mult: float = 1.0,
) -> tuple[float, float, float]:
    """
    Tính khối lượng lệnh sao cho:
    - Tỉ lệ vốn ký quỹ (Margin) nằm trong vùng mục tiêu $20 - $30 USD / lệnh.
    - Tự động co theo độ tin cậy tín hiệu & hệ số vĩ mô (news/Fed), nhưng không tụt dưới $20
      (trừ khi 30% vốn tài khoản nhỏ hơn $20) và không vượt $30 hoặc 30% vốn.
    Trả về: (qty, req_margin_usd, risk_amt_usd)
    """
    cap = max(0.0, float(capital or 0))
    lev = max(1, int(leverage or 10))
    if cap <= 0 or entry <= 0 or sl_pct <= 0:
        return 0.0, 0.0, 0.0

    risk_amt = cap * (float(max_risk_pct or 2.0) / 100.0)
    base_qty = (risk_amt / (entry * sl_pct)) * max(0.5, float(size_mult or 1.0))
    base_margin = (base_qty * entry) / lev

    max_safe_cap = cap * 0.30
    target_min = min(ORDER_MARGIN_MIN_USD, max_safe_cap)
    target_max = min(ORDER_MARGIN_MAX_USD, max_safe_cap)

    quality_ratio = max(0.0, min(1.0, (float(confidence or 75.0) - 65.0) / 30.0))
    band_margin = target_min + (target_max - target_min) * quality_ratio
    if size_mult < 1.0:
        band_margin = max(target_min, band_margin * max(0.75, float(size_mult)))

    req_margin = max(base_margin, band_margin)
    if target_min > 0:
        req_margin = max(req_margin, target_min)
    if target_max > 0:
        req_margin = min(req_margin, target_max)

    qty = round((req_margin * lev) / entry, 4)
    actual_margin = round((qty * entry) / lev, 2)
    actual_risk = round(qty * entry * sl_pct, 2)
    return qty, actual_margin, actual_risk


def _clear_position_caches(cache, user_id: str, sym: str, direction: str):
    if not cache:
        return
    for s_var in _sym_variants(sym):
        keys = [
            f"BREAKEVEN_{user_id}_{s_var}_{direction}",
            f"SCALE_OUT_{user_id}_{s_var}_{direction}",
            f"ENTRY_PLAN_{user_id}_{s_var}_{direction}",
            f"ORIGINAL_QTY_{user_id}_{s_var}_{direction}",
            f"PEAK_PRICE_{s_var}_{direction}",
            f"SL_COOLDOWN_{s_var}_{direction}",
            f"TRAILING_SL_{user_id}_{s_var}_{direction}_1",
            f"TRAILING_SL_{user_id}_{s_var}_{direction}_2",
            f"SL_HEAL_COOLDOWN:{user_id}:{s_var}:{direction}",
            f"TP1_DONE:{user_id}:{s_var}:{direction}",
        ]
        for k in keys:
            try:
                cache.delete(k)
            except Exception:
                pass


def _get_entry_plan_from_cache(cache, user_id: str, sym: str, direction: str) -> Optional[dict]:
    if not cache:
        return None
    for s_var in _sym_variants(sym):
        try:
            raw_ep = cache.get(f"ENTRY_PLAN_{user_id}_{s_var}_{direction}")
            if raw_ep:
                ep_data = json.loads(raw_ep) if isinstance(raw_ep, str) else raw_ep
                if isinstance(ep_data, dict):
                    return ep_data
        except Exception:
            pass
    return None


def _is_position_protected_runner(
    bx,
    user: User,
    sym: str,
    direction: str,
    entry: float,
    current_price: float,
    net_pnl_usd: float = 0.0,
    triggers: Optional[dict] = None,
) -> tuple[bool, float, str]:
    """
    Chỉ giữ lại vị thế gồng tiếp khi ĐỒNG THỜI thỏa mãn 2 điều kiện:
    1) Có entry đẹp / đang có lợi nhuận dương (giá vượt qua điểm hòa vốn có đệm phí & PnL >= 0).
    2) Stop Loss thực tế trên sàn BingX ĐÃ được dời về vùng dương bảo vệ lãi
       (LONG: SL > entry; SHORT: 0 < SL < entry).
    """
    if entry <= 0 or current_price <= 0:
        return False, 0.0, "Thiếu dữ liệu giá entry/hiện tại"

    be_p = bx.breakeven_price(direction, entry)
    if direction == "LONG":
        in_good_profit = current_price > max(entry, be_p) and net_pnl_usd >= 0
    else:
        in_good_profit = current_price < min(entry, be_p) and net_pnl_usd >= 0

    if not in_good_profit:
        return False, 0.0, f"Entry chưa có lãi đẹp (PnL ${net_pnl_usd:+.2f}, giá ${current_price:.4f} vs BE ${be_p:.4f})"

    if triggers is None:
        try:
            triggers = bx.get_trigger_orders()
        except Exception:
            triggers = None

    if triggers is None:
        # Lỗi API tạm thời: kiểm tra cache breakeven để tránh đóng oan lệnh đang gồng lãi
        cache = _get_active_redis()
        be_cached = False
        if cache:
            try:
                be_cached = bool(cache.get(f"BREAKEVEN_{user.telegram_id}_{sym}_{direction}"))
            except Exception:
                pass
        if be_cached:
            return True, be_p, f"Entry đẹp & cache xác nhận đã dời SL về dương (${be_p:.4f})"
        return True, 0.0, "Tạm giữ do lỗi đọc trigger orders khi vị thế đang có lãi"

    clean_sym = sym.replace("-", "").upper()
    trig = triggers.get(sym) or triggers.get(clean_sym) or {}
    current_sl = float(trig.get("sl", 0) or 0)

    if direction == "LONG":
        sl_in_profit = current_sl > 0 and current_sl > entry
    else:
        sl_in_profit = 0 < current_sl < entry

    if sl_in_profit:
        return True, current_sl, f"Entry đẹp (PnL ${net_pnl_usd:+.2f}) & SL đã dời về dương (${current_sl:.4f})"

    return False, current_sl, f"SL chưa dời về vùng dương (SL=${current_sl:.4f} vs Entry=${entry:.4f})"


def evaluate_reversal_for_position(user: User, pos: dict, current_price: float, db: Session, triggers: Optional[dict] = None) -> dict:
    sym = pos["symbol"]
    clean_sym = _clean_sym(sym)
    scanner_sym = _to_scanner_sym(sym)
    direction = pos["direction"]
    qty = float(pos.get("qty", 0))
    entry = float(pos.get("entry", 0))

    if qty <= 0:
        return {"action": "SKIP"}

    now = time.time()
    cache_ttl = 150 if _is_precious_metal(sym) else 180

    with _get_user_sym_lock(user.telegram_id, sym):
      try:
        # 1. DÙNG CHUNG CACHE PHÂN TÍCH VỚI SIGNALBOT (TRÁNH 2 LUỒNG PHÂN TÍCH LỆCH NHAU)
        cached = _LAST_REVERSAL_EVAL.get(clean_sym)
        analysis = None
        if cached and isinstance(cached, dict) and now - cached.get("time", 0) < cache_ttl:
            new_direction = cached.get("direction", "WAIT")
            conf = cached.get("conf", 0)
            analysis = cached.get("analysis", {})
        else:
            # Kiểm tra cache mới nhất từ SignalBot trong local_store
            try:
                raw_shared = local_store.get(f"SHARED_ANALYSIS:{clean_sym}")
                if raw_shared:
                    shared_data = json.loads(raw_shared) if isinstance(raw_shared, str) else raw_shared
                    if isinstance(shared_data, dict) and shared_data.get("plan"):
                        analysis = shared_data
                        new_direction = analysis.get("final", "WAIT")
                        conf = float(analysis.get("confidence", 0) or 0)
                        _LAST_REVERSAL_EVAL[clean_sym] = {
                            "time": now,
                            "direction": new_direction,
                            "conf": conf,
                            "analysis": analysis,
                        }
            except Exception:
                analysis = None

        if analysis is None:
            from analyzer.engine import SignalEngine
            engine = SignalEngine()
            analysis = engine.full_analysis(scanner_sym, db=db)
            new_direction = analysis.get("final", "WAIT")
            conf = float(analysis.get("confidence", 0) or 0)

            _LAST_REVERSAL_EVAL[clean_sym] = {
                "time": now,
                "direction": new_direction,
                "conf": conf,
                "analysis": analysis,
            }
            try:
                local_store.setex(f"SHARED_ANALYSIS:{clean_sym}", cache_ttl, json.dumps(analysis, default=str))
            except Exception:
                pass

        # ══════════════════════════════════════════════════════════
        # [NEW] LỚP BẢO VỆ ĐỘNG (BREAKEVEN & QUẢN LÝ TP/SL TRADERSZ)
        # ══════════════════════════════════════════════════════════
        bx = get_bx(user)
        leverage = 50 if _is_precious_metal(sym) else max(10, int(getattr(user, 'leverage', 10) or 10))

        cache = _get_active_redis()
        be_key = f"BREAKEVEN_{user.telegram_id}_{sym}_{direction}"
        is_breakeven = False
        try:
            is_breakeven = any(bool(cache.get(f"BREAKEVEN_{user.telegram_id}_{sv}_{direction}")) for sv in _sym_variants(sym)) if cache else False
        except Exception:
            pass

        # Đọc ENTRY_PLAN gốc TRƯỚC khi gọi manage_position_dynamic để dời Hòa vốn đúng theo TP1/TP2 của lệnh
        plan = dict(analysis.get("plan", {}) or {})
        tp_levels_raw = plan.get("tp_levels")
        ep_data = _get_entry_plan_from_cache(cache, user.telegram_id, sym, direction)
        if ep_data:
            if float(ep_data.get("sl") or 0) > 0:
                plan["sl"] = float(ep_data["sl"])
            if float(ep_data.get("tp1") or 0) > 0:
                plan["tp1"] = float(ep_data["tp1"])
            if float(ep_data.get("tp2") or 0) > 0:
                plan["tp2"] = float(ep_data["tp2"])
            if ep_data.get("tp_levels"):
                tp_levels_raw = ep_data["tp_levels"]
                plan["tp_levels"] = tp_levels_raw

        if not tp_levels_raw:
            tp_levels_raw = [
                {"level": 1, "price": float(plan.get("tp1", 0) or 0), "close_pct": 0.5},
                {"level": 2, "price": float(plan.get("tp2", 0) or 0), "close_pct": 0.5},
            ]
            plan["tp_levels"] = tp_levels_raw

        pos_analysis = dict(analysis)
        pos_analysis["plan"] = plan

        # Chạy quản trị vị thế: Dời SL về Hòa vốn (Breakeven) khi đạt 50% TP1 hoặc qua TP1
        dynamic_status = bx.manage_position_dynamic(
            sym, pos_analysis, leverage=leverage, redis_client=cache, target_direction=direction
        )

        if dynamic_status.get("action") in ("BREAKEVEN", "TRAILING_SL"):
            triggers = None  # Cần đọc lại SL mới vừa dời trên BingX nếu kiểm tra đảo chiều
            if dynamic_status.get("action") == "BREAKEVEN" and cache:
                try:
                    cache.setex(be_key, 86400, "1")
                    is_breakeven = True
                except Exception:
                    pass
            target_lvl = dynamic_status.get("level", 1)
            new_sl_val = dynamic_status.get("new_sl", entry)
            sl_notif_key = f"TRAILING_SL_{user.telegram_id}_{sym}_{direction}_{target_lvl}"
            is_sl_notified = False
            try:
                is_sl_notified = bool(cache.get(sl_notif_key)) or _SENT_NOTIFICATIONS.get(sl_notif_key, False)
            except Exception:
                is_sl_notified = _SENT_NOTIFICATIONS.get(sl_notif_key, False)

            if not is_sl_notified:
                lvl_str = f"Entry (Hòa vốn <code>${new_sl_val:.4f}</code>)" if target_lvl == 1 else f"mốc TP{target_lvl-1} (<code>${new_sl_val:.4f}</code>)"
                _tg_send(
                    REGISTER_TOKEN, user.telegram_id,
                    f"🛡️ <b>DỜI SL HÒA VỐN / BẢO VỆ: {sym}</b>\n"
                    f"Giá đã đi được 50% chặng đường tới TP{target_lvl}. Tự động dời Stoploss về {lvl_str} để bảo vệ vốn!"
                )
                _SENT_NOTIFICATIONS[sl_notif_key] = True
                try:
                    cache.setex(sl_notif_key, 86400, "1")
                except Exception:
                    pass

        elif dynamic_status.get("action") == "CLOSE":
            roe_closed = dynamic_status.get("roe", 0)
            close_type = dynamic_status.get("type", "ĐÓNG VỊ THẾ")
            close_icon = "🎯" if roe_closed > 0 else "📉"
            _tg_send(
                REGISTER_TOKEN, user.telegram_id,
                f"{close_icon} <b>THÔNG BÁO TỰ ĐỘNG: {sym} ({direction})</b>\n\n"
                f"Loại: <b>{close_type}</b>\n"
                f"Lãi/Lỗ (ROE): <b>{roe_closed:+.2f}%</b>"
            )
            _save_journal(user.telegram_id, sym, direction, roe_closed, qty)

            if cache:
                _clear_position_caches(cache, user.telegram_id, sym, direction)
                for sv in _sym_variants(sym):
                    try:
                        cache.setex(f"REVERSAL_CLOSED:{user.telegram_id}:{sv}:{direction}", 120, "1")
                    except Exception:
                        pass
            return {"action": "CLOSED", "old_direction": direction}

        # 2. TÍNH TOÁN VỊ THẾ HIỆN TẠI
        in_profit = False
        pnl_pct = 0.0
        net_pnl_usd = 0.0

        if entry > 0:
            in_profit = (direction == "LONG" and current_price > entry) or (direction == "SHORT" and current_price < entry)
            pnl_pct = ((current_price - entry) / entry) * 100 * leverage if direction == "LONG" else ((entry - current_price) / entry) * 100 * leverage
            gross_pnl_usd = (current_price - entry) * qty if direction == "LONG" else (entry - current_price) * qty
            fee_usd = qty * entry * 0.0010
            pos_api_pnl = float(pos.get("pnl", 0))
            net_pnl_usd = pos_api_pnl if pos_api_pnl != 0 else (gross_pnl_usd - fee_usd)

        is_reversal = (direction == "LONG" and new_direction == "SHORT") or (direction == "SHORT" and new_direction == "LONG")

        # ══════════════════════════════════════════════════════════
        # [BẢN VÁ LỖI TOÁN HỌC]: CHẶN TP ẢO DO AI ĐẢO VIEW
        # ══════════════════════════════════════════════════════════
        tp_levels = []
        for lv in tp_levels_raw:
            lvl_p = float(lv.get("price", 0))
            if direction == "LONG" and lvl_p <= entry:
                lvl_p = 0
            elif direction == "SHORT" and lvl_p >= entry:
                lvl_p = 0
            tp_levels.append({"level": lv.get("level"), "price": lvl_p, "close_pct": lv.get("close_pct", 0.25)})

        tp1 = tp_levels[0]["price"] if tp_levels else 0

        # [NEW v6.12] Đếm số mốc TP đã xử lý
        partial_key = f"SCALE_OUT_{user.telegram_id}_{sym}_{direction}"
        tp_progress = 0
        cache = _get_active_redis()
        if cache:
            try:
                raw_p = cache.get(partial_key)
                tp_progress = int(raw_p) if raw_p else 0
            except Exception:
                tp_progress = 0
        is_scaled_out = tp_progress > 0 


        # ══════════════════════════════════════════════════════════
        # LOGIC GIAO DỊCH (HỆ THỐNG QUẢN LÝ TRẠNG THÁI ƯU TIÊN ĐỘNG)
        # ══════════════════════════════════════════════════════════
        action = "KEEP"
        reason = "Xu hướng ủng hộ, tiếp tục giữ lệnh."
        should_reverse = False
        emoji = "🛡️"
        action_type = ""

        DYNAMIC_SL_LIMIT = -35.0

        # 🚨 [TẦNG ƯU TIÊN 0]: PHẢN XẠ CHỐT TỪNG MỐC TP TRADERSZ (TP1 = 50% EQ -> DỜI HÒA VỐN, TP2 = BIÊN ĐỐI DIỆN)
        next_tp = tp_levels[tp_progress] if tp_progress < len(tp_levels) else None
        if next_tp and next_tp["price"] > 0:
            reached = (direction == "LONG" and current_price >= next_tp["price"]) or \
                      (direction == "SHORT" and current_price <= next_tp["price"])
            if reached:
                action = "SCALE_OUT"
                action_type = f"CHỐT LỜI TP{next_tp['level']}"
                emoji = "🎯"
                reason = (f"Giá chạm mốc TP{next_tp['level']} TraderSZ (${next_tp['price']:.4f}). "
                          f"Xử lý TP{next_tp['level']} & dời SL về Hòa vốn bảo vệ phần còn lại.")

        if action != "SCALE_OUT":
            # Bảo vệ cắt lỗ an toàn tối đa (-35% ROE) khi SL sàn bị trượt
            if pnl_pct <= DYNAMIC_SL_LIMIT:
                action = "CLOSE_ALL"
                action_type = "CẮT LỖ BẢO VỆ VỐN (VIRTUAL SL TOÀN DIỆN)"
                emoji = "🚨"
                reason = f"Trạng thái âm {pnl_pct:.2f}% vượt ngưỡng lỗ tối đa ({DYNAMIC_SL_LIMIT}%) -> Cắt lỗ bảo toàn tài khoản."
            else:
                # 🛡️ FAILED BREAKOUT KILL-SWITCH (TraderSZ):
                # Nếu vị thế đang giữ là BREAKOUT CONTINUATION mà giá đóng quay ngược lại vào trong Range
                # hoặc xuất hiện tín hiệu ngược dạng Return-to-Range/SFP -> Cắt nhanh Breakout giả & đảo chiều!
                held_setup_type = str((ep_data or {}).get("setup_type", "") or "")
                held_swept_lvl = float((ep_data or {}).get("swept_level", 0) or 0)
                new_setup_obj = (analysis.get("traderssz", {}) or {}).get("setup", {}) or {}
                new_setup_type = str(new_setup_obj.get("setup_type", "") or "")
                is_failed_bo_close = False
                if held_setup_type == "RANGE_BREAKOUT_CONTINUATION" and held_swept_lvl > 0:
                    if direction == "LONG" and current_price < held_swept_lvl * 0.9975:
                        is_failed_bo_close = True
                    elif direction == "SHORT" and current_price > held_swept_lvl * 1.0025:
                        is_failed_bo_close = True

                min_conf = float(getattr(user, "min_confidence", 68) or 68)
                rev_plan = analysis.get("plan", {}) or {}
                has_valid_rev_plan = float(rev_plan.get("entry", 0) or 0) > 0 and float(rev_plan.get("sl", 0) or 0) > 0
                is_failed_bo_rev = bool(
                    is_reversal
                    and (held_setup_type == "RANGE_BREAKOUT_CONTINUATION" or new_setup_obj.get("is_failed_breakout"))
                    and new_setup_type in ("DEVIATION_RETURN_TO_RANGE", "SFP_WICK_REJECTION", "LEVEL_RECLAIM_SR_FLIP")
                    and conf >= min(64.0, min_conf)
                    and has_valid_rev_plan
                )

                if is_failed_bo_rev:
                    action = "CLOSE_ALL"
                    should_reverse = True
                    action_type = "FAILED BREAKOUT KILL-SWITCH & ĐẢO CHIỀU"
                    emoji = "⚡"
                    reason = (
                        f"Phát hiện Breakout giả (Failed Breakout): {direction} đảo sang {new_direction} "
                        f"({new_setup_type}, Conf {conf:.1f}%) -> Cắt nhanh & đảo theo SFP quay lại Range."
                    )
                    log.info("⚡ [FAILED BREAKOUT REVERSAL] %s %s: %s", user.telegram_id, sym, reason)
                elif is_failed_bo_close and not (new_direction == direction and new_setup_type == "RANGE_BREAKOUT_CONTINUATION"):
                    action = "CLOSE_ALL"
                    should_reverse = False
                    action_type = "CẮT NHANH FAILED BREAKOUT (QUAY LẠI RANGE)"
                    emoji = "⚡"
                    reason = (
                        f"Lệnh Breakout {direction} đã mất mốc biên ${held_swept_lvl:.4f} (giá hiện tại ${current_price:.4f}) "
                        f"-> Thoát nhanh bảo vệ vốn theo kỷ luật TraderSZ."
                    )
                    log.info("⚡ [FAILED BREAKOUT EXIT] %s %s: %s", user.telegram_id, sym, reason)
                elif is_reversal:
                    if conf >= min_conf and has_valid_rev_plan:
                        action = "CLOSE_ALL"
                        should_reverse = True
                        action_type = "ĐÓNG TOÀN BỘ & ĐẢO CHIỀU THEO TÍN HIỆU MỚI"
                        emoji = "🔄"
                        reason = (
                            f"Phát hiện tín hiệu đảo chiều {direction} → {new_direction} (Conf {conf:.1f}% >= {min_conf:.0f}%) "
                            f"-> Đóng toàn bộ vị thế cũ & vào lệnh theo tín hiệu mới."
                        )
                        log.info("🔄 [REVERSAL TRIGGERED] %s %s: %s", user.telegram_id, sym, reason)
                    else:
                        action = "HOLD"
                        log.info(
                            "Giữ vị thế %s (%s): Tín hiệu ngược %s (%.1f%%) chưa đạt min_conf %.1f%% hoặc thiếu plan SL/TP.",
                            sym, direction, new_direction, conf, min_conf
                        )
                else:
                    # BỎ HOÀN TOÀN CHỐT LỜI ĐỘNG / CHỐT LỜI SIDEWAYS / CHỐT LỜI ĐẢO CHIỀU $1
                    action = "HOLD"
                    log.info(f"Giữ vị thế {sym} ({direction}): PnL {pnl_pct:+.2f}% (${net_pnl_usd:+.2f} USD), giữ chạy tới TP1/TP2 hoặc SL TraderSZ.")

        # ══════════════════════════════════════════════════════════
        # THỰC THI GIAO DỊCH (BẢO LƯU TOÀN BỘ CẤU TRÚC GỐC)
        # ══════════════════════════════════════════════════════════
        if action != "KEEP" and action != "HOLD":
            
            # --- CƠ CHẾ GỒNG: CHỐT TỪNG MỐC TP & DỜI SL ---
            if action == "SCALE_OUT":
                lv = tp_levels[tp_progress]
                next_lv = tp_levels[tp_progress + 1] if tp_progress + 1 < len(tp_levels) else None
                prev_price = tp_levels[tp_progress - 1]["price"] if tp_progress > 0 else None

                original_qty = qty
                cache = _get_active_redis()
                if cache:
                    try:
                        raw_oq = cache.get(f"ORIGINAL_QTY_{user.telegram_id}_{sym}_{direction}")
                        if raw_oq:
                            original_qty = float(raw_oq)
                    except Exception:
                        pass

                res = bx.handle_tp_level_hit(
                    sym, direction, lv["level"], original_qty, lv["close_pct"], entry,
                    prev_price, next_lv["price"] if next_lv else None
                )

                if res.get("ok"):
                    new_progress = tp_progress + 1
                    if cache:
                        try: cache.setex(partial_key, 86400, str(new_progress))
                        except: pass

                    if res.get("closed_all"):
                        msg_close_all = (
                            f"{emoji} <b>{action_type} — ĐÓNG TOÀN BỘ: {sym}</b>\n\n"
                            f"📊 Đã chốt hết vị thế {direction} tại TP{lv['level']} (PnL: {pnl_pct:+.2f}%)\n"
                            f"🔍 Lý do: <i>{reason}</i>"
                        )
                        _tg_send(REGISTER_TOKEN, user.telegram_id, msg_close_all)
                        if ADMIN_CHAT_ID and str(user.telegram_id) != str(ADMIN_CHAT_ID):
                            _tg_send(REPORT_TOKEN or REGISTER_TOKEN, ADMIN_CHAT_ID, f"👤 User <code>{user.telegram_id}</code>:\n" + msg_close_all)
                        _save_journal(user.telegram_id, sym, direction, pnl_pct, original_qty)
                        if cache:
                            try: cache.delete(partial_key)
                            except: pass
                    elif res.get("skipped_partial_moved_be"):
                        new_sl = res.get("new_sl", 0)
                        next_target = next_lv["price"] if next_lv else 0
                        if cache:
                            try:
                                cache.setex(be_key, 86400, "1")
                            except Exception:
                                pass
                        msg_be_hold = (
                            f"🛡️ <b>CHẠM TP1 — DỜI SL HÒA VỐN: {sym}</b>\n\n"
                            f"📊 Giá đã chạm TP1 (50% EQ) — giữ 100% vị thế {direction} chạy tới TP2\n"
                            f"🔒 SL đã dời về Hòa vốn: <code>${new_sl:.4f}</code>\n"
                            f"🎯 Target TP2: <code>${next_target:.4f}</code>"
                        )
                        _tg_send(REGISTER_TOKEN, user.telegram_id, msg_be_hold)
                    else:
                        new_sl = res.get("new_sl", 0)
                        next_target = next_lv["price"] if next_lv else 0
                        if cache and lv["level"] == 1:
                            try:
                                cache.setex(be_key, 86400, "1")
                            except Exception:
                                pass
                        msg_partial = (
                            f"{emoji} <b>{action_type}: {sym}</b>\n\n"
                            f"📊 Đã chốt: {int(lv['close_pct']*100)}% vị thế gốc {direction} (PnL: {pnl_pct:+.2f}%)\n"
                            f"🔒 SL phần còn lại: Dời lên <code>${new_sl:.4f}</code>\n"
                            f"🎯 Target tiếp theo (TP{next_lv['level'] if next_lv else '-'}): <code>${next_target:.4f}</code>\n"
                            f"🔍 Lý do: <i>{reason}</i>"
                        )
                        _tg_send(REGISTER_TOKEN, user.telegram_id, msg_partial)
                        if ADMIN_CHAT_ID and str(user.telegram_id) != str(ADMIN_CHAT_ID):
                            _tg_send(REPORT_TOKEN or REGISTER_TOKEN, ADMIN_CHAT_ID, f"👤 User <code>{user.telegram_id}</code>:\n" + msg_partial)
                        _save_journal(user.telegram_id, sym, direction, pnl_pct, res.get("closed_qty", 0))
            
            # --- CƠ CHẾ NGẮT: ĐÓNG TOÀN BỘ VỊ THẾ / ĐẢO CHIỀU ---
            elif action == "CLOSE_ALL":
                bx.cancel_all_orders(sym)
                res = bx.close_position(sym, qty, direction)

                if res.get("ok"):
                    bx.wait_position_closed(sym, direction, timeout=2.5)
                    if cache:
                        try:
                            for sv in _sym_variants(sym):
                                cache.setex(f"REVERSAL_CLOSED:{user.telegram_id}:{sv}:{direction}", 120, "1")
                            _clear_position_caches(cache, user.telegram_id, sym, direction)
                        except Exception:
                            pass

                    msg_rev_close = (
                        f"{emoji} <b>{action_type}: {sym}</b>\n\n"
                        f"📊 Vị thế cũ: {direction} @ ${entry:.4f}\n"
                        f"📈 Giá chốt: ${current_price:.4f} | PnL: {pnl_pct:+.2f}%\n"
                        f"🔍 Lý do: <i>{reason}</i>"
                    )
                    _tg_send(REGISTER_TOKEN, user.telegram_id, msg_rev_close)
                    if ADMIN_CHAT_ID and str(user.telegram_id) != str(ADMIN_CHAT_ID):
                        _tg_send(REPORT_TOKEN or REGISTER_TOKEN, ADMIN_CHAT_ID, f"👤 User <code>{user.telegram_id}</code>:\n" + msg_rev_close)
                    _save_journal(user.telegram_id, sym, direction, pnl_pct, qty)

                    # --- VÀO LỆNH NGƯỢC LẠI THEO TÍN HIỆU ĐẢO CHIỀU ---
                    if should_reverse:
                        new_entry = float(analysis["plan"]["entry"])
                        new_sl    = float(analysis["plan"]["sl"])
                        new_tp1   = float(analysis["plan"]["tp1"])
                        new_tp2   = float(analysis["plan"].get("tp2", 0))
                        rev_tp_levels = analysis.get("plan", {}).get("tp_levels", [])

                        if new_tp2 <= 0:
                            if new_direction == "LONG":
                                new_tp2 = round(new_tp1 + abs(new_tp1 - new_entry), 4)
                            else:
                                new_tp2 = round(new_tp1 - abs(new_tp1 - new_entry), 4)

                        sl_pct = abs(new_entry - new_sl) / new_entry
                        if sl_pct >= 0.001:
                            new_p_win = analysis.get("bayes_ev", {}).get("p_win", 50) / 100
                            new_rr    = analysis.get("rr_ratio", 1.5)
                            rev_lev, rev_reason, _ = calculate_dynamic_leverage(analysis, user.leverage or 10, sl_pct)
                            new_qty, rev_margin, _ = _calc_order_sizing(
                                capital=user.capital,
                                max_risk_pct=user.max_risk_pct,
                                entry=new_entry,
                                sl_pct=sl_pct,
                                leverage=rev_lev,
                                confidence=conf,
                                size_mult=1.0,
                            )

                            if new_qty > 0:
                                bx.set_leverage(sym, leverage=rev_lev, side="ALL")
                                rev_res = bx.place_order(
                                    sym, "BUY" if new_direction == "LONG" else "SELL", new_qty, new_sl, new_tp2,
                                    tp_levels=rev_tp_levels,
                                    leverage=rev_lev, p_win=new_p_win, rr_ratio=new_rr
                                )
                                if rev_res.get("ok"):
                                    actual_rev_qty = float(rev_res.get("qty", new_qty))
                                    actual_rev_margin = round((actual_rev_qty * new_entry) / max(1, rev_lev), 2)
                                    actual_rev_sl = float(rev_res.get("sl_price") or new_sl)
                                    actual_rev_tp = float(rev_res.get("tp_price") or new_tp2)
                                    if cache:
                                        try:
                                            for sv in _sym_variants(sym):
                                                cache.setex(f"PENDING:{user.telegram_id}:{sv}:{new_direction}", 90, "1")
                                                rev_setup = (analysis.get("traderssz", {}) or {}).get("setup", {}) or {}
                                                cache.setex(
                                                    f"ENTRY_PLAN_{user.telegram_id}_{sv}_{new_direction}",
                                                    7 * 86400,
                                                    json.dumps({
                                                        "sl": actual_rev_sl, "tp1": new_tp1, "tp2": actual_rev_tp,
                                                        "tp_levels": rev_tp_levels,
                                                        "setup_type": rev_setup.get("setup_type", ""),
                                                        "swept_level": float(rev_setup.get("swept_level", 0) or 0),
                                                    })
                                                )
                                                cache.setex(
                                                    f"ORIGINAL_QTY_{user.telegram_id}_{sv}_{new_direction}",
                                                    7 * 86400, str(actual_rev_qty)
                                                )
                                        except Exception:
                                            pass
                                    sl_tag = "✅" if rev_res.get("sl_ok", True) else "⚠️(đang tự đồng bộ)"
                                    tp_tag = "✅" if rev_res.get("tp_ok", True) else "⚠️(đang tự đồng bộ)"
                                    _tg_send(
                                        REGISTER_TOKEN, user.telegram_id,
                                        f"🔄 <b>ĐẢO CHIỀU VÀO LỆNH MỚI: {sym}</b>\n"
                                        f"📈 {new_direction} | Conf: {conf:.1f}%\n"
                                        f"⚡ Đòn bẩy: <b>{rev_lev}x</b> ({rev_reason})\n"
                                        f"💰 Qty: {actual_rev_qty:.4f} | Margin: ~${actual_rev_margin:.2f}\n"
                                        f"🛑 SL: <code>${actual_rev_sl:.4f}</code> {sl_tag}\n"
                                        f"🎯 TP1: <code>${new_tp1:.4f}</code> | TP2: <code>${actual_rev_tp:.4f}</code> {tp_tag}"
                                    )
                                    return {"action": "REVERSED", "old_direction": direction, "new_direction": new_direction}
                    return {"action": "CLOSED", "old_direction": direction}

        return {"action": action}
      except Exception as e:
        log.warning("Evaluate reversal for %s %s error: %s", getattr(user, 'telegram_id', 'Unknown'), sym, e)
        return {"action": "ERROR"}

def _cleanup_inactive_users():
    """Tự động dọn dẹp người dùng không hoạt động và dữ liệu nhật ký quá hạn (>180 ngày)."""
    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=30)
        # Hủy kích hoạt user không có API Key và không hoạt động > 30 ngày
        inactive = db.query(User).filter(
            User.is_active == True,
            (User.api_key == None) | (User.api_key == ""),
            User.registered_at <= cutoff
        ).all()
        for u in inactive:
            u.is_active = False
            log.info("🧹 [Cleanup] Tự động tạm khóa tài khoản không cài API key: %s", u.telegram_id)

        # Xóa các bản ghi TradeJournal quá cũ (> 180 ngày)
        journal_cutoff = datetime.utcnow() - timedelta(days=180)
        deleted_count = db.query(TradeJournal).filter(TradeJournal.timestamp < journal_cutoff).delete(synchronize_session=False)
        if deleted_count > 0:
            log.info("🧹 [Cleanup] Đã dọn dẹp %d bản ghi nhật ký giao dịch cũ (>180 ngày).", deleted_count)

        db.commit()
    except Exception as e:
        log.warning("⚠️ _cleanup_inactive_users error: %s", e)
        db.rollback()
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════
# SYNC POSITIONS & BALANCE
# ══════════════════════════════════════════════════════════════════
def sync_bingx_positions():
    global LIVE_POSITIONS
    _bx_cache: dict = {}
    cleanup_counter = 0

    while True:
        try:
            db           = SessionLocal()
            active_users = db.query(User).filter(User.is_active == True).all()

            current_all: list = []
            current_map: dict = {}
            fetch_failed_users: set = set()

            for user in active_users:
                tid = user.telegram_id
                try:
                    if tid not in _bx_cache:
                        _bx_cache[tid] = get_bx(user)
                    bx = _bx_cache[tid]

                    balance = bx.get_balance()
                    if balance > 0 and abs(balance - (user.capital or 0)) / max(user.capital or 1, 1) > 0.02:
                        _update_user_balance_and_tier(user, balance, db)

                    if user.capital < MIN_CAPITAL_TO_TRADE:
                        continue

                    positions = bx.get_open_positions()
                    triggers  = bx.get_trigger_orders()

                    if positions is None:
                        fetch_failed_users.add(tid)
                        log.warning("⚠️ Không lấy được danh sách vị thế của user %s từ BingX -> Bỏ qua sync user này.", tid)
                        continue

                    if not isinstance(positions, list):
                        positions = []
                    if not isinstance(triggers, dict):
                        triggers = {}

                    for p in positions:
                        if not isinstance(p, dict):
                            continue
                        sym  = p.get("symbol", "")
                        if not sym:
                            continue
                        cur  = bx.get_latest_price(sym) or p.get("entry", 0)

                        # Evaluate for reversal / breakeven / trailing SL
                        eval_res = evaluate_reversal_for_position(user, p, cur, db, triggers=triggers) or {}
                        eval_act = eval_res.get("action")
                        if eval_act == "CLOSED":
                            # Vị thế đã bị đóng — không self-heal và không đưa lại vào current_map
                            continue
                        if eval_act == "REVERSED":
                            # Vị thế cũ vừa đóng & đảo sang chiều mới -> đồng bộ lại vị thế mới trên BingX
                            new_dir = eval_res.get("new_direction")
                            fresh_list = bx.get_open_positions(sym)
                            fresh_pos = None
                            if isinstance(fresh_list, list):
                                fresh_pos = next(
                                    (fp for fp in fresh_list if fp.get("symbol") == _clean_sym(sym) and fp.get("direction") == new_dir),
                                    None,
                                )
                            if not fresh_pos:
                                continue
                            p = fresh_pos
                            fresh_trigs = bx.get_trigger_orders()
                            if isinstance(fresh_trigs, dict):
                                triggers = fresh_trigs
                            cur = bx.get_latest_price(sym) or p.get("entry", cur)

                        trig = (triggers.get(sym, {}) or triggers.get(_clean_sym(sym), {})) if isinstance(triggers, dict) else {}
                        if not isinstance(trig, dict):
                            trig = {}

                        # [SELF-HEAL SL/TP] Nếu vị thế đang mở trên BingX bị thiếu SL hoặc TP -> Tự động đặt bù ngay!
                        pos_dir = p.get("direction", "LONG")
                        pos_entry = float(p.get("entry", 0) or 0)
                        pos_qty = float(p.get("qty", 0) or 0)
                        heal_cache = _get_active_redis()
                        ep_sl = pos_entry * (0.98 if pos_dir == "LONG" else 1.02)
                        ep_tp1 = pos_entry * (1.025 if pos_dir == "LONG" else 0.975)
                        ep_tp2 = pos_entry * (1.04 if pos_dir == "LONG" else 0.96)
                        ep_lvls = None
                        ep_obj = _get_entry_plan_from_cache(heal_cache, tid, sym, pos_dir)
                        if ep_obj:
                            ep_sl = float(ep_obj.get("sl") or ep_sl)
                            ep_tp1 = float(ep_obj.get("tp1") or ep_tp1)
                            ep_tp2 = float(ep_obj.get("tp2") or ep_tp2)
                            ep_lvls = ep_obj.get("tp_levels")

                        if triggers is not None and pos_entry > 0 and pos_qty > 0 and (not trig.get("sl") or not trig.get("tp2")):
                            heal_key = f"SL_HEAL_COOLDOWN:{tid}:{sym}:{pos_dir}"
                            already_healed = False
                            if heal_cache:
                                try:
                                    already_healed = bool(heal_cache.get(heal_key))
                                except Exception:
                                    pass
                            if not already_healed:
                                log.warning(
                                    "🩹 [SELF-HEAL SL/TP] %s (%s %s): Phát hiện thiếu trên BingX (SL=%s, TP=%s) -> Tự động đặt bù!",
                                    tid, sym, pos_dir, bool(trig.get("sl")), bool(trig.get("tp2")),
                                )
                                heal_res = bx.ensure_sl_tp(sym, pos_dir, pos_qty, ep_sl, ep_tp2, tp_levels=ep_lvls)
                                if heal_cache:
                                    try:
                                        ttl = 180 if (heal_res.get("sl_placed", True) and heal_res.get("tp_placed", True)) else 30
                                        heal_cache.setex(heal_key, ttl, "1")
                                    except Exception:
                                        pass
                                if heal_res.get("healed"):
                                    refreshed = bx.get_trigger_orders()
                                    if isinstance(refreshed, dict):
                                        trig = refreshed.get(sym, {}) or refreshed.get(sym.replace("-", ""), {}) or trig

                        sl   = trig.get("sl", ep_sl)
                        tp2  = trig.get("tp2", ep_tp2)
                        tp1  = ep_tp1
                        pnl  = p.get("pnl", 0)
                        margin = user.capital * (user.max_risk_pct / 100)
                        pct  = round(pnl / margin * 100, 2) if margin > 0 else 0

                        pos_key = f"{tid}_{sym}_{p.get('direction', 'LONG')}"
                        current_map[pos_key] = {
                            "direction": p.get("direction", "LONG"), "pct": pct,
                            "qty": p.get("qty", 0), "user_id": tid,
                            "entry": p.get("entry", 0), "sl": sl, "tp2": tp2,
                            "leverage": 50 if _is_precious_metal(sym) else max(10, int(user.leverage or 10))
                        }
                        current_all.append({
                            "user_id": tid, "tier": user.tier, "capital": user.capital,
                            "symbol": sym, "direction": p.get("direction", "LONG"), "entry": p.get("entry", 0),
                            "current_price": cur, "pnl": pnl, "pnl_pct": pct,
                            "qty": p.get("qty", 0), "sl": sl, "tp1": tp1, "tp2": tp2,
                        })
                except Exception as e:
                    log.warning("Sync user %s error: %s", tid, e)
                    fetch_failed_users.add(tid)
                    _bx_cache.pop(tid, None)

            prev_map = getattr(sync_bingx_positions, "_prev", {})
            for k, v in prev_map.items():
                user_id = v.get("user_id")
                if user_id in fetch_failed_users:
                    current_map[k] = v
                    continue

                if k not in current_map:
                    parts = k.split("_", 2)
                    if len(parts) == 3:
                        user_id, symbol, direction = parts[0], parts[1], parts[2]
                        pnl_pct = v.get("pct", 0)
                        qty = v.get("qty", 0)
                        entry = v.get("entry", 0)
                        sl = v.get("sl", 0)
                        tp2 = v.get("tp2", 0)
                        lev = v.get("leverage", 50 if _is_precious_metal(symbol) else 10)

                        # Check if this close was already notified by reversal (kiểm tra cả dạng có dấu '-' lẫn không dấu '-')
                        was_reversal = False
                        cache = _get_active_redis()
                        if cache:
                            try:
                                for sv in _sym_variants(symbol):
                                    rev_key = f"REVERSAL_CLOSED:{user_id}:{sv}:{direction}"
                                    if cache.get(rev_key):
                                        was_reversal = True
                                        cache.delete(rev_key)
                            except Exception:
                                pass

                        if not was_reversal:
                            cur_px = 0
                            try:
                                bx_inst = _bx_cache.get(user_id)
                                if bx_inst:
                                    cur_px = bx_inst.get_latest_price(symbol)
                            except Exception:
                                pass

                            is_sl_hit = False
                            is_tp_hit = False

                            if direction == "LONG":
                                if sl > 0 and cur_px > 0 and cur_px <= sl * 1.003:
                                    is_sl_hit = True
                                elif sl > 0 and (cur_px <= 0 or cur_px < entry) and pnl_pct < 0:
                                    is_sl_hit = True
                                elif tp2 > 0 and cur_px >= tp2 * 0.997:
                                    is_tp_hit = True
                            else: # SHORT
                                if sl > 0 and cur_px > 0 and cur_px >= sl * 0.997:
                                    is_sl_hit = True
                                elif sl > 0 and (cur_px <= 0 or cur_px > entry) and pnl_pct < 0:
                                    is_sl_hit = True
                                elif tp2 > 0 and cur_px <= tp2 * 1.003:
                                    is_tp_hit = True

                            if is_sl_hit:
                                outcome_emoji = "🛑"
                                outcome_text = "CẮT LỖ (STOP LOSS HIT)"
                                if entry > 0 and sl > 0:
                                    sl_price_pct = abs(entry - sl) / entry
                                    calc_sl_pct = -round(sl_price_pct * lev * 100, 2)
                                    pnl_pct = min(pnl_pct, calc_sl_pct) if calc_sl_pct < 0 else pnl_pct
                            elif is_tp_hit:
                                outcome_emoji = "🏆"
                                outcome_text = "CHỐT LỜI THÀNH CÔNG (TP2)"
                                if entry > 0 and tp2 > 0:
                                    tp_price_pct = abs(tp2 - entry) / entry
                                    calc_tp_pct = round(tp_price_pct * lev * 100, 2)
                                    pnl_pct = max(pnl_pct, calc_tp_pct)
                            elif pnl_pct > 0.1:
                                outcome_emoji = "🏆"
                                outcome_text = "CHỐT LỜI THÀNH CÔNG"
                            elif pnl_pct < -0.1:
                                outcome_emoji = "🛑"
                                outcome_text = "CẮT LỖ (SL / ĐÓNG THỦ CÔNG)"
                            else:
                                outcome_emoji = "🛡️"
                                outcome_text = "HOÀ VỐN / ĐÓNG THỦ CÔNG"

                            pnl_usd = 0
                            try:
                                user_db = db.query(User).filter(User.telegram_id == user_id).first()
                                if user_db:
                                    pnl_usd = user_db.capital * (user_db.max_risk_pct / 100) * pnl_pct / 100
                            except Exception as e:
                                log.error("Error getting user_db for closing notification: %s", e)

                            close_msg = (
                                f"{outcome_emoji} <b>VỊ THẾ ĐÃ ĐÓNG: {symbol}</b>\n\n"
                                f"📈 Hướng: <b>{direction}</b>\n"
                                f"💰 Khối lượng: {qty:.4f} {symbol}\n"
                                f"📊 PnL: <b>{pnl_pct:+.2f}% ({'+' if pnl_usd >= 0 else ''}${pnl_usd:.2f})</b>\n"
                                f"🛑 SL cũ: <code>${sl:.4f}</code> | 🏆 Target: <code>${tp2:.4f}</code>\n"
                                f"🎯 Kết quả: <b>{outcome_text}</b>"
                            )
                            _tg_send(REGISTER_TOKEN, user_id, close_msg)
                            if ADMIN_CHAT_ID and str(user_id) != str(ADMIN_CHAT_ID):
                                _tg_send(REPORT_TOKEN or REGISTER_TOKEN, ADMIN_CHAT_ID, f"👤 User <code>{user_id}</code>:\n" + close_msg)

                        _save_journal(user_id, symbol, direction, pnl_pct, qty)

            sync_bingx_positions._prev = current_map

            with _POS_LOCK:
                LIVE_POSITIONS = current_all

            db.close()

        except Exception as e:
            log.error("sync_bingx_positions: %s", e)

        cleanup_counter += 1
        if cleanup_counter >= 2880:
            cleanup_counter = 0
            threading.Thread(target=_cleanup_inactive_users, daemon=True).start()

        time.sleep(30)


def _save_journal(user_id: str, symbol: str, direction: str, pnl_pct: float, qty: float):
    db = SessionLocal()
    try:
            user    = db.query(User).filter(User.telegram_id == user_id).first()
            tier    = user.tier if user else "TIER1"
            capital = user.capital if user else 0
            pnl_usd = capital * (user.max_risk_pct / 100) * pnl_pct / 100 if user else 0

            result = "WIN" if pnl_pct > 0 else "LOSS"
            lesson = (f"Lệnh {direction} {result} {round(abs(pnl_pct),2)}%. "
                      + ("Tuân thủ tốt cấu trúc Range & Deviation TraderSZ." if result == "WIN"
                         else "Kiểm tra lại Dealing Range (PDH/PDL/Monday), vùng 50% EQ và nến xác nhận SFP/Return-to-Range trước khi vào tiếp."))
            outcome = "TP" if pnl_pct > 0 else "SL"

            # [NEW v6.5] Đính kèm feature snapshot lúc vào lệnh (nếu còn trong Redis)
            # để nuôi dữ liệu cho Meta-labeling filter (worker/meta_labeling.py).
            # KHÔNG xoá key sau khi đọc: lệnh MỚI cùng symbol+direction sẽ tự ghi
            # đè key này lúc vào lệnh tiếp theo, nên không cần dọn thủ công; TTL 7
            # ngày lo phần dọn rác nếu vì lý do gì đó không có lệnh mới.
            entry_features_json = None
            cache = _get_active_redis()
            if cache:
                try:
                    raw = cache.get(f"ENTRY_FEATURES_{user_id}_{symbol}_{direction}")
                    if raw:
                        entry_features_json = raw.decode() if isinstance(raw, bytes) else raw
                except Exception:
                    pass

            db.add(TradeJournal(
                symbol=symbol, user_id=user_id, tier=tier, direction=direction,
                outcome=outcome, pnl_pct=pnl_pct, pnl_usd=pnl_usd,
                context=f"{symbol} {direction} @ {datetime.now().strftime('%d/%m %H:%M')}",
                lesson=lesson, entry_features=entry_features_json))
            if user:
                user.total_pnl = (user.total_pnl or 0) + pnl_usd

            old = (db.query(TradeJournal).filter(TradeJournal.user_id == user_id)
                   .order_by(TradeJournal.timestamp.desc()).all())
            if len(old) > 50:
                for r in old[50:]:
                    db.delete(r)
            db.commit()

            if cache:
                try:
                    cache.delete(f"TP1_DONE:{user_id}:{symbol}:{direction}")
                except Exception:
                    pass

    except Exception as e:
        db.rollback()
        log.error("_save_journal: %s", e)
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════
# TRADE WORKER
# ══════════════════════════════════════════════════════════════════
async def _trade_worker_async():
    log.info("Trade Worker khoi dong...")
    while True:
        r = None
        use_local = False
        try:
            r = await aioredis.from_url(
                REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=10,
                socket_keepalive=True,
                retry_on_timeout=True
            )
            await r.ping()
            log.info("Worker Redis Cloud OK")
        except Exception as e:
            use_local = True
            log.warning("⚠️ Worker dùng LocalStore (0%% RAM & 0%% Request Quota): %s", e)

        try:
            while True:
                msg = None
                if not use_local and r:
                    try:
                        msg = await r.blpop("TRADE_SIGNALS", timeout=2)
                    except Exception as e:
                        log.warning("⚠️ Redis error during blpop, fallback sang LocalStore: %s", e)
                        use_local = True

                if use_local:
                    msg = await local_store.blpop_async("TRADE_SIGNALS", timeout=2)

                if not msg:
                    await asyncio.sleep(0.1)
                    continue

                _, data_str = msg
                try:
                    signal = json.loads(data_str) if isinstance(data_str, str) else data_str
                    data_str = json.dumps(signal) if not isinstance(data_str, str) else data_str
                except Exception:
                    continue

                if not use_local and r:
                    try:
                        await r.lpush("WEB_SIGNALS", data_str)
                        await r.lpush("WEB_SIGNALS_RECORD", data_str)
                        await r.ltrim("WEB_SIGNALS", 0, 29)
                        await r.ltrim("WEB_SIGNALS_RECORD", 0, 99)
                    except Exception:
                        pass

                # Luôn sync vào LocalStore cho Web UI
                local_store.lpush("WEB_SIGNALS", data_str)
                local_store.lpush("WEB_SIGNALS_RECORD", data_str)
                local_store.ltrim("WEB_SIGNALS", 0, 29)
                local_store.ltrim("WEB_SIGNALS_RECORD", 0, 99)

                if not BOT_GLOBAL_AUTO or BOT_KILL_SWITCH:
                    log.info(f"Skip trade: GLOBAL_AUTO={BOT_GLOBAL_AUTO}, KILL_SWITCH={BOT_KILL_SWITCH}")
                    continue

                final = signal.get("final", "WAIT")
                conf  = float(signal.get("confidence", 0))
                if final == "WAIT":
                    continue

                db = SessionLocal()
                try:
                    log.info(f"Processing signal {signal.get('symbol')} for users...")
                    users = (db.query(User)
                             .filter(User.is_active == True, User.auto_trade == True,
                                     User.capital >= MIN_CAPITAL_TO_TRADE).all())
                except Exception:
                    users = []
                finally:
                    db.close()

                eligible = [u for u in users if conf >= (u.min_confidence or 68)]
                if not eligible:
                    log.info("Khong co user du dieu kien cho signal %.1f%%", conf)
                    continue

                tasks = [asyncio.to_thread(_execute_for_user, u, signal) for u in eligible]
                await asyncio.gather(*tasks, return_exceptions=True)

        except Exception as e:
            log.error("Worker error: %s", e)
            await asyncio.sleep(5)
        finally:
            if r:
                try:
                    await r.aclose()
                except Exception:
                    pass


def _execute_for_user(user: User, signal: dict):
    sym       = signal.get("symbol", "")
    direction = signal.get("final", "WAIT")
    if not sym or direction == "WAIT":
        return

    with _get_user_sym_lock(user.telegram_id, sym):
      try:
        pending_key = f"PENDING:{user.telegram_id}:{sym}:{direction}"
        cache = _get_active_redis()
        if cache:
            try:
                if any(cache.get(f"PENDING:{user.telegram_id}:{sv}:{direction}") for sv in _sym_variants(sym)):
                    log.info("Cooldown %s %s (%s) - skip", user.telegram_id, sym, direction)
                    return
            except Exception:
                pass

        bx_live = get_bx(user)
        clean_sym = _clean_sym(sym)
        try:
            live = bx_live.get_open_positions()
        except Exception as e:
            log.warning("BingX realtime check: %s", e)
            live = None

        if live is None:
            with _POS_LOCK:
                already_same = [
                    p for p in LIVE_POSITIONS
                    if str(p.get("user_id")) == user.telegram_id
                    and p.get("symbol", "").replace("-", "").upper() == clean_sym
                ]
            if already_same:
                log.info("Da co vi the %s (user %s, offline check) - bo qua", sym, user.telegram_id)
                return
            live = []

        same_sym_positions = [
            p for p in live
            if isinstance(p, dict) and p.get("symbol", "").replace("-", "").upper() == clean_sym
        ]

        # Nếu đã có vị thế CÙNG CHIỀU (và không có vị thế ngược chiều) -> bỏ qua, không nhồi thêm lệnh
        opposite_positions = [p for p in same_sym_positions if p.get("direction") != direction]
        if any(p.get("direction") == direction for p in same_sym_positions) and not opposite_positions:
            log.info("BingX xac nhan da co vi the cung chieu %s (%s) - bo qua", sym, direction)
            return

        # Nếu đang có vị thế NGƯỢC CHIỀU -> ĐÓNG TOÀN BỘ vị thế cũ & đi theo tín hiệu đảo chiều mới
        reversed_closed_sym = False
        if opposite_positions:
            bx_live.cancel_all_orders(sym)
            for old_pos in opposite_positions:
                old_dir = old_pos.get("direction", "LONG")
                old_entry = float(old_pos.get("entry", 0) or 0)
                old_qty = float(old_pos.get("qty", 0) or 0)
                old_pnl = float(old_pos.get("pnl", 0) or 0)
                if old_qty <= 0:
                    continue
                close_res = bx_live.close_position(sym, old_qty, old_dir)
                if not close_res.get("ok"):
                    log.error("❌ Đóng vị thế %s %s để đảo chiều thất bại: %s", sym, old_dir, close_res.get("msg"))
                    return
                bx_live.wait_position_closed(sym, old_dir, timeout=2.5)
                reversed_closed_sym = True
                old_lev = 50 if _is_precious_metal(sym) else max(10, int(user.leverage or 10))
                cur_px = bx_live.get_latest_price(sym) or float(signal.get("plan", {}).get("entry", 0) or old_entry)
                if old_entry > 0:
                    old_roe = ((cur_px - old_entry) / old_entry) * 100 * old_lev if old_dir == "LONG" else ((old_entry - cur_px) / old_entry) * 100 * old_lev
                else:
                    old_roe = 0.0
                if cache:
                    try:
                        for sv in _sym_variants(sym):
                            cache.setex(f"REVERSAL_CLOSED:{user.telegram_id}:{sv}:{old_dir}", 120, "1")
                        _clear_position_caches(cache, user.telegram_id, sym, old_dir)
                    except Exception:
                        pass
                _save_journal(user.telegram_id, sym, old_dir, old_roe, old_qty)
                _tg_send(
                    REGISTER_TOKEN, user.telegram_id,
                    f"🔄 <b>ĐÓNG TOÀN BỘ & ĐẢO CHIỀU THEO TÍN HIỆU MỚI: {sym}</b>\n\n"
                    f"📊 Đóng vị thế cũ: {old_dir} @ ${old_entry:.4f} (PnL ~${old_pnl:+.2f})\n"
                    f"➡️ Chuẩn bị vào lệnh mới hướng <b>{direction}</b> (Conf: {signal.get('confidence', 0):.1f}%)"
                )

        with _POS_LOCK:
            total_pos = sum(
                1 for p in LIVE_POSITIONS
                if str(p.get("user_id")) == user.telegram_id
                and (not reversed_closed_sym or p.get("symbol", "").replace("-", "").upper() != clean_sym)
            )
        if total_pos >= (user.max_positions or 7):
            log.info("Max positions %d da dat (user %s)", user.max_positions or 7, user.telegram_id)
            return

        entry = float(signal["plan"]["entry"])
        sl    = float(signal["plan"]["sl"])
        tp1   = float(signal["plan"]["tp1"])
        tp2   = float(signal["plan"].get("tp2", 0))
        tp_levels = signal.get("plan", {}).get("tp_levels", [])

        if tp2 <= 0:
            if direction == "LONG":
                tp2 = round(tp1 + abs(tp1 - entry), 4)
            else:
                tp2 = round(tp1 - abs(tp1 - entry), 4)

        sl_pct = abs(entry - sl) / entry
        if sl_pct < 0.001:
            log.warning("SL qua gan entry (%.4f%%) - bo qua", sl_pct * 100)
            return

        # [NEW v6.11] Tính hệ số co khối lượng theo tin tức vĩ mô / Fed Rate / Monday Range Rule
        size_mult_combined = 1.0
        htf_meta = (signal.get("traderssz", {}) or {}).get("htf_opens_and_ranges", {}) or {}
        monday_mult = float(htf_meta.get("monday_size_mult", 1.0) or 1.0)
        if monday_mult < 1.0:
            size_mult_combined *= monday_mult
            log.info("📅 [MONDAY RANGE RULE] %s: Đang tạo biên Thứ Hai -> điều chỉnh size x%.2f", sym, monday_mult)

        news_risk = signal.get("news_risk", {}) or {}
        if news_risk.get("active"):
            size_m = max(0.75, float(news_risk.get("size_mult", 1.0)))
            size_mult_combined *= size_m
            log.warning("📰 %s: Đang trong vùng ảnh hưởng tin '%s' -> điều chỉnh khối lượng x%.2f (giữ tối thiểu 75%% size).",
                        sym, news_risk.get("event"), size_m)

        # [NEW v6.15] ĐIỀU CHỈNH KHỐI LƯỢNG THEO KỲ VỌNG LÃI SUẤT FED (FED RATE POLICY)
        try:
            from analyzer.macro_analyzer import get_macro_market_context
            m_ctx = get_macro_market_context()
            fed_size = float(m_ctx.get("fed_size_mult", 1.0))
            if fed_size != 1.0:
                size_mult_combined *= fed_size
                log.info("💵 [FED RATE SIZING] %s: Hệ số vĩ mô x%.2f (Fed: %s | Cut Prob: %.1f%%)",
                         sym, fed_size, m_ctx.get("fed_stance"), m_ctx.get("fed_rate_cut_prob", 50))
        except Exception as ex_m_size:
            log.debug("Lỗi tính Fed size: %s", ex_m_size)

        # ══════════════════════════════════════════════════════════
        # [M5 SCALPING] QUẢN LÝ VỐN, CHỐNG CHÁY & MỤC TIÊU NGÀY ($20 - $50 USD)
        # ══════════════════════════════════════════════════════════
        today_pnl = 0.0
        try:
            tj_db = SessionLocal()
            since_24h = datetime.utcnow() - timedelta(hours=24)
            rows_24h = tj_db.query(TradeJournal).filter(
                TradeJournal.user_id == str(user.telegram_id),
                TradeJournal.timestamp >= since_24h
            ).all()
            today_pnl = sum(r.pnl_usd or 0.0 for r in rows_24h)
            tj_db.close()
        except Exception as ex_pnl:
            log.debug("Lỗi tính PnL 24h: %s", ex_pnl)

        # 1. Khóa lợi nhuận khi đạt mục tiêu ngày ($20 - $50 USD)
        target_daily = float(os.getenv("DAILY_PROFIT_TARGET_USD", "30.0"))
        if today_pnl >= target_daily:
            log.info("🏆 [TARGET LOCK] User %s đã đạt mục tiêu ngày (+%.2f USD >= $%.2f). Tự động KHÓA LỢI NHUẬN để bảo toàn thành quả!",
                     user.telegram_id, today_pnl, target_daily)
            return

        # 3. ĐÒN BẨY LINH ĐỘNG (10x - 20x; RIÊNG VÀNG/BẠC x50 CHO 2 CHIỀU LONG/SHORT)
        effective_leverage, lev_reason, lev_meta = calculate_dynamic_leverage(
            signal=signal,
            user_base_leverage=user.leverage or 10,
            sl_pct=sl_pct
        )
        log.info("⚡ [DYNAMIC LEVERAGE] User %s | %s %s: Lev=%dx (%s)",
                 user.telegram_id, direction, sym, effective_leverage, lev_reason)

        # 4. TỈ LỆ VỐN KÝ QUỸ MỖI LỆNH ($20 - $30 USD, tối đa 30% vốn)
        qty, req_margin, risk_amt = _calc_order_sizing(
            capital=user.capital,
            max_risk_pct=user.max_risk_pct,
            entry=entry,
            sl_pct=sl_pct,
            leverage=effective_leverage,
            confidence=float(signal.get("confidence", 75.0)),
            size_mult=size_mult_combined,
        )
        log.info("💰 [ORDER SIZING] User %s | %s %s: Margin=$%.2f ($20-$30) | Qty=%.4f | Lev=%dx",
                 user.telegram_id, direction, sym, req_margin, qty, effective_leverage)

        if qty <= 0:
            return

        entry_features = {
            "symbol": sym, "direction": direction,
            "confidence": signal.get("confidence", 70),
            "bayes_ev": signal.get("bayes_ev", {}),
            "rr_ratio": signal.get("rr_ratio", 1.5),
            "hmm": signal.get("hmm", {}),
            "oi_signal": signal.get("oi_signal", "N/A"),
            "funding": signal.get("funding", 0),
        }

        # Vào lệnh trực tiếp theo Price Action TraderSZ — bỏ AI Meta-Filter phản biện/chặn lệnh
        bx   = bx_live
        side = "BUY" if direction == "LONG" else "SELL"
        bx.set_leverage(sym, leverage=effective_leverage, side="ALL")
        bx.cancel_all_orders(sym)
        sig_p_win = signal.get("bayes_ev", {}).get("p_win", signal.get("confidence", 50)) / 100
        sig_rr    = signal.get("rr_ratio", 1.5)
        if not tp_levels:
            tp_levels = [
                {"level": 1, "price": tp1, "close_pct": 0.50},
                {"level": 2, "price": tp2, "close_pct": 0.50},
            ]
        res = bx.place_order(sym, side, qty, sl, tp2, leverage=effective_leverage, p_win=sig_p_win, rr_ratio=sig_rr, tp_levels=tp_levels)

        if res.get("ok"):
            actual_qty = float(res.get("qty", qty))
            actual_margin = round((actual_qty * entry) / max(1, effective_leverage), 2)
            actual_sl  = float(res.get("sl_price") or sl)
            actual_tp  = float(res.get("tp_price") or tp2)
            sl_ok      = res.get("sl_ok", False)
            tp_ok      = res.get("tp_ok", False)

            cache = _get_active_redis()
            if cache:
                try:
                    for sv in _sym_variants(sym):
                        cache.setex(f"PENDING:{user.telegram_id}:{sv}:{direction}", 90, "1")
                        cache.setex(
                            f"ENTRY_FEATURES_{user.telegram_id}_{sv}_{direction}",
                            7 * 86400, json.dumps(entry_features)
                        )
                        sig_setup = (signal.get("traderssz", {}) or {}).get("setup", {}) or {}
                        cache.setex(
                            f"ENTRY_PLAN_{user.telegram_id}_{sv}_{direction}",
                            7 * 86400,
                            json.dumps({
                                "sl": actual_sl, "tp1": tp1, "tp2": actual_tp,
                                "tp_levels": tp_levels,
                                "setup_type": sig_setup.get("setup_type", ""),
                                "swept_level": float(sig_setup.get("swept_level", 0) or 0),
                            })
                        )
                        cache.setex(
                            f"ORIGINAL_QTY_{user.telegram_id}_{sv}_{direction}",
                            7 * 86400, str(actual_qty)
                        )
                except Exception:
                    pass

            log.info("OK %s: %s %s qty=%.4f margin=$%.2f lev=%dx sl_ok=%s tp_ok=%s (%s)",
                     user.telegram_id, direction, sym, actual_qty, actual_margin, effective_leverage, sl_ok, tp_ok, lev_reason)
            news_notice = ""
            if news_risk.get("active"):
                news_notice = f"\n⚠️ <i>(Đã giảm {int((1 - news_risk.get('size_mult', 0.5)) * 100)}% vốn do tin tức: {news_risk.get('event')})</i>"

            lev_tag = f"{effective_leverage}x"
            if effective_leverage >= 50:
                lev_tag += " 🥇 (Vàng/Bạc x50 2 chiều LONG/SHORT)"
            elif effective_leverage >= 18:
                lev_tag += f" 🚀 (Trend Boost {effective_leverage}x)"
            elif effective_leverage > 10:
                lev_tag += f" 📈 (Trend {effective_leverage}x)"
            else:
                lev_tag += " 🛡️ (Range Base 10x)"

            sl_badge = "✅ Đã đặt sàn" if sl_ok else "⚠️ Chờ Self-Heal"
            tp_badge = "✅ Đã đặt sàn" if tp_ok else "⚠️ Chờ Self-Heal"

            _tg_send(
                REGISTER_TOKEN, user.telegram_id,
                f"🚨 <b>LỆNH MỚI: {sym}</b>\n"
                f"📈 {direction} | Conf: {signal.get('confidence',0):.1f}%\n"
                f"⚡ Đòn bẩy: <b>{lev_tag}</b>\n"
                f"💰 Qty: {actual_qty:.4f} | Margin: ~${actual_margin:.2f}{news_notice}\n"
                f"🛑 SL: <code>${actual_sl:.4f}</code> ({sl_badge}) | Risk: ${risk_amt:.2f}\n"
                f"🎯 TP1: <code>${tp1:.4f}</code> → chốt 50% + SL → Entry\n"
                f"🏆 TP2: <code>${actual_tp:.4f}</code> ({tp_badge}) → đích 50% còn lại")
        else:
            # [FIX v6.6] TRƯỚC: "BingX loi %s: %s" với %s đầu = user.telegram_id
            # -> trông giống mã lỗi BingX nhưng thực ra là ID người dùng, gây hiểu
            # nhầm khi debug (số trong log tưởng là error code, thực ra là Telegram ID).
            log.error("❌ Đặt lệnh %s thất bại cho user %s: %s", sym, user.telegram_id, res.get("msg"))
            if res.get("margin_mode_blocked"):
                cache = _get_active_redis()
                notified = False
                block_key = f"MARGIN_BLOCK_{user.telegram_id}_{sym}"
                if cache:
                    try:
                        cache.setex(pending_key, 1800, "1")
                        notified = bool(cache.get(block_key))
                        if not notified:
                            cache.setex(block_key, 3600, "1")
                    except Exception:
                        pass
                if not notified:
                    _tg_send(
                        REGISTER_TOKEN, user.telegram_id,
                        f"⚠️ <b>KHÔNG ĐẶT ĐƯỢC LỆNH {sym} ({direction})</b>\n\n"
                        f"Sàn BingX từ chối: <code>cross multi position mode not support current order source</code>.\n\n"
                        f"📌 <b>Cách xử lý trong 5 giây trên App BingX:</b>\n"
                        f"1. Mở BingX Futures → chọn cặp <b>{sym}</b>\n"
                        f"2. Chuyển chế độ ký quỹ từ <b>Cross</b> sang <b>Isolated (Cô lập)</b> hoặc đổi từ <b>Đa vị thế (Separate)</b> sang <b>Hợp nhất (Merged)</b>."
                    )

      except Exception as e:
          log.error("_execute_for_user %s: %s", user.telegram_id, e)
def run_trade_worker():
    asyncio.run(_trade_worker_async())


def run_signal_bot():
    try:
        SignalBot().start()
    except Exception as e:
        log.error("SignalBot crash: %s", e)


def _register_telegram_webhook():
    if REGISTER_TOKEN and RENDER_URL:
        try:
            url = f"{TG_BASE}/bot{REGISTER_TOKEN}/setWebhook"
            webhook_url = f"{RENDER_URL}/telegram/webhook"
            r = _req.get(url, params={"url": webhook_url}, timeout=10)
            if r.status_code == 200:
                log.info("✅ Auto register Telegram Webhook success: %s", webhook_url)
            else:
                log.warning("⚠️ Failed to register Telegram Webhook: %s", r.text)
        except Exception as e:
            log.warning("⚠️ Error registering Telegram Webhook: %s", e)


TRADERSZ_CLEAN_SLATE_TAG = "TRADERSZ_CLEAN_SLATE_V1"


def _clear_history_cache_keys(user_id: Optional[str] = None) -> int:
    """Xoá các khoá cooldown, feature snapshot, scale-out và tín hiệu cũ trong Redis/LocalStore."""
    cleared = 0
    uid = str(user_id).strip() if user_id else None
    if uid:
        prefixes = (
            f"PENDING:{uid}:",
            f"REVERSAL_CLOSED:{uid}:",
            f"ENTRY_FEATURES_{uid}_",
            f"SCALE_OUT_{uid}_",
            f"ORIGINAL_QTY_{uid}_",
            f"TP1_DONE:{uid}:",
            f"TRAILING_SL_{uid}_",
            f"BREAKEVEN_{uid}_",
            "SL_COOLDOWN_",
            "PEAK_PRICE_",
        )
        exact_keys: tuple[str, ...] = ()
    else:
        prefixes = (
            "PENDING:",
            "REVERSAL_CLOSED:",
            "SL_COOLDOWN_",
            "SL_HEAL_COOLDOWN:",
            "ENTRY_FEATURES_",
            "ENTRY_PLAN_",
            "SCALE_OUT_",
            "PEAK_PRICE_",
            "ORIGINAL_QTY_",
            "TP1_DONE:",
            "TRAILING_SL_",
            "BREAKEVEN_",
        )
        exact_keys = ("TRADE_SIGNALS", "WEB_SIGNALS", "WEB_SIGNALS_RECORD")

    try:
        cleared += local_store.delete_by_prefix(prefixes)
        if exact_keys:
            cleared += local_store.delete(*exact_keys)
    except Exception as e:
        log.warning("Lỗi dọn LocalStore khi reset lịch sử: %s", e)

    if redis_client:
        try:
            for pfx in prefixes:
                matched = redis_client.keys(f"{pfx}*")
                if matched:
                    cleared += int(redis_client.delete(*matched) or 0)
            if exact_keys:
                cleared += int(redis_client.delete(*exact_keys) or 0)
        except Exception as e:
            log.warning("Lỗi dọn Redis khi reset lịch sử: %s", e)

    try:
        if uid:
            stale_notif_keys = [k for k in list(_SENT_NOTIFICATIONS.keys()) if f"_{uid}_" in k]
            for k in stale_notif_keys:
                _SENT_NOTIFICATIONS.pop(k, None)
        else:
            _SENT_NOTIFICATIONS.clear()
            _LAST_REVERSAL_EVAL.clear()
    except Exception:
        pass

    return cleared


def reset_trade_history(user_id: Optional[str] = None, mark_tag: Optional[str] = None) -> dict:
    """
    Xoá lịch sử giao dịch cũ (TradeJournal, SignalStats, User.total_pnl, cache cooldown/features)
    và reset MetaLabelFilter để bot dễ vào lệnh sạch theo phương pháp TraderSZ.
    """
    db = SessionLocal()
    deleted_trades = 0
    deleted_stats = 0
    uid = str(user_id).strip() if user_id else None
    try:
        if uid:
            deleted_trades = (
                db.query(TradeJournal)
                .filter(TradeJournal.user_id == uid)
                .delete(synchronize_session=False)
            )
            db.query(User).filter(User.telegram_id == uid).update(
                {"total_pnl": 0.0}, synchronize_session=False
            )
        else:
            deleted_trades = db.query(TradeJournal).delete(synchronize_session=False)
            deleted_stats = db.query(SignalStats).delete(synchronize_session=False)
            db.query(User).update({"total_pnl": 0.0}, synchronize_session=False)

        if mark_tag is not None:
            cfg_row = db.query(AppConfig).first()
            if not cfg_row:
                cfg_row = AppConfig(bot_active=True, kill_switch=False, strategy_reset_tag=mark_tag)
                db.add(cfg_row)
            else:
                cfg_row.strategy_reset_tag = mark_tag

        db.commit()
    except Exception as e:
        db.rollback()
        log.error("reset_trade_history DB error: %s", e)
        return {"ok": False, "msg": f"Lỗi xoá lịch sử DB: {e}"}
    finally:
        db.close()

    cleared_cache_keys = _clear_history_cache_keys(user_id=uid)
    MetaLabelFilter.reset_cache()

    return {
        "ok": True,
        "scope": uid or "ALL",
        "deleted_trades": int(deleted_trades or 0),
        "deleted_stats": int(deleted_stats or 0),
        "cleared_cache_keys": int(cleared_cache_keys or 0),
    }


def _run_one_time_traderssz_reset():
    db = SessionLocal()
    try:
        cfg_row = db.query(AppConfig).first()
        if cfg_row and cfg_row.strategy_reset_tag == TRADERSZ_CLEAN_SLATE_TAG:
            return
    except Exception as e:
        log.warning("Kiểm tra strategy_reset_tag lỗi: %s", e)
        return
    finally:
        db.close()

    res = reset_trade_history(user_id=None, mark_tag=TRADERSZ_CLEAN_SLATE_TAG)
    if res.get("ok"):
        log.info(
            "🧹 [TRADERSZ CLEAN SLATE] Đã tự động xoá lịch sử giao dịch cũ (%d lệnh, %d stats, %d cache keys) để bắt đầu lưu lịch sử TraderSZ mới.",
            res.get("deleted_trades", 0), res.get("deleted_stats", 0), res.get("cleared_cache_keys", 0),
        )


def _ensure_max_positions_limit(min_positions: int = 7):
    """Nâng giới hạn tổng vị thế tối đa của toàn bộ user hiện có lên tối thiểu min_positions (7 lệnh)."""
    db = SessionLocal()
    try:
        users_to_update = db.query(User).filter(
            (User.max_positions == None) | (User.max_positions < min_positions)
        ).all()
        if users_to_update:
            for u in users_to_update:
                u.max_positions = min_positions
            db.commit()
            log.info("📈 Đã nâng tổng vị thế tối đa lên %d lệnh cho %d user.", min_positions, len(users_to_update))
    except Exception as e:
        db.rollback()
        log.warning("⚠️ Lỗi nâng max_positions khi khởi động: %s", e)
    finally:
        db.close()


@app.on_event("startup")
async def startup_event():
    _ensure_max_positions_limit(7)
    _run_one_time_traderssz_reset()
    threading.Thread(target=run_signal_bot,         daemon=True, name="signal-bot").start()
    threading.Thread(target=run_trade_worker,       daemon=True, name="trade-worker").start()
    threading.Thread(target=sync_bingx_positions,   daemon=True, name="pos-sync").start()
    threading.Thread(target=_tp1_monitor,           daemon=True, name="tp1-monitor").start()
    threading.Thread(target=_schedule_weekly_report, daemon=True, name="report-bot").start()
    threading.Thread(target=_register_telegram_webhook, daemon=True, name="webhook-register").start()
    threading.Thread(target=lambda: [time.sleep(600) or gc.collect() for _ in iter(int, 1)],
                     daemon=True, name="gc").start()

    log.info("Tat ca threads khoi dong")


# ══════════════════════════════════════════════════════════════════
# HEALTH CHECK
# ══════════════════════════════════════════════════════════════════
@app.api_route("/", methods=["GET", "HEAD"])
def health():
    return {
        "status": "online", 
        "version": "v6.1",
        "tiers": {t: c["label"] for t, c in TIER_CONFIG.items()}
    }





# ══════════════════════════════════════════════════════════════════
# TELEGRAM WEBHOOKS
# ══════════════════════════════════════════════════════════════════
@app.post("/telegram/webhook")
async def tg_webhook_bot2(request: Request):
    try:
        data = await request.json()
        msg  = data.get("message", {})
        if not msg:
            return {"status": "ok"}
        chat_id = str(msg["chat"]["id"])
        text    = msg.get("text", "")

        if text == "/start":
            miniapp_url = (RENDER_URL or "https://auto-trade-v6.onrender.com") + "/miniapp/connect"
            _tg_send_inline(
                REGISTER_TOKEN, chat_id,
                "👋 <b>Chào mừng đến với SignalBot v6.1!</b>\n\n"
                "Để bắt đầu copy trade tự động, kết nối BingX API của bạn.\n"
                "⚠️ Không cấp quyền <b>Rút Tiền</b> cho API Key!",
                {"inline_keyboard": [[{"text": "🔗 Kết Nối BingX API",
                                       "web_app": {"url": miniapp_url}}]]})

        elif text == "/status":
            db = SessionLocal()
            u  = db.query(User).filter(User.telegram_id == chat_id).first()
            db.close()
            if not u:
                _tg_send(REGISTER_TOKEN, chat_id, "❌ Bạn chưa đăng ký. Gõ /start để bắt đầu.")
            else:
                cfg = TIER_CONFIG.get(u.tier, TIER_CONFIG["TIER1"])
                _tg_send(
                    REGISTER_TOKEN, chat_id,
                    f"📊 <b>Tài Khoản Của Bạn</b>\n\n"
                    f"💰 Vốn: <b>${u.capital:.2f}</b>\n"
                    f"🏷 Tier: <b>{cfg['label']}</b>\n"
                    f"🎯 Min Confidence: <b>{u.min_confidence}%</b>\n"
                    f"⚡ Leverage: <b>{u.leverage}x</b> <i>(Linh động 10x-20x | Vàng/Bạc 50x)</i>\n"
                    f"📈 Risk/Lệnh: <b>{u.max_risk_pct}%</b>\n"
                    f"🔄 Auto-trade: <b>{'BẬT' if u.auto_trade else 'TẮT'}</b>\n"
                    f"📊 Total PnL: <b>${u.total_pnl:+.2f}</b>")

        elif text.startswith("/donbay") or text.startswith("/leverage"):
            parts = text.split()
            if len(parts) >= 2 and parts[1].isdigit():
                val = int(parts[1])
                if 10 <= val <= 20:
                    db = SessionLocal()
                    u = db.query(User).filter(User.telegram_id == chat_id).first()
                    if u:
                        u.leverage = val
                        db.commit()
                        _tg_send(REGISTER_TOKEN, chat_id,
                                 f"✅ Đã cập nhật đòn bẩy cơ bản của bạn lên <b>{val}x</b>!\n\n"
                                 f"🤖 <b>Cơ chế đòn bẩy mới:</b>\n"
                                 f"• <b>Vàng / Bạc (LONG &amp; SHORT)</b>: Cố định <b>50x</b> cho cả 2 chiều.\n"
                                 f"• <b>Crypto / Chứng khoán</b>: Linh động <b>10x - 20x</b> theo cấu trúc Range &amp; HTF Bias.\n"
                                 f"• <b>SFP Vùng Biên / A+ Setup</b>: Nâng lên <b>15x - 20x</b>; nền tối thiểu <b>10x</b>.")
                    db.close()
                else:
                    _tg_send(REGISTER_TOKEN, chat_id, "⚠️ Đòn bẩy hợp lệ cho Crypto/Stock từ <b>10x</b> đến <b>20x</b> (Vàng/Bạc tự động <b>50x</b>). Ví dụ: <code>/donbay 15</code>")
            else:
                _tg_send(REGISTER_TOKEN, chat_id,
                         "⚡ <b>HỆ THỐNG ĐÒN BẨY MỚI (10x - 20x | VÀNG/BẠC 50x)</b>\n\n"
                         "• <b>Vàng &amp; Bạc (XAU / XAG)</b>: Cố định <b>50x</b> cho cả 2 chiều LONG và SHORT.\n"
                         "• <b>Nền cơ bản (Crypto / Stock)</b>: Tối thiểu <b>10x</b>.\n"
                         "• <b>SFP / A+ HTF Confluence</b>: Tự động nâng lên <b>15x - 20x</b>.\n\n"
                         "👉 Cú pháp chỉnh đòn bẩy cơ bản: <code>/donbay 10</code> hoặc <code>/donbay 20</code>")

        elif text == "/dashboard":
            dash_url = (RENDER_URL or "") + f"/my-dashboard?uid={chat_id}"
            _tg_send_inline(
                REGISTER_TOKEN, chat_id, "📊 Mở Dashboard cá nhân của bạn:",
                {"inline_keyboard": [[{"text": "📊 Dashboard Của Tôi",
                                       "web_app": {"url": dash_url}}]]})

        elif text in ("/report", "/pnl", "/profit"):
            threading.Thread(target=lambda: _send_daily_report(chat_id), daemon=True).start()
            _tg_send(REGISTER_TOKEN, chat_id, "📊 Đang tổng hợp dữ liệu PnL & phân tích hiệu suất, vui lòng chờ trong giây lát...")
            
        elif text.startswith("/close "):
            symbol = text.split(" ", 1)[1].strip().upper()
            _handle_user_close(chat_id, symbol)

        elif text.lower().startswith(("/xoalichsu", "/resethistory", "/clearhistory")):
            parts = text.strip().split()
            want_all = len(parts) >= 2 and parts[1].lower() == "all"
            is_admin = (not ADMIN_CHAT_ID) or (str(chat_id) == str(ADMIN_CHAT_ID))
            target_uid = None if (want_all and is_admin) else chat_id
            res = reset_trade_history(user_id=target_uid)
            if res.get("ok"):
                scope_label = "TOÀN HỆ THỐNG" if target_uid is None else "tài khoản của bạn"
                _tg_send(
                    REGISTER_TOKEN, chat_id,
                    f"🧹 <b>ĐÃ XOÁ LỊCH SỬ GIAO DỊCH CŨ ({scope_label})!</b>\n\n"
                    f"• Đã xoá: <b>{res.get('deleted_trades', 0)}</b> lệnh cũ trong TradeJournal\n"
                    f"• Đã dọn: <b>{res.get('cleared_cache_keys', 0)}</b> khoá cooldown / cache cũ\n"
                    f"• Đã gỡ bỏ: Daily Loss Circuit Breaker, điểm phạt Winrate cũ (-22%/-32%) & Meta-Labeling cũ.\n\n"
                    f"✅ Hệ thống đã sẵn sàng ghi nhận lịch sử giao dịch mới theo chuẩn <b>TraderSZ</b> và vào lệnh thông thoáng!"
                )
            else:
                _tg_send(REGISTER_TOKEN, chat_id, f"❌ Lỗi xoá lịch sử: {res.get('msg', 'Unknown')}")

    except Exception as e:
        log.error("tg_webhook_bot2: %s", e)
    return {"status": "ok"}


def _handle_user_close(telegram_id: str, symbol: str):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        if not user:
            _tg_send(REGISTER_TOKEN, telegram_id, "❌ Tài khoản không tồn tại.")
            return

        bx = get_bx(user)

        with _POS_LOCK:
            user_pos = [p for p in LIVE_POSITIONS
                        if str(p.get("user_id")) == telegram_id
                        and p.get("symbol") == symbol]

        if not user_pos:
            try:
                live = bx.get_open_positions()
                for lp in live:
                    if lp.get("symbol") == symbol:
                        user_pos = [lp]
                        break
            except Exception as e:
                log.warning("get_open_positions for close: %s", e)

        if not user_pos:
            _tg_send(REGISTER_TOKEN, telegram_id, f"❌ Không tìm thấy lệnh <b>{symbol}</b> đang mở.")
            return

        p   = user_pos[0]
        qty = float(p.get("qty", 0))
        direction = p.get("direction", "LONG")

        if qty <= 0:
            try:
                live = bx.get_open_positions()
                for lp in live:
                    if lp.get("symbol") == symbol:
                        qty = float(lp.get("qty", 0))
                        direction = lp.get("direction", direction)
                        break
            except Exception:
                pass

        if qty <= 0:
            _tg_send(REGISTER_TOKEN, telegram_id, f"❌ Không xác định được khối lượng lệnh {symbol}.")
            return

        res = bx.close_position(symbol, qty, direction)
        if res.get("ok"):
            pnl     = float(p.get("pnl", 0))
            pct     = float(p.get("pnl_pct", 0))
            sign    = "+" if pnl >= 0 else ""
            _tg_send(REGISTER_TOKEN, telegram_id,
                     f"✅ <b>Đã đóng lệnh {symbol}!</b>\n"
                     f"📈 {direction} | Qty: {qty:.4f}\n"
                     f"💰 PnL: <b>{sign}${pnl:.2f} ({sign}{pct:.2f}%)</b>")
            cache = _get_active_redis()
            if cache:
                try:
                    cache.delete(f"TP1_DONE:{telegram_id}:{symbol}:{direction}")
                    cache.delete(f"PENDING:{telegram_id}:{symbol}")
                except Exception:
                    pass
        else:
            err_msg = res.get("msg", "Unknown error")
            _tg_send(REGISTER_TOKEN, telegram_id,
                     f"❌ Lỗi đóng lệnh {symbol}:\n<code>{err_msg}</code>")
            log.error("_handle_user_close %s %s: %s", telegram_id, symbol, err_msg)

    except Exception as e:
        _tg_send(REGISTER_TOKEN, telegram_id, f"❌ Lỗi hệ thống: {str(e)[:200]}")
        log.error("_handle_user_close exception %s: %s", telegram_id, e)
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════
# ĐĂNG KÝ USER
# ══════════════════════════════════════════════════════════════════

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class UserRegister(BaseModel):
    telegram_id: str
    api_key:     str
    api_secret:  str


@app.post("/api/users/register")
def register_user(data: UserRegister, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.telegram_id == data.telegram_id).first()

    try:
        bx      = BingXExchange(data.api_key, data.api_secret)
        capital = bx.get_balance()
        if capital <= 0:
            price = bx.get_latest_price("BTC-USDT")
            if price <= 0:
                raise HTTPException(400, "API Key không hợp lệ hoặc không kết nối được BingX")
            capital = float(os.getenv("DEFAULT_CAPITAL", "100"))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Lỗi kết nối BingX: {str(e)[:100]}")

    tier = get_tier(capital)
    if not tier:
        raise HTTPException(400, f"Số dư ${capital:.2f} thấp hơn tối thiểu ${MIN_CAPITAL_TO_TRADE:.0f}.")

    if existing:
        existing.api_key              = data.api_key
        existing.api_secret_encrypted = encrypt_api_secret(data.api_secret)
        existing.is_active            = True
        existing.is_locked            = False
        existing.auto_trade           = True
        _update_user_balance_and_tier(existing, capital, db)
        db.commit()
        cfg = TIER_CONFIG[existing.tier]
        return {"status": "updated", "tier": existing.tier, "label": cfg["label"],
                "capital": f"${capital:.2f}", "min_confidence": cfg["min_confidence"],
                "target_monthly": cfg["target_monthly"]}

    new_user = User(
        telegram_id=data.telegram_id, exchange="BINGX",
        api_key=data.api_key, api_secret_encrypted=encrypt_api_secret(data.api_secret),
        capital=round(capital, 2), auto_trade=True,
        registered_at=datetime.utcnow(), last_balance_update=datetime.utcnow())
    apply_tier(new_user, tier)
    db.add(new_user)
    db.commit()

    cfg = TIER_CONFIG[tier]
    notify_admin(
        f"🆕 <b>User mới đăng ký!</b>\n"
        f"👤 UID: <code>{data.telegram_id}</code>\n"
        f"💰 Vốn: ${capital:.2f} | {cfg['label']}\n"
        f"🎯 Conf: {cfg['min_confidence']}% | Risk: {cfg['max_risk_pct']}%")

    return {"status": "success", "tier": tier, "label": cfg["label"],
            "capital": f"${capital:.2f}", "min_confidence": cfg["min_confidence"],
            "leverage": cfg["leverage"], "target_monthly": cfg["target_monthly"],
            "msg": f"Đăng ký thành công! Tier: {cfg['label']}"}


# ══════════════════════════════════════════════════════════════════
# MARKET DEPTH API
# ══════════════════════════════════════════════════════════════════
@app.get("/api/market-depth")
def get_market_depth(symbol: str = Query(default="BTCUSDT")):
    from analyzer.fetcher import CryptoFetcher
    fetcher = CryptoFetcher()
    
    symbol = symbol.upper().strip()
    if not symbol:
        return {"success": False, "error": "Invalid symbol"}
        
    try:
        price = fetcher.price(symbol)
        if not price or price <= 0:
            k = fetcher.klines(symbol, "1m")
            if k and len(k.get("close", [])) > 0:
                price = k["close"][-1]
            else:
                price = 0.0
                
        ob = fetcher.order_book(symbol, depth=10)
        liq = fetcher.liquidation_levels(symbol, price if price > 0 else 1.0)
        
        return {
            "success": True,
            "symbol": symbol,
            "price": price,
            "orderbook": ob,
            "liquidation": liq
        }
    except Exception as e:
        log.error("API /api/market-depth error for %s: %s", symbol, e)
        return {"success": False, "error": str(e)}


# ══════════════════════════════════════════════════════════════════
# STATE API
# ══════════════════════════════════════════════════════════════════

def _compute_win_stats(db: Session, user_id: str = None, days: int = 30) -> dict:
    """[FIX v6.6] Tính win_rate, profit_factor và % ROI THỰC TẾ theo VỐN TÀI KHOẢN (Real Capital ROI)
    thay vì cộng dồn ROE đòn bẩy ảo."""
    try:
        since = datetime.utcnow() - timedelta(days=days)
        q = db.query(TradeJournal).filter(TradeJournal.timestamp >= since)
        if user_id:
            q = q.filter(TradeJournal.user_id == user_id)
        rows = q.all()
        if not rows:
            return {"win_rate": 0, "profit_factor": 0, "total_trades": 0, "total_pnl_usd": 0.0, "total_pnl_pct": 0.0, "wins": 0, "losses": 0}

        wins   = [r for r in rows if (r.pnl_usd or 0) > 0 or (r.pnl_pct or 0) > 0]
        losses = [r for r in rows if (r.pnl_usd or 0) <= 0 and (r.pnl_pct or 0) <= 0]
        win_rate = round(len(wins) / len(rows) * 100, 1)

        gross_win  = sum((r.pnl_usd or 0) for r in wins)
        gross_loss = abs(sum((r.pnl_usd or 0) for r in losses))
        total_pnl_usd = round(sum((r.pnl_usd or 0) for r in rows), 2)

        if gross_loss > 0:
            profit_factor = round(gross_win / gross_loss, 2)
        else:
            profit_factor = round(gross_win, 2) if gross_win > 0 else 0

        user = db.query(User).filter(User.telegram_id == user_id).first() if user_id else None
        equity = user.capital if user and user.capital else 0.0

        # Tính ROI % thực tế của vốn (Real Capital ROI %)
        initial_est = equity - total_pnl_usd
        if initial_est > 0:
            real_roi_pct = round((total_pnl_usd / initial_est) * 100, 2)
        elif equity > 0:
            real_roi_pct = round((total_pnl_usd / equity) * 100, 2)
        else:
            real_roi_pct = 0.0

        return {
            "win_rate": win_rate, 
            "profit_factor": profit_factor,
            "total_trades": len(rows), 
            "wins": len(wins), 
            "losses": len(losses),
            "total_pnl_usd": total_pnl_usd,
            "total_pnl_pct": real_roi_pct,
        }
    except Exception as e:
        log.warning("_compute_win_stats error: %s", e)
        return {"win_rate": 0, "profit_factor": 0, "total_trades": 0, "total_pnl_usd": 0.0, "total_pnl_pct": 0.0, "wins": 0, "losses": 0}

@app.get("/api/state")
def get_state(request: Request, db: Session = Depends(get_db), uid: str = Query(default="")):
    if uid:
        user = db.query(User).filter(User.telegram_id == uid).first()
        if not user:
            return {"error": "User not found"}
        with _POS_LOCK:
            positions = [p for p in LIVE_POSITIONS if str(p.get("user_id")) == uid]
        cfg = TIER_CONFIG.get(user.tier, TIER_CONFIG["TIER1"])
        wstats = _compute_win_stats(db, user_id=uid)
        return {
            "auto_trade": user.auto_trade, "kill_switch": BOT_KILL_SWITCH,
            "tier": user.tier, "tier_label": cfg["label"],
            "min_confidence": user.min_confidence,
            "stats": {"equity": user.capital, "total_return": wstats["total_pnl_pct"],
                     "daily_pnl_pct": 0, "total_pnl": user.total_pnl or 0,
                     "win_rate": wstats["win_rate"], "profit_factor": wstats["profit_factor"],
                     "total_trades": wstats["total_trades"]},
            "positions": positions, "signals": _get_signals(),
        }

    users = db.query(User).filter(User.is_active == True).all()
    tier_summary = {}
    for t, cfg in TIER_CONFIG.items():
        tier_users = [u for u in users if u.tier == t]
        tier_summary[t] = {
            "label": cfg["label"], "count": len(tier_users),
            "capital": sum(u.capital or 0 for u in tier_users),
            "min_confidence": cfg["min_confidence"],
        }
    with _POS_LOCK:
        positions = list(LIVE_POSITIONS)

    wstats = _compute_win_stats(db)
    return {
        "auto_trade": BOT_GLOBAL_AUTO, "kill_switch": BOT_KILL_SWITCH,
        "stats": {
            "equity": sum(u.capital or 0 for u in users), "total_users": len(users),
            "total_return": wstats["total_pnl_pct"], "daily_pnl_pct": 0,
            "win_rate": wstats["win_rate"], "profit_factor": wstats["profit_factor"],
            "total_trades": wstats["total_trades"], "drawdown_pct": 0,
        },
        "tier_summary": tier_summary, "positions": positions,
        "signals": _get_signals(), "risk_config": _redis_get("GLOBAL:RISK_CONFIG", {}),
    }


def _get_signals():
    client = _get_active_redis()
    try:
        raws = client.lrange("WEB_SIGNALS", 0, 19)
        result = []
        for raw in raws:
            d = json.loads(raw) if isinstance(raw, str) else raw
            result.append({"symbol": d.get("symbol"), "final": d.get("final"),
                           "confidence": d.get("confidence", 0),
                           "timestamp": d.get("timestamp", ""),
                           "asset_type": d.get("asset_type", "CRYPTO")})
        return result
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════
# ADMIN COMMANDS
# ══════════════════════════════════════════════════════════════════
@app.post("/api/cmd")
async def handle_cmd(request: Request, token: str = Query(default="")):
    global BOT_GLOBAL_AUTO, BOT_KILL_SWITCH
    if token != ADMIN_SECRET:
        raise HTTPException(401, "Unauthorized")

    try:
        cmd = await request.json()
        action = cmd.get("action", "")

        if action == "update_risk":
            cfg = cmd.get("config", {})
            _redis_set("GLOBAL:RISK_CONFIG", cfg)
            return {"ok": True, "msg": "Da luu cau hinh risk"}

        if action == "toggle_auto":
            BOT_GLOBAL_AUTO = bool(cmd.get("enabled", not BOT_GLOBAL_AUTO))
            db = SessionLocal()
            try:
                db.query(User).filter(User.is_active == True).update(
                    {"auto_trade": BOT_GLOBAL_AUTO}, synchronize_session=False)
                db.commit()
            finally:
                db.close()
            return {"ok": True, "msg": "Auto-trade " + ("BAT" if BOT_GLOBAL_AUTO else "TAT")}

        if action == "kill_switch":
            BOT_KILL_SWITCH = bool(cmd.get("enabled", not BOT_KILL_SWITCH))
            if BOT_KILL_SWITCH:
                threading.Thread(target=_close_all_positions, daemon=True).start()
            return {"ok": True, "msg": "Kill Switch " + ("BAT - dang dong tat ca" if BOT_KILL_SWITCH else "TAT")}

        if action == "close":
            sym = cmd.get("symbol", "").upper()
            if sym:
                threading.Thread(target=_close_symbol, args=(sym,), daemon=True).start()
            return {"ok": True, "msg": f"Dang dong {sym}"}

        if action == "update_user":
            tid  = cmd.get("telegram_id", "")
            data = cmd.get("data", {})
            db   = SessionLocal()
            try:
                u = db.query(User).filter(User.telegram_id == tid).first()
                if not u:
                    return {"ok": False, "msg": "User khong ton tai"}
                allowed = {"max_risk_pct", "max_positions", "leverage",
                          "capital", "auto_trade", "min_confidence", "tier"}
                for k, v in data.items():
                    if k in allowed:
                        setattr(u, k, v)
                db.commit()
            finally:
                db.close()
            return {"ok": True, "msg": f"Cap nhat {tid}"}

        if action == "send_report":
            rtype = cmd.get("type", "daily")
            tid = cmd.get("telegram_id", None)
            if rtype == "weekly":
                threading.Thread(target=lambda: _send_weekly_report(tid), daemon=True).start()
                return {"ok": True, "msg": "📊 Đang gửi báo cáo TUẦN qua Telegram..."}
            else:
                threading.Thread(target=lambda: _send_daily_report(tid), daemon=True).start()
                return {"ok": True, "msg": "📊 Đang gửi báo cáo PnL NGÀY qua Telegram..."}

        if action == "cleanup":
            threading.Thread(target=_cleanup_inactive_users, daemon=True).start()
            return {"ok": True, "msg": "Dang don DB..."}

        if action == "reset_history":
            tid = cmd.get("telegram_id") or None
            res = reset_trade_history(user_id=tid)
            if res.get("ok"):
                return {
                    "ok": True,
                    "msg": f"🧹 Đã xoá {res.get('deleted_trades', 0)} lệnh lịch sử cũ & {res.get('cleared_cache_keys', 0)} khoá cooldown/cache. Sẵn sàng giao dịch TraderSZ mới!",
                    **res,
                }
            return res

        if action == "trigger_signal":
            signal_data = cmd.get("signal", {})
            if not signal_data or not signal_data.get("symbol"):
                return {"ok": False, "msg": "Tin hieu thieu thong tin symbol"}
            cache = _get_active_redis()
            if cache:
                try:
                    cache.rpush("TRADE_SIGNALS", json.dumps(signal_data))
                    store_name = "Redis" if cache == redis_client else "LocalStore (0% RAM/Quota)"
                    return {"ok": True, "msg": f"Đã gửi tín hiệu {signal_data.get('symbol')} ({signal_data.get('final')}) vào hàng đợi {store_name}!"}
                except Exception as e:
                    return {"ok": False, "msg": f"Lỗi đẩy tín hiệu vào hàng đợi: {e}"}
            else:
                return {"ok": False, "msg": "Lỗi: Hệ thống lưu trữ/cooldown không khả dụng!"}

        return {"ok": False, "msg": f"Khong ho tro: {action}"}

    except Exception as e:
        return {"ok": False, "msg": str(e)}


@app.post("/api/history/reset")
def api_reset_history(token: str = Query(default=""), uid: str = Query(default="")):
    if token != ADMIN_SECRET:
        raise HTTPException(401, "Unauthorized")
    target_uid = uid.strip() if uid and uid.strip() else None
    res = reset_trade_history(user_id=target_uid)
    if res.get("ok"):
        res["msg"] = f"🧹 Đã xoá {res.get('deleted_trades', 0)} lệnh cũ & dọn {res.get('cleared_cache_keys', 0)} cache cooldown."
    return res


@app.post("/api/user/close")
async def user_close_position(request: Request):
    try:
        body   = await request.json()
        uid    = body.get("user_id", "").strip()
        symbol = body.get("symbol", "").upper().strip()
        if not uid or not symbol:
            return {"ok": False, "msg": "Thieu user_id hoac symbol"}
        _handle_user_close(uid, symbol)
        return {"ok": True, "msg": f"Dang dong {symbol}..."}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


@app.api_route("/api/report/send", methods=["GET", "POST"])
def trigger_pnl_report(uid: str = Query(default=""), type: str = Query(default="daily")):
    try:
        if type == "weekly":
            threading.Thread(target=lambda: _send_weekly_report(uid if uid else None), daemon=True).start()
        else:
            threading.Thread(target=lambda: _send_daily_report(uid if uid else None), daemon=True).start()
        return {"ok": True, "msg": f"📊 Đã kích hoạt gửi báo cáo PnL ({type}) qua Telegram thành công!"}
    except Exception as e:
        return {"ok": False, "msg": f"Lỗi kích hoạt báo cáo: {e}"}


# ══════════════════════════════════════════════════════════════════
# USERS API
# ══════════════════════════════════════════════════════════════════
@app.get("/api/users")
def list_users(token: str = Query(default=""), db: Session = Depends(get_db)):
    if token != ADMIN_SECRET:
        raise HTTPException(401, "Unauthorized")
    users = db.query(User).filter(User.is_active == True).all()
    return [{
        "telegram_id": u.telegram_id, "tier": u.tier, "capital": u.capital,
        "min_confidence": u.min_confidence, "max_risk_pct": u.max_risk_pct,
        "max_positions": u.max_positions, "leverage": u.leverage,
        "auto_trade": u.auto_trade, "total_pnl": u.total_pnl or 0,
        "registered_at": u.registered_at.strftime("%d/%m/%Y") if u.registered_at else "",
    } for u in users]


@app.put("/api/users/{telegram_id}")
def update_user(telegram_id: str, body: dict, token: str = Query(default=""),
                db: Session = Depends(get_db)):
    if token != ADMIN_SECRET:
        raise HTTPException(401, "Unauthorized")
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if not user:
        raise HTTPException(404, "User khong ton tai")
    allowed = {"min_confidence", "max_risk_pct", "max_positions",
              "leverage", "auto_trade", "capital", "tier"}
    for k, v in body.items():
        if k in allowed:
            setattr(user, k, v)
    db.commit()
    return {"ok": True}


@app.delete("/api/users/{telegram_id}")
def delete_user(telegram_id: str, token: str = Query(default=""), db: Session = Depends(get_db)):
    if token != ADMIN_SECRET:
        raise HTTPException(401, "Unauthorized")
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if not user:
        raise HTTPException(404, "User khong ton tai")
    db.delete(user)
    db.commit()
    return {"ok": True, "msg": f"Da xoa {telegram_id}"}


def _close_all_positions():
    db = SessionLocal()
    try:
        users = db.query(User).filter(User.is_active == True).all()
    finally:
        db.close()

    with _POS_LOCK:
        positions = list(LIVE_POSITIONS)

    for user in users:
        try:
            bx = get_bx(user)
            user_pos = [p for p in positions
                        if str(p.get("user_id")) == user.telegram_id]
            for p in user_pos:
                qty = float(p.get("qty", 0))
                if qty <= 0:
                    live = bx.get_open_positions()
                    for lp in live:
                        if lp.get("symbol") == p["symbol"]:
                            qty = float(lp.get("qty", 0))
                            break
                if qty > 0:
                    res = bx.close_position(p["symbol"], qty, p["direction"])
                    if not res.get("ok"):
                        log.error("close_all %s %s: %s",
                                  user.telegram_id, p["symbol"], res.get("msg"))
                time.sleep(0.2)
        except Exception as e:
            log.error("close_all user %s: %s", user.telegram_id, e)


def _close_symbol(symbol: str):
    db = SessionLocal()
    try:
        users = db.query(User).filter(User.is_active == True).all()
    finally:
        db.close()

    with _POS_LOCK:
        pos_list = [p for p in LIVE_POSITIONS if p.get("symbol") == symbol]

    for user in users:
        try:
            bx = get_bx(user)
            user_pos = [p for p in pos_list
                        if str(p.get("user_id")) == user.telegram_id]
            for p in user_pos:
                qty = float(p.get("qty", 0))
                if qty <= 0:
                    live = bx.get_open_positions()
                    for lp in live:
                        if lp.get("symbol") == symbol:
                            qty = float(lp.get("qty", 0))
                            break
                if qty > 0:
                    bx.close_position(symbol, qty, p["direction"])
        except Exception as e:
            log.error("close_symbol %s user %s: %s", symbol, user.telegram_id, e)


# ══════════════════════════════════════════════════════════════════
# ADMIN DASHBOARD GATE & HTML
# ══════════════════════════════════════════════════════════════════

ADMIN_DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SIGNALBOT v6.1 - Admin Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/lucide@latest"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
    <style>
        body {
            font-family: 'Inter', sans-serif;
            background-color: #030611;
            color: #e2e8f0;
        }
        .font-mono {
            font-family: 'JetBrains Mono', monospace;
        }
        .scrollbar-thin::-webkit-scrollbar {
            width: 5px;
            height: 5px;
        }
        .scrollbar-thin::-webkit-scrollbar-track {
            background: #040811;
        }
        .scrollbar-thin::-webkit-scrollbar-thumb {
            background: #1b263e;
            border-radius: 4px;
        }
        .badge-active {
            background-color: rgba(16, 185, 129, 0.1);
            color: #10b981;
            border: 1px solid rgba(16, 185, 129, 0.2);
        }
        .badge-inactive {
            background-color: rgba(239, 68, 68, 0.1);
            color: #ef4444;
            border: 1px solid rgba(239, 68, 68, 0.2);
        }
    </style>
</head>
<body class="min-h-screen flex flex-col antialiased">

    <!-- LOGIN SCREEN -->
    <div id="login-screen" class="hidden fixed inset-0 z-50 flex items-center justify-center bg-[#030611] px-4">
        <div class="bg-[#0b1120] border border-[#1b263e] rounded-xl p-8 max-w-md w-full shadow-2xl">
            <div class="text-center mb-6">
                <h1 class="text-2xl font-extrabold tracking-wider text-transparent bg-clip-text bg-gradient-to-r from-emerald-400 to-cyan-400">
                    SIGNALBOT v6.1
                </h1>
                <p class="text-xs text-[#718096] mt-2 font-medium">Đăng nhập quyền quản trị tối cao</p>
            </div>
            
            <form id="login-form" class="space-y-4">
                <div>
                    <label class="text-xs text-[#718096] font-bold uppercase tracking-wider block mb-1">Mật khẩu Admin</label>
                    <input type="password" id="admin-password" placeholder="Nhập mã bảo mật..." required
                           class="w-full bg-[#040811] border border-[#1b263e] rounded-lg px-4 py-3 text-sm focus:outline-none focus:border-emerald-500 font-mono text-[#e2e8f0]">
                </div>
                <button type="submit" 
                        class="w-full py-3 rounded-lg text-sm font-black bg-gradient-to-r from-emerald-500 to-cyan-500 text-black hover:opacity-90 transition-all shadow-[0_0_15px_rgba(16,185,129,0.15)]">
                    XÁC THỰC HỆ THỐNG
                </button>
                <p id="login-error" class="text-red-400 text-xs font-semibold text-center mt-2 hidden">⚠️ Sai mật khẩu hoặc lỗi kết nối!</p>
            </form>
        </div>
    </div>

    <!-- MAIN DASHBOARD CONTENT -->
    <div id="dashboard-content" class="hidden flex-1 flex flex-col">
        <!-- Header -->
        <header class="border-b border-[#1b263e] bg-[#080d1a] px-6 py-4 flex flex-col md:flex-row justify-between items-start md:items-center gap-4 shrink-0 shadow-lg">
            <div>
                <div class="flex items-center gap-3">
                    <h1 class="text-xl font-extrabold tracking-wider text-transparent bg-clip-text bg-gradient-to-r from-emerald-400 via-mint-300 to-cyan-400">
                        SIGNAL<span class="text-[#05f38c]">BOT</span> v6.1
                    </h1>
                    <span class="px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 flex items-center gap-1.5">
                        <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 inline-block animate-pulse"></span>
                        LIVE DASHBOARD
                    </span>
                    <button onclick="logout()" class="text-xs text-[#718096] hover:text-red-400 font-bold ml-4 border-l border-[#1b263e] pl-4">👉 ĐĂNG XUẤT</button>
                </div>
                <p class="text-xs text-[#718096] mt-1 font-medium">Bảng điều khiển quản trị viên và cấu hình thanh khoản orderbook theo thời gian thực</p>
            </div>

            <div class="flex items-center gap-4 self-stretch md:self-auto justify-between md:justify-end font-mono text-xs">
                <div>
                    <span class="text-[#718096]">AUTO SYSTEM: </span>
                    <span id="header-auto-badge" class="font-bold">ĐANG TẢI...</span>
                </div>
                <div class="h-6 w-px bg-[#1b263e]"></div>
                <div>
                    <span class="text-red-400 font-bold">KILL SWITCH: </span>
                    <span id="header-kill-badge" class="font-bold">ĐANG TẢI...</span>
                </div>
            </div>
        </header>

        <!-- Workspace Grid -->
        <main class="flex-1 p-6 w-full max-w-[1700px] mx-auto grid grid-cols-1 xl:grid-cols-12 gap-6 overflow-hidden">
            
            <!-- LEFT COLUMN: System Controls, Config & Users (4 cols) -->
            <div class="xl:col-span-4 flex flex-col gap-6">
                <!-- System Config -->
                <div class="bg-[#0b1120] border border-[#1b263e] rounded-xl p-5 flex flex-col gap-4 shadow-lg">
                    <h2 class="text-sm font-bold text-[#718096] tracking-wider uppercase flex items-center gap-2">
                        <i data-lucide="settings" class="w-4 h-4 text-emerald-400"></i> Bảng lệnh hệ thống
                    </h2>

                    <div class="grid grid-cols-2 gap-3">
                        <button onclick="toggleGlobalAuto()" class="py-2.5 px-3 rounded-lg text-xs font-black border border-[#1b263e] bg-[#080d1a] text-emerald-400 hover:bg-emerald-950/20 hover:border-emerald-500/40 transition-all flex items-center justify-center gap-2">
                            <i data-lucide="play" class="w-3.5 h-3.5"></i> AUTO-TRADE
                        </button>
                        <button onclick="toggleKillSwitch()" class="py-2.5 px-3 rounded-lg text-xs font-black border border-red-500/20 bg-red-950/10 text-red-400 hover:bg-red-900/30 hover:border-red-500/40 transition-all flex items-center justify-center gap-2">
                            <i data-lucide="shield-alert" class="w-3.5 h-3.5 animate-pulse"></i> KILL SWITCH
                        </button>
                    </div>

                    <div class="border-t border-[#1b263e]/40 my-1"></div>

                    <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
                        <button onclick="triggerAction('send_report', {type: 'daily'})" class="py-2 px-1 text-center bg-[#070b16] hover:bg-[#141d2e] rounded border border-[#1b263e] text-[10px] font-black text-[#e2e8f0] transition-all">
                            📊 BÁO CÁO NGÀY
                        </button>
                        <button onclick="triggerAction('send_report', {type: 'weekly'})" class="py-2 px-1 text-center bg-[#070b16] hover:bg-[#141d2e] rounded border border-[#1b263e] text-[10px] font-black text-[#e2e8f0] transition-all">
                            📊 BÁO CÁO TUẦN
                        </button>
                        <button onclick="triggerAction('cleanup')" class="py-2 px-1 text-center bg-[#070b16] hover:bg-[#141d2e] rounded border border-[#1b263e] text-[10px] font-black text-[#e2e8f0] transition-all">
                            🧹 DỌN DẸP DB
                        </button>
                        <button onclick="if(confirm('Xoá toàn bộ lịch sử giao dịch cũ, reset Circuit Breaker & điểm phạt Winrate để bắt đầu giao dịch TraderSZ mới?')) triggerAction('reset_history')" class="py-2 px-1 text-center bg-amber-950/30 hover:bg-amber-900/40 rounded border border-amber-500/30 text-[10px] font-black text-amber-300 transition-all">
                            🗑️ XOÁ LỊCH SỬ GD
                        </button>
                    </div>
                </div>

                <!-- Add/Update User Form -->
                <div class="bg-[#0b1120] border border-[#1b263e] rounded-xl p-5 flex flex-col gap-4 shadow-lg">
                    <h2 class="text-sm font-bold text-[#718096] tracking-wider uppercase flex items-center gap-2">
                        <i data-lucide="user-plus" class="w-4 h-4 text-emerald-400"></i> Đăng ký / Sửa User
                    </h2>

                    <form id="user-form" class="space-y-3">
                        <div class="grid grid-cols-2 gap-2">
                            <div>
                                <label class="text-[10px] text-[#718096] font-bold block uppercase mb-1">Telegram UID</label>
                                <input type="text" id="form-user-id" required placeholder="6286755..."
                                       class="w-full bg-[#040811] border border-[#1b263e] rounded p-2 text-xs font-mono text-[#e2e8f0]">
                            </div>
                            <div>
                                <label class="text-[10px] text-[#718096] font-bold block uppercase mb-1">Vốn (Capital)</label>
                                <input type="number" id="form-user-capital" required placeholder="500.0"
                                       class="w-full bg-[#040811] border border-[#1b263e] rounded p-2 text-xs font-mono text-[#e2e8f0]">
                            </div>
                        </div>

                        <div class="grid grid-cols-3 gap-2">
                            <div>
                                <label class="text-[10px] text-[#718096] font-bold block uppercase mb-1">Min Conf (%)</label>
                                <input type="number" id="form-user-conf" value="70"
                                       class="w-full bg-[#040811] border border-[#1b263e] rounded p-2 text-xs font-mono text-[#e2e8f0]">
                            </div>
                            <div>
                                <label class="text-[10px] text-[#718096] font-bold block uppercase mb-1">Risk / Lệnh (%)</label>
                                <input type="number" step="0.1" id="form-user-risk" value="1.5"
                                       class="w-full bg-[#040811] border border-[#1b263e] rounded p-2 text-xs font-mono text-[#e2e8f0]">
                            </div>
                            <div>
                                <label class="text-[10px] text-[#718096] font-bold block uppercase mb-1">Leverage</label>
                                <input type="number" id="form-user-lev" value="10"
                                       class="w-full bg-[#040811] border border-[#1b263e] rounded p-2 text-xs font-mono text-[#e2e8f0]">
                            </div>
                        </div>

                        <button type="submit" class="w-full py-2 bg-emerald-500 hover:bg-emerald-600 text-black font-black text-xs rounded transition-all">
                            💾 LƯU THÔNG TIN USER
                        </button>
                    </form>
                </div>

                <!-- Users List -->
                <div class="bg-[#0b1120] border border-[#1b263e] rounded-xl p-5 flex flex-col gap-3 shadow-lg flex-1 min-h-[250px] overflow-hidden">
                    <h2 class="text-sm font-bold text-[#718096] tracking-wider uppercase flex items-center gap-2 mb-2">
                        <i data-lucide="users" class="w-4 h-4 text-emerald-400"></i> Người dùng copy trade (<span id="user-count">0</span>)
                    </h2>

                    <div class="overflow-y-auto max-h-[350px] scrollbar-thin flex flex-col gap-2" id="users-container">
                        <!-- Users render dynamically -->
                    </div>
                </div>
            </div>

            <!-- MIDDLE COLUMN: Orderbook & Liquidity Real-time View (5 cols) -->
            <div class="xl:col-span-5 flex flex-col gap-6">
                <!-- Dynamic Orderbook & Depth Map -->
                <div class="bg-[#0b1120] border border-[#1b263e] rounded-xl p-5 flex flex-col gap-4 shadow-lg">
                    <div class="flex justify-between items-center">
                        <h2 class="text-sm font-bold text-[#718096] tracking-wider uppercase flex items-center gap-2">
                            <i data-lucide="book-open" class="w-4 h-4 text-emerald-400"></i> Phân tích Sổ Lệnh L2 (Orderbook)
                        </h2>
                        <span id="orderbook-imb" class="font-mono text-xs font-black">--</span>
                    </div>

                    <div class="grid grid-cols-2 gap-4 text-xs font-mono bg-[#040811] p-3 rounded-lg border border-[#141d2e] relative">
                        <!-- Asks Column (Sells) -->
                        <div class="flex flex-col gap-1">
                            <span class="text-red-400 font-bold block border-b border-[#1b263e]/40 pb-1 mb-1 text-center">ASK (SELL WALLS)</span>
                            <div id="asks-container" class="flex flex-col gap-1 h-[140px] overflow-y-auto scrollbar-thin">
                                <!-- Asks map here -->
                            </div>
                        </div>

                        <!-- Bids Column (Buys) -->
                        <div class="flex flex-col gap-1">
                            <span class="text-emerald-400 font-bold block border-b border-[#1b263e]/40 pb-1 mb-1 text-center">BID (BUY WALLS)</span>
                            <div id="bids-container" class="flex flex-col gap-1 h-[140px] overflow-y-auto scrollbar-thin">
                                <!-- Bids map here -->
                            </div>
                        </div>
                    </div>

                    <div class="grid grid-cols-2 gap-3 text-center">
                        <div class="bg-[#040811] rounded p-2 text-xs border border-[#141d2e]">
                            <span class="text-[#718096] text-[10px] uppercase font-bold block">Tường Mua Lớn Nhất</span>
                            <span id="buy-wall-val" class="text-emerald-400 font-bold block mt-0.5">--</span>
                        </div>
                        <div class="bg-[#040811] rounded p-2 text-xs border border-[#141d2e]">
                            <span class="text-[#718096] text-[10px] uppercase font-bold block">Tường Bán Lớn Nhất</span>
                            <span id="sell-wall-val" class="text-red-400 font-bold block mt-0.5">--</span>
                        </div>
                    </div>
                </div>

                <!-- Liquidation Map & Cascade Risk Indicator -->
                <div class="bg-[#0b1120] border border-[#1b263e] rounded-xl p-5 flex flex-col gap-4 shadow-lg">
                    <div class="flex justify-between items-center">
                        <h2 class="text-sm font-bold text-[#718096] tracking-wider uppercase flex items-center gap-2">
                            <i data-lucide="layers" class="w-4 h-4 text-emerald-400"></i> Bản đồ thanh lý đòn bẩy
                        </h2>
                        <span id="cascade-risk-badge" class="px-2 py-0.5 rounded text-[10px] font-black">ĐANG QUÉT</span>
                    </div>

                    <div class="grid grid-cols-2 gap-4 text-xs font-mono">
                        <!-- Long Liquidation Levels -->
                        <div class="bg-[#040811] border border-[#141d2e] rounded-lg p-3">
                            <span class="text-emerald-400 font-bold block border-b border-[#1b263e]/30 pb-1 mb-2">Thanh Lý LONG</span>
                            <div id="long-liqs" class="space-y-1.5 text-[11px]">
                                <!-- Long levels -->
                            </div>
                        </div>

                        <!-- Short Liquidation Levels -->
                        <div class="bg-[#040811] border border-[#141d2e] rounded-lg p-3">
                            <span class="text-red-400 font-bold block border-b border-[#1b263e]/30 pb-1 mb-2">Thanh Lý SHORT</span>
                            <div id="short-liqs" class="space-y-1.5 text-[11px]">
                                <!-- Short levels -->
                            </div>
                        </div>
                    </div>

                    <div class="bg-[#040811] border border-[#141d2e] rounded-lg p-3 text-xs flex justify-between font-mono">
                        <div>
                            <span class="text-[#718096] text-[10px] uppercase block">dominant side</span>
                            <span id="dominant-side-val" class="font-bold">--</span>
                        </div>
                        <div class="text-right">
                            <span class="text-[#718096] text-[10px] uppercase block">spread thị trường</span>
                            <span id="spread-pct-val" class="font-bold">--</span>
                        </div>
                    </div>
                </div>
            </div>

            <!-- RIGHT COLUMN: Signal Commander & Open Positions (3 cols) -->
            <div class="xl:col-span-3 flex flex-col gap-6">
                <!-- Signal Commander Form -->
                <div class="bg-[#0b1120] border border-[#1b263e] rounded-xl p-5 flex flex-col gap-4 shadow-lg">
                    <h2 class="text-sm font-bold text-[#718096] tracking-wider uppercase flex items-center gap-2">
                        <i data-lucide="zap" class="w-4 h-4 text-emerald-400"></i> Bộ Phát Tín Hiệu (Redis Command)
                    </h2>

                    <form id="signal-form" class="space-y-3 font-mono text-xs">
                        <div>
                            <label class="text-[10px] text-[#718096] font-bold block uppercase mb-1">Cặp Giao Dịch</label>
                            <div class="grid grid-cols-3 gap-2">
                                <select id="sig-symbol-select" onchange="document.getElementById('sig-symbol').value = this.value; updateMarketDepth();"
                                        class="col-span-2 bg-[#040811] border border-[#1b263e] rounded p-2 text-xs text-emerald-400 font-bold">
                                    <option value="BTCUSDT" selected>BTCUSDT (Bitcoin)</option>
                                    <option value="ETHUSDT">ETHUSDT (Ethereum)</option>
                                    <option value="BNBUSDT">BNBUSDT (Binance Coin)</option>
                                    <option value="SOLUSDT">SOLUSDT (Solana)</option>
                                    <option value="HYPEUSDT">HYPEUSDT (Hyperliquid)</option>
                                    <option value="XAUUSD">XAUUSD (Gold)</option>
                                    <option value="NCCOXAG2USD-USDT">NCCOXAG2USDUSDT (Silver)</option>
                                    <option value="TSLA">TSLA (Tesla)</option>
                                    <option value="NVDA">NVDA (Nvidia)</option>
                                    <option value="SPY">SPY (S&P 500)</option>
                                    <option value="QQQ">QQQ (Nasdaq 100)</option>
                                    <option value="">Khác (Tùy chọn)...</option>
                                </select>
                                <input type="text" id="sig-symbol" required value="BTCUSDT" oninput="updateMarketDepth();"
                                       class="col-span-1 bg-[#040811] border border-[#1b263e] rounded p-2 text-xs uppercase text-center text-gray-200" placeholder="Symbol">
                            </div>
                        </div>

                        <div class="grid grid-cols-2 gap-2">
                            <div>
                                <label class="text-[10px] text-[#718096] font-bold block uppercase mb-1">Hướng Lệnh</label>
                                <select id="sig-direction" class="w-full bg-[#040811] border border-[#1b263e] rounded p-2 text-xs font-bold text-emerald-400">
                                    <option value="LONG" class="text-emerald-400 font-bold">LONG</option>
                                    <option value="SHORT" class="text-red-400 font-bold">SHORT</option>
                                </select>
                            </div>
                            <div>
                                <label class="text-[10px] text-[#718096] font-bold block uppercase mb-1">Độ Tin Cậy (%)</label>
                                <input type="number" id="sig-confidence" required value="85"
                                       class="w-full bg-[#040811] border border-[#1b263e] rounded p-2 text-xs">
                            </div>
                        </div>

                        <div class="grid grid-cols-2 gap-2">
                            <div>
                                <label class="text-[10px] text-[#718096] font-bold block uppercase mb-1">Giá Vào Lệnh</label>
                                <input type="number" step="0.01" id="sig-entry" required placeholder="92500"
                                       class="w-full bg-[#040811] border border-[#1b263e] rounded p-2 text-xs">
                            </div>
                            <div>
                                <label class="text-[10px] text-red-400 font-bold block uppercase mb-1">Giá Cắt Lỗ (SL)</label>
                                <input type="number" step="0.01" id="sig-sl" required placeholder="91200"
                                       class="w-full bg-[#040811] border border-[#1b263e] rounded p-2 text-xs text-red-400">
                            </div>
                        </div>

                        <div class="grid grid-cols-2 gap-2">
                            <div>
                                <label class="text-[10px] text-emerald-400 font-bold block uppercase mb-1">Chốt Lời 1 (TP1)</label>
                                <input type="number" step="0.01" id="sig-tp1" required placeholder="94500"
                                       class="w-full bg-[#040811] border border-[#1b263e] rounded p-2 text-xs text-emerald-400">
                            </div>
                            <div>
                                <label class="text-[10px] text-blue-400 font-bold block uppercase mb-1">Chốt Lời 2 (TP2)</label>
                                <input type="number" step="0.01" id="sig-tp2" required placeholder="96000"
                                       class="w-full bg-[#040811] border border-[#1b263e] rounded p-2 text-xs text-blue-400">
                            </div>
                        </div>

                        <button type="submit" class="w-full py-2.5 bg-gradient-to-r from-emerald-500 to-cyan-500 text-black font-extrabold text-xs rounded transition-all shadow-[0_0_10px_rgba(16,185,129,0.1)]">
                            🚀 PHÁT TÍN HIỆU (EMIT SIGNAL)
                        </button>
                    </form>
                </div>

                <!-- Live Positions -->
                <div class="bg-[#0b1120] border border-[#1b263e] rounded-xl p-5 flex flex-col gap-3 shadow-lg flex-1 min-h-[220px] overflow-hidden">
                    <h2 class="text-sm font-bold text-[#718096] tracking-wider uppercase flex items-center gap-2 mb-2">
                        <i data-lucide="terminal" class="w-4 h-4 text-emerald-400"></i> Lệnh Đang Mở (Real-time)
                    </h2>

                    <div id="positions-container" class="overflow-y-auto max-h-[350px] scrollbar-thin flex flex-col gap-2">
                        <!-- Positions rendered dynamically -->
                    </div>
                </div>
            </div>

        </main>
    </div>

    <!-- SCRIPT FOR DYNAMIC DATA POLLING -->
    <script>
        let adminToken = localStorage.getItem('admin_token') || '';

        document.getElementById('login-form').addEventListener('submit', async function(e) {
            e.preventDefault();
            const pass = document.getElementById('admin-password').value;
            // Test validity by list_users call
            try {
                const res = await fetch('/api/users?token=' + encodeURIComponent(pass));
                if (res.status === 200) {
                    adminToken = pass;
                    localStorage.setItem('admin_token', pass);
                    showDashboard();
                } else {
                    showError();
                }
            } catch (err) {
                showError();
            }
        });

        function showError() {
            const err = document.getElementById('login-error');
            err.classList.remove('hidden');
            setTimeout(() => err.classList.add('hidden'), 5000);
        }

        function checkAuth() {
            if (!adminToken) {
                document.getElementById('login-screen').classList.remove('hidden');
                document.getElementById('dashboard-content').classList.add('hidden');
            } else {
                showDashboard();
            }
        }

        function logout() {
            localStorage.removeItem('admin_token');
            adminToken = '';
            checkAuth();
        }

        function showDashboard() {
            document.getElementById('login-screen').classList.add('hidden');
            document.getElementById('dashboard-content').classList.remove('hidden');
            lucide.createIcons();
            fetchData();
            // Start regular intervals
            setInterval(fetchData, 3000);
        }

        async function triggerAction(actionName, extraParams = {}) {
            if (!adminToken) return;
            const payload = { action: actionName, ...extraParams };
            try {
                const res = await fetch('/api/cmd?token=' + encodeURIComponent(adminToken), {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const d = await res.json();
                alert(d.msg || (d.ok ? 'Thao tác thành công!' : 'Có lỗi xảy ra!'));
                fetchData();
            } catch (err) {
                alert('Lỗi kết nối API!');
            }
        }

        async function toggleGlobalAuto() {
            if (!adminToken) return;
            try {
                const res = await fetch('/api/state?token=' + encodeURIComponent(adminToken));
                const state = await res.json();
                const nextState = !state.auto_trade;
                const r = await fetch('/api/cmd?token=' + encodeURIComponent(adminToken), {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action: 'toggle_auto', enabled: nextState })
                });
                const d = await r.json();
                fetchData();
            } catch(e) {}
        }

        async function toggleKillSwitch() {
            if (!adminToken) return;
            try {
                const res = await fetch('/api/state?token=' + encodeURIComponent(adminToken));
                const state = await res.json();
                const nextState = !state.kill_switch;
                const r = await fetch('/api/cmd?token=' + encodeURIComponent(adminToken), {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action: 'kill_switch', enabled: nextState })
                });
                const d = await r.json();
                fetchData();
            } catch(e) {}
        }

        // Add/Update User Form
        document.getElementById('user-form').addEventListener('submit', async function(e) {
            e.preventDefault();
            const uid = document.getElementById('form-user-id').value.trim();
            const cap = parseFloat(document.getElementById('form-user-capital').value);
            const conf = parseFloat(document.getElementById('form-user-conf').value);
            const risk = parseFloat(document.getElementById('form-user-risk').value);
            const lev = parseInt(document.getElementById('form-user-lev').value);

            if (!uid || isNaN(cap)) return;

            try {
                const registerPayload = {
                    telegram_id: uid,
                    api_key: 'BINGX_MOCK_KEY_' + uid,
                    api_secret: 'BINGX_MOCK_SECRET_' + uid
                };
                
                // First call register to ensure DB record exists
                const regRes = await fetch('/api/users/register', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(registerPayload)
                });
                
                // Then call put update for the specific params
                const updatePayload = {
                    capital: cap,
                    min_confidence: conf,
                    max_risk_pct: risk,
                    leverage: lev,
                    auto_trade: true
                };
                
                const upRes = await fetch(`/api/users/${uid}?token=` + encodeURIComponent(adminToken), {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(updatePayload)
                });

                if (upRes.ok) {
                    alert('Lưu người dùng ' + uid + ' thành công!');
                    document.getElementById('user-form').reset();
                    fetchData();
                } else {
                    alert('Lưu thất bại!');
                }
            } catch (err) {
                alert('Lỗi khi lưu user!');
            }
        });

        // Delete user helper
        async function deleteUser(uid) {
            if (!confirm('Bạn có chắc chắn muốn xóa user ' + uid + ' khỏi hệ thống?')) return;
            try {
                const res = await fetch(`/api/users/${uid}?token=` + encodeURIComponent(adminToken), {
                    method: 'DELETE'
                });
                if (res.ok) {
                    fetchData();
                } else {
                    alert('Không thể xóa user!');
                }
            } catch (e) {}
        }

        // Close position helper
        async function closeUserPosition(uid, symbol) {
            if (!confirm('Bạn có muốn đóng vị thế ' + symbol + ' của user ' + uid + '?')) return;
            try {
                const res = await fetch('/api/user/close', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ user_id: uid, symbol: symbol })
                });
                const d = await res.json();
                alert(d.msg || 'Yêu cầu đóng lệnh đã gửi!');
                fetchData();
            } catch (e) {}
        }

        // Manual Signal Dispatcher Form
        document.getElementById('signal-form').addEventListener('submit', async function(e) {
            e.preventDefault();
            const symbol = document.getElementById('sig-symbol').value.toUpperCase().trim();
            const direction = document.getElementById('sig-direction').value;
            const confidence = parseFloat(document.getElementById('sig-confidence').value);
            const entry = parseFloat(document.getElementById('sig-entry').value);
            const sl = parseFloat(document.getElementById('sig-sl').value);
            const tp1 = parseFloat(document.getElementById('sig-tp1').value);
            const tp2 = parseFloat(document.getElementById('sig-tp2').value);

            const signalPayload = {
                symbol: symbol,
                final: direction,
                confidence: confidence,
                plan: {
                    entry: entry,
                    sl: sl,
                    tp1: tp1,
                    tp2: tp2
                },
                timestamp: new Date().toISOString()
            };

            try {
                const res = await fetch('/api/cmd?token=' + encodeURIComponent(adminToken), {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        action: 'trigger_signal',
                        signal: signalPayload
                    })
                });
                const d = await res.json();
                alert(d.msg || 'Đã gửi tín hiệu giao dịch!');
            } catch(err) {
                alert('Không thể gửi tín hiệu!');
            }
        });

        // Core data polling
        async function fetchData() {
            if (!adminToken) return;

            try {
                // Fetch state
                const resState = await fetch('/api/state?token=' + encodeURIComponent(adminToken));
                if (resState.status === 401) {
                    logout();
                    return;
                }
                const state = await resState.json();

                // Fetch users
                const resUsers = await fetch('/api/users?token=' + encodeURIComponent(adminToken));
                const users = await resUsers.json();

                // Render Header info
                const headerAuto = document.getElementById('header-auto-badge');
                headerAuto.innerText = state.auto_trade ? 'HOẠT ĐỘNG' : 'TẠM DỪNG';
                headerAuto.className = state.auto_trade ? 'text-emerald-400 font-bold animate-pulse' : 'text-amber-500 font-bold';

                const headerKill = document.getElementById('header-kill-badge');
                headerKill.innerText = state.kill_switch ? 'Armed (KHẨN CẤP)' : 'Standby (AN TOÀN)';
                headerKill.className = state.kill_switch ? 'text-red-500 font-black animate-bounce' : 'text-emerald-400 font-bold';

                // Render Users
                document.getElementById('user-count').innerText = users.length;
                const userBox = document.getElementById('users-container');
                userBox.innerHTML = '';
                
                users.forEach(u => {
                    const row = document.createElement('div');
                    row.className = 'flex justify-between items-center bg-[#070b16] border border-[#141d2e] rounded-lg p-3 text-xs';
                    row.innerHTML = `
                        <div>
                            <span class="font-bold block text-emerald-400">UID: ${u.telegram_id}</span>
                            <span class="text-[10px] text-[#718096]">Capital: $${u.capital.toFixed(2)} | Lev: ${u.leverage}x | Risk: ${u.max_risk_pct}%</span>
                            <span class="text-[10px] text-[#718096] block">PnL Tích Lũy: <b class="${u.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}">${u.total_pnl >= 0 ? '+' : ''}$${u.total_pnl.toFixed(2)}</b></span>
                        </div>
                        <div class="flex gap-1.5">
                            <button onclick="editUserFill('${u.telegram_id}', ${u.capital}, ${u.min_confidence}, ${u.max_risk_pct}, ${u.leverage})" 
                                    class="p-1.5 rounded bg-blue-950/40 text-blue-400 border border-blue-500/20 hover:bg-blue-900/40 font-bold text-[10px] uppercase">
                                Sửa
                            </button>
                            <button onclick="deleteUser('${u.telegram_id}')" 
                                    class="p-1.5 rounded bg-red-950/40 text-red-400 border border-red-500/20 hover:bg-red-900/40 font-bold text-[10px] uppercase">
                                Xóa
                            </button>
                        </div>
                    `;
                    userBox.appendChild(row);
                });

                // Render Open Positions
                const posBox = document.getElementById('positions-container');
                posBox.innerHTML = '';
                
                if (state.positions && state.positions.length > 0) {
                    state.positions.forEach(p => {
                        const card = document.createElement('div');
                        card.className = `border rounded-lg p-3 text-xs bg-[#070b16] ${p.direction === 'LONG' ? 'border-emerald-500/20' : 'border-red-500/20'}`;
                        card.innerHTML = `
                            <div class="flex justify-between items-start">
                                <div>
                                    <span class="font-black ${p.direction === 'LONG' ? 'text-emerald-400' : 'text-red-400'}">${p.direction} - ${p.symbol}</span>
                                    <span class="text-[10px] text-[#718096] block font-mono">Qty: ${p.qty} | User: ${p.user_id}</span>
                                </div>
                                <button onclick="closeUserPosition('${p.user_id}', '${p.symbol}')" 
                                        class="px-2 py-1 text-[10px] font-black bg-red-950/50 text-red-400 border border-red-500/20 rounded hover:bg-red-900/40">
                                    ĐÓNG VỊ THẾ
                                </button>
                            </div>
                        `;
                        posBox.appendChild(card);
                    });
                } else {
                    posBox.innerHTML = '<div class="text-[#718096] text-center text-xs py-4 font-mono">Không có vị thế hoạt động.</div>';
                }

                // Render real-time orderbook & liquidations dynamically
                await updateMarketDepth();

            } catch (err) {
                console.error(err);
            }
        }

        function editUserFill(id, capital, min_confidence, max_risk_pct, leverage) {
            document.getElementById('form-user-id').value = id;
            document.getElementById('form-user-capital').value = capital;
            document.getElementById('form-user-conf').value = min_confidence;
            document.getElementById('form-user-risk').value = max_risk_pct;
            document.getElementById('form-user-lev').value = leverage;
        }

        // Pre-fill active asset price data to helper fields in Signal form
        function preFillSignal(symbol, entryPrice) {
            document.getElementById('sig-symbol').value = symbol;
            const selectEl = document.getElementById('sig-symbol-select');
            if (selectEl) {
                let found = false;
                for (let i = 0; i < selectEl.options.length; i++) {
                    if (selectEl.options[i].value === symbol) {
                        selectEl.selectedIndex = i;
                        found = true;
                        break;
                    }
                }
                if (!found) {
                    selectEl.value = ""; // Show as Khác/custom
                }
            }
            document.getElementById('sig-entry').value = entryPrice.toFixed(2);
            const atr = entryPrice * 0.012;
            const direction = document.getElementById('sig-direction').value;
            if (direction === 'LONG') {
                document.getElementById('sig-sl').value = (entryPrice - atr).toFixed(2);
                document.getElementById('sig-tp1').value = (entryPrice + atr * 2).toFixed(2);
                document.getElementById('sig-tp2').value = (entryPrice + atr * 3.5).toFixed(2);
            } else {
                document.getElementById('sig-sl').value = (entryPrice + atr).toFixed(2);
                document.getElementById('sig-tp1').value = (entryPrice - atr * 2).toFixed(2);
                document.getElementById('sig-tp2').value = (entryPrice - atr * 3.5).toFixed(2);
            }
        }

        // Real-time Orderbook and Liquidation Map from Real API
        let lastSimulatedPrice = null;
        async function updateMarketDepth() {
            const symbolInput = document.getElementById('sig-symbol');
            if (!symbolInput) return;
            const symbol = symbolInput.value.toUpperCase().trim() || 'BTCUSDT';
            
            try {
                const res = await fetch(`/api/market-depth?symbol=${encodeURIComponent(symbol)}`);
                if (!res.ok) throw new Error("API error");
                const data = await res.json();
                
                if (data.success) {
                    renderRealOrderbook(data.symbol, data.price, data.orderbook);
                    renderRealLiquidations(data.symbol, data.price, data.liquidation);
                } else {
                    renderSimulatedDepth(symbol);
                }
            } catch (err) {
                console.error("updateMarketDepth error:", err);
                renderSimulatedDepth(symbol);
            }
        }

        function renderRealOrderbook(symbol, price, ob) {
            const bidContainer = document.getElementById('bids-container');
            const askContainer = document.getElementById('asks-container');
            if (!bidContainer || !askContainer) return;
            
            bidContainer.innerHTML = '';
            askContainer.innerHTML = '';

            let bids = ob.bids || [];
            let asks = ob.asks || [];
            
            if (bids.length === 0 || asks.length === 0) {
                const step = price * 0.0001;
                for (let i = 1; i <= 6; i++) {
                    const askP = price + i * step;
                    const askQ = Math.random() * 1.5 + 0.1 + (i === 3 ? 5 : 0);
                    asks.push([askP, askQ]);

                    const bidP = price - i * step;
                    const bidQ = Math.random() * 1.5 + 0.1 + (i === 4 ? 6 : 0);
                    bids.push([bidP, bidQ]);
                }
                asks.sort((a, b) => a[0] - b[0]);
                bids.sort((a, b) => b[0] - a[0]);
            }

            let maxUsd = 0.001;
            const formattedAsks = asks.slice(0, 6).map(a => {
                const p = parseFloat(a[0]);
                const q = parseFloat(a[1]);
                const usd = p * q;
                if (usd > maxUsd) maxUsd = usd;
                return { price: p, qty: q, usd: usd };
            });
            const formattedBids = bids.slice(0, 6).map(b => {
                const p = parseFloat(b[0]);
                const q = parseFloat(b[1]);
                const usd = p * q;
                if (usd > maxUsd) maxUsd = usd;
                return { price: p, qty: q, usd: usd };
            });

            [...formattedAsks].reverse().forEach(ask => {
                const row = document.createElement('div');
                row.className = 'flex justify-between relative py-0.5 px-1 hover:bg-white/5';
                const pct = (ask.usd / maxUsd) * 100;
                row.innerHTML = `
                    <div class="absolute right-0 top-0 bottom-0 bg-red-500/5 transition-all" style="width: ${pct}%"></div>
                    <span class="text-red-400 relative z-10 font-bold">$${ask.price.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 4})}</span>
                    <span class="text-gray-400 relative z-10">${ask.qty.toFixed(3)}</span>
                    <span class="text-gray-600 relative z-10">$${Math.round(ask.usd).toLocaleString()}</span>
                `;
                askContainer.appendChild(row);
            });

            formattedBids.forEach(bid => {
                const row = document.createElement('div');
                row.className = 'flex justify-between relative py-0.5 px-1 hover:bg-white/5';
                const pct = (bid.usd / maxUsd) * 100;
                row.innerHTML = `
                    <div class="absolute right-0 top-0 bottom-0 bg-emerald-500/5 transition-all" style="width: ${pct}%"></div>
                    <span class="text-emerald-400 relative z-10 font-bold">$${bid.price.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 4})}</span>
                    <span class="text-gray-400 relative z-10">${bid.qty.toFixed(3)}</span>
                    <span class="text-gray-600 relative z-10">$${Math.round(bid.usd).toLocaleString()}</span>
                `;
                bidContainer.appendChild(row);
            });

            const bestBidWall = formattedBids.length > 0 ? formattedBids.reduce((m, b) => b.usd > m.usd ? b : m, formattedBids[0]) : { price, usd: 0 };
            const bestAskWall = formattedAsks.length > 0 ? formattedAsks.reduce((m, a) => a.usd > m.usd ? a : m, formattedAsks[0]) : { price, usd: 0 };
            document.getElementById('buy-wall-val').innerText = `$${bestBidWall.price.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 2})} ($${Math.round(bestBidWall.usd).toLocaleString()})`;
            document.getElementById('sell-wall-val').innerText = `$${bestAskWall.price.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 2})} ($${Math.round(bestAskWall.usd).toLocaleString()})`;

            const imbVal = ob.imbalance !== undefined ? ob.imbalance : 0;
            const imbDisp = document.getElementById('orderbook-imb');
            imbDisp.innerText = 'Imbalance: ' + (imbVal >= 0 ? '+' : '') + imbVal + '%';
            imbDisp.className = 'font-mono text-xs font-black ' + (imbVal >= 0 ? 'text-emerald-400' : 'text-red-400');

            const sigEntryInput = document.getElementById('sig-entry');
            if (sigEntryInput && !sigEntryInput.dataset.listenerAttached) {
                sigEntryInput.dataset.listenerAttached = "true";
                sigEntryInput.addEventListener('focus', function() {
                    if (!sigEntryInput.value) {
                        preFillSignal(symbol, price);
                    }
                });
            }
        }

        function renderRealLiquidations(symbol, price, liq) {
            const longBox = document.getElementById('long-liqs');
            const shortBox = document.getElementById('short-liqs');
            if (!longBox || !shortBox) return;
            
            longBox.innerHTML = '';
            shortBox.innerHTML = '';

            const leverages = [5, 10, 20, 50, 100];
            let longLiqs = liq.long_liq_levels || [];
            let shortLiqs = liq.short_liq_levels || [];

            if (longLiqs.length === 0 || shortLiqs.length === 0) {
                longLiqs = leverages.map(lev => {
                    const lp = price * (1 - 0.9 / lev);
                    return { leverage: lev, price: lp, distance_pct: ((price - lp) / price) * 100 };
                });
                shortLiqs = leverages.map(lev => {
                    const sp = price * (1 + 0.9 / lev);
                    return { leverage: lev, price: sp, distance_pct: ((sp - price) / price) * 100 };
                });
            }

            longLiqs.forEach(l => {
                const divL = document.createElement('div');
                divL.className = 'flex justify-between text-gray-400';
                divL.innerHTML = `<span>${l.leverage}x Đòn bẩy</span><span class="text-emerald-400 font-bold">$${l.price.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 4})} (${l.distance_pct.toFixed(1)}%)</span>`;
                longBox.appendChild(divL);
            });

            shortLiqs.forEach(s => {
                const divS = document.createElement('div');
                divS.className = 'flex justify-between text-gray-400';
                divS.innerHTML = `<span>${s.leverage}x Đòn bẩy</span><span class="text-red-400 font-bold">$${s.price.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 4})} (${s.distance_pct.toFixed(1)}%)</span>`;
                shortBox.appendChild(divS);
            });

            const cascadeBadge = document.getElementById('cascade-risk-badge');
            const hasCascade = liq.cascade_risk !== undefined ? liq.cascade_risk : false;
            cascadeBadge.innerText = hasCascade ? 'HIGH RISK' : 'NORMAL';
            cascadeBadge.className = 'px-2 py-0.5 rounded text-[10px] font-black ' + (hasCascade ? 'bg-red-500/20 text-red-400 animate-pulse' : 'bg-emerald-500/10 text-emerald-400');

            const dominantSide = liq.dominant_side || "NEUTRAL";
            const domVal = document.getElementById('dominant-side-val');
            if (dominantSide === "LONG") {
                domVal.innerText = '🔼 LONG (BULLS DOMINANT)';
                domVal.className = 'font-bold text-xs text-emerald-400';
            } else if (dominantSide === "SHORT") {
                domVal.innerText = '🔽 SHORT (BEARS DOMINANT)';
                domVal.className = 'font-bold text-xs text-red-400';
            } else {
                domVal.innerText = '↕️ NEUTRAL (BALANCED MARKET)';
                domVal.className = 'font-bold text-xs text-gray-400';
            }
            
            const spread = liq.spread_pct || 0.0012;
            document.getElementById('spread-pct-val').innerText = spread.toFixed(4) + '%';
            document.getElementById('spread-pct-val').className = 'text-gray-200 font-bold';
        }

        function renderSimulatedDepth(symbol) {
            let basePrice = 92850.5;
            if (symbol.includes("ETH")) basePrice = 3500.0;
            else if (symbol.includes("SOL")) basePrice = 145.0;
            else if (symbol.includes("BNB")) basePrice = 580.0;
            else if (symbol.includes("ADA")) basePrice = 0.38;
            else if (symbol.includes("XRP")) basePrice = 0.58;
            else if (symbol.includes("DOGE")) basePrice = 0.12;

            if (lastSimulatedPrice === null || Math.abs(lastSimulatedPrice - basePrice) / basePrice > 0.5) {
                lastSimulatedPrice = basePrice;
            }
            lastSimulatedPrice += (Math.random() - 0.5) * (basePrice * 0.001);

            renderRealOrderbook(symbol, lastSimulatedPrice, { ok: false });
            renderRealLiquidations(symbol, lastSimulatedPrice, { ok: false });
        }

        // Initialize Page
        checkAuth();
    </script>
</body>
</html>
"""


@app.get("/admin", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
def get_dashboard_admin(request: Request, token: str = Query(default="")):
    return HTMLResponse(content=ADMIN_DASHBOARD_HTML, status_code=200)


MINIAPP_CONNECT_HTML = """<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />
    <title>SignalBot API Registration</title>
    <!-- Tailwind CSS -->
    <script src="https://cdn.tailwindcss.com"></script>
    <!-- Google Fonts: Inter & Space Grotesk -->
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght=400;500;600;700&family=Space+Grotesk:wght=500;700&family=JetBrains+Mono&display=swap" rel="stylesheet">
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    fontFamily: {
                        sans: ['Inter', 'sans-serif'],
                        display: ['Space Grotesk', 'sans-serif'],
                        mono: ['JetBrains Mono', 'monospace'],
                    }
                }
            }
        }
    </script>
    <style>
        body {
            font-family: 'Inter', sans-serif;
            background-color: #030712;
            color: #f3f4f6;
            -webkit-tap-highlight-color: transparent;
        }
    </style>
</head>
<body class="flex flex-col min-h-screen px-4 py-6 justify-center">
    <div class="max-w-md w-full mx-auto bg-gray-900/60 border border-gray-800/80 rounded-2xl p-6 shadow-2xl backdrop-blur-xl">
        <!-- Header -->
        <div class="text-center mb-8">
            <div class="inline-flex items-center justify-center w-12 h-12 rounded-xl bg-emerald-500/10 text-emerald-400 mb-3 border border-emerald-500/20">
                <svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z" />
                </svg>
            </div>
            <h1 class="text-2xl font-bold font-display tracking-tight text-white mb-1">SignalBot Auto-Trade</h1>
            <p class="text-sm text-gray-400">Kết nối API BingX để tự động copy trade real-time</p>
        </div>

        <!-- Form -->
        <form id="registerForm" onsubmit="handleRegister(event)" class="space-y-5">
            <div>
                <label class="block text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Telegram UID</label>
                <input type="text" id="telegram_id" required 
                    class="w-full bg-gray-950 border border-gray-800 rounded-xl px-4 py-3 text-white font-mono placeholder-gray-600 focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/50 transition-all"
                    placeholder="Nhập Telegram UID của bạn...">
            </div>

            <div>
                <label class="block text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">BingX API Key</label>
                <input type="text" id="api_key" required 
                    class="w-full bg-gray-950 border border-gray-800 rounded-xl px-4 py-3 text-white font-mono placeholder-gray-600 focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/50 transition-all"
                    placeholder="Dán API Key...">
            </div>

            <div>
                <label class="block text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">BingX API Secret</label>
                <input type="password" id="api_secret" required 
                    class="w-full bg-gray-950 border border-gray-800 rounded-xl px-4 py-3 text-white font-mono placeholder-gray-600 focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/50 transition-all"
                    placeholder="Dán API Secret...">
            </div>

            <div class="pt-2">
                <button type="submit" id="submitBtn"
                    class="w-full bg-emerald-500 hover:bg-emerald-600 text-gray-950 font-bold py-3.5 rounded-xl shadow-lg shadow-emerald-500/20 active:scale-[0.98] transition-all flex items-center justify-center space-x-2">
                    <span>Kết Nối Tài Khoản</span>
                </button>
            </div>
        </form>

        <!-- Result Box -->
        <div id="resultBox" class="hidden mt-6 p-4 rounded-xl border"></div>
    </div>

    <!-- Script -->
    <script>
        // Auto fill UID from query params
        window.addEventListener('DOMContentLoaded', () => {
            const params = new URLSearchParams(window.location.search);
            const uid = params.get('uid') || params.get('telegram_id') || params.get('id');
            if (uid) {
                document.getElementById('telegram_id').value = uid;
            }
        });

        async function handleRegister(e) {
            e.preventDefault();
            const submitBtn = document.getElementById('submitBtn');
            const resultBox = document.getElementById('resultBox');
            
            const telegram_id = document.getElementById('telegram_id').value.trim();
            const api_key = document.getElementById('api_key').value.trim();
            const api_secret = document.getElementById('api_secret').value.trim();
            
            submitBtn.disabled = true;
            submitBtn.innerHTML = `
                <svg class="animate-spin h-5 w-5 mr-3 text-gray-950" viewBox="0 0 24 24">
                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" fill="none"></circle>
                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                <span>Đang kết nối...</span>
            `;
            
            resultBox.classList.add('hidden');
            
            try {
                const response = await fetch('/api/users/register', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ telegram_id, api_key, api_secret })
                });
                
                const data = await response.json();
                
                if (response.ok) {
                    resultBox.className = "mt-6 p-4 rounded-xl bg-emerald-500/10 border-emerald-500/20 text-emerald-400 space-y-2";
                    resultBox.innerHTML = `
                        <div class="font-bold flex items-center space-x-2">
                            <svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                            </svg>
                            <span>Kết Nối Thành Công!</span>
                        </div>
                        <p class="text-xs text-gray-300">Tài khoản của bạn đã được liên kết với hệ thống auto-trade.</p>
                        <div class="pt-2 text-xs border-t border-emerald-500/10 space-y-1">
                            <div>• Tier: <span class="font-bold text-white">${data.label || data.tier}</span></div>
                            <div>• Số dư: <span class="font-bold text-white">${data.capital}</span></div>
                            <div>• Đòn bẩy tối đa: <span class="font-bold text-white">${data.leverage || 'Tự động'}x</span></div>
                        </div>
                    `;
                } else {
                    throw new Error(data.detail || 'Không thể liên kết API Key. Vui lòng kiểm tra lại.');
                }
            } catch (err) {
                resultBox.className = "mt-6 p-4 rounded-xl bg-red-500/10 border-red-500/20 text-red-400 space-y-1 text-sm";
                resultBox.innerHTML = `
                    <div class="font-bold flex items-center space-x-2">
                        <svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                        </svg>
                        <span>Lỗi Kết Nối</span>
                    </div>
                    <p class="text-xs text-gray-300">${err.message}</p>
                `;
            } finally {
                submitBtn.disabled = false;
                submitBtn.innerHTML = '<span>Kết Nối Tài Khoản</span>';
                resultBox.classList.remove('hidden');
            }
        }
    </script>
</body>
</html>
"""

MINIAPP_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />
    <title>SignalBot User Dashboard</title>
    <!-- Tailwind CSS -->
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght=400;500;600;700&family=Space+Grotesk:wght=500;700&family=JetBrains+Mono&display=swap" rel="stylesheet">
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    fontFamily: {
                        sans: ['Inter', 'sans-serif'],
                        display: ['Space Grotesk', 'sans-serif'],
                        mono: ['JetBrains Mono', 'monospace'],
                    }
                }
            }
        }
    </script>
    <style>
        body {
            font-family: 'Inter', sans-serif;
            background-color: #030712;
            color: #f3f4f6;
            -webkit-tap-highlight-color: transparent;
        }
        .shimmer {
            background: linear-gradient(90deg, #1f2937 25%, #374151 50%, #1f2937 75%);
            background-size: 200% 100%;
            animation: loading 1.5s infinite;
        }
        @keyframes loading {
            0% { background-position: 200% 0; }
            100% { background-position: -200% 0; }
        }
    </style>
</head>
<body class="min-h-screen px-4 py-6">
    <div class="max-w-md mx-auto space-y-6">
        <!-- Header -->
        <div class="flex items-center justify-between">
            <div class="flex items-center space-x-3">
                <div class="w-10 h-10 rounded-xl bg-emerald-500/10 flex items-center justify-center border border-emerald-500/20 text-emerald-400">
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 8v8m-4-5v5m-4-2v2m-2 4h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
                    </svg>
                </div>
                <div>
                    <h1 class="text-lg font-bold font-display text-white">My Dashboard</h1>
                    <p class="text-xs text-gray-400" id="uid-display">Telegram: ...</p>
                </div>
            </div>
            <button onclick="loadData()" class="p-2 rounded-xl bg-gray-900 border border-gray-800 text-gray-400 hover:text-white transition-all">
                <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 1121.21 4.89M9 11l3-3 3 3m0 0a1 1 0 01-1 1H10a1 1 0 01-1-1z" />
                </svg>
            </button>
        </div>

        <!-- ID Input for testing if opened in standard browser without query param -->
        <div id="uid-input-box" class="hidden p-4 bg-gray-900/40 border border-gray-800 rounded-xl space-y-3">
            <p class="text-xs text-gray-400">Vui lòng nhập Telegram UID để xem dữ liệu:</p>
            <div class="flex space-x-2">
                <input type="text" id="manual-uid" class="flex-1 bg-gray-950 border border-gray-800 rounded-lg px-3 py-2 text-sm text-white font-mono" placeholder="Nhập Telegram UID...">
                <button onclick="setManualUid()" class="bg-emerald-500 hover:bg-emerald-600 text-gray-950 font-bold px-4 py-2 rounded-lg text-sm">Xem</button>
            </div>
        </div>

        <!-- Profile Overview Card -->
        <div class="bg-gray-900/60 border border-gray-800 rounded-2xl p-5 space-y-4 shadow-xl backdrop-blur-md">
            <div class="grid grid-cols-2 gap-4">
                <div class="bg-gray-950/60 p-3.5 rounded-xl border border-gray-800/60">
                    <span class="text-xs text-gray-400 block mb-1">Số dư (Equity)</span>
                    <span class="text-lg font-bold text-white font-mono" id="val-balance">$-.--</span>
                </div>
                <div class="bg-gray-950/60 p-3.5 rounded-xl border border-gray-800/60">
                    <span class="text-xs text-gray-400 block mb-1">Cấp độ (Tier)</span>
                    <span class="text-sm font-bold text-emerald-400 uppercase tracking-wider block mt-1" id="val-tier">-</span>
                </div>
            </div>

            <div class="flex items-center justify-between pt-2 border-t border-gray-800/60">
                <div class="flex items-center space-x-2">
                    <span class="w-2.5 h-2.5 rounded-full" id="status-dot"></span>
                    <span class="text-xs text-gray-300" id="val-status">Trạng thái: Đang tải...</span>
                </div>
                <span class="text-xs font-mono text-gray-500" id="val-confidence">Conf tối thiểu: --%</span>
            </div>
        </div>

        <!-- Section: Active Positions -->
        <div>
            <div class="flex items-center justify-between mb-3.5">
                <h2 class="text-sm font-bold uppercase tracking-wider text-gray-400 font-display">Vị thế đang mở</h2>
                <span class="px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-gray-900 border border-gray-800 text-gray-400" id="pos-count">0</span>
            </div>

            <!-- Position List -->
            <div id="position-list" class="space-y-3">
                <!-- Loading Shimmer -->
                <div class="shimmer h-24 rounded-xl opacity-25"></div>
            </div>
        </div>

        <!-- Section: Latest Market Signals -->
        <div>
            <div class="flex items-center justify-between mb-3.5">
                <h2 class="text-sm font-bold uppercase tracking-wider text-gray-400 font-display">Tín hiệu bot mới nhất</h2>
            </div>

            <div id="signal-list" class="space-y-3">
                <div class="shimmer h-20 rounded-xl opacity-10"></div>
            </div>
        </div>
    </div>

    <!-- Script -->
    <script>
        let currentUid = '';

        window.addEventListener('DOMContentLoaded', () => {
            const params = new URLSearchParams(window.location.search);
            currentUid = params.get('uid') || params.get('telegram_id') || params.get('id');
            
            if (!currentUid) {
                document.getElementById('uid-input-box').classList.remove('hidden');
            } else {
                document.getElementById('uid-display').innerText = `Telegram UID: ${currentUid}`;
                loadData();
            }
        });

        function setManualUid() {
            const val = document.getElementById('manual-uid').value.trim();
            if (val) {
                currentUid = val;
                document.getElementById('uid-display').innerText = `Telegram UID: ${currentUid}`;
                document.getElementById('uid-input-box').classList.add('hidden');
                loadData();
            }
        }

        async function loadData() {
            if (!currentUid) return;
            
            try {
                const response = await fetch(`/api/state?uid=${currentUid}`);
                const data = await response.json();
                
                if (data.error) {
                    throw new Error(data.error);
                }

                // Render Summary
                document.getElementById('val-balance').innerText = `$${(data.stats.equity || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
                document.getElementById('val-tier').innerText = data.tier_label || data.tier;
                document.getElementById('val-confidence').innerText = `Conf tối thiểu: ${data.min_confidence || 68}%`;
                
                const statusDot = document.getElementById('status-dot');
                const statusText = document.getElementById('val-status');
                
                if (data.auto_trade && !data.kill_switch) {
                    statusDot.className = "w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse";
                    statusText.innerText = "Trạng thái: Hoạt động (Auto)";
                } else if (data.kill_switch) {
                    statusDot.className = "w-2.5 h-2.5 rounded-full bg-red-500";
                    statusText.innerText = "Trạng thái: Kill Switch ĐANG BẬT";
                } else {
                    statusDot.className = "w-2.5 h-2.5 rounded-full bg-amber-500";
                    statusText.innerText = "Trạng thái: Đang Tắt (Hủy Kích Hoạt)";
                }

                // Render Positions
                const posList = document.getElementById('position-list');
                const posCount = document.getElementById('pos-count');
                const positions = data.positions || [];
                
                posCount.innerText = positions.length;
                
                if (positions.length === 0) {
                    posList.innerHTML = `
                        <div class="text-center py-8 bg-gray-900/30 border border-gray-800/40 rounded-xl">
                            <span class="text-gray-600 block text-2xl mb-1">📦</span>
                            <span class="text-xs text-gray-500">Chưa có vị thế nào được mở.</span>
                        </div>
                    `;
                } else {
                    posList.innerHTML = '';
                    positions.forEach(p => {
                        const sideBg = p.direction === 'LONG' ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' : 'bg-red-500/10 text-red-400 border-red-500/20';
                        const pnlColor = p.pnl_pct >= 0 ? 'text-emerald-400' : 'text-red-400';
                        const pnlSign = p.pnl_pct >= 0 ? '+' : '';
                        
                        const div = document.createElement('div');
                        div.className = "bg-gray-900 border border-gray-800 rounded-xl p-4 shadow-md space-y-3";
                        div.innerHTML = `
                            <div class="flex items-center justify-between">
                                <div class="flex items-center space-x-2">
                                    <span class="text-sm font-bold font-display text-white">${p.symbol}</span>
                                    <span class="px-2 py-0.5 rounded text-[10px] font-bold border ${sideBg}">${p.direction}</span>
                                </div>
                                <button onclick="closePosition('${p.symbol}')" class="px-2.5 py-1 text-[10px] font-bold bg-red-500/10 text-red-400 hover:bg-red-500/20 rounded-lg border border-red-500/20 transition-all">Đóng</button>
                            </div>
                            
                            <div class="grid grid-cols-3 gap-2 pt-2 border-t border-gray-800/60 text-center">
                                <div>
                                    <span class="text-[10px] text-gray-500 block">Vào lệnh</span>
                                    <span class="text-xs font-semibold text-gray-300 font-mono">$${p.entry.toFixed(4)}</span>
                                </div>
                                <div>
                                    <span class="text-[10px] text-gray-500 block">Hiện tại</span>
                                    <span class="text-xs font-semibold text-gray-300 font-mono">$${p.current_price.toFixed(4)}</span>
                                </div>
                                <div>
                                    <span class="text-[10px] text-gray-500 block">Lợi nhuận (PnL)</span>
                                    <span class="text-xs font-bold ${pnlColor} font-mono">${pnlSign}${p.pnl_pct}%</span>
                                </div>
                            </div>
                        `;
                        posList.appendChild(div);
                    });
                }

                // Render Signals
                const sigList = document.getElementById('signal-list');
                const signals = data.signals || [];
                
                if (signals.length === 0) {
                    sigList.innerHTML = `
                        <div class="text-center py-6 bg-gray-900/30 border border-gray-800/40 rounded-xl">
                            <span class="text-xs text-gray-600">Không có tín hiệu gần đây.</span>
                        </div>
                    `;
                } else {
                    sigList.innerHTML = '';
                    signals.slice(0, 3).forEach(s => {
                        const sideBg = s.final === 'LONG' ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400';
                        
                        const div = document.createElement('div');
                        div.className = "bg-gray-900/40 border border-gray-800 rounded-xl p-3 flex items-center justify-between text-xs";
                        div.innerHTML = `
                            <div class="space-y-1">
                                <div class="flex items-center space-x-2">
                                    <span class="font-bold text-white">${s.symbol}</span>
                                    <span class="px-1.5 py-0.5 rounded text-[9px] font-bold ${sideBg}">${s.final}</span>
                                </div>
                                <div class="text-[10px] text-gray-500">Confidence: ${s.confidence}% | Entry: ${s.plan?.entry || '-'}</div>
                            </div>
                            <span class="text-[10px] font-mono text-gray-600">${s.timestamp || 'Mới'}</span>
                        `;
                        sigList.appendChild(div);
                    });
                }

            } catch (err) {
                console.error("Dashboard error:", err);
            }
        }

        async function closePosition(symbol) {
            if (!confirm(`Bạn có chắc muốn đóng ngay vị thế ${symbol} bằng lệnh MARKET?`)) {
                return;
            }
            
            try {
                const response = await fetch('/api/user/close', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ user_id: currentUid, symbol })
                });
                
                const data = await response.json();
                alert(data.msg || 'Yêu cầu đóng vị thế đã được gửi.');
                setTimeout(loadData, 1500);
            } catch (err) {
                alert('Có lỗi xảy ra: ' + err.message);
            }
        }
    </script>
</body>
</html>
"""

@app.get("/miniapp/connect", response_class=HTMLResponse)
def get_miniapp_connect(request: Request):
    return HTMLResponse(content=MINIAPP_CONNECT_HTML, status_code=200)


@app.get("/my-dashboard", response_class=HTMLResponse)
@app.get("/miniapp/dashboard", response_class=HTMLResponse)
def get_miniapp_dashboard(request: Request):
    return HTMLResponse(content=MINIAPP_DASHBOARD_HTML, status_code=200)

if __name__ == "__main__":
    # Lấy port từ Render (mặc định là 10000 nếu chạy cục bộ)
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
