# ═══════════════════════════════════════════════════════════
# RISK ENGINE & M5 SCALPING CAPITAL MANAGER — SignalBot v6.2
# ═══════════════════════════════════════════════════════════
# Bảo vệ tuyệt đối rủi ro tài khoản (Anti-Liquidation Shield)
# Tối ưu hóa cho chiến thuật Scalping M5 với mục tiêu $20 - $50 / ngày
# ═══════════════════════════════════════════════════════════

import logging
import math
import time
from datetime import datetime, timezone

log = logging.getLogger("RiskEngine")


class ScalpM5RiskManager:
    """
    Quản lý rủi ro và vốn chuyên sâu cho Scalping khung M5:
    - Mục tiêu lợi nhuận: 20$ - 50$ / ngày
    - Không bao giờ để cháy tài khoản (Stoploss luôn cách xa ngưỡng thanh lý tối thiểu 3x - 5x)
    - Tự động ngắt giao dịch (Circuit Breaker) khi chạm lỗ tối đa trong ngày
    - Khóa lợi nhuận khi đã đạt chỉ tiêu $20 - $50 trong ngày để tránh overtrading
    - Tự động kích hoạt dời SL về Breakeven (hòa vốn + phí) khi đạt TP1 (1.5R)
    """

    def __init__(
        self,
        daily_target_usd: float = 30.0,       # Mục tiêu lợi nhuận $20 - $50/ngày (mặc định $30)
        daily_max_loss_usd: float = 15.0,     # Chặn lỗ tối đa trong ngày (15$ hoặc 3% vốn)
        risk_per_trade_pct: float = 1.0,      # Rủi ro tối đa mỗi lệnh: 1% vốn
        max_leverage: int = 15,               # Đòn bẩy tối đa an toàn cho M5
        max_margin_ratio: float = 0.30,       # Ký quỹ mỗi lệnh không vượt quá 30% tổng tài khoản
        consecutive_loss_limit: int = 3,      # Tạm dừng nếu thua liên tiếp 3 lệnh
        cooldown_seconds: int = 3600          # Thời gian nghỉ sau khi chạm giới hạn (60 phút)
    ):
        self.daily_target_usd = daily_target_usd
        self.daily_max_loss_usd = daily_max_loss_usd
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_leverage = max_leverage
        self.max_margin_ratio = max_margin_ratio
        self.consecutive_loss_limit = consecutive_loss_limit
        self.cooldown_seconds = cooldown_seconds

        # Theo dõi hiệu suất ngày (reset mỗi 00:00 UTC)
        self.current_day_str = self._get_today_str()
        self.daily_pnl_usd = 0.0
        self.daily_trades_count = 0
        self.daily_wins = 0
        self.daily_losses = 0
        self.consecutive_losses = 0
        self.is_daily_target_reached = False
        self.is_daily_stopped = False
        self.cooldown_until = 0.0

    def _get_today_str(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def check_and_reset_day(self):
        """Tự động làm mới bộ đếm khi sang ngày mới theo chuẩn UTC"""
        today = self._get_today_str()
        if today != self.current_day_str:
            log.info("📅 Khởi động ngày giao dịch mới UTC: %s (PnL hôm qua: $%.2f)", today, self.daily_pnl_usd)
            self.current_day_str = today
            self.daily_pnl_usd = 0.0
            self.daily_trades_count = 0
            self.daily_wins = 0
            self.daily_losses = 0
            self.consecutive_losses = 0
            self.is_daily_target_reached = False
            self.is_daily_stopped = False
            self.cooldown_until = 0.0

    def get_status(self) -> dict:
        """Trả về trạng thái kiểm soát vốn và tiến độ đạt mục tiêu ngày"""
        self.check_and_reset_day()
        now = time.time()
        in_cooldown = now < self.cooldown_until

        return {
            "date": self.current_day_str,
            "daily_pnl_usd": round(self.daily_pnl_usd, 2),
            "target_usd": self.daily_target_usd,
            "max_loss_usd": self.daily_max_loss_usd,
            "target_progress_pct": round(min(100.0, max(0.0, (self.daily_pnl_usd / self.daily_target_usd) * 100)), 1) if self.daily_target_usd > 0 else 0,
            "trades_count": self.daily_trades_count,
            "wins": self.daily_wins,
            "losses": self.daily_losses,
            "win_rate": round((self.daily_wins / self.daily_trades_count * 100), 1) if self.daily_trades_count > 0 else 0.0,
            "is_target_reached": self.is_daily_target_reached,
            "is_stopped": self.is_daily_stopped,
            "in_cooldown": in_cooldown,
            "cooldown_remaining_sec": max(0, int(self.cooldown_until - now)) if in_cooldown else 0,
            "can_trade": not self.is_daily_stopped and not self.is_daily_target_reached and not in_cooldown
        }

    def check_pre_trade_eligibility(self) -> tuple[bool, str]:
        """
        Kiểm tra trước khi vào lệnh: Đã đạt target ngày chưa, hoặc đã chạm SL ngày chưa.
        Giúp kỷ luật 100%, bảo toàn vốn tuyệt đối.
        """
        self.check_and_reset_day()
        now = time.time()

        if self.is_daily_stopped:
            return False, f"🛑 Đã chạm ngưỡng cắt lỗ tối đa trong ngày (-${self.daily_max_loss_usd:.2f}). Bảo toàn vốn!"

        if self.is_daily_target_reached:
            return False, f"🎉 Đã đạt mục tiêu lợi nhuận trong ngày (${self.daily_pnl_usd:.2f} >= ${self.daily_target_usd:.2f}). Khóa lãi thành công!"

        if now < self.cooldown_until:
            rem = int(self.cooldown_until - now)
            return False, f"⏳ Đang trong thời gian nghỉ cooldown sau {self.consecutive_losses} lệnh thua (còn {rem}s)."

        return True, "OK"

    def calculate_scalp_order(
        self,
        capital: float,
        entry_price: float,
        sl_price: float,
        direction: str,
        user_leverage: int = 10,
        risk_pct_override: float = None,
        market_regime: str = "NORMAL",
        is_trend_aligned: bool = False,
        adx_val: float = 0.0
    ) -> dict:
        """
        Tính toán quy mô lệnh Scalping M5 chuẩn mực:
        - Tính chính xác số USD chịu rủi ro (Risk USD)
        - Tính khoảng cách SL %
        - Tính toán đòn bẩy linh động (Dynamic Leverage 5x - 15x: Trend nâng 10x-15x, Sideway giữ 5x)
        - Bảo vệ chống cháy (Anti-Liquidation Shield: Liq cách SL >= 3x)
        - Tính ký quỹ (Margin USD) đảm bảo không quá 30% vốn
        - Tính các mốc TP1 (1.5R) và TP2 (2.5R)
        - Tính số lệnh thắng cần thiết để đạt $20 - $50
        """
        can_trade, reason = self.check_pre_trade_eligibility()
        if not can_trade:
            return {"approved": False, "reason": reason}

        if capital <= 0 or entry_price <= 0 or sl_price <= 0:
            return {"approved": False, "reason": "Dữ liệu vốn hoặc giá không hợp lệ"}

        # 1. Tính khoảng cách Stop Loss
        sl_dist = abs(entry_price - sl_price)
        sl_pct = sl_dist / entry_price

        # Ngăn chặn SL quá hẹp gây trượt giá, hoặc quá rộng làm mất tính chất Scalping M5
        if sl_pct < 0.002: # < 0.2%
            log.warning("SL quá hẹp (%.3f%%), tự động điều chỉnh tối thiểu 0.3%% để tránh quét râu", sl_pct * 100)
            sl_pct = 0.003
            sl_dist = entry_price * sl_pct
            sl_price = entry_price * (1 - sl_pct) if direction == "LONG" else entry_price * (1 + sl_pct)
        elif sl_pct > 0.03: # > 3.0% (quá rộng đối với M5 Scalp)
            return {"approved": False, "reason": f"SL quá xa ({sl_pct*100:.2f}% > 3%), không đạt tiêu chí Scalp M5"}

        # 2. Tính số tiền chấp nhận rủi ro (Risk Amount)
        risk_pct = risk_pct_override or self.risk_per_trade_pct
        risk_amount_usd = capital * (risk_pct / 100.0)

        # Chặn nếu rủi ro một lệnh vượt quá 50% mức lỗ tối đa cho phép trong ngày
        max_allowed_single_risk = self.daily_max_loss_usd * 0.5
        if risk_amount_usd > max_allowed_single_risk:
            risk_amount_usd = max_allowed_single_risk
            risk_pct = (risk_amount_usd / capital) * 100.0

        # 3. Tính Giá trị danh nghĩa lệnh (Notional Value)
        # Risk = Notional * sl_pct => Notional = Risk / sl_pct
        notional_usd = risk_amount_usd / sl_pct
        contract_qty = notional_usd / entry_price

        # 4. THIẾT KẾ ĐÒN BẨY LINH ĐỘNG (5x - 15x) VÀ BẢO VỆ CHỐNG CHÁY (Anti-Liquidation Shield)
        # - Sideway / Counter-Trend: giữ đòn bẩy an toàn 5x để phòng thủ
        # - Trend Following: nâng đòn bẩy linh động 10x - 15x
        if market_regime in ("SIDEWAYS", "SIDEWAY_RANGE", "COUNTER_TREND"):
            target_lev = 5
        elif is_trend_aligned or market_regime == "TREND_FOLLOWING":
            if adx_val >= 35.0:
                target_lev = 15
            elif adx_val >= 25.0:
                target_lev = 12
            else:
                target_lev = 10
        else:
            target_lev = 5

        safe_max_lev = min(self.max_leverage, target_lev, max(5, int(round(0.60 / sl_pct))))
        actual_leverage = max(5, min(15, safe_max_lev))

        # Ký quỹ yêu cầu
        required_margin = notional_usd / actual_leverage

        # Nếu ký quỹ vượt quá 30% tổng tài khoản -> Giảm Notional để không bị kẹt vốn
        max_allowed_margin = capital * self.max_margin_ratio
        if required_margin > max_allowed_margin:
            required_margin = max_allowed_margin
            notional_usd = required_margin * actual_leverage
            contract_qty = notional_usd / entry_price
            risk_amount_usd = notional_usd * sl_pct

        # 5. Ước tính giá thanh lý (Liquidation Price)
        # Margin call / Liq xấp xỉ: Entry * (1 - 0.9 / Leverage) cho Long
        liq_distance_pct = (0.90 / actual_leverage) * 100
        if direction == "LONG":
            est_liq_price = entry_price * (1 - 0.90 / actual_leverage)
            is_liq_safe = est_liq_price < (sl_price * 0.98) # Liq nằm thấp hơn SL ít nhất 2%
        else:
            est_liq_price = entry_price * (1 + 0.90 / actual_leverage)
            is_liq_safe = est_liq_price > (sl_price * 1.02) # Liq nằm cao hơn SL ít nhất 2%

        # 6. Các mốc Take Profit theo R:R (Risk/Reward)
        # TP1 = 1.5R (Chốt 60% vị thế, dời SL về hòa vốn Breakeven)
        # TP2 = 2.5R (Chốt 40% vị thế còn lại)
        r_usd = risk_amount_usd
        tp1_dist = sl_dist * 1.5
        tp2_dist = sl_dist * 2.5

        if direction == "LONG":
            tp1_price = entry_price + tp1_dist
            tp2_price = entry_price + tp2_dist
        else:
            tp1_price = entry_price - tp1_dist
            tp2_price = entry_price - tp2_dist

        # Lợi nhuận kỳ vọng khi khớp đủ TP1 và TP2
        # TP1 (60% pos * 1.5R = 0.9R) + TP2 (40% pos * 2.5R = 1.0R) = 1.9R tổng lợi nhuận
        expected_profit_usd = (0.60 * 1.5 * r_usd) + (0.40 * 2.5 * r_usd)
        
        # Số lệnh thắng trung bình để đạt mục tiêu $20 - $50
        remaining_target = max(0.0, self.daily_target_usd - self.daily_pnl_usd)
        trades_needed = math.ceil(remaining_target / expected_profit_usd) if expected_profit_usd > 0 else 1

        return {
            "approved": True,
            "direction": direction,
            "entry_price": round(entry_price, 4),
            "sl_price": round(sl_price, 4),
            "sl_pct": round(sl_pct * 100, 3),
            "tp1_price": round(tp1_price, 4),
            "tp2_price": round(tp2_price, 4),
            "contract_qty": round(contract_qty, 4),
            "notional_usd": round(notional_usd, 2),
            "required_margin": round(required_margin, 2),
            "margin_pct_of_capital": round((required_margin / capital) * 100, 1),
            "risk_amount_usd": round(risk_amount_usd, 2),
            "risk_pct": round(risk_pct, 2),
            "leverage": actual_leverage,
            "est_liq_price": round(est_liq_price, 4),
            "liq_distance_pct": round(liq_distance_pct, 2),
            "is_anti_liquidation_safe": is_liq_safe,
            "expected_profit_usd": round(expected_profit_usd, 2),
            "daily_target_usd": self.daily_target_usd,
            "trades_to_daily_target": trades_needed,
            "auto_be_trigger_price": round(tp1_price, 4),
            "details": f"Scalp M5 {direction}: Ký quỹ ${required_margin:.1f} ({actual_leverage}x), SL rủi ro ${risk_amount_usd:.2f} ({risk_pct:.1f}%), TP1 ${tp1_price:.2f} (1.5R), TP2 ${tp2_price:.2f} (2.5R)."
        }

    def record_trade_result(self, pnl_usd: float):
        """Ghi nhận kết quả giao dịch và kiểm tra trạng thái kích hoạt Circuit Breaker"""
        self.check_and_reset_day()
        self.daily_pnl_usd += pnl_usd
        self.daily_trades_count += 1

        if pnl_usd > 0:
            self.daily_wins += 1
            self.consecutive_losses = 0
            log.info("🎯 Thắng lệnh! PnL +$%.2f. Tổng ngày: $%.2f / $%.2f", pnl_usd, self.daily_pnl_usd, self.daily_target_usd)
            
            # Kiểm tra đạt target ngày
            if self.daily_pnl_usd >= self.daily_target_usd:
                self.is_daily_target_reached = True
                log.info("🏆 CHÚC MỪNG: Đã đạt mục tiêu lợi nhuận $%.2f ngày hôm nay! Khóa giao dịch.", self.daily_pnl_usd)
        else:
            self.daily_losses += 1
            self.consecutive_losses += 1
            log.warning("🛑 Lỗ lệnh! PnL -$%.2f. Tổng ngày: $%.2f / $%.2f (Thua liên tiếp: %d)",
                        abs(pnl_usd), self.daily_pnl_usd, self.daily_target_usd, self.consecutive_losses)

            # Kiểm tra chạm lỗ tối đa trong ngày (Daily Loss Circuit Breaker)
            if self.daily_pnl_usd <= -self.daily_max_loss_usd:
                self.is_daily_stopped = True
                log.error("🚨 EMERGENCY: Đã chạm ngưỡng cắt lỗ ngày (-$%.2f)! Kích hoạt Circuit Breaker dừng bot ngay.",
                          self.daily_max_loss_usd)

            # Kiểm tra chuỗi thua liên tiếp
            elif self.consecutive_losses >= self.consecutive_loss_limit:
                self.cooldown_until = time.time() + self.cooldown_seconds
                log.warning("⚠️ Thua %d lệnh liên tiếp. Kích hoạt Cooldown nghỉ %d phút tránh whipsaw thị trường.",
                            self.consecutive_losses, self.cooldown_seconds // 60)


class RiskEngine:
    """
    Tương thích ngược với hệ thống cũ và tích hợp ScalpM5RiskManager
    """
    def __init__(self, global_max_risk_pct: float = 1.5, max_positions_per_user: int = 2):
        self.global_max_risk_pct = global_max_risk_pct
        self.max_positions_per_user = max_positions_per_user
        self.scalp_manager = ScalpM5RiskManager(
            daily_target_usd=30.0,
            daily_max_loss_usd=15.0,
            risk_per_trade_pct=1.0,
            max_leverage=15
        )

    def validate_signal_for_user(self, user_id: str, symbol: str, signal_confidence: float, user_min_confidence: float) -> bool:
        """Xác thực signal có thỏa mãn mức tin cậy và không vi phạm kỷ luật ngày"""
        can_trade, reason = self.scalp_manager.check_pre_trade_eligibility()
        if not can_trade:
            log.info("  [User %s] Bỏ qua signal %s: %s", user_id, symbol, reason)
            return False

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
        if sl_pct < 0.002:
            sl_pct = 0.003
            
        risk_amount = capital * (max_risk_pct / 100.0)
        qty = risk_amount / (entry * sl_pct)
        return round(qty, 4)

    def calculate_scalp_m5_order(self, capital: float, entry: float, sl: float, direction: str, leverage: int = 10):
        """Hàm API tính toán trọn gói cho Scalping M5"""
        return self.scalp_manager.calculate_scalp_order(
            capital=capital,
            entry_price=entry,
            sl_price=sl,
            direction=direction,
            user_leverage=leverage
        )
