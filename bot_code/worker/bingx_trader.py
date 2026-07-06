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
        
        # Tá»± Äá»ng Äá»nh dáº¡ng Symbol thÃ nh chuáº©n BingX (cÃ³ dáº¥u gáº¡ch ngang, vÃ­ dá»¥: BTC-USDT)
        if "symbol" in params and params["symbol"]:
            sym = str(params["symbol"]).strip().upper()
            if "-" not in sym:
                if sym.endswith("USDT"):
                    params["symbol"] = sym[:-4] + "-USDT"
                elif sym.endswith("USDC"):
                    params["symbol"] = sym[:-4] + "-USDC"

        # TrÃ¡nh gá»­i request vÃ  log spam náº¿u API Key/Secret trá»ng, bá» thiáº¿u hoáº·c lÃ  mock key
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
        
        # Sáº¯p xáº¿p alphabet cÃ¡c tham sá» vÃ  táº¡o query string
        sorted_items = sorted(params.items())
        query_string = urllib.parse.urlencode(sorted_items)
        
        # TÃ­nh toÃ¡n chá»¯ kÃ½ dá»±a trÃªn query string ÄÃ£ sáº¯p xáº¿p
        signature = self._sign(params)

        # Táº¡o URL Äáº§y Äá»§ chá»©a query string vÃ  chá»¯ kÃ½ ÄÃ£ khá»p hoÃ n háº£o thá»© tá»±
        full_url = f"{self.BASE_URL}{path}?{query_string}&signature={signature}"

        headers = {
            "X-BX-APIKEY": self.api_key
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
            if res.get("code") != 0:
                log.warning("BingX API returned non-zero code: %s", res)
            return res
        except Exception as e:
            log.error("BingX request error %s %s: %s", method, path, e)
            return {"code": -1, "msg": str(e), "data": {}}

    def get_balance(self) -> float:
        """Láº¥y sá» dÆ° kháº£ dá»¥ng (USDT) cá»§a tÃ i khoáº£n Futures VST/Standard/Perpetual"""
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
        """Láº¥y giÃ¡ má»i nháº¥t cá»§a Symbol"""
        res = self._request("GET", "/openApi/swap/v1/ticker/price", {"symbol": symbol})
        if isinstance(res, dict) and res.get("code") == 0:
            data = res.get("data")
            if isinstance(data, dict):
                return float(data.get("price", 0))
        return 0.0

    def set_leverage(self, symbol: str, leverage: int) -> dict:
        """Thiáº¿t láº­p ÄÃ²n báº©y cho lá»nh (cáº£ LONG vÃ  SHORT)"""
        res_long = self._request("POST", "/openApi/swap/v2/trade/leverage", {
            "symbol": symbol,
            "leverage": leverage,
            "side": "LONG"
        })
        res_short = self._request("POST", "/openApi/swap/v2/trade/leverage", {
            "symbol": symbol,
            "leverage": leverage,
            "side": "SHORT"
        })
        return res_long

    def get_open_positions(self, symbol: str = None) -> list:
        """Láº¥y danh sÃ¡ch cÃ¡c vá» tháº¿ Äang má»"""
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
                        
                        # Fix for Float Division by Zero: avgPrice is the standard key in BingX Swap V2
                        entry_price = float(p.get("avgPrice") or p.get("entryPrice") or 0)
                        
                        positions.append({
                            "symbol": normalized_sym,
                            "direction": "LONG" if qty > 0 else "SHORT",
                            "entry": entry_price,
                            "qty": abs(qty),
                            "pnl": float(p.get("unrealizedProfit", 0)),
                        })
        return positions

    def get_trigger_orders(self) -> dict:
        """Láº¥y danh sÃ¡ch cÃ¡c lá»nh kÃ­ch hoáº¡t (SL/TP)"""
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
        """Äáº·t lá»nh Market + cÃ i SL/TP Äi kÃ¨m"""
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
            # ThÃ nh cÃ´ng -> Tiáº¿p tá»¥c Äáº·t lá»nh TP/SL náº¿u cÃ³
            order_id = res.get("data", {}).get("orderId")
            log.info("Placed Market Order %s OK: %s", order_id, side)
            self._place_sl_tp(symbol, side, qty, sl_price, tp_price)
            return {"ok": True, "order_id": order_id}
        return {"ok": False, "msg": res.get("msg", "Error placing order")}

    def _place_sl_tp(self, symbol: str, side: str, qty: float, sl_price: float, tp_price: float):
        if qty <= 0:
            return
        opposite_side = "SELL" if side == "BUY" else "BUY"
        position_side = "LONG" if side == "BUY" else "SHORT"
        if sl_price > 0:
            self._request("POST", "/openApi/swap/v2/trade/order", {
                "symbol": symbol,
                "side": opposite_side,
                "type": "STOP_MARKET",
                "stopPrice": sl_price,
                "quantity": qty,
                "positionSide": position_side
            })
        if tp_price > 0:
            self._request("POST", "/openApi/swap/v2/trade/order", {
                "symbol": symbol,
                "side": opposite_side,
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": tp_price,
                "quantity": qty,
                "positionSide": position_side
            })

    def cancel_all_orders(self, symbol: str) -> dict:
        """Há»§y toÃ n bá» lá»nh chá» cá»§a Symbol"""
        return self._request("DELETE", "/openApi/swap/v2/trade/allOpenOrders", {
            "symbol": symbol
        })

    def close_position(self, symbol: str, qty: float, direction: str) -> dict:
        """ÄÃ³ng vá» tháº¿ báº±ng lá»nh ngÆ°á»£c hÆ°á»ng"""
        opposite_side = "SELL" if direction == "LONG" else "BUY"
        params = {
            "symbol": symbol,
            "side": opposite_side,
            "type": "MARKET",
            "quantity": qty,
            "positionSide": direction
        }
        res = self._request("POST", "/openApi/swap/v2/trade/order", params)
        if res.get("code") == 0:
            self.cancel_all_orders(symbol)
            return {"ok": True}
        return {"ok": False, "msg": res.get("msg", "Error closing")}

    def handle_tp1_hit(self, symbol: str, direction: str, total_qty: float, entry_price: float, tp2_price: float) -> dict:
        """Xá»­ lÃ½ chá»t lá»i TP1 má»t pháº§n (50%) vá» tháº¿ vÃ  di dá»i SL vá» Entry"""
        # Náº¿u khá»i lÆ°á»£ng quÃ¡ nhá» khÃ´ng thá» chia ÄÃ´i, chá» kÃ©o SL vá» entry vÃ  giá»¯ nguyÃªn lá»nh tá»i TP2
        # Táº¡m thá»i chia ÄÃ´i chÃ­nh xÃ¡c Äáº¿n 4 chá»¯ sá» tháº­p phÃ¢n
        half_qty = round(total_qty * 0.5, 4)
        if half_qty <= 0 or half_qty == total_qty:
            log.info("Qty too small to split (%s), moving SL to entry only for %s", total_qty, symbol)
            self.cancel_all_orders(symbol)
            self._place_sl_tp(
                symbol=symbol,
                side="BUY" if direction == "LONG" else "SELL",
                qty=total_qty,
                sl_price=entry_price,
                tp_price=tp2_price
            )
            return {"ok": True, "split": False}
            
        log.info("Handling partial TP1 close for %s: %s, qty=%s", symbol, direction, half_qty)
        
        # 1. ÄÃ³ng má»t ná»­a vá» tháº¿ báº±ng lá»nh Market
        res = self.close_position(symbol, half_qty, direction)
        if not res.get("ok"):
            return res

        # 2. Há»§y SL/TP cÅ© vÃ  thiáº¿t láº­p SL má»i vá» Entry, TP2 má»i cho pháº§n cÃ²n láº¡i
        self.cancel_all_orders(symbol)
        
        # Äáº·t SL má»i vá» Entry (Breakeven) vÃ  giá»¯ TP2 cho ná»­a cÃ²n láº¡i
        self._place_sl_tp(
            symbol=symbol,
            side="BUY" if direction == "LONG" else "SELL",
            qty=round(total_qty - half_qty, 4),
            sl_price=entry_price,
            tp_price=tp2_price
        )
        return {"ok": True}
