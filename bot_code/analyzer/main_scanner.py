# ═══════════════════════════════════════════════════════════
# MAIN SCANNER — SignalBot v6.1 (Analyzer Core)
# ═══════════════════════════════════════════════════════════
import os, time, json, logging, schedule, gc, requests, threading
from datetime import datetime as dt_module
import redis

from analyzer.engine import SignalEngine
from analyzer.telegram_bot import TelegramBot
from analyzer.config import Config
from core_api.models import SessionLocal, TradeJournal
from core_api.local_store import local_store

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
cfg = Config()


def get_memory_for_ai(symbol: str) -> str:
    try:
        from core_api.main import SessionLocal, TradeJournal
        db = SessionLocal()
        lessons = db.query(TradeJournal).filter(
            TradeJournal.symbol == symbol,
            TradeJournal.pnl_pct < 0
        ).order_by(TradeJournal.timestamp.desc()).limit(3).all()
        db.close()
        if not lessons:
            return "Chưa có cảnh báo nào từ dữ liệu quá khứ."
        mem = "⚠️ CẢNH BÁO TỪ QUÁ KHỨ (CÁC LỆNH BỊ CẮT LỖ GẦN NHẤT):\n"
        for l in lessons:
            mem += f"- Lần trước đánh {l.direction}: {l.lesson}\n"
        return mem
    except Exception as e:
        logging.getLogger("SignalBot").warning(f"Không thể lấy trí nhớ: {e}")
        return ""


def _build_traderssz_summary(data: dict) -> str:
    """Tạo tóm tắt thuần Price Action TraderSZ (không qua AI phản biện)."""
    tsz = data.get("traderssz", {}) or {}
    htf = tsz.get("htf_opens_and_ranges", {}) or {}
    setup = tsz.get("setup", {}) or {}
    htf_bias = tsz.get("htf_bias", {}) or {}
    btc_ctx = data.get("btc_context") or tsz.get("btc_bias") or {}
    final_sig = data.get("final", "WAIT")
    plan = data.get("plan", {}) or {}

    btc_line = ""
    if data.get("asset_type") == "CRYPTO" and "BTC" not in str(data.get("symbol", "")).upper() and btc_ctx:
        btc_line = f"\n🧭 Đồng pha BTC: {btc_ctx.get('bias', 'NEUTRAL')} ({btc_ctx.get('reason', '')})."

    if final_sig in ("LONG", "SHORT") and setup.get("detected"):
        if setup.get("setup_type") == "RANGE_BREAKOUT_CONTINUATION":
            return (
                f"📌 Khung {str(setup.get('tf', '?')).upper()} xác nhận PHÁ VỠ RANGE (Breakout Continuation) "
                f"tại {setup.get('range_label', htf.get('range_label', '?'))}.\n"
                f"🚀 Giá phá dứt khoát mốc ${setup.get('swept_level', 0)} và giữ ngoài biên -> Tiếp diễn {final_sig} thuần TraderSZ PA.{btc_line}\n"
                f"🎯 Kế hoạch: SL ${plan.get('sl', 0)} | TP1 ${plan.get('tp1', 0)} (+Hòa vốn) | TP2 (Mở rộng Range) ${plan.get('tp2', 0)}."
            )
        return (
            f"📌 Khung {str(setup.get('tf', '?')).upper()} xác nhận {setup.get('setup_type', 'SFP')} "
            f"tại {setup.get('range_label', htf.get('range_label', '?'))}.\n"
            f"🔄 Giá quét râu ${setup.get('sweep_extreme', 0)} qua mốc ${setup.get('swept_level', 0)} "
            f"và đóng nến quay lại Range -> Kích hoạt {final_sig} thuần TraderSZ PA.{btc_line}\n"
            f"🎯 Kế hoạch: SL ${plan.get('sl', 0)} | TP1 (50% EQ) ${plan.get('tp1', 0)} (+Hòa vốn) | TP2 (Biên đối diện) ${plan.get('tp2', 0)}."
        )
    return (
        f"⏳ Range: {htf.get('range_label', htf.get('range_name', '?'))} "
        f"[${htf.get('range_low', 0)} – EQ ${htf.get('eq_50', 0)} – ${htf.get('range_high', 0)}].\n"
        f"🧭 Phân vùng: {htf.get('market_zone', '?')} | HTF Bias: {htf_bias.get('bias', 'RANGE_NEUTRAL')}.{btc_line}\n"
        f"💬 {tsz.get('reason', 'Chờ giá quét biên RH/RL quay lại Range hoặc phá vỡ dứt khoát tiếp diễn xu hướng.')}"
    )


class SignalBot:
    CRYPTO_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "HYPEUSDT"]
    METALS_SYMBOLS = ["NCCOGOLD2USD-USDT", "NCCOXAG2USD-USDT"]
    STOCK_SYMBOLS  = ["TSLA", "NVDA", "SPY", "QQQ"]

    def __init__(self):
        self.log = logging.getLogger("SignalBot")
        self.dt  = dt_module
        self.db  = SessionLocal()

        self.engine       = SignalEngine()
        self.tg           = TelegramBot()
        self.last_signals = {}
        self._scan_count  = 0
        self._closed_notified = set()

        self._pushed_signals: dict[str, float] = {}
        self._push_cooldown = 90

        redis_url = (os.environ.get("REDIS_URL") or "").strip()
        self.redis_client = None
        if redis_url and any(redis_url.startswith(p) for p in ("redis://", "rediss://", "unix://")):
            try:
                self.redis_client = redis.from_url(redis_url, socket_connect_timeout=5)
                self.redis_client.ping()
                self.log.info("🟢 Redis Queue kết nối OK")
            except Exception as e:
                self.log.warning("⚠️ Lỗi kết nối Redis: %s -> Dùng LocalStore", e)
                self.redis_client = None
        else:
            self.log.info("ℹ️ Scanner sử dụng LocalStore Queue (In-Memory)")

    def _cleanup(self):
        keys = self.CRYPTO_SYMBOLS + self.METALS_SYMBOLS + self.STOCK_SYMBOLS
        self.last_signals    = {k: v for k, v in self.last_signals.items() if k in keys}
        self._pushed_signals = {k: v for k, v in self._pushed_signals.items() if k in keys}
        gc.collect()
        self.log.info("🧹 RAM đã dọn dẹp")

    def should_send(self, symbol, new):
        tsz = new.get("traderssz", {})
        setup = tsz.get("setup", {})
        if setup.get("detected") and new.get("final") in ("LONG", "SHORT"):
            return True, f"🔥 TRADERSZ {setup.get('tf','').upper()} {setup.get('setup_type','SFP')} ({new['final']})"
        prev = self.last_signals.get(symbol)
        if not prev or not prev.get("final"):
            return True, "lần đầu"
        if prev["final"] != new["final"]:
            return True, "signal đổi " + prev["final"] + "→" + new["final"]
        prev_zone = prev.get("traderssz", {}).get("htf_opens_and_ranges", {}).get("market_zone")
        new_zone  = tsz.get("htf_opens_and_ranges", {}).get("market_zone")
        if prev_zone and new_zone and prev_zone != new_zone:
            return True, f"TraderSZ đổi vùng {prev_zone}→{new_zone}"
        if new["confidence"] >= 80 and new["final"] != "WAIT":
            return True, "confidence cao"
        return False, "không đổi"

    def push_to_queue(self, symbol, data):
        client = None
        if self.redis_client:
            try:
                self.redis_client.ping()
                client = self.redis_client
            except Exception:
                client = local_store
        else:
            client = local_store

        now = time.time()
        last_push = self._pushed_signals.get(symbol, 0)
        if now - last_push < self._push_cooldown:
            remaining = int(self._push_cooldown - (now - last_push))
            self.log.info("  ⏭️ Bỏ qua push %s — cooldown còn %ds", symbol, remaining)
            return

        payload = {
            "signal_id": f"sig_{int(now)}_{symbol}",
            "symbol":    symbol,
            "asset_type": data.get("asset_type", "CRYPTO"),
            "final":     data["final"],
            "confidence": data["confidence"],
            "plan":      data["plan"],
            "timestamp": data.get("timestamp", now),
            "bayes_ev":  data.get("bayes_ev", {}),
            "rr_ratio":  data.get("rr_ratio", 1.5),
            "hmm":       data.get("hmm", {}),
            "oi_signal": data.get("oi_signal", "N/A"),
            "funding":   data.get("funding", 0),
            "news_risk": data.get("news_risk", {}),
            "traderssz": data.get("traderssz", {}),
            "trade_regime": data.get("trade_regime", "RANGE_DEVIATION"),
        }
        try:
            client.lpush("TRADE_SIGNALS", json.dumps(payload))
            client.ltrim("TRADE_SIGNALS", 0, 99)
            self._pushed_signals[symbol] = now
            store_name = "Redis" if client == self.redis_client else "LocalStore (0% RAM/Quota)"
            self.log.info(f"📤 Đã đẩy lệnh {data['final']} {symbol} vào Hàng đợi ({store_name}) [Pure TraderSZ PA].")
        except Exception as e:
            self.log.error(f"❌ Lỗi đẩy Queue: {e}")

    def _scan(self, symbols, label):
        self.log.info("─── %s ───", label)
        for sym in symbols:
            try:
                tradeable, note = self.engine.is_tradeable(sym)

                if tradeable and sym in self._closed_notified:
                    self._closed_notified.discard(sym)
                    self.log.info("  🔔 %s: Thị trường mở lại", sym)

                if not tradeable:
                    self.log.info("  ⏸️  %s: %s", sym, note)
                    if sym not in self._closed_notified:
                        self.tg.send("⏸️ <b>" + sym + "</b> — " + note + "\nBot tự phân tích khi mở lại.")
                        self._closed_notified.add(sym)
                    continue

                data = self.engine.full_analysis(sym, db=self.db)
                data["ai_memory"] = get_memory_for_ai(sym)

                final_sig = data.get("final", "WAIT")
                conf = data.get("confidence", 0)
                ev_ratio = data.get("bayes_ev", {}).get("ev_ratio", 0)

                if final_sig == "WAIT" and ev_ratio < 0.2 and conf < 65:
                    self.log.info("  ⏭️ [TraderSZ] %s: Chưa có SFP/Deviation (%s, Conf: %.1f%%)", sym, final_sig, conf)
                    time.sleep(0.5)
                    continue

                pa_text = _build_traderssz_summary(data)
                htf = data.get("traderssz", {}).get("htf_opens_and_ranges", {})

                self.log.info("  %s: %s %.1f%% | Zone:%s (%.1f%%) | Range:%s [$%.2f-$%.2f EQ:$%.2f] | Pure TraderSZ PA",
                              sym, data["final"], data["confidence"],
                              htf.get("market_zone", "?"), htf.get("range_pos_pct", 50.0),
                              htf.get("range_name", "?"), htf.get("range_low", 0), htf.get("range_high", 0), htf.get("eq_50", 0))

                should, reason = self.should_send(sym, data)
                if should:
                    msg = self.tg.format_signal(data, pa_text, "Pure TraderSZ PA")
                    self.tg.send(msg)
                    self.last_signals[sym] = data
                    self.log.info("  📱 Đã gửi [%s]: %s", reason, sym)

                    if data["final"] in ("LONG", "SHORT"):
                        # Vào lệnh trực tiếp theo tín hiệu Price Action TraderSZ — bỏ hoàn toàn AI phản biện
                        self.push_to_queue(sym, data)
                else:
                    self.log.info("  ⏭️  Bỏ qua [%s]: %s %s %.1f%%",
                                  reason, sym, data["final"], data["confidence"])

            except Exception as e:
                self.log.error("  ❌ Lỗi xử lý %s: %s", sym, e)

            time.sleep(1.0)

    def run_crypto(self):
        self._scan_count += 1
        self.log.info("═" * 45)
        self.log.info("🔄 Scan #%d — %s", self._scan_count, self.dt.now().strftime("%H:%M:%S"))
        try:
            self._scan(self.CRYPTO_SYMBOLS, "CRYPTO BTC·ETH·BNB·SOL·HYPE")
        except Exception as e:
            self.log.error("❌ run_crypto: %s", e)
        if self._scan_count % 8 == 0:
            self._cleanup()

    def run_metals(self):
        try:
            self.log.info("═" * 45)
            self.log.info("🥇 Gold & Silver — %s", self.dt.now().strftime("%H:%M:%S"))
            self._scan(self.METALS_SYMBOLS, "GOLD · SILVER (3m)")
        except Exception as e:
            self.log.error("❌ run_metals: %s", e)

    def run_stocks(self):
        try:
            self.log.info("═" * 45)
            self.log.info("📈 Stock — %s", self.dt.now().strftime("%H:%M:%S"))
            self._scan(self.STOCK_SYMBOLS, "US STOCKS")
        except Exception as e:
            self.log.error("❌ run_stocks: %s", e)

    def _hourly_report(self):
        try:
            if not self.last_signals:
                return
            now = self.dt.now().strftime("%d/%m/%Y %H:%M")
            SIG = {"LONG": "🚀", "SHORT": "📉", "WAIT": "⏳"}
            ZONE_IC = {"PREMIUM": "🔴", "DISCOUNT": "🟢", "NO_TRADE_EQ_ZONE": "⚪"}
            rows = ["📋 <b>BÁO CÁO ĐỊNH KỲ TRADERSZ</b>", "🕐 " + now, "━━━━━━━━━━━━━━━━━━━━━━━━━", "🪙 <b>CRYPTO</b>"]
            for sym in self.CRYPTO_SYMBOLS:
                d = self.last_signals.get(sym)
                if not d or not d.get("final"): continue
                htf = d.get("traderssz", {}).get("htf_opens_and_ranges", {})
                zn = htf.get("market_zone", "?")
                eq = htf.get("eq_50", 0)
                bar = "█" * int(d["confidence"] / 10) + "░" * (10 - int(d["confidence"] / 10))
                rows.append("  " + SIG.get(d["final"], "❓") + " <b>" + sym.replace("USDT", "") + "</b> <code>$" +
                             str(round(d["price"], 2)) + "</code> <b>" + d["final"] + "</b> " +
                             str(d["confidence"]) + "% [" + bar + "] " + ZONE_IC.get(zn, "⚪") + f"{zn} (EQ:${eq})")
            rows.append("\n📈 <b>VÀNG · BẠC · CỔ PHIẾU</b>")
            for sym in self.METALS_SYMBOLS + self.STOCK_SYMBOLS:
                d = self.last_signals.get(sym)
                if not d or not d.get("final"): continue
                htf = d.get("traderssz", {}).get("htf_opens_and_ranges", {})
                zn = htf.get("market_zone", "?")
                eq = htf.get("eq_50", 0)
                rows.append("  " + SIG.get(d["final"], "❓") + " <b>" + sym + "</b> <code>$" +
                             str(round(d["price"], 2)) + "</code> <b>" + d["final"] + "</b> " +
                             str(d["confidence"]) + "% " + ZONE_IC.get(zn, "⚪") + f"{zn} (EQ:${eq})")
            rows += ["\n━━━━━━━━━━━━━━━━━━━━━━━━━", "✅ SignalBot Pure TraderSZ đang chạy bình thường"]
            self.tg.send("\n".join(rows))
            self.log.info("📋 Báo cáo định kỳ đã gửi")
        except Exception as e:
            self.log.error("❌ Hourly report: %s", e)

    def _self_ping(self):
        try:
            url = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
            if not url: return
            for attempt in range(3):
                try:
                    r = requests.get(url, timeout=10)
                    self.log.info("🏓 Self-ping OK (%d)", r.status_code)
                    return
                except Exception:
                    if attempt < 2: time.sleep(3)
        except Exception as e:
            self.log.error("❌ Ping: %s", e)

    def _build_schedule(self):
        schedule.clear()
        # Quét Crypto & Vàng/Bạc mỗi 3 phút; Cổ phiếu Mỹ giữ 30 phút
        crypto_interval = 3 if getattr(cfg, "M5_SCALP_MODE", True) else 15
        schedule.every(crypto_interval).minutes.do(self.run_crypto)
        schedule.every(3).minutes.do(self.run_metals)
        schedule.every(30).minutes.do(self.run_stocks)
        schedule.every().hour.at(":30").do(self._hourly_report)
        schedule.every(4).minutes.do(self._self_ping)
        self.log.info("📅 Đã cài đặt lịch quét: Crypto/%dp | Vàng-Bạc/3p | Stock/30p", crypto_interval)

    def start(self):
        self.log.info("━" * 45)
        self.log.info("🚀 SignalBot v6.1 (Analyzer Core) khởi động!")
        self.log.info("━" * 45)

        self._self_ping()
        self.run_crypto()
        self.run_metals()
        self.run_stocks()

        self._build_schedule()
        self.log.info("📅 Schedule: Crypto/15p · Stock+Gold/30p · Ping/4p · Report/giờ")

        consecutive_errors = 0
        while True:
            try:
                schedule.run_pending()
                consecutive_errors = 0
            except Exception as e:
                consecutive_errors += 1
                self.log.error("❌ Schedule #%d: %s", consecutive_errors, e)
                if consecutive_errors >= 10:
                    self.log.warning("⚠️  Rebuild schedule...")
                    try:
                        self._build_schedule()
                        consecutive_errors = 0
                    except Exception as e2:
                        self.log.error("❌ Rebuild: %s", e2)

            time.sleep(10)
