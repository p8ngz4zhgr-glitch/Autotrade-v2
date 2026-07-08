""" API"""
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
from sqlalchemy import func

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core_api.models import SessionLocal, User, TradeJournal
from core_api.security import encrypt_api_secret, decrypt_api_secret
from analyzer.main_scanner import SignalBot
from worker.bingx_trader import BingXExchange

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("MainAPI")

app = FastAPI(title="SignalBot v6.1")

REGISTER_TOKEN = os.getenv("TELEGRAM_REGISTER_TOKEN", "")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "admin123")
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

# ══════════════════════════════════════════════════════════════════
# TIER CONFIG
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

def _tg_send(token: str, chat_id: str, text: str):
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        _req.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=5)
    except Exception as e:
        log.warning("_tg_send error: %s", e)

def _save_journal(uid: str, sym: str, direction: str, pnl_pct: float, qty: float):
    try:
        db = SessionLocal()
        user = db.query(User).filter(User.telegram_id == uid).first()
        j = TradeJournal(
            symbol=sym,
            user_id=uid,
            tier=user.tier if user else "TIER1",
            direction=direction,
            outcome="WIN" if pnl_pct > 0 else "LOSS",
            pnl_pct=pnl_pct,
            pnl_usd=0.0,
            lesson="Early Exit"
        )
        db.add(j)
        db.commit()
        db.close()
    except Exception as e:
        pass

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
                        
                    # Tránh trigger nhiều lần
                    redis_key = f"TP1_HIT:{user_id}:{sym}:{direction}"
                    if redis_client and redis_client.exists(redis_key):
                        continue
                        
                    is_hit = False
                    if direction == "LONG" and cur_price >= tp1 and tp1 > entry:
                        is_hit = True
                    elif direction == "SHORT" and cur_price <= tp1 and tp1 < entry:
                        is_hit = True
                        
                    if is_hit:
                        res = bx.handle_tp1_hit(sym, direction, qty, entry, tp2)
                        if res.get("ok"):
                            if redis_client:
                                redis_client.setex(redis_key, 86400, "1")  # Lưu 1 ngày
                            _tg_send(
                                REGISTER_TOKEN, user_id,
                                f"🎯 <b>TP1 HIT: {sym}</b>\n"
                                f"📈 Đã chốt 50% vị thế tại ${tp1:.4f}\n"
                                f"🛡️ Đã dời SL về Entry (${entry:.4f}) để bảo toàn vốn."
                            )
            db.close()
        except Exception as e:
            log.error("_tp1_monitor error: %s", e)
        time.sleep(15)

def _schedule_weekly_report():
    pass

def _register_telegram_webhook():
    try:
        url = f"https://api.telegram.org/bot{REGISTER_TOKEN}/setWebhook"
        webhook_url = f"{os.getenv('RENDER_URL', 'https://auto-trade-v6.onrender.com')}/telegram/webhook"
        r = _req.get(url, params={"url": webhook_url}, timeout=10)
        if r.status_code == 200:
            log.info("✅ Auto register Telegram Webhook success: %s", webhook_url)
    except Exception as e:
        log.warning("⚠️ Error registering Telegram Webhook: %s", e)

def evaluate_reversal_for_position(user: User, pos: dict, current_price: float, db: Session):
    sym = pos["symbol"]
    direction = pos["direction"]
    qty = float(pos.get("qty", 0))
    entry = float(pos.get("entry", 0))
    
    now = time.time()
    
    try:
        cached = _LAST_REVERSAL_EVAL.get(sym)
        if cached and isinstance(cached, dict) and now - cached.get("time", 0) < 180:
            new_direction = cached.get("direction", "WAIT")
            conf = cached.get("conf", 0)
            analysis = cached.get("analysis", {})
        else:
            from analyzer.engine import SignalEngine
            engine = SignalEngine()
            analysis = engine.full_analysis(sym)
            new_direction = analysis.get("final", "WAIT")
            conf = analysis.get("confidence", 0)
            
            _LAST_REVERSAL_EVAL[sym] = {
                "time": now,
                "direction": new_direction,
                "conf": conf,
                "analysis": analysis
            }
            
        is_reversal = (direction == "LONG" and new_direction == "SHORT") or (direction == "SHORT" and new_direction == "LONG")
        is_weak_trend = (new_direction == "WAIT" and conf < 40)
        
        should_close_early = False
        should_reverse = False
        
        if is_reversal and conf >= 70:
            should_close_early = True
            should_reverse = True
        elif is_reversal and conf >= 40:
            should_close_early = True
        elif is_weak_trend:
            should_close_early = True
            
        if should_close_early:
            bx = get_bx(user)
            #in_profit = (direction == "LONG" and current_price > entry) or (direction == "SHORT" and current_price < entry)
            #pnl_pct = ((current_price - entry) / entry * 100 if direction == "LONG" else (entry - current_price) / entry * 100)
            # Kiểm tra an toàn trước khi tính toán
            if entry > 0:
               in_profit = (direction == "LONG" and current_price > entry) or (direction == "SHORT" and current_price < entry)
               pnl_pct = ((current_price - entry) / entry * 100 if direction == "LONG" else (entry - current_price) / entry * 100)
            else:
     # Gán giá trị mặc định an toàn nếu entry = 0
               in_profit = False
               pnl_pct = 0.0

            action_type = "CHỐT LỜI SỚM" if in_profit else "CẮT LỖ SỚM"
            emoji = "💰" if in_profit else "⚠️"
            reason = "đảo chiều mạnh" if should_reverse else ("đảo chiều yếu" if is_reversal else "xu hướng suy yếu (WAIT)")
            
            log.info("🚨 Early Exit detected for %s %s: %s (Reason: %s)", user.telegram_id, sym, action_type, reason)
            
            res = bx.close_position(sym, qty, direction)
            if res.get("ok"):
                if redis_client:
                    try:
                        redis_client.setex(f"REVERSAL_CLOSED:{user.telegram_id}:{sym}:{direction}", 120, "1")
                    except Exception:
                        pass
                
                bx.cancel_all_orders(sym)
                
                _tg_send(
                    REGISTER_TOKEN, user.telegram_id,
                    f"{emoji} <b>{action_type} ({reason.upper()}): {sym}</b>\n\n"
                    f"🔄 Đánh giá lại: Xu hướng chuyển sang <b>{new_direction}</b> (Conf: {conf}%).\n"
                    f"📊 Vị thế cũ: {direction} @ ${entry:.4f}\n"
                    f"📈 Giá hiện tại: ${current_price:.4f} | PnL: {pnl_pct:+.2f}%\n"
                    f"🔒 Đã tự động đóng vị thế và huỷ SL/TP cũ để bảo vệ vốn."
                )
                
                _save_journal(user.telegram_id, sym, direction, pnl_pct, qty)
                time.sleep(1.5)
                
                if should_reverse:
                    new_entry = float(analysis["plan"]["entry"])
                    new_sl    = float(analysis["plan"]["sl"])
                    new_tp1   = float(analysis["plan"]["tp1"])
                    new_tp2   = float(analysis["plan"].get("tp2", 0))
                    if new_tp2 <= 0:
                        if new_direction == "LONG":
                            new_tp2 = round(new_tp1 + abs(new_tp1 - new_entry), 4)
                        else:
                            new_tp2 = round(new_tp1 - abs(new_tp1 - new_entry), 4)
                    
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
        sym = signal.get("symbol", "").strip().upper()
        direction = signal.get("final", "WAIT")
        
        # 1. Validate tín hiệu
        if not sym or direction == "WAIT": return

        # 2. Kiểm tra Cooldown & Vị thế (Giữ nguyên)
        pending_key = f"PENDING:{user.telegram_id}:{sym}"
        if redis_client and redis_client.get(pending_key): return
        
        with _POS_LOCK:
            already = [p for p in LIVE_POSITIONS if str(p.get("user_id")) == user.telegram_id and p.get("symbol") == sym]
        if already: return

        # 3. Tính toán khối lượng (Safety First)
        plan = signal.get("plan", {})
        entry = float(plan.get("entry", 0))
        sl    = float(plan.get("sl", 0))
        tp1   = float(plan.get("tp1", 0))
        tp2   = float(plan.get("tp2", 0) or (tp1 + abs(tp1 - entry) if direction == "LONG" else tp1 - abs(tp1 - entry)))
        
        sl_pct = abs(entry - sl) / entry
        if sl_pct < 0.001 or entry <= 0: return

        risk_amt = user.capital * (user.max_risk_pct / 100)
        qty = round(risk_amt / (entry * sl_pct), 4)

        # 4. THỰC HIỆN ĐẶT LỆNH
        bx = get_bx(user)
        side = "BUY" if direction == "LONG" else "SELL"
        
        # Thiết lập leverage và cancel các lệnh cũ
        bx.set_leverage(sym, leverage=user.leverage)
        bx.cancel_all_orders(sym)
        
        # ĐẶT LỆNH MARKET
        res = bx.place_order(sym, side, qty, sl, tp2)

        # 5. BÁO CÁO TELEGRAM ĐỘC LẬP
        # Dù kết quả res có thể là partial success (lệnh khớp nhưng SL/TP lỗi), vẫn phải báo cho user
        if res and res.get("ok"):
            if redis_client: redis_client.setex(pending_key, 60, "1")
            log.info("OK %s: %s %s qty=%.4f", user.telegram_id, direction, sym, qty)
            _tg_send(REGISTER_TOKEN, user.telegram_id,
                f"✅ <b>ĐÃ VÀO LỆNH: {sym}</b>\n"
                f"📈 {direction} | Qty: {qty:.4f}\n"
                f"🛑 SL: <code>${sl:.4f}</code> | 🎯 TP: <code>${tp2:.4f}</code>")
        else:
            # Gửi lỗi chi tiết để bạn biết tại sao
            error_msg = res.get("msg", "Unknown Error")
            log.error("BingX đặt lệnh lỗi cho %s: %s", user.telegram_id, error_msg)
            _tg_send(REGISTER_TOKEN, user.telegram_id, f"❌ <b>LỖI VÀO LỆNH {sym}</b>\n<code>{error_msg}</code>")

    except Exception as e:
        log.error("_execute_for_user %s: %s", user.telegram_id, str(e))

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


@app.on_event("startup")
async def startup_event():
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
                            <div class="grid grid-cols-3 gap-2">
                                <select id="sig-symbol-select" onchange="document.getElementById('sig-symbol').value = this.value; updateMarketDepth();"
                                        class="col-span-2 bg-[#040811] border border-[#1b263e] rounded p-2 text-xs text-emerald-400 font-bold">
                                    <option value="BTCUSDT" selected>BTCUSDT (Bitcoin)</option>
                                    <option value="ETHUSDT">ETHUSDT (Ethereum)</option>
                                    <option value="BNBUSDT">BNBUSDT (Binance Coin)</option>
                                    <option value="SOLUSDT">SOLUSDT (Solana)</option>
                                    <option value="HYPEUSDT">HYPEUSDT (Hyperliquid)</option>
                                    <option value="XAUUSD">XAUUSD (Gold)</option>
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

