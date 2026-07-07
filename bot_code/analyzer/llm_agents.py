from analyzer.config import Config
from datetime import datetime, timedelta
import logging
import requests
import time
import re

cfg = Config()
log = logging.getLogger("analyzer.llm_agents")


class _CircuitBreaker:
    MAX_FAILS = 3
    COOLDOWN  = 600

    def __init__(self):
        self._fails    : dict[str, int]   = {}
        self._down_until: dict[str, float] = {}

    def is_up(self, name: str) -> bool:
        t = self._down_until.get(name, 0)
        if t and time.time() < t:
            return False
        if t and time.time() >= t:
            self._fails.pop(name, None)
            self._down_until.pop(name, None)
            log.info("🔄 [%s] Circuit breaker RESET — thử lại", name)
        return True

    def ok(self, name: str):
        self._fails[name] = 0
        self._down_until.pop(name, None)

    def fail(self, name: str):
        n = self._fails.get(name, 0) + 1
        self._fails[name] = n
        if n >= self.MAX_FAILS:
            self._down_until[name] = time.time() + self.COOLDOWN
            log.warning("🔴 [%s] Circuit breaker OPEN — skip %ds", name, self.COOLDOWN)

    def status(self) -> dict:
        now = time.time()
        return {k: f"DOWN {int(v - now)}s" for k, v in self._down_until.items()}


_cb = _CircuitBreaker()


def get_symbol_stats(symbol: str, direction: str, days: int = 30) -> dict:
    try:
        from core_api.models import SessionLocal, TradeJournal
        db    = SessionLocal()
        since = datetime.utcnow() - timedelta(days=days)
        rows  = (db.query(TradeJournal)
                 .filter(TradeJournal.symbol    == symbol,
                         TradeJournal.direction  == direction,
                         TradeJournal.timestamp >= since)
                 .order_by(TradeJournal.timestamp.desc())
                 .limit(20)
                 .all())
        db.close()
    except Exception as e:
        log.debug("get_symbol_stats DB error: %s", e)
        return {"win_rate": 50, "total": 0, "confidence_mult": 1.0,
                "reason": "Chưa có dữ liệu lịch sử"}

    if len(rows) < 3:
        return {"win_rate": 50, "total": len(rows), "confidence_mult": 1.0,
                "reason": f"Chỉ có {len(rows)} giao dịch — chưa đủ mẫu (cần ≥3)"}

    wins    = [r for r in rows if (r.pnl_pct or 0) > 0]
    losses  = [r for r in rows if (r.pnl_pct or 0) <= 0]
    wr      = round(len(wins) / len(rows) * 100, 1)
    avg_pnl = round(sum(r.pnl_pct or 0 for r in rows) / len(rows), 2)
    recent  = [r.outcome or ("TP" if (r.pnl_pct or 0) > 0 else "SL") for r in rows[:5]]

    streak = 0
    streak_type = recent[0] if recent else "N/A"
    for o in recent:
        if o == streak_type:
            streak += 1
        else:
            break

    if   wr >= 75 and avg_pnl > 1.5:
        mult, reason = 1.10, f"WR {wr}% ({len(rows)} lệnh) avg+{avg_pnl}% → +10% confidence"
    elif wr >= 65:
        mult, reason = 1.05, f"WR {wr}% ({len(rows)} lệnh) → +5% confidence"
    elif wr >= 50:
        mult, reason = 1.00, f"WR {wr}% ({len(rows)} lệnh) → neutral"
    elif wr >= 40:
        mult, reason = 0.93, f"WR {wr}% ({len(rows)} lệnh) → -7% confidence"
    elif wr >= 30:
        mult, reason = 0.85, f"⚠️ WR {wr}% ({len(rows)} lệnh) → -15% confidence"
    else:
        mult, reason = 0.78, f"🚫 WR {wr}% ({len(rows)} lệnh) → -22% confidence (poor history)"

    if streak >= 3 and streak_type in ("SL", "LOSS"):
        mult   = round(mult * 0.90, 3)
        reason += f" | ⚠️ {streak} SL liên tiếp → thêm -10%"

    return {
        "win_rate":          wr,
        "total":             len(rows),
        "wins":              len(wins),
        "losses":            len(losses),
        "avg_pnl":           avg_pnl,
        "recent_outcomes":   recent,
        "confidence_mult":   round(mult, 3),
        "reason":            reason,
    }


def apply_statistical_overlay(symbol: str, direction: str,
                               raw_confidence: float) -> tuple[float, str]:
    stats = get_symbol_stats(symbol, direction)
    if stats["total"] < 3:
        return raw_confidence, "Chưa đủ dữ liệu lịch sử"

    adjusted = round(min(95, max(30, raw_confidence * stats["confidence_mult"])), 1)
    delta    = round(adjusted - raw_confidence, 1)
    sign     = "+" if delta >= 0 else ""
    explain  = (f"Statistical Overlay: WR={stats['win_rate']}% ({stats['total']} trades) "
                f"→ {sign}{delta}% ({raw_confidence}% → {adjusted}%)\n"
                f"  {stats['reason']}\n"
                f"  Recent: {' → '.join(stats['recent_outcomes'][:5])}")
    return adjusted, explain


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
            return ""
        mem = "⚠️ CẢNH BÁO TỪ QUÁ KHỨ (CÁC LỆNH BỊ CẮT LỖ GẦN NHẤT):\n"
        for l in lessons:
            mem += f"- Đánh {l.direction}: {l.lesson}\n"
        return mem
    except Exception as e:
        log.warning(f"Lỗi lấy bộ nhớ AI: {e}")
        return ""


class LLMChain:

    def __init__(self):
        self.groq_key    = cfg.GROQ_API_KEY
        self.gemini_key  = cfg.GEMINI_API_KEY
        self.mistral_key = cfg.MISTRAL_API_KEY
        self.nvidia_key  = getattr(cfg, "NVIDIA_API_KEY", "")

    def _slot(self):
        m = datetime.now().minute
        if   m < 15: s = 0
        elif m < 30: s = 1
        elif m < 45: s = 2
        else:        s = 3
        names = {0: "Groq", 1: "Mistral", 2: "NVIDIA NIM", 3: "Rule-based"}
        log.info("Chiếu slot AI: %d → %s", m, names[s])
        return s

    def _prompt(self, data):
        fibo  = data.get("fibo", {})
        wy    = data.get("wyckoff", {})
        cvd   = data.get("cvd", {})
        vol   = data.get("volume_1h", {})
        ell   = data.get("elliott_4h", {})
        atype = data.get("asset_type", "CRYPTO")
        ctx   = {"CRYPTO": "crypto futures", "STOCK": "cổ phiếu Mỹ",
                 "GOLD":   "vàng NCCOGOLD2USD-USDT"}.get(atype, "")
        tfs   = " | ".join(tf + ":" + r["direction"][0] + "(" + str(r["score"]) + ")"
                           for tf, r in data["timeframes"].items())
        return ("Chuyên gia " + ctx + ". Phân tích ngắn (150 từ):\n"
                + data["symbol"] + " $" + str(data["price"]) + " | "
                + data["final"] + " " + str(data["confidence"]) + "%\n"
                "TF: " + tfs + "\n"
                "Elliott:" + str(ell.get("wave_pattern","?")) + " (" + str(ell.get("current_wave","?")) + ")\n"
                "CVD:" + str(cvd.get("trend","?")) + " Vol:" + str(vol.get("vol_trend","?")) + "\n"
                "Fibo:" + str(fibo.get("trend","?")) + " Zone:" + str(fibo.get("zone","?")) + "\n"
                "Wyckoff:" + str(wy.get("phase","?")) + " " + str(wy.get("bias","?")) + "\n"
                "SL:$" + str(data["plan"]["sl"]) + " TP1:$" + str(data["plan"]["tp1"]) + "\n\n"
                "Format:\nKY_THUAT: ...\nHANH_DONG: [MUA/BAN/CHO]\nRUI_RO: ...")

    def _groq(self, prompt):
        if not self.groq_key:
            raise ValueError("No Groq key")
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": "Bearer " + self.groq_key,
                     "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile",
                  "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 300, "temperature": 0.3},
            timeout=20)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()

    def _gemini(self, prompt):
        if not self.gemini_key:
            raise ValueError("No Gemini key")
        for model in ["gemini-2.0-flash-lite", "gemini-2.0-flash"]:
            try:
                r = requests.post(
                    "https://generativelanguage.googleapis.com/v1beta/models/"
                    + model + ":generateContent?key=" + self.gemini_key,
                    json={"contents": [{"parts": [{"text": prompt}]}],
                          "generationConfig": {"maxOutputTokens": 300, "temperature": 0.3}},
                    timeout=10)
                if r.status_code in (429, 404, 403):
                    time.sleep(0.5)
                    continue
                r.raise_for_status()
                return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            except Exception:
                continue
        raise RuntimeError("Gemini all fail")

    def _mistral(self, prompt):
        if not self.mistral_key:
            raise ValueError("No Mistral key")
        r = requests.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": "Bearer " + self.mistral_key,
                     "Content-Type": "application/json"},
            json={"model": "mistral-small-latest",
                  "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 300, "temperature": 0.3},
            timeout=20)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()

    def _nvidia_nim(self, prompt, fast=False):
        if not self.nvidia_key:
            raise ValueError("No NVIDIA NIM key")
        models = (["meta/llama-3.1-8b-instruct", "meta/llama-3.3-70b-instruct"] if fast
                  else ["meta/llama-3.3-70b-instruct", "meta/llama-3.1-8b-instruct"])
        for model in models:
            try:
                r = requests.post(
                    "https://integrate.api.nvidia.com/v1/chat/completions",
                    headers={"Authorization": "Bearer " + self.nvidia_key,
                             "Content-Type": "application/json"},
                    json={"model": model,
                          "messages": [{"role": "user", "content": prompt}],
                          "max_tokens":  300,
                          "temperature": 0.3,
                          "top_p":       0.9,
                          "stream":      False},
                    timeout=25)
                if r.status_code == 429:
                    log.warning("NVIDIA NIM rate limit — thử model tiếp theo")
                    time.sleep(2)
                    continue
                r.raise_for_status()
                result = r.json()["choices"][0]["message"]["content"].strip()
                if result:
                    log.info("  NVIDIA NIM [%s] OK", model.split("/")[-1])
                    return result
            except Exception as e:
                log.warning("NVIDIA NIM [%s]: %s", model, e)
                continue
        raise RuntimeError("NVIDIA NIM: tất cả models thất bại")

    def _rule(self, data):
        sig   = data["final"]
        conf  = data["confidence"]
        price = data["price"]
        plan  = data["plan"]
        fibo  = data.get("fibo", {})
        wy    = data.get("wyckoff", {})
        cvd   = data.get("cvd", {})
        vol   = data.get("volume_1h", {})
        bo    = data.get("breakout", {})
        wh    = data.get("whale", {})
        tfs   = data.get("timeframes", {})
        lvls  = fibo.get("levels", {})
        atype = data.get("asset_type", "CRYPTO")
        fund  = data.get("funding", 0)

        tf1h   = tfs.get("1h", {})
        tf4h   = tfs.get("4h", {})
        tf15m  = tfs.get("15m", {})
        rsi_1h = tf1h.get("rsi", 50)
        rsi_4h = tf4h.get("rsi", 50)
        macd_1h = tf1h.get("macd", {}).get("cross", "NEUTRAL")
        ema_1h  = tf1h.get("ema", {})
        bb_1h   = tf1h.get("bb", {})
        stoch_1h = tf1h.get("stoch", {})

        tech_points = []
        if rsi_1h < 30:
            tech_points.append("RSI 1H=" + str(rsi_1h) + " — vùng oversold mạnh, áp lực mua tăng")
        elif rsi_1h < 45:
            tech_points.append("RSI 1H=" + str(rsi_1h) + " — momentum yếu, chưa xác nhận đảo chiều")
        elif rsi_1h > 70:
            tech_points.append("RSI 1H=" + str(rsi_1h) + " — vùng overbought, cẩn thận pullback")
        elif rsi_1h > 55:
            tech_points.append("RSI 1H=" + str(rsi_1h) + " — momentum bullish đang hình thành")
        else:
            tech_points.append("RSI 1H=" + str(rsi_1h) + " — trung tính, chờ xác nhận hướng")

        if abs(rsi_1h - rsi_4h) < 10:
            tech_points.append("RSI 4H=" + str(rsi_4h) + " — đồng thuận với 1H, tín hiệu đáng tin")
        elif rsi_1h < 50 and rsi_4h > 55:
            tech_points.append("RSI divergence: 1H bearish nhưng 4H vẫn bullish — cẩn thận")

        if macd_1h == "BULL_CROSS":
            hist = tf1h.get("macd", {}).get("hist", 0)
            tech_points.append("MACD 1H bullish cross" +
                                (" + histogram dương — đà tăng" if hist > 0 else " — chờ histogram xác nhận"))
        elif macd_1h == "BEAR_CROSS":
            tech_points.append("MACD 1H bearish cross — áp lực bán đang tăng")

        if ema_1h.get("bull"):
            tech_points.append("EMA 20>50>200: stack bullish hoàn chỉnh — uptrend trung hạn xác nhận")
        elif ema_1h.get("bear"):
            tech_points.append("EMA 20<50<200: stack bearish hoàn chỉnh — downtrend trung hạn xác nhận")

        bb_pct = bb_1h.get("pct", 50)
        if bb_1h.get("squeeze"):
            tech_points.append("BB Squeeze — tích lũy, sắp có breakout mạnh")
        elif bb_pct < 15:
            tech_points.append("Giá gần BB Lower (" + str(bb_pct) + "%) — oversold ngắn hạn")
        elif bb_pct > 85:
            tech_points.append("Giá gần BB Upper (" + str(bb_pct) + "%) — overbought ngắn hạn")

        fibo_wyc_points = []
        fibo_trend = fibo.get("trend", "?")
        fibo_zone  = fibo.get("zone", "BETWEEN")
        in_golden  = fibo.get("in_golden", False)

        if in_golden:
            fibo_wyc_points.append(
                "Giá trong Golden Zone 0.382-0.618 — " +
                ("vùng mua lý tưởng trong uptrend" if fibo_trend == "UPTREND"
                 else "vùng bán lý tưởng trong downtrend"))
        elif "NEAR_0.618" in fibo_zone:
            fibo_wyc_points.append(
                "Giá test 0.618 ($" + str(lvls.get("0.618","?")) + ") — " +
                ("support quan trọng" if fibo_trend == "UPTREND" else "resistance mạnh"))

        wy_phase = wy.get("phase", "TRANSITION")
        wy_bias  = wy.get("bias", "NEUTRAL")
        wy_pos   = wy.get("pos_range", 50)
        wy_desc  = {
            "ACCUMULATION":    "Smart money đang gom hàng (Pos=" + str(wy_pos) + "%) — chuẩn bị tăng",
            "MARKUP":          "Markup phase — uptrend xác nhận",
            "RE-ACCUMULATION": "Tích lũy lại trước sóng kế tiếp",
            "DISTRIBUTION":    "⚠️ Smart money phân phối (Pos=" + str(wy_pos) + "%) — rủi ro đảo chiều",
            "MARKDOWN":        "Markdown phase — downtrend xác nhận",
        }.get(wy_phase, "Phase đang chuyển đổi")
        fibo_wyc_points.append("Wyckoff " + wy_phase + ": " + wy_desc)

        ell = data.get("elliott_4h", {})
        if ell.get("wave_pattern"):
            fibo_wyc_points.append(f"Elliott Wave: {ell.get('wave_pattern')} ({ell.get('current_wave')}) — {ell.get('description')}")

        smart_points = []
        cvd_trend = cvd.get("trend", "NEUTRAL")
        cvd_div   = cvd.get("divergence", False)
        cvd_msgs  = {"BULLISH": "CVD BULLISH — áp lực mua thực sự đang tăng mạnh",
                     "BEARISH": "CVD BEARISH — áp lực bán chiếm ưu thế",
                     "BULLISH_DIV": "CVD Bullish Divergence — giá giảm nhưng lực mua tích lũy",
                     "BEARISH_DIV": "⚠️ CVD Bearish Divergence — giá tăng nhưng lực mua yếu dần"}
        if cvd_trend in cvd_msgs:
            smart_points.append(cvd_msgs[cvd_trend])

        obv_trend  = vol.get("obv_trend", "NEUTRAL")
        vol_trend  = vol.get("vol_trend", "NORMAL")
        vol_ratio  = vol.get("vol_ratio", 1.0)
        pressure   = vol.get("pressure", "NEUTRAL")
        buy_pct    = vol.get("buy_pct", 50)
        poc_price  = vol.get("poc", price)

        if vol_trend == "SURGE":
            smart_points.append("Volume SURGE " + str(vol_ratio) + "x — xác nhận breakout")
        elif vol_trend == "DRY":
            smart_points.append("Volume cạn kiệt — thiếu conviction, tránh vào lệnh lớn")

        if pressure == "STRONG_BUY":
            smart_points.append("Buy Pressure " + str(buy_pct) + "% — người mua chiếm ưu thế")
        elif pressure == "STRONG_SELL":
            smart_points.append("Sell Pressure " + str(100-buy_pct) + "% — người bán chiếm ưu thế")

        smart_points.append("POC $" + str(poc_price) + " — giá đang " + ("trên" if price > poc_price else "dưới") + " POC")

        if bo.get("type","NONE") != "NONE":
            smart_points.append(f"🚨 BREAKOUT: {bo.get('desc','')}")

        lines = [
            "[RULE_BASE_ENGINE]",
            "KY_THUAT:",
            "  - " + "\n  - ".join(tech_points[:3]),
            "FIBONACCI_WYCKOFF:",
            "  - " + "\n  - ".join(fibo_wyc_points[:2]),
            "ON_CHAIN_DERIVATIVES:",
            "  - " + "\n  - ".join(smart_points[:3]),
            "VERDICT: " + sig + " | Conf: " + str(conf) + "%",
            "PLAN: Entry=" + str(price) + " SL=" + str(plan["sl"]) + " TP=" + str(plan["tp1"])
        ]
        return "\n".join(lines)

    def analyze(self, data):
        prompt = self._prompt(data)
        slot = self._slot()

        rotation = {
            0: [("Groq",    lambda p: self._groq(p)),
                ("NVIDIA",  lambda p: self._nvidia_nim(p, fast=True)),
                ("Mistral", lambda p: self._mistral(p)),
                ("Gemini",  lambda p: self._gemini(p))],
            1: [("Mistral", lambda p: self._mistral(p)),
                ("Groq",    lambda p: self._groq(p)),
                ("NVIDIA",  lambda p: self._nvidia_nim(p, fast=True)),
                ("Gemini",  lambda p: self._gemini(p))],
            2: [("NVIDIA",  lambda p: self._nvidia_nim(p, fast=True)),
                ("Groq",    lambda p: self._groq(p)),
                ("Mistral", lambda p: self._mistral(p)),
                ("Gemini",  lambda p: self._gemini(p))],
            3: [("NVIDIA",  lambda p: self._nvidia_nim(p, fast=True)),
                ("Mistral", lambda p: self._mistral(p)),
                ("Groq",    lambda p: self._groq(p)),
                ("Gemini",  lambda p: self._gemini(p))],
        }

        for name, fn in rotation.get(slot, rotation[0]):
            if not _cb.is_up(name):
                log.info("  ⏭️  [%s] circuit open — skip", name)
                continue
            try:
                result = fn(prompt)
                if result and len(result.strip()) > 30:
                    _cb.ok(name)
                    log.info("  Agent [%s] OK", name)
                    return result.strip(), name
                _cb.fail(name)
            except Exception as e:
                log.warning("  Agent [%s] lỗi: %s", name, e)
                _cb.fail(name)

        log.warning("⚠️ Tất cả LLM lỗi hoặc circuit open, fallback sang Rule Base Engine")
        return self._rule(data), "Rule-based"

    def run(self, data):
        log.info("🤖 Multi-Agent Pipeline bắt đầu...")

        memory = data.get("ai_memory", get_memory_for_ai(data["symbol"]))
        def inject(prompt):
            return (prompt + "\n\n--- KINH NGHIEM QUA KHU ---\n" + memory + "\n---")  if memory else prompt

        S1  = ["TECHNICAL_ANALYST","ONCHAIN_ANALYST","MACRO_ANALYST","MOMENTUM_ANALYST"]
        S2  = ["BULL_RESEARCHER","BEAR_RESEARCHER","RESEARCH_MANAGER"]
        S34 = ["TRADER_AGENT","RISK_AGGRESSIVE","RISK_CONSERVATIVE","RISK_NEUTRAL"]
        S5  = ["FINAL_VERDICT"]

        log.info("  Stage 1: 4 Domain Analysts...")
        s1_raw = self._call(inject(self._p_stage1(data)), fast=True)
        s1     = self._parse(s1_raw, S1)
        time.sleep(1)

        log.info("  Stage 2: Bull/Bear/Manager...")
        s2_raw = self._call(inject(self._p_stage2(data, s1_raw)), fast=True)
        s2     = self._parse(s2_raw, S2)
        time.sleep(1)

        log.info("  Stage 3-4: Trader + Risk Debate...")
        s34_raw = self._call(inject(self._p_stage34(data, s2_raw)), fast=True)
        s34     = self._parse(s34_raw, S34)
        time.sleep(1)

        log.info("  Stage 5: CIO Final Verdict...")
        s5_raw = self._call(inject(self._p_stage5(data, s1_raw, s2_raw, s34_raw)), fast=False)
        s5     = self._parse(s5_raw, S5)

        log.info("✅ Pipeline hoàn thành!")

        signal_direction = "LONG"
        if "SIGNAL: SHORT" in s5_raw.upper():
            signal_direction = "SHORT"
        elif "SIGNAL: WAIT" in s5_raw.upper():
            signal_direction = "WAIT"

        stat_adj_conf = None
        stat_explain  = ""
        if signal_direction != "WAIT":
            raw_conf = float(data.get("confidence", 70))
            stat_adj_conf, stat_explain = apply_statistical_overlay(
                data["symbol"], signal_direction, raw_conf)

        return {"stage1": s1, "stage2": s2,
                "stage3": {"TRADER_AGENT": s34.get("TRADER_AGENT", "N/A")},
                "stage4": {k: s34.get(k, "N/A") for k in S34[1:]},
                "stage5": s5,
                "stat_confidence": stat_adj_conf,
                "stat_explanation": stat_explain,
                "stat_direction":   signal_direction}

    def _call(self, prompt, fast=False):
        slot = self._slot()

        rotation = {
            0: [("Groq",    lambda p: self._groq(p)),
                ("NVIDIA",  lambda p: self._nvidia_nim(p, fast=fast)),
                ("Mistral", lambda p: self._mistral(p)),
                ("Gemini",  lambda p: self._gemini(p))],
            1: [("Mistral", lambda p: self._mistral(p)),
                ("Groq",    lambda p: self._groq(p)),
                ("NVIDIA",  lambda p: self._nvidia_nim(p, fast=fast)),
                ("Gemini",  lambda p: self._gemini(p))],
            2: [("NVIDIA",  lambda p: self._nvidia_nim(p, fast=fast)),
                ("Groq",    lambda p: self._groq(p)),
                ("Mistral", lambda p: self._mistral(p)),
                ("Gemini",  lambda p: self._gemini(p))],
            3: [("NVIDIA",  lambda p: self._nvidia_nim(p, fast=fast)),
                ("Mistral", lambda p: self._mistral(p)),
                ("Groq",    lambda p: self._groq(p)),
                ("Gemini",  lambda p: self._gemini(p))],
        }

        for name, fn in rotation.get(slot, rotation[0]):
            if not _cb.is_up(name):
                log.info("  ⏭️  [%s] circuit open — skip", name)
                continue
            try:
                result = fn(prompt)
                if result and len(result.strip()) > 30:
                    _cb.ok(name)
                    log.info("  Agent [%s] OK", name)
                    return result.strip()
                _cb.fail(name)
            except Exception as e:
                log.warning("  Agent [%s] lỗi: %s", name, e)
                _cb.fail(name)
        return ""

    def _summary(self, data):
        fibo = data.get("fibo", {})
        wy   = data.get("wyckoff", {})
        cvd  = data.get("cvd", {})
        vol  = data.get("volume_1h", {})
        bo   = data.get("breakout", {})
        lvls = fibo.get("levels", {})
        
        tfs  = "\n".join(
            "  " + tf + ": " + r["direction"] +
            " score=" + str(r["score"]) +
            " rsi="   + str(r["rsi"]) +
            " macd="  + r["macd"]["cross"]
            for tf, r in data["timeframes"].items())
        return (
            "Asset  : " + data["symbol"] + " (" + data.get("asset_type","") + ")\n"
            "Gia    : $" + str(round(data["price"], 2)) + "\n"
            "Signal : " + data["final"] + " " + str(data["confidence"]) + "%\n"
            "TF:\n" + tfs + "\n"
            "CVD=" + str(cvd.get("trend","?")) +
            " OBV=" + str(vol.get("obv_trend","?")) +
            " Vol=" + str(vol.get("vol_trend","?")) + "x" + str(vol.get("vol_ratio",1)) + "\n"
            "Pressure=" + str(vol.get("pressure","?")) + "(" + str(vol.get("buy_pct",50)) + "%buy)"
            " VWAP=$" + str(vol.get("vwap",0)) + "\n"
            "Fibo=" + str(fibo.get("trend","?")) +
            " Zone=" + str(fibo.get("zone","?")) + "\n"
            "Wyckoff=" + str(wy.get("phase","?")) + " " + str(wy.get("bias","?")) + "\n"
            "Breakout=" + str(bo.get("type","NONE"))
        )

    def _p_stage1(self, data):
        atype = data.get("asset_type","CRYPTO")
        ctx = {"CRYPTO": "crypto. Xem on-chain, whale, derivatives",
               "STOCK":  "co phieu My. Xem earnings, Fed, sector",
               "GOLD":   "vang NCCOGOLD2USD-USDT. Xem DXY, lai suat Fed, CPI",
               }.get(atype, "tai san tai chinh")
        return (
            "Ban la 4 chuyen gia phan tich " + ctx + ".\n"
            "QUY TAC: Moi agent bat dau bang [TAG] trong ngoac vuong.\n"
            "Viet bang tieng Viet, ngan gon (toi da 80 tu/agent), dua vao so lieu cu the.\n\n"
            "DU LIEU:\n" + self._summary(data) + "\n\n"
            "[TECHNICAL_ANALYST]\nPhan tich RSI/MACD/EMA/BB/Fibonacci/Wyckoff.\n"
            "Neu 2 tin hieu manh nhat. Bias: BULLISH/BEARISH/NEUTRAL\n\n"
            "[ONCHAIN_ANALYST]\nPhan tich CVD/OBV/Buy Pressure/OI/Funding/Whale.\n"
            "Nhu cau mua/ban thuc su? Bias: BULLISH/BEARISH/NEUTRAL\n\n"
            "[MACRO_ANALYST]\nPhan tich yeu to vi mo anh huong den " + data["symbol"] + ".\n"
            "1-2 catalyst hoac rui ro macro. Bias: BULLISH/BEARISH/NEUTRAL\n\n"
            "[MOMENTUM_ANALYST]\nPhan tich VWAP, POC, breakout level.\n"
            "Momentum dang tang hay giam? Bias: BULLISH/BEARISH/NEUTRAL"
        )

    def _p_stage2(self, data, s1):
        return (
            "Ban la 2 nha nghien cuu va 1 quan ly.\n"
            "QUY TAC: Moi agent bat dau bang [TAG] trong ngoac vuong.\n\n"
            "KET QUA STAGE 1:\n" + s1[:800] + "\n\n"
            "GIA: " + data["symbol"] + " $" + str(round(data["price"],2)) +
            " | SL=$" + str(data["plan"]["sl"]) + " TP1=$" + str(data["plan"]["tp1"]) + "\n\n"
            "[BULL_RESEARCHER]\n3 ly do MUA manh nhat. Muc tieu gia?\n\n"
            "[BEAR_RESEARCHER]\n3 rui ro lon nhat neu MUA. Muc tieu gia?\n\n"
            "[RESEARCH_MANAGER]\nBull vs Bear — ai thuyet phuc hon?\n"
            "Verdict: BULLISH/BEARISH/NEUTRAL. Conviction: HIGH/MEDIUM/LOW."
        )

    def _p_stage34(self, data, s2):
        mgr_idx  = s2.find("[RESEARCH_MANAGER]")
        mgr_text = s2[mgr_idx:mgr_idx+300] if mgr_idx >= 0 else s2[-300:]
        return (
            "Ban la 1 Trader va 3 quan ly rui ro.\n"
            "QUY TAC: Moi agent bat dau bang [TAG] trong ngoac vuong.\n\n"
            "RESEARCH VERDICT:\n" + mgr_text + "\n\n"
            "GIA: " + data["symbol"] + " $" + str(round(data["price"],2)) +
            " | SL=$" + str(data["plan"]["sl"]) + " TP1=$" + str(data["plan"]["tp1"]) + "\n\n"
            "[TRADER_AGENT]\nAction: LONG/SHORT/WAIT\nEntry/SL/TP1/Size/RR (cu the)\nLy do: (2 cau)\n\n"
            "[RISK_AGGRESSIVE]\nDieu chinh de toi da hoa loi nhuan.\nVerdict: EXECUTE/WAIT\n\n"
            "[RISK_CONSERVATIVE]\nRui ro chua tinh den? Can them dieu kien gi?\nVerdict: EXECUTE/WAIT\n\n"
            "[RISK_NEUTRAL]\nKe hoach can bang: Entry/SL/TP/Size\nVerdict: EXECUTE/WAIT"
        )

    def _p_stage5(self, data, s1, s2, s34):
        def count_bias(text):
            bull, bear = 0, 0
            for line in text.upper().split("\n"):
                if any(k in line for k in ("BIAS:", "VERDICT:", "SIGNAL:")):
                    if any(w in line for w in ("BULLISH","LONG","EXECUTE")): bull += 1
                    elif any(w in line for w in ("BEARISH","SHORT","WAIT")): bear += 1
            return bull, bear

        b1,  r1  = count_bias(s1)
        b2,  r2  = count_bias(s2)
        b34, r34 = count_bias(s34)
        total_bull = b1 + b2 + b34
        total_bear = r1 + r2 + r34
        total_vote = total_bull + total_bear
        bull_pct   = round(total_bull / total_vote * 100) if total_vote > 0 else 50

        if   bull_pct >= 65: consensus_signal, consensus_level = "LONG",  "STRONG" if bull_pct >= 75 else "MODERATE"
        elif bull_pct <= 35: consensus_signal, consensus_level = "SHORT", "STRONG" if bull_pct <= 25 else "MODERATE"
        else:                consensus_signal, consensus_level = "WAIT",  "SPLIT"

        mgr_idx    = s2.find("[RESEARCH_MANAGER]")
        mgr_text   = s2[mgr_idx:mgr_idx+300] if mgr_idx >= 0 else s2[-200:]
        trader_idx = s34.find("[TRADER_AGENT]")
        trader_txt = s34[trader_idx:trader_idx+200] if trader_idx >= 0 else ""
        neu_idx    = s34.find("[RISK_NEUTRAL]")
        neu_txt    = s34[neu_idx:neu_idx+200] if neu_idx >= 0 else ""
        exec_count = s34.upper().count("VERDICT: EXECUTE")
        wait_count = s34.upper().count("VERDICT: WAIT")

        price, sl, tp1, tp2, rr = (data["price"], data["plan"]["sl"],
                                    data["plan"]["tp1"], data["plan"]["tp2"],
                                    data.get("rr_ratio", 2.0))
        return (
            "Ban la CIO — nguoi ra quyet dinh cuoi cung.\n"
            "YEU CAU: Bat dau bang [FINAL_VERDICT] trong ngoac vuong.\n\n"
            "=== PHIEU BAU TU 12 AGENTS ===\n"
            "Stage 1: Bull=" + str(b1) + " Bear=" + str(r1) + "\n"
            "Stage 2: Bull=" + str(b2) + " Bear=" + str(r2) + "\n"
            "Stage 3-4: Bull=" + str(b34) + " Bear=" + str(r34) + "\n"
            "TONG: Bull=" + str(total_bull) + " Bear=" + str(total_bear) +
            " (" + str(bull_pct) + "% Bull)\n"
            "CONSENSUS: " + consensus_signal + " [" + consensus_level + "]\n\n"
            "Research Manager: " + mgr_text[:200] + "\n"
            "Trader: " + trader_txt[:150] + "\n"
            "Risk Neutral: " + neu_txt[:150] + "\n"
            "Risk Debaters: " + str(exec_count) + " EXECUTE / " + str(wait_count) + " WAIT\n\n"
            + data["symbol"] + " $" + str(round(price, 2)) +
            " | SL=$" + str(sl) + " TP1=$" + str(tp1) + " TP2=$" + str(tp2) + " R:R=1:" + str(rr) + "\n\n"
            "NHIEM VU: Consensus la " + consensus_signal + " (" + str(bull_pct) + "% Bull).\n\n"
            "[FINAL_VERDICT]\n"
            "Signal: " + consensus_signal + "\nConfidence: ...%\n"
            "Consensus: " + consensus_level + " (" + str(bull_pct) + "% Bull)\n"
            "Entry: $" + str(round(price, 2)) + "\nSL: $" + str(sl) +
            "\nTP1: $" + str(tp1) + "\nTP2: $" + str(tp2) + "\nSize: 2% von\n"
            "Tom_tat: (quyet dinh + ly do chinh + canh bao)\n"
            "Kich_ban_xau: (dieu kien nao se lam signal nay sai?)"
        )

    def _parse(self, text, tags):
        if not text:
            return {t: "Chưa có phân tích" for t in tags}
        norm = text
        for tag in tags:
            esc = re.escape(tag)
            for pat in [r"\*{1,3}" + esc + r"\*{1,3}",
                        r"#{1,4}\s*" + esc,
                        r"\d+[.)]\s*" + esc,
                        r"\[" + esc + r"\]"]:
                norm = re.sub(pat, "[" + tag + "]", norm, flags=re.I)
            norm = re.sub(r"(\[" + re.escape(tag) + r"\]\s*){2,}",
                          "[" + tag + "]\n", norm, flags=re.I)
        result = {}
        for tag in tags:
            marker = "[" + tag + "]"
            start  = norm.find(marker)
            if start == -1:
                result[tag] = "Không tìm thấy phân tích"
                continue
            end = len(norm)
            for other in tags:
                if other == tag: continue
                p = norm.find("[" + other + "]", start + 1)
                if 0 < p < end: end = p
            block = norm[start + len(marker):end].strip()
            result[tag] = block if block else "Nội dung rỗng"
        return result


class MultiAgentPipeline:

    def __init__(self, llm_chain):
        self.llm = llm_chain

    def _call(self, prompt, fast=False):
        return self.llm._call(prompt, fast=fast)

    def run(self, data):
        log.info("🤖 Multi-Agent Pipeline bắt đầu...")

        memory = data.get("ai_memory", get_memory_for_ai(data["symbol"]))
        def inject(prompt):
            return (prompt + "\n\n--- KINH NGHIEM QUA KHU ---\n" + memory + "\n---")  if memory else prompt

        # Inject L2 Orderbook and Liquidity Sweep into prompts for Stage 5
        sweep = data.get("liquidity_sweep", {})
        ob    = data.get("orderbook", {})
        
        extra_context = ""
        if sweep.get("detected"):
            extra_context += f"- 🔥 BẪY THANH KHOẢN ({sweep.get('type')}): Đã quét râu tại mức giá {sweep.get('price')} kèm Volume lớn. Hãy phân tích đây là hành động săn thanh khoản của cá mập trước khi đảo chiều.\n"
            
        if ob.get("detected"):
            extra_context += f"- 🧱 SỔ LỆNH L2 (Order Book): Tường mua cứng (hỗ trợ) tại {ob.get('support_wall')}, Tường bán cứng (kháng cự) tại {ob.get('resist_wall')}. Độ lệch Imbalance là {ob.get('imbalance')} (Âm = phe Bán áp đảo, Dương = phe Mua áp đảo).\n"

        S1  = ["TECHNICAL_ANALYST","ONCHAIN_ANALYST","MACRO_ANALYST","MOMENTUM_ANALYST"]
        S2  = ["BULL_RESEARCHER","BEAR_RESEARCHER","RESEARCH_MANAGER"]
        S34 = ["TRADER_AGENT","RISK_AGGRESSIVE","RISK_CONSERVATIVE","RISK_NEUTRAL"]
        S5  = ["FINAL_VERDICT"]

        log.info("  Stage 1: 4 Domain Analysts...")
        s1_raw = self._call(inject(self._p_stage1(data)), fast=True)
        s1     = self._parse(s1_raw, S1)
        time.sleep(1)

        log.info("  Stage 2: Bull/Bear/Manager...")
        s2_raw = self._call(inject(self._p_stage2(data, s1_raw)), fast=True)
        s2     = self._parse(s2_raw, S2)
        time.sleep(1)

        log.info("  Stage 3-4: Trader + Risk Debate...")
        s34_raw = self._call(inject(self._p_stage34(data, s2_raw)), fast=True)
        s34     = self._parse(s34_raw, S34)
        time.sleep(1)

        log.info("  Stage 5: CIO Final Verdict...")
        stage5_prompt = self._p_stage5(data, s1_raw, s2_raw, s34_raw)
        if extra_context:
            stage5_prompt += "\n\n=== CHÚ Ý DỮ LIỆU SỔ LỆNH & THANH KHOẢN ===\n" + extra_context
        
        s5_raw = self._call(inject(stage5_prompt), fast=False)
        s5     = self._parse(s5_raw, S5)

        signal_direction = "LONG"
        if "SIGNAL: SHORT" in s5_raw.upper():
            signal_direction = "SHORT"
        elif "SIGNAL: WAIT" in s5_raw.upper():
            signal_direction = "WAIT"

        stat_adj_conf = None
        stat_explain  = ""
        if signal_direction != "WAIT":
            raw_conf = float(data.get("confidence", 70))
            stat_adj_conf, stat_explain = apply_statistical_overlay(
                data["symbol"], signal_direction, raw_conf)

        return {"stage1": s1, "stage2": s2,
                "stage3": {"TRADER_AGENT": s34.get("TRADER_AGENT", "N/A")},
                "stage4": {k: s34.get(k, "N/A") for k in S34[1:]},
                "stage5": s5,
                "stat_confidence": stat_adj_conf,
                "stat_explanation": stat_explain,
                "stat_direction":   signal_direction}

    def _summary(self, data):
        fibo = data.get("fibo", {})
        wy   = data.get("wyckoff", {})
        cvd  = data.get("cvd", {})
        vol  = data.get("volume_1h", {})
        bo   = data.get("breakout", {})
        lvls = fibo.get("levels", {})
        tfs  = "\n".join(
            "  " + tf + ": " + r["direction"] +
            " score=" + str(r["score"]) +
            " rsi="   + str(r["rsi"]) +
            " macd="  + r["macd"]["cross"]
            for tf, r in data["timeframes"].items())
        return (
            "Asset  : " + data["symbol"] + " (" + data.get("asset_type","") + ")\n"
            "Gia    : $" + str(round(data["price"], 2)) + "\n"
            "Signal : " + data["final"] + " " + str(data["confidence"]) + "%\n"
            "TF:\n" + tfs + "\n"
            "CVD=" + str(cvd.get("trend","?")) +
            " OBV=" + str(vol.get("obv_trend","?")) +
            " Vol=" + str(vol.get("vol_trend","?")) + "x" + str(vol.get("vol_ratio",1)) + "\n"
            "Pressure=" + str(vol.get("pressure","?")) + "(" + str(vol.get("buy_pct",50)) + "%buy)"
            " VWAP=$" + str(vol.get("vwap",0)) + "(" + str(vol.get("vwap_signal","?")) + ")\n"
            "Fibo=" + str(fibo.get("trend","?")) +
            " Zone=" + str(fibo.get("zone","?")) + "\n"
            "Wyckoff=" + str(wy.get("phase","?")) + " " + str(wy.get("bias","?")) + "\n"
            "Breakout=" + str(bo.get("type","NONE"))
        )

    def _p_stage1(self, data):
        atype = data.get("asset_type","CRYPTO")
        ctx = {"CRYPTO": "crypto. Xem on-chain, whale, derivatives",
               "STOCK":  "co phieu My. Xem earnings, Fed, sector",
               "GOLD":   "vang NCCOGOLD2USD-USDT. Xem DXY, lai suat Fed, CPI",
               }.get(atype, "tai san tai chinh")
        return (
            "Ban la 4 chuyen gia phan tich " + ctx + ".\n"
            "QUY TAC: Moi agent bat dau bang [TAG] trong ngoac vuong.\n"
            "Viet bang tieng Viet, ngan gon (toi da 80 tu/agent), dua vao so lieu cu the.\n\n"
            "DU LIEU:\n" + self._summary(data) + "\n\n"
            "[TECHNICAL_ANALYST]\nPhan tich RSI/MACD/EMA/BB/Fibonacci/Wyckoff.\n"
            "Neu 2 tin hieu manh nhat. Bias: BULLISH/BEARISH/NEUTRAL\n\n"
            "[ONCHAIN_ANALYST]\nPhan tich CVD/OBV/Buy Pressure/OI/Funding/Whale.\n"
            "Nhu cau mua/ban thuc su? Bias: BULLISH/BEARISH/NEUTRAL\n\n"
            "[MACRO_ANALYST]\nPhan tich yeu to vi mo anh huong den " + data["symbol"] + ".\n"
            "1-2 catalyst hoac rui ro macro. Bias: BULLISH/BEARISH/NEUTRAL\n\n"
            "[MOMENTUM_ANALYST]\nPhan tich da tang/giam, VWAP, POC, breakout level.\n"
            "Momentum dang tang hay giam? Bias: BULLISH/BEARISH/NEUTRAL"
        )

    def _p_stage2(self, data, s1):
        return (
            "Ban la 2 nha nghien cuu va 1 quan ly.\n"
            "QUY TAC: Moi agent bat dau bang [TAG] trong ngoac vuong.\n\n"
            "KET QUA STAGE 1:\n" + s1[:800] + "\n\n"
            "GIA: " + data["symbol"] + " $" + str(round(data["price"],2)) +
            " | SL=$" + str(data["plan"]["sl"]) + " TP1=$" + str(data["plan"]["tp1"]) + "\n\n"
            "[BULL_RESEARCHER]\n3 ly do MUA manh nhat. Muc tieu gia?\n\n"
            "[BEAR_RESEARCHER]\n3 rui ro lon nhat neu MUA. Muc tieu gia?\n\n"
            "[RESEARCH_MANAGER]\nBull vs Bear — ai thuyet phuc hon?\n"
            "Verdict: BULLISH/BEARISH/NEUTRAL. Conviction: HIGH/MEDIUM/LOW. Ly do: (1 cau)"
        )

    def _p_stage34(self, data, s2):
        mgr_idx  = s2.find("[RESEARCH_MANAGER]")
        mgr_text = s2[mgr_idx:mgr_idx+300] if mgr_idx >= 0 else s2[-300:]
        return (
            "Ban la 1 Trader va 3 quan ly rui ro.\n"
            "QUY TAC: Moi agent bat dau bang [TAG] trong ngoac vuong.\n\n"
            "RESEARCH VERDICT:\n" + mgr_text + "\n\n"
            "GIA: " + data["symbol"] + " $" + str(round(data["price"],2)) +
            " | SL=$" + str(data["plan"]["sl"]) + " TP1=$" + str(data["plan"]["tp1"]) + "\n\n"
            "[TRADER_AGENT]\nAction: LONG/SHORT/WAIT\nEntry/SL/TP1/Size/RR (cu the)\nLy do: (2 cau)\n\n"
            "[RISK_AGGRESSIVE]\nDieu chinh de toi da hoa loi nhuan.\nVerdict: EXECUTE/WAIT\n\n"
            "[RISK_CONSERVATIVE]\nRui ro chua tinh den? Can them dieu kien gi?\nVerdict: EXECUTE/WAIT\n\n"
            "[RISK_NEUTRAL]\nKe hoach can bang: Entry/SL/TP/Size\nVerdict: EXECUTE/WAIT"
        )

    def _p_stage5(self, data, s1, s2, s34):
        def count_bias(text):
            bull, bear = 0, 0
            for line in text.upper().split("\n"):
                if any(k in line for k in ("BIAS:", "VERDICT:", "SIGNAL:")):
                    if any(w in line for w in ("BULLISH","LONG","EXECUTE")): bull += 1
                    elif any(w in line for w in ("BEARISH","SHORT","WAIT")): bear += 1
            return bull, bear

        b1,  r1  = count_bias(s1)
        b2,  r2  = count_bias(s2)
        b34, r34 = count_bias(s34)
        total_bull = b1 + b2 + b34
        total_bear = r1 + r2 + r34
        total_vote = total_bull + total_bear
        bull_pct   = round(total_bull / total_vote * 100) if total_vote > 0 else 50

        if   bull_pct >= 65: consensus_signal, consensus_level = "LONG",  "STRONG" if bull_pct >= 75 else "MODERATE"
        elif bull_pct <= 35: consensus_signal, consensus_level = "SHORT", "STRONG" if bull_pct <= 25 else "MODERATE"
        else:                consensus_signal, consensus_level = "WAIT",  "SPLIT"

        mgr_idx    = s2.find("[RESEARCH_MANAGER]")
        mgr_text   = s2[mgr_idx:mgr_idx+300] if mgr_idx >= 0 else s2[-200:]
        trader_idx = s34.find("[TRADER_AGENT]")
        trader_txt = s34[trader_idx:trader_idx+200] if trader_idx >= 0 else ""
        neu_idx    = s34.find("[RISK_NEUTRAL]")
        neu_txt    = s34[neu_idx:neu_idx+200] if neu_idx >= 0 else ""
        exec_count = s34.upper().count("VERDICT: EXECUTE")
        wait_count = s34.upper().count("VERDICT: WAIT")

        price, sl, tp1, tp2, rr = (data["price"], data["plan"]["sl"],
                                    data["plan"]["tp1"], data["plan"]["tp2"],
                                    data.get("rr_ratio", 2.0))
        return (
            "Ban la CIO — nguoi ra quyet dinh cuoi cung.\n"
            "YEU CAU: Bat dau bang [FINAL_VERDICT] trong ngoac vuong.\n\n"
            "=== PHIEU BAU TU 12 AGENTS ===\n"
            "Stage 1: Bull=" + str(b1) + " Bear=" + str(r1) + "\n"
            "Stage 2: Bull=" + str(b2) + " Bear=" + str(r2) + "\n"
            "Stage 3-4: Bull=" + str(b34) + " Bear=" + str(r34) + "\n"
            "TONG: Bull=" + str(total_bull) + " Bear=" + str(total_bear) +
            " (" + str(bull_pct) + "% Bull)\n"
            "CONSENSUS: " + consensus_signal + " [" + consensus_level + "]\n\n"
            "Research Manager: " + mgr_text[:200] + "\n"
            "Trader: " + trader_txt[:150] + "\n"
            "Risk Neutral: " + neu_txt[:150] + "\n"
            "Risk Debaters: " + str(exec_count) + " EXECUTE / " + str(wait_count) + " WAIT\n\n"
            + data["symbol"] + " $" + str(round(price, 2)) +
            " | SL=$" + str(sl) + " TP1=$" + str(tp1) + " TP2=$" + str(tp2) + " R:R=1:" + str(rr) + "\n\n"
            "NHIEM VU: Consensus la " + consensus_signal + " (" + str(bull_pct) + "% Bull).\n"
            "QUAN TRONG: Signal va Tom_tat PHAI NHAT QUAN nhau.\n\n"
            "[FINAL_VERDICT]\n"
            "Signal: " + consensus_signal + "\nConfidence: ...%\n"
            "Consensus: " + consensus_level + " (" + str(bull_pct) + "% Bull)\n"
            "Entry: $" + str(round(price, 2)) + "\nSL: $" + str(sl) +
            "\nTP1: $" + str(tp1) + "\nTP2: $" + str(tp2) + "\nSize: 2% von\n"
            "Tom_tat: (quyet dinh + ly do chinh + canh bao)\n"
            "Kich_ban_xau: (dieu kien nao se lam signal nay sai?)"
        )

    def _parse(self, text, tags):
        if not text:
            return {t: "Chưa có phân tích" for t in tags}
        norm = text
        for tag in tags:
            esc = re.escape(tag)
            for pat in [r"\*{1,3}" + esc + r"\*{1,3}",
                        r"#{1,4}\s*" + esc,
                        r"\d+[.)]\s*" + esc,
                        r"\[" + esc + r"\]"]:
                norm = re.sub(pat, "[" + tag + "]", norm, flags=re.I)
            norm = re.sub(r"(\[" + re.escape(tag) + r"\]\s*){2,}",
                          "[" + tag + "]\n", norm, flags=re.I)
        result = {}
        for tag in tags:
            marker = "[" + tag + "]"
            start  = norm.find(marker)
            if start == -1:
                result[tag] = "Không tìm thấy phân tích"
                continue
            end = len(norm)
            for other in tags:
                if other == tag: continue
                p = norm.find("[" + other + "]", start + 1)
                if 0 < p < end: end = p
            block = norm[start + len(marker):end].strip()
            result[tag] = block if block else "Nội dung rỗng"
        return result

    @staticmethod
    def format_telegram(result, symbol):
        s1 = result["stage1"]
        s2 = result["stage2"]
        s3 = result["stage3"]
        s4 = result["stage4"]
        s5 = result["stage5"]

        def cut(text, n=350):
            t = str(text).strip()
            if len(t) <= n: return t
            truncated = t[:n]
            for sep in [". ","! ","? ","\n"]:
                pos = truncated.rfind(sep)
                if pos > int(n * 0.6):
                    return truncated[:pos + len(sep)].rstrip() + " [...]"
            pos = truncated.rfind(" ")
            return (truncated[:pos] if pos > int(n * 0.7) else truncated) + "[...]"

        def bias_icon(text):
            u = str(text).upper()
            if "BULLISH" in u: return "🟢"
            if "BEARISH" in u: return "🔴"
            return "🟡"

        def exec_icon(text):
            u = str(text).upper()
            if "EXECUTE" in u: return "✅"
            if "WAIT"    in u: return "⏳"
            return "❓"

        ta  = s1.get("TECHNICAL_ANALYST",  "N/A")
        oa  = s1.get("ONCHAIN_ANALYST",    "N/A")
        ma  = s1.get("MACRO_ANALYST",      "N/A")
        mom = s1.get("MOMENTUM_ANALYST",   "N/A")
        bull = s2.get("BULL_RESEARCHER",   "N/A")
        bear = s2.get("BEAR_RESEARCHER",   "N/A")
        mgr  = s2.get("RESEARCH_MANAGER",  "N/A")
        trd  = s3.get("TRADER_AGENT",      "N/A")
        agg  = s4.get("RISK_AGGRESSIVE",   "N/A")
        con  = s4.get("RISK_CONSERVATIVE", "N/A")
        neu  = s4.get("RISK_NEUTRAL",      "N/A")
        fin  = s5.get("FINAL_VERDICT",     "N/A")

        sig_icon = "⏳"
        fu = fin.upper()
        if "SIGNAL: LONG"  in fu: sig_icon = "🚀"
        if "SIGNAL: SHORT" in fu: sig_icon = "📉"

        msg1 = ("🔬 <b>STAGE 1 — 4 CHUYÊN GIA</b> | " + symbol + "\n"
                "━━━━━━━━━━━━━━━━━━━━━━•\n\n"
                "📊 <b>Kỹ Thuật</b> " + bias_icon(ta) + "\n" + cut(ta, 450) + "\n\n"
                "🔗 <b>On-Chain/Derivatives</b> " + bias_icon(oa) + "\n" + cut(oa, 450) + "\n\n"
                "🌍 <b>Macro/Fundamental</b> " + bias_icon(ma) + "\n" + cut(ma, 450) + "\n\n"
                "⚡ <b>Momentum/Price Action</b> " + bias_icon(mom) + "\n" + cut(mom, 450))

        msg2 = ("⚔️ <b>STAGE 2 — BULL vs BEAR</b> | " + symbol + "\n"
                "━━━━━━━━━━━━━━━━━━━━━━•\n\n"
                "🐂 <b>Bull Researcher</b>\n" + cut(bull, 550) + "\n\n"
                "🐻 <b>Bear Researcher</b>\n" + cut(bear, 550) + "\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━•\n"
                "👔 <b>Research Manager Verdict</b>\n" + cut(mgr, 500))

        msg3 = ("💹 <b>STAGE 3-4 — TRADER + RISK</b> | " + symbol + "\n"
                "━━━━━━━━━━━━━━━━━━━━━━•\n"
                "💼 <b>Trader Agent</b>\n" + cut(trd, 550) + "\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━•\n"
                "🔴 <b>Aggressive</b> " + exec_icon(agg) + "\n" + cut(agg, 280) + "\n\n"
                "🔵 <b>Conservative</b> " + exec_icon(con) + "\n" + cut(con, 280) + "\n\n"
                "⚪ <b>Neutral</b> " + exec_icon(neu) + "\n" + cut(neu, 280))

        stat_conf = result.get("stat_confidence")
        stat_expl = result.get("stat_explanation", "")
        stat_line = ""
        if stat_conf is not None:
            icon = "🟢" if stat_conf >= 75 else "🟡" if stat_conf >= 68 else "🔴"
            first_line = stat_expl.split("\n")[0] if stat_expl else ""
            stat_line = ("\n\n📊 <b>STATISTICAL OVERLAY</b>\n"
                         + icon + " Confidence điều chỉnh: <b>" + str(stat_conf) + "%</b>\n"
                         + "<i>" + first_line + "</i>")

        msg4 = (sig_icon + " <b>STAGE 5 — CIO FINAL VERDICT</b> | " + symbol + "\n"
                "━━━━━━━━━━━━━━━━━━━━━━•\n\n"
                + cut(fin, 900)
                + stat_line + "\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━•\n"
                "🤖 <i>12 AI Agents · 4 Stages · SignalBot v6.1</i>")

        return [msg1, msg2, msg3, msg4]
