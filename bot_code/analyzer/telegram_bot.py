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
        conf  = data["confidence"]
        atype = data.get("asset_type", "CRYPTO")

        tsz      = data.get("traderssz", {})
        htf      = tsz.get("htf_opens_and_ranges", {})
        htf_bias = tsz.get("htf_bias", {})
        session  = tsz.get("session", {})
        setup    = tsz.get("setup", {})
        ob       = data.get("orderbook", {})

        stat_conf = data.get("stat_confidence")
        stat_note = ""
        if stat_conf is not None and abs(stat_conf - conf) >= 2:
            delta    = round(stat_conf - conf, 1)
            sign     = "+" if delta > 0 else ""
            icon     = "🟢" if stat_conf >= 75 else "🟡" if stat_conf >= 68 else "🔴"
            stat_note = (f"\n  └ {icon} AI Stat: <b>{stat_conf:.1f}%</b> "
                         f"({sign}{delta}% vs raw)")

        HEADER = {"LONG": "🟢 🚀 <b>TRADERSZ LONG (QUÉT ĐÁY RANGE)</b>",
                  "SHORT": "🔴 📉 <b>TRADERSZ SHORT (QUÉT ĐỈNH RANGE)</b>",
                  "WAIT": "🟡 ⏳ <b>TRADERSZ CHỜ QUÉT BIÊN RANGE</b>"}
        AICON  = {"CRYPTO": "🪙", "STOCK": "📈", "GOLD": "🥇"}
        ANAME  = {"BTCUSDT": "Bitcoin", "ETHUSDT": "Ethereum", "BNBUSDT": "BNB Chain",
                  "HYPEUSDT": "HyperLiquid", "TSLA": "Tesla", "NVDA": "NVIDIA",
                  "SPY": "S&P 500 ETF", "QQQ": "Nasdaq 100 ETF", "NCCOGOLD2USD-USDT": "Vàng (XAU)"}
        TF_IC  = {"LONG": "🟢", "SHORT": "🔴", "WAIT": "⚪"}
        ZONE_IC = {"PREMIUM": "🔴 PREMIUM (Canh SHORT)", "DISCOUNT": "🟢 DISCOUNT (Canh LONG)", "NO_TRADE_EQ_ZONE": "⛔ NO-TRADE ZONE (Giữa 50% EQ)"}

        conf_bar  = "█" * int(conf / 10) + "░" * (10 - int(conf / 10))
        tf_line   = "  ".join(
            TF_IC.get(r.get("direction", "WAIT"), "⚪") + tf.upper()
            for tf, r in data.get("timeframes", {}).items())

        sym_display = AICON.get(atype, "📊") + " " + ANAME.get(data["symbol"], data["symbol"])
        llm_clean   = self._to_safe_html(llm_text or "")

        price = float(data.get("price", 0))
        zone_str = ZONE_IC.get(htf.get("market_zone", ""), htf.get("market_zone", "?"))

        setup_block = []
        if setup.get("detected"):
            setup_block = [
                "",
                "🔥 <b>XÁC NHẬN DEVIATION / SFP (RETURN TO RANGE)</b>",
                f"  ├ Khung & Range : <b>{str(setup.get('tf','?')).upper()}</b> · {setup.get('range_label','')}",
                f"  ├ Mô hình       : <b>{setup.get('setup_type','SFP')}</b>" + (" (Retest biên ✅)" if setup.get("is_retest") else ""),
                f"  ├ Mốc đã quét   : <code>${setup.get('swept_level', 0)}</code> (Đỉnh/Đáy râu: <code>${setup.get('sweep_extreme', 0)}</code>)",
            ]
        else:
            setup_block = [
                "",
                f"⏳ <b>TRẠNG THÁI:</b> {tsz.get('reason', 'Chờ giá quét biên RH/RL')}",
            ]

        ob_section = []
        if ob.get("detected"):
            obi = ob.get("imbalance", 0)
            obi_icon = "🟢" if obi > 0 else ("🔴" if obi < 0 else "⚪")
            ob_section = [
                "",
                "🧱 <b>THANH KHOẢN ORDER BOOK</b>",
                f"  ├ Tường Bán : <code>${ob.get('resist_wall')}</code> | Tường Mua: <code>${ob.get('support_wall')}</code>",
                f"  └ Imbalance : {obi_icon} <b>{obi}</b>",
            ]

        asia_line = (
            f"  ├ Asia (00-08h): <code>${htf.get('asia_low',0)}</code> – <code>${htf.get('asia_high',0)}</code>"
            if htf.get("asia_ready") else "  ├ Asia (00-08h): Đang hình thành"
        )

        lines = [
            HEADER.get(sig, sig) + " — " + sym_display,
            "━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"💰 Giá       : <code>${round(price, 4)}</code>",
            f"🎯 Độ tin cậy: <b>{conf}%</b> <code>[{conf_bar}]</code>" + stat_note,
            f"📊 Đa khung  : {tf_line}",
            f"🕐 Phiên     : {session.get('session','?')} ({'Killzone 🔥' if session.get('is_killzone') else 'Normal'})",
            *setup_block,
            "",
            "📐 <b>TRADERSZ DEALING RANGE & 50% EQ</b>",
            f"  ├ Range chính : <b>{htf.get('range_label', htf.get('range_name','?'))}</b>",
            f"  ├ Range High  : <code>${htf.get('range_high',0)}</code>",
            f"  ├ 50% EQ      : <code>${htf.get('eq_50',0)}</code> (Vị trí: {htf.get('range_pos_pct',50)}%)",
            f"  ├ Range Low   : <code>${htf.get('range_low',0)}</code>",
            f"  ├ Phân vùng   : <b>{zone_str}</b>",
            f"  ├ PDH / PDL   : <code>${htf.get('pdh',0)}</code> / <code>${htf.get('pdl',0)}</code>",
            f"  ├ Monday Range: <code>${htf.get('monday_low',0)}</code> – <code>${htf.get('monday_high',0)}</code>",
            f"  ├ Weekly Range: <code>${htf.get('pwl',0)}</code> – <code>${htf.get('pwh',0)}</code>",
            asia_line,
            "",
            "⚓ <b>MACRO OPENS (Y · M · W · D)</b>",
            f"  ├ Year Open   : <code>${htf.get('year_open',0)}</code> ({'Trên ▲' if price >= htf.get('year_open',0) else 'Dưới ▼'})",
            f"  ├ Month Open  : <code>${htf.get('monthly_open',0)}</code> ({'Trên ▲' if price >= htf.get('monthly_open',0) else 'Dưới ▼'})",
            f"  ├ Week Open   : <code>${htf.get('weekly_open',0)}</code> ({'Trên ▲' if price >= htf.get('weekly_open',0) else 'Dưới ▼'})",
            f"  ├ Daily Open  : <code>${htf.get('daily_open',0)}</code> ({'Trên ▲' if price >= htf.get('daily_open',0) else 'Dưới ▼'})",
            f"  └ HTF Bias    : <b>{htf_bias.get('bias','RANGE_NEUTRAL')}</b> (4H: {htf_bias.get('h4_structure','?')})",
        ]

        lines.extend(ob_section)

        lines.extend([
            "",
            "📌 <b>KẾ HOẠCH LỆNH TRADERSZ (RANGE-TO-RANGE)</b>",
            f"  ├ Entry     : <code>${plan['entry']}</code>",
            f"  ├ 🛑 SL Râu : <code>${plan['sl']}</code> (-{data.get('sl_pct','1.5')}%)",
            f"  ├ 🎯 TP1 (50% @ EQ): <code>${plan.get('tp1', 0)}</code> (+Dời SL Hòa Vốn)",
            f"  ├ 🏆 TP2 (50% @ Biên): <code>${plan.get('tp2', 0)}</code>",
            f"  ├ ⚡ Đòn bẩy: <b>{data.get('dynamic_leverage', plan.get('recommended_leverage', 5))}x</b> ({data.get('leverage_note', 'Linh động 5x-15x')})",
            f"  └ ⚖️ R:R    : <b>1:{data.get('rr_ratio','2.0')}</b>",
            "",
            "📐 <b>PHÂN TÍCH PA</b> <i>(" + llm_name + ")</i>",
            "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄",
            llm_clean,
            "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄",
            "",
            "⏰ " + data.get("timestamp", "") + "  |  Pure Naked Chart · TraderSZ Method",
        ])
        return "\n".join(lines)

