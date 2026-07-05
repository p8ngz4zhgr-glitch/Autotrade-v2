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
        
        # Convert any boolean to lowercase string "true"/"false" for query formatting
        for k, v in list(params.items()):
            if isinstance(v, bool):
                params[k] = "true" if v else "false"
        
        # Tự động định dạng Symbol thành chuẩn BingX
        if "symbol" in params and params["symbol"]:
            sym = str(params["symbol"]).strip().upper()
            if "-" not in sym:
                if sym.endswith("USDT"):
                    params["symbol"] = sym[:-4] + "-USDT"
                elif sym.endswith("USDC"):
                    params["symbol"] = sym[:-4] + "-USDC"

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
            "Content-Type": "application/json"
        }

        try:
            if method.upper() == "GET":
                r = requests.get(full_url, headers=headers, timeout=10)
            else:
                r = requests.post(full_url, headers=headers, timeout=10)
            
            r.raise_for_status()
            res = r.json()
            if not isinstance(res, dict):
                log.warning("BingX API returned non-dict response: %s", res)
                return {"code": -1, "msg": str(res), "data": {}}
            if res.get("code") != 0:
                log.warning("BingX API returned non-zero code: %s", res)
            return res
        except Exception as e:
            log.error("BingX request error %s %s: %s", method, path, e)
            return {"code": -1, "msg": str(e), "data": {}}

    def get_balance(self) -> float:
        """Lấy số dư khả dụng (USDT) của tài khoản Futures VST/Standard/Perpetual"""
        res = self._request("GET", "/openApi/swap/v2/user/balance")
        if isinstance(res, dict) and res.get("code") == 0:
            data = res.get("data")
            
            def extract_usdt(item):
                if isinstance(item, dict) and item.get("asset") == "USDT":
                    # Lấy số dư: ưu tiên equity (vốn chủ sở hữu), sau đó balance
                    return float(item.get("equity", 0) or item.get("balance", 0) or item.get("availableMargin", 0))
                return None

            # Quét đa dạng các cấu trúc API trả về
            if isinstance(data, list):
                for item in data:
                    val = extract_usdt(item)
                    if val is not None: return val
            elif isinstance(data, dict):
                balances = data.get("balance")
                if isinstance(balances, list):
                    for item in balances:
                        val = extract_usdt(item)
                        if val is not None: return val
                elif isinstance(balances, dict):
                    val = extract_usdt(balances)
                    if val is not None: return val
                
                val = extract_usdt(data)
                if val is not None: return val
                
                assets = data.get("assets", [])
                if isinstance(assets, list):
                    for item in assets:
                        val = extract_usdt(item)
                        if val is not None: return val

        return 0.0

    def get_latest_price(self, symbol: str) -> float:
        res = self._request("GET", "/openApi/swap/v1/ticker/price", {"symbol": symbol})
        if isinstance(res, dict) and res.get("code") == 0:
            data = res.get("data")
            if isinstance(data, dict):
                return float(data.get("price", 0))
        return 0.0

    def set_leverage(self, symbol: str, leverage: int, side: str = "LONG") -> dict:
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
                        
                        # Fix: Sử dụng avgPrice thay vì entryPrice cho BingX v2
                        entry_price = float(p.get("avgPrice", 0) or p.get("entryPrice", 0))
                        
                        # Fix: Lấy PnL Ratio trực tiếp từ API thay vì tính toán sai
                        pnl_ratio = p.get("pnlRatio")
                        pnl_pct = float(pnl_ratio) * 100 if pnl_ratio is not None else 0.0

                        positions.append({
                            "symbol": normalized_sym,
                            "direction": "LONG" if qty > 0 else "SHORT",
                            "entry": entry_price,
                            "qty": abs(qty),
                            "pnl": float(p.get("unrealizedProfit", 0)),
                            "pnl_pct": pnl_pct
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

    def place_order(self, symbol: str, side: str, qty: float, sl_price: float, tp_price: float) -> dict:
        position_side = "LONG" if side == "BUY" else "SHORT"
        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": qty,
            "positionSide": position_side,
        }
        res = self._request("POST", "/openApi/swap/v2/trade/order", params)
        if res.get("code") == 0:
            order_id = res.get("data", {}).get("orderId")
            log.info("Placed Market Order %s OK: %s", order_id, side)
            self._place_sl_tp(symbol, side, qty, sl_price, tp_price)
            return {"ok": True, "order_id": order_id}
        return {"ok": False, "msg": res.get("msg", "Error placing order")}

    def _place_sl_tp(self, symbol: str, side: str, qty: float, sl_price: float, tp_price: float):
        opposite_side = "SELL" if side == "BUY" else "BUY"
        position_side = "LONG" if side == "BUY" else "SHORT"
        if sl_price > 0:
            self._request("POST", "/openApi/swap/v2/trade/order", {
                "symbol": symbol,
                "side": opposite_side,
                "type": "STOP_MARKET",
                "stopPrice": sl_price,
                "quantity": qty,
                "positionSide": position_side,
                "reduceOnly": True
            })
        if tp_price > 0:
            self._request("POST", "/openApi/swap/v2/trade/order", {
                "symbol": symbol,
                "side": opposite_side,
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": tp_price,
                "quantity": qty,
                "positionSide": position_side,
                "reduceOnly": True
            })

    def cancel_all_orders(self, symbol: str) -> dict:
        return self._request("POST", "/openApi/swap/v2/trade/cancelAllAfter", {
            "symbol": symbol
        })

    def close_position(self, symbol: str, qty: float, direction: str) -> dict:
        opposite_side = "SELL" if direction == "LONG" else "BUY"
        params = {
            "symbol": symbol,
            "side": opposite_side,
            "type": "MARKET",
            "quantity": qty,
            "positionSide": direction,
            "reduceOnly": True
        }
        res = self._request("POST", "/openApi/swap/v2/trade/order", params)
        if res.get("code") == 0:
            self.cancel_all_orders(symbol)
            return {"ok": True}
        return {"ok": False, "msg": res.get("msg", "Error closing")}

    def handle_tp1_hit(self, symbol: str, direction: str, total_qty: float, entry_price: float, tp2_price: float) -> dict:
        half_qty = round(total_qty * 0.5, 4)
        log.info("Handling partial TP1 close for %s: %s, qty=%s", symbol, direction, half_qty)
        
        res = self.close_position(symbol, half_qty, direction)
        if not res.get("ok"):
            return res

        self.cancel_all_orders(symbol)
        
        self._place_sl_tp(
            symbol=symbol,
            side="BUY" if direction == "LONG" else "SELL",
            qty=round(total_qty - half_qty, 4),
            sl_price=entry_price,
            tp_price=tp2_price
        )
        return {"ok": True}