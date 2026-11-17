# ═══════════════════════════════════════════════════════════════════════════════
# PURE TRADERSZ PRICE ACTION, LIQUIDITY & DEVIATION (SFP) ENGINE
# ═══════════════════════════════════════════════════════════════════════════════
# Triết lý giao dịch thuần Naked Chart của TraderSZ:
# 1. Context (Dealing Range & HTF Opens):
#    - Macro Opens: Year Open (YO), Monthly Open (MO), Weekly Open (WO), Daily Open (DO)
#    - Key Ranges: Previous Day High/Low (PDH/PDL), Monday High/Low (Monday Range),
#                  Previous Week High/Low (PWH/PWL), Asian Range (00:00-08:00 UTC)
#    - Range High (RH), Range Low (RL), 50% Equilibrium (EQ = (RH + RL) / 2)
# 2. No-Trade Zone vs Edge Zones:
#    - Đứng ngoài (WAIT) khi giá lơ lửng ở vùng giữa quanh 50% EQ.
#    - Vùng Premium (> 50% EQ, sát RH): CHỈ canh SHORT khi có Deviation/SFP.
#    - Vùng Discount (< 50% EQ, sát RL): CHỈ canh LONG khi có Deviation/SFP.
# 3. Quy luật Deviation / SFP (Swing Failure Pattern) & "Return to Range":
#    - Không bao giờ FOMO đánh Breakout khi giá vượt RH hoặc thủng RL.
#    - Chờ giá quét thanh khoản ra ngoài biên Range (Deviation/SFP), sau đó nến
#      (15m, 1H, 4H hoặc D1) rút râu / đóng cửa quay trở lại BÊN TRONG Range.
# 4. Entry, Stop Loss & Take Profit (Range-to-Range):
#    - Entry: Ngay khi nến xác nhận Return to Range (hoặc retest biên vừa quét).
#    - SL: Đặt ngay phía trên đỉnh râu Deviation (SHORT) hoặc dưới đáy râu Deviation (LONG).
#    - TP1: Luôn là trục giữa Range — 50% Equilibrium (EQ) (chốt 50%, dời SL về hòa vốn).
#    - TP2: Biên đối diện của Range (Range Low nếu Short / Range High nếu Long).
# ═══════════════════════════════════════════════════════════════════════════════

from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple


class TradersszFramework:
    """Pure TraderSZ Price Action Engine (Naked Chart — Zero Lagging Indicators)."""

    @staticmethod
    def detect_session_context(utcnow: Optional[datetime] = None) -> Dict[str, Any]:
        """Xác định phiên giao dịch UTC hiện tại (Asia / London / New York / Dead Zone)."""
        if utcnow is None:
            utcnow = datetime.now(timezone.utc)
        hour_utc = utcnow.hour + utcnow.minute / 60.0

        if 0.0 <= hour_utc < 8.0:
            return {
                "session": "ASIA",
                "is_killzone": False,
                "allow_trades": True,
                "preferred_mode": "ASIA_RANGE_BUILDING",
                "desc": "Phiên Á (00:00–08:00 UTC) — Hình thành biên Range Á, canh SFP tại PDH/PDL & Monday Range",
            }
        elif 8.0 <= hour_utc < 13.0:
            return {
                "session": "LONDON_OPEN",
                "is_killzone": True,
                "allow_trades": True,
                "preferred_mode": "LONDON_DEVIATION_SFP",
                "desc": "London Killzone (08:00–13:00 UTC) — Thời điểm vàng quét biên Range / Asia High-Low & Return to Range",
            }
        elif 13.0 <= hour_utc < 21.0:
            return {
                "session": "NEW_YORK_OPEN",
                "is_killzone": True,
                "allow_trades": True,
                "preferred_mode": "NY_RANGE_TO_RANGE",
                "desc": "New York Killzone (13:00–21:00 UTC) — Thanh khoản cao nhất, đẩy giá từ biên này sang biên kia Range",
            }
        else:
            return {
                "session": "DEAD_ZONE",
                "is_killzone": False,
                "allow_trades": True,  # Crypto giao dịch 24/7, chỉ không cộng bonus Killzone
                "preferred_mode": "LOW_LIQUIDITY_FILTER",
                "desc": "Late NY / Rollover (21:00–24:00 UTC) — Thanh khoản mỏng, chỉ chọn SFP khung lớn R:R cao",
            }

    @staticmethod
    def _ts_to_utc(ts_ms: int) -> datetime:
        if ts_ms > 1e11:
            return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        return datetime.fromtimestamp(float(ts_ms), tz=timezone.utc)

    @staticmethod
    def calculate_htf_opens_and_ranges(
        d1_klines: Optional[Dict[str, List[float]]],
        h1_klines: Optional[Dict[str, List[float]]] = None,
        m15_klines: Optional[Dict[str, List[float]]] = None,
        current_price: float = 0.0,
        utcnow: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Tính chính xác theo lịch UTC:
        - Year Open (YO), Monthly Open (MO), Weekly Open (WO), Daily Open (DO)
        - Previous Day High/Low (PDH/PDL) & 50% EQ
        - Monday Range (Monday High / Monday Low & 50% EQ)
        - Previous Week High/Low (PWH/PWL & 50% EQ)
        - Previous Month High/Low (PMH/PML & 50% EQ)
        - Asian Session High/Low (00:00–08:00 UTC hôm nay)
        """
        if utcnow is None:
            utcnow = datetime.now(timezone.utc)

        if not d1_klines or len(d1_klines.get("close", [])) < 3:
            p = current_price or 100.0
            rh, rl = p * 1.02, p * 0.98
            eq = (rh + rl) / 2.0
            return {
                "daily_open": p, "weekly_open": p, "monthly_open": p, "year_open": p,
                "pdh": p * 1.015, "pdl": p * 0.985, "pd_eq": p,
                "monday_high": p * 1.015, "monday_low": p * 0.985, "monday_eq": p, "monday_ready": False,
                "pwh": p * 1.03, "pwl": p * 0.97, "pw_eq": p,
                "pmh": rh, "pml": rl, "pm_eq": eq,
                "asia_high": 0.0, "asia_low": 0.0, "asia_eq": 0.0,
                "range_name": "FALLBACK_RANGE",
                "range_high": rh, "range_low": rl, "eq_50": eq,
                "range_width_pct": 4.0, "range_pos_pct": 50.0,
                "market_zone": "NO_TRADE_EQ_ZONE", "dist_to_eq_pct": 0.0,
                "all_ranges": [],
            }

        d_opens  = d1_klines.get("open", [])
        d_highs  = d1_klines.get("high", [])
        d_lows   = d1_klines.get("low", [])
        d_closes = d1_klines.get("close", [])
        d_ts     = d1_klines.get("timestamp", [])
        n = len(d_closes)

        if current_price <= 0:
            current_price = d_closes[-1]

        has_ts = bool(d_ts and len(d_ts) == n and d_ts[-1] > 0)

        # Mặc định dùng nến cuối nếu thiếu timestamp
        daily_open = d_opens[-1]
        pdh = d_highs[-2] if n >= 2 else d_highs[-1]
        pdl = d_lows[-2] if n >= 2 else d_lows[-1]

        # Xác định theo lịch UTC chuẩn nếu có timestamps
        weekly_open = d_opens[-min(7, n)]
        monthly_open = d_opens[-min(30, n)]
        year_open = d_opens[0]

        monday_high = 0.0
        monday_low = 0.0
        monday_ready = False

        pwh = max(d_highs[-min(8, n):-1]) if n >= 2 else d_highs[-1]
        pwl = min(d_lows[-min(8, n):-1]) if n >= 2 else d_lows[-1]
        pmh = max(d_highs[-min(31, n):-1]) if n >= 2 else d_highs[-1]
        pml = min(d_lows[-min(31, n):-1]) if n >= 2 else d_lows[-1]

        if has_ts:
            today_date = utcnow.date()
            cur_year = today_date.year
            cur_month = (today_date.year, today_date.month)
            cur_iso_year, cur_iso_week, _ = today_date.isocalendar()

            dates_utc = [TradersszFramework._ts_to_utc(t).date() for t in d_ts]

            # Daily Open & PDH/PDL
            today_indices = [i for i, d in enumerate(dates_utc) if d == today_date]
            if today_indices:
                t_idx = today_indices[0]
                daily_open = d_opens[t_idx]
                if t_idx > 0:
                    pdh = d_highs[t_idx - 1]
                    pdl = d_lows[t_idx - 1]
            else:
                daily_open = d_opens[-1]
                pdh = d_highs[-2] if n >= 2 else d_highs[-1]
                pdl = d_lows[-2] if n >= 2 else d_lows[-1]

            # Year Open: nến đầu tiên của năm UTC hiện tại
            year_indices = [i for i, d in enumerate(dates_utc) if d.year == cur_year]
            if year_indices:
                year_open = d_opens[year_indices[0]]

            # Monthly Open & Previous Month High/Low
            month_indices = [i for i, d in enumerate(dates_utc) if (d.year, d.month) == cur_month]
            if month_indices:
                monthly_open = d_opens[month_indices[0]]
                first_m = month_indices[0]
                prev_m_indices = [
                    i for i in range(first_m)
                    if dates_utc[i].month == (12 if cur_month[1] == 1 else cur_month[1] - 1)
                ]
                if prev_m_indices:
                    pmh = max(d_highs[i] for i in prev_m_indices)
                    pml = min(d_lows[i] for i in prev_m_indices)

            # Weekly Open, Previous Week High/Low, Monday Range
            week_indices = [
                i for i, d in enumerate(dates_utc)
                if d.isocalendar()[:2] == (cur_iso_year, cur_iso_week)
            ]
            if week_indices:
                weekly_open = d_opens[week_indices[0]]
                # Monday của tuần hiện tại (weekday == 0)
                mon_this_week = [i for i in week_indices if dates_utc[i].weekday() == 0]
                if mon_this_week:
                    m_idx = mon_this_week[0]
                    # Nếu hôm nay KHÔNG phải thứ 2 (tức thứ 2 đã đóng cửa xong) -> Monday Range chính thức
                    if today_date.weekday() != 0 or utcnow.hour >= 20:
                        monday_high = d_highs[m_idx]
                        monday_low = d_lows[m_idx]
                        monday_ready = True

                # Tuần trước
                first_w = week_indices[0]
                if first_w > 0:
                    prev_w_cal = dates_utc[first_w - 1].isocalendar()[:2]
                    prev_w_indices = [i for i in range(first_w) if dates_utc[i].isocalendar()[:2] == prev_w_cal]
                    if prev_w_indices:
                        pwh = max(d_highs[i] for i in prev_w_indices)
                        pwl = min(d_lows[i] for i in prev_w_indices)
                        # Nếu Monday tuần này chưa đóng, dùng Monday tuần trước làm tham chiếu phụ
                        if not monday_ready:
                            mon_prev = [i for i in prev_w_indices if dates_utc[i].weekday() == 0]
                            if mon_prev:
                                monday_high = d_highs[mon_prev[0]]
                                monday_low = d_lows[mon_prev[0]]
                                monday_ready = True

        # Fallback Monday Range nếu thị trường chứng khoán/tuần không có ngày thứ 2 (nghỉ lễ)
        if not monday_ready:
            monday_high = pdh
            monday_low = pdl

        # Asian Session High/Low (00:00 - 08:00 UTC hôm nay) từ 15m hoặc 1h
        asia_high = 0.0
        asia_low = 0.0
        asia_ready = False
        intraday = m15_klines if (m15_klines and m15_klines.get("timestamp")) else h1_klines
        if intraday and intraday.get("timestamp") and len(intraday["timestamp"]) == len(intraday.get("close", [])):
            today_d = utcnow.date()
            a_highs, a_lows = [], []
            for idx, ts in enumerate(intraday["timestamp"]):
                dt = TradersszFramework._ts_to_utc(ts)
                if dt.date() == today_d and 0 <= dt.hour < 8:
                    a_highs.append(intraday["high"][idx])
                    a_lows.append(intraday["low"][idx])
            if a_highs and a_lows and utcnow.hour >= 8:
                asia_high = max(a_highs)
                asia_low = min(a_lows)
                asia_ready = True

        # Xây dựng danh sách tất cả các Dealing Ranges hợp lệ
        all_ranges: List[Dict[str, Any]] = []

        def _add_range(name: str, label: str, rh: float, rl: float, weight: int):
            if rh > rl > 0:
                width_pct = (rh - rl) / rl * 100.0
                if width_pct >= 0.35:
                    all_ranges.append({
                        "name": name,
                        "label": label,
                        "range_high": round(rh, 4),
                        "range_low": round(rl, 4),
                        "eq_50": round((rh + rl) / 2.0, 4),
                        "width_pct": round(width_pct, 2),
                        "weight": weight,
                    })

        # Ưu tiên TraderSZ: Previous Day Range (PDH/PDL), Monday Range (MonH/MonL), Previous Week Range (PWH/PWL), Asian Range
        _add_range("DAILY_RANGE", "Range Ngày Hôm Qua (PDH/PDL)", pdh, pdl, 3)
        if monday_ready and (monday_high != pdh or monday_low != pdl):
            _add_range("MONDAY_RANGE", "Monday Range (Đỉnh/Đáy Thứ Hai)", monday_high, monday_low, 4)
        _add_range("WEEKLY_RANGE", "Range Tuần Trước (PWH/PWL)", pwh, pwl, 3)
        if asia_ready:
            _add_range("ASIA_RANGE", "Range Phiên Á (00–08h UTC)", asia_high, asia_low, 2)
        if pmh > pml > 0 and (pmh != pwh or pml != pwl):
            _add_range("MONTHLY_RANGE", "Range Tháng Trước (PMH/PML)", pmh, pml, 2)

        # Chọn Dealing Range chính làm bối cảnh tổng quan:
        # Ưu tiên Monday Range nếu giá đang vận động trong hoặc quanh biên của nó, tiếp theo là PDH/PDL, rồi Weekly Range.
        primary = None
        for cand_name in ("DAILY_RANGE", "MONDAY_RANGE", "WEEKLY_RANGE"):
            for r in all_ranges:
                if r["name"] == cand_name:
                    # Chọn Range mà giá đang ở gần hoặc bên trong (lệch tối đa 3% khỏi biên)
                    if r["range_low"] * 0.97 <= current_price <= r["range_high"] * 1.03:
                        primary = r
                        break
            if primary:
                break
        if primary is None:
            primary = all_ranges[0] if all_ranges else {
                "name": "DAILY_RANGE",
                "label": "Range Ngày (PDH/PDL)",
                "range_high": round(pdh, 4),
                "range_low": round(pdl, 4),
                "eq_50": round((pdh + pdl) / 2.0, 4),
                "width_pct": round((pdh - pdl) / max(pdl, 1e-6) * 100, 2),
                "weight": 3,
            }

        rh = primary["range_high"]
        rl = primary["range_low"]
        eq_50 = primary["eq_50"]
        span = max(rh - rl, 1e-6)
        range_pos_pct = (current_price - rl) / span * 100.0
        dist_to_eq_pct = (current_price - eq_50) / eq_50 * 100.0 if eq_50 > 0 else 0.0

        # Phân vùng TraderSZ:
        # - Vùng giữa (35% - 65% quanh 50% EQ): No-Trade Zone
        # - Vùng Premium (>= 65%): Canh SHORT khi quét RH
        # - Vùng Discount (<= 35%): Canh LONG khi quét RL
        if range_pos_pct >= 62.0:
            market_zone = "PREMIUM"
        elif range_pos_pct <= 38.0:
            market_zone = "DISCOUNT"
        else:
            market_zone = "NO_TRADE_EQ_ZONE"

        return {
            "daily_open": round(daily_open, 4),
            "weekly_open": round(weekly_open, 4),
            "monthly_open": round(monthly_open, 4),
            "year_open": round(year_open, 4),
            "pdh": round(pdh, 4),
            "pdl": round(pdl, 4),
            "pd_eq": round((pdh + pdl) / 2.0, 4),
            "monday_high": round(monday_high, 4),
            "monday_low": round(monday_low, 4),
            "monday_eq": round((monday_high + monday_low) / 2.0, 4),
            "monday_ready": monday_ready,
            "pwh": round(pwh, 4),
            "pwl": round(pwl, 4),
            "pw_eq": round((pwh + pwl) / 2.0, 4),
            "pmh": round(pmh, 4),
            "pml": round(pml, 4),
            "pm_eq": round((pmh + pml) / 2.0, 4),
            "asia_high": round(asia_high, 4),
            "asia_low": round(asia_low, 4),
            "asia_eq": round((asia_high + asia_low) / 2.0, 4) if asia_ready else 0.0,
            "asia_ready": asia_ready,
            "range_name": primary["name"],
            "range_label": primary["label"],
            "range_high": rh,
            "range_low": rl,
            "eq_50": eq_50,
            "range_width_pct": primary["width_pct"],
            "range_pos_pct": round(range_pos_pct, 1),
            "market_zone": market_zone,
            "dist_to_eq_pct": round(dist_to_eq_pct, 2),
            "all_ranges": all_ranges,
        }

    @staticmethod
    def _scan_tf_deviation(
        tf_name: str,
        klines: Optional[Dict[str, List[float]]],
        range_obj: Dict[str, Any],
        current_price: float,
        sl_buffer_pct: float = 0.0020,
        min_rr: float = 1.5,
    ) -> Optional[Dict[str, Any]]:
        """
        Kiểm tra Deviation / SFP (Swing Failure Pattern) & Quy luật 'Return to Range'
        trên một khung thời gian (1d, 4h, 1h, 15m) đối với một Range cụ thể (RH, RL, EQ).

        Hỗ trợ cả 2 biến thể chuẩn TraderSZ:
        1) Single-candle SFP: Cây nến mới đóng (hoặc nến hiện tại nếu đã xác nhận râu)
           đẩy râu vượt RH rồi đóng ngược xuống dưới RH (hoặc thủng RL rồi đóng ngược lên trên RL).
        2) Multi-candle Deviation & Return to Range: 1–4 cây nến trước đóng cửa bên ngoài
           RH/RL (bẫy đám đông FOMO breakout), sau đó nến xác nhận ĐÓNG CỬA quay lại BÊN TRONG Range.
        """
        if not klines or len(klines.get("close", [])) < 8:
            return None

        opens  = klines["open"]
        highs  = klines["high"]
        lows   = klines["low"]
        closes = klines["close"]
        vols   = klines.get("volume", [1.0] * len(closes))

        rh = float(range_obj["range_high"])
        rl = float(range_obj["range_low"])
        eq = float(range_obj["eq_50"])
        r_name = range_obj["name"]
        r_label = range_obj["label"]
        r_weight = int(range_obj.get("weight", 2))

        if rh <= rl or current_price <= 0:
            return None

        # Trọng số theo khung thời gian (TraderSZ ưu tiên H1, H4, D1; M15 dùng để bóp SL sắc nét)
        tf_weight = {"1d": 4, "4h": 4, "1h": 3, "15m": 2}.get(tf_name, 1)

        # Kiểm tra 2 nến gần nhất: nến cuối [-1] (đang chạy/vừa đóng) và nến vừa đóng [-2]
        # (nếu nến [-2] đã Return to Range và nến [-1] đang retest sát biên RH/RL thì càng chuẩn!)
        avg_vol = sum(vols[-20:-1]) / max(1, len(vols[-20:-1])) if len(vols) >= 6 else 1.0

        candidates: List[Dict[str, Any]] = []

        # ── A. KIỂM TRA BEARISH DEVIATION / SFP TẠI RANGE HIGH -> SHORT ──
        # Điều kiện vào lệnh: Giá hiện tại phải nằm BÊN DƯỚI Range High (đã Return to Range)
        # và vẫn còn nằm ở nửa trên (gần RH hơn là sát EQ) để đảm bảo biên lợi nhuận về EQ & RL.
        if current_price < rh and current_price > eq:
            # 1) Nến [-1] hoặc [-2] vừa quét râu lên trên RH rồi đóng lại dưới RH
            for idx in (-1, -2):
                c_o, c_h, c_l, c_c = opens[idx], highs[idx], lows[idx], closes[idx]
                c_span = max(c_h - c_l, 1e-9)
                upper_wick = c_h - max(c_o, c_c)
                wick_ratio = upper_wick / c_span

                # Single-candle SFP: High vượt RH ít nhất 0.05% (quét thanh khoản), Close nằm dưới RH
                is_single_sfp = (c_h > rh * 1.0005) and (c_c < rh) and (wick_ratio >= 0.25 or c_c < c_o)

                # Multi-candle Deviation: 1-4 nến trước idx từng đóng trên RH, và nến idx đóng ngược vào trong (< RH)
                prev_window = closes[max(0, len(closes) + idx - 4): len(closes) + idx]
                prev_highs  = highs[max(0, len(highs) + idx - 4): len(highs) + idx]
                was_outside = any(pc > rh for pc in prev_window)
                is_return_to_range = was_outside and (c_c < rh)

                if is_single_sfp or is_return_to_range:
                    # Đảm bảo giá hiện tại vẫn giữ bên dưới RH (không bị bật lại vượt RH)
                    if idx == -2 and current_price >= rh:
                        continue
                    # Đỉnh râu Deviation cao nhất trong cụm quét 4 nến gần nhất
                    sweep_extreme = max(highs[max(0, len(highs) - 5):])
                    sl_price = round(sweep_extreme * (1.0 + sl_buffer_pct), 4)
                    # Tối thiểu SL 0.35% để không bị trượt giá sàn
                    if (sl_price - current_price) / current_price < 0.0035:
                        sl_price = round(current_price * 1.0035, 4)

                    risk = sl_price - current_price
                    tp1 = round(eq, 4)
                    tp2 = round(rl, 4)
                    reward_tp1 = current_price - tp1
                    reward_tp2 = current_price - tp2

                    if risk <= 0 or reward_tp1 <= 0 or reward_tp2 <= 0:
                        continue

                    sl_pct = risk / current_price
                    if sl_pct > 0.045:  # Râu quá rộng (>4.5%), bỏ qua để kiếm kèo khung nhỏ hơn
                        continue

                    rr_tp1 = reward_tp1 / risk
                    rr_tp2 = reward_tp2 / risk
                    if rr_tp2 < min_rr or rr_tp1 < 0.6:
                        continue

                    is_retest = abs(current_price - rh) / rh <= 0.0040
                    setup_type = "DEVIATION_RETURN_TO_RANGE" if is_return_to_range else "SFP_WICK_REJECTION"
                    vol_ratio = vols[idx] / avg_vol if avg_vol > 0 else 1.0

                    candidates.append({
                        "detected": True,
                        "direction": "SHORT",
                        "tf": tf_name,
                        "range_name": r_name,
                        "range_label": r_label,
                        "setup_type": setup_type,
                        "is_retest": is_retest,
                        "swept_level": round(rh, 4),
                        "sweep_extreme": round(sweep_extreme, 4),
                        "entry": round(current_price, 4),
                        "sl": sl_price,
                        "sl_pct": round(sl_pct * 100, 2),
                        "tp1": tp1,
                        "tp2": tp2,
                        "rr_tp1": round(rr_tp1, 2),
                        "rr_tp2": round(rr_tp2, 2),
                        "vol_ratio": round(vol_ratio, 2),
                        "score": tf_weight * 10 + r_weight * 5 + (6 if is_retest else 0) + (4 if rr_tp2 >= 2.5 else 0),
                        "reason": (
                            f"TraderSZ {tf_name.upper()} Bearish {setup_type}: "
                            f"Quét thanh khoản trên {r_label} (${rh:.4f}, đỉnh râu ${sweep_extreme:.4f}) "
                            f"và đóng nến quay trở lại vào trong Range -> SHORT về EQ 50% (${tp1:.4f}) & Range Low (${tp2:.4f})."
                        ),
                    })
                    break

        # ── B. KIỂM TRA BULLISH DEVIATION / SFP TẠI RANGE LOW -> LONG ──
        if current_price > rl and current_price < eq:
            for idx in (-1, -2):
                c_o, c_h, c_l, c_c = opens[idx], highs[idx], lows[idx], closes[idx]
                c_span = max(c_h - c_l, 1e-9)
                lower_wick = min(c_o, c_c) - c_l
                wick_ratio = lower_wick / c_span

                is_single_sfp = (c_l < rl * 0.9995) and (c_c > rl) and (wick_ratio >= 0.25 or c_c > c_o)

                prev_window = closes[max(0, len(closes) + idx - 4): len(closes) + idx]
                was_outside = any(pc < rl for pc in prev_window)
                is_return_to_range = was_outside and (c_c > rl)

                if is_single_sfp or is_return_to_range:
                    if idx == -2 and current_price <= rl:
                        continue
                    sweep_extreme = min(lows[max(0, len(lows) - 5):])
                    sl_price = round(sweep_extreme * (1.0 - sl_buffer_pct), 4)
                    if (current_price - sl_price) / current_price < 0.0035:
                        sl_price = round(current_price * 0.9965, 4)

                    risk = current_price - sl_price
                    tp1 = round(eq, 4)
                    tp2 = round(rh, 4)
                    reward_tp1 = tp1 - current_price
                    reward_tp2 = tp2 - current_price

                    if risk <= 0 or reward_tp1 <= 0 or reward_tp2 <= 0:
                        continue

                    sl_pct = risk / current_price
                    if sl_pct > 0.045:
                        continue

                    rr_tp1 = reward_tp1 / risk
                    rr_tp2 = reward_tp2 / risk
                    if rr_tp2 < min_rr or rr_tp1 < 0.6:
                        continue

                    is_retest = abs(current_price - rl) / rl <= 0.0040
                    setup_type = "DEVIATION_RETURN_TO_RANGE" if is_return_to_range else "SFP_WICK_REJECTION"
                    vol_ratio = vols[idx] / avg_vol if avg_vol > 0 else 1.0

                    candidates.append({
                        "detected": True,
                        "direction": "LONG",
                        "tf": tf_name,
                        "range_name": r_name,
                        "range_label": r_label,
                        "setup_type": setup_type,
                        "is_retest": is_retest,
                        "swept_level": round(rl, 4),
                        "sweep_extreme": round(sweep_extreme, 4),
                        "entry": round(current_price, 4),
                        "sl": sl_price,
                        "sl_pct": round(sl_pct * 100, 2),
                        "tp1": tp1,
                        "tp2": tp2,
                        "rr_tp1": round(rr_tp1, 2),
                        "rr_tp2": round(rr_tp2, 2),
                        "vol_ratio": round(vol_ratio, 2),
                        "score": tf_weight * 10 + r_weight * 5 + (6 if is_retest else 0) + (4 if rr_tp2 >= 2.5 else 0),
                        "reason": (
                            f"TraderSZ {tf_name.upper()} Bullish {setup_type}: "
                            f"Quét thanh khoản dưới {r_label} (${rl:.4f}, đáy râu ${sweep_extreme:.4f}) "
                            f"và đóng nến quay trở lại vào trong Range -> LONG về EQ 50% (${tp1:.4f}) & Range High (${tp2:.4f})."
                        ),
                    })
                    break

        if not candidates:
            return None
        return max(candidates, key=lambda x: x["score"])

    @staticmethod
    def evaluate_htf_bias(
        htf_data: Dict[str, Any],
        d1_klines: Optional[Dict[str, List[float]]],
        h4_klines: Optional[Dict[str, List[float]]],
        current_price: float,
    ) -> Dict[str, Any]:
        """
        Thiên hướng khung lớn (HTF Bias) thuần Price Action của TraderSZ:
        - So sánh vị thế giá với Year Open, Monthly Open, Weekly Open, Daily Open.
        - Đánh giá cấu trúc đỉnh/đáy thuần túy trên 4H & D1 (không dùng chỉ báo).
        """
        yo = htf_data.get("year_open", current_price)
        mo = htf_data.get("monthly_open", current_price)
        wo = htf_data.get("weekly_open", current_price)
        do = htf_data.get("daily_open", current_price)

        above_yo = current_price >= yo
        above_mo = current_price >= mo
        above_wo = current_price >= wo
        above_do = current_price >= do

        bull_opens = sum([above_yo, above_mo, above_wo, above_do])

        # Kiểm tra cấu trúc Swing Price Action thuần trên 4H (so đỉnh đáy 20 nến gần nhất)
        h4_bias = "NEUTRAL"
        if h4_klines and len(h4_klines.get("close", [])) >= 16:
            h4_h = h4_klines["high"]
            h4_l = h4_klines["low"]
            prev_h, curr_h = max(h4_h[-16:-8]), max(h4_h[-8:])
            prev_l, curr_l = min(h4_l[-16:-8]), min(h4_l[-8:])
            if curr_h > prev_h and curr_l > prev_l:
                h4_bias = "BULLISH"
            elif curr_h < prev_h and curr_l < prev_l:
                h4_bias = "BEARISH"
            elif curr_h > prev_h and curr_l < prev_l:
                h4_bias = "RANGE_EXPANSION"
            else:
                h4_bias = "RANGE_CONSOLIDATION"

        if bull_opens >= 3 and h4_bias != "BEARISH":
            overall_bias = "BULLISH"
        elif bull_opens <= 1 and h4_bias != "BULLISH":
            overall_bias = "BEARISH"
        else:
            overall_bias = "RANGE_NEUTRAL"

        return {
            "bias": overall_bias,
            "h4_structure": h4_bias,
            "above_yo": above_yo,
            "above_mo": above_mo,
            "above_wo": above_wo,
            "above_do": above_do,
            "bull_opens_count": bull_opens,
            "desc": (
                f"HTF Bias: {overall_bias} (4H: {h4_bias} | "
                f"Opens: YO {'▲' if above_yo else '▼'} · MO {'▲' if above_mo else '▼'} · "
                f"WO {'▲' if above_wo else '▼'} · DO {'▲' if above_do else '▼'})"
            ),
        }

    @staticmethod
    def evaluate_btc_bias(
        btc_klines_by_tf: Dict[str, Optional[Dict[str, List[float]]]],
        btc_price: float,
        btc_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Xác định thiên hướng dẫn dắt của BTC (Market Leader Bias) thuần Price Action TraderSZ:
        - Không dùng chỉ báo trễ (EMA/RSI/MACD).
        - Ưu tiên 1: BTC đang có tín hiệu SFP / Deviation Return-to-Range (LONG -> BULLISH, SHORT -> BEARISH).
        - Ưu tiên 2: HTF Bias (vị thế so với YO/MO/WO/DO + cấu trúc đỉnh/đáy 4H).
        - Ưu tiên 3: Vị thế nến Intraday so với Daily Open + Weekly Open + cấu trúc 4H.
        """
        if btc_result is None:
            btc_result = TradersszFramework.evaluate_traderssz_complete(
                klines_by_tf=btc_klines_by_tf,
                current_price=btc_price,
            )

        btc_signal = btc_result.get("signal", "WAIT")
        btc_setup = btc_result.get("setup", {}) or {}
        htf_bias = btc_result.get("htf_bias", {}) or {}
        htf_data = btc_result.get("htf_opens_and_ranges", {}) or {}

        h4_struct = htf_bias.get("h4_structure", "NEUTRAL")
        htf_dir = htf_bias.get("bias", "RANGE_NEUTRAL")
        above_do = bool(htf_bias.get("above_do", False))
        above_wo = bool(htf_bias.get("above_wo", False))
        bull_opens = int(htf_bias.get("bull_opens_count", 2))

        do = float(htf_data.get("daily_open", btc_price) or btc_price)
        do_dist_pct = ((btc_price - do) / do * 100.0) if do > 0 else 0.0

        # Kiểm tra thêm lực nến 1H thuần PA của BTC để bắt sớm nhịp pump/dump từ Open
        h1_kl = (btc_klines_by_tf or {}).get("1h")
        h1_push = "FLAT"
        if h1_kl and len(h1_kl.get("close", [])) >= 2:
            h1_o = float(h1_kl["open"][-1])
            h1_c = float(h1_kl["close"][-1])
            if h1_c > h1_o and btc_price > do:
                h1_push = "UP"
            elif h1_c < h1_o and btc_price < do:
                h1_push = "DOWN"

        if btc_signal == "LONG":
            bias = "BULLISH"
            strength = "STRONG"
            tf_lbl = str(btc_setup.get("tf", "HTF")).upper()
            rng_lbl = btc_setup.get("range_label", "Range")
            reason = f"BTC đang kích hoạt LONG SFP ({tf_lbl} · {rng_lbl})"
        elif btc_signal == "SHORT":
            bias = "BEARISH"
            strength = "STRONG"
            tf_lbl = str(btc_setup.get("tf", "HTF")).upper()
            rng_lbl = btc_setup.get("range_label", "Range")
            reason = f"BTC đang kích hoạt SHORT SFP ({tf_lbl} · {rng_lbl})"
        elif htf_dir == "BULLISH":
            bias = "BULLISH"
            strength = "MODERATE"
            reason = f"BTC HTF Bias BULLISH (trên {bull_opens}/4 Opens, 4H: {h4_struct})"
        elif htf_dir == "BEARISH":
            bias = "BEARISH"
            strength = "MODERATE"
            reason = f"BTC HTF Bias BEARISH (trên {bull_opens}/4 Opens, 4H: {h4_struct})"
        elif above_do and above_wo and h4_struct != "BEARISH":
            bias = "BULLISH"
            strength = "INTRADAY"
            reason = "BTC giữ giá trên cả Daily Open & Weekly Open"
        elif (not above_do) and (not above_wo) and h4_struct != "BULLISH":
            bias = "BEARISH"
            strength = "INTRADAY"
            reason = "BTC nằm dưới cả Daily Open & Weekly Open"
        elif h4_struct == "BULLISH" and above_do and do_dist_pct >= 0.25 and h1_push == "UP":
            bias = "BULLISH"
            strength = "INTRADAY"
            reason = f"BTC cấu trúc 4H tăng + trên Daily Open (+{do_dist_pct:.2f}%)"
        elif h4_struct == "BEARISH" and (not above_do) and do_dist_pct <= -0.25 and h1_push == "DOWN":
            bias = "BEARISH"
            strength = "INTRADAY"
            reason = f"BTC cấu trúc 4H giảm + dưới Daily Open ({do_dist_pct:.2f}%)"
        else:
            bias = "NEUTRAL"
            strength = "NEUTRAL"
            reason = "BTC đi ngang cân bằng quanh Open/EQ — cho phép Altcoin đánh biên 2 đầu"

        return {
            "bias": bias,
            "strength": strength,
            "btc_signal": btc_signal,
            "btc_price": round(float(btc_price), 2),
            "h4_structure": h4_struct,
            "htf_bias": htf_dir,
            "above_do": above_do,
            "above_wo": above_wo,
            "bull_opens_count": bull_opens,
            "reason": reason,
        }

    @staticmethod
    def evaluate_traderssz_complete(
        klines_by_tf: Dict[str, Optional[Dict[str, List[float]]]],
        current_price: float,
        sl_buffer_pct: float = 0.0020,
        min_rr: float = 1.5,
        btc_bias: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Tổng hợp toàn diện hệ thống giao dịch thuần Price Action TraderSZ:
        1. Session Context (Asia / London / NY)
        2. HTF Opens (YO, MO, WO, DO) & Dealing Ranges (PDH/PDL, Monday Range, PWH/PWL, Asia)
        3. 50% Equilibrium & No-Trade Zone Check
        4. Multi-TF Deviation / SFP & 'Return to Range' Detection (1D, 4H, 1H, 15M)
        5. BTC Market-Leader Directional Alignment (cho Altcoin Crypto không đánh ngược sóng BTC)
        6. Precision Wick SL + TP1 (50% EQ) + TP2 (Opposite Range Boundary)
        """
        d1_klines  = klines_by_tf.get("1d")
        h4_klines  = klines_by_tf.get("4h")
        h1_klines  = klines_by_tf.get("1h")
        m15_klines = klines_by_tf.get("15m")

        session_ctx = TradersszFramework.detect_session_context()
        htf_data = TradersszFramework.calculate_htf_opens_and_ranges(
            d1_klines=d1_klines,
            h1_klines=h1_klines,
            m15_klines=m15_klines,
            current_price=current_price,
        )
        htf_bias = TradersszFramework.evaluate_htf_bias(
            htf_data=htf_data,
            d1_klines=d1_klines,
            h4_klines=h4_klines,
            current_price=current_price,
        )

        # Quét Deviation / SFP trên tất cả các Range & Khung thời gian (ưu tiên 4H/1H/1D, sau đó 15M)
        setups: List[Dict[str, Any]] = []
        scan_tfs = [("1d", d1_klines), ("4h", h4_klines), ("1h", h1_klines), ("15m", m15_klines)]
        for range_obj in htf_data.get("all_ranges", []):
            for tf_name, tf_klines in scan_tfs:
                hit = TradersszFramework._scan_tf_deviation(
                    tf_name=tf_name,
                    klines=tf_klines,
                    range_obj=range_obj,
                    current_price=current_price,
                    sl_buffer_pct=sl_buffer_pct,
                    min_rr=min_rr,
                )
                if hit:
                    setups.append(hit)

        # Bảo vệ chuẩn TraderSZ: không đánh SHORT khi Dealing Range chính đang ở DISCOUNT,
        # và không đánh LONG khi Dealing Range chính đang ở PREMIUM.
        primary_zone = str(htf_data.get("market_zone", "NO_TRADE_EQ_ZONE")).upper()
        if setups:
            if primary_zone == "DISCOUNT":
                setups = [s for s in setups if s["direction"] == "LONG"]
            elif primary_zone == "PREMIUM":
                setups = [s for s in setups if s["direction"] == "SHORT"]

        btc_dir_bias = str((btc_bias or {}).get("bias", "NEUTRAL")).upper()

        if setups:
            candidate_setups = setups
            if btc_dir_bias == "BULLISH":
                aligned_setups = [s for s in setups if s["direction"] == "LONG"]
                if aligned_setups:
                    candidate_setups = aligned_setups
                else:
                    blocked_best = max(setups, key=lambda s: (s["score"], s["rr_tp2"]))
                    eq = htf_data.get("eq_50", current_price)
                    rh = htf_data.get("range_high", current_price)
                    return {
                        "signal": "WAIT",
                        "confidence": 50.0,
                        "setup": {
                            "detected": False,
                            "setup_type": "BTC_ALIGNMENT_BLOCK",
                            "blocked_direction": "SHORT",
                            "blocked_tf": blocked_best.get("tf", ""),
                            "blocked_range": blocked_best.get("range_label", ""),
                        },
                        "all_setups": [],
                        "safe_sl": 0.0,
                        "sl_pct": 0.0,
                        "tp1_target": eq,
                        "tp2_target": rh,
                        "rr_ratio": 0.0,
                        "is_htf_aligned": False,
                        "btc_aligned": False,
                        "btc_bias": btc_bias,
                        "session": session_ctx,
                        "htf_opens_and_ranges": htf_data,
                        "htf_bias": htf_bias,
                        "reason": (
                            f"🛡️ Bỏ qua SHORT ({blocked_best.get('tf','').upper()} {blocked_best.get('range_label','')}) "
                            f"vì ngược chiều BTC: {(btc_bias or {}).get('reason', 'BTC đang BULLISH')}. "
                            f"Tuân thủ đồng pha BTC, chỉ canh LONG khi Altcoin quét đáy Range Low."
                        ),
                    }
            elif btc_dir_bias == "BEARISH":
                aligned_setups = [s for s in setups if s["direction"] == "SHORT"]
                if aligned_setups:
                    candidate_setups = aligned_setups
                else:
                    blocked_best = max(setups, key=lambda s: (s["score"], s["rr_tp2"]))
                    eq = htf_data.get("eq_50", current_price)
                    rl = htf_data.get("range_low", current_price)
                    return {
                        "signal": "WAIT",
                        "confidence": 50.0,
                        "setup": {
                            "detected": False,
                            "setup_type": "BTC_ALIGNMENT_BLOCK",
                            "blocked_direction": "LONG",
                            "blocked_tf": blocked_best.get("tf", ""),
                            "blocked_range": blocked_best.get("range_label", ""),
                        },
                        "all_setups": [],
                        "safe_sl": 0.0,
                        "sl_pct": 0.0,
                        "tp1_target": eq,
                        "tp2_target": rl,
                        "rr_ratio": 0.0,
                        "is_htf_aligned": False,
                        "btc_aligned": False,
                        "btc_bias": btc_bias,
                        "session": session_ctx,
                        "htf_opens_and_ranges": htf_data,
                        "htf_bias": htf_bias,
                        "reason": (
                            f"🛡️ Bỏ qua LONG ({blocked_best.get('tf','').upper()} {blocked_best.get('range_label','')}) "
                            f"vì ngược chiều BTC: {(btc_bias or {}).get('reason', 'BTC đang BEARISH')}. "
                            f"Tuân thủ đồng pha BTC, chỉ canh SHORT khi Altcoin quét đỉnh Range High."
                        ),
                    }

            # Chọn Setup tốt nhất (ưu tiên khung lớn hơn, Range uy tín hơn như Monday/PDH-PDL, và R:R cao)
            best = max(candidate_setups, key=lambda s: (s["score"], s["rr_tp2"]))
            direction = best["direction"]

            # Tính Confidence thuần TraderSZ (72% - 96%)
            conf = 74.0
            if best["tf"] in ("1d", "4h"):
                conf += 8.0
            elif best["tf"] == "1h":
                conf += 5.0

            if best["range_name"] in ("MONDAY_RANGE", "DAILY_RANGE", "WEEKLY_RANGE"):
                conf += 4.0

            if best.get("is_retest"):
                conf += 4.0

            if best.get("rr_tp2", 0) >= 2.5:
                conf += 4.0

            # Hợp lưu với Open (WO/MO/DO) hoặc HTF Bias
            wo = htf_data.get("weekly_open", current_price)
            open_aligned = (
                (direction == "LONG" and (current_price >= wo * 0.998 or htf_bias["bias"] == "BULLISH")) or
                (direction == "SHORT" and (current_price <= wo * 1.002 or htf_bias["bias"] == "BEARISH"))
            )
            if open_aligned:
                conf += 4.0

            btc_aligned = (
                (btc_dir_bias == "BULLISH" and direction == "LONG") or
                (btc_dir_bias == "BEARISH" and direction == "SHORT")
            )
            if btc_aligned:
                conf += 3.0

            if session_ctx.get("is_killzone"):
                conf += 3.0

            conf = round(min(96.0, max(70.0, conf)), 1)
            reason_txt = best["reason"]
            if btc_aligned:
                reason_txt = f"{reason_txt} [Đồng pha BTC {btc_dir_bias}]"

            return {
                "signal": direction,
                "confidence": conf,
                "setup": best,
                "all_setups": candidate_setups[:5],
                "safe_sl": best["sl"],
                "sl_pct": best["sl_pct"],
                "tp1_target": best["tp1"],
                "tp2_target": best["tp2"],
                "rr_ratio": best["rr_tp2"],
                "is_htf_aligned": open_aligned,
                "btc_aligned": btc_aligned,
                "btc_bias": btc_bias,
                "session": session_ctx,
                "htf_opens_and_ranges": htf_data,
                "htf_bias": htf_bias,
                "reason": reason_txt,
            }

        # Không có Deviation / SFP rõ ràng tại biên Range -> ĐỨNG NGOÀI (WAIT)
        zone = htf_data.get("market_zone", "NO_TRADE_EQ_ZONE")
        rh = htf_data.get("range_high", current_price)
        rl = htf_data.get("range_low", current_price)
        eq = htf_data.get("eq_50", current_price)
        if zone == "NO_TRADE_EQ_ZONE":
            wait_reason = (
                f"⛔ NO-TRADE ZONE (Giữa Range quanh 50% EQ ${eq:.4f}): "
                f"Giá đang lơ lửng tại {htf_data.get('range_pos_pct', 50)}% của {htf_data.get('range_label')}. "
                f"TraderSZ đứng ngoài chờ quét RH (${rh:.4f}) hoặc RL (${rl:.4f})."
            )
            wait_conf = 45.0
        elif zone == "PREMIUM":
            wait_reason = (
                f"⏳ Vùng PREMIUM ({htf_data.get('range_pos_pct', 70)}% Range): "
                f"Tuyệt đối không FOMO Long. Đang rình giá quét vượt Range High (${rh:.4f}) rồi đóng nến quay lại vào trong Range để SHORT."
            )
            wait_conf = 55.0
        else:
            wait_reason = (
                f"⏳ Vùng DISCOUNT ({htf_data.get('range_pos_pct', 30)}% Range): "
                f"Tuyệt đối không FOMO Short. Đang rình giá quét thủng Range Low (${rl:.4f}) rồi đóng nến quay lại vào trong Range để LONG."
            )
            wait_conf = 55.0

        return {
            "signal": "WAIT",
            "confidence": wait_conf,
            "setup": {"detected": False, "setup_type": "NO_SFP_WAIT"},
            "all_setups": [],
            "safe_sl": 0.0,
            "sl_pct": 0.0,
            "tp1_target": eq,
            "tp2_target": rh if zone == "DISCOUNT" else rl,
            "rr_ratio": 0.0,
            "is_htf_aligned": False,
            "btc_aligned": False,
            "btc_bias": btc_bias,
            "session": session_ctx,
            "htf_opens_and_ranges": htf_data,
            "htf_bias": htf_bias,
            "reason": wait_reason,
        }
