# ═══════════════════════════════════════════════════════════
# 3. INDICATORS — v6.1
# ═══════════════════════════════════════════════════════════
import math


class Indicators:

    @staticmethod
    def _rsi_series(closes, period=14) -> list[float]:
        """
        Tính toàn bộ RSI series — dùng Wilder's smoothing (α = 1/period).
        """
        closes = [c for c in closes if c and c > 0]
        if len(closes) < period + 1:
            return [50.0]
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]

        # Seed
        gains = [max(d, 0) for d in deltas[:period]]
        losses = [max(-d, 0) for d in deltas[:period]]
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period

        rsi_vals = []
        if avg_loss == 0:
            rsi_vals.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_vals.append(round(100 - 100 / (1 + rs), 2))

        # Wilder's smoothing
        alpha = 1 / period
        for d in deltas[period:]:
            g = max(d, 0)
            l = max(-d, 0)
            avg_gain = avg_gain * (1 - alpha) + g * alpha
            avg_loss = avg_loss * (1 - alpha) + l * alpha
            if avg_loss == 0:
                rsi_vals.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_vals.append(round(100 - 100 / (1 + rs), 2))

        return rsi_vals

    @staticmethod
    def rsi(closes, period=14) -> float:
        vals = Indicators._rsi_series(closes, period)
        return vals[-1] if vals else 50.0

    @staticmethod
    def ema(closes, period) -> float:
        closes = [c for c in closes if c and c > 0]
        if not closes:
            return 0.0
        if len(closes) < period:
            return closes[-1]
        k = 2 / (period + 1)
        v = sum(closes[:period]) / period
        for p in closes[period:]:
            v = p * k + v * (1 - k)
        return round(v, 2)

    @staticmethod
    def _ema_series(values: list[float], period: int) -> list[float]:
        if len(values) < period:
            return [values[-1]] if values else []
        k   = 2 / (period + 1)
        out = [sum(values[:period]) / period]
        for v in values[period:]:
            out.append(v * k + out[-1] * (1 - k))
        return out

    @staticmethod
    def macd(closes) -> dict:
        """
        Calculates MACD series in O(n) complexity.
        """
        closes = [c for c in closes if c and c > 0]
        if len(closes) < 35:
            return {"cross": "NEUTRAL", "hist": 0, "macd_line": 0, "signal": 0}

        ema12_s = Indicators._ema_series(closes, 12)
        ema26_s = Indicators._ema_series(closes, 26)

        offset12 = 11
        offset26 = 25

        n_macd = len(ema26_s)
        macd_line = [
            ema12_s[i + (offset26 - offset12)] - ema26_s[i]
            for i in range(n_macd)
        ]

        if len(macd_line) < 9:
            val = macd_line[-1] if macd_line else 0
            return {"cross": "NEUTRAL" if abs(val) < 0.0001 else ("BULL_CROSS" if val > 0 else "BEAR_CROSS"),
                    "hist": round(val, 6), "macd_line": round(val, 6), "signal": 0}

        signal_s = Indicators._ema_series(macd_line, 9)
        macd_val = macd_line[-1]
        sig_val  = signal_s[-1]
        hist     = macd_val - sig_val

        return {
            "cross":      "BULL_CROSS" if macd_val > sig_val else "BEAR_CROSS",
            "hist":       round(hist, 6),
            "macd_line":  round(macd_val, 6),
            "signal":     round(sig_val, 6),
        }

    @staticmethod
    def bollinger(closes, period=20) -> dict:
        closes = [c for c in closes if c and c > 0]
        if len(closes) < period:
            return {"pct": 50, "squeeze": False, "upper": 0, "lower": 0, "mid": 0}
        sl  = closes[-period:]
        mid = sum(sl) / period
        if mid == 0:
            return {"pct": 50, "squeeze": False, "upper": 0, "lower": 0, "mid": 0}
        std   = math.sqrt(sum((x - mid) ** 2 for x in sl) / period)
        upper = mid + 2 * std
        lower = mid - 2 * std
        price = closes[-1]
        pct   = ((price - lower) / (upper - lower) * 100) if upper != lower else 50
        bw    = (upper - lower) / mid * 100
        return {
            "upper":   round(upper, 2),
            "lower":   round(lower, 2),
            "mid":     round(mid, 2),
            "pct":     round(max(0, min(100, pct)), 1),
            "squeeze": bw < 2.5,
            "bw":      round(bw, 3),
        }

    @staticmethod
    def fibonacci(highs, lows, closes, lookback=80) -> dict:
        n   = min(lookback, len(highs), len(lows), len(closes))
        rh  = highs[-n:]
        rl  = lows[-n:]
        sh, sl = max(rh), min(rl)
        price  = closes[-1]
        diff   = sh - sl
        if diff == 0:
            return {"levels": {}, "zone": "N/A", "in_golden": False,
                    "trend": "N/A", "swing_high": price, "swing_low": price}

        hi_idx = len(rh) - 1 - rh[::-1].index(sh)
        lo_idx = len(rl) - 1 - rl[::-1].index(sl)
        uptrend = hi_idx > lo_idx

        if uptrend:
            lvls = {
                "0.236": round(sh - diff * 0.236, 2),
                "0.382": round(sh - diff * 0.382, 2),
                "0.5":   round(sh - diff * 0.5,   2),
                "0.618": round(sh - diff * 0.618, 2),
                "0.786": round(sh - diff * 0.786, 2),
                "1.272": round(sh + diff * 0.272, 2),
                "1.618": round(sh + diff * 0.618, 2),
                "2.0":   round(sh + diff * 1.0,   2),
            }
        else:
            lvls = {
                "0.236": round(sl + diff * 0.236, 2),
                "0.382": round(sl + diff * 0.382, 2),
                "0.5":   round(sl + diff * 0.5,   2),
                "0.618": round(sl + diff * 0.618, 2),
                "0.786": round(sl + diff * 0.786, 2),
                "1.272": round(sl - diff * 0.272, 2),
                "1.618": round(sl - diff * 0.618, 2),
                "2.0":   round(sl - diff * 1.0,   2),
            }

        zone = "BETWEEN"
        for k, v in lvls.items():
            if abs(price - v) / price * 100 < 1.2:
                zone = "NEAR_" + k
                break

        lo2 = min(lvls["0.382"], lvls["0.618"])
        hi2 = max(lvls["0.382"], lvls["0.618"])

        return {
            "trend":      "UPTREND" if uptrend else "DOWNTREND",
            "swing_high": round(sh, 2),
            "swing_low":  round(sl, 2),
            "levels":     lvls,
            "zone":       zone,
            "in_golden":  lo2 <= price <= hi2,
        }

    @staticmethod
    def wyckoff(closes, highs, lows, volumes, lookback=80) -> dict:
        EMPTY = {"phase": "TRANSITION", "bias": "NEUTRAL",
                 "action": "⏳ CHỜ", "score_adj": 0,
                 "pos_range": 50, "vol_trend": "NEUTRAL", "events": []}

        closes  = [c for c in closes  if c and c > 0]
        highs   = [h for h in highs   if h and h > 0]
        lows    = [l for l in lows    if l and l > 0]
        volumes = [v for v in volumes if v is not None]

        n = min(lookback, len(closes), len(highs), len(lows), len(volumes))
        if n < 20:
            return EMPTY

        c = closes[-n:]
        h = highs[-n:]
        l = lows[-n:]
        v = volumes[-n:]

        mid    = n // 2
        ph, pl = max(h), min(l)
        prange = ph - pl
        price  = closes[-1]
        pos    = (price - pl) / prange if prange > 0 else 0.5

        avg_v1    = sum(v[:mid]) / mid if mid > 0 else 1
        avg_v2    = sum(v[mid:]) / max(n - mid, 1)
        avg_c1    = sum(c[:mid]) / mid
        avg_c2    = sum(c[mid:]) / max(n - mid, 1)
        price_up  = avg_c2 > avg_c1
        vol_expand = avg_v2 > avg_v1 * 1.2
        vol_dry    = avg_v2 < avg_v1 * 0.8
        is_tight   = ((max(h[-20:]) - min(l[-20:])) / prange < 0.3) if prange > 0 else False

        if   pos < 0.3 and vol_dry and is_tight:
            phase, bias, adj = "ACCUMULATION",    "BULLISH", +20
            action = "⏳ CHỜ BREAKOUT — Spring/SOS"
        elif price_up and vol_expand and pos > 0.5:
            phase, bias, adj = "MARKUP",          "BULLISH", +15
            action = "✅ MUA — Trend following"
        elif 0.3 < pos < 0.7 and vol_dry and not price_up:
            phase, bias, adj = "RE-ACCUMULATION", "BULLISH", +10
            action = "✅ MUA DẦN — Trước sóng kế"
        elif pos > 0.7 and vol_dry and is_tight:
            phase, bias, adj = "DISTRIBUTION",   "BEARISH", -20
            action = "⛔ TRÁNH LONG — UTAD/SOW"
        elif not price_up and vol_expand and pos < 0.5:
            phase, bias, adj = "MARKDOWN",        "BEARISH", -15
            action = "🔴 SHORT/BÁN — Tránh bắt đáy"
        else:
            phase, bias, adj = "TRANSITION",      "NEUTRAL",  0
            action = "⏳ CHỜ — Quan sát thêm"

        # Event detection
        events  = []
        rc      = closes[-20:]
        rv      = volumes[-20:]
        rh20    = highs[-20:]
        rl20    = lows[-20:]
        avg_rv  = sum(rv) / len(rv) if rv else 1

        for i in range(1, len(rc) - 1):
            if rl20[i] < min(rl20[:i] or [rl20[i]]) and rc[i] > rl20[i] * 1.005 and rv[i] < avg_rv:
                events.append("🌱 SPRING — Đáy giả, cơ hội mua")
            if rh20[i] > max(rh20[:i] or [rh20[i]]) and rc[i] < rh20[i] * 0.995 and rv[i] > avg_rv * 1.5:
                events.append("⛰️ UTAD — Đỉnh giả, cơ hội bán")
            if rc[i] > rc[i-1] * 1.01 and rv[i] > avg_rv * 1.8:
                events.append("💪 SOS — Sức mạnh xác nhận")
            if rc[i] < rc[i-1] * 0.99 and rv[i] > avg_rv * 1.8:
                events.append("⚠️ SOW — Điểm yếu xác nhận")

        return {
            "phase":     phase,
            "bias":      bias,
            "action":    action,
            "score_adj": adj,
            "pos_range": round(pos * 100, 1),
            "vol_trend": "EXPANDING" if vol_expand else "DRYING" if vol_dry else "NEUTRAL",
            "events":    list(dict.fromkeys(events))[:3],
        }

    @staticmethod
    def stoch_rsi(closes, period=14, smooth_k=3, smooth_d=3) -> dict:
        NEUTRAL = {"k": 50, "d": 50, "signal": "NEUTRAL", "score_adj": 0}
        closes = [c for c in closes if c and c > 0]
        if len(closes) < period * 2 + 3:
            return NEUTRAL

        rsi_vals = Indicators._rsi_series(closes, period)
        if len(rsi_vals) < period:
            return NEUTRAL

        stoch_vals = []
        for i in range(period - 1, len(rsi_vals)):
            window = rsi_vals[i - period + 1: i + 1]
            mn, mx = min(window), max(window)
            stoch_vals.append(
                (rsi_vals[i] - mn) / (mx - mn) * 100 if mx != mn else 50
            )

        if not stoch_vals:
            return NEUTRAL

        def sma(lst, n):
            return [sum(lst[max(0, i-n+1): i+1]) / min(i+1, n) for i in range(len(lst))]

        k_vals = sma(stoch_vals, smooth_k)
        d_vals = sma(k_vals, smooth_d)
        k = round(k_vals[-1], 1)
        d = round(d_vals[-1], 1) if d_vals else k

        if   k < 20 and d < 20: signal, adj = "OVERSOLD",   +12
        elif k > 80 and d > 80: signal, adj = "OVERBOUGHT", -12
        elif k > d  and k < 50: signal, adj = "BULL_CROSS",  +6
        elif k < d  and k > 50: signal, adj = "BEAR_CROSS",  -6
        else:                   signal, adj = "NEUTRAL",      0

        return {"k": k, "d": d, "signal": signal, "score_adj": adj}

    @staticmethod
    def cvd(closes, taker_buy_vols, total_vols) -> dict:
        NEUTRAL = {"trend": "NEUTRAL", "divergence": False, "score_adj": 0}
        n = min(len(closes), len(taker_buy_vols), len(total_vols))
        if n < 20:
            return EMPTY

        bv = [taker_buy_vols[i] for i in range(-20, 0)]
        sv = [total_vols[i] - taker_buy_vols[i] for i in range(-20, 0)]

        deltas = [bv[i] - sv[i] for i in range(20)]
        d1, d2 = sum(deltas[:10]), sum(deltas[10:])
        cvd_up = d2 > d1
        px_up  = closes[-1] > closes[-10] if len(closes) >= 10 else True
        div    = (cvd_up and not px_up) or (not cvd_up and px_up)

        if   cvd_up and px_up:             trend, adj = "BULLISH",     +15
        elif not cvd_up and not px_up:     trend, adj = "BEARISH",     -15
        elif div and not cvd_up and px_up: trend, adj = "BEARISH_DIV", -15
        elif div and cvd_up and not px_up: trend, adj = "BULLISH_DIV", +15
        else:                              trend, adj = "NEUTRAL",       0

        return {"trend": trend, "divergence": div, "score_adj": adj}

    @staticmethod
    def breakout_detector(closes, highs, lows, volumes, lookback=50) -> dict:
        NONE = {"type": "NONE", "strength": 0, "score_adj": 0, "desc": ""}
        if len(closes) < lookback:
            return NONE

        price   = closes[-1]
        resist  = max(highs[-lookback:-1])
        supprt  = min(lows[-lookback:-1])
        avg_vol = sum(volumes[-lookback:-1]) / (lookback - 1)
        vr      = volumes[-1] / avg_vol if avg_vol > 0 else 1
        rng     = highs[-1] - lows[-1]
        body    = abs(closes[-1] - closes[-2]) / rng * 100 if rng > 0 else 0

        if price > resist and vr >= 1.8 and body >= 55:
            return {"type": "BREAKOUT_UP",   "strength": min(100, int(vr*30 + body*0.4)),
                    "score_adj": +25,
                    "desc": f"Phá kháng cự ${round(resist,2)} | Vol {round(vr,1)}x"}
        if price < supprt and vr >= 1.8 and body >= 55:
            return {"type": "BREAKOUT_DOWN", "strength": min(100, int(vr*30 + body*0.4)),
                    "score_adj": -25,
                    "desc": f"Phá hỗ trợ ${round(supprt,2)} | Vol {round(vr,1)}x"}

        dr = (resist - price) / price * 100
        ds = (price - supprt) / price * 100
        if dr < 0.5 and vr >= 1.3:
            return {"type": "PRE_BREAKOUT_UP",   "strength": 50, "score_adj": +12,
                    "desc": f"Sắp phá kháng cự ${round(resist,2)}"}
        if ds < 0.5 and vr >= 1.3:
            return {"type": "PRE_BREAKOUT_DOWN", "strength": 50, "score_adj": -12,
                    "desc": f"Sắp phá hỗ trợ ${round(supprt,2)}"}
        return NONE

    @staticmethod
    def whale_detector(closes, volumes, taker_buy_vols) -> dict:
        NONE = {"detected": False, "type": "NONE", "score_adj": 0}
        if len(volumes) < 20:
            return NONE

        avg_vol   = sum(volumes[-20:-1]) / 19
        curr_vol  = volumes[-1]
        curr_buy  = taker_buy_vols[-1] if taker_buy_vols else curr_vol * 0.52
        buy_ratio = curr_buy / curr_vol if curr_vol > 0 else 0.5
        vol_ratio = curr_vol / avg_vol  if avg_vol  > 0 else 1

        if vol_ratio < 3.0:
            return NONE

        if buy_ratio >= 0.70:
            return {"detected": True, "type": "WHALE_BUY", "score_adj": +20,
                    "vol_ratio": round(vol_ratio, 2),
                    "desc": f"🐋 Whale MUA — Vol {round(vol_ratio,1)}x | {round(buy_ratio*100)}% buy"}
        if buy_ratio <= 0.30:
            return {"detected": True, "type": "WHALE_SELL", "score_adj": -20,
                    "vol_ratio": round(vol_ratio, 2),
                    "desc": f"🐋 Whale BÁN — Vol {round(vol_ratio,1)}x | {round((1-buy_ratio)*100)}% sell"}
        return NONE

    @staticmethod
    def volume_analysis(closes, highs, lows, volumes, taker_buy_vols) -> dict:
        EMPTY = {"score_adj": 0, "summary": "N/A", "vwap": 0,
                 "obv_trend": "NEUTRAL", "pressure": "NEUTRAL",
                 "vol_trend": "NEUTRAL", "poc": 0, "vol_ratio": 1,
                 "buy_pct": 50, "dist_poc": 0, "vwap_signal": "N/A",
                 "buy_confirm": False, "sell_confirm": False}

        n = min(len(closes), len(highs), len(lows), len(volumes), len(taker_buy_vols), 50)
        if n < 10:
            return EMPTY

        cls_n = closes[-n:]
        hgh_n = highs[-n:]
        low_n = lows[-n:]
        vol_n = volumes[-n:]
        tbv_n = taker_buy_vols[-n:]
        price = closes[-1]

        # Volume trend
        avg20  = sum(vol_n[-20:]) / 20
        curr_v = vol_n[-1]
        vr     = curr_v / avg20 if avg20 > 0 else 1
        if   vr >= 2.0: vt, va = "SURGE",  +12
        elif vr >= 1.5: vt, va = "HIGH",    +8
        elif vr >= 0.8: vt, va = "NORMAL",   0
        elif vr >= 0.5: vt, va = "LOW",     -5
        else:           vt, va = "DRY",    -10

        # OBV
        obv, obv_vals = 0.0, []
        for i in range(1, len(cls_n)):
            obv += vol_n[i] if cls_n[i] > cls_n[i-1] else (-vol_n[i] if cls_n[i] < cls_n[i-1] else 0)
            obv_vals.append(obv)

        obv_trend, oa = "NEUTRAL", 0
        if len(obv_vals) >= 10:
            obv_ma = sum(obv_vals[-10:]) / 10
            last5  = obv_vals[-5] if len(obv_vals) >= 5 else obv_vals[0]
            if obv_vals[-1] > obv_ma and obv_vals[-1] > last5:
                obv_trend, oa = "BULLISH", +12
            elif obv_vals[-1] < obv_ma:
                obv_trend, oa = "BEARISH", -12

        # VWAP
        tp   = [(hgh_n[i] + low_n[i] + cls_n[i]) / 3 for i in range(n)]
        ttv  = sum(vol_n) or 1
        vwap = round(sum(tp[i] * vol_n[i] for i in range(n)) / ttv, 2)
        if   price > vwap * 1.005: vs, vwa = "ABOVE",  +8
        elif price < vwap * 0.995: vs, vwa = "BELOW",  -8
        else:                       vs, vwa = "AT",      0

        # POC
        pmn, pmx = min(low_n), max(hgh_n)
        prng     = pmx - pmn
        N_BUCKETS = 20
        buckets = [0.0] * N_BUCKETS
        if prng > 0:
            for i in range(n):
                idx = min(N_BUCKETS - 1, int((cls_n[i] - pmn) / prng * N_BUCKETS))
                buckets[idx] += vol_n[i]
        poc_idx   = buckets.index(max(buckets))
        poc_price = round(pmn + (poc_idx + 0.5) * prng / N_BUCKETS, 2)
        dist_poc  = (price - poc_price) / poc_price * 100
        poc_adj   = +5 if price > poc_price * 1.01 else (-5 if price < poc_price * 0.99 else 0)

        # Buy pressure
        buy5  = sum(tbv_n[-5:])
        sell5 = sum(vol_n[-5:]) - buy5
        tot5  = buy5 + sell5
        bp    = buy5 / tot5 * 100 if tot5 > 0 else 50

        if   bp >= 65: pressure, pa = "STRONG_BUY",  +15
        elif bp >= 55: pressure, pa = "BUY",           +8
        elif bp <= 35: pressure, pa = "STRONG_SELL",  -15
        elif bp <= 45: pressure, pa = "SELL",          -8
        else:          pressure, pa = "NEUTRAL",        0

        total_adj    = max(-30, min(30, va + oa + vwa + poc_adj + pa))
        buy_confirm  = vt in ("SURGE","HIGH") and pressure in ("STRONG_BUY","BUY")
        sell_confirm = vt in ("SURGE","HIGH") and pressure in ("STRONG_SELL","SELL")
        if buy_confirm:  total_adj = max(-30, min(30, total_adj + 10))
        if sell_confirm: total_adj = max(-30, min(30, total_adj - 10))

        return {
            "score_adj":    total_adj,
            "summary":      (f"Vol:{vt}({round(vr,1)}x) OBV:{obv_trend} "
                             f"VWAP:{vs}(${vwap}) Press:{pressure}({round(bp)}%)"),
            "vol_trend":    vt,
            "vol_ratio":    round(vr, 2),
            "obv_trend":    obv_trend,
            "vwap":         vwap,
            "vwap_signal":  vs,
            "poc":          poc_price,
            "dist_poc":     round(dist_poc, 2),
            "pressure":     pressure,
            "buy_pct":      round(bp, 1),
            "buy_confirm":  buy_confirm,
            "sell_confirm": sell_confirm,
        }

    @staticmethod
    def candlestick_patterns(opens: list, highs: list, lows: list,
                              closes: list, volumes: list = None) -> dict:
        EMPTY = {"bias": "NEUTRAL", "patterns": [], "score_adj": 0,
                 "strength": 0, "confirm_long": False, "confirm_short": False,
                 "bull_count": 0, "bear_count": 0}

        n = min(len(opens), len(highs), len(lows), len(closes))
        if n < 5:
            return EMPTY

        o = [float(x) for x in opens[-n:]]
        h = [float(x) for x in highs[-n:]]
        l = [float(x) for x in lows[-n:]]
        c = [float(x) for x in closes[-n:]]
        v = ([float(x) for x in volumes[-n:]] if volumes and len(volumes) >= n
             else [1.0] * n)

        i   = n - 1
        i1  = n - 2
        i2  = n - 3
        i3  = n - 4

        def body(idx):     return abs(c[idx] - o[idx])
        def range_(idx):   return h[idx] - l[idx]
        def upper_wick(idx): return h[idx] - max(c[idx], o[idx])
        def lower_wick(idx): return min(c[idx], o[idx]) - l[idx]
        def bullish(idx):  return c[idx] > o[idx]
        def bearish(idx):  return c[idx] < o[idx]
        def avg_body(start, count):
            return sum(body(start + k) for k in range(count)) / count if count else 0

        avg_v = sum(v[:-1]) / max(len(v)-1, 1)
        vol_surge = v[i] > avg_v * 1.4
        ab5 = avg_body(i-4, 4) if i >= 4 else avg_body(0, i)

        found_bull = []
        found_bear = []

        # 1. Hammer
        if (range_(i) > 0 and body(i) > 0
                and lower_wick(i) >= 2 * body(i)
                and upper_wick(i) <= body(i) * 0.6
                and c[i1] < o[i1]
                and l[i] < l[i1]):
            found_bull.append("Hammer" + (" [V]" if vol_surge else ""))

        # 2. Inverted Hammer
        if (range_(i) > 0 and body(i) > 0
                and upper_wick(i) >= 2 * body(i)
                and lower_wick(i) <= body(i) * 0.5
                and bearish(i1)):
            found_bull.append("Inverted Hammer")

        # 3. Bullish Engulfing
        if (bullish(i) and bearish(i1)
                and o[i] <= c[i1]
                and c[i] >= o[i1]
                and body(i) > body(i1) * 1.05):
            found_bull.append("Bullish Engulfing" + (" [V]" if vol_surge else ""))

        # 4. Morning Star
        if i >= 2:
            if (bearish(i2)
                    and body(i2) > ab5 * 0.5
                    and body(i1) < body(i2) * 0.55
                    and bullish(i)
                    and c[i] > (o[i2] + c[i2]) / 2
                    and body(i) > ab5 * 0.4):
                found_bull.append("Morning Star" + (" [V]" if vol_surge else ""))

        # 5. Three White Soldiers
        if i >= 2:
            if (bullish(i2) and bullish(i1) and bullish(i)
                    and c[i] > c[i1] > c[i2]
                    and o[i1] > o[i2] and o[i] > o[i1]
                    and lower_wick(i)  <= body(i)  * 0.4
                    and lower_wick(i1) <= body(i1) * 0.4
                    and body(i) > ab5 * 0.5):
                found_bull.append("Three White Soldiers")

        # 6. Piercing Line
        if (bullish(i) and bearish(i1)
                and o[i] < l[i1]
                and c[i] > (o[i1] + c[i1]) / 2
                and c[i] < o[i1]):
            found_bull.append("Piercing Line")

        # 7. Bullish Harami
        if (bullish(i) and bearish(i1)
                and o[i] > c[i1] and c[i] < o[i1]
                and body(i) < body(i1) * 0.55):
            found_bull.append("Bullish Harami")

        # 8. Dragonfly Doji
        if (range_(i) > 0
                and body(i) / range_(i) < 0.12
                and lower_wick(i) >= range_(i) * 0.65
                and bearish(i1) and bearish(i2)):
            found_bull.append("Dragonfly Doji")

        # 9. Tweezer Bottom
        if (bearish(i1) and bullish(i)
                and abs(l[i] - l[i1]) / max(l[i], 0.001) < 0.005
                and body(i) > range_(i) * 0.35):
            found_bull.append("Tweezer Bottom")

        # 10. Bullish Kicker
        if (bearish(i1) and bullish(i)
                and o[i] > o[i1]
                and body(i) > ab5 * 0.7):
            found_bull.append("Bullish Kicker")

        # Bearish Patterns
        # 11. Shooting Star
        if (range_(i) > 0 and body(i) > 0
                and upper_wick(i) >= 2 * body(i)
                and lower_wick(i) <= max(body(i) * 0.5, range_(i) * 0.15)
                and bullish(i1)
                and h[i] > h[i1]):
            found_bear.append("Shooting Star" + (" [V]" if vol_surge else ""))

        # 12. Hanging Man
        if (range_(i) > 0 and body(i) > 0
                and lower_wick(i) >= 2 * body(i)
                and upper_wick(i) <= body(i) * 0.6
                and bullish(i1) and bullish(i2)
                and c[i] > c[i2]):
            found_bear.append("Hanging Man")

        # 13. Bearish Engulfing
        if (bearish(i) and bullish(i1)
                and o[i] >= c[i1]
                and c[i] <= o[i1]
                and body(i) > body(i1) * 1.1):
            found_bear.append("Bearish Engulfing" + (" [V]" if vol_surge else ""))

        # 14. Evening Star
        if i >= 2:
            if (bullish(i2)
                    and body(i2) > ab5 * 0.8
                    and body(i1) < body(i2) * 0.4
                    and bearish(i)
                    and c[i] < (o[i2] + c[i2]) / 2):
                found_bear.append("Evening Star" + (" [V]" if vol_surge else ""))

        # 15. Three Black Crows
        if i >= 2:
            if (bearish(i2) and bearish(i1) and bearish(i)
                    and c[i] < c[i1] < c[i2]
                    and o[i1] < o[i2] and o[i] < o[i1]
                    and upper_wick(i) <= body(i) * 0.3
                    and body(i) > ab5 * 0.6):
                found_bear.append("Three Black Crows")

        # 16. Dark Cloud Cover
        if (bearish(i) and bullish(i1)
                and o[i] > h[i1]
                and c[i] < (o[i1] + c[i1]) / 2
                and c[i] > o[i1]):
            found_bear.append("Dark Cloud Cover")

        # 17. Bearish Harami
        if (bearish(i) and bullish(i1)
                and o[i] < c[i1] and c[i] > o[i1]
                and body(i) < body(i1) * 0.5):
            found_bear.append("Bearish Harami")

        # 18. Gravestone Doji
        if (range_(i) > 0
                and body(i) / range_(i) < 0.1
                and upper_wick(i) >= range_(i) * 0.7
                and bullish(i1) and bullish(i2)):
            found_bear.append("Gravestone Doji")

        # 19. Tweezer Top
        if (bullish(i1) and bearish(i)
                and abs(h[i] - h[i1]) / max(h[i], 0.001) < 0.003
                and body(i) > range_(i) * 0.4):
            found_bear.append("Tweezer Top")

        # 20. Bearish Kicker
        if (bullish(i1) and bearish(i)
                and o[i] < o[i1]
                and body(i) > ab5 * 0.8):
            found_bear.append("Bearish Kicker")

        bull_score = sum(2 if "[V]" in p else 1 for p in found_bull)
        bear_score = sum(2 if "[V]" in p else 1 for p in found_bear)
        net = bull_score - bear_score

        score_adj = max(-25, min(25, net * 5))

        if   net >= 2:  bias = "BULLISH"
        elif net <= -2: bias = "BEARISH"
        else:           bias = "NEUTRAL"

        total_score = bull_score + bear_score
        strength = min(100, total_score * 15) if total_score > 0 else 0

        HIGH_QUALITY_BULL = {"Bullish Engulfing", "Morning Star", "Three White Soldiers", "Bullish Kicker"}
        HIGH_QUALITY_BEAR = {"Bearish Engulfing", "Evening Star", "Three Black Crows", "Bearish Kicker"}
        confirm_long  = any(any(hq in p for hq in HIGH_QUALITY_BULL) for p in found_bull)
        confirm_short = any(any(hq in p for hq in HIGH_QUALITY_BEAR) for p in found_bear)

        all_patterns = found_bull + found_bear

        return {
            "bias":          bias,
            "patterns":      all_patterns,
            "bull_patterns": found_bull,
            "bear_patterns": found_bear,
            "bull_count":    len(found_bull),
            "bear_count":    len(found_bear),
            "score_adj":     score_adj,
            "strength":      strength,
            "confirm_long":  confirm_long,
            "confirm_short": confirm_short,
        }

    @staticmethod
    def market_structure(closes: list, highs: list, lows: list,
                         lookback: int = 50) -> dict:
        EMPTY = {"structure": "SIDEWAYS", "hh": False, "hl": False,
                 "lh": False, "ll": False, "bos": False, "choch": False,
                 "score_adj": 0, "last_swing_high": 0, "last_swing_low": 0}

        n = min(len(closes), len(highs), len(lows), lookback)
        if n < 10:
            return EMPTY

        h = highs[-n:]
        l = lows[-n:]
        c = closes[-n:]

        def find_swings(arr, mode="high", min_pct=0.002):
            swings = []
            n = len(arr)
            for i in range(1, n - 1):
                if mode == "high":
                    if arr[i] > arr[i-1] and arr[i] > arr[i+1]:
                        prev_min = min(arr[max(0, i-3):i]) if i > 0 else arr[0]
                        if prev_min > 0 and (arr[i] - prev_min) / prev_min >= min_pct:
                            swings.append((i, arr[i]))
                else:
                    if arr[i] < arr[i-1] and arr[i] < arr[i+1]:
                        prev_max = max(arr[max(0, i-3):i]) if i > 0 else arr[0]
                        if prev_max > 0 and (prev_max - arr[i]) / prev_max >= min_pct:
                            swings.append((i, arr[i]))
            return swings

        swing_highs = find_swings(h, "high")
        swing_lows  = find_swings(l, "low")

        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return EMPTY

        last_sh = swing_highs[-1][1]
        prev_sh = swing_highs[-2][1]
        last_sl = swing_lows[-1][1]
        prev_sl = swing_lows[-2][1]

        hh = last_sh > prev_sh
        lh = last_sh < prev_sh
        hl = last_sl > prev_sl
        ll = last_sl < prev_sl

        if hh and hl:
            structure = "UPTREND"
            adj = +10
        elif lh and ll:
            structure = "DOWNTREND"
            adj = -10
        elif hh and ll:
            structure = "EXPANDING"
            adj = 0
        elif lh and hl:
            structure = "CONTRACTING"
            adj = 0
        else:
            structure = "SIDEWAYS"
            adj = 0

        price = c[-1]
        bos_bull  = price > last_sh and structure in ("DOWNTREND", "SIDEWAYS")
        bos_bear  = price < last_sl and structure in ("UPTREND", "SIDEWAYS")
        bos       = bos_bull or bos_bear

        if bos_bull:
            adj += 12
        elif bos_bear:
            adj -= 12

        mid_sh = (last_sh + prev_sh) / 2
        mid_sl = (last_sl + prev_sl) / 2
        choch = (price > mid_sh and structure == "DOWNTREND") or \
                (price < mid_sl and structure == "UPTREND")

        return {
            "structure":      structure,
            "hh": hh, "hl": hl, "lh": lh, "ll": ll,
            "bos":            bos,
            "bos_bull":       bos_bull,
            "bos_bear":       bos_bear,
            "choch":          choch,
            "score_adj":      max(-20, min(20, adj)),
            "last_swing_high": round(last_sh, 4),
            "last_swing_low":  round(last_sl, 4),
        }

    @staticmethod
    def elliott_wave_analysis(closes: list, highs: list, lows: list) -> dict:
        EMPTY = {
            "wave_pattern": "Neutral Wave Accumulation",
            "current_wave": "None",
            "trend": "NEUTRAL",
            "description": "Cấu trúc sóng dao động trong biên độ tích lũy ngang, chưa hình thành xu hướng Elliot rõ ràng.",
            "score_adj": 0
        }
        n = len(closes)
        if n < 15:
            return EMPTY

        try:
            h = [float(x) for x in highs[-15:]]
            l = [float(x) for x in lows[-15:]]
            c = [float(x) for x in closes[-15:]]

            sw_highs = []
            sw_lows = []

            for i in range(2, 13):
                if h[i] == max(h[i-2:i+3]):
                    sw_highs.append((i, h[i]))
                if l[i] == min(l[i-2:i+3]):
                    sw_lows.append((i, l[i]))

            sw_highs.sort(key=lambda x: x[0])
            sw_lows.sort(key=lambda x: x[0])

            if len(sw_highs) < 2 or len(sw_lows) < 2:
                if c[-1] > c[-8]:
                    return {
                        "wave_pattern": "Impulse Wave 1/3 Rising",
                        "current_wave": "Wave 3",
                        "trend": "BULLISH",
                        "description": "Sóng tăng trưởng ngắn hạn đang đẩy giá lên, xu hướng Elliot thiên hướng Bullish Impulse.",
                        "score_adj": 5
                    }
                else:
                    return {
                        "wave_pattern": "Corrective Wave A/C Descending",
                        "current_wave": "Wave C",
                        "trend": "BEARISH",
                        "description": "Sóng điều chỉnh ngắn hạn đang giảm, xu hướng Elliot thiên hướng Bearish Correction.",
                        "score_adj": -5
                    }

            sh1_idx, sh1_val = sw_highs[-1]
            sh2_idx, sh2_val = sw_highs[-2]
            sl1_idx, sl1_val = sw_lows[-1]
            sl2_idx, sl2_val = sw_lows[-2]

            price_now = c[-1]

            if sl1_val > sl2_val and sh1_val > sh2_val:
                if price_now > sh1_val:
                    return {
                        "wave_pattern": "5-Wave Bullish Impulse",
                        "current_wave": "Wave 3",
                        "trend": "BULLISH",
                        "description": "Sóng 3 Elliot Impulse đột phá đỉnh cũ. Sóng tăng trưởng mạnh nhất trong chu kỳ kỹ thuật, hỗ trợ lực mua mạnh mẽ.",
                        "score_adj": 12
                    }
                else:
                    return {
                        "wave_pattern": "5-Wave Bullish Impulse",
                        "current_wave": "Wave 4",
                        "trend": "NEUTRAL",
                        "description": "Sóng 4 Elliot điều chỉnh tích lũy sau đợt tăng Sóng 3. Áp lực bán nhẹ, cơ hội gom hàng khi retest hỗ trợ.",
                        "score_adj": 3
                    }

            elif sl1_val < sl2_val and sh1_val < sh2_val:
                if price_now < sl1_val:
                    return {
                        "wave_pattern": "3-Wave Bearish Correction",
                        "current_wave": "Wave C",
                        "trend": "BEARISH",
                        "description": "Sóng C điều chỉnh hoảng loạn phá vỡ đáy cũ. Lực bán tháo chiếm ưu thế tuyệt đối, khuyến nghị hạn chế bắt đáy.",
                        "score_adj": -12
                    }
                else:
                    return {
                        "wave_pattern": "3-Wave Bearish Correction",
                        "current_wave": "Wave B",
                        "trend": "BULLISH",
                        "description": "Sóng B hồi phục kỹ thuật trong kênh giảm giá. Lực mua mang tính ngắn hạn và rủi ro cao, chờ tín hiệu xác nhận.",
                        "score_adj": -3
                    }

            else:
                if price_now > sh1_val:
                    return {
                        "wave_pattern": "Wave 5 Extended",
                        "current_wave": "Wave 5",
                        "trend": "BULLISH",
                        "description": "Sóng 5 tăng mở rộng vượt đỉnh cũ. Đà tăng cuối chu kỳ, cẩn trọng vùng quá mua đảo chiều.",
                        "score_adj": 6
                    }
                elif price_now < sl1_val:
                    return {
                        "wave_pattern": "Wave A Impulse",
                        "current_wave": "Wave A",
                        "trend": "BEARISH",
                        "description": "Sóng A điều chỉnh khởi đầu cho chu kỳ giảm giá mới. Áp lực chốt lời gia tăng rõ rệt.",
                        "score_adj": -6
                    }
                else:
                    return {
                        "wave_pattern": "Neutral Wave Accumulation",
                        "current_wave": "Wave 2/4 Sideways",
                        "trend": "NEUTRAL",
                        "description": "Sóng Elliot đang đi ngang tích lũy tích cực tích cực (Sóng 2 hoặc Sóng 4 phẳng). Chờ đợi breakout bùng nổ.",
                        "score_adj": 1
                    }

        except Exception as e:
            return EMPTY
