# ═══════════════════════════════════════════════════════════
# 5. SIGNAL ENGINE — v6.1 (Parallel TF + Smart Confidence)
# ═══════════════════════════════════════════════════════════
import time
import logging
import math
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from analyzer.fetcher import CryptoFetcher, StockFetcher
from analyzer.indicators import Indicators

log = logging.getLogger("analyzer.engine")


class SignalEngine:
    TIMEFRAMES = ["15m", "1h", "4h", "1d"]

    # [FIX] Dùng chung 1 ThreadPoolExecutor thay vì tạo mới mỗi lần full_analysis
    # max_workers=4 khớp với số TF để tất cả chạy song song
    _executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tf-worker")

    def __init__(self):
        self.crypto = CryptoFetcher()
        self.stock  = StockFetcher()
        self.ind    = Indicators()
        self._cache = {}

    def _fetcher(self, symbol):
        if symbol in ("BTCUSDT", "ETHUSDT", "BNBUSDT", "HYPEUSDT"):
            return self.crypto, "CRYPTO"
        if symbol in ("TSLA", "NVDA", "SPY", "QQQ"):
            return self.stock, "STOCK"
        if symbol == "XAUUSD":
            return self.stock, "GOLD"
        return self.crypto, "CRYPTO"

    def is_tradeable(self, symbol):
        if symbol in ("BTCUSDT", "ETHUSDT", "BNBUSDT", "HYPEUSDT"):
            return True, "Crypto 24/7"
        if symbol in ("TSLA", "NVDA", "SPY", "QQQ"):
            return self.stock.market_open()
        if symbol == "XAUUSD":
            return self.stock.is_gold_open()
        return True, "OK"

    def analyze_tf(self, symbol, interval, fetcher):
        data   = fetcher.klines(symbol, interval)
        closes = data["close"]
        highs  = data["high"]
        lows   = data["low"]
        vols   = data["volume"]
        tbvols = data["taker_buy_vol"]
        price  = closes[-1]

        # ── Indicators ─────────────────────────────────────────
        rsi_v  = self.ind.rsi(closes)
        macd_d = self.ind.macd(closes)
        bb     = self.ind.bollinger(closes)
        fibo   = self.ind.fibonacci(highs, lows, closes)
        wyc    = self.ind.wyckoff(closes, highs, lows, vols)
        cvd_d  = self.ind.cvd(closes, tbvols, vols)
        bo     = self.ind.breakout_detector(closes, highs, lows, vols)
        whale  = self.ind.whale_detector(closes, vols, tbvols)
        vol_d  = self.ind.volume_analysis(closes, highs, lows, vols, tbvols)
        candle = self.ind.candlestick_patterns(data["open"], highs, lows, closes, vols)
        mstruct = self.ind.market_structure(closes, highs, lows)
        elliott = self.ind.elliott_wave_analysis(closes, highs, lows)
        fvg = self.ind.order_flow_fvg(highs, lows, closes)

        ema20  = self.ind.ema(closes, 20)
        ema50  = self.ind.ema(closes, 50)
        ema200 = self.ind.ema(closes, 200)
        ema_bull = price > ema20 > ema50 > ema200
        ema_bear = price < ema20 < ema50 < ema200

        # ── ATR ─────────────────────────────────────────────────
        if len(highs) >= 14 and len(lows) >= 14:
            trs = [max(highs[i] - lows[i],
                       abs(highs[i] - closes[i-1]),
                       abs(lows[i]  - closes[i-1]))
                   for i in range(1, min(15, len(closes)))]
            atr = sum(trs) / len(trs) if trs else price * 0.01
        else:
            atr = price * 0.01
        atr_pct = atr / price * 100

        # ── Trend strength (ADX simplified) ─────────────────────
        if len(closes) >= 14:
            changes = [abs(closes[i] - closes[i-1]) for i in range(-14, 0)]
            avg_change = sum(changes) / 14
            trend_strength = avg_change / (atr if atr > 0 else 1)
            is_trending = trend_strength > 0.5
        else:
            is_trending = True
            trend_strength = 1.0

        # ══════════════════════════════════════════════════════
        # SCORING
        # ══════════════════════════════════════════════════════
        score = 50

        # 1. RSI (weight 15)
        if is_trending:
            if rsi_v < 40:   score += 12
            elif rsi_v < 48: score += 6
            elif rsi_v > 60: score -= 6
            elif rsi_v > 72: score -= 12
        else:
            if rsi_v < 30:   score += 15
            elif rsi_v < 45: score += 8
            elif rsi_v > 70: score -= 15
            elif rsi_v > 55: score -= 8

        # 2. EMA Stack (weight 12)
        if ema_bull:   score += 12
        elif ema_bear: score -= 12
        elif price > ema20:  score += 4
        elif price < ema20:  score -= 4

        # 3. MACD (weight 8)
        hist = macd_d.get("hist", 0)
        if macd_d["cross"] == "BULL_CROSS" and hist > 0:
            score += 8
        elif macd_d["cross"] == "BEAR_CROSS" and hist < 0:
            score -= 8
        elif macd_d["cross"] == "BULL_CROSS":
            score += 4
        elif macd_d["cross"] == "BEAR_CROSS":
            score -= 4

        # 4. Bollinger Bands (weight 8)
        if bb["pct"] < 15:   score += 8
        elif bb["pct"] < 30: score += 4
        elif bb["pct"] > 85: score -= 8
        elif bb["pct"] > 70: score -= 4
        if bb.get("squeeze"):
            if ema_bull: score += 6
            elif ema_bear: score -= 6

        # 5. Fibonacci (weight 10)
        fibo_trend = fibo.get("trend","?")
        fibo_zone  = fibo.get("zone","")
        if fibo.get("in_golden"):
            score += 10 if fibo_trend == "UPTREND" else -10
        if "NEAR_0.618" in fibo_zone and fibo_trend == "UPTREND":
            score += 7
        elif "NEAR_0.382" in fibo_zone and fibo_trend == "UPTREND":
            score += 4
        if "NEAR_0.382" in fibo_zone and fibo_trend == "DOWNTREND":
            score -= 7
        elif "NEAR_0.618" in fibo_zone and fibo_trend == "DOWNTREND":
            score -= 4

        # 6. Wyckoff (weight 12)
        wy_adj = wyc.get("score_adj", 0)
        if wyc.get("phase") == "TRANSITION":
            score += wy_adj * 0.3
        else:
            score += wy_adj * 0.7

        # 7. CVD (weight 10)
        cvd_adj = cvd_d.get("score_adj", 0)
        if cvd_d.get("divergence"):
            score += cvd_adj * 1.2
        else:
            score += cvd_adj * 0.7

        # 8. Volume Analysis (weight 10)
        vol_adj = vol_d.get("score_adj", 0)
        score += vol_adj * 0.5

        # 9. Stochastic RSI (weight 8)
        stoch = self.ind.stoch_rsi(closes)
        stoch_adj = stoch.get("score_adj", 0)
        if abs(stoch_adj) >= 10:
            if ((stoch_adj > 0 and rsi_v < 50) or
                (stoch_adj < 0 and rsi_v > 50)):
                score += stoch_adj * 0.8
            else:
                score += stoch_adj * 0.4

        # 10. Candlestick Patterns (weight 12)
        # Mô hình nến kinh điển — bổ sung tín hiệu kỹ thuật vi mô
        candle_adj  = candle.get("score_adj", 0)
        candle_str  = candle.get("strength", 0)
        if candle_str >= 30:
            if candle.get("confirm_long") or candle.get("confirm_short"):
                score += candle_adj * 1.0
            else:
                score += candle_adj * 0.6

        # 11. Market Structure (weight 10)
        ms_adj = mstruct.get("score_adj", 0)
        ms_str = mstruct.get("structure", "SIDEWAYS")
        if ms_str in ("UPTREND", "DOWNTREND"):
            score += ms_adj * 0.7
        elif mstruct.get("bos"):
            score += ms_adj * 1.0

        # 12. Elliott Wave (weight 12)
        score += elliott.get("score_adj", 0)
        score += fvg.get("score_adj", 0)
        score = max(5, min(95, score))

        # ══════════════════════════════════════════════════════
        # SIGNAL DIRECTION
        # ══════════════════════════════════════════════════════
        if bo["type"] == "BREAKOUT_UP" and bo["strength"] >= 70:
            direction = "LONG"
            score     = max(score, 72)
        elif bo["type"] == "BREAKOUT_DOWN" and bo["strength"] >= 70:
            direction = "SHORT"
            score     = min(score, 28)
        elif whale.get("detected") and whale["type"] == "WHALE_BUY":
            direction = "LONG"
            score     = max(score, 70)
        elif whale.get("detected") and whale["type"] == "WHALE_SELL":
            direction = "SHORT"
            score     = min(score, 30)
        elif score >= 67: direction = "LONG"
        elif score <= 33: direction = "SHORT"
        else:             direction = "WAIT"

        # Anti-noise filter
        if not is_trending and 38 <= score <= 62 and bo.get("type", "NONE") == "NONE":
            direction = "WAIT"

        # Candlestick conflict filter
        if interval in ("1h", "4h"):
            if direction == "LONG" and candle.get("confirm_short"):
                log.debug("Candle conflict: %s LONG bị filter bởi %s",
                          interval, candle.get("bear_patterns", []))
                if candle.get("strength", 0) >= 50:
                    score = min(score, 48)
                    direction = "WAIT"
            elif direction == "SHORT" and candle.get("confirm_long"):
                log.debug("Candle conflict: %s SHORT bị filter bởi %s",
                          interval, candle.get("bull_patterns", []))
                if candle.get("strength", 0) >= 50:
                    score = max(score, 52)
                    direction = "WAIT"

        return {
            "interval": interval, "direction": direction,
            "score": round(score, 1), "rsi": rsi_v,
            "macd": macd_d, "bb": bb,
            "ema": {"bull": ema_bull, "bear": ema_bear,
                    "e20": ema20, "e50": ema50, "e200": ema200},
            "fibo": fibo, "wyckoff": wyc, "cvd": cvd_d,
            "breakout": bo, "whale": whale, "volume": vol_d,
            "candle": candle, "market_structure": mstruct,
            "elliott": elliott,
            "fvg": fvg,
            "atr": round(atr, 4), "atr_pct": round(atr_pct, 3),
            "is_trending": is_trending, "price": price,
            "high": highs, "low": lows, "close": closes
        }

    def full_analysis(self, symbol):
        fetcher, atype = self._fetcher(symbol)
        log.info("📊 Phân tích %s [%s]...", symbol, atype)
        results = {}

        # Chạy song song 4 TF
        def _fetch_tf(tf):
            return tf, self.analyze_tf(symbol, tf, fetcher)

        futures = {self._executor.submit(_fetch_tf, tf): tf for tf in self.TIMEFRAMES}
        for fut in as_completed(futures, timeout=35):
            tf = futures[fut]
            try:
                tf_name, res = fut.result()
                results[tf_name] = res
            except Exception as e:
                log.warning("  TF %s lỗi: %s", tf, e)

        if not results:
            raise RuntimeError("Không lấy được dữ liệu " + symbol)

        # Penalty confidence khi thiếu TF quan trọng
        missing_important = [tf for tf in ("1h", "4h") if tf not in results]
        tf_penalty = len(missing_important) * 10

        tf_count = len(results)
        if tf_count < 2:
            raise RuntimeError(f"Quá ít TF ({tf_count}) cho {symbol}")

        price = list(results.values())[-1]["price"]
        oi_now, oi_delta = 0.0, 0.0
        funding = 0.0
        oi_signal, oi_desc = "N/A", "N/A"

        if atype == "CRYPTO":
            oi_now, oi_delta = self.crypto.open_interest(symbol)
            funding          = self.crypto.funding_rate(symbol)
            prev             = self._cache.get(symbol, price)
            self._cache[symbol] = price
            px_up  = price > prev
            oi_up  = oi_delta > 0.5
            oi_dn  = oi_delta < -0.5
            if   oi_up  and px_up:     oi_signal, oi_desc = "LONG_BUILD",    "Long mới vào — bullish"
            elif oi_up  and not px_up: oi_signal, oi_desc = "SHORT_BUILD",   "Short mới vào — bearish"
            elif oi_dn  and px_up:     oi_signal, oi_desc = "SHORT_SQUEEZE", "Short bị ép"
            elif oi_dn  and not px_up: oi_signal, oi_desc = "LONG_LIQ",      "Long bị thanh lý"
            else:                      oi_signal, oi_desc = "NEUTRAL",        "OI ổn định"

        mkt_open, mkt_note = True, ""
        if atype == "STOCK":
            mkt_open, mkt_note = self.stock.market_open()

        longs     = sum(1 for r in results.values() if r["direction"] == "LONG")
        shorts    = sum(1 for r in results.values() if r["direction"] == "SHORT")
        avg_score = sum(r["score"] for r in results.values()) / len(results)

        cvd_1h = results.get("1h", {}).get("cvd", {})
        bo_1h  = results.get("1h", {}).get("breakout", {})
        bo_4h  = results.get("4h", {}).get("breakout", {})
        wh_1h  = results.get("1h", {}).get("whale", {})
        wy_4h  = results.get("4h", {}).get("wyckoff", {})
        fi_1h  = results.get("1h", {}).get("fibo", {})
        vol_1h = results.get("1h", {}).get("volume", {})
        vol_4h = results.get("4h", {}).get("volume", {})

        smart = 0
        cvd_tr = cvd_1h.get("trend", "NEUTRAL")
        if cvd_tr == "BULLISH":       smart += 20
        elif cvd_tr == "BULLISH_DIV": smart += 15
        elif cvd_tr == "BEARISH":     smart -= 20
        elif cvd_tr == "BEARISH_DIV": smart -= 15
        if wy_4h.get("bias") == "BULLISH":   smart += 10
        elif wy_4h.get("bias") == "BEARISH": smart -= 10
        if fi_1h.get("trend") == "UPTREND":     smart += 8
        elif fi_1h.get("trend") == "DOWNTREND": smart -= 8
        if wh_1h.get("detected"):
            smart += 25 if wh_1h["type"] == "WHALE_BUY" else -25
        vol_smart = (vol_1h.get("score_adj", 0) + vol_4h.get("score_adj", 0)) * 0.5
        smart += vol_smart
        if vol_1h.get("buy_confirm")  and vol_4h.get("buy_confirm"):  smart += 15
        if vol_1h.get("sell_confirm") and vol_4h.get("sell_confirm"): smart -= 15

        combined = avg_score * 0.6 + (50 + smart) * 0.4
        combined = max(0, min(100, combined))

        log.info("  TF=%.1f Smart=%+.1f Combined=%.1f L:%d S:%d",
                 avg_score, smart, combined, longs, shorts)

        # Scale down threshold khi thiếu TF
        min_long_tfs  = min(3, tf_count - 1)
        min_short_tfs = min(3, tf_count - 1)

        final, conf = None, 50.0
        for tf_n, bo in [("1H", bo_1h), ("4H", bo_4h)]:
            if bo.get("type") in ("BREAKOUT_UP","BREAKOUT_DOWN") and bo.get("strength",0) >= 70:
                final = "LONG" if bo["type"] == "BREAKOUT_UP" else "SHORT"
                conf  = min(95, 70 + bo["strength"] * 0.25)
                log.info("🚨 Breakout override [%s]", tf_n)
                break

        if final is None:
            long_ok  = longs >= min_long_tfs  and combined >= 62 and smart >= -10
            short_ok = shorts >= min_short_tfs and combined <= 38 and smart <= 10
            if   long_ok  or combined >= 68: final = "LONG";  conf = round(min(95, combined), 1)
            elif short_ok or combined <= 32: final = "SHORT"; conf = round(min(95, 100-combined), 1)
            else:                             final = "WAIT";  conf = round(min(95, max(30, combined)), 1)
            if cvd_tr in ("BEARISH_DIV","BULLISH_DIV") and abs(smart) < 20:
                conf = round(conf * 0.85, 1)
                if conf < 55:
                    final = "WAIT"

        # Áp dụng TF penalty vào confidence
        if tf_penalty > 0:
            conf = round(max(30, conf - tf_penalty), 1)
            log.warning("  ⚠️ Thiếu TF %s → penalty -%d%% → conf=%.1f%%",
                        missing_important, tf_penalty, conf)

        # ── 12-Agent L2 Orderbook and Liquidity Analysis ─────────────────
        ob_data = {"detected": False}
        sweep_data = {"detected": False}

        if atype == "CRYPTO":
            try:
                # Lấy dữ liệu Sổ lệnh L2
                ob_raw = fetcher.order_book(symbol, depth=30)
                if ob_raw.get("ok"):
                    best_bid_wall = ob_raw["bid_walls"][0] if ob_raw.get("bid_walls") else {"price": 0, "usd": 0}
                    best_ask_wall = ob_raw["ask_walls"][0] if ob_raw.get("ask_walls") else {"price": 0, "usd": 0}
                    
                    ob_data = {
                        "detected": True,
                        "ratio": ob_raw["ratio"],
                        "imbalance": ob_raw["imbalance"],
                        "spread_pct": ob_raw["spread_pct"],
                        "support_wall": best_bid_wall["price"],
                        "support_wall_usd": best_bid_wall["usd"],
                        "resist_wall": best_ask_wall["price"],
                        "resist_wall_usd": best_ask_wall["usd"],
                    }
                
                # Lấy dữ liệu thanh lý
                liq_raw = fetcher.liquidation_levels(symbol, price)
                
                # Phân tích Bẫy Thanh Khoản (Liquidity Sweep)
                tf_to_check = results.get("1h") or results.get("15m")
                if tf_to_check:
                    mstruct = tf_to_check.get("market_structure", {})
                    swing_high = mstruct.get("last_swing_high", 0)
                    swing_low = mstruct.get("last_swing_low", 0)
                    
                    high_price = tf_to_check.get("high", [0])[-1]
                    low_price = tf_to_check.get("low", [0])[-1]
                    close_price = tf_to_check.get("price", price)
                    
                    detected_sweep = False
                    sweep_type = "NONE"
                    sweep_p = 0.0
                    
                    # Bullish Liquidity Sweep (Spring): Giá quét dưới Swing Low cũ nhưng đóng cửa trên Swing Low cũ
                    if swing_low > 0 and low_price < swing_low and close_price > swing_low:
                        detected_sweep = True
                        sweep_type = "BULLISH_SWEEP"
                        sweep_p = low_price
                    # Bearish Liquidity Sweep (UTAD): Giá quét trên Swing High cũ nhưng đóng cửa dưới Swing High cũ
                    elif swing_high > 0 and high_price > swing_high and close_price < swing_high:
                        detected_sweep = True
                        sweep_type = "BEARISH_SWEEP"
                        sweep_p = high_price
                    
                    if detected_sweep:
                        sweep_data = {
                            "detected": True,
                            "type": sweep_type,
                            "price": sweep_p,
                            "cascade_risk": liq_raw.get("cascade_risk", False),
                            "dominant_side": liq_raw.get("dominant_side", "NEUTRAL")
                        }
            except Exception as e:
                log.warning("⚠️ Lỗi phân tích L2 Orderbook/Liquidity cho %s: %s", symbol, e)

        # ATR-based SL/TP
        atr_1h     = results.get("1h", {}).get("atr", price * 0.01)
        atr_pct_1h = results.get("1h", {}).get("atr_pct", 1.0)
        sl_atr_pct = max(0.8, min(2.5, atr_pct_1h * 1.5))
        tp1_pct    = sl_atr_pct * 2.0
        tp2_pct    = sl_atr_pct * 3.5
        fibo4l     = results.get("4h", {}).get("fibo", {}).get("levels", {})

        # ATR-based defaults
        atr_tp1 = round(price * (1 + tp1_pct / 100), 2)
        atr_tp2 = round(price * (1 + tp2_pct / 100), 2)
        atr_tp1_s = round(price * (1 - tp1_pct / 100), 2)
        atr_tp2_s = round(price * (1 - tp2_pct / 100), 2)

        fibo4h_trend = results.get("4h", {}).get("fibo", {}).get("trend", "")

        if final == "LONG":
            sl = round(price * (1 - sl_atr_pct / 100), 2)
            if fibo4h_trend == "UPTREND":
                f272 = fibo4l.get("1.272")
                f618 = fibo4l.get("1.618")
                if (f272 and f618 and
                        f272 > price * 1.005 and
                        f618 > price * 1.005 and
                        f618 > f272):
                    tp1 = round(f272, 2)
                    tp2 = round(f618, 2)
                else:
                    tp1 = atr_tp1
                    tp2 = atr_tp2
            else:
                tp1 = atr_tp1
                tp2 = atr_tp2

        elif final == "SHORT":
            sl  = round(price * (1 + sl_atr_pct / 100), 2)
            if fibo4h_trend == "DOWNTREND":
                f272 = fibo4l.get("1.272")
                f618 = fibo4l.get("1.618")
                if (f272 and f618 and
                        f272 < price * 0.995 and
                        f618 < price * 0.995 and
                        f618 < f272):
                    tp1 = round(f272, 2)
                    tp2 = round(f618, 2)
                else:
                    tp1 = atr_tp1_s
                    tp2 = atr_tp2_s
            else:
                tp1 = atr_tp1_s
                tp2 = atr_tp2_s

        else:  # WAIT
            sl  = round(price * (1 - sl_atr_pct / 100), 2)
            tp1 = atr_tp1
            tp2 = atr_tp2

        # Đảm bảo thứ tự TP luôn đúng
        if final == "LONG":
            if tp2 <= tp1:
                log.warning("⚠️ TP2(%.4f) <= TP1(%.4f) cho LONG %s — dùng ATR", tp2, tp1, symbol)
                tp1 = atr_tp1
                tp2 = atr_tp2
            if tp1 <= price:
                tp1 = atr_tp1
            if tp2 <= tp1:
                tp2 = round(tp1 * 1.015, 2)

        elif final == "SHORT":
            if tp2 >= tp1:
                log.warning("⚠️ TP2(%.4f) >= TP1(%.4f) cho SHORT %s — dùng ATR", tp2, tp1, symbol)
                tp1 = atr_tp1_s
                tp2 = atr_tp2_s
            if tp1 >= price:
                tp1 = atr_tp1_s
            if tp2 >= tp1:
                tp2 = round(tp1 * 0.985, 2)

        rr_ratio = round(tp1_pct / sl_atr_pct, 2) if sl_atr_pct > 0 else 2.0

        candle_1h = results.get("1h", {}).get("candle", {})
        candle_4h = results.get("4h", {}).get("candle", {})
        ms_1h     = results.get("1h", {}).get("market_structure", {})
        ms_4h     = results.get("4h", {}).get("market_structure", {})

        all_bull_patterns = (candle_1h.get("bull_patterns", []) +
                             candle_4h.get("bull_patterns", []))
        all_bear_patterns = (candle_1h.get("bear_patterns", []) +
                             candle_4h.get("bear_patterns", []))

        candle_summary = {
            "bias":           candle_4h.get("bias", candle_1h.get("bias", "NEUTRAL")),
            "bull_patterns":  all_bull_patterns,
            "bear_patterns":  all_bear_patterns,
            "confirm_long":   candle_4h.get("confirm_long") or candle_1h.get("confirm_long"),
            "confirm_short":  candle_4h.get("confirm_short") or candle_1h.get("confirm_short"),
            "strength":       max(candle_4h.get("strength", 0), candle_1h.get("strength", 0)),
        }

        if final == "LONG" and candle_summary["confirm_long"]:
            conf = round(min(95, conf * 1.05), 1)
        elif final == "SHORT" and candle_summary["confirm_short"]:
            conf = round(min(95, conf * 1.05), 1)

        return {
            "symbol": symbol, "asset_type": atype,
            "price": price, "final": final, "confidence": conf,
            "longs": longs, "shorts": shorts, "timeframes": results,
            "plan": {"entry": price, "sl": sl, "tp1": tp1, "tp2": tp2},
            "wyckoff": wy_4h, "fibo": fi_1h, "cvd": cvd_1h,
            "breakout": bo_1h, "whale": wh_1h,
            "volume_1h": vol_1h, "volume_4h": vol_4h,
            "candle": candle_summary,
            "elliott_4h": results.get("4h", {}).get("elliott", {}),
            "market_structure_1h": ms_1h,
            "market_structure_4h": ms_4h,
            "oi_signal": oi_signal, "oi_desc": oi_desc,
            "oi_delta": round(oi_delta, 3), "funding": round(funding, 4),
            "mkt_open": mkt_open, "mkt_note": mkt_note,
            "rr_ratio": rr_ratio, "sl_pct": round(sl_atr_pct, 2),
            "tf_count": tf_count,
            "orderbook": ob_data,
            "liquidity_sweep": sweep_data,
            "timestamp": datetime.now().strftime("%d/%m %H:%M"),
        }
