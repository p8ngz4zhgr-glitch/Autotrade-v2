# ═══════════════════════════════════════════════════════════
# MAIN SCANNER — SignalBot v6.1 (Analyzer Core)
# ═══════════════════════════════════════════════════════════
import os, time, json, logging, schedule, gc, requests, threading
from datetime import datetime as dt_module
import redis

from analyzer.engine import SignalEngine
from analyzer.llm_agents import LLMChain, MultiAgentPipeline
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
        logging.getLogger("SignalBot").warning(f"Không thể lấy trí nhớ AI: {e}")
        return ""


class SignalBot:
    CRYPTO_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "HYPEUSDT"]
    STOCK_SYMBOLS  = ["TSLA", "NVDA", "SPY", "QQQ", "NCCOGOLD2USD-USDT"]

    _PIPELINE_SEMAPHORE = threading.Semaphore(4)  # Tăng lên 4 luồng song song

    def __init__(self):
        self.log = logging.getLogger("SignalBot")
        self.dt  = dt_module
        self.db  = SessionLocal()

        self.engine       = SignalEngine()
        self.llm          = LLMChain()
        self.tg           = TelegramBot()
        self.pipeline     = MultiAgentPipeline(self.llm)
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
        keys = self.CRYPTO_SYMBOLS + self.STOCK_SYMBOLS
        self.last_signals    = {k: v for k, v in self.last_signals.items() if k in keys}
        self._pushed_signals = {k: v for k, v in self._pushed_signals.items() if k in keys}
        gc.collect()
        self.log.info("🧹 RAM đã dọn dẹp")

    def should_send(self, symbol, new):
        sweep = new.get("liquidity_sweep", {})
        if sweep.get("detected"):
            return True, f"🔥 LIQUIDITY SWEEP ({sweep.get('type')})"
        prev = self.last_signals.get(symbol)
        if not prev or not prev.get("final"):
            return True, "lần đầu"
        if prev["final"] != new["final"]:
            return True, "signal đổi " + prev["final"] + "→" + new["final"]
        if prev.get("wyckoff", {}).get("phase") != new.get("wyckoff", {}).get("phase"):
            return True, "Wyckoff đổi pha"
        bo = new.get("breakout", {})
        if bo.get("type", "NONE") != "NONE" and bo.get("strength", 0) >= 60:
            return True, "Breakout " + bo["type"]
        if new.get("whale", {}).get("detected"):
            return True, "Whale detected"
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
            # [FIX v6.2] Truyền edge thực (p_win/rr_ratio) để lớp Kelly sizing
            # ở main.py/bingx_trader.py không còn phải dùng số mặc định chung chung.
            "bayes_ev":  data.get("bayes_ev", {}),
            "rr_ratio":  data.get("rr_ratio", 1.5),
            # [NEW v6.5] Thêm context cho Meta-labeling filter (worker/meta_labeling.py)
            "hmm":       data.get("hmm", {}),
            "oi_signal": data.get("oi_signal", "N/A"),
            "funding":   data.get("funding", 0),
            "news_risk": data.get("news_risk", {}),
        }
        try:
            client.lpush("TRADE_SIGNALS", json.dumps(payload))
            client.ltrim("TRADE_SIGNALS", 0, 99)
            self._pushed_signals[symbol] = now
            store_name = "Redis" if client == self.redis_client else "LocalStore (0% RAM/Quota)"
            self.log.info(f"📤 Đã đẩy lệnh {data['final']} {symbol} vào Hàng đợi ({store_name}).")
        except Exception as e:
            self.log.error(f"❌ Lỗi đẩy Queue: {e}")

    def _run_pipeline_sync(self, sym, data):
        final_sig = data.get("final", "WAIT")
        conf = data.get("confidence", 0)
        ev_ratio = data.get("bayes_ev", {}).get("ev_ratio", 0)
        if final_sig == "WAIT" and conf < 70 and ev_ratio < 0.3:
            return
        with self._PIPELINE_SEMAPHORE:
            try:
                result = self.pipeline.run(data)
                msgs   = MultiAgentPipeline.format_telegram(result, sym)
                for i, msg in enumerate(msgs):
                    self.tg.send(msg)
                    if i < len(msgs) - 1:
                        time.sleep(1)
                self.log.info("🤖 Pipeline %s hoàn thành (%d msgs)", sym, len(msgs))
            except Exception as e:
                self.log.error("❌ Pipeline %s: %s", sym, e)

    def _run_pipeline(self, sym, data):
        t = threading.Thread(
            target=self._run_pipeline_sync,
            args=(sym, data),
            name=f"pipeline-{sym}",
            daemon=True,
        )
        t.start()
        self.log.info("  🔀 Pipeline %s → background thread [%s]", sym, t.name)

    # ══════════════════════════════════════════════════════════════
    # [FIX v6.3] CỔNG PHẢN BIỆN 12-AGENT TRƯỚC KHI VÀO LỆNH
    # ──────────────────────────────────────────────────────────────
    # Trước đây: push_to_queue() chạy TRƯỚC, pipeline 12-agent chạy song song
    # ở luồng nền chỉ để gửi Telegram -> hội đồng phản biện không hề ảnh hưởng
    # tới quyết định vào lệnh, trái với yêu cầu gốc ("12 agent phải tư duy
    # phân tích để đưa ra nhận định phân tích để vào lệnh").
    # Nay: với các tín hiệu LONG/SHORT (không phải WAIT), pipeline chạy ĐỒNG BỘ
    # trước push_to_queue(), có timeout + fail-open (nếu LLM lỗi/treo thì GIỮ
    # NGUYÊN quyết định gốc của engine, không để bot đứng hình vì API sập).
    # Nếu hội đồng KHÔNG đồng thuận hướng lệnh (hoặc tự chấm WAIT) -> huỷ vào
    # lệnh. Nếu đồng thuận -> dùng confidence đã hiệu chỉnh theo thống kê
    # thắng/thua lịch sử (apply_statistical_overlay) thay cho confidence thô.
    # ══════════════════════════════════════════════════════════════
    _PIPELINE_GATE_TIMEOUT = 90  # giây — đủ cho 4 lượt gọi LLM tuần tự, có dự phòng

    def _gate_with_pipeline(self, sym, data):
        result_holder = {}

        def _run():
            with self._PIPELINE_SEMAPHORE:
                try:
                    result_holder["result"] = self.pipeline.run(data)
                except Exception as e:
                    result_holder["error"] = e

        t = threading.Thread(target=_run, name=f"gate-{sym}", daemon=True)
        t.start()
        t.join(timeout=self._PIPELINE_GATE_TIMEOUT)

        if t.is_alive():
            self.log.warning("  ⏱️ [GATE] %s: Pipeline quá %ds -> fail-open, giữ tín hiệu gốc của engine.",
                              sym, self._PIPELINE_GATE_TIMEOUT)
            return data

        if "result" not in result_holder:
            self.log.warning("  ⚠️ [GATE] %s: Pipeline lỗi -> fail-open, giữ tín hiệu gốc. Lỗi: %s",
                              sym, result_holder.get("error"))
            return data

        result = result_holder["result"]

        try:
            msgs = MultiAgentPipeline.format_telegram(result, sym)
            for i, msg in enumerate(msgs):
                self.tg.send(msg)
                if i < len(msgs) - 1:
                    time.sleep(1)
        except Exception as e:
            self.log.warning("  ⚠️ [GATE] format_telegram %s lỗi: %s", sym, e)

        engine_dir = data.get("final", "WAIT")
        panel_dir  = result.get("stat_direction", "WAIT")
        panel_conf = result.get("stat_confidence")

        if panel_dir == "WAIT" or panel_dir != engine_dir:
            self.log.warning("  🧠 [VETO] %s: Engine đề xuất %s nhưng Hội đồng 12-agent = %s -> HUỶ vào lệnh.",
                              sym, engine_dir, panel_dir)
            self.tg.send(
                f"🧠 <b>HỘI ĐỒNG 12-AGENT PHỦ QUYẾT: {sym}</b>\n"
                f"Engine đề xuất <b>{engine_dir}</b> nhưng hội đồng phản biện kết luận "
                f"<b>{panel_dir}</b> -> Đã HUỶ, không vào lệnh."
            )
            data["final"] = "WAIT"
            data["veto_reason"] = f"panel={panel_dir} vs engine={engine_dir}"
            return data

        if panel_conf is not None:
            old_conf = data.get("confidence", 0)
            data["confidence"] = panel_conf
            self.log.info("  🧠 [GATE] %s: Đồng thuận %s | Confidence %.1f%% -> %.1f%% (đã hiệu chỉnh lịch sử)",
                          sym, panel_dir, old_conf, panel_conf)

        return data

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
                
                # [LỌC SỚM TÀI NGUYÊN] Bỏ qua AI cho lệnh WAIT có toán học quá yếu
                if final_sig == "WAIT" and ev_ratio < 0.2 and conf < 65:
                    self.log.info("  ⏭️ [Early Filter] Bỏ qua AI cho %s (EV: %.2f, Conf: %.1f%%) để tiết kiệm server", sym, ev_ratio, conf)
                    time.sleep(1.0)
                    continue

                llm_text, llm = self.llm.analyze(data)
                v1h = data.get("volume_1h", {})

                self.log.info("  %s: %s %.1f%% | CVD:%s | Vol:%s(%.1fx) | Press:%s | LLM:%s",
                              sym, data["final"], data["confidence"],
                              data.get("cvd", {}).get("trend", "?"),
                              v1h.get("vol_trend", "?"), v1h.get("vol_ratio", 1),
                              v1h.get("pressure", "?"), llm)

                should, reason = self.should_send(sym, data)
                if should:
                    msg = self.tg.format_signal(data, llm_text, llm)
                    self.tg.send(msg)
                    self.last_signals[sym] = data
                    self.log.info("  📱 Đã gửi [%s]: %s", reason, sym)

                    if data["final"] != "WAIT":
                        # [FIX v6.3] Phản biện 12-agent TRƯỚC khi vào lệnh (đồng bộ, fail-open)
                        data = self._gate_with_pipeline(sym, data)
                        self.last_signals[sym] = data  # confidence/final có thể đã đổi sau gate
                        if data["final"] != "WAIT":
                            self.push_to_queue(sym, data)
                    else:
                        # WAIT: không cần chặn lệnh, chạy pipeline nền để tham khảo như cũ
                        self._run_pipeline(sym, data)
                else:
                    self.log.info("  ⏭️  Bỏ qua [%s]: %s %s %.1f%%",
                                  reason, sym, data["final"], data["confidence"])

            except Exception as e:
                self.log.error("  ❌ Lỗi xử lý %s: %s", sym, e)

            time.sleep(1.5)

    def run_crypto(self):
        self._scan_count += 1
        self.log.info("═" * 45)
        self.log.info("🔄 Scan #%d — %s", self._scan_count, self.dt.now().strftime("%H:%M:%S"))
        try:
            self._scan(self.CRYPTO_SYMBOLS, "CRYPTO BTC·ETH·BNB")
        except Exception as e:
            self.log.error("❌ run_crypto: %s", e)
        if self._scan_count % 8 == 0:
            self._cleanup()

    def run_stocks(self):
        try:
            self.log.info("═" * 45)
            self.log.info("📈 Stock+Gold — %s", self.dt.now().strftime("%H:%M:%S"))
            self._scan(self.STOCK_SYMBOLS, "STOCK+GOLD")
        except Exception as e:
            self.log.error("❌ run_stocks: %s", e)

    def _hourly_report(self):
        try:
            if not self.last_signals:
                return
            now = self.dt.now().strftime("%d/%m/%Y %H:%M")
            SIG = {"LONG": "🚀", "SHORT": "📉", "WAIT": "⏳"}
            WY  = {"ACCUMULATION": "🔵", "MARKUP": "🟢", "RE-ACCUMULATION": "🟩",
                   "DISTRIBUTION": "🔴", "MARKDOWN": "⛔", "TRANSITION": "⚪"}
            rows = ["📋 <b>BÁO CÁO ĐỊNH KỲ</b>", "🕐 " + now, "━━━━━━━━━━━━━━━━━━━━━━━━━", "🪙 <b>CRYPTO</b>"]
            for sym in self.CRYPTO_SYMBOLS:
                d = self.last_signals.get(sym)
                if not d or not d.get("final"): continue
                wy  = d.get("wyckoff", {}).get("phase", "?")
                bar = "█" * int(d["confidence"] / 10) + "░" * (10 - int(d["confidence"] / 10))
                rows.append("  " + SIG.get(d["final"], "❓") + " <b>" + sym[:3] + "</b> <code>$" +
                             str(round(d["price"], 2)) + "</code> <b>" + d["final"] + "</b> " +
                             str(d["confidence"]) + "% [" + bar + "] " + WY.get(wy, "⚪") + wy)
            rows.append("\n📈 <b>CỔ PHIẾU MỸ</b>")
            for sym in ["TSLA", "NVDA", "SPY", "QQQ", "NCCOGOLD2USD-USDT"]:
                d = self.last_signals.get(sym)
                if not d or not d.get("final"): continue
                wy = d.get("wyckoff", {}).get("phase", "?")
                rows.append("  " + SIG.get(d["final"], "❓") + " <b>" + sym + "</b> <code>$" +
                             str(round(d["price"], 2)) + "</code> <b>" + d["final"] + "</b> " +
                             str(d["confidence"]) + "% " + WY.get(wy, "⚪") + wy)
            rows += ["\n━━━━━━━━━━━━━━━━━━━━━━━━━", "✅ SignalBot v6.1 Core đang chạy bình thường"]
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
        schedule.every(15).minutes.do(self.run_crypto)
        schedule.every(30).minutes.do(self.run_stocks)
        schedule.every().hour.at(":30").do(self._hourly_report)
        schedule.every(4).minutes.do(self._self_ping)

    def start(self):
        self.log.info("━" * 45)
        self.log.info("🚀 SignalBot v6.1 (Analyzer Core) khởi động!")
        self.log.info("━" * 45)

        self._self_ping()
        self.run_crypto()
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
