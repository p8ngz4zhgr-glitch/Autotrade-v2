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
