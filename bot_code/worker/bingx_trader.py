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
        self.api_key    = api_key
        self.api_secret = api_secret

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
        
        params["apiKey"]    = self.api_key
        params["timestamp"] = int(time.time() * 1000)
        params["sign"]      = self._sign(params)

        headers = {
            "X-BX-APIKEY": self.api_key,
            "Content-Type": "application/json"
        }

        url = f"{self.BASE_URL}{path}"
        try:
            if method.upper() == "GET":
                r = requests.get(url, params=params, headers=headers, timeout=10)
            else:
                r = requests.post(url, json=params, headers=headers, timeout=10)
            
            r.raise_for_status()
            res = r.json()
            if res.get("code") != 0:
                log.warning("BingX API returned non-zero code: %s", res)
            return res
        except Exception as e:
            log.error("BingX request error %s %s: %s", method, path, e)
            return {"code": -1, "msg": str(e), "data": {}}

    def get_balance(self) -> float:
        """Lấy số dư khả dụng (USDT) của tài khoản Futures VST/Standard/Perpetual"""
        res = self._request("GET", "/openApi/swap/v2/user/balance")
        if res.get("code") == 0:
            for item in res.get("data", {}).get("balance", []):
                if item.get("asset") == "USDT":
                    return float(item.get("balance", 0))
        return 0.0

    def get_latest_price(self, symbol: str) -> float:
        """Lấy giá mới nhất của Symbol"""
        res = self._request("GET", "/openApi/swap/v1/ticker/price", {"symbol": symbol})
        if res.get("code") == 0:
            return float(res.get("data", {}).get("price", 0))
        return 0.0

    def set_leverage(self, symbol: str, leverage: int, side: str = "LONG") -> dict:
        """Thiết lập đòn bẩy cho lệnh"""
        return self._request("POST", "/openApi/swap/v2/trade/leverage", {
            "symbol": symbol,
            "leverage": leverage,
            "side": side
        })

    def get_open_positions(self, symbol: str = None) -> list:
        """Lấy danh sách các vị thế đang mở"""
        params = {}
        if symbol:
            params["symbol"] = symbol
        res = self._request("GET", "/openApi/swap/v2/user/positions", params)
        positions = []
        if res.get("code") == 0:
            for p in res.get("data", []):
                qty = float(p.get("positionAmt", 0))
                if qty == 0:
                    continue
                positions.append({
                    "symbol": p.get("symbol"),
                    "direction": "LONG" if qty > 0 else "SHORT",
                    "entry": float(p.get("entryPrice", 0)),
                    "qty": abs(qty),
                    "pnl": float(p.get("unrealizedProfit", 0)),
                })
        return positions

    def get_trigger_orders(self) -> dict:
        """Lấy danh sách các lệnh kích hoạt (SL/TP)"""
        res = self._request("GET", "/openApi/swap/v2/trade/openOrders")
        triggers = {}
        if res.get("code") == 0:
            for o in res.get("data", []):
                sym = o.get("symbol")
                if sym not in triggers:
                    triggers[sym] = {}
                otype = o.get("type", "")
                if "STOP_MARKET" in otype or "STOP" in otype:
                    triggers[sym]["sl"] = float(o.get("stopPrice", 0))
                elif "TAKE_PROFIT" in otype or "LIMIT" in otype:
                    triggers[sym]["tp2"] = float(o.get("price", 0))
        return triggers

    def place_order(self, symbol: str, side: str, qty: float, sl_price: float, tp_price: float) -> dict:
        """Đặt lệnh Market + cài SL/TP đi kèm"""
        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": qty,
        }
        res = self._request("POST", "/openApi/swap/v2/trade/order", params)
        if res.get("code") == 0:
            # Thành công -> Tiếp tục đặt lệnh TP/SL nếu có
            order_id = res.get("data", {}).get("orderId")
            log.info("Placed Market Order %s OK: %s", order_id, side)
            self._place_sl_tp(symbol, side, qty, sl_price, tp_price)
            return {"ok": True, "order_id": order_id}
        return {"ok": False, "msg": res.get("msg", "Error placing order")}

    def _place_sl_tp(self, symbol: str, side: str, qty: float, sl_price: float, tp_price: float):
        opposite_side = "SELL" if side == "BUY" else "BUY"
        if sl_price > 0:
            self._request("POST", "/openApi/swap/v2/trade/order", {
                "symbol": symbol,
                "side": opposite_side,
                "type": "STOP_MARKET",
                "stopPrice": sl_price,
                "quantity": qty,
                "reduceOnly": True
            })
        if tp_price > 0:
            self._request("POST", "/openApi/swap/v2/trade/order", {
                "symbol": symbol,
                "side": opposite_side,
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": tp_price,
                "quantity": qty,
                "reduceOnly": True
            })

    def cancel_all_orders(self, symbol: str) -> dict:
        """Hủy toàn bộ lệnh chờ của Symbol"""
        return self._request("POST", "/openApi/swap/v2/trade/cancelAllAfter", {
            "symbol": symbol
        })

    def close_position(self, symbol: str, qty: float, direction: str) -> dict:
        """Đóng vị thế bằng lệnh ngược hướng"""
        opposite_side = "SELL" if direction == "LONG" else "BUY"
        params = {
            "symbol": symbol,
            "side": opposite_side,
            "type": "MARKET",
            "quantity": qty,
            "reduceOnly": True
        }
        res = self._request("POST", "/openApi/swap/v2/trade/order", params)
        if res.get("code") == 0:
            self.cancel_all_orders(symbol)
            return {"ok": True}
        return {"ok": False, "msg": res.get("msg", "Error closing")}

    def handle_tp1_hit(self, symbol: str, direction: str, total_qty: float, entry_price: float, tp2_price: float) -> dict:
        """Xử lý chốt lời TP1 một phần (50%) vị thế và di dời SL về Entry"""
        half_qty = round(total_qty * 0.5, 4)
        log.info("Handling partial TP1 close for %s: %s, qty=%s", symbol, direction, half_qty)
        
        # 1. Đóng một nửa vị thế bằng lệnh Market
        res = self.close_position(symbol, half_qty, direction)
        if not res.get("ok"):
            return res

        # 2. Hủy SL/TP cũ và thiết lập SL mới về Entry, TP2 mới cho phần còn lại
        self.cancel_all_orders(symbol)
        
        # Đặt SL mới về Entry (Breakeven) và giữ TP2 cho nửa còn lại
        self._place_sl_tp(
            symbol=symbol,
            side="BUY" if direction == "LONG" else "SELL",
            qty=round(total_qty - half_qty, 4),
            sl_price=entry_price,
            tp_price=tp2_price
        )
        return {"ok": True}
