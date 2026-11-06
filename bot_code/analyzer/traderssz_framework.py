# ═══════════════════════════════════════════════════════════════════════════════
# TRADERSZ (TRADERSSZ) INSTITUTIONAL RANGE, OPENS & 50% EQUILIBRIUM FRAMEWORK
# ═══════════════════════════════════════════════════════════════════════════════
# Core methodology of Traderssz:
# 1. Macro Opens (The Anchors):
#    - Year Open (YO)
#    - Monthly Open (MO)
#    - Weekly Open (WO)
#    - Daily Open (DO)
# 2. Key Highs & Lows (Liquidity Extremes):
#    - Previous Day High (PDH) & Previous Day Low (PDL)
#    - Previous Week High (PWH) & Previous Week Low (PWL)
# 3. 50% Equilibrium (EQ) & Dealing Ranges (D, W, M):
#    - Range High (RH) & Range Low (RL)
#    - 50% Midpoint (EQ = (RH + RL) / 2)
#    - Premium Zone (> 50% EQ): Khu vực giá đắt, CHỈ CANH BÁN (SHORT).
#    - Discount Zone (< 50% EQ): Khu vực giá rẻ, CHỈ CANH MUA (LONG).
# 4. Range-to-Range Execution (Đánh từ biên này sang biên kia):
#    - Quét PDL / RL (hoặc rút chân tại Discount) -> Đánh bật lên 50% EQ rồi tiến về RH / PDH.
#    - Quét PDH / RH (hoặc rụt râu tại Premium) -> Đánh xả xuống 50% EQ rồi tiến về RL / PDL.
# 5. Session Killzones & FVG:
#    - Hội tụ cùng FVG retest để tối ưu hóa điểm vào lệnh & Risk-to-Reward 1:2.0 - 1:3.0+.
# ═══════════════════════════════════════════════════════════════════════════════

import math
from datetime import datetime
from typing import Dict, Any, List, Optional


class TradersszFramework:
    """
    Traderssz SMC Multi-Timeframe Strategy Engine.
    Implements exact institutional Opens (Year, Month, Week, Day),
    Dealing Ranges (High, Low), 50% Equilibrium (EQ), and Range-to-Range trading.
    """

    @staticmethod
    def detect_session_context(utcnow: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Traderssz Session Killzones:
        - Asian Session (00:00 - 08:00 UTC): Xây dựng Range tích lũy (Range High / Low).
        - London Killzone (08:00 - 13:00 UTC): Manipulation / Săn thanh khoản biên Range.
        - New York Killzone (13:00 - 21:00 UTC): Dòng tiền lớn Phố Wall bùng nổ xu hướng.
        - Dead Zone (21:00 - 24:00 UTC): Giãn spread, cấm mở vị thế.
        """
        if utcnow is None:
            utcnow = datetime.utcnow()
        hour_utc = utcnow.hour + utcnow.minute / 60.0

        if 0.0 <= hour_utc < 8.0:
            return {
                "session": "ASIA",
                "is_killzone": False,
                "allow_trades": True,
                "preferred_mode": "RANGE_SWEEP",
                "desc": "Phiên Á (00-08h UTC) — Biên tích lũy, săn sweep đỉnh đáy range"
            }
        elif 8.0 <= hour_utc < 13.0:
            return {
                "session": "LONDON_OPEN",
                "is_killzone": True,
                "allow_trades": True,
                "preferred_mode": "KILLZONE_DISPLACEMENT",
                "desc": "London Killzone (08-13h UTC) — Thời điểm vàng quét biên Range & bứt phá"
            }
        elif 13.0 <= hour_utc < 21.0:
            return {
                "session": "NEW_YORK_OPEN",
                "is_killzone": True,
                "allow_trades": True,
                "preferred_mode": "KILLZONE_DISPLACEMENT",
                "desc": "New York Killzone (13-21h UTC) — Dòng tiền lớn bùng nổ, trend chạy từ đầu này sang đầu kia range"
            }
        else:
            return {
                "session": "DEAD_ZONE",
                "is_killzone": False,
                "allow_trades": False,
                "preferred_mode": "NO_TRADE",
                "desc": "Dead Zone (21-24h UTC) — Giãn spread, thanh khoản mỏng, cấm mở vị thế"
            }

    @staticmethod
    def calculate_htf_opens_and_ranges(
        d1_klines: Optional[Dict[str, List[float]]],
        current_price: float
    ) -> Dict[str, Any]:
        """
        Xác định các mốc then chốt theo phương pháp Traderssz:
        - Daily Open (DO), Weekly Open (WO), Monthly Open (MO), Year Open (YO)
        - Previous Day High (PDH) & Previous Day Low (PDL)
        - Previous Week High (PWH) & Previous Week Low (PWL)
        - Dealing Range & 50% Equilibrium (EQ):
          + Range High (RH) & Range Low (RL)
          + 50% EQ = (RH + RL) / 2
          + Vùng Premium (Giá > 50% EQ) -> Ưu tiên CANH SHORT.
          + Vùng Discount (Giá < 50% EQ) -> Ưu tiên CANH LONG.
        """
        if not d1_klines or len(d1_klines.get("close", [])) < 5:
            # Fallback nếu thiếu nến D1
            return {
                "daily_open": current_price,
                "weekly_open": current_price,
                "monthly_open": current_price,
                "year_open": current_price,
                "pdh": current_price * 1.015,
                "pdl": current_price * 0.985,
                "range_high": current_price * 1.02,
                "range_low": current_price * 0.98,
                "eq_50": current_price,
                "market_zone": "EQUILIBRIUM",
                "dist_to_eq_pct": 0.0
            }

        d_opens = d1_klines.get("open", [])
        d_highs = d1_klines.get("high", [])
        d_lows = d1_klines.get("low", [])
        d_closes = d1_klines.get("close", [])
        n = len(d_closes)

        # 1. Các mốc Open giá (Anchors)
        daily_open = d_opens[-1]
        pdh = d_highs[-2] if n >= 2 else d_highs[-1]
        pdl = d_lows[-2] if n >= 2 else d_lows[-1]

        # Weekly Open (7 nến ngày gần nhất)
        w_lookback = min(7, n)
        weekly_open = d_opens[-w_lookback]
        pwh = max(d_highs[-w_lookback:])
        pwl = min(d_lows[-w_lookback:])

        # Monthly Open (30 nến ngày gần nhất)
        m_lookback = min(30, n)
        monthly_open = d_opens[-m_lookback]
        month_high = max(d_highs[-m_lookback:])
        month_low = min(d_lows[-m_lookback:])

        # Year Open (Nến xa nhất trong data D1 nhận được hoặc nến đầu năm)
        year_open = d_opens[0]

        # 2. XÁC ĐỊNH PHẠM VI RANGE VÀ VÙNG 50% EQUILIBRIUM (EQ)
        # Khung Range giao dịch chính được định nghĩa từ 14 đến 30 ngày (Dealing Range)
        range_high = max(month_high, pwh)
        range_low = min(month_low, pwl)
        eq_50 = (range_high + range_low) / 2.0

        range_width_pct = (range_high - range_low) / range_low * 100 if range_low > 0 else 1.0
        dist_to_eq_pct = (current_price - eq_50) / eq_50 * 100

        # Phân loại Premium / Discount
        if current_price > eq_50 * 1.002:
            market_zone = "PREMIUM"      # Vùng giá cao (Đắt) -> Trọng tâm tìm cơ hội BÁN (SHORT)
        elif current_price < eq_50 * 0.998:
            market_zone = "DISCOUNT"     # Vùng giá thấp (Rẻ) -> Trọng tâm tìm cơ hội MUA (LONG)
        else:
            market_zone = "EQUILIBRIUM"  # Ngay điểm cân bằng 50% (Hạn chế vào lệnh, chờ bứt phá)

        return {
            "daily_open": round(daily_open, 4),
            "weekly_open": round(weekly_open, 4),
            "monthly_open": round(monthly_open, 4),
            "year_open": round(year_open, 4),
            "pdh": round(pdh, 4),
            "pdl": round(pdl, 4),
            "pwh": round(pwh, 4),
            "pwl": round(pwl, 4),
            "range_high": round(range_high, 4),
            "range_low": round(range_low, 4),
            "eq_50": round(eq_50, 4),
            "range_width_pct": round(range_width_pct, 2),
            "market_zone": market_zone,
            "dist_to_eq_pct": round(dist_to_eq_pct, 2)
        }

    @staticmethod
    def detect_fvg(
        opens: List[float],
        highs: List[float],
        lows: List[float],
        closes: List[float],
        lookback: int = 15
    ) -> Dict[str, Any]:
        """
        Nhận diện khoảng trống mất cân bằng cung cầu (Fair Value Gap - FVG).
        """
        if len(closes) < 5:
            return {"has_fvg": False, "type": "NONE", "fvg_top": 0.0, "fvg_bottom": 0.0}

        n = len(closes)
        start_idx = max(2, n - lookback)

        for i in range(n - 1, start_idx - 1, -1):
            c1_high = highs[i - 2]
            c1_low = lows[i - 2]
            c2_open, c2_close = opens[i - 1], closes[i - 1]
            c3_high, c3_low = highs[i], lows[i]
            curr_price = closes[-1]

            # Bullish FVG
            if c3_low > c1_high and c2_close > c2_open:
                fvg_bottom = c1_high
                fvg_top = c3_low
                gap_pct = (fvg_top - fvg_bottom) / fvg_bottom * 100
                if gap_pct >= 0.12:
                    is_in_fvg = (fvg_bottom <= curr_price <= fvg_top * 1.002)
                    return {
                        "has_fvg": True,
                        "type": "BULLISH_FVG",
                        "fvg_top": round(fvg_top, 4),
                        "fvg_bottom": round(fvg_bottom, 4),
                        "gap_pct": round(gap_pct, 2),
                        "is_retesting": is_in_fvg,
                        "desc": f"Bullish FVG (${fvg_bottom:.2f} - ${fvg_top:.2f})"
                    }

            # Bearish FVG
            elif c1_low > c3_high and c2_close < c2_open:
                fvg_top = c1_low
                fvg_bottom = c3_high
                gap_pct = (fvg_top - fvg_bottom) / fvg_top * 100
                if gap_pct >= 0.12:
                    is_in_fvg = (fvg_bottom * 0.998 <= curr_price <= fvg_top)
                    return {
                        "has_fvg": True,
                        "type": "BEARISH_FVG",
                        "fvg_top": round(fvg_top, 4),
                        "fvg_bottom": round(fvg_bottom, 4),
                        "gap_pct": round(gap_pct, 2),
                        "is_retesting": is_in_fvg,
                        "desc": f"Bearish FVG (${fvg_bottom:.2f} - ${fvg_top:.2f})"
                    }

        return {"has_fvg": False, "type": "NONE", "fvg_top": 0.0, "fvg_bottom": 0.0}

    @staticmethod
    def detect_range_sweep(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        opens: List[float],
        volumes: List[float],
        htf_data: Dict[str, Any],
        lookback: int = 24
    ) -> Dict[str, Any]:
        """
        Nhận diện Quét thanh khoản biên Range (Range Sweep):
        - Quét PDL / PWL / Range Low ở vùng DISCOUNT -> Kích hoạt LONG đánh bật về 50% EQ & Range High.
        - Quét PDH / PWH / Range High ở vùng PREMIUM -> Kích hoạt SHORT đánh xả về 50% EQ & Range Low.
        """
        if len(closes) < lookback + 2:
            return {"detected": False, "setup": "NONE"}

        curr_price = closes[-1]
        c_open = opens[-1]
        c_high = highs[-1]
        c_low = lows[-1]

        candle_len = max(c_high - c_low, 1e-6)
        lower_wick = min(c_open, curr_price) - c_low
        upper_wick = c_high - max(c_open, curr_price)

        avg_vol = sum(volumes[-20:-1]) / 19 if len(volumes) >= 20 else 1.0
        curr_vol = volumes[-1] if volumes else 1.0
        vol_ratio = curr_vol / avg_vol if avg_vol > 0 else 1.0

        pdh = htf_data.get("pdh", 0)
        pdl = htf_data.get("pdl", 0)
        pwh = htf_data.get("pwh", 0)
        pwl = htf_data.get("pwl", 0)
        rh = htf_data.get("range_high", 0)
        rl = htf_data.get("range_low", 0)
        eq_50 = htf_data.get("eq_50", 0)

        # 1. QUÉT ĐÁY BIÊN RANGE (SSL / PDL / RL SWEEP) TẠI DISCOUNT -> LONG ĐÁNH VỀ EQ & RH
        target_low = max(pdl, pwl, rl * 0.99)
        swept_low = (min(lows[-3:]) <= target_low * 1.001) and (curr_price > target_low)
        is_bull_rejection = (curr_price > c_open) and ((lower_wick / candle_len) >= 0.30 or vol_ratio >= 1.2)

        if swept_low and is_bull_rejection and curr_price < eq_50 * 1.01:
            sweep_min = min(lows[-3:])
            safe_sl = round(sweep_min * 0.9975, 4)
            # Target 1: Mốc 50% Equilibrium (vùng cân bằng)
            # Target 2: Range High / PDH đối diện (đầu bên kia của range)
            tp1 = round(eq_50, 4)
            tp2 = round(rh if rh > curr_price else curr_price * 1.025, 4)

            return {
                "detected": True,
                "setup": "RANGE_LOW_SWEEP_LONG",
                "direction": "LONG",
                "swept_level": round(target_low, 4),
                "safe_sl": safe_sl,
                "tp1_eq": tp1,
                "tp2_rh": tp2,
                "vol_ratio": round(vol_ratio, 2),
                "reason": f"Traderssz Range Model: Quét thanh khoản đáy Range/PDL (${target_low:.2f}) tại vùng DISCOUNT -> Kích hoạt LONG đánh từ đầu này sang đầu kia Range (TP1: EQ 50% ${tp1:.2f} | TP2: Range High ${tp2:.2f})!"
            }

        # 2. QUÉT ĐỈNH BIÊN RANGE (BSL / PDH / RH SWEEP) TẠI PREMIUM -> SHORT ĐÁNH VỀ EQ & RL
        target_high = min(pdh, pwh, rh * 1.01)
        swept_high = (max(highs[-3:]) >= target_high * 0.999) and (curr_price < target_high)
        is_bear_rejection = (curr_price < c_open) and ((upper_wick / candle_len) >= 0.30 or vol_ratio >= 1.2)

        if swept_high and is_bear_rejection and curr_price > eq_50 * 0.99:
            sweep_max = max(highs[-3:])
            safe_sl = round(sweep_max * 1.0025, 4)
            # Target 1: Mốc 50% Equilibrium (vùng cân bằng)
            # Target 2: Range Low / PDL đối diện (đầu bên kia của range)
            tp1 = round(eq_50, 4)
            tp2 = round(rl if rl < curr_price and rl > 0 else curr_price * 0.975, 4)

            return {
                "detected": True,
                "setup": "RANGE_HIGH_SWEEP_SHORT",
                "direction": "SHORT",
                "swept_level": round(target_high, 4),
                "safe_sl": safe_sl,
                "tp1_eq": tp1,
                "tp2_rl": tp2,
                "vol_ratio": round(vol_ratio, 2),
                "reason": f"Traderssz Range Model: Quét thanh khoản đỉnh Range/PDH (${target_high:.2f}) tại vùng PREMIUM -> Kích hoạt SHORT đánh từ đầu này sang đầu kia Range (TP1: EQ 50% ${tp1:.2f} | TP2: Range Low ${tp2:.2f})!"
            }

        return {"detected": False, "setup": "NONE"}

    @staticmethod
    def evaluate_traderssz_complete(
        m5_opens: List[float],
        m5_highs: List[float],
        m5_lows: List[float],
        m5_closes: List[float],
        m5_volumes: List[float],
        d1_klines: Optional[Dict[str, List[float]]],
        current_price: float
    ) -> Dict[str, Any]:
        """
        Tổng hợp toàn diện hệ thống Traderssz:
        Opens (Year, Month, Week, Day) + 50% EQ Range + Session Killzones + Sweep + FVG.
        """
        session_ctx = TradersszFramework.detect_session_context()
        htf_data = TradersszFramework.calculate_htf_opens_and_ranges(d1_klines, current_price)

        range_sweep = TradersszFramework.detect_range_sweep(
            m5_highs, m5_lows, m5_closes, m5_opens, m5_volumes, htf_data, lookback=24
        )
        fvg_res = TradersszFramework.detect_fvg(
            m5_opens, m5_highs, m5_lows, m5_closes, lookback=12
        )

        signal = "WAIT"
        conf = 50.0
        reason = "Chờ giá tiếp cận biên Range High/Low hoặc điểm cân bằng 50% EQ."
        safe_sl = 0.0
        tp1_target = htf_data.get("eq_50", current_price)
        tp2_target = htf_data.get("range_high" if signal == "LONG" else "range_low", current_price)

        if range_sweep.get("detected"):
            direction = range_sweep.get("direction")
            safe_sl = range_sweep.get("safe_sl")
            tp1_target = range_sweep.get("tp1_eq")
            tp2_target = range_sweep.get("tp2_rh" if direction == "LONG" else "tp2_rl")

            # Kiểm tra vị thế tương quan với Year Open, Month Open, Week Open
            # Thuận sóng Open (Ví dụ LONG khi giá đang ở trên Weekly Open hoặc bật lại từ Monthly Open)
            mo = htf_data.get("monthly_open", current_price)
            wo = htf_data.get("weekly_open", current_price)
            is_open_confluence = (direction == "LONG" and current_price >= wo * 0.998) or \
                                 (direction == "SHORT" and current_price <= wo * 1.002)

            # Hội tụ thêm Retest FVG -> A+ SETUP đỉnh cao của Traderssz
            if fvg_res.get("has_fvg"):
                fvg_type = fvg_res.get("type")
                if (direction == "LONG" and fvg_type == "BULLISH_FVG") or \
                   (direction == "SHORT" and fvg_type == "BEARISH_FVG"):
                    signal = direction
                    conf = 92.0
                    reason = f"⭐ [TRADERSSZ A+ RANGE SETUP] {range_sweep.get('reason')} Kèm Retest {fvg_res.get('desc')}!"
                else:
                    signal = direction
                    conf = 82.0
                    reason = f"⚡ [TRADERSSZ RANGE SETUP] {range_sweep.get('reason')}"
            else:
                signal = direction
                conf = 78.0
                reason = f"⚡ [TRADERSSZ RANGE SETUP] {range_sweep.get('reason')}"

            if is_open_confluence:
                conf = min(98.0, conf + 5.0)

            if session_ctx.get("is_killzone"):
                conf = min(98.0, conf + 5.0)

        return {
            "signal": signal,
            "confidence": conf,
            "safe_sl": safe_sl,
            "tp1_target": tp1_target,
            "tp2_target": tp2_target,
            "session": session_ctx,
            "htf_opens_and_ranges": htf_data,
            "range_sweep": range_sweep,
            "fvg": fvg_res,
            "reason": reason
        }
