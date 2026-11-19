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

        st_type = setup.get("setup_type", "")
        is_breakout = st_type == "RANGE_BREAKOUT_CONTINUATION"
        is_reclaim = st_type == "LEVEL_RECLAIM_SR_FLIP"
        if is_breakout:
            HEADER = {
                "LONG": "🟢 🚀 <b>TRADERSZ LONG (PHÁ ĐỈNH RANGE · TIẾP DIỄN TĂNG)</b>",
                "SHORT": "🔴 📉 <b>TRADERSZ SHORT (PHÁ ĐÁY RANGE · TIẾP DIỄN GIẢM)</b>",
                "WAIT": "🟡 ⏳ <b>TRADERSZ CHỜ XÁC NHẬN RANGE</b>",
            }
        elif is_reclaim:
            HEADER = {
                "LONG": "🟢 ♻️ <b>TRADERSZ LONG (RECLAIM OPEN · S/R FLIP)</b>",
                "SHORT": "🔴 ♻️ <b>TRADERSZ SHORT (MẤT MỐC OPEN · S/R FLIP)</b>",
                "WAIT": "🟡 ⏳ <b>TRADERSZ CHỜ QUÉT BIÊN RANGE</b>",
            }
        else:
            HEADER = {
                "LONG": "🟢 🚀 <b>TRADERSZ LONG (QUÉT ĐÁY RANGE)</b>",
                "SHORT": "🔴 📉 <b>TRADERSZ SHORT (QUÉT ĐỈNH RANGE)</b>",
                "WAIT": "🟡 ⏳ <b>TRADERSZ CHỜ QUÉT BIÊN RANGE</b>",
            }
        AICON  = {"CRYPTO": "🪙", "STOCK": "📈", "GOLD": "🥇", "SILVER": "🥈"}
        ANAME  = {"BTCUSDT": "Bitcoin", "ETHUSDT": "Ethereum", "BNBUSDT": "BNB Chain",
                  "SOLUSDT": "Solana", "HYPEUSDT": "HyperLiquid", "TSLA": "Tesla",
                  "NCSKTSLA2USD-USDT": "Tesla (TSLA)", "NCSKTSLA2USDUSDT": "Tesla (TSLA)",
                  "NVDA": "NVIDIA", "SPY": "S&P 500 ETF", "QQQ": "Nasdaq 100 ETF",
                  "NCCOGOLD2USD-USDT": "Vàng (XAU)",
                  "NCCOXAG2USD-USDT": "Bạc (XAG)", "NCCOXAG2USDUSDT": "Bạc (XAG)"}
        TF_IC  = {"LONG": "🟢", "SHORT": "🔴", "WAIT": "⚪"}
        ZONE_IC = {
            "BREAKOUT_ABOVE_RH": "🟢 PHÁ ĐỈNH RANGE (Breakout Tăng trên RH)",
            "BREAKOUT_BELOW_RL": "🔴 PHÁ ĐÁY RANGE (Breakout Giảm dưới RL)",
            "PREMIUM": "🔴 PREMIUM (Canh SHORT)",
            "DISCOUNT": "🟢 DISCOUNT (Canh LONG)",
            "NO_TRADE_EQ_ZONE": "⛔ NO-TRADE ZONE (Giữa 50% EQ)",
        }

        conf_bar  = "█" * int(conf / 10) + "░" * (10 - int(conf / 10))
        tf_line   = "  ".join(
            TF_IC.get(r.get("direction", "WAIT"), "⚪") + tf.upper()
            for tf, r in data.get("timeframes", {}).items())

        sym_display = AICON.get(atype, "📊") + " " + ANAME.get(data["symbol"], data["symbol"])
        llm_clean   = self._to_safe_html(llm_text or "")

        price = float(data.get("price", 0))
        raw_zone = htf.get("market_zone", "")
        pos_pct = float(htf.get("range_pos_pct", 50) or 50)
        if pos_pct < 0 and raw_zone == "DISCOUNT":
            raw_zone = "BREAKOUT_BELOW_RL"
        elif pos_pct > 100 and raw_zone == "PREMIUM":
            raw_zone = "BREAKOUT_ABOVE_RH"
        zone_str = ZONE_IC.get(raw_zone, raw_zone or "?")

        confluences = []
        if setup.get("msb_confirmed"):
            confluences.append(f"15M MSB ✅ (${setup.get('msb_level', 0)})")
        if setup.get("liquidity_pool_swept"):
            confluences.append("Quét cụm EQH/EQL ✅")
        if setup.get("is_failed_breakout"):
            confluences.append("Failed Breakout ⚡")
        conf_line = [f"  └ Hợp lưu Pro   : <b>{' · '.join(confluences)}</b>"] if confluences else []

        setup_block = []
        if setup.get("detected"):
            if is_breakout:
                setup_block = [
                    "",
                    "🚀 <b>XÁC NHẬN PHÁ VỠ RANGE (BREAKOUT CONTINUATION)</b>",
                    f"  ├ Khung & Range : <b>{str(setup.get('tf','?')).upper()}</b> · {setup.get('range_label','')}",
                    f"  ├ Mô hình       : <b>{st_type}</b>" + (" (Retest biên ✅)" if setup.get("is_retest") else ""),
                    f"  ├ Biên bị phá   : <code>${setup.get('swept_level', 0)}</code> (Điểm tựa SL: <code>${setup.get('sweep_extreme', 0)}</code>)",
                    *conf_line,
                ]
            elif is_reclaim:
                setup_block = [
                    "",
                    "♻️ <b>XÁC NHẬN RECLAIM LEVEL &amp; S/R FLIP</b>",
                    f"  ├ Khung & Mốc   : <b>{str(setup.get('tf','?')).upper()}</b> · {setup.get('range_label','')}",
                    f"  ├ Mô hình       : <b>{st_type}</b>" + (" (Retest ✅)" if setup.get("is_retest") else ""),
                    f"  ├ Mốc Reclaim   : <code>${setup.get('swept_level', 0)}</code> (Râu quét: <code>${setup.get('sweep_extreme', 0)}</code>)",
                    *conf_line,
                ]
            else:
                setup_block = [
                    "",
                    "🔥 <b>XÁC NHẬN DEVIATION / SFP (RETURN TO RANGE)</b>",
                    f"  ├ Khung & Range : <b>{str(setup.get('tf','?')).upper()}</b> · {setup.get('range_label','')}",
                    f"  ├ Mô hình       : <b>{setup.get('setup_type','SFP')}</b>" + (" (Retest biên ✅)" if setup.get("is_retest") else ""),
                    f"  ├ Mốc đã quét   : <code>${setup.get('swept_level', 0)}</code> (Đỉnh/Đáy râu: <code>${setup.get('sweep_extreme', 0)}</code>)",
                    *conf_line,
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

        btc_ctx = data.get("btc_context") or tsz.get("btc_bias") or {}
        btc_leader_lines = []
        if atype == "CRYPTO" and "BTC" not in str(data.get("symbol", "")).upper() and btc_ctx:
            btc_leader_lines.append(
                f"  ├ BTC Leader  : <b>{btc_ctx.get('bias', 'NEUTRAL')}</b> ({btc_ctx.get('btc_signal', 'WAIT')}) · {self._to_safe_html(btc_ctx.get('reason', ''))}"
            )

        pools = htf.get("liquidity_pools", {}) or {}
        eqh_str = ", ".join(f"${x}" for x in (pools.get("eqh") or [])[:2]) or "Không"
        eql_str = ", ".join(f"${x}" for x in (pools.get("eql") or [])[:2]) or "Không"
        m_rule = htf.get("monday_rule_state", "STANDARD")
        m_tag = " (Đang tạo biên Thứ Hai · -25% size)" if m_rule == "MONDAY_FORMING" else (" (Cửa sổ săn Monday 🔥)" if m_rule == "MONDAY_HUNT_WINDOW" else "")

        lines = [
            HEADER.get(sig, sig) + " — " + sym_display,
            "━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"💰 Giá       : <code>${round(price, 4)}</code>",
            f"🎯 Độ tin cậy: <b>{conf}%</b> <code>[{conf_bar}]</code>" + stat_note,
            f"📊 Đa khung  : {tf_line}",
            f"🕐 Phiên     : {session.get('session','?')} ({'Killzone 🔥' if session.get('is_killzone') else 'Normal'})",
            *setup_block,
            "",
            "📐 <b>TRADERSZ DEALING RANGE &amp; 50% EQ</b>",
            f"  ├ Range chính : <b>{htf.get('range_label', htf.get('range_name','?'))}</b>{m_tag}",
            f"  ├ Range High  : <code>${htf.get('range_high',0)}</code> (75%: <code>${htf.get('q75',0)}</code>)",
            f"  ├ 50% EQ      : <code>${htf.get('eq_50',0)}</code> (Vị trí: {htf.get('range_pos_pct',50)}%)",
            f"  ├ Range Low   : <code>${htf.get('range_low',0)}</code> (25%: <code>${htf.get('q25',0)}</code>)",
            f"  ├ Phân vùng   : <b>{zone_str}</b>",
            f"  ├ Thanh khoản : EQH [{eqh_str}] | EQL [{eql_str}]",
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
            *btc_leader_lines,
            f"  └ HTF Bias    : <b>{htf_bias.get('bias','RANGE_NEUTRAL')}</b> (4H: {htf_bias.get('h4_structure','?')})",
        ]

        lines.extend(ob_section)

        tp_lvls = plan.get("tp_levels") or []
        tp1_pct_lbl = int(round(float(tp_lvls[0].get("close_pct", 0.5)) * 100)) if len(tp_lvls) >= 1 else 50
        tp2_pct_lbl = int(round(float(tp_lvls[-1].get("close_pct", 0.5)) * 100)) if len(tp_lvls) >= 2 else (100 - tp1_pct_lbl)
        plan_title = "📌 <b>KẾ HOẠCH LỆNH BREAKOUT TIẾP DIỄN</b>" if is_breakout else "📌 <b>KẾ HOẠCH LỆNH TRADERSZ (RANGE-TO-RANGE)</b>"
        sl_label = "SL Biên" if is_breakout else "SL Râu"
        tp1_label = f"TP1 ({tp1_pct_lbl}% · Mở rộng 0.5x)" if is_breakout else f"TP1 ({tp1_pct_lbl}% @ EQ)"
        tp2_label = f"TP2 ({tp2_pct_lbl}% · Mở rộng 1.0x)" if is_breakout else f"TP2 ({tp2_pct_lbl}% @ Biên)"

        lines.extend([
            "",
            plan_title,
            f"  ├ Entry     : <code>${plan['entry']}</code>",
            f"  ├ 🛑 {sl_label} : <code>${plan['sl']}</code> (-{data.get('sl_pct','1.5')}%)",
            f"  ├ 🎯 {tp1_label}: <code>${plan.get('tp1', 0)}</code> (+Dời SL Hòa Vốn)",
            f"  ├ 🏆 {tp2_label}: <code>${plan.get('tp2', 0)}</code>",
            f"  ├ ⚡ Đòn bẩy: <b>{data.get('dynamic_leverage', plan.get('recommended_leverage', 10))}x</b> ({data.get('leverage_note', 'Linh động 10x-20x | Vàng/Bạc 50x')})",
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

