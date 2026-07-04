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
RENDER_URL      = os.getenv("RENDER_EXTERNAL_URL", "")
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

                    half_qty = round(qty * 0.5, 4)
                    remaining = round(qty - half_qty, 4)
                    
                    pnl_pct = ((tp1 - entry) / entry * 100 if direction == "LONG"
                               else (entry - tp1) / entry * 100)
                    pnl_usd = user.capital * (user.max_risk_pct / 100) * pnl_pct / 100

                    _tg_send(
                        REGISTER_TOKEN, uid,
                        f"🎯 <b>TP1 HIT — {symbol}!</b>\n\n"
                        f"✅ Đã chốt <b>50%</b> vị thế ({half_qty} {symbol})\n"
                        f"💰 Lãi: <b>+{pnl_pct:.2f}% (+${pnl_usd:.2f})</b>\n\n"
                        f"🔒 SL đã kéo về <b>Entry ${entry:.4f}</b> (Breakeven)\n"
                        f"🚀 Còn {remaining} {symbol} chạy đến TP2 = <b>${tp2:.4f}</b>\n\n"
                        f"<i>Lệnh hiện tại: Không còn rủi ro lỗ vốn!</i>")
                    log.info("TP1 done & notified: %s %s", uid, symbol)

                except Exception as e:
                    log.error("TP1 monitor user %s %s: %s", uid, symbol, e)
                finally:
                    db.close()

        except Exception as e:
            log.error("_tp1_monitor loop: %s", e)
        time.sleep(30)


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

                    for p in positions:
                        sym  = p["symbol"]
                        cur  = bx.get_latest_price(sym) or p["entry"]
                        trig = triggers.get(sym, {})
                        sl   = trig.get("sl",  p["entry"] * (0.98 if p["direction"] == "LONG" else 1.02))
                        tp2  = trig.get("tp2", p["entry"] * (1.05 if p["direction"] == "LONG" else 0.95))
                        tp1  = p["entry"] * (1.025 if p["direction"] == "LONG" else 0.975)
                        pnl  = p["pnl"]
                        margin = user.capital * (user.max_risk_pct / 100)
                        pct  = round(pnl / margin * 100, 2) if margin > 0 else 0

                        pos_key = f"{tid}_{sym}_{p['direction']}"
                        current_map[pos_key] = {
                            "direction": p["direction"], "pct": pct,
                            "qty": p.get("qty", 0), "user_id": tid,
                        }
                        current_all.append({
                            "user_id": tid, "tier": user.tier, "capital": user.capital,
                            "symbol": sym, "direction": p["direction"], "entry": p["entry"],
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
                        _save_journal(parts[0], parts[1], parts[2], v.get("pct", 0), v.get("qty", 0))

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

        sl_pct = abs(entry - sl) / entry
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


def run_trade_worker():
    asyncio.run(_trade_worker_async())


def run_signal_bot():
    try:
        SignalBot().start()
    except Exception as e:
        log.error("SignalBot crash: %s", e)


@app.on_event("startup")
async def startup_event():
    threading.Thread(target=run_signal_bot,         daemon=True, name="signal-bot").start()
    threading.Thread(target=run_trade_worker,       daemon=True, name="trade-worker").start()
    threading.Thread(target=sync_bingx_positions,   daemon=True, name="pos-sync").start()
    threading.Thread(target=_tp1_monitor,           daemon=True, name="tp1-monitor").start()
    threading.Thread(target=_schedule_weekly_report, daemon=True, name="report-bot").start()
    threading.Thread(target=lambda: [time.sleep(600) or gc.collect() for _ in iter(int, 1)],
                     daemon=True, name="gc").start()
    log.info("Tat ca threads khoi dong")


# ══════════════════════════════════════════════════════════════════
# HEALTH CHECK
# ══════════════════════════════════════════════════════════════════
@app.get("/")
def health():
    return {"status": "online", "version": "v6.1",
            "tiers": {t: c["label"] for t, c in TIER_CONFIG.items()}}


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
                    f"⚡ Leverage: <b>{u.leverage}x</b>\n"
                    f"📈 Risk/Lệnh: <b>{u.max_risk_pct}%</b>\n"
                    f"🔄 Auto-trade: <b>{'BẬT' if u.auto_trade else 'TẮT'}</b>\n"
                    f"📊 Total PnL: <b>${u.total_pnl:+.2f}</b>")

        elif text == "/dashboard":
            dash_url = (RENDER_URL or "") + f"/my-dashboard?uid={chat_id}"
            _tg_send_inline(
                REGISTER_TOKEN, chat_id, "📊 Mở Dashboard cá nhân của bạn:",
                {"inline_keyboard": [[{"text": "📊 Dashboard Của Tôi",
                                       "web_app": {"url": dash_url}}]]})

        elif text == "/report":
            threading.Thread(target=_send_daily_report, daemon=True).start()
            _tg_send(REGISTER_TOKEN, chat_id, "📊 Đang tạo báo cáo ngày, vui lòng chờ...")
            
        elif text.startswith("/close "):
            symbol = text.split(" ", 1)[1].strip().upper()
            _handle_user_close(chat_id, symbol)

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
            if redis_client:
                try:
                    redis_client.delete(f"TP1_DONE:{telegram_id}:{symbol}:{direction}")
                    redis_client.delete(f"PENDING:{telegram_id}:{symbol}")
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
# STATE API
# ══════════════════════════════════════════════════════════════════
@app.get("/api/state")
def get_state(request: Request, db: Session = Depends(get_db), uid: str = Query(default="")):
    if uid:
        user = db.query(User).filter(User.telegram_id == uid).first()
        if not user:
            return {"error": "User not found"}
        with _POS_LOCK:
            positions = [p for p in LIVE_POSITIONS if str(p.get("user_id")) == uid]
        cfg = TIER_CONFIG.get(user.tier, TIER_CONFIG["TIER1"])
        return {
            "auto_trade": user.auto_trade, "kill_switch": BOT_KILL_SWITCH,
            "tier": user.tier, "tier_label": cfg["label"],
            "min_confidence": user.min_confidence,
            "stats": {"equity": user.capital, "total_return": 0,
                     "daily_pnl_pct": 0, "total_pnl": user.total_pnl or 0},
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

    return {
        "auto_trade": BOT_GLOBAL_AUTO, "kill_switch": BOT_KILL_SWITCH,
        "stats": {
            "equity": sum(u.capital or 0 for u in users), "total_users": len(users),
            "total_return": 0, "daily_pnl_pct": 0, "win_rate": 0,
            "profit_factor": 0, "drawdown_pct": 0,
        },
        "tier_summary": tier_summary, "positions": positions,
        "signals": _get_signals(), "risk_config": _redis_get("GLOBAL:RISK_CONFIG", {}),
    }


def _get_signals():
    if not redis_client:
        return []
    try:
        raws = redis_client.lrange("WEB_SIGNALS", 0, 19)
        result = []
        for raw in raws:
            d = json.loads(raw)
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
            if rtype == "weekly":
                threading.Thread(target=_send_weekly_report, daemon=True).start()
                return {"ok": True, "msg": "📊 Đang gửi báo cáo TUẦN..."}
            else:
                threading.Thread(target=_send_daily_report, daemon=True).start()
                return {"ok": True, "msg": "📊 Đang gửi báo cáo NGÀY..."}

        if action == "cleanup":
            threading.Thread(target=_cleanup_inactive_users, daemon=True).start()
            return {"ok": True, "msg": "Dang don DB..."}

        if action == "trigger_signal":
            signal_data = cmd.get("signal", {})
            if not signal_data or not signal_data.get("symbol"):
                return {"ok": False, "msg": "Tin hieu thieu thong tin symbol"}
            if redis_client:
                redis_client.rpush("TRADE_SIGNALS", json.dumps(signal_data))
                return {"ok": True, "msg": f"Đã gửi tín hiệu {signal_data.get('symbol')} ({signal_data.get('final')}) vào hàng đợi Redis!"}
            else:
                return {"ok": False, "msg": "Lỗi: Redis không kết nối!"}

        return {"ok": False, "msg": f"Khong ho tro: {action}"}

    except Exception as e:
        return {"ok": False, "msg": str(e)}


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

                    <div class="grid grid-cols-3 gap-2">
                        <button onclick="triggerAction('send_report', {type: 'daily'})" class="py-2 px-1 text-center bg-[#070b16] hover:bg-[#141d2e] rounded border border-[#1b263e] text-[10px] font-black text-[#e2e8f0] transition-all">
                            📊 BÁO CÁO NGÀY
                        </button>
                        <button onclick="triggerAction('send_report', {type: 'weekly'})" class="py-2 px-1 text-center bg-[#070b16] hover:bg-[#141d2e] rounded border border-[#1b263e] text-[10px] font-black text-[#e2e8f0] transition-all">
                            📊 BÁO CÁO TUẦN
                        </button>
                        <button onclick="triggerAction('cleanup')" class="py-2 px-1 text-center bg-[#070b16] hover:bg-[#141d2e] rounded border border-[#1b263e] text-[10px] font-black text-[#e2e8f0] transition-all">
                            🧹 DỌN DẸP DB
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
                                <input type="number" id="form-user-lev" value="5"
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
                            <input type="text" id="sig-symbol" required value="BTCUSDT"
                                   class="w-full bg-[#040811] border border-[#1b263e] rounded p-2 text-xs">
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

                // Render orderbook & simulation
                renderMockOrderbook(state);

                // Render liquidations
                renderMockLiquidations(state);

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

        // Real-time Mock / Calculated orderbook
        let currentMockBasePrice = 92850.5;
        function renderMockOrderbook(state) {
            // Pick a reasonable active base price
            let symbol = document.getElementById('sig-symbol').value || 'BTCUSDT';
            
            // Build a dynamic order book based on last ticker
            const bidContainer = document.getElementById('bids-container');
            const askContainer = document.getElementById('asks-container');
            bidContainer.innerHTML = '';
            askContainer.innerHTML = '';

            const step = 0.5;
            let bids = [];
            let asks = [];
            
            let maxUsd = 0;
            
            // Generate mock orderbook visually around current mock base price
            for (let i = 1; i <= 6; i++) {
                const askPrice = currentMockBasePrice + i * step;
                const askQty = Math.random() * 1.8 + 0.1 + (i === 3 ? 12 : 0); // Wall
                const askUsd = askPrice * askQty;
                asks.push({ price: askPrice, qty: askQty, usd: askUsd });
                if (askUsd > maxUsd) maxUsd = askUsd;

                const bidPrice = currentMockBasePrice - i * step;
                const bidQty = Math.random() * 1.8 + 0.1 + (i === 4 ? 14 : 0); // Wall
                const bidUsd = bidPrice * bidQty;
                bids.push({ price: bidPrice, qty: bidQty, usd: bidUsd });
                if (bidUsd > maxUsd) maxUsd = bidUsd;
            }

            // Asks high to low
            asks.reverse().forEach(ask => {
                const row = document.createElement('div');
                row.className = 'flex justify-between relative py-0.5 px-1 hover:bg-white/5';
                const pct = (ask.usd / maxUsd) * 100;
                row.innerHTML = `
                    <div class="absolute right-0 top-0 bottom-0 bg-red-500/5 transition-all" style="width: ${pct}%"></div>
                    <span class="text-red-400 relative z-10 font-bold">$${ask.price.toFixed(1)}</span>
                    <span class="text-gray-400 relative z-10">${ask.qty.toFixed(3)}</span>
                    <span class="text-gray-600 relative z-10">$${Math.round(ask.usd).toLocaleString()}</span>
                `;
                askContainer.appendChild(row);
            });

            // Bids high to low
            bids.forEach(bid => {
                const row = document.createElement('div');
                row.className = 'flex justify-between relative py-0.5 px-1 hover:bg-white/5';
                const pct = (bid.usd / maxUsd) * 100;
                row.innerHTML = `
                    <div class="absolute right-0 top-0 bottom-0 bg-emerald-500/5 transition-all" style="width: ${pct}%"></div>
                    <span class="text-emerald-400 relative z-10 font-bold">$${bid.price.toFixed(1)}</span>
                    <span class="text-gray-400 relative z-10">${bid.qty.toFixed(3)}</span>
                    <span class="text-gray-600 relative z-10">$${Math.round(bid.usd).toLocaleString()}</span>
                `;
                bidContainer.appendChild(row);
            });

            // Update walls displays
            const bestBidWall = bids.reduce((m, b) => b.usd > m.usd ? b : m, bids[0]);
            const bestAskWall = asks.reduce((m, a) => a.usd > m.usd ? a : m, asks[0]);
            document.getElementById('buy-wall-val').innerText = `$${bestBidWall.price.toFixed(1)} ($${Math.round(bestBidWall.usd).toLocaleString()})`;
            document.getElementById('sell-wall-val').innerText = `$${bestAskWall.price.toFixed(1)} ($${Math.round(bestAskWall.usd).toLocaleString()})`;

            const imbVal = Math.round(((bids.reduce((s, b) => s + b.usd, 0) - asks.reduce((s, a) => s + a.usd, 0)) / (bids.reduce((s, b) => s + b.usd, 0) + asks.reduce((s, a) => s + a.usd, 0))) * 100);
            const imbDisp = document.getElementById('orderbook-imb');
            imbDisp.innerText = 'Imbalance: ' + (imbVal >= 0 ? '+' : '') + imbVal + '%';
            imbDisp.className = 'font-mono text-xs font-black ' + (imbVal >= 0 ? 'text-emerald-400' : 'text-red-400');

            // Float the price slightly
            currentMockBasePrice += (Math.random() - 0.5) * 1.5;

            // Connect form listener
            const clickTrigger = document.createElement('div');
            // Attach a small action so double-clicking pre-fills the signal entry field
            document.getElementById('sig-entry').addEventListener('focus', function() {
                if (!document.getElementById('sig-entry').value) {
                    preFillSignal(symbol, currentMockBasePrice);
                }
            });
        }

        function renderMockLiquidations(state) {
            const longBox = document.getElementById('long-liqs');
            const shortBox = document.getElementById('short-liqs');
            longBox.innerHTML = '';
            shortBox.innerHTML = '';

            const leverages = [5, 10, 20, 50, 100];
            const p = currentMockBasePrice;

            leverages.forEach(lev => {
                const liqLong = p * (1 - 0.9 / lev);
                const distLong = ((p - liqLong) / p) * 100;
                
                const divL = document.createElement('div');
                divL.className = 'flex justify-between text-gray-400';
                divL.innerHTML = `<span>${lev}x Đòn bẩy</span><span class="text-emerald-400 font-bold">$${liqLong.toFixed(1)} (${distLong.toFixed(1)}%)</span>`;
                longBox.appendChild(divL);

                const liqShort = p * (1 + 0.9 / lev);
                const distShort = ((liqShort - p) / p) * 100;

                const divS = document.createElement('div');
                divS.className = 'flex justify-between text-gray-400';
                divS.innerHTML = `<span>${lev}x Đòn bẩy</span><span class="text-red-400 font-bold">$${liqShort.toFixed(1)} (${distShort.toFixed(1)}%)</span>`;
                shortBox.appendChild(divS);
            });

            // Cascade alert logic
            const cascadeBadge = document.getElementById('cascade-risk-badge');
            const hasCascade = Math.random() > 0.8;
            cascadeBadge.innerText = hasCascade ? 'HIGH RISK' : 'NORMAL';
            cascadeBadge.className = 'px-2 py-0.5 rounded text-[10px] font-black ' + (hasCascade ? 'bg-red-500/20 text-red-400 animate-pulse' : 'bg-emerald-500/10 text-emerald-400');

            document.getElementById('dominant-side-val').innerText = Math.random() > 0.55 ? '🔼 LONG (BULLS DOMINANT)' : '🔽 SHORT (BEARS DOMINANT)';
            document.getElementById('dominant-side-val').className = 'font-bold text-xs ' + (Math.random() > 0.55 ? 'text-emerald-400' : 'text-red-400');
            
            document.getElementById('spread-pct-val').innerText = '0.0012%';
            document.getElementById('spread-pct-val').className = 'text-gray-200 font-bold';
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

