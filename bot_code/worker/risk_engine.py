import logging

log = logging.getLogger("RiskEngine")


class RiskEngine:
    def __init__(self, global_max_risk_pct: float = 2.0, max_positions_per_user: int = 3):
        self.global_max_risk_pct = global_max_risk_pct
        self.max_positions_per_user = max_positions_per_user

    def validate_signal_for_user(self, user_id: str, symbol: str, signal_confidence: float, user_min_confidence: float) -> bool:
        """Xác thực signal có thỏa mãn mức rủi ro tối thiểu của user hay không"""
        if signal_confidence < user_min_confidence:
            log.info("  [User %s] Signal %s bị từ chối do Confidence %.1f%% < Min %.1f%%",
                     user_id, symbol, signal_confidence, user_min_confidence)
            return False
        return True

    def calculate_position_size(self, capital: float, max_risk_pct: float, entry: float, sl: float) -> float:
        """Tính khối lượng lệnh dựa trên Equity, Risk % và khoảng cách Stoploss"""
        if entry <= 0 or sl <= 0 or entry == sl:
            return 0.0
        
        sl_pct = abs(entry - sl) / entry
        if sl_pct < 0.001:  # Tránh SL quá hẹp gây đòn bẩy ảo cực lớn
            log.warning("Stoploss quá hẹp (%.4f%%). Tránh rủi ro trượt giá, tính size theo SL 0.5%%", sl_pct * 100)
            sl_pct = 0.005
            
        risk_amount = capital * (max_risk_pct / 100)
        qty = risk_amount / (entry * sl_pct)
        return round(qty, 4)
