import hmac
import hashlib
import time
import requests
import urllib.parse
import logging

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
            
        # Convert boolean to lowercase string "true"/"false"
        for k, v in list(params.items()):
            if isinstance(v, bool):
                params[k] = "true" if v else "false"
            
        # Tự động dọn dẹp và định dạng Symbol thành chuẩn BingX
        if "symbol" in params and params["symbol"]:
            sym = str(params["symbol"]).strip().upper()
            
            # GIỮ NGUYÊN mã đặc biệt nếu nó khớp đúng chuẩn từ hệ thống của bạn
            if "NCCOGOLD2USD" in sym:
                params["symbol"] = "NCCOGOLD2USD-USDT"
            
            # Với các mã Crypto thông thường
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
                log.warning("BingX API returned non-dict response: %s", res)
                return {"code": -1, "msg": str(res), "data": {}}
            if res.get("code") != 0 and res.get("code") != 101205:
                log.warning("BingX API returned non-zero code: %s", res)
            return res
        except Exception as e:
            log.error("BingX request error %s %s: %s", method, path, e)
            return {"code": -1, "msg": str(e), "data": {}}

    def get_balance(self) -> float:
        res = self._request("GET", "/openApi/swap/v2/user/balance")
        if isinstance(res, dict) and res.get("code") == 0:
            data = res.get("data")
            if isinstance(data, dict):
                balances = data.get("balance", [])
                if isinstance(balances, list):
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

    def set_leverage(self, symbol: str, leverage: int, side: str = "BOTH") -> dict:
        return self._request("POST", "/openApi/swap/v2/trade/leverage", {
            "symbol": symbol,
            "leverage": leverage,
            "side": side
        })

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
                            
                        positions.append({
                            "symbol": normalized_sym,
                            "direction": direction,
                            "entry": float(p.get("entryPrice", 0)),
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
        # Xử lý an toàn float để tránh lỗi tràn thập phân hoặc dính Scientific Notation (1e-05)
        for k, v in list(params.items()):
            if isinstance(v, float):
                # Format tối đa 8 số thập phân, tự cắt bỏ số 0 vô nghĩa ở cuối
                formatted_v = format(v, '.8f').rstrip('0').rstrip('.')
                params[k] = formatted_v if formatted_v else "0"
            elif isinstance(v, int) and not isinstance(v, bool):
                params[k] = str(v)

        # Gửi request lần 1 (Mặc định kỳ vọng tài khoản đang dùng Hedge Mode)
        res = self._request("POST", "/openApi/swap/v2/trade/order", params)
        
        # Xử lý Fallback nếu user đang dùng One-Way Mode (lỗi 109400)
        if res.get("code") == 109400 and "positionSide" in params: 
            log.warning("Lỗi Hedge Mode 109400. Kích hoạt Fallback One-Way (BOTH) cho mã: %s", params.get("symbol"))
            
            params["positionSide"] = "BOTH"
            
            # --- CHỐT CHẶN AN TOÀN TRÁNH MỞ LỆNH NGƯỢC ---
            order_type = params.get("type", "").upper()
            if order_type in ["STOP_MARKET", "TAKE_PROFIT_MARKET", "STOP", "TAKE_PROFIT"]:
                params["reduceOnly"] = "true"
                log.info("Đã chèn thêm reduceOnly=true cho lệnh %s để chống mở vị thế ngược.", order_type)
            
            # Thực hiện gửi lại request sau khi đã sửa tham số
            res = self._request("POST", "/openApi/swap/v2/trade/order", params)
            
        return res

    def place_order(self, symbol: str, side: str, qty: float, sl_price: float, tp_price: float, leverage: int = 5) -> dict:
        # 1. KIỂM TRA VỊ THẾ (CHỐNG NHỒI LỆNH)
        open_positions = self.get_open_positions(symbol)
        if len(open_positions) > 0:
            current_direction = open_positions[0].get("direction")
            current_qty = open_positions[0].get("qty")
            log.info("⛔ BỎ QUA: Đã có sẵn vị thế %s cho mã %s (qty=%s). Không nhồi thêm lệnh.", current_direction, symbol, current_qty)
            return {"ok": False, "msg": "Position already exists"}

        position_side = "LONG" if side == "BUY" else "SHORT"

        # 2. CÀI ĐẶT ĐÒN BẨY TRƯỚC KHI VÀO LỆNH (Tránh thiếu margin)
        try:
            log.info(f"Cài đặt đòn bẩy {leverage}x cho {symbol} ({position_side}) trước khi đặt lệnh...")
            # Truyền đích danh hướng (position_side) thay vì "BOTH" để tương thích chuẩn Hedge Mode
            self.set_leverage(symbol, leverage, side=position_side)
        except Exception as e:
            log.warning(f"Lỗi khi set đòn bẩy cho {symbol}: {e}")

        # 3. GỌT SỐ THẬP PHÂN KHỐI LƯỢNG (Tránh lỗi size precision)
        safe_qty = float(int(qty * 10000) / 10000)

        # 4. THỰC THI ĐẶT LỆNH
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
            log.info("✅ Placed Market Order %s OK: %s (Qty an toàn: %s)", order_id, side, safe_qty)
            
            # Đặt luôn Stoploss và Take Profit
            self._place_sl_tp(symbol, side, safe_qty, sl_price, tp_price)
            
            return {"ok": True, "order_id": order_id}
            
        return {"ok": False, "msg": res.get("msg", "Error placing order")}

    def _place_sl_tp(self, symbol: str, side: str, qty: float, sl_price: float, tp_price: float):
        opposite_side = "SELL" if side == "BUY" else "BUY"
        position_side = "LONG" if side == "BUY" else "SHORT"
        
        # LƯU Ý: Tuyệt đối KHÔNG gán "reduceOnly": "true" vì nó báo lỗi xung đột với Hedge Mode ban đầu
        # Trách nhiệm xử lý reduceOnly đã được đẩy sang hàm _safe_order khi cần thiết
        if sl_price > 0:
            self._safe_order({
                "symbol": symbol,
                "side": opposite_side,
                "type": "STOP_MARKET",
                "stopPrice": sl_price,
                "quantity": qty,
                "positionSide": position_side
            })
            
        if tp_price > 0:
            self._safe_order({
                "symbol": symbol,
                "side": opposite_side,
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": tp_price,
                "quantity": qty,
                "positionSide": position_side
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
            # Đã xóa reduceOnly để không lỗi 109400
        }
        res = self._safe_order(params)
        if res.get("code") in (0, 101205):
            self.cancel_all_orders(symbol)
            return {"ok": True}
        return {"ok": False, "msg": res.get("msg", "Error closing")}

    def handle_tp1_hit(self, symbol: str, direction: str, total_qty: float, entry_price: float, tp2_price: float) -> dict:
        # Dùng phương pháp gọt chữ số thay vì round() để tránh lỗi vượt quá Margin khi làm tròn lên
        half_qty_raw = total_qty * 0.5
        half_qty = float(int(half_qty_raw * 10000) / 10000)
        
        log.info("Handling partial TP1 close for %s: %s, qty=%s", symbol, direction, half_qty)
        
        res = self.close_position(symbol, half_qty, direction)
        if not res.get("ok"):
            return res

        self.cancel_all_orders(symbol)
        
        # Tính toán lại số lượng còn lại bằng cách gọt tương tự
        remaining_qty_raw = total_qty - half_qty
        remaining_qty = float(int(remaining_qty_raw * 10000) / 10000)
        
        self._place_sl_tp(
            symbol=symbol,
            side="BUY" if direction == "LONG" else "SELL",
            qty=remaining_qty,
            sl_price=entry_price,
            tp_price=tp2_price
        )
        return {"ok": True}
