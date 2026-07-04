# ═══════════════════════════════════════════════════════════
# 2. TELEGRAM BOT — v6.1 (Channel + MiniApp integration)
# ═══════════════════════════════════════════════════════════
import re
import json
import logging
import requests
from analyzer.config import Config

cfg = Config()
log = logging.getLogger("TelegramBot")


class TelegramBot:
    BASE = "https://api.telegram.org"

    def __init__(self):
        self.token   = cfg.TELEGRAM_TOKEN
        self.chat_id = cfg.TELEGRAM_CHAT_ID

    @staticmethod
    def _to_plain(text: str) -> str:
        t = str(text)
        t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", t)
        t = re.sub(r"<[^>]*>", "", t)
        for esc, rep in [("&amp;","&"),("&lt;","<"),("&gt;",">"),("&quot;",'"'),("&#39;","'")]:
            t = t.replace(esc, rep)
        return t.strip()

    @staticmethod
    def _to_safe_html(text: str) -> str:
        ALLOWED_OPEN  = {"b", "i", "code", "pre", "u", "s"}
        ALLOWED_CLOSE = {"/" + t for t in ALLOWED_OPEN}
        t      = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(text))
        parts  = re.split(r"(<[^>]{0,100}>)", t)
        result = []
        opened = []

        for part in parts:
            if re.match(r"^<[^>]{0,100}>$", part):
                inner = part[1:-1].strip().lower()
                if inner in ALLOWED_OPEN:
                    result.append("<" + inner + ">")
                    opened.append(inner)
                elif inner in ALLOWED_CLOSE:
                    tag = inner[1:]
                    if tag in opened:
                        while opened and opened[-1] != tag:
                            result.append("</" + opened.pop() + ">")
                        if opened:
                            opened.pop()
                        result.append("</" + tag + ">")
                else:
                    safe = part.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
                    result.append(safe)
            else:
                p = re.sub(r"&(?!(amp|lt|gt|quot|#[0-9]+);)", "&amp;", part)
                p = p.replace("<", "&lt;").replace(">", "&gt;")
                result.append(p)

        for tag in reversed(opened):
            result.append("</" + tag + ">")
        return "".join(result)

    def send_connect_button(self, target_chat_id):
        token = cfg.TELEGRAM_REGISTER_TOKEN
        if not token:
            log.error("Thiếu TELEGRAM_REGISTER_TOKEN")
            return None
        url_app = (cfg.RENDER_EXTERNAL_URL or "https://auto-trade-v6.onrender.com") + "/miniapp/connect"
        payload = {
            "chat_id":      target_chat_id,
            "parse_mode":   "HTML",
            "text":         (
                "👋 <b>Chào mừng đến với SignalBot v6.1!</b>\n\n"
                "🤖 Copy Trade tự động trên BingX Futures\n"
                "🔒 API Key được mã hóa AES-256\n"
                "📊 Hệ thống phân tích 12 AI Agents\n\n"
                "<b>Bấm nút bên dưới để kết nối:</b>"
            ),
            "reply_markup": json.dumps({"inline_keyboard": [[{
                "text":    "🔗 Kết Nối BingX API",
                "web_app": {"url": url_app},
            }]]})
        }
        try:
            r = requests.post(f"{self.BASE}/bot{token}/sendMessage", json=payload, timeout=10)
            if r.ok:
                log.info("✅ Sent connect button to %s", target_chat_id)
                return r.json()
            log.error("Connect button error: %s", r.text[:200])
        except Exception as e:
            log.error("send_connect_button: %s", e)
        return None

    def send_user_dashboard_button(self, target_chat_id):
        token = cfg.TELEGRAM_REGISTER_TOKEN
        if not token:
            return None
        url_app = (cfg.RENDER_EXTERNAL_URL or "https://auto-trade-v6.onrender.com") + "/my-dashboard"
        payload = {
            "chat_id":      target_chat_id,
            "text":         "📊 Xem số dư và vị thế đang mở của bạn:",
            "reply_markup": json.dumps({"inline_keyboard": [[{
                "text":    "📊 Mở Dashboard Của Tôi",
                "web_app": {"url": url_app},
            }]]})
        }
        try:
            requests.post(f"{self.BASE}/bot{token}/sendMessage", json=payload, timeout=5)
        except Exception as e:
            log.error("send_user_dashboard_button: %s", e)

    def send(self, text: str):
        cid = str(self.chat_id).strip()
        if not cid or not self.token:
            log.error("TELEGRAM_TOKEN hoặc CHAT_ID trống")
            return None
        try:
            chat_id = int(cid)
        except ValueError:
            log.error("CHAT_ID sai định dạng: %s", cid)
            return None

        html = self._to_safe_html(text)
        if len(html) > 4096:
            html = html[:4000] + "\n<i>[...]</i>"
        try:
            r = requests.post(
                f"{self.BASE}/bot{self.token}/sendMessage",
                json={"chat_id": chat_id, "text": html,
                      "parse_mode": "HTML", "disable_web_page_preview": True},
                timeout=15)
            if r.ok:
                return r.json()
            resp = r.json()
            new_cid = resp.get("parameters", {}).get("migrate_to_chat_id")
            if new_cid:
                log.warning("Group migrate! Cập nhật TELEGRAM_CHAT_ID: %d", new_cid)
                self.chat_id = str(new_cid)
                r2 = requests.post(
                    f"{self.BASE}/bot{self.token}/sendMessage",
                    json={"chat_id": new_cid, "text": html,
                          "parse_mode": "HTML", "disable_web_page_preview": True},
                    timeout=15)
                if r2.ok:
                    return r2.json()
            log.warning("Telegram HTML %d: %s", r.status_code, resp.get("description","")[:80])
        except Exception as e:
            log.warning("Telegram HTML error: %s", e)

        plain = self._to_plain(text)
        if len(plain) > 4096:
            plain = plain[:4000] + "\n[...]"
        try:
            r = requests.post(
                f"{self.BASE}/bot{self.token}/sendMessage",
                json={"chat_id": chat_id, "text": plain, "disable_web_page_preview": True},
                timeout=15)
            if r.ok:
                return r.json()
        except Exception as e:
            log.error("Telegram plain error: %s", e)
        return None

    def format_signal(self, data: dict, llm_text: str, llm_name: str) -> str:
        sig   = data["final"]
        plan  = data["plan"]
        fibo  = data.get("fibo", {})
        wy    = data.get("wyckoff", {})
        cvd   = data.get("cvd", {})
        bo    = data.get("breakout", {})
        wh    = data.get("whale", {})
        vol   = data.get("volume_1h", {})
        lvls  = fibo.get("levels", {})
        conf  = data["confidence"]
        atype = data.get("asset_type", "CRYPTO")
        
        sweep = data.get("liquidity_sweep", {})
        ob    = data.get("orderbook", {})

        stat_conf = data.get("stat_confidence")
        stat_note = ""
        if stat_conf is not None and abs(stat_conf - conf) >= 2:
            delta    = round(stat_conf - conf, 1)
            sign     = "+" if delta > 0 else ""
            icon     = "🟢" if stat_conf >= 75 else "🟡" if stat_conf >= 68 else "🔴"
            stat_note = (f"\n  └ {icon} AI Stat: <b>{stat_conf:.1f}%</b> "
                         f"({sign}{delta}% vs raw)")

        candle     = data.get("candle", {})
        ms_4h      = data.get("market_structure_4h", {})
        bull_pats  = candle.get("bull_patterns", [])
        bear_pats  = candle.get("bear_patterns", [])
        candle_bias = candle.get("bias", "NEUTRAL")
        ms_struct   = ms_4h.get("structure", "SIDEWAYS")

        CANDLE_IC = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪"}
        MS_IC     = {"UPTREND": "🔼", "DOWNTREND": "🔽",
                     "SIDEWAYS": "↔️", "EXPANDING": "↕️", "CONTRACTING": "⏸"}

        candle_line = ""
        if bull_pats or bear_pats:
            pat_text = ", ".join((bull_pats + bear_pats)[:4])
            candle_line = (f"\n  ├ Nến 1H+4H : {CANDLE_IC.get(candle_bias,'⚪')} {candle_bias}"
                           f" | {pat_text}")

        HEADER = {"LONG": "🟢 🚀 <b>TÍN HIỆU MUA</b>",
                  "SHORT": "🔴 📉 <b>TÍN HIỆU BÁN</b>",
                  "WAIT": "🟡 ⏳ <b>ĐANG CHỜ ĐỢI</b>"}
        AICON  = {"CRYPTO": "🪙", "STOCK": "📈", "GOLD": "🥇"}
        ANAME  = {"BTCUSDT": "Bitcoin", "ETHUSDT": "Ethereum", "BNBUSDT": "BNB Chain",
                  "HYPEUSDT": "HyperLiquid", "TSLA": "Tesla", "NVDA": "NVIDIA",
                  "SPY": "S&P 500 ETF", "QQQ": "Nasdaq 100 ETF", "XAUUSD": "Vàng XAU/USD"}
        WY_IC  = {"ACCUMULATION": "🔵", "MARKUP": "🟢", "RE-ACCUMULATION": "🟩",
                  "DISTRIBUTION": "🔴", "MARKDOWN": "⛔", "TRANSITION": "⚪"}
        BI_IC  = {"BULLISH": "📈", "BEARISH": "📉", "NEUTRAL": "➡️"}
        TF_IC  = {"LONG": "🟢", "SHORT": "🔴", "WAIT": "⚪"}
        CVD_IC = {"BULLISH": "🟢", "BEARISH": "🔴",
                  "BULLISH_DIV": "💚", "BEARISH_DIV": "❤️", "NEUTRAL": "⚪"}
        PRESS_IC = {"STRONG_BUY": "🟢🟢", "BUY": "🟢",
                    "STRONG_SELL": "🔴🔴", "SELL": "🔴", "NEUTRAL": "⚪"}

        conf_bar  = "█" * int(conf / 10) + "░" * (10 - int(conf / 10))
        tf_line   = "  ".join(
            TF_IC.get(r.get("direction","WAIT"),"⚪") + tf.upper()
            for tf, r in data.get("timeframes", {}).items())
        wy_ph     = wy.get("phase", "?")
        wy_bi     = wy.get("bias", "?")
        cvd_tr    = cvd.get("trend", "?")
        cvd_div   = " ⚠️ DIV!" if cvd.get("divergence") else ""
        golden    = "✅ Golden Zone 0.382-0.618" if fibo.get("in_golden") else "⬜ Ngoài Golden Zone"
        fa        = "⬆️" if fibo.get("trend") == "UPTREND" else "⬇️"

        ev_lines  = ("\n" + "\n".join("   ⚡ " + e for e in wy.get("events", []))
                     if wy.get("events") else "")
        bo_line   = ("\n🚨 <b>BREAKOUT</b>: " + str(bo.get("desc",""))
                     if bo.get("type","NONE") != "NONE" else "")
        wh_line   = ("\n" + str(wh.get("desc","")) if wh.get("detected") else "")
        mkt_line  = ("\n🏛️ " + str(data.get("mkt_note","")) if data.get("mkt_note") else "")
        
        sweep_line = ("\n🔥 <b>BẪY THANH KHOẢN (" + str(sweep.get("type", "")) + ")</b>: Quét râu tại <code>$" + 
                      str(round(sweep.get("price", 0), 2)) + "</code>" if sweep.get("detected") else "")

        if atype == "CRYPTO":
            oi_sec = (f"  ├ OI Delta : {data.get('oi_delta',0)}% → {data.get('oi_signal','?')}\n"
                      f"  ├ OI Desc  : {data.get('oi_desc','?')}\n"
                      f"  └ Funding  : <code>{data.get('funding',0)}%</code>")
        elif atype == "GOLD":
            oi_sec = "  └ Xem DXY + lãi suất Fed khi giao dịch vàng"
        else:
            oi_sec = "  └ Xem earnings + macro Mỹ khi giao dịch cổ phiếu"

        sym_display = AICON.get(atype,"📊") + " " + ANAME.get(data["symbol"], data["symbol"])
        llm_clean   = self._to_safe_html(llm_text or "")
        
        ob_section = []
        if ob.get("detected"):
            obi = ob.get("imbalance", 0)
            obi_icon = "🟢" if obi > 0 else ("🔴" if obi < 0 else "⚪")
            ob_section = [
                "",
                "🧱 <b>SỔ LỆNH L2 (ORDER BOOK)</b>",
                f"  ├ Tường Bán (Cản) : <code>${ob.get('resist_wall')}</code>",
                f"  ├ Tường Mua (Đỡ)  : <code>${ob.get('support_wall')}</code>",
                f"  └ Mất cân bằng    : {obi_icon} <b>{obi}</b>"
            ]

        lines = [
            HEADER.get(sig, sig) + " — " + sym_display,
            "━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"💰 Giá       : <code>${round(data['price'],2)}</code>",
            f"🎯 Độ tin cậy: <b>{conf}%</b> <code>[{conf_bar}]</code>" + stat_note,
            "📊 Khung giờ : " + tf_line,
            bo_line + wh_line + mkt_line + sweep_line,
            "",
            "⚡ <b>SMART SIGNALS</b>",
            "  ├ CVD      : " + CVD_IC.get(cvd_tr,"⚪") + " " + cvd_tr + cvd_div,
            "  ├ Vol      : " + str(vol.get("vol_trend","?")) + " x" + str(vol.get("vol_ratio",1)),
            "  ├ OBV      : " + str(vol.get("obv_trend","?")),
            "  ├ Pressure : " + PRESS_IC.get(vol.get("pressure","NEUTRAL"),"⚪") + " " +
                               str(vol.get("pressure","?")) + " (" + str(vol.get("buy_pct",50)) + "% buy)",
            "  ├ VWAP     : <code>$" + str(vol.get("vwap",0)) + "</code> (" + str(vol.get("vwap_signal","?")) + ")",
            "  ├ POC      : <code>$" + str(vol.get("poc",0)) + "</code>",
            candle_line,
            "  ├ MS 4H    : " + MS_IC.get(ms_struct,"↔️") + " " + ms_struct +
                               (" ⚡BOS" if ms_4h.get("bos") else ""),
            oi_sec,
            "",
            "📐 <b>FIBONACCI (1H)</b>",
            "  ├ Xu hướng : " + fa + " " + str(fibo.get("trend","?")),
            "  ├ Vùng giá : <code>" + str(fibo.get("zone","?")) + "</code>",
            "  ├ " + golden,
            "  ├ 0.382 ▶ <code>$" + str(lvls.get("0.382","?")) + "</code>",
            "  ├ 0.618 ▶ <code>$" + str(lvls.get("0.618","?")) + "</code>",
            "  └ Ext 1.618: <code>$" + str(lvls.get("1.618","?")) + "</code>",
            "",
            "📊 <b>WYCKOFF (4H)</b>",
            "  ├ Pha    : " + WY_IC.get(wy_ph,"⚪") + " <b>" + wy_ph + "</b>",
            "  ├ Hướng  : " + BI_IC.get(wy_bi,"➡️") + " " + wy_bi,
            "  ├ Volume : " + str(wy.get("vol_trend","?")) + " | Pos: " + str(wy.get("pos_range","?")) + "%",
            "  └ " + str(wy.get("action","?")),
            ev_lines,
        ]
        
        lines.extend(ob_section)
        
        lines.extend([
            "",
            "📌 <b>KẾ HOẠCH LỆNH</b>",
            "  ├ Vào lệnh  : <code>$" + str(plan["entry"]) + "</code>",
            "  ├ 🛑 Cắt lỗ: <code>$" + str(plan["sl"]) + "</code> (-" + str(data.get("sl_pct","1.5")) + "%)",
            "  ├ 🎯 TP1    : <code>$" + str(plan["tp1"]) + "</code>",
            "  ├ 🏆 TP2    : <code>$" + str(plan["tp2"]) + "</code>",
            "  └ ⚖️ R:R     : 1:" + str(data.get("rr_ratio","2.0")),
            "",
            "🤖 <b>AI</b> <i>(" + llm_name + ")</i>",
            "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄",
            llm_clean,
            "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄",
            "",
            "⏰ " + data.get("timestamp","") + "  |  💼 " + str(data.get("sl_pct","2")) + "% rủi ro/lệnh",
        ])
        return "\n".join(lines)
