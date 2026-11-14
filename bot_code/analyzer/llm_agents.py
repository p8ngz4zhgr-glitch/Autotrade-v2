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
        # [NEW v6.7] Bộ nhớ MỀM riêng: chỉ ghi lần thử GẦN NHẤT + thời điểm, KHÔNG
        # phụ thuộc/không reset theo _fails (bộ đếm cứng dễ bị reset về 0 mỗi khi
        # có 1 lần thành công xen kẽ, nên circuit breaker cứng gần như không bao
        # giờ mở nổi trong 1 đợt rate-limit chập chờn — xem giải thích ở _call()).
        self._recent_fail_at: dict[str, float] = {}

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
        self._recent_fail_at.pop(name, None)

    def fail(self, name: str):
        n = self._fails.get(name, 0) + 1
        self._fails[name] = n
        self._recent_fail_at[name] = time.time()
        if n >= self.MAX_FAILS:
            self._down_until[name] = time.time() + self.COOLDOWN
            log.debug("🔴 [%s] Circuit breaker OPEN — skip %ds", name, self.COOLDOWN)

    def recently_failed(self, name: str, window: float = 90) -> bool:
        """[NEW v6.7] Có thất bại trong window giây gần nhất không — dùng để XẾP
        SAU trong thứ tự thử, không phải để CHẶN hẳn như is_up()."""
        t = self._recent_fail_at.get(name, 0)
        return bool(t) and (time.time() - t) < window

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

    def _has_key(self, name: str) -> bool:
        if name == "Groq":
            return bool(self.groq_key and self.groq_key.strip())
        if name == "NVIDIA":
            return bool(self.nvidia_key and self.nvidia_key.strip())
        if name == "Mistral":
            return bool(self.mistral_key and self.mistral_key.strip())
        if name == "Gemini":
            return bool(self.gemini_key and self.gemini_key.strip())
        return True

    def _ordered(self, rotation_list):
        """
        [NEW v6.8] Ưu tiên provider có API Key hợp lệ và không dính gần thất bại.
        """
        return sorted(rotation_list, key=lambda item: (
            not self._has_key(item[0]),
            not _cb.is_up(item[0]),
            _cb.recently_failed(item[0])
        ))

    def _prompt(self, data):
        tsz   = data.get("traderssz", {})
        htf   = tsz.get("htf_opens_and_ranges", {})
        setup = tsz.get("setup", {})
        bias  = tsz.get("htf_bias", {})
        atype = data.get("asset_type", "CRYPTO")
        ctx   = {"CRYPTO": "crypto futures", "STOCK": "co phieu My",
                 "GOLD":   "vang XAU"}.get(atype, "")
        tfs   = " | ".join(tf + ":" + r["direction"][0] + "(" + str(r["score"]) + ")"
                           for tf, r in data.get("timeframes", {}).items())
        return ("Chuyen gia Price Action " + ctx + " (phuong phap TraderSZ - Naked Chart). Phan tich ngan (150 tu):\n"
                + data["symbol"] + " $" + str(data["price"]) + " | "
                + data["final"] + " " + str(data["confidence"]) + "%\n"
                "TF: " + tfs + "\n"
                "Range: " + str(htf.get("range_name","?")) + " [RL=$" + str(htf.get("range_low",0))
                + " | EQ50%=$" + str(htf.get("eq_50",0)) + " | RH=$" + str(htf.get("range_high",0))
                + "] Zone=" + str(htf.get("market_zone","?")) + "\n"
                "Opens: YO=$" + str(htf.get("year_open",0)) + " MO=$" + str(htf.get("monthly_open",0))
                + " WO=$" + str(htf.get("weekly_open",0)) + " DO=$" + str(htf.get("daily_open",0)) + "\n"
                "PDH/PDL=$" + str(htf.get("pdh",0)) + "/$" + str(htf.get("pdl",0))
                + " | Monday=$" + str(htf.get("monday_low",0)) + "-$" + str(htf.get("monday_high",0)) + "\n"
                "Deviation/SFP: " + str(setup.get("setup_type","NONE")) + " (HTF Bias=" + str(bias.get("bias","?")) + ")\n"
                "SL=$" + str(data["plan"]["sl"]) + " TP1(EQ)=$" + str(data["plan"]["tp1"])
                + " TP2(Edge)=$" + str(data["plan"]["tp2"]) + " RR=1:" + str(data.get("rr_ratio",2.0)) + "\n\n"
                "Format:\nKY_THUAT: ...\nHANH_DONG: [MUA/BAN/CHO]\nRUI_RO: ...")

    def _groq(self, prompt):
        if not self.groq_key:
            raise ValueError("No Groq key")
        models = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "deepseek-r1-distill-llama-70b", "llama-3.2-11b-vision-preview", "llama-3.2-3b-preview"]
        for model in models:
            try:
                r = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": "Bearer " + self.groq_key,
                             "Content-Type": "application/json"},
                    json={"model": model,
                          "messages": [{"role": "user", "content": prompt}],
                          "max_tokens": 300, "temperature": 0.3},
                    timeout=12)
                if r.status_code in (400, 401, 403, 404, 410, 429):
                    log.debug("Groq [%s] status %d", model, r.status_code)
                    time.sleep(0.3)
                    continue
                r.raise_for_status()
                res = r.json()["choices"][0]["message"]["content"].strip()
                if res:
                    log.info("  Groq [%s] OK", model)
                    return res
            except Exception as e:
                log.debug("Groq [%s]: %s", model, e)
                continue
        raise RuntimeError("Groq: tất cả models thất bại")

    def _gemini(self, prompt):
        if not self.gemini_key:
            raise ValueError("No Gemini key")
        models = ["gemini-2.0-flash-lite", "gemini-2.0-flash", "gemini-1.5-flash"]
        for model in models:
            try:
                r = requests.post(
                    "https://generativelanguage.googleapis.com/v1beta/models/"
                    + model + ":generateContent?key=" + self.gemini_key,
                    json={"contents": [{"parts": [{"text": prompt}]}],
                          "generationConfig": {"maxOutputTokens": 300, "temperature": 0.3}},
                    timeout=10)
                if r.status_code in (400, 401, 403, 404, 429):
                    time.sleep(0.3)
                    continue
                r.raise_for_status()
                res = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                if res:
                    log.info("  Gemini [%s] OK", model)
                    return res
            except Exception:
                continue
        raise RuntimeError("Gemini: tất cả models thất bại")

    def _mistral(self, prompt):
        if not self.mistral_key:
            raise ValueError("No Mistral key")
        models = ["mistral-small-latest", "mistral-medium-latest", "open-mistral-7b"]
        for model in models:
            try:
                r = requests.post(
                    "https://api.mistral.ai/v1/chat/completions",
                    headers={"Authorization": "Bearer " + self.mistral_key,
                             "Content-Type": "application/json"},
                    json={"model": model,
                          "messages": [{"role": "user", "content": prompt}],
                          "max_tokens": 300, "temperature": 0.3},
                    timeout=12)
                if r.status_code in (400, 401, 403, 404, 410, 429):
                    log.debug("Mistral [%s] status %d", model, r.status_code)
                    time.sleep(0.3)
                    continue
                r.raise_for_status()
                res = r.json()["choices"][0]["message"]["content"].strip()
                if res:
                    log.info("  Mistral [%s] OK", model)
                    return res
            except Exception as e:
                log.debug("Mistral [%s]: %s", model, e)
                continue
        raise RuntimeError("Mistral: tất cả models thất bại")

    def _nvidia_nim(self, prompt, fast=False):
        if not self.nvidia_key:
            raise ValueError("No NVIDIA NIM key")
        models = [
            "meta/llama-3.3-70b-instruct",
            "meta/llama-3.1-70b-instruct",
            "nvidia/llama-3.1-nemotron-70b-instruct",
            "meta/llama-3.1-8b-instruct",
            "deepseek-ai/deepseek-r1",
            "mistralai/mistral-large-2-instruct"
        ]
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
                    timeout=10)
                if r.status_code in (400, 401, 403, 404, 410, 429):
                    log.debug("NVIDIA NIM [%s] status %d", model, r.status_code)
                    continue
                r.raise_for_status()
                result = r.json()["choices"][0]["message"]["content"].strip()
                if result:
                    log.info("  NVIDIA NIM [%s] OK", model.split("/")[-1])
                    return result
            except Exception as e:
                log.debug("NVIDIA NIM [%s]: %s", model, e)
                continue
        raise RuntimeError("NVIDIA NIM: tất cả models thất bại")

    def _rule(self, data):
        sig   = data["final"]
        conf  = data["confidence"]
        price = data["price"]
        plan  = data["plan"]
        tsz   = data.get("traderssz", {})
        htf   = tsz.get("htf_opens_and_ranges", {})
        setup = tsz.get("setup", {})
        bias  = tsz.get("htf_bias", {})
        sess  = tsz.get("session", {})
        atype = data.get("asset_type", "CRYPTO")
        fund  = data.get("funding", 0)
        oi_sig = data.get("oi_signal", "N/A")

        pa_points = [
            f"Dealing Range ({htf.get('range_label','?')}): RL=${htf.get('range_low',0)} | 50% EQ=${htf.get('eq_50',0)} | RH=${htf.get('range_high',0)}",
            f"Vị trí giá: {htf.get('range_pos_pct',50)}% Range ({htf.get('market_zone','?')}) | Phiên {sess.get('session','?')}",
            f"HTF Opens: YO=${htf.get('year_open',0)} · MO=${htf.get('monthly_open',0)} · WO=${htf.get('weekly_open',0)} · DO=${htf.get('daily_open',0)} ({bias.get('bias','?')})",
        ]
        if setup.get("detected"):
            sfp_points = [
                f"Xác nhận {setup.get('tf','').upper()} {setup.get('setup_type','SFP')} quét mốc ${setup.get('swept_level',0)} (râu ${setup.get('sweep_extreme',0)})",
                f"Return to Range: Mục tiêu TP1 = 50% EQ (${plan['tp1']}) -> TP2 = Biên đối diện (${plan['tp2']}), R:R 1:{data.get('rr_ratio',2.0)}",
            ]
        else:
            sfp_points = [
                tsz.get("reason", "Chưa có nến xác nhận quét biên RH/RL & Return to Range"),
                "Tuân thủ No-Trade Zone quanh 50% EQ, kiên nhẫn đứng ngoài chờ bẫy thanh khoản.",
            ]
        extra_points = [f"4H Structure: {bias.get('h4_structure','?')}"]
        if atype == "CRYPTO" and abs(fund) >= 0.03:
            extra_points.append(f"Funding={fund:+.4f}%/8h ({oi_sig})")

        lines = [
            "[RULE_BASE_TRADERSZ]",
            "DEALING_RANGE_OPENS:",
            "  - " + "\n  - ".join(pa_points),
            "DEVIATION_SFP_RETURN_TO_RANGE:",
            "  - " + "\n  - ".join(sfp_points),
            "HTF_LIQUIDITY_CONTEXT:",
            "  - " + "\n  - ".join(extra_points),
            f"VERDICT: {sig} | Conf: {conf}%",
            f"PLAN: Entry={price} SL={plan['sl']} TP1(EQ)={plan['tp1']} TP2={plan['tp2']}",
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

        for name, fn in self._ordered(rotation.get(slot, rotation[0])):
            if not self._has_key(name):
                log.debug("  ⏭️  [%s] chưa cấu hình API Key — skip", name)
                continue
            if not _cb.is_up(name):
                log.debug("  ⏭️  [%s] circuit open — skip", name)
                continue
            try:
                result = fn(prompt)
                if result and len(result.strip()) > 30:
                    _cb.ok(name)
                    log.info("  Agent [%s] OK", name)
                    return result.strip(), name
                _cb.fail(name)
            except Exception as e:
                log.debug("  Agent [%s] chuyển dự phòng: %s", name, e)
                _cb.fail(name)

        log.info("ℹ️ Tự động chuyển sang Rule-based Engine phân tích kỹ thuật")
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

        # Đồng bộ hóa plan và sửa văn bản Stage 5 nếu có sự lệch hướng SL/TP (Class 1)
        price = data["price"]
        plan_sl = data["plan"].get("sl", price)
        plan_tp1 = data["plan"].get("tp1", price)
        plan_tp2 = data["plan"].get("tp2", price)

        if signal_direction == "SHORT":
            if plan_sl < price or plan_tp1 > price:
                sl_dist = abs(price - plan_sl) if plan_sl != price else round(price * 0.025, 2)
                tp1_dist = abs(plan_tp1 - price) if plan_tp1 != price else round(price * 0.035, 2)
                tp2_dist = abs(plan_tp2 - price) if plan_tp2 != price else round(price * 0.070, 2)
                new_sl = round(price + sl_dist, 2)
                new_tp1 = round(price - tp1_dist, 2)
                new_tp2 = round(price - tp2_dist, 2)
                data["plan"]["sl"] = new_sl
                data["plan"]["tp1"] = new_tp1
                data["plan"]["tp2"] = new_tp2
                if "tp_levels" in data["plan"]:
                    data["plan"]["tp_levels"] = [
                        {"level": 1, "price": new_tp1, "close_pct": 0.5},
                        {"level": 2, "price": new_tp2, "close_pct": 0.5}
                    ]
                if "FINAL_VERDICT" in s5:
                    v_text = s5["FINAL_VERDICT"]
                    v_text = re.sub(r"SL:\s*\$?\d+(\.\d+)?", f"SL: ${new_sl}", v_text)
                    v_text = re.sub(r"TP1:\s*\$?\d+(\.\d+)?", f"TP1: ${new_tp1}", v_text)
                    v_text = re.sub(r"TP2:\s*\$?\d+(\.\d+)?", f"TP2: ${new_tp2}", v_text)
                    s5["FINAL_VERDICT"] = v_text
        elif signal_direction == "LONG":
            if plan_sl > price or plan_tp1 < price:
                sl_dist = abs(plan_sl - price) if plan_sl != price else round(price * 0.025, 2)
                tp1_dist = abs(price - plan_tp1) if plan_tp1 != price else round(price * 0.035, 2)
                tp2_dist = abs(price - plan_tp2) if plan_tp2 != price else round(price * 0.070, 2)
                new_sl = round(price - sl_dist, 2)
                new_tp1 = round(price + tp1_dist, 2)
                new_tp2 = round(price + tp2_dist, 2)
                data["plan"]["sl"] = new_sl
                data["plan"]["tp1"] = new_tp1
                data["plan"]["tp2"] = new_tp2
                if "tp_levels" in data["plan"]:
                    data["plan"]["tp_levels"] = [
                        {"level": 1, "price": new_tp1, "close_pct": 0.5},
                        {"level": 2, "price": new_tp2, "close_pct": 0.5}
                    ]
                if "FINAL_VERDICT" in s5:
                    v_text = s5["FINAL_VERDICT"]
                    v_text = re.sub(r"SL:\s*\$?\d+(\.\d+)?", f"SL: ${new_sl}", v_text)
                    v_text = re.sub(r"TP1:\s*\$?\d+(\.\d+)?", f"TP1: ${new_tp1}", v_text)
                    v_text = re.sub(r"TP2:\s*\$?\d+(\.\d+)?", f"TP2: ${new_tp2}", v_text)
                    s5["FINAL_VERDICT"] = v_text

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

    def _call(self, prompt, fast=False, min_len=30):
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

        for name, fn in self._ordered(rotation.get(slot, rotation[0])):
            if not self._has_key(name):
                log.debug("  ⏭️  [%s] chưa cấu hình API Key — skip", name)
                continue
            if not _cb.is_up(name):
                log.debug("  ⏭️  [%s] circuit open — skip", name)
                continue
            try:
                result = fn(prompt)
                if result and len(result.strip()) >= min_len:
                    _cb.ok(name)
                    log.info("  Agent [%s] OK", name)
                    return result.strip()
                _cb.fail(name)
            except Exception as e:
                log.debug("  Agent [%s] chuyển dự phòng: %s", name, e)
                _cb.fail(name)
        return ""

    def query(self, system_prompt="", prompt="", max_tokens=100, fast=True):
        full_prompt = f"{system_prompt}\n\n{prompt}".strip() if system_prompt else prompt
        return self._call(full_prompt, fast=fast, min_len=1)

    def _summary(self, data):
        tsz   = data.get("traderssz", {})
        htf   = tsz.get("htf_opens_and_ranges", {})
        setup = tsz.get("setup", {})
        bias  = tsz.get("htf_bias", {})
        sess  = tsz.get("session", {})
        tfs = "\n".join(
            f"  {tf}: {r.get('direction','WAIT')} score={r.get('score',50)} ({r.get('note','')})"
            for tf, r in data.get("timeframes", {}).items()
        )
        return (
            f"Asset   : {data['symbol']} ({data.get('asset_type','')})\n"
            f"Gia     : ${round(data['price'], 4)}\n"
            f"Signal  : {data['final']} {data['confidence']}%\n"
            f"Session : {sess.get('session','?')} (Killzone={sess.get('is_killzone',False)})\n"
            f"Range   : {htf.get('range_label','?')} [RL=${htf.get('range_low',0)} | 50% EQ=${htf.get('eq_50',0)} | RH=${htf.get('range_high',0)}]\n"
            f"Zone    : {htf.get('market_zone','?')} (Pos={htf.get('range_pos_pct',50)}%)\n"
            f"PDH/PDL : ${htf.get('pdh',0)} / ${htf.get('pdl',0)} | Monday=${htf.get('monday_low',0)}-${htf.get('monday_high',0)}\n"
            f"Opens   : YO=${htf.get('year_open',0)} MO=${htf.get('monthly_open',0)} WO=${htf.get('weekly_open',0)} DO=${htf.get('daily_open',0)}\n"
            f"HTFBias : {bias.get('bias','?')} (4H={bias.get('h4_structure','?')})\n"
            f"SFP     : {setup.get('setup_type','NONE')} TF={setup.get('tf','?')} Swept=${setup.get('swept_level',0)} Extreme=${setup.get('sweep_extreme',0)}\n"
            f"TF:\n{tfs}"
        )

    def _p_stage1(self, data):
        atype = data.get("asset_type","CRYPTO")
        ctx = {"CRYPTO": "crypto futures",
               "STOCK":  "co phieu My",
               "GOLD":   "vang XAU",
               }.get(atype, "tai san tai chinh")
        return (
            "Ban la 4 chuyen gia Price Action theo phuong phap TraderSZ (Naked Chart) cho " + ctx + ".\n"
            "QUY TAC: Moi agent bat dau bang [TAG] trong ngoac vuong. KHONG dung chi bao tre (RSI/MACD/EMA/BB).\n"
            "Viet bang tieng Viet, ngan gon (toi da 80 tu/agent), dua vao Range, 50% EQ, Opens va Deviation/SFP.\n\n"
            "DU LIEU:\n" + self._summary(data) + "\n\n"
            "[TECHNICAL_ANALYST]\nPhan tich Dealing Range (PDH/PDL, Monday Range, PWH/PWL), Premium vs Discount & 50% EQ.\n"
            "Gia dang o bien hay No-Trade Zone? Bias: BULLISH/BEARISH/NEUTRAL\n\n"
            "[ONCHAIN_ANALYST]\nPhan tich thanh khoan bi quet (Liquidity Sweep / SFP) & Order Book / Funding.\n"
            "Bay thanh khoan da sap chua? Bias: BULLISH/BEARISH/NEUTRAL\n\n"
            "[MACRO_ANALYST]\nPhan tich vi the gia so voi Year/Month/Week/Day Open & Phien Killzone (Asia/London/NY).\n"
            "HTF Bias co ung ho? Bias: BULLISH/BEARISH/NEUTRAL\n\n"
            "[MOMENTUM_ANALYST]\nPhan tich nen xac nhan Return to Range, SL sau rau Deviation & Target EQ -> Bien doi dien.\n"
            "R:R co dat chuan TraderSZ? Bias: BULLISH/BEARISH/NEUTRAL"
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

        if consensus_signal == "SHORT":
            if sl < price or tp1 > price:
                sl_dist = abs(price - sl) if sl != price else round(price * 0.025, 2)
                tp1_dist = abs(tp1 - price) if tp1 != price else round(price * 0.035, 2)
                tp2_dist = abs(tp2 - price) if tp2 != price else round(price * 0.070, 2)
                sl = round(price + sl_dist, 2)
                tp1 = round(price - tp1_dist, 2)
                tp2 = round(price - tp2_dist, 2)
        elif consensus_signal == "LONG":
            if sl > price or tp1 < price:
                sl_dist = abs(sl - price) if sl != price else round(price * 0.025, 2)
                tp1_dist = abs(price - tp1) if tp1 != price else round(price * 0.035, 2)
                tp2_dist = abs(price - tp2) if tp2 != price else round(price * 0.070, 2)
                sl = round(price - sl_dist, 2)
                tp1 = round(price + tp1_dist, 2)
                tp2 = round(price + tp2_dist, 2)

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

        # Inject L2 Orderbook, Liquidity Sweep, and Bull/Bear Traps into prompts
        sweep = data.get("liquidity_sweep", {})
        ob    = data.get("orderbook", {})
        trap_info = data.get("trap_analysis", {})
        
        extra_context = ""
        if trap_info.get("detected"):
            t_type = "BẪY TĂNG GIÁ (BULL TRAP)" if trap_info.get("type") == "BULL_TRAP" else "BẪY GIẢM GIÁ (BEAR TRAP)"
            reasons = "; ".join(trap_info.get("reasons", []))
            extra_context += f"- ⚠️ PHÂN TÍCH BẪY CÁ MẶP: {t_type} [Mức độ: {trap_info.get('severity')}] tại giá ${trap_info.get('trap_price')}. Chi tiết lý do: {reasons}.\n"

        if sweep.get("detected"):
            extra_context += f"- 🔥 BẪY THANH KHOẢN ({sweep.get('type')}): Đã quét râu tại mức giá {sweep.get('price')} kèm Volume lớn. Hãy phân tích đây là hành động săn thanh khoản của cá mập trước khi đảo chiều.\n"
            
        if ob.get("detected"):
            extra_context += f"- 🧱 SỔ LỆNH L2 (Order Book): Tường mua cứng (hỗ trợ) tại {ob.get('support_wall')}, Tường bán cứng (kháng cự) tại {ob.get('resist_wall')}. Độ lệch Imbalance là {ob.get('imbalance')} (Âm = phe Bán áp đảo, Dương = phe Mua áp đảo).\n"

        sr = data.get("support_resistance", {})
        if sr and sr.get("nearest_support") and sr.get("nearest_resistance"):
            extra_context += f"- 📍 VÙNG S/R & VOLUME: Hỗ trợ gần tại ${sr.get('nearest_support')} (-{sr.get('support_dist_pct')}%), Kháng cự gần tại ${sr.get('nearest_resistance')} (+{sr.get('resistance_dist_pct')}%). Volume tại S/R: {sr.get('vol_ratio')}x ({sr.get('buy_pct')}% Mua / {sr.get('sell_pct')}% Bán).\n"

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

        # Đồng bộ hóa plan và sửa văn bản Stage 5 nếu có sự lệch hướng SL/TP
        price = data["price"]
        plan_sl = data["plan"].get("sl", price)
        plan_tp1 = data["plan"].get("tp1", price)
        plan_tp2 = data["plan"].get("tp2", price)

        if signal_direction == "SHORT":
            if plan_sl < price or plan_tp1 > price:
                sl_dist = abs(price - plan_sl) if plan_sl != price else round(price * 0.025, 2)
                tp1_dist = abs(plan_tp1 - price) if plan_tp1 != price else round(price * 0.035, 2)
                tp2_dist = abs(plan_tp2 - price) if plan_tp2 != price else round(price * 0.070, 2)
                new_sl = round(price + sl_dist, 2)
                new_tp1 = round(price - tp1_dist, 2)
                new_tp2 = round(price - tp2_dist, 2)
                data["plan"]["sl"] = new_sl
                data["plan"]["tp1"] = new_tp1
                data["plan"]["tp2"] = new_tp2
                if "tp_levels" in data["plan"]:
                    data["plan"]["tp_levels"] = [
                        {"level": 1, "price": new_tp1, "close_pct": 0.5},
                        {"level": 2, "price": new_tp2, "close_pct": 0.5}
                    ]
                if "FINAL_VERDICT" in s5:
                    v_text = s5["FINAL_VERDICT"]
                    v_text = re.sub(r"SL:\s*\$?\d+(\.\d+)?", f"SL: ${new_sl}", v_text)
                    v_text = re.sub(r"TP1:\s*\$?\d+(\.\d+)?", f"TP1: ${new_tp1}", v_text)
                    v_text = re.sub(r"TP2:\s*\$?\d+(\.\d+)?", f"TP2: ${new_tp2}", v_text)
                    s5["FINAL_VERDICT"] = v_text
        elif signal_direction == "LONG":
            if plan_sl > price or plan_tp1 < price:
                sl_dist = abs(plan_sl - price) if plan_sl != price else round(price * 0.025, 2)
                tp1_dist = abs(price - plan_tp1) if plan_tp1 != price else round(price * 0.035, 2)
                tp2_dist = abs(price - plan_tp2) if plan_tp2 != price else round(price * 0.070, 2)
                new_sl = round(price - sl_dist, 2)
                new_tp1 = round(price + tp1_dist, 2)
                new_tp2 = round(price + tp2_dist, 2)
                data["plan"]["sl"] = new_sl
                data["plan"]["tp1"] = new_tp1
                data["plan"]["tp2"] = new_tp2
                if "tp_levels" in data["plan"]:
                    data["plan"]["tp_levels"] = [
                        {"level": 1, "price": new_tp1, "close_pct": 0.5},
                        {"level": 2, "price": new_tp2, "close_pct": 0.5}
                    ]
                if "FINAL_VERDICT" in s5:
                    v_text = s5["FINAL_VERDICT"]
                    v_text = re.sub(r"SL:\s*\$?\d+(\.\d+)?", f"SL: ${new_sl}", v_text)
                    v_text = re.sub(r"TP1:\s*\$?\d+(\.\d+)?", f"TP1: ${new_tp1}", v_text)
                    v_text = re.sub(r"TP2:\s*\$?\d+(\.\d+)?", f"TP2: ${new_tp2}", v_text)
                    s5["FINAL_VERDICT"] = v_text

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
        tsz   = data.get("traderssz", {})
        htf   = tsz.get("htf_opens_and_ranges", {})
        setup = tsz.get("setup", {})
        bias  = tsz.get("htf_bias", {})
        sess  = tsz.get("session", {})
        tfs = "\n".join(
            f"  {tf}: {r.get('direction','WAIT')} score={r.get('score',50)} ({r.get('note','')})"
            for tf, r in data.get("timeframes", {}).items()
        )
        return (
            f"Asset   : {data['symbol']} ({data.get('asset_type','')})\n"
            f"Gia     : ${round(data['price'], 4)}\n"
            f"Signal  : {data['final']} {data['confidence']}%\n"
            f"Session : {sess.get('session','?')} (Killzone={sess.get('is_killzone',False)})\n"
            f"Range   : {htf.get('range_label','?')} [RL=${htf.get('range_low',0)} | 50% EQ=${htf.get('eq_50',0)} | RH=${htf.get('range_high',0)}]\n"
            f"Zone    : {htf.get('market_zone','?')} (Pos={htf.get('range_pos_pct',50)}%)\n"
            f"PDH/PDL : ${htf.get('pdh',0)} / ${htf.get('pdl',0)} | Monday=${htf.get('monday_low',0)}-${htf.get('monday_high',0)}\n"
            f"Opens   : YO=${htf.get('year_open',0)} MO=${htf.get('monthly_open',0)} WO=${htf.get('weekly_open',0)} DO=${htf.get('daily_open',0)}\n"
            f"HTFBias : {bias.get('bias','?')} (4H={bias.get('h4_structure','?')})\n"
            f"SFP     : {setup.get('setup_type','NONE')} TF={setup.get('tf','?')} Swept=${setup.get('swept_level',0)} Extreme=${setup.get('sweep_extreme',0)}\n"
            f"TF:\n{tfs}"
        )

    def _p_stage1(self, data):
        atype = data.get("asset_type","CRYPTO")
        ctx = {"CRYPTO": "crypto futures",
               "STOCK":  "co phieu My",
               "GOLD":   "vang XAU",
               }.get(atype, "tai san tai chinh")
        return (
            "Ban la 4 chuyen gia Price Action theo phuong phap TraderSZ (Naked Chart) cho " + ctx + ".\n"
            "QUY TAC: Moi agent bat dau bang [TAG] trong ngoac vuong. KHONG dung chi bao tre (RSI/MACD/EMA/BB).\n"
            "Viet bang tieng Viet, ngan gon (toi da 80 tu/agent), dua vao Range, 50% EQ, Opens va Deviation/SFP.\n\n"
            "DU LIEU:\n" + self._summary(data) + "\n\n"
            "[TECHNICAL_ANALYST]\nPhan tich Dealing Range (PDH/PDL, Monday Range, PWH/PWL), Premium vs Discount & 50% EQ.\n"
            "Gia dang o bien hay No-Trade Zone? Bias: BULLISH/BEARISH/NEUTRAL\n\n"
            "[ONCHAIN_ANALYST]\nPhan tich thanh khoan bi quet (Liquidity Sweep / SFP) & Order Book / Funding.\n"
            "Bay thanh khoản đã sập chưa? Bias: BULLISH/BEARISH/NEUTRAL\n\n"
            "[MACRO_ANALYST]\nPhan tich vi the gia so voi Year/Month/Week/Day Open & Phien Killzone (Asia/London/NY).\n"
            "HTF Bias co ung ho? Bias: BULLISH/BEARISH/NEUTRAL\n\n"
            "[MOMENTUM_ANALYST]\nPhan tich nen xac nhan Return to Range, SL sau rau Deviation & Target EQ -> Bien doi dien.\n"
            "R:R co dat chuan TraderSZ? Bias: BULLISH/BEARISH/NEUTRAL"
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

        if consensus_signal == "SHORT":
            if sl < price or tp1 > price:
                sl_dist = abs(price - sl) if sl != price else round(price * 0.025, 2)
                tp1_dist = abs(tp1 - price) if tp1 != price else round(price * 0.035, 2)
                tp2_dist = abs(tp2 - price) if tp2 != price else round(price * 0.070, 2)
                sl = round(price + sl_dist, 2)
                tp1 = round(price - tp1_dist, 2)
                tp2 = round(price - tp2_dist, 2)
        elif consensus_signal == "LONG":
            if sl > price or tp1 < price:
                sl_dist = abs(sl - price) if sl != price else round(price * 0.025, 2)
                tp1_dist = abs(price - tp1) if tp1 != price else round(price * 0.035, 2)
                tp2_dist = abs(price - tp2) if tp2 != price else round(price * 0.070, 2)
                sl = round(price - sl_dist, 2)
                tp1 = round(price + tp1_dist, 2)
                tp2 = round(price + tp2_dist, 2)

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
