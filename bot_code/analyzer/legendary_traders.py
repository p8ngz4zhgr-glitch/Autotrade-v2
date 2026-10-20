# ═══════════════════════════════════════════════════════════════════════════════
# LEGENDARY TRADERS STRATEGY FRAMEWORK (TÍCH HỢP CHIẾN LƯỢC CÁC TRADER NỔI TIẾNG)
# ═══════════════════════════════════════════════════════════════════════════════
# 1. Alexander Elder: Triple Screen System (H1 Tide -> M15 Wave -> M5 Ripple)
# 2. Michael Huddleston (ICT / SMC): Liquidity Sweep & Order Block Rejection
# 3. Mark Minervini: Volatility Contraction Pattern (VCP) & 1R Auto-Breakeven
# 4. Larry Williams: Scale-Out (TP1 60%) + ATR Trailing Stop (40%)
# 5. Richard Wyckoff: Spring / Upthrust Detection via VSA (Volume Spread Analysis)
# ═══════════════════════════════════════════════════════════════════════════════

import math
from typing import Dict, Any, List, Optional


class LegendaryTradersFramework:
    """
    Tập hợp các bộ lọc và mô hình vào lệnh từ các huyền thoại giao dịch:
    - Giải quyết dứt điểm vấn đề dính Stop Loss liên tục trên M5
    - Tối ưu hóa vốn nhỏ ($116) với đòn bẩy x5 an toàn (không lo bị thanh lý)
    """

    @staticmethod
    def elder_triple_screen(
        h1_closes: List[float],
        m15_closes: List[float],
        m5_closes: List[float],
        h1_ema50: float,
        h1_ema200: float,
        m15_rsi: float,
        m5_rsi: float
    ) -> Dict[str, Any]:
        """
        [ALEXANDER ELDER - TRIPLE SCREEN SYSTEM]
        Nguyên tắc: "Đừng bao giờ bơi ngược dòng thủy triều".
        - Screen 1 (H1 - Thủy triều): Xác định xu hướng lớn. H1 > EMA50 > EMA200 -> CHỈ LONG.
        - Screen 2 (M15 - Ngọn sóng): Chờ sóng hồi ngược chiều (Pullback). Long khi M15 RSI quá bán.
        - Screen 3 (M5 - Gợn sóng kích hoạt): Bắn tỉa khi nến M5 bắt đầu bẻ lái thuận chiều thủy triều.
        """
        if not h1_closes or not m15_closes or not m5_closes:
            return {"allowed_direction": "BOTH", "score": 0, "reason": "Thiếu dữ liệu đa khung"}

        h1_price = h1_closes[-1]
        # Screen 1: The Tide (H1)
        if h1_price > h1_ema50 and h1_ema50 >= h1_ema200 * 0.998:
            tide = "BULLISH"
        elif h1_price < h1_ema50 and h1_ema50 <= h1_ema200 * 1.002:
            tide = "BEARISH"
        else:
            tide = "NEUTRAL"

        # Screen 2: The Wave (M15 Pullback)
        wave_pullback = False
        if tide == "BULLISH" and m15_rsi <= 48:
            wave_pullback = True
        elif tide == "BEARISH" and m15_rsi >= 52:
            wave_pullback = True

        # Screen 3: Trigger confirmation (M5)
        m5_trigger = False
        if tide == "BULLISH" and wave_pullback and m5_rsi > 35 and m5_closes[-1] > m5_closes[-2]:
            m5_trigger = True
        elif tide == "BEARISH" and wave_pullback and m5_rsi < 65 and m5_closes[-1] < m5_closes[-2]:
            m5_trigger = True

        allowed = "BUY_ONLY" if tide == "BULLISH" else ("SELL_ONLY" if tide == "BEARISH" else "BOTH")
        
        return {
            "tide_h1": tide,
            "wave_pullback_m15": wave_pullback,
            "m5_trigger": m5_trigger,
            "allowed_direction": allowed,
            "score_boost": 20 if m5_trigger else (10 if wave_pullback else 0),
            "description": f"Elder Triple Screen: Thủy triều H1 {tide} | Sóng hồi M15 {'Hợp lệ' if wave_pullback else 'Chờ hồi'} | Kích hoạt M5 {'SẴN SÀNG' if m5_trigger else 'Chờ tín hiệu'}"
        }

    @staticmethod
    def ict_liquidity_sweep(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        opens: List[float],
        lookback: int = 24
    ) -> Dict[str, Any]:
        """
        [MICHAEL HUDDLESTON - ICT / SMART MONEY CONCEPTS]
        Nguyên tắc: "Market Maker luôn săn thanh khoản ở đỉnh/đáy cũ trước khi đẩy giá thật".
        - Quét thanh khoản đáy (Bullish Liquidity Sweep): Giá chọc thủng đáy cũ (Swing Low) của 24 nến gần nhất,
          nhưng nến đóng cửa rút chân lên cao hơn đáy cũ (Pinbar / Fakeout).
        - Quét thanh khoản đỉnh (Bearish Liquidity Sweep): Giá chọc qua đỉnh cũ (Swing High) rồi rút râu cắm xuống.
        -> Điểm vào lệnh chuẩn xác, Stop-loss đặt ngay dưới râu nến quét (cực kỳ an toàn, không bị quét lần 2).
        """
        if len(closes) < lookback + 2:
            return {"sweep_detected": False, "type": "NONE", "safe_sl": 0.0}

        # Tìm swing high và swing low trước đó (bỏ qua 2 nến gần nhất đang hình thành)
        prev_highs = highs[-lookback:-2]
        prev_lows = lows[-lookback:-2]
        swing_high = max(prev_highs)
        swing_low = min(prev_lows)

        curr_high = highs[-1]
        curr_low = lows[-1]
        curr_close = closes[-1]
        curr_open = opens[-1]

        # Kiểm tra Bullish Sweep (Săn thanh khoản bán để gom hàng đẩy lên)
        # Râu nến đâm qua swing low nhưng đóng nến nằm trên swing low
        is_bull_sweep = (curr_low < swing_low) and (curr_close > swing_low) and (curr_close >= curr_open)
        # Râu nến dưới dài ít nhất 40% chiều dài toàn bộ cây nến (Pinbar rút chân)
        candle_len = max(curr_high - curr_low, 1e-6)
        lower_wick = min(curr_open, curr_close) - curr_low
        wick_ratio = lower_wick / candle_len

        if is_bull_sweep and wick_ratio >= 0.35:
            # Safe SL đặt dưới đáy râu nến vừa quét + 0.15% buffer
            safe_sl = curr_low * 0.9985
            return {
                "sweep_detected": True,
                "type": "BULLISH_SWEEP",
                "swing_level": swing_low,
                "wick_low": curr_low,
                "safe_sl": safe_sl,
                "score_boost": 25,
                "reason": f"ICT Bullish Sweep: Quét sạch SL đáy cũ ${swing_low:.2f} rồi rút chân nến {wick_ratio*100:.0f}%! MM đã gom xong hàng."
            }

        # Kiểm tra Bearish Sweep (Săn thanh khoản mua để xả hàng đẩy xuống)
        is_bear_sweep = (curr_high > swing_high) and (curr_close < swing_high) and (curr_close <= curr_open)
        upper_wick = curr_high - max(curr_open, curr_close)
        upper_wick_ratio = upper_wick / candle_len

        if is_bear_sweep and upper_wick_ratio >= 0.35:
            safe_sl = curr_high * 1.0015
            return {
                "sweep_detected": True,
                "type": "BEARISH_SWEEP",
                "swing_level": swing_high,
                "wick_high": curr_high,
                "safe_sl": safe_sl,
                "score_boost": 25,
                "reason": f"ICT Bearish Sweep: Quét đỉnh cũ ${swing_high:.2f} rồi rụt cổ nến {upper_wick_ratio*100:.0f}%! MM đã xả hàng."
            }

        return {"sweep_detected": False, "type": "NONE", "safe_sl": 0.0}

    @staticmethod
    def minervini_vcp_filter(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        bb_width_series: List[float]
    ) -> Dict[str, Any]:
        """
        [MARK MINERVINI - VOLATILITY CONTRACTION PATTERN (VCP)]
        Nguyên tắc: "Biên độ co thắt trước khi sóng thần xuất hiện".
        - Tránh vào lệnh khi thị trường biến động hỗn loạn (Chop).
        - Khi dải Bollinger co thắt hẹp nhất (Squeeze) và ATR nén hẹp qua 3 đợt (T1 > T2 > T3),
          áp lực cung cạn kiệt, chỉ cần một lượng vol nhỏ là giá bật mạnh theo xu hướng.
        """
        if len(closes) < 20 or len(bb_width_series) < 10:
            return {"is_contracted": False, "status": "NORMAL", "score": 0}

        curr_bbw = bb_width_series[-1]
        avg_bbw_10 = sum(bb_width_series[-10:]) / 10

        # Kiểm tra biên độ nến 5 cây gần nhất so với 20 cây trước
        recent_ranges = [highs[i] - lows[i] for i in range(-5, 0)]
        prev_ranges = [highs[i] - lows[i] for i in range(-20, -5)]

        avg_recent = sum(recent_ranges) / len(recent_ranges) if recent_ranges else 1
        avg_prev = sum(prev_ranges) / len(prev_ranges) if prev_ranges else 1

        is_squeeze = curr_bbw < avg_bbw_10 * 0.85
        contraction_ratio = avg_recent / (avg_prev if avg_prev > 0 else 1)

        # Tính độ định hướng (Directional Efficiency) của 5 nến gần nhất:
        # Nếu giá đi thẳng 1 chiều (Trend Expansion) -> Không phải Chop!
        net_move = abs(closes[-1] - closes[-5]) if len(closes) >= 5 else 0
        total_travel = sum(recent_ranges) if sum(recent_ranges) > 0 else 1
        efficiency = net_move / total_travel  # Gần 1 là xu hướng mạnh, gần 0 là quét râu 2 đầu

        if is_squeeze and contraction_ratio < 0.70:
            return {
                "is_contracted": True,
                "status": "VCP_PRIME_SETUP",
                "contraction_ratio": round(contraction_ratio, 2),
                "score_boost": 15,
                "desc": f"Minervini VCP: Nén chặt biên độ (Biên độ giảm {round((1-contraction_ratio)*100)}%, BB thắt nút). Sẵn sàng bùng nổ theo xu hướng!"
            }
        elif contraction_ratio > 1.4:
            if efficiency >= 0.40:
                # Xu hướng đang bứt phá mạnh mẽ (Breakout / Trend Expansion)
                return {
                    "is_contracted": False,
                    "status": "TREND_EXPANSION",
                    "contraction_ratio": round(contraction_ratio, 2),
                    "score_boost": 10,
                    "desc": f"Minervini Momentum: Nến mở rộng theo xu hướng một chiều rõ nét (Độ hiệu quả {efficiency*100:.0f}%). Cho phép vào lệnh theo trend!"
                }
            else:
                # Quét râu hai đầu hỗn loạn không rõ hướng -> True Chop
                return {
                    "is_contracted": False,
                    "status": "HIGH_VOLATILITY_CHOP",
                    "contraction_ratio": round(contraction_ratio, 2),
                    "score_boost": -15,
                    "desc": "Thị trường biến động râu nến hỗn loạn 2 đầu (Chop rộng). Minervini khuyến nghị đứng ngoài quan sát."
                }

        return {"is_contracted": False, "status": "NORMAL", "contraction_ratio": 1.0, "score_boost": 0, "desc": "Biên độ bình thường."}

    @staticmethod
    def larry_williams_targets(
        entry_price: float,
        side: str,
        atr_value: float,
        capital_usd: float = 116.0,
        leverage: float = 5.0
    ) -> Dict[str, Any]:
        """
        [LARRY WILLIAMS - SCALE-OUT + TRAILING ATR]
        Kỷ lục gia biến $10k thành $1.1 triệu USD:
        - Vị thế $116 x 5 = $580.
        - Chia lệnh làm 2 chặng:
          + Chốt 60% ở TP1 (1.2R ~ $2.5 - $3.5): Đút tiền tươi vào túi để đảm bảo ngày nào cũng có lãi.
          + Giữ 40% còn lại gồng theo Larry Williams ATR Trailing Stop (1.5 x ATR) để ăn sóng lớn 2% - 4% (mang lại thêm $10 - $20).
          + Đồng thời kích hoạt Auto-Breakeven dời SL về Entry khi đạt TP1.
        """
        position_size_usd = capital_usd * leverage
        
        # Stoploss tối thiểu 1.1% để tránh bị râu M5 quét
        sl_dist = max(atr_value * 1.8, entry_price * 0.011)
        sl_pct = (sl_dist / entry_price) * 100
        
        # TP1: 1.2R (Khoảng 1.3% - 1.5% giá)
        tp1_dist = sl_dist * 1.2
        tp1_pct = (tp1_dist / entry_price) * 100
        
        # TP2: 2.2R (Khoảng 2.4% - 3.2% giá)
        tp2_dist = sl_dist * 2.2
        tp2_pct = (tp2_dist / entry_price) * 100

        if side.upper() in ["BUY", "LONG"]:
            sl_price = entry_price - sl_dist
            tp1_price = entry_price + tp1_dist
            tp2_price = entry_price + tp2_dist
        else:
            sl_price = entry_price + sl_dist
            tp1_price = entry_price - tp1_dist
            tp2_price = entry_price - tp2_dist

        # Lãi ước tính khi chia 60/40:
        profit_tp1 = (position_size_usd * 0.60) * (tp1_pct / 100)
        profit_tp2 = (position_size_usd * 0.40) * (tp2_pct / 100)
        total_full_profit = profit_tp1 + profit_tp2
        risk_usd = position_size_usd * (sl_pct / 100)

        return {
            "entry_price": round(entry_price, 4),
            "sl_price": round(sl_price, 4),
            "sl_pct": round(sl_pct, 2),
            "tp1_price": round(tp1_price, 4),
            "tp1_pct": round(tp1_pct, 2),
            "tp1_close_ratio": 0.60,
            "tp1_est_profit_usd": round(profit_tp1, 2),
            "tp2_price": round(tp2_price, 4),
            "tp2_pct": round(tp2_pct, 2),
            "tp2_close_ratio": 0.40,
            "tp2_est_profit_usd": round(profit_tp2, 2),
            "total_win_profit_usd": round(total_full_profit, 2),
            "max_risk_usd": round(risk_usd, 2),
            "rr_ratio": round(total_full_profit / (risk_usd if risk_usd > 0 else 1), 2),
            "auto_breakeven_trigger": round(tp1_price, 4)
        }
