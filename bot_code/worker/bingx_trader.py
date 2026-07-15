import hmac
import hashlib
import time
import requests
import urllib.parse
import logging
import json
import os

# Thử import QuantRiskManager
try:
    from .quant_math import QuantRiskManager
except ImportError:
    try:
        from quant_math import QuantRiskManager
    except ImportError:
        QuantRiskManager = None

log = logging.getLogger("BingXExchange")

class BingXExchange:
    BASE_URL = "https://open-api.bingx.com"

    def __init__(self, api_key: str, api_secret: str, state_file="bot_state.json"):
        self.api_key    = str(api_key).strip() if api_key else ""
        self.api_secret = str(api_secret).strip() if api_secret else ""
        self.state_file = state_file
        
        # Load trạng thái từ file cứng để chống mất dữ liệu khi restart
        self._position_stage = self._load_state()

    def _load_state(self) -> dict:
        """Đọc trạng thái bot từ file cứng."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                log.error("Lỗi đọc file state: %s", e)
        return {}

    def _save_state(self):
        """Lưu trạng thái bot ra file cứng."""
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self._position_stage, f, indent=4)
        except Exception as e:
            log.error("Lỗi ghi file state: %s", e)

    def _sign(self, params: dict) -> str:
        query_string = urllib.parse.urlencode(sorted(params.items()))
        return hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _request(self, method: str, path: str, params: dict = None) -> dict:
        if params is None:
            params = {}
            
        for k, v in list(params.items()):
            if isinstance(v, bool):
                params[k] = "true" if v else "false"
            
        if "symbol" in params and params["symbol"]:
            sym = str(params["symbol"]).strip().upper()
            
            if "NCCOGOLD2USD" in sym:
                params["symbol"] = "NCCOGOLD2USD-USDT"
            elif sym.endswith("USDT"):
                if "-" not in sym:
                    params["symbol"] = sym[:-4] + "-USDT"
                else:
                    params["symbol"] = sym
            else:
                params["symbol"] = sym

        if not self.api_key or not self.api_secret:
            return {"code": -1, "msg": "API key or secret is empty", "data": {}}
            
        api_key_lower = self.api_key.lower()
        api_secret_lower = self.api_secret.lower()
        if (api_key_lower.startswith("mock") or 
            api_secret_lower.startswith("mock") or 
            "your_" in api_key_lower or 
            "your_" in api_secret_lower):
            return {"code": -1, "msg": "Mock API key/secret detected", "data": {}}

        params["timestamp"] = int(time.time() * 1000)
        
        sorted_items = sorted(params.items())
        query_string = urllib.parse.urlencode(sorted_items)
        signature = self._sign(params)
        full_url = f"{self.BASE_URL}{path}?{query_string}&signature={signature}"

        headers = {
            "X-BX-APIKEY": self.api_key,
        }

        try:
            if method.upper() == "GET":
                r = requests.get(full_url, headers=headers, timeout=10)
            elif method.upper() == "DELETE":
                r = requests.delete(full_url, headers=headers, timeout=10)
            else:
                r = requests.post(full_url, headers=headers, timeout=10)
            
            r.raise_for_status()
            res = r.json()
            if not isinstance(res, dict):
                return {"code": -1, "msg": str(res), "data": {}}
            return res
        except Exception as e:
            log.error("BingX request error %s %s: %s", method, path, e)
            return {"code": -1, "msg": str(e), "data": {}}

    def get_balance(self) -> float:
        res = self._request("GET", "/openApi/swap/v2/user/balance")
        if isinstance(res, dict) and res.get("code") == 0:
            data = res.get("data")
            if isinstance(data, dict):
                balances = data.get("balance")
                if isinstance(balances, dict):
                    if balances.get("asset") == "USDT":
                        return float(balances.get("balance", 0))
                elif isinstance(balances, list):
                    for item in balances:
                        if isinstance(item, dict) and item.get("asset") == "USDT":
                            return float(item.get("balance", 0))
        return 0.0

    def get_latest_price(self, symbol: str) -> float:
        res = self._request("GET", "/openApi/swap/v1/ticker/price", {"symbol": symbol})
        if isinstance(res, dict) and res.get("code") == 0:
            data = res.get("data")
            if isinstance(data, dict):
                return float(data.get("price", 0))
        return 0.0

    def set_leverage(self, symbol: str, leverage: int, side: str = "ALL") -> dict:
        res = self._request("POST", "/openApi/swap/v2/trade/leverage", {
            "symbol": symbol,
            "leverage": leverage,
            "side": side
        })
        if isinstance(res, dict) and res.get("code") == 109400:
            res = self._request("POST", "/openApi/swap/v2/trade/leverage", {
                "symbol": symbol,
                "leverage": leverage,
                "side": "BOTH"
            })
        return res

    def get_open_positions(self, symbol: str = None) -> list:
        params = {}
        if symbol:
            params["symbol"] = symbol
        res = self._request("GET", "/openApi/swap/v2/user/positions", params)
        positions = []
        if isinstance(res, dict) and res.get("code") == 0:
            data = res.get("data")
            if isinstance(data, list):
                for p in data:
                    if isinstance(p, dict):
                        qty = float(p.get("positionAmt", 0))
                        if qty == 0:
                            continue
                        
                        sym = p.get("symbol", "")
                        normalized_sym = sym.replace("-", "") if sym else ""
                        
                        pos_side = p.get("positionSide")
                        if pos_side in ("LONG", "SHORT"):
                            direction = pos_side
                        else:
                            direction = "LONG" if qty > 0 else "SHORT"
                            
                        entry_val = p.get("avgPrice", p.get("entryPrice", 0))
                        
                        positions.append({
                            "symbol": normalized_sym,
                            "direction": direction,
                            "entry": float(entry_val),
                            "qty": abs(qty),
                            "pnl": float(p.get("unrealizedProfit", 0)),
                        })
        return positions

    def get_trigger_orders(self) -> dict:
        res = self._request("GET", "/openApi/swap/v2/trade/openOrders")
        triggers = {}
        if isinstance(res, dict) and res.get("code") == 0:
            data = res.get("data")
            if isinstance(data, list):
                order_list = data
            elif isinstance(data, dict):
                order_list = data.get("orders") or data.get("order") or []
            else:
                order_list = []

            for o in order_list:
                if isinstance(o, dict):
                    sym = o.get("symbol")
                    normalized_sym = sym.replace("-", "") if sym else ""
                    if normalized_sym not in triggers:
                        triggers[normalized_sym] = {}
                    otype = o.get("type", "")
                    if "STOP_MARKET" in otype or "STOP" in otype:
                        triggers[normalized_sym]["sl"] = float(o.get("stopPrice", 0))
                    elif "TAKE_PROFIT" in otype or "LIMIT" in otype:
                        triggers[normalized_sym]["tp2"] = float(o.get("price", 0))

            if not order_list:
                log.debug("get_trigger_orders: không thấy order nào trong data=%r", data)
        else:
            log.warning("get_trigger_orders: API lỗi hoặc code != 0: %r", res)
        return triggers

    def _safe_order(self, params: dict) -> dict:
        for k, v in list(params.items()):
            if isinstance(v, float):
                formatted_v = format(v, '.8f').rstrip('0').rstrip('.')
                params[k] = formatted_v if formatted_v else "0"
            elif isinstance(v, int) and not isinstance(v, bool):
                params[k] = str(v)

        res = self._request("POST", "/openApi/swap/v2/trade/order", params)
        
        if res.get("code") == 109400 and "positionSide" in params: 
            params["positionSide"] = "BOTH"
            order_type = params.get("type", "").upper()
            if order_type in ["STOP_MARKET", "TAKE_PROFIT_MARKET", "STOP", "TAKE_PROFIT"]:
                params["reduceOnly"] = "true"
            res = self._request("POST", "/openApi/swap/v2/trade/order", params)
            
        return res

    def place_order(self, symbol: str, side: str, qty: float, sl_price: float, tp_price: float, leverage: int = 5, p_win: float = 0.5, rr_ratio: float = 1.5) -> dict:
        open_positions = self.get_open_positions(symbol)
        if len(open_positions) > 0:
            current_direction = open_positions[0].get("direction")
            current_qty = open_positions[0].get("qty")
            log.info("⛔ BỎ QUA: Đã có sẵn vị thế %s cho mã %s (qty=%s).", current_direction, symbol, current_qty)
            return {"ok": False, "msg": "Position already exists"}

        try:
            self.set_leverage(symbol, leverage, side="ALL")
        except Exception as e:
            log.warning(f"Lỗi khi set đòn bẩy cho {symbol}: {e}")

        available_balance = self.get_balance()
        current_price = self.get_latest_price(symbol)
        
        if available_balance <= 0 or current_price <= 0:
            log.error(f"⛔ Lỗi số dư hoặc giá cho {symbol}.")
            return {"ok": False, "msg": "Invalid balance or price"}

        risk_percent = 0.1 
        
        if QuantRiskManager:
            try:
                quant = QuantRiskManager()
                all_open_pos = self.get_open_positions() 
                markowitz_multiplier = quant.get_markowitz_penalty(symbol, all_open_pos)
                kelly_percent = quant.calculate_kelly_fraction(p_win, rr_ratio, fraction=0.5)
                risk_percent = min(0.1, kelly_percent * markowitz_multiplier)
                log.info(f"🧠 [QUANT] Markowitz={markowitz_multiplier:.2f}, Kelly={kelly_percent:.3f} -> Risk={risk_percent:.3f}")
            except Exception as e:
                log.warning(f"Lỗi module Quant, dùng Risk mặc định 10%. Lỗi: {e}")
        
        capital_to_use = available_balance * risk_percent
        
        if (capital_to_use * leverage) < 5.0:
            capital_to_use = 5.0 / leverage
            log.info(f"⚠️ Vốn tính toán nhỏ hơn mức tối thiểu, điều chỉnh vốn về {capital_to_use:.2f} để đáp ứng lệnh sàn.")

        calculated_qty = (capital_to_use * leverage) / current_price
        safe_qty = float(int(calculated_qty * 10000) / 10000)

        MIN_QTY_MAP = {
            "BTC": 0.001,
            "ETH": 0.01,
            "BNB": 0.1,
            "SOL": 0.1,
            "XRP": 10.0,
            "DOGE": 100.0
        }
        
        base_asset = symbol.replace("USDT", "").replace("-", "").upper()
        min_qty = MIN_QTY_MAP.get(base_asset, 0.0001) 
        
        if safe_qty < min_qty:
            safe_qty = min_qty
            capital_to_use = (safe_qty * current_price) / leverage
            log.info(f"⚠️ Qty tính toán nhỏ hơn quy định của sàn đối với {base_asset}. Tự động nâng Qty lên {min_qty}")
        
        log.info(f"💰 Balance: {available_balance:.2f} USDT | Dùng {capital_to_use:.2f} USDT (Lev {leverage}x) -> Qty: {safe_qty}")

        position_side = "LONG" if side == "BUY" else "SHORT"
        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": safe_qty,
            "positionSide": position_side
        }
        
        res = self._safe_order(params)
        
        if res.get("code") == 0:
            order_id = res.get("data", {}).get("orderId")
            log.info("✅ Placed Market Order %s OK: %s (Qty: %s)", order_id, side, safe_qty)
            self._place_sl_tp(symbol, side, safe_qty, sl_price, tp_price)
            
            # Reset trạng thái nhiều chặng cho lệnh mới này và lưu ra file
            self._position_stage[symbol] = {
                "leg": 1, 
                "mid_done": False, 
                "original_qty": safe_qty, 
                "peak_roe": 0.0,
                "sweep_sl_tightened": False,
                "last_sl_price": sl_price,
                "sweep_sl_updated_at": 0
            }
            self._save_state()
            
            return {"ok": True, "order_id": order_id}
            
        return {"ok": False, "msg": res.get("msg", "Error placing order")}

    def _place_sl_tp(self, symbol: str, side: str, qty: float, sl_price: float, tp_price: float):
        opposite_side = "SELL" if side == "BUY" else "BUY"
        position_side = "LONG" if side == "BUY" else "SHORT"
        
        if sl_price > 0:
            self._safe_order({
                "symbol": symbol,
                "side": opposite_side,
                "type": "STOP_MARKET",
                "stopPrice": sl_price,
                "quantity": qty,
                "positionSide": position_side,
                "workingType": "MARK_PRICE"  
            })
            
        if tp_price > 0:
            self._safe_order({
                "symbol": symbol,
                "side": opposite_side,
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": tp_price,
                "quantity": qty,
                "positionSide": position_side,
                "workingType": "CONTRACT_PRICE" 
            })

    def cancel_all_orders(self, symbol: str) -> dict:
        return self._request("DELETE", "/openApi/swap/v2/trade/allOpenOrders", {
            "symbol": symbol
        })

    def close_position(self, symbol: str, qty: float, direction: str) -> dict:
        opposite_side = "SELL" if direction == "LONG" else "BUY"
        params = {
            "symbol": symbol,
            "side": opposite_side,
            "type": "MARKET",
            "quantity": qty,
            "positionSide": direction
        }
        res = self._safe_order(params)
        if res.get("code") in (0, 101205):
            return {"ok": True}
        return {"ok": False, "msg": res.get("msg", "Error closing")}

    def handle_tp1_hit(self, symbol: str, direction: str, total_qty: float, entry_price: float, tp2_price: float) -> dict:
        log.info(f"Đang xóa lệnh SL/TP cũ của {symbol}...")
        self.cancel_all_orders(symbol)
        
        half_qty = float(int((total_qty * 0.5) * 10000) / 10000)
        close_res = self.close_position(symbol, half_qty, direction)
        if not close_res.get("ok"):
            log.error(f"Lỗi khi chốt 50% vị thế: {close_res.get('msg')}")
            return close_res

        self.cancel_all_orders(symbol)
        remaining_qty = float(int((total_qty - half_qty) * 10000) / 10000)
        
        if entry_price <= 0:
            open_pos = self.get_open_positions(symbol)
            if open_pos:
                entry_price = open_pos[0].get("entry", 0)
        
        self._place_sl_tp(
            symbol=symbol,
            side="BUY" if direction == "LONG" else "SELL", 
            qty=remaining_qty,
            sl_price=entry_price,
            tp_price=tp2_price
        )
        return {"ok": True}

    def manage_position_dynamic(self, symbol: str, analysis_result: dict, leverage: int = 5) -> dict:
        open_positions = self.get_open_positions(symbol)
        if not open_positions:
            self._position_stage.pop(symbol, None)
            self._save_state()
            return {"action": "NONE", "msg": "Không có vị thế mở."}
            
        pos = open_positions[0]
        direction = pos.get("direction")
        entry_price = float(pos.get("entry", 0))
        current_qty = float(pos.get("qty", 0))
        current_price = self.get_latest_price(symbol)
        
        if entry_price <= 0 or current_price <= 0:
            return {"action": "NONE", "msg": "Giá bị lỗi."}

        plan = analysis_result.get("plan", {})
        tp1_price = float(plan.get("tp1", 0))
        tp2_price = float(plan.get("tp2", 0))
        
        if direction == "LONG":
            roe = ((current_price - entry_price) / entry_price) * 100 * leverage
        else:
            roe = ((entry_price - current_price) / entry_price) * 100 * leverage

        new_signal = analysis_result.get("final", "WAIT")
        is_trending = analysis_result.get("timeframes", {}).get("1h", {}).get("is_trending", True)

        # BƯỚC 1: XỬ LÝ ĐẢO CHIỀU (REVERSAL)
        if (direction == "LONG" and new_signal == "SHORT") or \
           (direction == "SHORT" and new_signal == "LONG"):
            log.warning(f"🚨 Xu hướng đảo chiều ({direction} -> {new_signal}). Đóng toàn bộ lệnh cũ!")
            self.close_position(symbol, current_qty, direction)
            self.cancel_all_orders(symbol)
            self._position_stage.pop(symbol, None)
            self._save_state()
            return {
                "action": "REVERSE", 
                "msg": f"Đã đóng vị thế {direction}.",
                "new_direction": new_signal
            }

        stage = self._position_stage.get(symbol)
        if stage is None:
            stage = {
                "leg": 1, 
                "mid_done": False, 
                "original_qty": current_qty, 
                "peak_roe": roe,
                "sweep_sl_tightened": False, 
                "last_sl_price": 0,
                "sweep_sl_updated_at": 0
            }
            self._position_stage[symbol] = stage
            self._save_state()
        else:
            stage["peak_roe"] = max(stage.get("peak_roe", roe), roe)
            self._position_stage[symbol] = stage
            self._save_state()

        # BƯỚC 2: BẢO VỆ TRƯỚC LIQUIDITY SWEEP
        sweep_info = analysis_result.get("liquidity_sweep", {}) or {}
        ms_1h_info = analysis_result.get("market_structure_1h", {}) or {}
        swing_high = float(ms_1h_info.get("last_swing_high", 0) or 0)
        swing_low  = float(ms_1h_info.get("last_swing_low", 0) or 0)

        sweep_type = sweep_info.get("type") if sweep_info.get("detected") else None
        sweep_against_position = (
            (direction == "SHORT" and sweep_type == "BULLISH_SWEEP") or
            (direction == "LONG" and sweep_type == "BEARISH_SWEEP")
        )

        if sweep_against_position:
            log.warning(f"🚨 {symbol} bị quét thanh khoản ngược hướng lệnh ({sweep_type}). Đóng sớm để bảo vệ vốn!")
            self.close_position(symbol, current_qty, direction)
            self.cancel_all_orders(symbol)
            self._position_stage.pop(symbol, None)
            self._save_state()
            return {"action": "CLOSE", "type": "LIQUIDITY_SWEEP", "sweep_type": sweep_type, "roe": roe}

        # [ĐÃ SỬA] Nâng vùng nhận diện lên 1.5% để tránh nhiễu
        SWEEP_PROXIMITY_PCT = 1.5      
        MIN_SL_IMPROVEMENT_PCT = 0.1  
        SWEEP_SL_COOLDOWN_SEC = 180   

        near_danger_zone = False
        if direction == "LONG" and swing_high > 0 and current_price <= swing_high:
            near_danger_zone = (swing_high - current_price) / current_price * 100 <= SWEEP_PROXIMITY_PCT
        elif direction == "SHORT" and swing_low > 0 and current_price >= swing_low:
            near_danger_zone = (current_price - swing_low) / current_price * 100 <= SWEEP_PROXIMITY_PCT

        cooldown_ok = (time.time() - stage.get("sweep_sl_updated_at", 0)) >= SWEEP_SL_COOLDOWN_SEC

                if near_danger_zone and cooldown_ok:
            # 1. Xác định điều kiện an toàn để dời SL về Entry (Phải đang lãi ít nhất 0.2%)
            is_safe_to_move = False
            if direction == "LONG" and current_price > (entry_price * 1.002):
                is_safe_to_move = True
            elif direction == "SHORT" and current_price < (entry_price * 0.998):
                is_safe_to_move = True

            # 2. Xử lý dựa trên trạng thái an toàn
            current_live_sl = stage.get("last_sl_price", 0)
            if current_live_sl == 0:
                current_live_sl = float(self.get_trigger_orders().get(symbol, {}).get("sl", 0) or 0)

            needs_update = False
            tightened_sl = current_live_sl

            if is_safe_to_move:
                tightened_sl = entry_price
                if current_live_sl <= 0:
                    needs_update = True  
                elif direction == "LONG":
                    needs_update = (tightened_sl - current_live_sl) / current_price * 100 > MIN_SL_IMPROVEMENT_PCT
                else:
                    needs_update = (current_live_sl - tightened_sl) / current_price * 100 > MIN_SL_IMPROVEMENT_PCT
            else:
                log.info(f"⚠️ {symbol} gần vùng quét nhưng đang lỗ/chưa đủ lãi (Giá: {current_price} | Entry: {entry_price}). Giữ nguyên SL gốc bảo vệ.")

            # 3. Gửi lệnh cập nhật nếu hợp lệ
            if needs_update:
                log.info(f"🛡️ {symbol} đang tiến gần đỉnh/đáy thanh khoản. Dời SL an toàn về Entry: {current_live_sl} -> {tightened_sl}.")
                self.cancel_all_orders(symbol)
                target_tp = tp2_price if stage["leg"] == 2 else tp1_price
                
                self._place_sl_tp(symbol, "BUY" if direction == "LONG" else "SELL", current_qty, tightened_sl, target_tp)
                
                stage["sweep_sl_tightened"] = True
                stage["sweep_sl_updated_at"] = time.time()
                stage["last_sl_price"] = tightened_sl 
                self._position_stage[symbol] = stage
                self._save_state() 
                
                return {
                    "action": "UPDATE_SL_TP",
                    "type": "SWEEP_PROXIMITY",
                    "msg": f"Gần vùng quét thanh khoản, dời SL về Entry {tightened_sl}"
                }


        # BƯỚC 3: CHỐT LỜI SỚM BẢO VỆ LỢI NHUẬN
        PROFIT_ARM_ROE = 6.0
        PROFIT_TRAIL_GIVEBACK = 4.0
        peak_roe = stage.get("peak_roe", roe)
        if peak_roe >= PROFIT_ARM_ROE and roe <= (peak_roe - PROFIT_TRAIL_GIVEBACK):
            log.info(f"🔒 {symbol} chốt lời sớm: đỉnh ROE={peak_roe:.2f}%, hiện tại={roe:.2f}% (hồi lại > {PROFIT_TRAIL_GIVEBACK}%).")
            self.close_position(symbol, current_qty, direction)
            self.cancel_all_orders(symbol)
            self._position_stage.pop(symbol, None)
            self._save_state()
            return {"action": "CLOSE", "type": "PROFIT_LOCK", "roe": roe, "peak_roe": peak_roe}

        # BƯỚC 4: SL SỚM BẢO VỆ VỐN
        EARLY_SL_ROE_THRESHOLD = -5.0
        if stage["leg"] == 1 and not stage.get("mid_done"):
            if roe <= EARLY_SL_ROE_THRESHOLD and not is_trending:
                log.info(f"🛑 {symbol} cắt lỗ sớm bảo vệ vốn: ROE={roe:.2f}% <= {EARLY_SL_ROE_THRESHOLD}% và xu hướng yếu.")
                self.close_position(symbol, current_qty, direction)
                self.cancel_all_orders(symbol)
                self._position_stage.pop(symbol, None)
                self._save_state()
                return {"action": "CLOSE", "type": "EARLY_SL", "roe": roe}

        # BƯỚC 5: XỬ LÝ LỌC NHIỄU KHÔNG RÕ XU HƯỚNG (WAIT REGIME)
        if new_signal == "WAIT":
            SL_THRESHOLD = 12.0
            TP_THRESHOLD = 8.0
            if roe <= -SL_THRESHOLD or roe >= TP_THRESHOLD:
                if not is_trending:
                    log.info(f"💰 Chốt lời/Cắt lỗ sớm {roe:.2f}% do thị trường đi ngang (WAIT).")
                    self.close_position(symbol, current_qty, direction)
                    self.cancel_all_orders(symbol)
                    self._position_stage.pop(symbol, None)
                    self._save_state()
                    return {"action": "CLOSE", "type": "WAIT_REGIME", "roe": roe}

        # BƯỚC 6: GỒNG LÃI NHIỀU CHẶNG
        original_side = "BUY" if direction == "LONG" else "SELL"
        trend_continues = (direction == "LONG" and new_signal == "LONG") or \
                          (direction == "SHORT" and new_signal == "SHORT")

        MID_CLOSE_RATIO = 0.3
        TP1_CLOSE_RATIO = 0.5

        # ================= CHẶNG 1: ENTRY -> TP1 =================
        if stage["leg"] == 1:
            if tp1_price <= 0:
                return {"action": "HOLD", "msg": "Chưa có TP1 để tính toán."}

            if direction == "LONG":
                tp1_hit = current_price >= tp1_price
            else:
                tp1_hit = current_price <= tp1_price

            # [ĐÃ SỬA] Xóa bỏ hoàn toàn khối lệnh dời SL về Entry khi giá mới chạy được 50%
            # Cho phép giá điều chỉnh (pullback) thoải mái miễn là chưa chạm SL gốc.

            # -- Đã chạm TP1 --
            if tp1_hit:
                close_qty = float(int((current_qty * TP1_CLOSE_RATIO) * 10000) / 10000)
                close_qty = min(close_qty, current_qty)
                self.cancel_all_orders(symbol)
                if close_qty > 0:
                    self.close_position(symbol, close_qty, direction)
                remaining_qty = float(int((current_qty - close_qty) * 10000) / 10000)

                if trend_continues and tp2_price > 0 and remaining_qty > 0:
                    # Chạm TP1 chốt lời xong, bây giờ mới dời SL của phần gồng lãi về Entry (tp1_price)
                    self._place_sl_tp(symbol, original_side, remaining_qty, tp1_price, tp2_price)
                    
                    stage["leg"] = 2
                    stage["mid_done"] = False
                    stage["sweep_sl_tightened"] = False
                    stage["last_sl_price"] = tp1_price
                    self._position_stage[symbol] = stage
                    self._save_state()
                    
                    log.info(f"📈 {symbol} chạm TP1. Chốt {close_qty}, gồng {remaining_qty} tới TP2={tp2_price}.")
                    return {
                        "action": "SCALE_OUT",
                        "leg": 1,
                        "msg": f"TP1 đạt, chốt {close_qty}, gồng {remaining_qty} sang TP2={tp2_price}",
                        "next_leg": 2
                    }
                else:
                    if remaining_qty > 0:
                        self.close_position(symbol, remaining_qty, direction)
                    self.cancel_all_orders(symbol)
                    self._position_stage.pop(symbol, None)
                    self._save_state()
                    
                    log.info(f"💰 {symbol} chạm TP1, xu hướng yếu -> chốt toàn bộ.")
                    return {"action": "CLOSE", "type": "TP1_FINAL", "msg": "Chạm TP1, xu hướng yếu, đóng toàn bộ."}

            return {"action": "HOLD", "msg": "Đang giữ SL gốc, chờ chạm TP1."}

        # ================= CHẶNG 2: TP1 -> TP2 =================
        else:
            if tp2_price <= 0:
                return {"action": "HOLD", "msg": "Chưa có TP2 để tính toán."}

            if direction == "LONG":
                dist_to_tp2 = tp2_price - tp1_price if tp2_price > tp1_price else 0
                curr_dist2 = current_price - tp1_price
                tp2_hit = current_price >= tp2_price
            else:
                dist_to_tp2 = tp1_price - tp2_price if tp1_price > tp2_price else 0
                curr_dist2 = tp1_price - current_price
                tp2_hit = current_price <= tp2_price

            progress2 = (curr_dist2 / dist_to_tp2) if dist_to_tp2 > 0 else 0

            # -- Mốc 50% quãng đường tới TP2 --
            # (Đoạn này giữ nguyên vì ở Chặng 2 lệnh đã an toàn và đang khóa lãi)
            if not stage.get("mid_done"):
                if progress2 >= 0.5:
                    partial_qty = float(int((current_qty * MID_CLOSE_RATIO) * 10000) / 10000)
                    partial_qty = min(partial_qty, current_qty)
                    if partial_qty > 0:
                        self.close_position(symbol, partial_qty, direction)
                    remaining_qty = float(int((current_qty - partial_qty) * 10000) / 10000)
                    self.cancel_all_orders(symbol)
                    self._place_sl_tp(symbol, original_side, remaining_qty, tp1_price, tp2_price)
                    
                    stage["mid_done"] = True
                    stage["last_sl_price"] = tp1_price
                    self._position_stage[symbol] = stage
                    self._save_state()
                    
                    log.info(f"🛡️ {symbol} đạt 50% quãng đường tới TP2. Chốt {partial_qty}, dời SL lên TP1={tp1_price}, chờ TP2={tp2_price}.")
                    return {
                        "action": "SCALE_OUT",
                        "leg": 2,
                        "msg": f"Chốt {partial_qty} tại 50% TP2 | SL={tp1_price} | chờ TP2={tp2_price}"
                    }
                return {"action": "HOLD", "msg": "Đang chờ 50% quãng đường tới TP2."}

            # -- Chạm TP2 --
            if tp2_hit:
                self.cancel_all_orders(symbol)
                if current_qty > 0:
                    self.close_position(symbol, current_qty, direction)
                self._position_stage.pop(symbol, None)
                self._save_state()
                
                log.info(f"🎯 {symbol} đạt TP2. Chốt toàn bộ lệnh.")
                return {"action": "CLOSE", "type": "TP2_FINAL", "msg": "Đạt TP2, chốt toàn bộ lệnh."}

            return {"action": "HOLD", "msg": "Đã dời SL lên TP1, đang chờ chạm TP2."}

