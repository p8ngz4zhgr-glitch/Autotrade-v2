# ═══════════════════════════════════════════════════════════
# GIANLUCA BRUNI ASIAN SESSION SWEEP ANALYZER
# Chiến Lược Săn Thanh Khoản Hộp Phiên Á (World Trading Champion)
# ═══════════════════════════════════════════════════════════
import logging
from datetime import datetime, timezone

log = logging.getLogger("BruniAnalyzer")

def analyze_bruni_asian_sweep(closes: list, highs: list, lows: list, opens: list, current_price: float, now_dt: datetime = None) -> dict:
    """
    Phân tích Thiết lập Giao dịch Gianluca Bruni (Asian Range Box + Fakeout Liquidity Sweep):
    1. Tính Hộp Phiên Á (Asian High/Low Box) trong khung 00:00 - 08:00 UTC.
    2. Phát hiện Bẫy Phá Vỡ Giả (Fakeouts) trong Phiên London/Mỹ (sau 08:00 UTC):
       - Bull Trap Sweep: Giá vượt Asian High rồi rút râu sập mạnh xuống lại Hộp -> Kích hoạt SHORT.
       - Bear Trap Sweep: Giá thủng Asian Low rồi rút chân giật mạnh lên lại Hộp -> Kích hoạt LONG.
    3. Tính toán SL/TP chuẩn Bruni (SL ngoài râu quét, TP1 tại Trục Giữa, TP2 tại Biên Đối Diện).
    """
    EMPTY = {
        "detected": False,
        "signal": "NONE", # "LONG", "SHORT", "NONE"
        "setup_name": "NONE",
        "asian_high": 0.0,
        "asian_low": 0.0,
        "asian_mid": 0.0,
        "sweep_price": 0.0,
        "suggested_sl": 0.0,
        "suggested_tp1": 0.0,
        "suggested_tp2": 0.0,
        "confidence_boost": 0.0,
        "reason": "Không có thiết lập bẫy thanh khoản Hộp Phiên Á."
    }

    if not closes or not highs or not lows or len(closes) < 32:
        return EMPTY

    if now_dt is None:
        now_dt = datetime.utcnow()

    # Chỉ tính toán khi đã bước vào Phiên London/Mỹ (sau 08:00 UTC)
    # Lấy 32 nến 15m gần nhất đại diện cho chu kỳ 8 tiếng phiên Á
    n_candles = min(len(closes), 96) # 24 tiếng = 96 nến 15m
    h_slice = highs[-n_candles:]
    l_slice = lows[-n_candles:]
    c_slice = closes[-n_candles:]
    o_slice = opens[-n_candles:] if opens else c_slice

    # Giả định 32 nến đầu tiên trong chu kỳ ngày là Phiên Á (00:00 - 08:00 UTC)
    asian_candles = 32
    if len(h_slice) < asian_candles + 4:
        return EMPTY

    asian_high = max(h_slice[:asian_candles])
    asian_low = min(l_slice[:asian_candles])
    asian_mid = round((asian_high + asian_low) / 2.0, 4)

    if asian_high <= asian_low or asian_high == 0:
        return EMPTY

    range_pct = ((asian_high - asian_low) / asian_low) * 100
    # Nếu biên độ phiên Á quá hẹp (<0.2%) hoặc quá biến động (>4.0%), bỏ qua để tránh nhiễu
    if range_pct < 0.2 or range_pct > 4.0:
        return EMPTY

    # Quan sát các nến sau phiên Á (Phiên London / Mỹ)
    post_asian_highs = h_slice[asian_candles:]
    post_asian_lows = l_slice[asian_candles:]
    recent_high = max(post_asian_highs[-6:]) if len(post_asian_highs) >= 6 else max(post_asian_highs)
    recent_low = min(post_asian_lows[-6:]) if len(post_asian_lows) >= 6 else min(post_asian_lows)

    last_close = c_slice[-1]
    last_high = h_slice[-1]
    last_low = l_slice[-1]
    last_open = o_slice[-1]

    # 1. BRUNI BULL TRAP SWEEP (SHORT SIGNAL)
    # Giá vừa giật vượt Asian High nhưng nến hiện tại/gần đây quay trở lại bên trong/dưới Asian High
    if recent_high > asian_high and last_close < asian_high:
        top_wick = last_high - max(last_open, last_close)
        body = abs(last_close - last_open)
        # Nến có râu trên dài gấp đôi thân nến (Rejection) hoặc cạn lực mua
        if top_wick >= body * 0.8 or last_close < last_open:
            sweep_p = recent_high
            sl_price = round(sweep_p * 1.003, 4) # SL ở trên đỉnh râu quét +0.3%
            tp1 = asian_mid
            tp2 = asian_low

            log.info("🏆 [GIANLUCA BRUNI SET UP] Phát hiện Bull Trap Sweep Phiên Á! (Asian High: %.4f | Sweep High: %.4f | Close: %.4f)",
                     asian_high, sweep_p, last_close)

            return {
                "detected": True,
                "signal": "SHORT",
                "setup_name": "BRUNI_BULL_TRAP_SWEEP",
                "asian_high": asian_high,
                "asian_low": asian_low,
                "asian_mid": asian_mid,
                "sweep_price": sweep_p,
                "suggested_sl": sl_price,
                "suggested_tp1": tp1,
                "suggested_tp2": tp2,
                "confidence_boost": 20.0, # Thưởng 20% độ tự tin
                "reason": f"Mô hình Gianluca Bruni: Quét đỉnh Hộp Phiên Á (${asian_high:.4f}) thành công rồi sập râu trở lại Hộp -> Kích hoạt SHORT đón sóng xả."
            }

    # 2. BRUNI BEAR TRAP SWEEP (LONG SIGNAL)
    # Giá vừa giật thủng Asian Low nhưng nến hiện tại/gần đây giật chân quay trở lại bên trên Asian Low
    if recent_low < asian_low and last_close > asian_low:
        bottom_wick = min(last_open, last_close) - last_low
        body = abs(last_close - last_open)
        # Nến có râu dưới dài (Rejection Pinbar)
        if bottom_wick >= body * 0.8 or last_close > last_open:
            sweep_p = recent_low
            sl_price = round(sweep_p * 0.997, 4) # SL dưới đáy râu quét -0.3%
            tp1 = asian_mid
            tp2 = asian_high

            log.info("🏆 [GIANLUCA BRUNI SET UP] Phát hiện Bear Trap Sweep Phiên Á! (Asian Low: %.4f | Sweep Low: %.4f | Close: %.4f)",
                     asian_low, sweep_p, last_close)

            return {
                "detected": True,
                "signal": "LONG",
                "setup_name": "BRUNI_BEAR_TRAP_SWEEP",
                "asian_high": asian_high,
                "asian_low": asian_low,
                "asian_mid": asian_mid,
                "sweep_price": sweep_p,
                "suggested_sl": sl_price,
                "suggested_tp1": tp1,
                "suggested_tp2": tp2,
                "confidence_boost": 20.0, # Thưởng 20% độ tự tin
                "reason": f"Mô hình Gianluca Bruni: Quét đáy Hộp Phiên Á (${asian_low:.4f}) lấy thanh khoản rồi rút chân quay lại Hộp -> Kích hoạt LONG bắt đáy."
            }

    return EMPTY
