import math
import logging

log = logging.getLogger("QuantMath")

class QuantRiskManager:
    def __init__(self):
        # Ma trận hệ số tương quan đơn giản hóa (Mô phỏng Markowitz)
        # Giúp giảm rủi ro danh mục: Tránh nhồi lệnh vào các mã đi chung xu hướng
        self.correlation_matrix = {
            "CRYPTO": ["BTCUSDT", "ETHUSDT", "BNBUSDT", "HYPEUSDT"],
            "TECH_STOCKS": ["QQQ", "NVDA", "TSLA"],
            "BROAD_MARKET": ["SPY"],
            "SAFE_HAVEN": ["NCCOGOLD2USD-USDT"]
        }

    def _get_asset_class(self, symbol):
        for asset_class, symbols in self.correlation_matrix.items():
            if symbol in symbols:
                return asset_class
        return "UNKNOWN"

    def calculate_kalman_filter(self, closes: list, q: float = 1e-5, r: float = 0.01) -> list:
        """
        Bộ lọc Kalman 1 chiều: Khử nhiễu giá (Whipsaw) để tìm Giá Trị Thực (True Price).
        Hỗ trợ loại bỏ các râu nến ảo, giúp các mốc Fibonacci hoặc các pha test cung cầu
        (Spring/Upthrust trong Wyckoff) được xác định chính xác hơn.
        
        Tham số:
        - q (Process Noise): Độ nhiễu hệ thống. Mặc định 1e-5 giả định giá thay đổi mượt mà.
        - r (Measurement Noise): Độ nhiễu đo lường. Tăng r nếu coin giật râu quá mạnh (Crypto thường dùng 0.01 - 0.05).
        """
        if not closes:
            return []

        kalman_filter = []
        error_cov = []

        # Khởi tạo trạng thái ban đầu (Lấy giá đóng cửa nến đầu tiên)
        kalman_filter.append(closes[0])
        error_cov.append(1.0)

        for t in range(1, len(closes)):
            # 1. BƯỚC DỰ BÁO (Prediction Step)
            # Dự báo giá tiếp theo sẽ giữ nguyên như giá trước đó (vì là random walk)
            pred_estimate = kalman_filter[t-1]
            pred_error_cov = error_cov[t-1] + q

            # 2. BƯỚC CẬP NHẬT (Update Step)
            # Tính Hệ số Kalman (Kalman Gain): Quyết định nên tin vào dự báo hay tin vào giá thị trường hiện tại
            kalman_gain = pred_error_cov / (pred_error_cov + r)
            
            # Tính toán lại Giá trị thực (True Price)
            current_estimate = pred_estimate + kalman_gain * (closes[t] - pred_estimate)
            
            # Cập nhật phương sai sai số cho vòng lặp tiếp theo
            current_error_cov = (1 - kalman_gain) * pred_error_cov

            kalman_filter.append(current_estimate)
            error_cov.append(current_error_cov)

        return kalman_filter

    def calculate_ewma_volatility(self, closes: list, lambda_: float = 0.94) -> float:
        """
        GARCH-Lite (EWMA): Tính toán phương sai/độ lệch chuẩn động.
        lambda_ = 0.94 là hằng số chuẩn của JP Morgan RiskMetrics cho dữ liệu ngày/giờ.
        """
        if len(closes) < 2:
            return 0.01

        # Tính tỷ suất lợi nhuận (returns)
        returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
        
        # Khởi tạo phương sai ban đầu
        variance = sum(r**2 for r in returns) / len(returns)
        
        # Cập nhật phương sai theo trọng số hàm mũ (EWMA)
        for r in returns:
            variance = lambda_ * variance + (1 - lambda_) * (r**2)
            
        volatility = math.sqrt(variance)
        return volatility

    def get_markowitz_penalty(self, symbol: str, open_positions: list) -> float:
        """
        Lý thuyết Danh mục Markowitz: Phạt (giảm vốn) nếu danh mục đang quá tập trung rủi ro.
        Trả về hệ số nhân (vd: 1.0 = giữ nguyên vốn, 0.5 = giảm 50% vốn).
        """
        if not open_positions:
            return 1.0 # Chưa có lệnh nào, danh mục sạch -> Full vốn

        target_class = self._get_asset_class(symbol)
        penalty = 1.0

        for pos in open_positions:
            pos_symbol = pos.get("symbol", "")
            pos_class = self._get_asset_class(pos_symbol)
            
            # Nếu đang có mã cùng nhóm tài sản (Ví dụ đang có BTC, định mua thêm ETH)
            if pos_class == target_class and pos_symbol != symbol:
                penalty *= 0.6  # Cắt 40% lượng vốn được phép đánh để phân tán rủi ro
                log.info(f"Markowitz: Giảm vốn {symbol} do đang giữ mã tương quan {pos_symbol}.")

        return penalty

    def calculate_kelly_fraction(self, p_win: float, reward_risk_ratio: float, fraction: float = 0.5) -> float:
        """
        Tiêu chuẩn Kelly: Tính % vốn tối ưu dựa trên xác suất thắng và tỷ lệ R:R.
        fraction = 0.5 (Half-Kelly) để kìm hãm sự hung hăng của công thức gốc.
        """
        if reward_risk_ratio <= 0:
            return 0.0
            
        # Công thức Kelly: K = p - (1-p)/b
        kelly_pct = p_win - ((1.0 - p_win) / reward_risk_ratio)
        
        if kelly_pct <= 0:
            return 0.0 # Bắt buộc không vào lệnh nếu Kelly âm (Kỳ vọng lỗ)
            
        # Áp dụng Fractional Kelly và giới hạn trần (Max 20% vốn)
        safe_kelly = max(0.01, min(0.20, kelly_pct * fraction))
        return safe_kelly

    def calculate_anti_liquidation_buffer(self, entry_price: float, sl_price: float, leverage: int, direction: str) -> dict:
        """
        Tính toán khoảng đệm thanh lý (Anti-Liquidation Buffer):
        Đảm bảo giá thanh lý của sàn luôn nằm ngoài điểm cắt lỗ (Stop Loss) ít nhất 3x.
        """
        if entry_price <= 0 or leverage <= 0:
            return {"safe": False, "liq_price": 0, "liq_dist_pct": 0, "sl_dist_pct": 0}

        sl_dist_pct = abs(entry_price - sl_price) / entry_price * 100.0
        liq_dist_pct = (0.90 / leverage) * 100.0

        if direction == "LONG":
            liq_price = entry_price * (1.0 - 0.90 / leverage)
            is_safe = (liq_price < sl_price) and (liq_dist_pct >= sl_dist_pct * 2.5)
        else:
            liq_price = entry_price * (1.0 + 0.90 / leverage)
            is_safe = (liq_price > sl_price) and (liq_dist_pct >= sl_dist_pct * 2.5)

        return {
            "safe": is_safe,
            "liq_price": round(liq_price, 4),
            "liq_dist_pct": round(liq_dist_pct, 2),
            "sl_dist_pct": round(sl_dist_pct, 2),
            "safety_multiple": round(liq_dist_pct / sl_dist_pct, 1) if sl_dist_pct > 0 else 99.0
        }

    def calculate_m5_scalp_sizing(
        self,
        capital: float,
        entry_price: float,
        sl_price: float,
        direction: str,
        risk_pct: float = 1.0,
        leverage: int = 10,
        target_daily_usd: float = 30.0
    ) -> dict:
        """
        Tính toán quy mô lệnh cho chiến lược Scalping M5 với mục tiêu $20 - $50/ngày.
        """
        if capital <= 0 or entry_price <= 0 or sl_price <= 0:
            return {"error": "Invalid inputs"}

        sl_pct = abs(entry_price - sl_price) / entry_price
        sl_pct = max(0.003, min(0.03, sl_pct)) # Kẹp trong khoảng 0.3% - 3.0%

        risk_usd = capital * (risk_pct / 100.0)
        notional_usd = risk_usd / sl_pct

        # Khống chế đòn bẩy để bảo vệ tài khoản
        max_safe_lev = min(leverage, max(3, int(0.60 / sl_pct)))
        margin_usd = notional_usd / max_safe_lev

        # Nếu ký quỹ vượt 25% vốn, điều chỉnh giảm
        if margin_usd > capital * 0.25:
            margin_usd = capital * 0.25
            notional_usd = margin_usd * max_safe_lev
            risk_usd = notional_usd * sl_pct

        contract_qty = notional_usd / entry_price
        buffer_info = self.calculate_anti_liquidation_buffer(entry_price, sl_price, max_safe_lev, direction)

        # TP1 (1.5R - chốt 60%) và TP2 (2.5R - chốt 40%)
        dist = abs(entry_price - sl_price)
        tp1 = entry_price + (dist * 1.5 if direction == "LONG" else -dist * 1.5)
        tp2 = entry_price + (dist * 2.5 if direction == "LONG" else -dist * 2.5)

        avg_profit_per_win = (0.60 * 1.5 * risk_usd) + (0.40 * 2.5 * risk_usd) # 1.9R
        wins_needed_for_target = math.ceil(target_daily_usd / avg_profit_per_win) if avg_profit_per_win > 0 else 1

        return {
            "direction": direction,
            "capital": capital,
            "entry_price": round(entry_price, 4),
            "sl_price": round(sl_price, 4),
            "tp1_price": round(tp1, 4),
            "tp2_price": round(tp2, 4),
            "contract_qty": round(contract_qty, 4),
            "margin_usd": round(margin_usd, 2),
            "notional_usd": round(notional_usd, 2),
            "risk_usd": round(risk_usd, 2),
            "leverage": max_safe_lev,
            "liq_price": buffer_info["liq_price"],
            "safety_multiple": buffer_info["safety_multiple"],
            "anti_liquidation_guarantee": buffer_info["safe"],
            "avg_profit_win_usd": round(avg_profit_per_win, 2),
            "target_daily_usd": target_daily_usd,
            "wins_needed": wins_needed_for_target
        }

