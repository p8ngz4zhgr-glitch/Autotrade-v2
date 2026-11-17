import hmac
import hashlib
import json
import math
import time
import threading
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
    _CONTRACT_SPECS_CACHE = {}
    _CONTRACT_SPECS_TS = 0.0
    _SPEC_LOCK = threading.Lock()

    def __init__(self, api_key: str, api_secret: str):
        self.api_key = str(api_key).strip() if api_key else ""
        self.api_secret = str(api_secret).strip() if api_secret else ""
        self._be_set_positions = set()
        self._dual_side_mode = None  # True = Hedge (LONG/SHORT), False = One-Way (BOTH)
        self._separate_isolated_mode = False
        self._position_ids_cache = {}

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        sym = str(symbol or "").strip().upper()
        if not sym:
            return ""
        if "NCCOGOLD2USD" in sym:
            return "NCCOGOLD2USD-USDT"
        if "NCCOXAG2USD" in sym:
            return "NCCOXAG2USD-USDT"
        if sym.endswith("USDT") and "-" not in sym:
            return sym[:-4] + "-USDT"
        return sym

    @staticmethod
    def _is_precious_metal(symbol: str) -> bool:
        s = str(symbol or "").upper()
        return any(k in s for k in ("NCCOGOLD", "NCCOXAG", "XAU", "XAG", "GOLD", "SILVER"))

    def _sign(self, params: dict) -> str:
        # BingX Swap V2 yêu cầu ký chuỗi raw key=val đã sắp xếp (không URL-encode dấu {}":, của JSON stopLoss/takeProfit)
        raw_query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        return hmac.new(
            self.api_secret.encode("utf-8"),
            raw_query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _request(self, method: str, path: str, params: dict = None) -> dict:
        if params is None:
            params = {}
        else:
            params = dict(params)

        for k, v in list(params.items()):
            if isinstance(v, bool):
                params[k] = "true" if v else "false"

        if "symbol" in params and params["symbol"]:
            params["symbol"] = self._normalize_symbol(params["symbol"])

        if not self.api_key or not self.api_secret:
            return {"code": -1, "msg": "API key or secret is empty", "data": {}}

        api_key_lower = self.api_key.lower()
        api_secret_lower = self.api_secret.lower()
        if (
            api_key_lower.startswith("mock")
            or api_secret_lower.startswith("mock")
            or "your_" in api_key_lower
            or "your_" in api_secret_lower
        ):
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

            # Đọc JSON trước raise_for_status để giữ nguyên code/msg thật từ BingX khi HTTP 4xx
            try:
                res = r.json()
                if isinstance(res, dict):
                    return res
            except Exception:
                pass

            r.raise_for_status()
            return {"code": -1, "msg": r.text[:200], "data": {}}
        except Exception as e:
            log.error("BingX request error %s %s: %s", method, path, e)
            return {"code": -1, "msg": str(e), "data": {}}

    # ════════════════════════════════════════════════════════════════════
    # THÔNG SỐ HỢP ĐỒNG SÀN (PRICE / QTY PRECISION & MIN TRADE LIMITS)
    # ════════════════════════════════════════════════════════════════════
    @classmethod
    def _refresh_contract_specs(cls):
        now = time.time()
        with cls._SPEC_LOCK:
            if cls._CONTRACT_SPECS_CACHE and (now - cls._CONTRACT_SPECS_TS) < 3600:
                return
            try:
                r = requests.get(f"{cls.BASE_URL}/openApi/swap/v2/quote/contracts", timeout=8)
                res = r.json() if r.status_code == 200 else {}
                items = res.get("data", []) if isinstance(res, dict) else []
                if isinstance(items, list) and items:
                    new_map = {}
                    for it in items:
                        if not isinstance(it, dict):
                            continue
                        sym = str(it.get("symbol", "")).upper()
                        if not sym:
                            continue
                        spec = {
                            "pricePrecision": int(it.get("pricePrecision", 4) or 4),
                            "quantityPrecision": int(it.get("quantityPrecision", 4) or 4),
                            "tradeMinQuantity": float(it.get("tradeMinQuantity", 0.0) or 0.0),
                            "tradeMinUSDT": float(it.get("tradeMinUSDT", 0.0) or 0.0),
                        }
                        new_map[sym] = spec
                        new_map[sym.replace("-", "")] = spec
                    if new_map:
                        cls._CONTRACT_SPECS_CACHE = new_map
                        cls._CONTRACT_SPECS_TS = now
            except Exception as e:
                log.debug("Không tải được contract specs BingX (dùng fallback): %s", e)

    @classmethod
    def get_contract_spec(cls, symbol: str) -> dict:
        cls._refresh_contract_specs()
        norm = cls._normalize_symbol(symbol)
        clean = norm.replace("-", "")
        if norm in cls._CONTRACT_SPECS_CACHE:
            return cls._CONTRACT_SPECS_CACHE[norm]
        if clean in cls._CONTRACT_SPECS_CACHE:
            return cls._CONTRACT_SPECS_CACHE[clean]

        # Fallback an toàn theo từng đồng phổ biến nếu API public quote tạm thời lỗi
        if clean.startswith("BTC"):
            return {"pricePrecision": 1, "quantityPrecision": 3, "tradeMinQuantity": 0.001, "tradeMinUSDT": 5.0}
        if clean.startswith("ETH"):
            return {"pricePrecision": 2, "quantityPrecision": 2, "tradeMinQuantity": 0.01, "tradeMinUSDT": 5.0}
        if clean.startswith("BNB"):
            return {"pricePrecision": 2, "quantityPrecision": 2, "tradeMinQuantity": 0.01, "tradeMinUSDT": 5.0}
        if clean.startswith("SOL"):
            return {"pricePrecision": 2, "quantityPrecision": 1, "tradeMinQuantity": 0.1, "tradeMinUSDT": 5.0}
        if clean.startswith("HYPE"):
            return {"pricePrecision": 3, "quantityPrecision": 1, "tradeMinQuantity": 0.1, "tradeMinUSDT": 5.0}
        if clean.startswith("XRP"):
            return {"pricePrecision": 4, "quantityPrecision": 0, "tradeMinQuantity": 1.0, "tradeMinUSDT": 5.0}
        if clean.startswith("DOGE"):
            return {"pricePrecision": 5, "quantityPrecision": 0, "tradeMinQuantity": 10.0, "tradeMinUSDT": 5.0}
        return {"pricePrecision": 3, "quantityPrecision": 2, "tradeMinQuantity": 0.01, "tradeMinUSDT": 5.0}

    def _format_price(self, symbol: str, price: float) -> float:
        if not price or price <= 0:
            return 0.0
        spec = self.get_contract_spec(symbol)
        pp = max(0, min(8, int(spec.get("pricePrecision", 4))))
        return round(float(price), pp)

    def _format_qty(self, symbol: str, qty: float) -> float:
        if not qty or qty <= 0:
            return 0.0
        spec = self.get_contract_spec(symbol)
        qp = max(0, min(8, int(spec.get("quantityPrecision", 4))))
        factor = 10 ** qp
        return float(math.floor(float(qty) * factor + 1e-9) / factor)

    def is_hedge_mode(self) -> bool:
        """Kiểm tra chế độ vị thế của tài khoản (Hedge LONG/SHORT vs One-Way BOTH)."""
        if self._dual_side_mode is not None:
            return self._dual_side_mode
        try:
            res = self._request("GET", "/openApi/swap/v2/trade/positionSide/dual")
            if isinstance(res, dict) and res.get("code") == 0:
                data = res.get("data") or {}
                raw = data.get("dualSidePosition")
                if isinstance(raw, bool):
                    self._dual_side_mode = raw
                elif isinstance(raw, str):
                    self._dual_side_mode = (raw.lower() == "true")
        except Exception as e:
            log.debug("Không kiểm tra được positionSide/dual: %s", e)
        if self._dual_side_mode is None:
            self._dual_side_mode = True
        return self._dual_side_mode

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
            log.error("⚠️ get_balance: code=0 nhưng không thấy USDT trong response: %s", res)
            return 0.0
        log.error(
            "⚠️ get_balance thất bại — BingX trả về: code=%s msg=%s",
            res.get("code") if isinstance(res, dict) else "?",
            res.get("msg") if isinstance(res, dict) else res,
        )
        return 0.0

    def get_latest_price(self, symbol: str) -> float:
        res = self._request("GET", "/openApi/swap/v1/ticker/price", {"symbol": symbol})
        if isinstance(res, dict) and res.get("code") == 0:
            data = res.get("data")
            if isinstance(data, dict):
                price = float(data.get("price", 0))
                if price <= 0:
                    log.error(
                        "⚠️ get_latest_price(%s): code=0 nhưng price=%s trong response: %s",
                        symbol, data.get("price"), res,
                    )
                return price
        log.error(
            "⚠️ get_latest_price(%s) thất bại — BingX trả về: code=%s msg=%s",
            symbol,
            res.get("code") if isinstance(res, dict) else "?",
            res.get("msg") if isinstance(res, dict) else res,
        )
        return 0.0

    def set_leverage(self, symbol: str, leverage: int, side: str = "ALL") -> dict:
        target_lev = int(leverage)
        if self._is_precious_metal(symbol):
            target_lev = 50
        req_side = str(side or "ALL").upper()

        # Trong chế độ Hedge (LONG/SHORT), đặt rõ cho CẢ 2 CHIỀU LONG & SHORT
        if req_side in ("ALL", "BOTH"):
            if self.is_hedge_mode():
                res_l = self._request("POST", "/openApi/swap/v2/trade/leverage", {
                    "symbol": symbol,
                    "leverage": target_lev,
                    "side": "LONG",
                })
                res_s = self._request("POST", "/openApi/swap/v2/trade/leverage", {
                    "symbol": symbol,
                    "leverage": target_lev,
                    "side": "SHORT",
                })
                ok_l = isinstance(res_l, dict) and res_l.get("code") == 0
                ok_s = isinstance(res_s, dict) and res_s.get("code") == 0
                if ok_l and ok_s:
                    log.info("✅ Đã set đòn bẩy %s: LONG=%dx & SHORT=%dx", symbol, target_lev, target_lev)
                    return {"code": 0, "msg": "SUCCESS", "long": res_l, "short": res_s}
                # Fallback One-Way mode nếu tài khoản không ở Hedge mode
                res_b = self._request("POST", "/openApi/swap/v2/trade/leverage", {
                    "symbol": symbol,
                    "leverage": target_lev,
                    "side": "BOTH",
                })
                if isinstance(res_b, dict) and res_b.get("code") == 0:
                    return res_b
                return res_l if ok_l else (res_s if ok_s else res_l)
            else:
                res_b = self._request("POST", "/openApi/swap/v2/trade/leverage", {
                    "symbol": symbol,
                    "leverage": target_lev,
                    "side": "BOTH",
                })
                if isinstance(res_b, dict) and res_b.get("code") == 0:
                    return res_b
                res_l = self._request("POST", "/openApi/swap/v2/trade/leverage", {
                    "symbol": symbol,
                    "leverage": target_lev,
                    "side": "LONG",
                })
                res_s = self._request("POST", "/openApi/swap/v2/trade/leverage", {
                    "symbol": symbol,
                    "leverage": target_lev,
                    "side": "SHORT",
                })
                if isinstance(res_l, dict) and res_l.get("code") == 0:
                    return {"code": 0, "msg": "SUCCESS", "long": res_l, "short": res_s}
                return res_b

        return self._request("POST", "/openApi/swap/v2/trade/leverage", {
            "symbol": symbol,
            "leverage": target_lev,
            "side": req_side,
        })

    def get_margin_type(self, symbol: str) -> str:
        res = self._request("GET", "/openApi/swap/v2/trade/marginType", {"symbol": symbol})
        if isinstance(res, dict) and res.get("code") == 0:
            data = res.get("data") or {}
            if isinstance(data, dict):
                return str(data.get("marginType", "") or "").upper()
        return ""

    def set_margin_type(self, symbol: str, margin_type: str) -> dict:
        return self._request("POST", "/openApi/swap/v2/trade/marginType", {
            "symbol": symbol,
            "marginType": str(margin_type).upper(),
        })

    @staticmethod
    def _is_order_source_mode_error(msg: str) -> bool:
        m = str(msg or "").lower()
        return (
            "multi position mode" in m
            or "cross multi" in m
            or "order source" in m
        )

    def get_open_positions(self, symbol: str = None):
        params = {}
        if symbol:
            params["symbol"] = symbol
        res = self._request("GET", "/openApi/swap/v2/user/positions", params)
        positions = []
        if isinstance(res, dict) and res.get("code") == 0:
            data = res.get("data")
            raw_pos = []
            if isinstance(data, list):
                raw_pos = data
            elif isinstance(data, dict):
                raw_pos = data.get("positions", []) or data.get("list", []) or []

            for p in raw_pos:
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
                    pid = str(p.get("positionId") or p.get("id") or "").strip()
                    if pid and normalized_sym:
                        self._position_ids_cache[(normalized_sym, direction)] = pid

                    positions.append({
                        "symbol": normalized_sym,
                        "direction": direction,
                        "entry": float(entry_val),
                        "qty": abs(qty),
                        "pnl": float(p.get("unrealizedProfit", 0)),
                        "position_id": pid,
                    })
            return positions
        else:
            log.error(
                "⚠️ get_open_positions thất bại: code=%s msg=%s",
                res.get("code") if isinstance(res, dict) else "?",
                res.get("msg") if isinstance(res, dict) else res,
            )
            return None

    def get_raw_open_orders(self, symbol: str = None):
        params = {"symbol": symbol} if symbol else {}
        res = self._request("GET", "/openApi/swap/v2/trade/openOrders", params)
        if not (isinstance(res, dict) and res.get("code") == 0):
            return None
        data = res.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("orders", []) or data.get("list", []) or []
        return []

    def get_trigger_orders(self):
        """
        Trả về dict {symbol: {"sl":..,"tp2":..}} khi gọi sàn THÀNH CÔNG (kể cả
        khi không có lệnh chờ nào — dict rỗng hợp lệ). Trả về None khi GỌI SÀN
        THẤT BẠI (lỗi API/mạng/xác thực).
        """
        orders = self.get_raw_open_orders()
        if orders is None:
            log.error("⚠️ get_trigger_orders thất bại khi truy vấn openOrders")
            return None

        triggers = {}
        symbol_prices = {}
        for o in orders:
            if isinstance(o, dict):
                sym = o.get("symbol", "")
                normalized_sym = sym.replace("-", "") if sym else ""
                if normalized_sym not in triggers:
                    triggers[normalized_sym] = {}

                otype = o.get("type", "").upper()
                stop_price = float(o.get("stopPrice") or o.get("triggerPrice") or o.get("price") or 0)
                pos_side = o.get("positionSide", "").upper()
                side = o.get("side", "").upper()

                if stop_price > 0:
                    is_sl = False
                    is_tp = False

                    if "STOP_LOSS" in otype or "STOP_MARKET" in otype or "STOP" in otype:
                        if "TAKE_PROFIT" not in otype:
                            is_sl = True
                    elif "TAKE_PROFIT" in otype:
                        is_tp = True
                    elif "LIMIT" in otype:
                        is_tp = True

                    if not is_sl and not is_tp:
                        inferred_pos_side = ""
                        if pos_side == "LONG":
                            inferred_pos_side = "LONG"
                        elif pos_side == "SHORT":
                            inferred_pos_side = "SHORT"
                        elif side == "SELL":
                            inferred_pos_side = "LONG"
                        elif side == "BUY":
                            inferred_pos_side = "SHORT"

                        if inferred_pos_side:
                            if normalized_sym not in symbol_prices:
                                symbol_prices[normalized_sym] = self.get_latest_price(sym)
                            cur_p = symbol_prices[normalized_sym]
                            if cur_p > 0:
                                if inferred_pos_side == "LONG":
                                    if stop_price < cur_p:
                                        is_sl = True
                                    else:
                                        is_tp = True
                                elif inferred_pos_side == "SHORT":
                                    if stop_price > cur_p:
                                        is_sl = True
                                    else:
                                        is_tp = True

                    if is_sl:
                        triggers[normalized_sym]["sl"] = stop_price
                        if sym:
                            if sym not in triggers:
                                triggers[sym] = {}
                            triggers[sym]["sl"] = stop_price
                    elif is_tp:
                        triggers[normalized_sym]["tp2"] = stop_price
                        if sym:
                            if sym not in triggers:
                                triggers[sym] = {}
                            triggers[sym]["tp2"] = stop_price
        return triggers

    def cancel_order_by_id(self, symbol: str, order_id) -> dict:
        return self._request("DELETE", "/openApi/swap/v2/trade/order", {
            "symbol": symbol,
            "orderId": str(order_id),
        })

    def _resolve_position_id(self, symbol: str, position_side: str = "", force_refresh: bool = False) -> str:
        norm_clean = self._normalize_symbol(symbol).replace("-", "").upper()
        pos_dir = str(position_side or "").upper()
        if not force_refresh and norm_clean:
            if pos_dir in ("LONG", "SHORT") and (norm_clean, pos_dir) in self._position_ids_cache:
                return self._position_ids_cache[(norm_clean, pos_dir)]
            for (c_sym, _), pid in self._position_ids_cache.items():
                if c_sym == norm_clean and pid:
                    return pid

        positions = self.get_open_positions(symbol)
        if isinstance(positions, list):
            for p in positions:
                if p.get("symbol") == norm_clean and p.get("position_id"):
                    if pos_dir in ("LONG", "SHORT") and p.get("direction") != pos_dir:
                        continue
                    return str(p["position_id"])
            for p in positions:
                if p.get("symbol") == norm_clean and p.get("position_id"):
                    return str(p["position_id"])
        return ""

    def _safe_order(self, params: dict) -> dict:
        clean_params = dict(params)
        order_type = str(clean_params.get("type", "")).upper()
        side_val = str(clean_params.get("side", "")).upper()
        is_trigger = order_type in ("STOP_MARKET", "TAKE_PROFIT_MARKET", "STOP", "TAKE_PROFIT")
        raw_pos_side = str(clean_params.get("positionSide", "")).upper()
        is_closing_market = (
            (side_val == "SELL" and raw_pos_side == "LONG")
            or (side_val == "BUY" and raw_pos_side == "SHORT")
        )
        is_closing_order = (
            is_trigger
            or is_closing_market
            or str(clean_params.get("reduceOnly", "")).lower() == "true"
            or str(clean_params.get("closePosition", "")).lower() == "true"
        )

        # Điều chỉnh theo chế độ One-Way (BOTH) nếu tài khoản đang bật One-Way
        if not self.is_hedge_mode() and "positionSide" in clean_params:
            clean_params["positionSide"] = "BOTH"
            if is_trigger and str(clean_params.get("closePosition", "")).lower() != "true":
                clean_params["reduceOnly"] = "true"

        # Nếu tài khoản dùng Separate Isolated Mode -> gắn sẵn positionId khi đóng/cài SL-TP
        if self._separate_isolated_mode and is_closing_order and not clean_params.get("positionId"):
            lookup_dir = raw_pos_side if raw_pos_side in ("LONG", "SHORT") else (
                "LONG" if side_val == "SELL" else ("SHORT" if side_val == "BUY" else "")
            )
            pid = self._resolve_position_id(clean_params.get("symbol", ""), lookup_dir)
            if pid:
                clean_params["positionId"] = pid

        for k, v in list(clean_params.items()):
            if isinstance(v, float):
                formatted_v = format(v, '.8f').rstrip('0').rstrip('.')
                clean_params[k] = formatted_v if formatted_v else "0"
            elif isinstance(v, int) and not isinstance(v, bool):
                clean_params[k] = str(v)

        res = self._request("POST", "/openApi/swap/v2/trade/order", clean_params)

        # Cho phép tự sửa tối đa 2 lần nếu gặp lỗi PositionSide (Hedge vs One-Way) hoặc thiếu positionId (Separate Isolated)
        for _ in range(2):
            if res.get("code") == 0:
                break
            err_msg = str(res.get("msg", "")).lower()
            fixed = False

            needs_position_id = (
                "positionid" in err_msg
                or "separate isolated" in err_msg
            )
            is_mode_mismatch = (
                "positionside" in err_msg
                or "position side" in err_msg
                or "dual" in err_msg
                or "one-way" in err_msg
                or "hedge mode" in err_msg
            )

            if needs_position_id and not clean_params.get("positionId"):
                self._separate_isolated_mode = True
                cur_ps = str(clean_params.get("positionSide", "")).upper()
                lookup_dir = cur_ps if cur_ps in ("LONG", "SHORT") else (
                    "LONG" if side_val == "SELL" else ("SHORT" if side_val == "BUY" else "")
                )
                pid = self._resolve_position_id(clean_params.get("symbol", ""), lookup_dir, force_refresh=True)
                if pid:
                    clean_params["positionId"] = pid
                    fixed = True

            if is_mode_mismatch and "positionSide" in clean_params:
                if "hedge mode" in err_msg or clean_params["positionSide"] == "BOTH":
                    self._dual_side_mode = True
                    clean_params.pop("reduceOnly", None)
                    if is_trigger:
                        clean_params["positionSide"] = "LONG" if side_val == "SELL" else "SHORT"
                    else:
                        clean_params["positionSide"] = "LONG" if side_val == "BUY" else "SHORT"
                    fixed = True
                elif clean_params["positionSide"] != "BOTH":
                    self._dual_side_mode = False
                    clean_params["positionSide"] = "BOTH"
                    if is_trigger and str(clean_params.get("closePosition", "")).lower() != "true":
                        clean_params["reduceOnly"] = "true"
                    fixed = True

            if not fixed:
                break
            res = self._request("POST", "/openApi/swap/v2/trade/order", clean_params)

        return res

    def place_order(self, symbol: str, side: str, qty: float, sl_price: float, tp_price: float, leverage: int = 10, p_win: float = 0.5, rr_ratio: float = 1.5, tp_levels: list = None) -> dict:
        if self._is_precious_metal(symbol):
            leverage = 50
        else:
            leverage = max(10, min(20, int(leverage or 10)))

        open_positions = self.get_open_positions(symbol)
        if open_positions is None:
            return {"ok": False, "msg": "Không kiểm tra được vị thế hiện tại trên BingX"}
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
            reason = []
            if available_balance <= 0:
                reason.append(f"balance={available_balance}")
            if current_price <= 0:
                reason.append(f"price={current_price}")
            msg = f"Invalid balance or price ({', '.join(reason)}) — xem log get_balance/get_latest_price phía trên để biết lý do thật từ BingX"
            log.error(f"⛔ {symbol}: {msg}")
            return {"ok": False, "msg": msg}

        quality_mult = 1.0
        markowitz_multiplier = 1.0
        kelly_percent = None

        if QuantRiskManager:
            try:
                quant = QuantRiskManager()
                all_open_pos = self.get_open_positions() or []
                markowitz_multiplier = quant.get_markowitz_penalty(symbol, all_open_pos)
                kelly_percent = quant.calculate_kelly_fraction(p_win, rr_ratio, fraction=0.5)
                KELLY_NEUTRAL = 0.0833
                raw_mult = (kelly_percent / KELLY_NEUTRAL) if KELLY_NEUTRAL > 0 else 1.0
                quality_mult = max(0.5, min(1.3, raw_mult)) * markowitz_multiplier
                log.info(
                    f"🧠 [QUANT] p_win={p_win:.2f} rr={rr_ratio:.2f} Kelly={kelly_percent:.3f} "
                    f"Markowitz={markowitz_multiplier:.2f} -> Quality x{quality_mult:.2f}"
                )
            except Exception as e:
                log.warning(f"Lỗi module Quant, giữ nguyên qty gốc (x1.0). Lỗi: {e}")

        if qty and qty > 0:
            safe_qty = qty * quality_mult
        else:
            risk_percent = min(0.1, (kelly_percent if kelly_percent is not None else 0.05) * markowitz_multiplier)
            capital_to_use = available_balance * risk_percent
            if (capital_to_use * leverage) < 5.0:
                capital_to_use = 5.0 / leverage
                log.info(f"⚠️ Vốn tính toán nhỏ hơn mức tối thiểu, điều chỉnh vốn về {capital_to_use:.2f} để đáp ứng lệnh sàn.")
            safe_qty = (capital_to_use * leverage) / current_price

        required_margin = (safe_qty * current_price) / leverage if leverage > 0 else safe_qty * current_price
        if required_margin > available_balance * 0.95:
            safe_qty = (available_balance * 0.95 * leverage) / current_price
            log.warning(f"⚠️ Qty vượt quá margin khả dụng ({required_margin:.2f} > {available_balance*0.95:.2f}), giảm về an toàn.")

        spec = self.get_contract_spec(symbol)
        MIN_QTY_MAP = {
            "BTC": 0.001,
            "ETH": 0.01,
            "BNB": 0.1,
            "SOL": 0.1,
            "XRP": 10.0,
            "DOGE": 100.0,
        }
        base_asset = symbol.replace("USDT", "").replace("-", "").upper()
        min_qty = max(float(spec.get("tradeMinQuantity", 0) or 0), MIN_QTY_MAP.get(base_asset, 0.0001))

        safe_qty = self._format_qty(symbol, safe_qty)
        if safe_qty < min_qty:
            safe_qty = self._format_qty(symbol, min_qty) or min_qty
            log.info(f"⚠️ Qty tính toán nhỏ hơn quy định của sàn đối với {base_asset}. Tự động nâng Qty lên {safe_qty}")

        capital_to_use = (safe_qty * current_price) / leverage if leverage > 0 else safe_qty * current_price
        log.info(
            f"💰 Balance: {available_balance:.2f} USDT | Qty gốc(caller): {qty} -> Qty cuối: {safe_qty} "
            f"| Margin ~{capital_to_use:.2f} USDT (Lev {leverage}x)"
        )

        # Chuẩn hoá SL/TP theo pricePrecision của sàn TRƯỚC khi gắn vào lệnh MARKET
        position_side = "LONG" if side == "BUY" else "SHORT"
        sl_adj, tp_adj, tp_levels_adj = self._sanitize_sl_tp_levels(
            symbol, position_side, current_price, sl_price, tp_price, tp_levels
        )

        # Tự động tạo 2 mốc TP (TP1 + TP2) nếu chưa có tp_levels
        if not tp_levels_adj and tp_adj > 0 and sl_adj > 0 and current_price > 0:
            sl_dist = abs(current_price - sl_adj)
            if sl_dist > 0:
                if side == "BUY":
                    tp1_p = self._format_price(symbol, current_price + 1.5 * sl_dist)
                    if tp1_p < tp_adj:
                        tp_levels_adj = [
                            {"level": 1, "price": tp1_p, "close_pct": 0.5},
                            {"level": 2, "price": tp_adj, "close_pct": 0.5},
                        ]
                else:
                    tp1_p = self._format_price(symbol, current_price - 1.5 * sl_dist)
                    if tp1_p > tp_adj:
                        tp_levels_adj = [
                            {"level": 1, "price": tp1_p, "close_pct": 0.5},
                            {"level": 2, "price": tp_adj, "close_pct": 0.5},
                        ]

        # Tính xem lệnh này có đủ kích thước để tách 2 TP không hay chỉ nên dùng 1 TP tổng
        effective_tps = self._plan_adaptive_tp_levels(symbol, safe_qty, current_price, tp_adj, tp_levels_adj)

        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": safe_qty,
            "positionSide": position_side,
        }

        # Gắn trực tiếp stopLoss (và takeProfit nếu chỉ có 1 mốc TP) ngay trong lệnh MARKET
        # để trên BingX App/Web hiển thị ngay TP/SL gắn liền vào vị thế
        attached_sl = False
        attached_tp = False
        if sl_adj > 0:
            params["stopLoss"] = json.dumps({
                "type": "STOP_MARKET",
                "stopPrice": sl_adj,
                "price": sl_adj,
                "workingType": "MARK_PRICE",
            }, separators=(",", ":"))
            attached_sl = True
        if len(effective_tps) == 1:
            single_tp = effective_tps[0]["price"]
            params["takeProfit"] = json.dumps({
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": single_tp,
                "price": single_tp,
                "workingType": "CONTRACT_PRICE",
            }, separators=(",", ":"))
            attached_tp = True

        res = self._safe_order(params)

        # Fallback 1: Nếu bị từ chối do "cross multi position mode not support current order source",
        # thử chuyển marginType của cặp này sang ISOLATED / SEPARATE_ISOLATED rồi đặt lại ngay
        if res.get("code") != 0 and self._is_order_source_mode_error(res.get("msg", "")):
            cur_mt = self.get_margin_type(symbol)
            log.warning(
                "⚠️ %s: Sàn từ chối (%s) | marginType hiện tại=%s -> thử chuyển sang ISOLATED/SEPARATE_ISOLATED...",
                symbol, res.get("msg", ""), cur_mt or "?",
            )
            candidates = [mt for mt in ("ISOLATED", "SEPARATE_ISOLATED") if mt != cur_mt]
            if not candidates:
                candidates = ["ISOLATED", "SEPARATE_ISOLATED"]
            for target_mt in candidates:
                mt_res = self.set_margin_type(symbol, target_mt)
                log.info(
                    "⚙️ [MARGIN MODE] %s: Chuyển sang %s -> code=%s msg=%s",
                    symbol, target_mt,
                    mt_res.get("code") if isinstance(mt_res, dict) else "?",
                    mt_res.get("msg") if isinstance(mt_res, dict) else mt_res,
                )
                if isinstance(mt_res, dict) and mt_res.get("code") == 0:
                    time.sleep(0.2)
                    try:
                        self.set_leverage(symbol, leverage, side="ALL")
                    except Exception:
                        pass
                    res = self._safe_order(params)
                    if res.get("code") == 0 or not self._is_order_source_mode_error(res.get("msg", "")):
                        break

        # Fallback 2: Nếu BingX từ chối gắn inline stopLoss/takeProfit (ví dụ vài cặp đặc thù hoặc giá giật),
        # mở lệnh MARKET thuần trước rồi đặt SL/TP độc lập ngay lập tức
        if res.get("code") != 0 and (attached_sl or attached_tp):
            log.warning(
                "⚠️ %s: Gắn TP/SL trực tiếp cùng lệnh MARKET bị sàn từ chối (%s). Thử vào MARKET rồi đặt TP/SL riêng...",
                symbol, res.get("msg", "Unknown"),
            )
            bare_params = {
                "symbol": symbol,
                "side": side,
                "type": "MARKET",
                "quantity": safe_qty,
                "positionSide": position_side,
            }
            res = self._safe_order(bare_params)
            attached_sl = False
            attached_tp = False

        if res.get("code") == 0:
            order_id = res.get("data", {}).get("orderId")
            log.info("✅ Placed Market Order %s OK: %s (Qty: %s)", order_id, side, safe_qty)

            # Đợi 0.3s cho vị thế khớp hoàn toàn trên sổ lệnh BingX
            time.sleep(0.3)

            # Nếu chưa gắn inline đủ cả SL & TP (ví dụ có 2 mốc TP hoặc inline fallback), gọi đặt phần còn thiếu
            sl_ok = attached_sl
            tp_ok = attached_tp
            if not sl_ok or not tp_ok:
                sl_res, tp_res = self._place_sl_tp(
                    symbol, side, safe_qty,
                    0.0 if sl_ok else sl_adj,
                    0.0 if tp_ok else tp_adj,
                    tp_levels=None if tp_ok else effective_tps,
                    return_status=True,
                )
                sl_ok = sl_ok or sl_res
                tp_ok = tp_ok or tp_res

            # Xác minh thực tế trên sàn BingX một lần nữa (đề phòng inline attach bị sàn bỏ qua)
            triggers = self.get_trigger_orders()
            if isinstance(triggers, dict):
                clean_sym = symbol.replace("-", "")
                trig = triggers.get(symbol, {}) or triggers.get(clean_sym, {}) or {}
                has_sl_on_exchange = bool(trig.get("sl"))
                has_tp_on_exchange = bool(trig.get("tp2"))

                if (not has_sl_on_exchange and sl_adj > 0) or (not has_tp_on_exchange and (tp_adj > 0 or effective_tps)):
                    log.warning(
                        "⚠️ %s: Xác minh sau vào lệnh thấy thiếu trên BingX (SL=%s, TP=%s) -> Tự động đặt bù ngay!",
                        symbol, has_sl_on_exchange, has_tp_on_exchange,
                    )
                    fix_sl, fix_tp = self._place_sl_tp(
                        symbol, side, safe_qty,
                        0.0 if has_sl_on_exchange else sl_adj,
                        0.0 if has_tp_on_exchange else tp_adj,
                        tp_levels=None if has_tp_on_exchange else effective_tps,
                        return_status=True,
                    )
                    sl_ok = has_sl_on_exchange or fix_sl
                    tp_ok = has_tp_on_exchange or fix_tp
                else:
                    sl_ok = has_sl_on_exchange
                    tp_ok = has_tp_on_exchange

            return {
                "ok": True,
                "order_id": order_id,
                "qty": safe_qty,
                "sl_ok": bool(sl_ok),
                "tp_ok": bool(tp_ok),
                "sl_price": sl_adj,
                "tp_price": effective_tps[-1]["price"] if effective_tps else tp_adj,
            }

        err_msg = str(res.get("msg", "Error placing order"))
        if self._is_order_source_mode_error(err_msg):
            return {
                "ok": False,
                "margin_mode_blocked": True,
                "msg": f"{err_msg} (Tài khoản BingX đang bật Cross Multi-Position cho {symbol} — cần đổi từ Cross sang Isolated hoặc tắt Đa vị thế trên App BingX)",
            }
        return {"ok": False, "msg": err_msg}

    def _sanitize_sl_tp_levels(self, symbol: str, position_side: str, cur_price: float, sl_price: float, tp_price: float, tp_levels: list = None):
        """
        Đảm bảo SL/TP luôn đúng phía với giá hiện tại và làm tròn đúng pricePrecision của BingX.
        Không bao giờ xoá trắng SL về 0 nếu giá mới nhích nhẹ qua điểm vào lúc đặt lệnh.
        """
        sl_out = self._format_price(symbol, sl_price) if sl_price and sl_price > 0 else 0.0
        tp_out = self._format_price(symbol, tp_price) if tp_price and tp_price > 0 else 0.0

        if cur_price and cur_price > 0:
            if position_side == "LONG":
                if sl_out > 0 and sl_out >= cur_price:
                    clamped_sl = self._format_price(symbol, cur_price * 0.992)
                    log.warning(f"⚠️ [SL CLAMP] LONG {symbol}: sl_price ({sl_out}) >= cur ({cur_price}) -> Kéo an toàn về {clamped_sl}")
                    sl_out = clamped_sl
                if tp_out > 0 and tp_out <= cur_price:
                    clamped_tp = self._format_price(symbol, cur_price * 1.015)
                    log.warning(f"⚠️ [TP CLAMP] LONG {symbol}: tp_price ({tp_out}) <= cur ({cur_price}) -> Đẩy về {clamped_tp}")
                    tp_out = clamped_tp
            else:  # SHORT
                if sl_out > 0 and sl_out <= cur_price:
                    clamped_sl = self._format_price(symbol, cur_price * 1.008)
                    log.warning(f"⚠️ [SL CLAMP] SHORT {symbol}: sl_price ({sl_out}) <= cur ({cur_price}) -> Kéo an toàn về {clamped_sl}")
                    sl_out = clamped_sl
                if tp_out > 0 and tp_out >= cur_price:
                    clamped_tp = self._format_price(symbol, cur_price * 0.985)
                    log.warning(f"⚠️ [TP CLAMP] SHORT {symbol}: tp_price ({tp_out}) >= cur ({cur_price}) -> Đẩy về {clamped_tp}")
                    tp_out = clamped_tp

        clean_levels = []
        if tp_levels and isinstance(tp_levels, list):
            for idx, lvl in enumerate(tp_levels):
                if not isinstance(lvl, dict):
                    continue
                p = self._format_price(symbol, float(lvl.get("price", 0) or 0))
                pct = float(lvl.get("close_pct", 0) or 0)
                if p <= 0 or pct <= 0:
                    continue
                if cur_price and cur_price > 0:
                    if position_side == "LONG" and p <= cur_price:
                        continue
                    if position_side == "SHORT" and p >= cur_price:
                        continue
                clean_levels.append({
                    "level": lvl.get("level", idx + 1),
                    "price": p,
                    "close_pct": pct,
                })

        if not clean_levels and tp_out > 0:
            clean_levels = [{"level": 1, "price": tp_out, "close_pct": 1.0}]

        return sl_out, tp_out, clean_levels

    def _plan_adaptive_tp_levels(self, symbol: str, qty: float, ref_price: float, tp_price: float, tp_levels: list) -> list:
        """
        Kiểm tra mỗi phần chia TP có đạt min_qty & min_notional của BingX hay không.
        Nếu chia nhỏ bị dưới ngưỡng sàn -> gộp lại thành 1 hoặc 2 mốc TP hợp lệ để sàn không từ chối.
        """
        if not tp_levels:
            if tp_price and tp_price > 0:
                return [{"level": 1, "price": self._format_price(symbol, tp_price), "close_pct": 1.0}]
            return []

        spec = self.get_contract_spec(symbol)
        clean_sym = symbol.replace("-", "").upper()
        base_asset = clean_sym.replace("USDT", "")
        MIN_QTY_MAP = {"BTC": 0.001, "ETH": 0.01, "BNB": 0.1, "SOL": 0.1, "XRP": 10.0, "DOGE": 100.0}
        min_qty = max(float(spec.get("tradeMinQuantity", 0) or 0), MIN_QTY_MAP.get(base_asset, 0.0001))

        if "ETH" in clean_sym:
            min_notional_floor = max(25.0, float(spec.get("tradeMinUSDT", 0) or 0))
        elif "BTC" in clean_sym:
            min_notional_floor = max(10.0, float(spec.get("tradeMinUSDT", 0) or 0))
        elif "BNB" in clean_sym:
            min_notional_floor = max(7.5, float(spec.get("tradeMinUSDT", 0) or 0))
        elif "SOL" in clean_sym:
            min_notional_floor = max(8.0, float(spec.get("tradeMinUSDT", 0) or 0))
        else:
            min_notional_floor = max(6.0, float(spec.get("tradeMinUSDT", 0) or 0))

        px = ref_price if ref_price > 0 else tp_levels[0]["price"]
        total_notional = qty * px

        # Kiểm tra nếu tách làm nhiều lệnh thì từng lệnh con có đạt min_qty & min_notional không
        can_split_all = len(tp_levels) > 1 and total_notional >= min_notional_floor * len(tp_levels)
        if can_split_all:
            for lvl in tp_levels:
                part_qty = self._format_qty(symbol, qty * lvl["close_pct"])
                if part_qty < min_qty or (part_qty * px) < min_notional_floor:
                    can_split_all = False
                    break

        if can_split_all:
            return tp_levels

        # Thử tách 2 mốc TP (50% / 50%) nếu cả 2 nửa đều đạt min_qty & min_notional
        if len(tp_levels) >= 2:
            half_qty = self._format_qty(symbol, qty * 0.5)
            if half_qty >= min_qty and (half_qty * px) >= min_notional_floor:
                return [
                    {"level": 1, "price": tp_levels[0]["price"], "close_pct": 0.50},
                    {"level": 2, "price": tp_levels[-1]["price"], "close_pct": 0.50},
                ]

        # Không đủ tách nhỏ -> gộp 100% vào 1 lệnh TP (ưu tiên TP cuối/TP2 nếu hợp lý, hoặc TP1)
        chosen_price = tp_levels[0]["price"] if len(tp_levels) == 1 else (tp_price if tp_price > 0 else tp_levels[0]["price"])
        log.info(
            f"ℹ️ [ADAPTIVE TP] {symbol}: Qty={qty} (${total_notional:.1f} USDT) không đủ tách nhiều lệnh con (min_qty={min_qty}, min_notional=${min_notional_floor:.1f}) -> Gộp thành 1 lệnh TP 100% tại ${chosen_price}."
        )
        return [{"level": 1, "price": self._format_price(symbol, chosen_price), "close_pct": 1.0}]

    def _place_sl_tp(self, symbol: str, side: str, qty: float, sl_price: float, tp_price: float, tp_levels: list = None, return_status: bool = False):
        opposite_side = "SELL" if side == "BUY" else "BUY"
        position_side = "LONG" if side == "BUY" else "SHORT"

        if sl_price <= 0 and tp_price <= 0 and not tp_levels:
            log.warning(f"⚠️ {symbol}: Bỏ qua đặt SL/TP vì giá trị nhận được đều <= 0 (SL: {sl_price}, TP: {tp_price})")
            return (False, False) if return_status else None

        cur_price = self.get_latest_price(symbol)
        sl_price, tp_price, valid_tp_levels = self._sanitize_sl_tp_levels(
            symbol, position_side, cur_price, sl_price, tp_price, tp_levels
        )
        qty_fmt = self._format_qty(symbol, qty)
        if qty_fmt <= 0:
            qty_fmt = qty

        sl_ok = False
        if sl_price > 0:
            res_sl = self._safe_order({
                "symbol": symbol,
                "side": opposite_side,
                "type": "STOP_MARKET",
                "stopPrice": sl_price,
                "quantity": qty_fmt,
                "positionSide": position_side,
                "workingType": "MARK_PRICE",
            })
            if res_sl.get("code") == 0:
                sl_ok = True
                log.info(f"🛡️ Đặt Stop Loss OK: {symbol} tại {sl_price} (Qty: {qty_fmt})")
            else:
                log.warning(f"⚠️ Đặt SL dạng quantity thất bại ({res_sl.get('msg')}) -> Thử closePosition=true cho {symbol}...")
                res_sl_cp = self._safe_order({
                    "symbol": symbol,
                    "side": opposite_side,
                    "type": "STOP_MARKET",
                    "stopPrice": sl_price,
                    "closePosition": "true",
                    "positionSide": position_side,
                    "workingType": "MARK_PRICE",
                })
                if res_sl_cp.get("code") == 0:
                    sl_ok = True
                    log.info(f"🛡️ Đặt Stop Loss (closePosition) OK: {symbol} tại {sl_price}")
                else:
                    # Fallback an toàn theo giá mới nhất nếu giá thị trường vừa chạm sát sl_price (tránh lỗi trigger ngay)
                    fresh_px = self.get_latest_price(symbol) or cur_price
                    if fresh_px > 0:
                        safe_sl = self._format_price(
                            symbol, fresh_px * (0.988 if position_side == "LONG" else 1.012)
                        )
                        if safe_sl > 0 and safe_sl != sl_price:
                            res_sl_fb = self._safe_order({
                                "symbol": symbol,
                                "side": opposite_side,
                                "type": "STOP_MARKET",
                                "stopPrice": safe_sl,
                                "closePosition": "true",
                                "positionSide": position_side,
                                "workingType": "CONTRACT_PRICE",
                            })
                            if res_sl_fb.get("code") == 0:
                                sl_ok = True
                                log.info(f"🛡️ Đặt Stop Loss Fallback (CONTRACT_PRICE) OK: {symbol} tại {safe_sl}")
                    if not sl_ok:
                        log.error(f"❌ Đặt Stop Loss THẤT BẠI: {symbol} - Lỗi: {res_sl_cp.get('msg', 'Unknown')}")

        # XỬ LÝ TAKE PROFIT THÔNG MINH
        ref_price = cur_price if cur_price > 0 else (tp_price if tp_price > 0 else 1.0)
        adjusted_tp_levels = self._plan_adaptive_tp_levels(
            symbol, qty_fmt, ref_price, tp_price, valid_tp_levels
        )

        success_tp_count = 0
        if adjusted_tp_levels:
            remaining_tp_qty = qty_fmt
            for idx, lvl in enumerate(adjusted_tp_levels):
                lvl_price = self._format_price(symbol, lvl.get("price", 0))
                lvl_pct = float(lvl.get("close_pct", 0) or 0)
                if lvl_price <= 0 or lvl_pct <= 0:
                    continue
                if idx == len(adjusted_tp_levels) - 1:
                    tp_qty = self._format_qty(symbol, remaining_tp_qty)
                else:
                    tp_qty = self._format_qty(symbol, qty_fmt * lvl_pct)
                    remaining_tp_qty = max(0.0, remaining_tp_qty - tp_qty)
                if tp_qty <= 0:
                    continue
                res_tp = self._safe_order({
                    "symbol": symbol,
                    "side": opposite_side,
                    "type": "TAKE_PROFIT_MARKET",
                    "stopPrice": lvl_price,
                    "quantity": tp_qty,
                    "positionSide": position_side,
                    "workingType": "CONTRACT_PRICE",
                })
                if res_tp.get("code") == 0:
                    success_tp_count += 1
                    log.info(f"🎯 Đặt Take Profit {lvl.get('level')} OK: {symbol} tại {lvl_price} (Qty: {tp_qty})")
                else:
                    log.warning(f"⚠️ Đặt Take Profit {lvl.get('level')} ({symbol} Qty {tp_qty}) lỗi: {res_tp.get('msg', 'Unknown')}")

        # Fallback: Nếu các mốc TP chia nhỏ đều thất bại -> đặt 1 lệnh TP toàn phần (quantity hoặc closePosition=true)
        if success_tp_count == 0 and (tp_price > 0 or adjusted_tp_levels):
            fallback_tp_price = tp_price if tp_price > 0 else adjusted_tp_levels[0].get("price", 0)
            fallback_tp_price = self._format_price(symbol, fallback_tp_price)
            if fallback_tp_price > 0:
                res_tp = self._safe_order({
                    "symbol": symbol,
                    "side": opposite_side,
                    "type": "TAKE_PROFIT_MARKET",
                    "stopPrice": fallback_tp_price,
                    "quantity": qty_fmt,
                    "positionSide": position_side,
                    "workingType": "CONTRACT_PRICE",
                })
                if res_tp.get("code") == 0:
                    success_tp_count += 1
                    log.info(f"🎯 Đặt Take Profit Toàn Phần (Fallback) OK: {symbol} tại {fallback_tp_price} (Qty: {qty_fmt})")
                else:
                    res_tp_cp = self._safe_order({
                        "symbol": symbol,
                        "side": opposite_side,
                        "type": "TAKE_PROFIT_MARKET",
                        "stopPrice": fallback_tp_price,
                        "closePosition": "true",
                        "positionSide": position_side,
                        "workingType": "CONTRACT_PRICE",
                    })
                    if res_tp_cp.get("code") == 0:
                        success_tp_count += 1
                        log.info(f"🎯 Đặt Take Profit (closePosition) OK: {symbol} tại {fallback_tp_price}")
                    else:
                        log.error(f"❌ Đặt Take Profit THẤT BẠI: {symbol} - Lỗi: {res_tp_cp.get('msg', 'Unknown')}")

        if return_status:
            return sl_ok, (success_tp_count > 0)
        return None

    def ensure_sl_tp(self, symbol: str, direction: str, qty: float, sl_price: float, tp_price: float, tp_levels: list = None) -> dict:
        """
        Tự động kiểm tra và đặt bù SL hoặc TP nếu vị thế đang mở trên BingX bị thiếu SL hoặc TP.
        """
        triggers = self.get_trigger_orders()
        if triggers is None:
            return {"ok": False, "msg": "API error"}
        clean_sym = symbol.replace("-", "").upper()
        trig = triggers.get(symbol, {}) or triggers.get(clean_sym, {}) or {}
        need_sl = not bool(trig.get("sl")) and sl_price > 0
        need_tp = not bool(trig.get("tp2")) and (tp_price > 0 or bool(tp_levels))
        if not need_sl and not need_tp:
            return {"ok": True, "healed": False, "sl": trig.get("sl"), "tp": trig.get("tp2")}

        side = "BUY" if direction == "LONG" else "SELL"
        sl_res, tp_res = self._place_sl_tp(
            symbol, side, qty,
            sl_price if need_sl else 0.0,
            tp_price if need_tp else 0.0,
            tp_levels=tp_levels if need_tp else None,
            return_status=True,
        )
        return {"ok": True, "healed": True, "sl_placed": sl_res, "tp_placed": tp_res}

    # ════════════════════════════════════════════════════════════════════
    # [FIX v6.2] Breakeven có đệm phí — tránh "hoà vốn" thành lỗ nhẹ sau phí/trượt giá
    # ════════════════════════════════════════════════════════════════════
    BREAKEVEN_BUFFER_PCT = 0.0012  # ~0.12%, đủ che hoàn toàn phí taker 2 chiều (0.05% x 2) + trượt giá nhỏ

    def breakeven_price(self, direction: str, entry_price: float) -> float:
        if entry_price <= 0:
            return entry_price
        if direction == "LONG":
            return round(entry_price * (1 + self.BREAKEVEN_BUFFER_PCT), 6)
        return round(entry_price * (1 - self.BREAKEVEN_BUFFER_PCT), 6)

    def set_runner_sl_tp(self, symbol: str, direction: str, qty: float, sl_price: float, tp_price: float):
        side = "BUY" if direction == "LONG" else "SELL"
        return self._place_sl_tp(symbol, side, qty, sl_price, tp_price)

    def update_sl_and_keep_remaining_tps(self, symbol: str, direction: str, qty: float, sl_price: float, raw_tp_levels: list, current_price: float):
        """
        Cập nhật SL mới AN TOÀN mà KHÔNG xoá oan các lệnh TP đang chờ trên sàn!
        Chỉ huỷ các lệnh STOP_MARKET (SL) cũ của symbol sau khi hoặc trước khi đặt SL mới;
        chỉ đặt lại TP nếu trên sàn chưa có lệnh TP nào.
        """
        norm_target = self._normalize_symbol(symbol)
        raw_orders = self.get_raw_open_orders(symbol)
        old_sl_ids = []
        has_existing_tp = False

        if isinstance(raw_orders, list):
            for o in raw_orders:
                if not isinstance(o, dict):
                    continue
                osym = self._normalize_symbol(o.get("symbol", ""))
                if osym != norm_target:
                    continue
                otype = str(o.get("type", "")).upper()
                oid = o.get("orderId")
                if "TAKE_PROFIT" in otype or otype == "LIMIT":
                    has_existing_tp = True
                elif "STOP" in otype and oid:
                    old_sl_ids.append(oid)

        # Huỷ riêng các lệnh SL cũ (giữ nguyên toàn bộ TP đang chờ trên sàn)
        if raw_orders is not None:
            for oid in old_sl_ids:
                self.cancel_order_by_id(symbol, oid)
        else:
            # Nếu không liệt kê được openOrders theo id thì mới dùng cancel_all và đặt lại cả TP
            self.cancel_all_orders(symbol)
            has_existing_tp = False

        remaining_tps = []
        if not has_existing_tp and raw_tp_levels and isinstance(raw_tp_levels, list):
            for lvl in raw_tp_levels:
                if isinstance(lvl, dict):
                    p = float(lvl.get("price", 0) or 0)
                    if p > 0:
                        if direction == "LONG" and p > current_price:
                            remaining_tps.append(lvl)
                        elif direction == "SHORT" and p < current_price:
                            remaining_tps.append(lvl)

        side = "BUY" if direction == "LONG" else "SELL"
        sl_ok, tp_ok = self._place_sl_tp(
            symbol, side, qty, sl_price, 0.0,
            tp_levels=remaining_tps if not has_existing_tp else None,
            return_status=True,
        )
        return {"sl_ok": sl_ok, "tp_ok": has_existing_tp or tp_ok}

    def cancel_all_orders(self, symbol: str) -> dict:
        return self._request("DELETE", "/openApi/swap/v2/trade/allOpenOrders", {
            "symbol": symbol,
        })

    def close_position(self, symbol: str, qty: float, direction: str) -> dict:
        opposite_side = "SELL" if direction == "LONG" else "BUY"
        qty_fmt = self._format_qty(symbol, qty) or qty
        params = {
            "symbol": symbol,
            "side": opposite_side,
            "type": "MARKET",
            "quantity": qty_fmt,
            "positionSide": direction,
        }
        res = self._safe_order(params)
        if res.get("code") in (0, 101205):
            return {"ok": True}
        return {"ok": False, "msg": res.get("msg", "Error closing")}

    def handle_tp1_hit(self, symbol: str, direction: str, total_qty: float, entry_price: float, tp2_price: float) -> dict:
        log.info(f"Đang xóa lệnh SL/TP cũ của {symbol}...")
        self.cancel_all_orders(symbol)

        half_qty = self._format_qty(symbol, total_qty * 0.5)
        close_res = self.close_position(symbol, half_qty, direction)
        if not close_res.get("ok"):
            log.error(f"Lỗi khi chốt 50% vị thế: {close_res.get('msg')}")
            return close_res

        self.cancel_all_orders(symbol)
        remaining_qty = self._format_qty(symbol, max(0.0, total_qty - half_qty))

        if entry_price <= 0:
            open_pos = self.get_open_positions(symbol)
            if open_pos:
                entry_price = open_pos[0].get("entry", 0)

        self.set_runner_sl_tp(
            symbol=symbol,
            direction=direction,
            qty=remaining_qty,
            sl_price=self.breakeven_price(direction, entry_price),
            tp_price=tp2_price,
        )
        return {"ok": True}

    def handle_tp_level_hit(self, symbol: str, direction: str, level: int, original_qty: float,
                             close_pct: float, entry_price: float,
                             prev_tp_price: float, next_tp_price) -> dict:
        open_pos = self.get_open_positions(symbol)
        if not open_pos:
            return {"ok": False, "msg": f"Không còn vị thế {symbol} để xử lý TP{level}"}
        current_qty = float(open_pos[0].get("qty", 0))
        if entry_price <= 0:
            entry_price = float(open_pos[0].get("entry", 0))

        spec = self.get_contract_spec(symbol)
        clean_sym = symbol.replace("-", "").upper()
        base_asset = clean_sym.replace("USDT", "")
        MIN_QTY_MAP = {"BTC": 0.001, "ETH": 0.01, "BNB": 0.1, "SOL": 0.1, "XRP": 10.0, "DOGE": 100.0}
        min_qty = max(float(spec.get("tradeMinQuantity", 0) or 0), MIN_QTY_MAP.get(base_asset, 0.0001))
        min_notional = max(5.0, float(spec.get("tradeMinUSDT", 0) or 0))
        cur_px = self.get_latest_price(symbol) or entry_price or 1.0

        close_qty = self._format_qty(symbol, min(current_qty, original_qty * close_pct))
        remaining_qty = self._format_qty(symbol, max(0.0, current_qty - close_qty))
        be_p = self._format_price(symbol, self.breakeven_price(direction, entry_price)) or self.breakeven_price(direction, entry_price)

        # Nếu là TP1 (còn TP2 phía trước) nhưng kích thước lệnh quá nhỏ không đủ tách 2 nửa trên sàn:
        # KHÔNG hủy SL/TP và KHÔNG đóng non — giữ 100% gồng tới TP2 & dời SL về Hòa vốn!
        cannot_split = (
            next_tp_price is not None
            and (
                close_qty < min_qty
                or remaining_qty < min_qty
                or (close_qty * cur_px) < min_notional
                or (remaining_qty * cur_px) < min_notional
            )
        )
        if cannot_split:
            raw_tps = [{"level": 2, "price": float(next_tp_price), "close_pct": 1.0}]
            upd = self.update_sl_and_keep_remaining_tps(symbol, direction, current_qty, be_p, raw_tps, cur_px)
            return {
                "ok": bool(upd.get("sl_ok", True)),
                "skipped_partial_moved_be": True,
                "closed_qty": 0.0,
                "remaining_qty": current_qty,
                "new_sl": be_p,
                "msg": f"Khối lượng nhỏ không đủ tách 50% -> Dời SL về Hòa vốn (${be_p}), giữ 100% chạy TP2 (${next_tp_price})",
            }

        if close_qty <= 0:
            return {"ok": False, "msg": f"Qty tính ra để chốt TP{level} <= 0"}

        # Chốt phần TP trước rồi mới hủy/cập nhật SL/TP mới (tránh mất bảo vệ nếu close_position lỗi)
        close_res = self.close_position(symbol, close_qty, direction)
        if not close_res.get("ok"):
            log.error(f"Lỗi khi chốt TP{level} ({close_qty} {symbol}): {close_res.get('msg')}")
            return close_res

        self.cancel_all_orders(symbol)
        if remaining_qty <= 0 or next_tp_price is None:
            return {"ok": True, "msg": f"Đã đóng hết vị thế {symbol} tại TP{level}", "closed_all": True}

        new_sl = be_p if level == 1 else (self._format_price(symbol, prev_tp_price) or prev_tp_price)
        self.set_runner_sl_tp(symbol, direction, remaining_qty, new_sl, next_tp_price)
        return {
            "ok": True,
            "msg": f"Đã chốt TP{level}, dời SL lên {new_sl}, target tiếp theo {next_tp_price}",
            "closed_qty": close_qty,
            "remaining_qty": remaining_qty,
            "new_sl": new_sl,
        }

    # ════════════════════════════════════════════════════════════════════
    # CƠ CHẾ LỌC NHIỄU, KÉO SL HÒA VỐN SỚM (EARLY BREAKEVEN) VÀ GỒNG LÃI
    # ════════════════════════════════════════════════════════════════════
    def manage_position_dynamic(self, symbol: str, analysis_result: dict, leverage: int = 10, redis_client=None) -> dict:
        if self._is_precious_metal(symbol):
            leverage = 50
        else:
            leverage = max(10, int(leverage or 10))
        open_positions = self.get_open_positions(symbol)
        if not open_positions:
            return {"action": "NONE", "msg": "Không có vị thế mở."}
            
        pos = open_positions[0]
        direction = pos.get("direction")
        entry_price = float(pos.get("entry", 0))
        current_qty = float(pos.get("qty", 0))
        current_price = self.get_latest_price(symbol)
        
        # Lấy thông tin SL/TP hiện tại TRÊN SÀN (quan trọng để tránh spam)
        # [FIX v6.11] get_trigger_orders() giờ trả None khi GỌI SÀN THẤT BẠI
        # (khác với {} khi gọi thành công nhưng không có lệnh). Nếu None, KHÔNG
        # được coi current_sl=0 rồi hành động — bỏ qua chu kỳ này, đợi lần poll
        # sau, để tránh cancel+đặt lại SL/TP một cách dư thừa mỗi khi sàn chập chờn.
        trigger_orders = self.get_trigger_orders()
        if trigger_orders is None:
            log.warning("⚠️ %s: Không đọc được lệnh chờ hiện tại trên sàn -> bỏ qua chu kỳ này, không hành động.", symbol)
            return {"action": "NONE", "msg": "Không xác thực được trạng thái SL/TP hiện tại trên sàn."}
        clean_sym = symbol.replace("-", "")
        active_orders = trigger_orders.get(symbol, {}) or trigger_orders.get(clean_sym, {})
        current_sl = float(active_orders.get("sl", 0))

        
        if entry_price <= 0 or current_price <= 0:
            return {"action": "NONE", "msg": "Giá bị lỗi."}

        # Tính toán ROE và lợi nhuận bằng USD
        plan = analysis_result.get("plan", {})
        tp1_price = float(plan.get("tp1", 0))
        tp2_price = float(plan.get("tp2", 0))
        
        if direction == "LONG":
            gross_pnl_usd = (current_price - entry_price) * current_qty
            raw_price_pct = ((current_price - entry_price) / entry_price) * 100
            roe = raw_price_pct * leverage
            dist_to_tp1 = abs(tp1_price - entry_price) if (tp1_price > 0 and tp1_price > entry_price) else 0
            curr_dist = current_price - entry_price
        else:
            gross_pnl_usd = (entry_price - current_price) * current_qty
            raw_price_pct = ((entry_price - current_price) / entry_price) * 100
            roe = raw_price_pct * leverage
            dist_to_tp1 = abs(entry_price - tp1_price) if (tp1_price > 0 and tp1_price < entry_price) else 0
            curr_dist = entry_price - current_price

        # Tính toán phí giao dịch sàn BingX 2 chiều ước tính (~0.05% x 2 = 0.10% tổng giá trị vị thế mở)
        notional_usd = current_qty * entry_price
        fee_usd = notional_usd * 0.0010  # 0.10% tổng phí 2 chiều (mở + đóng)

        # Lấy thêm PnL từ BingX API nếu có để tính chính xác hơn
        unrealized_pnl_api = float(pos.get("pnl", 0))
        if unrealized_pnl_api != 0:
            gross_pnl_usd = max(gross_pnl_usd, unrealized_pnl_api)

        net_pnl_usd = gross_pnl_usd - fee_usd

        # 0. CHỐT LỜI CẤP THỦ CÔNG / CỦA BINGX (Cho phép vị thế chạy tới các mốc TP1-TP4 chuẩn)
        # Bỏ quy tắc ép chốt lời quá sớm $0.20 USD để lệnh gồng đủ tỷ lệ Risk:Reward 1:1.5 -> 1:3 chuẩn thuật toán.

        # 0.5. TỰ ĐỘNG DỜI SL VỀ HÒA VỐN (BREAKEVEN) TRONG CỬA SỔ RA TIN VĨ MÔ LỚN (NFP/CPI/FOMC)
        # Chỉ kích hoạt khi thực sự trong cửa sổ ngắt lệnh (pause_trading=True) VÀ vị thế đã đạt lãi tối thiểu +0.8%
        # Tránh dời SL quá sớm khi lãi +0.02% gây dính SL hòa vốn oan do biến động tự nhiên.
        try:
            from worker.economic_calendar import news_risk_adjustment
            news_risk = news_risk_adjustment()
            if news_risk.get("pause_trading"):
                pnl_pct = ((current_price - entry_price) / entry_price * 100) if direction == "LONG" else ((entry_price - current_price) / entry_price * 100)
                if pnl_pct >= 0.8:
                    be_p = self.breakeven_price(direction, entry_price)
                    sl_at_be = (direction == "LONG" and current_sl >= be_p - (entry_price * 0.0002)) or \
                               (direction == "SHORT" and current_sl > 0 and current_sl <= be_p + (entry_price * 0.0002))
                    if not sl_at_be:
                        log.info(f"📰 [MACRO BREAKEVEN PROTECTION] {symbol} {direction}: Đang trong cửa sổ ra tin vĩ mô lớn ({news_risk.get('event')}) & Vị thế đạt lãi +{pnl_pct:.2f}% >= +0.8% -> Dời SL về Hòa Vốn (${be_p:.4f}) bảo vệ vốn!")
                        self.update_sl_and_keep_remaining_tps(symbol, direction, current_qty, be_p, plan.get("tp_levels", []), current_price)
                        return {
                            "action": "NEWS_BREAKEVEN",
                            "new_sl": be_p,
                            "msg": f"Đã tự động dời SL về Hòa Vốn (${be_p}) bảo vệ trước tin vĩ mô lớn ({news_risk.get('event')})."
                        }
        except Exception as ex_news_be:
            log.debug("Lỗi news breakeven protection: %s", ex_news_be)

        # 1. TÍNH TOÁN CÁC MỐC TP TRADERSZ (TP1 = 50% EQ -> DỜI SL HÒA VỐN, TP2 = BIÊN ĐỐI DIỆN)
        raw_tp_levels = plan.get("tp_levels", [])
        if (not raw_tp_levels or not isinstance(raw_tp_levels, list)) and (tp1_price > 0 or tp2_price > 0):
            raw_tp_levels = []
            if tp1_price > 0:
                raw_tp_levels.append({"level": 1, "price": tp1_price, "close_pct": 0.50})
            if tp2_price > 0 and tp2_price != tp1_price:
                raw_tp_levels.append({"level": 2, "price": tp2_price, "close_pct": 0.50})

        tp_list = []
        if raw_tp_levels and isinstance(raw_tp_levels, list):
            for lvl in raw_tp_levels:
                if isinstance(lvl, dict):
                    p = float(lvl.get("price", 0) or 0)
                    if p > 0:
                        tp_list.append(p)

        if not tp_list:
            for k in ["tp1", "tp2"]:
                val = float(plan.get(k, 0) or 0)
                if val > 0 and val not in tp_list:
                    tp_list.append(val)

        # Lọc và sắp xếp danh sách TP đúng hướng vị thế
        if direction == "LONG":
            tp_list = sorted([p for p in tp_list if p > entry_price])
        else:  # SHORT
            tp_list = sorted([p for p in tp_list if p < entry_price], reverse=True)

        if tp_list:
            be_price = self._format_price(symbol, self.breakeven_price(direction, entry_price)) or self.breakeven_price(direction, entry_price)
            tolerance = entry_price * 0.0002  # 0.02% đệm sai số giá

            # Duyệt từ mốc xa nhất xuống mốc gần nhất để tìm milestone 50% đã đạt
            for i in range(len(tp_list) - 1, -1, -1):
                prev_p = tp_list[i - 1] if i > 0 else entry_price
                curr_p = tp_list[i]

                if direction == "LONG":
                    milestone_50 = prev_p + 0.5 * (curr_p - prev_p)
                    reached_50 = (current_price >= milestone_50)
                    target_sl = be_price if i == 0 else (self._format_price(symbol, prev_p) or prev_p)
                    sl_already_set = (current_sl > 0) and (current_sl >= target_sl - tolerance)
                else:  # SHORT
                    milestone_50 = prev_p - 0.5 * (prev_p - curr_p)
                    reached_50 = (current_price <= milestone_50)
                    target_sl = be_price if i == 0 else (self._format_price(symbol, prev_p) or prev_p)
                    sl_already_set = (current_sl > 0) and (current_sl <= target_sl + tolerance)

                if reached_50:
                    if sl_already_set:
                        log.debug(f"🛡️ {symbol}: Giá đạt 50% chặng TP{i+1}, nhưng SL trên sàn ({current_sl}) đã ở/vượt mốc {target_sl}.")
                        break

                    cooldown_key = f"SL_COOLDOWN_{symbol}_{direction}"
                    if redis_client:
                        try:
                            if redis_client.get(cooldown_key):
                                log.warning(f"⏳ Cooldown dời SL đang kích hoạt cho {symbol} -> Bỏ qua lần dời SL này để tránh spam API.")
                                break
                        except Exception as e:
                            log.warning("Lỗi cooldown Redis: %s", e)

                    log.info(f"🛡️ [TRADERSZ BREAKEVEN] {symbol}: Đạt 50% chặng đường tới TP{i+1} (${curr_p}). Dời SL về {target_sl} (cũ: {current_sl})!")
                    upd = self.update_sl_and_keep_remaining_tps(symbol, direction, current_qty, target_sl, raw_tp_levels, current_price)
                    if not upd.get("sl_ok"):
                        log.warning(f"⚠️ {symbol}: Dời SL về {target_sl} thất bại -> thử lại chu kỳ sau.")
                        break

                    if redis_client:
                        try:
                            redis_client.setex(cooldown_key, 120, "1")
                        except Exception:
                            pass

                    action_name = "BREAKEVEN" if i == 0 else "TRAILING_SL"
                    return {
                        "action": action_name,
                        "level": i + 1,
                        "new_sl": self._format_price(symbol, target_sl) or target_sl,
                        "msg": f"Đã dời SL về {target_sl} khi đi được 50% chặng TP{i+1}."
                    }

        # 2. BẢO VỆ CẮT LỖ AN TOÀN TỐI ĐA (-35% ROE FAILSAFE) — BỎ HOÀN TOÀN CHỐT LỜI NHANH & CHANDELIER
        if roe <= -35.0:
            log.info(f"🛑 [MAX LOSS PROTECTION] {symbol} {direction}: Lỗ ROE {roe:.2f}% <= -35.0% -> CẮT LỖ BẢO VỆ TÀI KHOẢN!")
            self.close_position(symbol, current_qty, direction)
            self.cancel_all_orders(symbol)
            if redis_client:
                try:
                    redis_client.delete(f"PEAK_PRICE_{symbol}_{direction}")
                    redis_client.delete(f"SL_COOLDOWN_{symbol}_{direction}")
                except Exception:
                    pass
            return {"action": "CLOSE", "type": "CẮT LỖ BẢO VỆ TÀI KHOẢN (ROE <= -35.0%)", "roe": roe}

        # Giữ vị thế chạy tự do tới TP1 (50% EQ -> Hòa vốn) & TP2 (Biên đối diện) hoặc SL râu nến
        return {"action": "HOLD", "msg": "Giữ lệnh chạy theo kế hoạch TP/SL TraderSZ."}

