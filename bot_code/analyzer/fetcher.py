# ═══════════════════════════════════════════════════════════
# 1. CRYPTO FETCHER — v6.1 (Session Pool + Retry + Safe Fallback)
# ═══════════════════════════════════════════════════════════
from datetime import datetime
import logging
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("analyzer.fetcher")


def _make_session(retries=2, backoff=0.3, timeout=None):
    """
    Tạo requests.Session với connection pooling và retry tự động.
    """
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=4,
        pool_maxsize=10,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class CryptoFetcher:
    """
    Crypto data — Ưu tiên Bybit, Fallback sang BingX.
    """
    BGX = "https://open-api.bingx.com/openApi"
    BBT = "https://api.bybit.com"
    HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    BGX_IV = {"15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}
    BBT_IV = {"15m": "15", "1h": "60", "4h": "240", "1d": "D"}

    def __init__(self):
        self._session = _make_session(retries=2, backoff=0.3)

    def _bgx(self, s):
        s = str(s).strip().upper()
        if '-' in s: return s
        if s.endswith('USDT'):
            return s[:-4] + '-USDT'
        return s

    def _bbt(self, s):
        s = str(s).strip().upper()
        return s.replace('-', '')

    def _tbvol_ratio(self, closes):
        """
        Tính ratio taker buy volume động thay vì hardcode 52%.
        """
        if len(closes) < 6:
            return 0.52
        recent = closes[-1]
        prev   = closes[-6]
        if prev <= 0:
            return 0.52
        pct_change = (recent - prev) / prev
        ratio = 0.51 + pct_change * 3.5
        return round(max(0.40, min(0.62, ratio)), 3)

    def price(self, symbol):
        """Lấy giá Last Price"""
        if not symbol:
            return 0.0

        bbt_sym = self._bbt(symbol)
        bgx_sym = self._bgx(symbol)

        # Bybit
        try:
            r = self._session.get(
                self.BBT + "/v5/market/tickers",
                params={"category": "linear", "symbol": bbt_sym},
                headers=self.HDR, timeout=6)
            if r.status_code == 200:
                d = r.json()
                if d.get("retCode") == 0:
                    lst = d.get("result", {}).get("list", [])
                    if lst and lst[0].get("symbol") == bbt_sym:
                        p = float(lst[0].get("lastPrice", 0))
                        if p > 0:
                            return round(p, 4)
                    else:
                        log.warning("Bybit sai symbol cho %s", bbt_sym)
        except Exception as e:
            log.debug("Bybit ticker err %s: %s", bbt_sym, e)

        # BingX Fallback
        try:
            r = self._session.get(
                self.BGX + "/swap/v2/quote/ticker",
                params={"symbol": bgx_sym},
                headers=self.HDR, timeout=6)
            if r.status_code == 200:
                d = r.json()
                if d.get("code") == 0 and d.get("data"):
                    data = d["data"]
                    if isinstance(data, dict) and data.get("symbol") == bgx_sym:
                        p = float(data.get("lastPrice") or data.get("markPrice") or 0)
                        if p > 0:
                            return round(p, 4)
                    elif isinstance(data, list):
                        for item in data:
                            if item.get("symbol") == bgx_sym:
                                p = float(item.get("lastPrice", 0))
                                if p > 0:
                                    return round(p, 4)
        except Exception as e:
            log.debug("BingX ticker err %s: %s", bgx_sym, e)

        log.error("KHÔNG THỂ LẤY GIÁ CHO %s", symbol)
        return 0.0

    def klines(self, symbol, interval, limit=150):
        """Lấy Klines — Bybit trước, BingX fallback"""
        if not symbol:
            return None

        bbt_sym = self._bbt(symbol)
        bgx_sym = self._bgx(symbol)
        bbt_iv  = self.BBT_IV.get(interval, "60")
        bgx_iv  = self.BGX_IV.get(interval, "1h")

        # Bybit Klines
        try:
            r = self._session.get(
                self.BBT + "/v5/market/kline",
                params={"category": "linear", "symbol": bbt_sym,
                        "interval": bbt_iv, "limit": limit},
                headers=self.HDR, timeout=10)
            if r.status_code == 200:
                d = r.json()
                if d.get("retCode") == 0:
                    raw = list(reversed(d["result"]["list"]))
                    if len(raw) >= 20:
                        closes = [float(c[4]) for c in raw]
                        vols   = [max(float(c[5]), 0.001) for c in raw]
                        tbv_ratio = self._tbvol_ratio(closes)
                        return {
                            "open":          [float(c[1]) for c in raw],
                            "high":          [float(c[2]) for c in raw],
                            "low":           [float(c[3]) for c in raw],
                            "close":         closes,
                            "volume":        vols,
                            "taker_buy_vol": [v * tbv_ratio for v in vols],
                        }
        except Exception as e:
            log.debug("Bybit klines err %s %s: %s", bbt_sym, interval, e)

        # BingX Klines Fallback
        try:
            r = self._session.get(
                self.BGX + "/swap/v3/quote/klines",
                params={"symbol": bgx_sym, "interval": bgx_iv, "limit": limit},
                headers=self.HDR, timeout=10)
            if r.status_code == 200:
                d = r.json()
                if d.get("code") == 0:
                    data = d.get("data", [])
                    if len(data) >= 20:
                        closes = [float(c.get("close", 0)) for c in data]
                        vols   = [max(float(c.get("volume", 0)), 0.001) for c in data]
                        tbv_ratio = self._tbvol_ratio(closes)
                        tbvols = []
                        for i, c in enumerate(data):
                            tbv = c.get("takerBuyVolume") or c.get("taker_buy_volume")
                            if tbv and float(tbv) > 0:
                                tbvols.append(float(tbv))
                            else:
                                tbvols.append(vols[i] * tbv_ratio)
                        return {
                            "open":          [float(c.get("open", closes[i])) for i, c in enumerate(data)],
                            "high":          [float(c.get("high", closes[i])) for i, c in enumerate(data)],
                            "low":           [float(c.get("low",  closes[i])) for i, c in enumerate(data)],
                            "close":         closes,
                            "volume":        vols,
                            "taker_buy_vol": tbvols,
                        }
        except Exception as e:
            log.debug("BingX klines err %s %s: %s", bgx_sym, interval, e)

        log.error("Không lấy được klines cho %s [%s]", symbol, interval)
        return None

    def funding_rate(self, symbol):
        if not symbol:
            return 0.0
        try:
            r = self._session.get(
                self.BBT + "/v5/market/tickers",
                params={"category": "linear", "symbol": self._bbt(symbol)},
                headers=self.HDR, timeout=5)
            d = r.json()
            if d.get("retCode") == 0:
                lst = d.get("result", {}).get("list", [])
                if lst and lst[0].get("symbol") == self._bbt(symbol):
                    return float(lst[0].get("fundingRate", 0)) * 100
        except Exception:
            pass
        return 0.0

    def open_interest(self, symbol):
        if not symbol:
            return 0.0, 0.0
        try:
            r = self._session.get(
                self.BBT + "/v5/market/open-interest",
                params={"category": "linear", "symbol": self._bbt(symbol),
                        "intervalTime": "1h", "limit": 3},
                headers=self.HDR, timeout=5)
            d = r.json()
            if d.get("retCode") == 0:
                lst = d.get("result", {}).get("list", [])
                if len(lst) >= 2:
                    a = float(lst[0].get("openInterest", 0))
                    b = float(lst[1].get("openInterest", 0))
                    return a, round((a - b) / b * 100, 3) if b else 0
        except Exception:
            pass
        return 0.0, 0.0

    def order_book(self, symbol: str, depth: int = 50) -> dict:
        """
        Lấy Order Book (Depth) từ Bybit → BingX fallback.
        """
        EMPTY = {
            "bids": [], "asks": [],
            "bid_total": 0.0, "ask_total": 0.0,
            "ratio": 1.0, "imbalance": 0.0,
            "spread_pct": 0.0, "best_bid": 0.0, "best_ask": 0.0,
            "bid_walls": [], "ask_walls": [],
            "mid_price": 0.0, "ok": False,
        }
        if not symbol:
            return EMPTY

        bbt_sym = self._bbt(symbol)
        bgx_sym = self._bgx(symbol)

        # Bybit Order Book
        try:
            r = self._session.get(
                self.BBT + "/v5/market/orderbook",
                params={"category": "linear", "symbol": bbt_sym, "limit": depth},
                headers=self.HDR, timeout=8)
            if r.status_code == 200:
                d = r.json()
                if d.get("retCode") == 0:
                    raw_bids = [[float(p), float(q)] for p, q in d["result"].get("b", [])]
                    raw_asks = [[float(p), float(q)] for p, q in d["result"].get("a", [])]
                    return self._parse_orderbook(raw_bids, raw_asks)
        except Exception as e:
            log.debug("Bybit orderbook %s: %s", bbt_sym, e)

        # BingX Fallback
        try:
            r = self._session.get(
                self.BGX + "/swap/v2/quote/depth",
                params={"symbol": bgx_sym, "limit": depth},
                headers=self.HDR, timeout=8)
            if r.status_code == 200:
                d = r.json()
                if d.get("code") == 0:
                    data = d.get("data", {})
                    raw_bids = [[float(p), float(q)] for p, q in data.get("bids", [])]
                    raw_asks = [[float(p), float(q)] for p, q in data.get("asks", [])]
                    return self._parse_orderbook(raw_bids, raw_asks)
        except Exception as e:
            log.debug("BingX orderbook %s: %s", bgx_sym, e)

        return EMPTY

    @staticmethod
    def _parse_orderbook(raw_bids: list, raw_asks: list) -> dict:
        if not raw_bids or not raw_asks:
            return {"bids":[], "asks":[], "bid_total":0, "ask_total":0,
                    "ratio":1.0, "imbalance":0.0, "spread_pct":0.0,
                    "best_bid":0.0, "best_ask":0.0, "bid_walls":[], "ask_walls":[],
                    "mid_price":0.0, "ok":False}

        bids = sorted(raw_bids, key=lambda x: x[0], reverse=True)
        asks = sorted(raw_asks, key=lambda x: x[0])

        best_bid = bids[0][0] if bids else 0.0
        best_ask = asks[0][0] if asks else 0.0
        mid      = (best_bid + best_ask) / 2 if best_bid and best_ask else 0.0
        spread   = round((best_ask - best_bid) / mid * 100, 4) if mid else 0.0

        bid_total = sum(p * q for p, q in bids)
        ask_total = sum(p * q for p, q in asks)
        total     = bid_total + ask_total
        ratio     = round(bid_total / ask_total, 3) if ask_total > 0 else 1.0
        imbalance = round((bid_total - ask_total) / total * 100, 1) if total > 0 else 0.0

        def find_walls(orders, threshold_mult=2.5):
            if len(orders) < 3:
                return []
            sizes  = [q for _, q in orders]
            avg_q  = sum(sizes) / len(sizes)
            cutoff = avg_q * threshold_mult
            walls  = []
            for price, qty in orders:
                if qty >= cutoff:
                    walls.append({"price": round(price, 2),
                                  "qty": round(qty, 4),
                                  "usd": round(price * qty, 0),
                                  "mult": round(qty / avg_q, 1)})
            return sorted(walls, key=lambda x: x["usd"], reverse=True)[:5]

        bid_walls = find_walls(bids)
        ask_walls = find_walls(asks)

        return {
            "bids":      bids[:20],
            "asks":      asks[:20],
            "best_bid":  round(best_bid, 4),
            "best_ask":  round(best_ask, 4),
            "mid_price": round(mid, 4),
            "spread_pct": spread,
            "bid_total": round(bid_total, 0),
            "ask_total": round(ask_total, 0),
            "ratio":     ratio,
            "imbalance": imbalance,
            "bid_walls": bid_walls,
            "ask_walls": ask_walls,
            "ok":        True,
        }

    def liquidation_levels(self, symbol: str, current_price: float) -> dict:
        """
        Tính các ngưỡng thanh lý theo đòn bẩy.
        """
        EMPTY = {"long_liq_levels": [], "short_liq_levels": [],
                 "dominant_side": "NEUTRAL", "cascade_risk": False,
                 "long_ratio": 0.5, "short_ratio": 0.5, "ok": False}

        if not symbol or not current_price:
            return EMPTY

        try:
            r = self._session.get(
                self.BBT + "/v5/market/account-ratio",
                params={"category": "linear", "symbol": self._bbt(symbol),
                        "period": "5min", "limit": 1},
                headers=self.HDR, timeout=6)
            if r.status_code == 200:
                d = r.json()
                if d.get("retCode") == 0:
                    lst = d.get("result", {}).get("list", [])
                    if lst:
                        buy_ratio  = float(lst[0].get("buyRatio",  0.5))
                        sell_ratio = float(lst[0].get("sellRatio", 0.5))
                    else:
                        buy_ratio = sell_ratio = 0.5
                else:
                    buy_ratio = sell_ratio = 0.5
            else:
                buy_ratio = sell_ratio = 0.5
        except Exception:
            buy_ratio = sell_ratio = 0.5

        dominant = "LONG" if buy_ratio > sell_ratio else "SHORT"

        leverages = [5, 10, 20, 50, 100]
        p = current_price

        long_liqs  = []
        short_liqs = []
        for lev in leverages:
            liq_long  = round(p * (1 - 0.9 / lev), 2)
            liq_short = round(p * (1 + 0.9 / lev), 2)
            long_liqs.append({"leverage": lev, "price": liq_long,
                               "distance_pct": round((p - liq_long) / p * 100, 2)})
            short_liqs.append({"leverage": lev, "price": liq_short,
                                "distance_pct": round((liq_short - p) / p * 100, 2)})

        cascade_levels = [l["price"] for l in long_liqs if l["leverage"] in (20, 50)]
        cascade_risk   = any(abs(p - liq) / p < 0.02 for liq in cascade_levels)

        return {
            "long_liq_levels":  long_liqs,
            "short_liq_levels": short_liqs,
            "dominant_side":    dominant,
            "cascade_risk":     cascade_risk,
            "long_ratio":       round(buy_ratio, 3),
            "short_ratio":      round(sell_ratio, 3),
            "ok":               True,
        }


# ═══════════════════════════════════════════════════════════
# 2. STOCK / GOLD FETCHER — v6.1
# ═══════════════════════════════════════════════════════════

class StockFetcher:
    HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
           "Accept": "application/json",
           "Referer": "https://finance.yahoo.com/"}
    CGK    = "https://api.coingecko.com/api/v3"
    # Sửa GC%3DF thành GC=F để API requests không bị lỗi encode
    YAHOO  = {"TSLA": "TSLA", "NVDA": "NVDA", "SPY": "SPY", "QQQ": "QQQ", "NCCOGOLD2USD-USDT": "GC=F"}
    YH_IV  = {"15m": "15m", "1h": "1h", "4h": "1h", "1d": "1d"}
    YH_RNG = {"15m": "5d", "1h": "30d", "4h": "30d", "1d": "6mo"}
    PLIM   = {"TSLA": (10, 5000), "NVDA": (10, 5000),
               "SPY": (100, 1500), "QQQ": (100, 1500), "NCCOGOLD2USD-USDT": (1500, 6000)}

    _SYNTHETIC_PRICES = {"NCCOGOLD2USD-USDT": 2350.0, "SPY": 540.0, "TSLA": 220.0, "NVDA": 120.0}

    def __init__(self):
        self._session = _make_session(retries=2, backoff=0.5)

    def price(self, symbol):
        lo, hi = self.PLIM.get(symbol, (0, 1e9))

        # GOLD
        if symbol == "NCCOGOLD2USD-USDT":
            try:
                r = self._session.get(
                    self.CGK + "/simple/price",
                    params={"ids": "pax-gold,tether-gold", "vs_currencies": "usd"},
                    headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
                r.raise_for_status()
                d = r.json()
                for tok in ["pax-gold", "tether-gold"]:
                    p = float(d.get(tok, {}).get("usd", 0))
                    if lo < p < hi:
                        log.info("Gold CoinGecko %s: $%.2f", tok, p)
                        return round(p, 2)
            except Exception as e:
                log.warning("CoinGecko gold: %s", e)

            for base in ["query1", "query2"]:
                try:
                    r = self._session.get(
                        f"https://{base}.finance.yahoo.com/v8/finance/chart/GC=F"
                        "?interval=1m&range=1d",
                        headers=self.HDR, timeout=10)
                    if r.ok:
                        j = r.json()
                        # Kiểm tra an toàn xem có "result" không
                        if j.get("chart", {}).get("result"):
                            data = j["chart"]["result"][0]
                            meta = data.get("meta", {})
                            for key in ["regularMarketPrice", "chartPreviousClose"]:
                                p = float(meta.get(key, 0))
                                if lo < p < hi:
                                    log.info("Gold Yahoo %s [%s]: $%.2f", base, key, p)
                                    return round(p, 2)
                            closes = [c for c in data["indicators"]["quote"][0].get("close", [])
                                      if c and lo < float(c) < hi]
                            if closes:
                                return round(closes[-1], 2)
                except Exception as e:
                    log.warning("Yahoo GC=F %s: %s", base, e)

            return 0.0

        # STOCKS
        ticker = self.YAHOO.get(symbol, symbol)
        for base in ["query1", "query2"]:
            try:
                r = self._session.get(
                    f"https://{base}.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1m&range=1d",
                    headers=self.HDR, timeout=10)
                if r.ok:
                    j = r.json()
                    # Kiểm tra an toàn xem có "result" không
                    if j.get("chart", {}).get("result"):
                        data = j["chart"]["result"][0]
                        meta = data.get("meta", {})
                        p = float(meta.get("regularMarketPrice", 0))
                        if lo < p < hi:
                            return round(p, 2)
                        closes = [c for c in data["indicators"]["quote"][0].get("close", [])
                                  if c and lo < float(c) < hi]
                        if closes:
                            return round(closes[-1], 2)
            except Exception as e:
                log.warning("Yahoo price %s %s: %s", symbol, base, e)

        return 0.0

    def klines(self, symbol, interval, limit=100):
        lo, hi  = self.PLIM.get(symbol, (0, 1e9))
        ticker  = self.YAHOO.get(symbol, symbol)
        yh_iv   = self.YH_IV.get(interval, "1h")
        yh_rng  = self.YH_RNG.get(interval, "30d")

        for base in ["query1", "query2"]:
            try:
                r = self._session.get(
                    f"https://{base}.finance.yahoo.com/v8/finance/chart/{ticker}"
                    f"?interval={yh_iv}&range={yh_rng}",
                    headers=self.HDR, timeout=15)
                if r.status_code != 200:
                    continue
                    
                j = r.json()
                # Kiểm tra an toàn để fix lỗi sập NoneType
                if not j.get("chart", {}).get("result"):
                    continue
                    
                q = j["chart"]["result"][0]["indicators"]["quote"][0]

                def clean(lst):
                    return [float(x) for x in lst if x is not None and lo < float(x) < hi]

                closes  = clean(q.get("close",  []))
                highs   = clean(q.get("high",   []))
                lows    = clean(q.get("low",    []))
                opens   = clean(q.get("open",   []))
                volumes = [float(x) if x else 1.0 for x in q.get("volume", [])]

                if len(closes) < 20:
                    continue

                n = min(len(closes), len(highs), len(lows), len(opens), len(volumes), limit)
                c = closes[-n:]
                h = highs[-n:]   if len(highs)   >= n else c
                l = lows[-n:]
                o = opens[-n:]   if len(opens)   >= n else c
                v = volumes[-n:] if len(volumes) >= n else [1.0] * n

                return {"open": o, "high": h, "low": l, "close": c,
                        "volume": v, "taker_buy_vol": [x * 0.52 for x in v]}
            except Exception as e:
                log.warning("Yahoo klines %s %s %s: %s", symbol, interval, base, e)

        # Fallback synthetic klines
        is_open, _ = self.market_open() if symbol != "NCCOGOLD2USD-USDT" else self.is_gold_open()
        if is_open:
            log.error("🚫 Yahoo FAIL khi thị trường đang MỞ cho %s [%s] — bỏ qua TF này", symbol, interval)
            return None

        import random
        p = self.price(symbol)
        if p <= 0:
            p = self._SYNTHETIC_PRICES.get(symbol, 100.0)
        c = [p * (1 + random.uniform(-0.003, 0.003)) for _ in range(limit)]
        c[-1] = p
        v = [random.uniform(1000, 5000) for _ in range(limit)]
        return {"open": c[:], "high": [x * 1.005 for x in c],
                "low": [x * 0.995 for x in c], "close": c,
                "volume": v, "taker_buy_vol": [x * 0.52 for x in v]}

    def market_open(self):
        from datetime import timezone, timedelta
        et  = timezone(timedelta(hours=-
