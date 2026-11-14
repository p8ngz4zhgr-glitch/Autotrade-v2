# ═══════════════════════════════════════════════════════════════════════════════
# NAKED-CHART PRICE ACTION PRIMITIVES — Pure TraderSZ Edition
# ═══════════════════════════════════════════════════════════════════════════════
# Loại bỏ hoàn toàn các chỉ báo kỹ thuật truyền thống (RSI, MACD, EMA 147/258/369,
# Bollinger, Stochastic, Ichimoku, Fibonacci, Wyckoff, Elliott, ADX, CHOP).
# Chỉ giữ lại các phép đo Price Action thuần túy phục vụ TraderSZ:
#   - True Range / Biên độ nến thực tế (hỗ trợ chống nhiễu SL/Trailing)
#   - Swing High / Swing Low (Cấu trúc đỉnh/đáy thuần túy)
#   - Wick Rejection / Sweep Candle anatomy
# ═══════════════════════════════════════════════════════════════════════════════

from typing import Dict, Any, List


class PriceActionPrimitives:
    """Các phép đo cấu trúc nến & thanh khoản thuần túy (Không dùng chỉ báo trễ)."""

    @staticmethod
    def true_range_volatility(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
        """Đo biên độ dao động trung bình thực tế của nến (dùng làm tham chiếu an toàn cho Trailing/Risk)."""
        if len(closes) < 2 or len(highs) < 2 or len(lows) < 2:
            return closes[-1] * 0.01 if closes else 0.0
        start = max(1, len(closes) - period)
        trs = [
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            for i in range(start, len(closes))
        ]
        return sum(trs) / len(trs) if trs else closes[-1] * 0.01

    @staticmethod
    def swing_levels(highs: List[float], lows: List[float], lookback: int = 20) -> Dict[str, float]:
        """Xác định Swing High / Swing Low gần nhất và điểm giữa 50% EQ."""
        if not highs or not lows:
            return {"swing_high": 0.0, "swing_low": 0.0, "mid": 0.0}
        sh = max(highs[-lookback:])
        sl = min(lows[-lookback:])
        return {
            "swing_high": round(sh, 4),
            "swing_low": round(sl, 4),
            "mid": round((sh + sl) / 2.0, 4),
        }

    @staticmethod
    def candle_rejection(open_p: float, high_p: float, low_p: float, close_p: float) -> Dict[str, Any]:
        """Đo tỷ lệ râu nến trên/dưới để nhận diện quét thanh khoản & từ chối giá (Rejection)."""
        span = max(high_p - low_p, 1e-9)
        body = abs(close_p - open_p)
        upper_wick = high_p - max(open_p, close_p)
        lower_wick = min(open_p, close_p) - low_p
        return {
            "upper_wick_pct": round(upper_wick / span * 100, 1),
            "lower_wick_pct": round(lower_wick / span * 100, 1),
            "body_pct": round(body / span * 100, 1),
            "is_bullish_pin": lower_wick / span >= 0.45 and close_p >= open_p,
            "is_bearish_pin": upper_wick / span >= 0.45 and close_p <= open_p,
        }


# Alias nhẹ để tránh lỗi import nếu có script ngoài tham chiếu
Indicators = PriceActionPrimitives
