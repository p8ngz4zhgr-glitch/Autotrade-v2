"""
SignalBot v6.1 — Main API
"""

import os
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

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core_api.models import SessionLocal, User, TradeJournal
from core_api.security import encrypt_api_secret, decrypt_api_secret
from analyzer.main_scanner import SignalBot
from worker.bingx_trader import BingXExchange
from analyzer.telegram_bot import TelegramBot

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("MainAPI")

app = FastAPI(title="SignalBot v6.1")

# ══════════════════════════════════════════════════════════════════
# TIER CONFIG — TRUNG TÂM HỆ THỐNG
# ══════════════════════════════════════════════════════════════════
TIER_CONFIG = {
    "TIER1": {
        "label": "Ca Con", "min_capital": 0, "max_capital": 500,
        "min_confidence": 68.0, "max_risk_pct": 2.0,
        "max_positions": 2, "leverage": 5, "target_monthly": "5-8%",
    },
    "TIER2": {
        "label": "Tieu Chuan", "min_capital": 500, "max_capital": 2000,
        "min_confidence": 75.0, "max_risk_pct": 1.5,
        "max_positions": 3, "leverage": 5, "target_monthly": "4-6%",
    },
    "TIER3": {
        "label": "Ca Map", "min_capital": 2000, "max_capital": float("inf"),
        "min_confidence": 80.0, "max_risk_pct": 1.0,
        "max_positions": 5, "leverage": 3, "target_monthly": "3-5%",
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
    cfg = TIER_CONFIG[tier]
    user.tier           = tier
    user.min_confidence = cfg["min_confidence"]
    user.max_risk_pct   = cfg["max_risk_pct"]
    user.max_positions  = cfg["max_positions"]
    user.leverage       = cfg["leverage"]


# ══════════════════════════════════════════════════════════════════
# ENV & REDIS
# ══════════════════════════════════════════════════════════════════
REDIS_URL       = os.getenv("REDIS_URL", "redis://localhost:6379/0")
ADMIN_SECRET    = os.getenv("ADMIN_SECRET", "admin123")
RENDER_URL      = os.getenv("RENDER_EXTERNAL_URL", "") or os.getenv("APP_URL", "")
ADMIN_CHAT_ID   = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")
REPORT_TOKEN    = os.getenv("TELEGRAM_REPORT_TOKEN", "")
REGISTER_TOKEN  = os.getenv("TELEGRAM_REGISTER_TOKEN", "")
TG_BASE         = "https://api.telegram.org"

try:
    _rc_kwargs = {"decode_responses": True}
    if REDIS_URL.startswith("rediss://"):
        _rc_kwargs["ssl_cert_reqs"] = "none"
    redis_client = redis.from_url(REDIS_URL, **_rc_kwargs)
    redis_client.ping()
    log.info("Redis OK")
except Exception as e:
    redis_client = None
    log.error("Redis error: %s", e)


def _redis_get(key, default=None):
    if not redis_client:
        return default
    try:
        v = redis_client.get(key)
        return json.loads(v) if v else default
    except Exception:
        return default


def _redis_set(key, value, ex=86400 * 30):
    if not redis_client:
        return
    try:
        redis_client.set(key, json.dumps(value), ex=ex)
    except Exception as e:
        log.error("Redis set %s: %s", key, e)


# ══════════════════════════════════════════════════════════════════
# GLOBAL STATE
# ══════════════════════════════════════════════════════════════════
LIVE_POSITIONS: list = []
_POS_LOCK = threading.Lock()
BOT_GLOBAL_AUTO = True
BOT_KILL_SWITCH = False


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_bx(user: User) -> BingXExchange:
    secret = decrypt_api_secret(user.api_secret_encrypted)
    return BingXExchange(user.api_key, secret)


# ══════════════════════════════════════════════════════════════════
# TELEGRAM HELPERS
# ══════════════════════════════════════════════════════════════════
def _tg_send(token: str, chat_id, text: str, parse_mode="HTML"):
    if not token or not chat_id:
        return
    try:
        _req.post(
            f"{TG_BASE}/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text[:4096],
                  "parse_mode": parse_mode, "disable_web_page_preview": True},
            timeout=10)
    except Exception as e:
        log.warning("_tg_send: %s", e)


def _tg_send_inline(token: str, chat_id, text: str, keyboard: dict):
    if not token or not chat_id:
        return
    try:
        _req.post(
            f"{TG_BASE}/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text[:4096],
                  "parse_mode": "HTML", "reply_markup": json.dumps(keyboard)},
            timeout=10)
    except Exception as e:
        log.warning("_tg_send_inline: %s", e)


def notify_admin(text: str):
    _tg_send(REPORT_TOKEN, ADMIN_CHAT_ID, text)


# ══════════════════════════════════════════════════════════════════
# WEEKLY REPORT — Bot 3
# ══════════════════════════════════════════════════════════════════
def _send_weekly_report():
    try:
        db = SessionLocal()
        since = datetime.utcnow() - timedelta(days=7)
        journals = db.query(TradeJournal).filter(TradeJournal.timestamp >= since).all()
        users = db.query(User).filter(User.is_active == True).all()
        db.close()

        _send_pnl_report("TUẦN", journals, users, since)
        log.info("Weekly report sent")
    except Exception as e:
        log.error("weekly_report: %s", e)


def _send_pnl_report(period_label: str, journals, users, since_dt=None):
    total_users  = len(users)
    tier_counts  = {"TIER1": 0, "TIER2": 0, "TIER3": 0}
    tier_capital = {"TIER1": 0.0, "TIER2": 0.0, "TIER3": 0.0}
    
    for u in users:
        t = u.tier or "TIER1"
        tier_counts[t]  += 1
        tier_capital[t] += u.capital or 0

    tier_stats = {t: {"wins": 0, "losses": 0, "tp1": 0, "tp2": 0, "sl": 0,
                      "manual": 0, "pnl_usd": 0.0, "pnl_pcts": [],
                      "best": None, "worst": None} for t in TIER_CONFIG}

    sym_stats: dict = {}

    for j in journals:
        t = j.tier or "TIER1"
        if t not in tier_stats:
            continue
        pnl = j.pnl_usd or 0
        pct = j.pnl_pct or 0

        tier_stats[t]["pnl_usd"] += pnl
        tier_stats[t]["pnl_pcts"].append(pct)

        out = (j.outcome or "").upper()
        if pct > 0:
            tier_stats[t]["wins"] += 1
        else:
            tier_stats[t]["losses"] += 1

        if "TP2" in out:   tier_stats[t]["tp2"] += 1
        elif "TP1" in out: tier_stats[t]["tp1"] += 1
        elif "SL" in out:  tier_stats[t]["sl"] += 1
        else:              tier_stats[t]["manual"] += 1

        if tier_stats[t]["best"] is None or pct > (tier_stats[t]["best"].pnl_pct or 0):
            tier_stats[t]["best"] = j
        if tier_stats[t]["worst"] is None or pct < (tier_stats[t]["worst"].pnl_pct or 0):
            tier_stats[t]["worst"] = j

        sym = j.symbol or "?"
        if sym not in sym_stats:
            sym_stats[sym] = {"wins": 0, "losses": 0, "pnl": 0.0}
        sym_stats[sym]["pnl"] += pnl
        if pct > 0:
            sym_stats[sym]["wins"] += 1
        else:
            sym_stats[sym]["losses"] += 1

    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    since_str = since_dt.strftime("%d/%m") if since_dt else "?"

    lines = [
        f"📊 <b>BÁO CÁO {period_label} — SignalBot v6.1</b>",
        f"🗓 {since_str} → {now}",
        f"👥 Tổng users: <b>{total_users}</b> | Tổng vốn: <b>${sum(tier_capital.values()):,.0f}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    tier_icons = {"TIER1": "🐟", "TIER2": "🐠", "TIER3": "🦈"}

    for tier in ["TIER1", "TIER2", "TIER3"]:
        cfg = TIER_CONFIG[tier]
        cnt = tier_counts[tier]
        cap = tier_capital[tier]
        st  = tier_stats[tier]
        total_trades = st["wins"] + st["losses"]
        
        if total_trades == 0:
            lines.append(f"\n{tier_icons[tier]} <b>{tier}</b> — {cnt} users — Chưa có lệnh")
            continue

        wr = round(st["wins"] / total_trades * 100, 1)
        avg_pct = round(sum(st["pnl_pcts"]) / len(st["pnl_pcts"]), 2) if st["pnl_pcts"] else 0
        pnl_sign = "+" if st["pnl_usd"] >= 0 else ""
        wr_icon = "🟢" if wr >= 60 else "🟡" if wr >= 45 else "🔴"

        lines.extend([
            "",
            f"{tier_icons[tier]} <b>{tier}</b> — {cnt} users | Vốn: ${cap:,.0f}",
            f"  📈 Tổng: <b>{total_trades}</b> lệnh | {wr_icon} WinRate: <b>{wr}%</b>",
            f"  💰 P&L: <b>{pnl_sign}${st['pnl_usd']:.2f}</b> | Avg: {avg_pct:+.2f}%/lệnh",
            f"  🎯 TP2: {st['tp2']} | TP1: {st['tp1']} | 🛑 SL: {st['sl']} | Hand: {st['manual']}",
        ])

        sl_rate = round(st["sl"] / total_trades * 100, 1) if total_trades > 0 else 0
        if sl_rate > 50:
            lines.append(f"  ⚠️ SL rate cao: <b>{sl_rate}%</b> — cần xem lại strategy")
        elif sl_rate <= 30:
            lines.append(f"  ✅ SL rate tốt: {sl_rate}%")

        if st["best"] and (st["best"].pnl_pct or 0) > 0:
            lines.append(f"  🏆 Best: {st['best'].symbol} +{st['best'].pnl_pct:.2f}%")
        if st["worst"] and (st["worst"].pnl_pct or 0) < 0:
            lines.append(f"  💀 Worst: {st['worst'].symbol} {st['worst'].pnl_pct:.2f}%")

    if sym_stats:
        sorted_syms = sorted(sym_stats.items(), key=lambda x: x[1]["pnl"], reverse=True)
        lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("🏅 <b>TOP SYMBOLS</b>")
        for sym, st in sorted_syms[:5]:
            total = st["wins"] + st["losses"]
            wr = round(st["wins"] / total * 100) if total > 0 else 0
            sign = "+" if st["pnl"] >= 0 else ""
            lines.append(f"  {sym}: {sign}${st['pnl']:.2f} | WR {wr}% ({total} trades)")

    total_pnl = sum(tier_stats[t]["pnl_usd"] for t in TIER_CONFIG)
    all_trades = sum(tier_stats[t]["wins"] + tier_stats[t]["losses"] for t in TIER_CONFIG)
    all_wins   = sum(tier_stats[t]["wins"] for t in TIER_CONFIG)
    all_sl     = sum(tier_stats[t]["sl"] for t in TIER_CONFIG)

    lines.extend([
        "\n━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 <b>TỔNG HỢP</b>",
        f"  Tổng lệnh: {all_trades} | WR: {round(all_wins/all_trades*100,1) if all_trades else 0}%",
        f"  SL count: {all_sl}/{all_trades} ({round(all_sl/all_trades*100,1) if all_trades else 0}%)",
        f"  Tổng P&L: <b>{'+'if total_pnl>=0 else ''}${total_pnl:.2f}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        "🤖 <i>SignalBot v6.1 — Auto Report</i>",
    ])

    _tg_send(REPORT_TOKEN, ADMIN_CHAT_ID, "\n".join(lines))


def _send_daily_report():
    try:
        db = SessionLocal()
        since = datetime.utcnow() - timedelta(days=1)
        journals = db.query(TradeJournal).filter(TradeJournal.timestamp >= since).all()
        users = db.query(User).filter(User.is_active == True).all()
        db.close()
        
        if not journals:
            _tg_send(REPORT_TOKEN, ADMIN_CHAT_ID, "📊 <b>BÁO CÁO NGÀY</b>\n\nHôm nay không có lệnh nào được thực thi.")
            return
            
        _send_pnl_report("NGÀY", journals, users, since)
        log.info("Daily report sent")
    except Exception as e:
        log.error("daily_report: %s", e)


def _schedule_weekly_report():
    schedule.every().sunday.at("19:00").do(_send_weekly_report)
    schedule.every().day.at("23:00").do(_send_daily_report)
    log.info("📅 Report scheduler: Daily 23:00 + Weekly Sun 19:00")
    while True:
        schedule.run_pending()
        time.sleep(60)


# ══════════════════════════════════════════════════════════════════
# BALANCE & TIER MANAGEMENT
# ══════════════════════════════════════════════════════════════════
def _update_user_balance_and_tier(user: User, new_capital: float, db) -> bool:
    old_tier    = user.tier
    old_capital = user.capital
    user.capital              = round(new_capital, 2)
    user.last_balance_update  = datetime.utcnow()

    if new_capital < MIN_CAPITAL_TO_TRADE:
        if not user.is_locked:
            user.is_locked  = True
            user.auto_trade = False
            db.commit()
            notify_admin(
                f"⚠️ <b>User rút tiền!</b>\n"
                f"👤 UID: <code>{user.telegram_id}</code>\n"
                f"💰 Vốn: ${old_capital:.2f} → ${new_capital:.2f}\n"
                f"🔒 Auto-trade đã TẮT. Cần đăng ký lại.")
            _tg_send(
                REGISTER_TOKEN, user.telegram_id,
                "⚠️ <b>Tài khoản BingX của bạn không đủ số dư tối thiểu.</b>\n\n"
                f"Số dư hiện tại: <b>${new_capital:.2f}</b> (cần tối thiểu ${MIN_CAPITAL_TO_TRADE:.0f})\n\n"
                "Auto-trade đã được <b>TẮT</b>. Vui lòng nạp tiền và đăng ký lại.")
        return False

    new_tier = get_tier(new_capital)
    if not new_tier:
        return False

    apply_tier(user, new_tier)
    user.is_locked  = False
    user.auto_trade = True

    tier_changed = (new_tier != old_tier)
    if tier_changed:
        cfg = TIER_CONFIG[new_tier]
        old_min = TIER_CONFIG.get(old_tier, {}).get("min_capital", 0)
        direction = "⬆️ Nâng" if cfg["min_capital"] > old_min else "⬇️ Hạ"
        notify_admin(
            f"{direction} <b>Tier!</b> User <code>{user.telegram_id}</code>\n"
            f"💰 Vốn: ${old_capital:.0f} → ${new_capital:.0f}\n"
            f"📊 Tier: {old_tier} → {new_tier} {cfg['label']}\n"
            f"🎯 Confidence mới: {cfg['min_confidence']}%\n"
            f"⚡ Leverage: {cfg['leverage']}x | Risk: {cfg['max_risk_pct']}%/lệnh")
        _tg_send(
            REGISTER_TOKEN, user.telegram_id,
            f"📊 <b>Tài khoản của bạn đã được cập nhật!</b>\n\n"
            f"💰 Số dư: <b>${new_capital:.2f}</b>\n"
            f"🏷 Phân loại: <b>{cfg['label']}</b>\n"
            f"🎯 Ngưỡng tin cậy: <b>{cfg['min_confidence']}%</b>\n"
            f"📈 Target hàng tháng: <b>{cfg['target_monthly']}</b>\n\n"
            "Bot đã tự động điều chỉnh cấu hình rủi ro cho bạn!")
    db.commit()
    return tier_changed


def _cleanup_inactive_users():
    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=7)
        inactive = db.query(User).filter(
            User.capital < MIN_CAPITAL_TO_TRADE,
            User.last_balance_update < cutoff,
        ).all()
        for u in inactive:
            log.info("Xoa user %s (capital=%.2f)", u.telegram_id, u.capital)
            db.delete(u)
        if inactive:
            db.commit()
            log.info("Da xoa %d users khong du dieu kien", len(inactive))
    except Exception as e:
        db.rollback()
        log.error("cleanup_users: %s", e)
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════
# TP1 PARTIAL CLOSE MONITOR
# ══════════════════════════════════════════════════════════════════
def _tp1_monitor():
    log.info("TP1 Monitor khởi động...")
    while True:
        try:
            with _POS_LOCK:
                positions = list(LIVE_POSITIONS)

            for pos in positions:
                uid       = str(pos.get("user_id", ""))
                symbol    = pos.get("symbol", "")
                direction = pos.get("direction", "")
                entry     = float(pos.get("entry", 0))
                tp1       = float(pos.get("tp1", 0))
                tp2       = float(pos.get("tp2", 0))
                current   = float(pos.get("current_price", 0))
                qty       = float(pos.get("qty", 0))

                if not all([uid, symbol, direction, entry, tp1, current, qty]):
                    continue

                tp1_key = f"TP1_DONE:{uid}:{symbol}:{direction}"
                if redis_client:
                    try:
                        if redis_client.get(tp1_key):
                            continue
                    except Exception:
                        pass

                tp1_hit = False
                if direction == "LONG" and tp1 > 0 and current >= tp1:
                    tp1_hit = True
                elif direction == "SHORT" and tp1 > 0 and current <= tp1:
                    tp1_hit = True
                
                if not tp1_hit:
                    continue

                log.info("TP1 HIT: %s %s @ %.4f (TP1=%.4f)", uid, symbol, current, tp1)

                db = SessionLocal()
                try:
                    user = db.query(User).filter(User.telegram_id == uid).first()
                    if not user:
                        continue

                    bx = get_bx(user)
                    res = bx.handle_tp1_hit(
                        symbol=symbol,
                        direction=direction,
                        total_qty=qty,
                        entry_price=entry,
                        tp2_price=tp2
                    )

                    if not res.get("ok"):
                        log.error("TP1 Execution Failed: %s", res.get("msg"))
                        continue

                    if redis_client:
                        try:
                            redis_client.setex(tp1_key, 86400, "1")
                        except Exception:
                            pass

                    pnl_pct = 0.0
                    if entry > 0:
                        pnl_pct = ((tp1 - entry) / entry * 100 if direction == "LONG"
                                   else (entry - tp1) / entry * 100)
                                   
                    pnl_usd = user.capital * (user.max_risk_pct / 100) * pnl_pct / 100

                    if res.get("split", True):
                        half_qty = round(qty * 0.5, 4)
                        remaining = round(qty - half_qty, 4)
                        _tg_send(
                            REGISTER_TOKEN, uid,
                            f"🎯 <b>TP1 HIT — {symbol}!</b>\n\n"
                            f"✅ Đã chốt <b>50%</b> vị thế ({half_qty} {symbol})\n"
                            f"💰 Lãi: <b>+{pnl_pct:.2f}% (+${pnl_usd:.2f})</b>\n\n"
                            f"🔒 SL đã kéo về <b>Entry ${entry:.4f}</b> (Breakeven)\n"
                            f"🚀 Còn {remaining} {symbol} chạy đến TP2 = <b>${tp2:.4f}</b>\n\n"
                            f"<i>Lệnh hiện tại: Không còn rủi ro lỗ vốn!</i>")
                    else:
                        _tg_send(
                            REGISTER_TOKEN, uid,
                            f"🎯 <b>TP1 HIT — {symbol}!</b>\n\n"
                            f"⚠️ Khối lượng quá nhỏ ({qty} {symbol}), không thể chốt 50%\n"
                            f"🔒 SL đã kéo về <b>Entry ${entry:.4f}</b> (Breakeven)\n"
                            f"🚀 Tiếp tục gồng toàn bộ đến TP2 = <b>${tp2:.4f}</b>\n\n"
                            f"<i>Lệnh hiện tại: Không còn rủi ro lỗ vốn!</i>")
                    
                    log.info("TP1 done & notified: %s %s", uid, symbol)

                except Exception as e:
                    log.error("TP1 monitor user %s %s: %s", uid, symbol, e)
                finally:
                    db.close()

        except Exception as e:
            log.error("_tp1_monitor loop: %s", e)
        time.sleep(30)


_LAST_REVERSAL_EVAL = {}

def evaluate_reversal_for_position(user: User, pos: dict, current_price: float, db):
    sym = pos["symbol"]
    direction = pos["direction"]
    qty = float(pos.get("qty", 0))
    entry = float(pos.get("entry", 0))
    
    now = time.time()
    if now - _LAST_REVERSAL_EVAL.get(sym, 0) < 180:
        return
    _LAST_REVERSAL_EVAL[sym] = now
    
    try:
        from analyzer.engine import SignalEngine
        engine = SignalEngine()
        analysis = engine.full_analysis(sym)
        new_direction = analysis.get("final", "WAIT")
        conf = analysis.get("confidence", 0)
        
        is_reversal = (direction == "LONG" and new_direction == "SHORT") or (direction == "SHORT" and new_direction == "LONG")
        
        if is_reversal and conf >= 70:
            bx = get_bx(user)
            in_profit = (direction == "LONG" and current_price > entry) or (direction == "SHORT" and current_price < entry)
            
            pnl_pct = 0.0
            if entry > 0:
                pnl_pct = ((current_price - entry) / entry * 100 if direction == "LONG" else (entry - current_price) / entry * 100)
            
            action_type = "CHỐT LỜI SỚM" if in_profit else "CẮT LỖ SỚM"
            emoji = "💰" if in_profit else "⚠️"
            
            log.info("🚨 Reversal detected for %s %s: %s", user.telegram_id, sym, action_type)
            
            res = bx.close_position(sym, qty, direction)
            if res.get("ok"):
                if redis_client:
                    try:
                        redis_client.setex(f"REVERSAL_CLOSED:{user.telegram_id}:{sym}:{direction}", 120, "1")
                    except Exception:
                        pass
                _tg_send(
                    REGISTER_TOKEN, user.telegram_id,
                    f"{emoji} <b>{action_type} (REVERSAL): {sym}</b>\n\n"
                    f"🔄 Xu hướng thị trường đã đảo chiều sang <b>{new_direction}</b> (Conf: {conf}%).\n"
                    f"📊 Vị thế cũ: {direction} @ ${entry:.4f}\n"
                    f"📈 Giá hiện tại: ${current_price:.4f} | PnL: {pnl_pct:+.2f}%\n"
                    f"🔒 Đã tự động đóng vị thế cũ để bảo vệ vốn.\n\n"
                    f"⚡ <i>Hệ thống phân tích lại thị trường và đảo lệnh theo xu hướng mới...</i>"
                )
                
                _save_journal(user.telegram_id, sym, direction, pnl_pct, qty)
                
                time.sleep(1.5)
                
                new_entry = float(analysis["plan"]["entry"])
                new_sl    = float(analysis["plan"]["sl"])
                new_tp1   = float(analysis["plan"]["tp1"])
                new_tp2   = float(analysis["plan"].get("tp2", 0))
                if new_tp2 <= 0:
                    new_tp2 = round(new_tp1 + abs(new_tp1 - new_entry), 4)
                
                sl_pct = 0.0
                if new_entry > 0:
                    sl_pct = abs(new_entry - new_sl) / new_entry
                    
                if sl_pct >= 0.001:
                    risk_amt = user.capital * (user.max_risk_pct / 100)
                    new_qty = round(risk_amt / (new_entry * sl_pct), 4)
                    if new_qty > 0:
                        bx.set_leverage(sym, leverage=user.leverage)
                        bx.cancel_all_orders(sym)
                        new_order_res = bx.place_order(sym, "BUY" if new_direction == "LONG" else "SELL", new_qty, new_sl, new_tp2)
                        if new_order_res.get("ok"):
                            _tg_send(
                                REGISTER_TOKEN, user.telegram_id,
                                f"🚀 <b>VÀO LỆNH THEO XU HƯỚNG MỚI: {sym}</b>\n"
                                f"📈 {new_direction} | Conf: {conf:.1f}%\n"
                                f"💰 Qty: {new_qty:.4f} | Lev: {user.leverage}x\n"
                                f"🛑 SL: <code>${new_sl:.4f}</code>\n"
                                f"🎯 TP1: <code>${new_tp1:.4f}</code> | TP2: <code>${new_tp2:.4f}</code>"
                            )
    except Exception as e:
        log.warning("Evaluate reversal for %s %s error: %s", user.telegram_id, sym, e)


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
                        
                        # Evaluate for reversal / early close / lock profit
                        evaluate_reversal_for_position(user, p, cur, db)
                        trig = triggers.get(sym, {})
                        if not isinstance(trig, dict):
                            trig = {}
                        sl   = trig.get("sl",  p.get("entry", 0) * (0.98 if p.get("direction") == "LONG" else 1.02))
                        tp2  = trig.get("tp2", p.get("entry", 0) * (1.05 if p.get("direction") == "LONG" else 0.95))
                        tp1  = p.get("entry", 0) * (1.025 if p.get("direction", "LONG") == "LONG" else 0.975)
                        pnl  = p.get("pnl", 0)
                        margin = user.capital * (user.max_risk_pct / 100)
                        pct  = round(pnl / margin * 100, 2) if margin > 0 else 0

                        pos_key = f"{tid}_{sym}_{p.get('direction', 'LONG')}"
                        current_map[pos_key] = {
                            "direction": p.get("direction", "LONG"), "pct": pct,
                            "qty": p.get("qty", 0), "user_id": tid,
                            "entry": p.get("entry", 0), "sl": sl, "tp2": tp2,
                        }
                        current_all.append({
                            "user_id": tid, "tier": user.tier, "capital": user.capital,
                            "symbol": sym, "direction": p.get("direction", "LONG"), "entry": p.get("entry", 0),
                            "current_price": cur, "pnl": pnl, "pnl_pct": pct,
                            "qty": p.get("qty", 0), "sl": sl, "tp1": tp1, "tp2": tp2,
                        })
                except Exception as e:
                    log.warning("Sync user %s: %s", tid, e)
                    _bx_cache.pop(tid, None)

            prev_map = getattr(sync_bingx_positions, "_prev", {})
            for k, v in prev_map.items():
                if k not in current_map:
                    parts = k.split("_", 2)
                    if len(parts) == 3:
                        user_id, symbol, direction = parts[0], parts[1], parts[2]
                        pnl_pct = v.get("pct", 0)
                        qty = v.get("qty", 0)
                        entry = v.get("entry", 0)
                        sl = v.get("sl", 0)
                        tp2 = v.get("tp2", 0)

                        _save_journal(user_id, symbol, direction, pnl_pct, qty)

                        # Check if this close was already notified by reversal
                        was_reversal = False
                        if redis_client:
                            try:
                                rev_key = f"REVERSAL_CLOSED:{user_id}:{symbol}:{direction}"
                                if redis_client.get(rev_key):
                                    was_reversal = True
                                    redis_client.delete(rev_key)
                            except Exception:
                                pass

                        if not was_reversal:
                            # Send Telegram notification for Closed Position!
                            # Determine if it hit SL, TP2, or was closed manually
                            outcome_emoji = "🏆" if pnl_pct > 0 else "🛑"
                            outcome_text = "CHỐT LỜI THÀNH CÔNG (TP2)" if pnl_pct > 0 else "DỪNG LỖ (SL)"
                            if abs(pnl_pct) < 0.1:
                                outcome_emoji = "🛡️"
                                outcome_text = "HOÀ VỐN / ĐÓNG THỦ CÔNG"

                            pnl_usd = 0
                            try:
                                user_db = db.query(User).filter(User.telegram_id == user_id).first()
                                if user_db:
                                    pnl_usd = user_db.capital * (user_db.max_risk_pct / 100) * pnl_pct / 100
                            except Exception as e:
                                log.error("Error getting user_db for closing notification: %s", e)

                            _tg_send(
                                REGISTER_TOKEN, user_id,
                                f"{outcome_emoji} <b>VỊ THẾ ĐÃ ĐÓNG: {symbol}</b>\n\n"
                                f"📈 Hướng: <b>{direction}</b>\n"
                                f"💰 Khối lượng: {qty:.4f} {symbol}\n"
                                f"📊 PnL: <b>{pnl_pct:+.2f}% ({'+' if pnl_usd >= 0 else ''}${pnl_usd:.2f})</b>\n"
                                f"🛑 SL cũ: <code>${sl:.4f}</code> | 🏆 Target: <code>${tp2:.4f}</code>\n"
                                f"🎯 Kết quả: <b>{outcome_text}</b>"
                            )

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
                  + ("Xu hướng & timing tốt." if result == "WIN"
                     else "Kiểm tra CVD, volume, Wyckoff trước khi vào tiếp."))
        outcome = "TP" if pnl_pct > 0 else "SL"

        db.add(TradeJournal(
            symbol=symbol, user_id=user_id, tier=tier, direction=direction,
            outcome=outcome, pnl_pct=pnl_pct, pnl_usd=pnl_usd,
            context=f"{symbol} {direction} @ {datetime.now().strftime('%d/%m %H:%M')}",
            lesson=lesson))
        if user:
            user.total_pnl = (user.total_pnl or 0) + pnl_usd

        old = (db.query(TradeJournal).filter(TradeJournal.user_id == user_id)
               .order_by(TradeJournal.timestamp.desc()).all())
        if len(old) > 50:
            for r in old[50:]:
                db.delete(r)
        db.commit()

        if redis_client:
            try:
                redis_client.delete(f"TP1_DONE:{user_id}:{symbol}:{direction}")
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
    retry = 0
    while True:
        r = None
        try:
            # Dung cac thong so socket_timeout va socket_keepalive de tranh timeout ngat ket noi voi Upstash/Redis
            r = await aioredis.from_url(
                REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=10,
                socket_timeout=15,
                socket_keepalive=True,
                retry_on_timeout=True
            )
            await r.ping()
            log.info("Worker Redis OK")
            retry = 0
            while True:
                try:
                    # Giam timeout blpop xuong 2 giay de giu cho socket luon hoat dong va tranh socket read timeout (5s)
                    msg = await r.blpop("TRADE_SIGNALS", timeout=2)
                except (redis.exceptions.TimeoutError, asyncio.TimeoutError):
                    # Khi bi timeout doc, kiem tra ket noi bang cach ping, neu ping OK thi tiep tuc, neu loi thi break de reconnect
                    try:
                        await r.ping()
                        continue
                    except Exception:
                        break
                except (redis.exceptions.ConnectionError, redis.exceptions.RedisError):
                    break

                if not msg:
                    continue
                _, data_str = msg
                try:
                    signal = json.loads(data_str)
                except Exception:
                    continue

                await r.lpush("WEB_SIGNALS", data_str)
                await r.lpush("WEB_SIGNALS_RECORD", data_str)
                await r.ltrim("WEB_SIGNALS", 0, 29)
                await r.ltrim("WEB_SIGNALS_RECORD", 0, 99)

                if not BOT_GLOBAL_AUTO or BOT_KILL_SWITCH:
                    continue

                final = signal.get("final", "WAIT")
                conf  = float(signal.get("confidence", 0))
                if final == "WAIT":
                    continue

                db = SessionLocal()
                try:
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

        except (ConnectionRefusedError, OSError) as e:
            retry += 1
            await asyncio.sleep(min(5 * retry, 60))
        except Exception as e:
            retry += 1
            log.error("Worker error: %s", e)
            await asyncio.sleep(min(5 * retry, 60))
        finally:
            if r:
                try:
                    await r.aclose()
                except Exception:
                    pass


def _execute_for_user(user: User, signal: dict):
    try:
        sym       = signal.get("symbol", "")
        direction = signal.get("final", "WAIT")
        if not sym or direction == "WAIT":
            return

        pending_key = f"PENDING:{user.telegram_id}:{sym}"
        if redis_client:
            try:
                if redis_client.get(pending_key):
                    log.info("Cooldown %s %s - skip", user.telegram_id, sym)
                    return
            except Exception:
                pass

        with _POS_LOCK:
            already = [p for p in LIVE_POSITIONS
                       if str(p.get("user_id")) == user.telegram_id and p.get("symbol") == sym]
        if already:
            log.info("Da co vi the %s (user %s) - bo qua", sym, user.telegram_id)
            return

        try:
            bx_live = get_bx(user)
            live    = bx_live.get_open_positions()
            if any(p.get("symbol") == sym for p in live):
                log.info("BingX xac nhan da co vi the %s - bo qua", sym)
                return
        except Exception as e:
            log.warning("BingX realtime check: %s - tiep tuc", e)

        with _POS_LOCK:
            total_pos = sum(1 for p in LIVE_POSITIONS if str(p.get("user_id")) == user.telegram_id)
        if total_pos >= (user.max_positions or 2):
            log.info("Max positions %d da dat (user %s)", user.max_positions, user.telegram_id)
            return

        entry = float(signal["plan"]["entry"])
        sl    = float(signal["plan"]["sl"])
        tp1   = float(signal["plan"]["tp1"])
        tp2   = float(signal["plan"].get("tp2", 0))
        if tp2 <= 0:
            tp2 = round(tp1 + abs(tp1 - entry), 4)
            
        sl_pct = 0.0
        if entry > 0:
            sl_pct = abs(entry - sl) / entry
        else:
            log.warning("Lỗi tín hiệu entry=0: Bỏ qua")
            return
            
        if sl_pct < 0.001:
            log.warning("SL qua gan entry (%.4f%%) - bo qua", sl_pct * 100)
            return

        risk_amt = user.capital * (user.max_risk_pct / 100)
        qty      = round(risk_amt / (entry * sl_pct), 4)
        if qty <= 0:
            return

        bx   = get_bx(user)
        side = "BUY" if direction == "LONG" else "SELL"
        bx.set_leverage(sym, leverage=user.leverage)
        bx.cancel_all_orders(sym)
        res = bx.place_order(sym, side, qty, sl, tp2)

        if res.get("ok"):
            if redis_client:
                try:
                    redis_client.setex(pending_key, 60, "1")
                except Exception:
                    pass

            log.info("OK %s: %s %s qty=%.4f lev=%dx", user.telegram_id, direction, sym, qty, user.leverage)
            _tg_send(
                REGISTER_TOKEN, user.telegram_id,
                f"🚨 <b>LỆNH MỚI: {sym}</b>\n"
                f"📈 {direction} | Conf: {signal.get('confidence',0):.1f}%\n"
                f"💰 Qty: {qty:.4f} | Lev: {user.leverage}x\n"
                f"🛑 SL: <code>${sl:.4f}</code> | Risk: ${risk_amt:.2f}\n"
                f"🎯 TP1: <code>${tp1:.4f}</code> → chốt 50% + SL → Entry\n"
                f"🏆 TP2: <code>${tp2:.4f}</code> → đích 50% còn lại")
        else:
            log.error("BingX loi %s: %s", user.telegram_id, res.get("msg"))

    except Exception as e:
        log.error("_execute_for_user %s: %s", user.telegram_id, e)
# Rest of the file truncated to save space. The remaining API endpoints remain identical.
