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

    def set_leverage(self, symbol: str, leverage: int, side: str = "ALL") -> dict:
        # Bắt buộc mặc định side="ALL" để tương thích tuyệt đối Hedge Mode
        res = self._request("POST", "/openApi/swap/v2/trade/leverage", {
            "symbol": symbol,
            "leverage": leverage,
            "side": side
        })
        # Fallback nếu sàn đang ở One-Way mode (Lỗi 109400)
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
                        # Bỏ dấu gạch ngang để tương thích Database / Miniapp
                        normalized_sym = sym.replace("-", "") if sym else ""
                        
                        pos_side = p.get("positionSide")
                        if pos_side in ("LONG", "SHORT"):
                            direction = pos_side
                        else:
                            direction = "LONG" if qty > 0 else "SHORT"
                            
                        # FIX LỖI 0.0000 ENTRY BUG: Ưu tiên dùng avgPrice từ API
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
        # Xử lý an toàn float tránh tràn thập phân hoặc dính Scientific Notation
        for k, v in list(params.items()):
            if isinstance(v, float):
                formatted_v = format(v, '.8f').rstrip('0').rstrip('.')
                params[k] = formatted_v if formatted_v else "0"
            elif isinstance(v, int) and not isinstance(v, bool):
                params[k] = str(v)

        res = self._request("POST", "/openApi/swap/v2/trade/order", params)
        
        # Xử lý Fallback nếu user đang dùng One-Way Mode (lỗi 109400)
        if res.get("code") == 109400 and "positionSide" in params: 
            params["positionSide"] = "BOTH"
            order_type = params.get("type", "").upper()
            
            # VAN AN TOÀN: Bắt buộc chèn reduceOnly nếu là lệnh cắt lỗ / chốt lời ở mode One-Way
            if order_type in ["STOP_MARKET", "TAKE_PROFIT_MARKET", "STOP", "TAKE_PROFIT"]:
                params["reduceOnly"] = "true"
                
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

        # 2. CÀI ĐẶT ĐÒN BẨY TRƯỚC KHI VÀO LỆNH (Tránh thiếu margin)
        try:
            log.info(f"Cài đặt đòn bẩy {leverage}x cho {symbol} trước khi đặt lệnh...")
            self.set_leverage(symbol, leverage, side="ALL")
        except Exception as e:
            log.warning(f"Lỗi khi set đòn bẩy cho {symbol}: {e}")

        # 3. TỰ ĐỘNG LẤY SỐ DƯ VÀ TÍNH TOÁN THEO % QUẢN LÝ VỐN
        available_balance = self.get_balance()
        current_price = self.get_latest_price(symbol)
        
        risk_percent = 0.10  # Dùng 10% vốn
        
        # CHỐT CHẶN AN TOÀN NẾU VÍ TRỐNG:
        if available_balance <= 0:
            log.error(f"⛔ Ví Swap trống (hoặc lỗi API)! Hủy lệnh {symbol}.")
            return {"ok": False, "msg": "Ví Swap trống."}
            
        if current_price <= 0:
            log.error(f"⛔ LỖI: Không lấy được giá thị trường cho {symbol}. Bot hủy lệnh.")
            return {"ok": False, "msg": "Lỗi API giá."}

        # Tính toán vốn
        capital = available_balance * risk_percent
        if capital < 2.5: # Bảo vệ lệnh tối thiểu của sàn
            capital = 2.5
        if capital > available_balance:
            capital = available_balance

        # Tính toán Qty và gọt số thập phân (max 4 số)
        calculated_qty = (capital * leverage) / current_price
        safe_qty = float(int(calculated_qty * 10000) / 10000)
        log.info(f"💰 Balance: {available_balance:.2f} USDT | Dùng {capital:.2f} USDT (Lev {leverage}x) -> Tự động tính Qty: {safe_qty}")

        position_side = "LONG" if side == "BUY" else "SHORT"

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
            
            # Đặt luôn Stoploss và Take Profit (đã tích hợp WICK PROTECT)
            self._place_sl_tp(symbol, side, safe_qty, sl_price, tp_price)
            
            return {"ok": True, "order_id": order_id}
            
        return {"ok": False, "msg": res.get("msg", "Error placing order")}

    def _place_sl_tp(self, symbol: str, side: str, qty: float, sl_price: float, tp_price: float):
        opposite_side = "SELL" if side == "BUY" else "BUY"
        position_side = "LONG" if side == "BUY" else "SHORT"
        
        # TÍCH HỢP CHỐNG GIẬT RÂU (Wick Management)
        if sl_price > 0:
            self._safe_order({
                "symbol": symbol,
                "side": opposite_side,
                "type": "STOP_MARKET",
                "stopPrice": sl_price,
                "quantity": qty,
                "positionSide": position_side,
                "workingType": "MARK_PRICE"  # NÉ QUÉT RÂU ẢO
            })
            
        if tp_price > 0:
            self._safe_order({
                "symbol": symbol,
                "side": opposite_side,
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": tp_price,
                "quantity": qty,
                "positionSide": position_side,
                "workingType": "CONTRACT_PRICE" # BẮT RÂU CHỐT LỜI SỚM
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
        # 1. Hủy ngay mọi lệnh chờ (TP/SL cũ)
        log.info(f"Đang xóa lệnh SL/TP cũ của {symbol}...")
        self.cancel_all_orders(symbol)
        
        # 2. Gọt số thập phân và chốt lời 50%
        half_qty = float(int((total_qty * 0.5) * 10000) / 10000)
        log.info(f"Đang chốt 50% vị thế {direction} của {symbol} (Qty: {half_qty})...")
        
        close_res = self.close_position(symbol, half_qty, direction)
        if not close_res.get("ok"):
            log.error(f"Lỗi khi chốt 50% vị thế: {close_res.get('msg')}")
            return close_res

        # Hủy lại lần 2 đảm bảo an toàn tuyệt đối
        self.cancel_all_orders(symbol)
        
        # 3. Tính toán 50% khối lượng còn lại
        remaining_qty = float(int((total_qty - half_qty) * 10000) / 10000)
        
        # Dự phòng khẩn cấp: Tự fetch lại Entry nếu tín hiệu truyền vào bị sai
        if entry_price <= 0:
            open_pos = self.get_open_positions(symbol)
            if open_pos:
                entry_price = open_pos[0].get("entry", 0)
        
        # 4. Kéo SL về chuẩn giá Entry và thả TP2
        log.info(f"Cài đặt SL mới tại Entry ({entry_price}) và TP2 ({tp2_price}) cho {remaining_qty} {symbol}...")
        self._place_sl_tp(
            symbol=symbol,
            side="BUY" if direction == "LONG" else "SELL", # Trả lại hướng mua/bán gốc
            qty=remaining_qty,
            sl_price=entry_price,
            tp_price=tp2_price
        )
        return {"ok": True}
