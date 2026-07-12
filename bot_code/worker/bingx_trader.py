import hmac
import hashlib
import time
import requests
import urllib.parse
import logging

# Thử import QuantRiskManager, nếu bạn để cùng thư mục hoặc thư mục cha
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

    def __init__(self, api_key: str, api_secret: str):
        self.api_key    = str(api_key).strip() if api_key else ""
        self.api_secret = str(api_secret).strip() if api_secret else ""

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
                for o in data:
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

    # ════════════════════════════════════════════════════════════════════
    # CƠ CHẾ LỌC NHIỄU, KÉO SL HÒA VỐN SỚM (EARLY BREAKEVEN) VÀ GỒNG LÃI
    # ════════════════════════════════════════════════════════════════════
    def manage_position_dynamic(self, symbol: str, analysis_result: dict, leverage: int = 5) -> dict:
        open_positions = self.get_open_positions(symbol)
        if not open_positions:
            return {"action": "NONE", "msg": "Không có vị thế mở."}
            
        pos = open_positions[0]
        direction = pos.get("direction")
        entry_price = float(pos.get("entry", 0))
        current_qty = float(pos.get("qty", 0))
        current_price = self.get_latest_price(symbol)
        
        active_orders = self.get_trigger_orders().get(symbol, {}) 
        current_sl = float(active_orders.get("sl", 0))
        
        if entry_price <= 0 or current_price <= 0:
            return {"action": "NONE", "msg": "Giá bị lỗi."}

        plan = analysis_result.get("plan", {})
        tp1_price = float(plan.get("tp1", 0))
        
        if direction == "LONG":
            roe = ((current_price - entry_price) / entry_price) * 100 * leverage
            dist_to_tp1 = abs(tp1_price - entry_price) if tp1_price > 0 else 0
            curr_dist = current_price - entry_price
        else:
            roe = ((entry_price - current_price) / entry_price) * 100 * leverage
            dist_to_tp1 = abs(entry_price - tp1_price) if tp1_price > 0 else 0
            curr_dist = entry_price - current_price

        # 1. EARLY BREAKEVEN: 50% chặng đường -> Dời SL về Entry
        if dist_to_tp1 > 0 and (curr_dist / dist_to_tp1) >= 0.50:
            if abs(current_sl - entry_price) > (entry_price * 0.0001):
                log.info(f"🛡️ {symbol} đạt 50% TP1. Kéo SL về Entry {entry_price}!")
                self.cancel_all_orders(symbol)
                
                tp_target = float(plan.get("tp2", tp1_price))
                
                # [BẢN VÁ LỖI MŨI TÊN CHÍ MẠNG]
                # LONG thì phải đặt lệnh SELL để đóng. SHORT thì phải đặt lệnh BUY để đóng.
                close_direction = "SELL" if direction == "LONG" else "BUY"
                
                self._place_sl_tp(symbol, close_direction, current_qty, entry_price, tp_target)
                return {"action": "BREAKEVEN", "msg": "Kéo SL về hòa vốn."}

        # 2. XỬ LÝ LỌC NHIỄU (AI BÁO WAIT)
        new_signal = analysis_result.get("final", "WAIT")
        if new_signal == "WAIT":
            is_trending = analysis_result.get("timeframes", {}).get("1h", {}).get("is_trending", True)
            THRESHOLD = 12.0
            
            if roe >= THRESHOLD or roe <= -THRESHOLD:
                if not is_trending:
                    log.info(f"💰 Đóng chốt lời/cắt lỗ sớm {roe:.2f}% (Wait Regime).")
                    self.close_position(symbol, current_qty, direction)
                    self.cancel_all_orders(symbol)
                    return {"action": "CLOSE", "type": "CHỐT/CẮT SỚM", "roe": roe}
        
        # 3. ĐẢO CHIỀU HOÀN TOÀN
        elif (direction == "LONG" and new_signal == "SHORT") or \
             (direction == "SHORT" and new_signal == "LONG"):
            log.warning(f"🚨 Tín hiệu đảo ngược. Đóng lệnh {direction} cũ!")
            self.close_position(symbol, current_qty, direction)
            self.cancel_all_orders(symbol)
            return {"action": "CLOSE", "type": "ĐẢO CHIỀU", "roe": roe}

        return {"action": "HOLD", "msg": "Đang duy trì lệnh."}

