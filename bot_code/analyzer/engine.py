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
        # [NEW v6.9] Cache ngắn hạn cho closes của BTC — dùng để tính tương quan
        # BTC-altcoin mà không phải fetch lại BTC cho mỗi altcoin trong cùng 1 vòng quét
        self._btc_cache = {"closes": None, "ts": 0}
        self._BTC_CACHE_TTL = 240  # giây — ngắn hơn chu kỳ quét 15p nhưng đủ tránh fetch lặp lại

    def _fetcher(self, symbol):
        if symbol in ("BTCUSDT", "ETHUSDT", "BNBUSDT", "HYPEUSDT"):
            return self.crypto, "CRYPTO"
        if symbol in ("TSLA", "NVDA", "SPY", "QQQ"):
            return self.stock, "STOCK"
        if symbol == "NCCOGOLD2USD-USDT":
            return self.stock, "GOLD"
        return self.crypto, "CRYPTO"

    def is_tradeable(self, symbol):
        if symbol in ("BTCUSDT", "ETHUSDT", "BNBUSDT", "HYPEUSDT"):
            return True, "Crypto 24/7"
        if symbol in ("TSLA", "NVDA", "SPY", "QQQ"):
            return self.stock.market_open()
        if symbol == "NCCOGOLD2USD-USDT":
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
                   for i in range(len(closes) - 14, len(closes))]
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

    def _btc_trend_now(self) -> str:
        """
        [FIX v6.13] BUG THẬT vừa tìm ra: cổng tương quan BTC-altcoin trước đó
        chỉ kích hoạt khi btc_hmm_regime (đọc từ bảng MarketRegime, do HMM
        worker cập nhật mỗi 5 phút) == UPTREND/DOWNTREND. Nhưng nhãn đó đang
        báo SIDEWAYS cho BTC ngay cả khi Kalman=BULLISH, xu hướng 4H=LONG, và
        breakout override đã kích hoạt nhiều lần liên tiếp — HMM worker rõ
        ràng đang phân loại quá thận trọng/trễ so với thực tế. Kết quả: cổng
        chặn ngược-BTC KHÔNG BAO GIỜ kích hoạt trong điều kiện thị trường vừa
        rồi, dù tương quan đo được có cao tới đâu.
        Hàm này đọc xu hướng BTC TRỰC TIẾP từ chính dữ liệu giá vừa fetch cho
        phần tương quan (EMA20 vs EMA50) — không phụ thuộc HMM worker, phản
        ứng ngay trong cùng 1 lần quét thay vì chờ chu kỳ 5 phút của service khác.
        """
        try:
            now = time.time()
            if self._btc_cache["closes"] is None or (now - self._btc_cache["ts"]) > self._BTC_CACHE_TTL:
                btc_data = self.crypto.klines("BTCUSDT", "1h")
                self._btc_cache = {"closes": btc_data["close"], "ts": now}
            closes = self._btc_cache["closes"]
            if not closes or len(closes) < 55:
                return "UNKNOWN"

            def _ema(vals, period):
                k = 2 / (period + 1)
                e = vals[0]
                for v in vals[1:]:
                    e = v * k + e * (1 - k)
                return e

            recent = closes[-80:] if len(closes) >= 80 else closes
            ema20 = _ema(recent, 20)
            ema50 = _ema(recent, 50)
            if ema20 > ema50 * 1.001:
                return "UPTREND"
            elif ema20 < ema50 * 0.999:
                return "DOWNTREND"
            return "SIDEWAYS"
        except Exception as e:
            log.warning("  ⚠️ Lỗi tính xu hướng BTC trực tiếp: %s", e)
            return "UNKNOWN"

    def _btc_correlation(self, symbol, closes_1h, window=30):
        """
        [NEW v6.9] Đo tương quan lợi suất (returns) THẬT giữa symbol và BTC bằng
        Pearson trên `window` nến 1h gần nhất — thay cho việc chỉ dựa vào luật
        cứng "BTC dẫn dắt altcoin" đã có sẵn trong HMM worker (chỉ ép về
        SIDEWAYS khi ngược BTC, không phân biệt mức độ tương quan mạnh/yếu).
        Trả về hệ số tương quan trong [-1, 1], hoặc None nếu không tính được.
        """
        if symbol == "BTCUSDT" or not closes_1h:
            return None
        try:
            now = time.time()
            if self._btc_cache["closes"] is None or (now - self._btc_cache["ts"]) > self._BTC_CACHE_TTL:
                btc_data = self.crypto.klines("BTCUSDT", "1h")
                self._btc_cache = {"closes": btc_data["close"], "ts": now}
            btc_closes = self._btc_cache["closes"]

            n = min(len(closes_1h), len(btc_closes), window + 1)
            if n < 15:
                return None
            sym_c = closes_1h[-n:]
            btc_c = btc_closes[-n:]
            sym_ret = [(sym_c[i] - sym_c[i-1]) / sym_c[i-1] for i in range(1, n) if sym_c[i-1] > 0]
            btc_ret = [(btc_c[i] - btc_c[i-1]) / btc_c[i-1] for i in range(1, n) if btc_c[i-1] > 0]
            m = min(len(sym_ret), len(btc_ret))
            if m < 10:
                return None
            sym_ret, btc_ret = sym_ret[-m:], btc_ret[-m:]

            mean_s = sum(sym_ret) / m
            mean_b = sum(btc_ret) / m
            cov    = sum((sym_ret[i] - mean_s) * (btc_ret[i] - mean_b) for i in range(m))
            std_s  = (sum((r - mean_s) ** 2 for r in sym_ret)) ** 0.5
            std_b  = (sum((r - mean_b) ** 2 for r in btc_ret)) ** 0.5
            if std_s <= 0 or std_b <= 0:
                return None
            return round(cov / (std_s * std_b), 3)
        except Exception as e:
            log.warning("  ⚠️ Lỗi tính tương quan BTC cho %s: %s", symbol, e)
            return None

    def full_analysis(self, symbol, db=None):
        fetcher, atype = self._fetcher(symbol)
        log.info("📊 Phân tích %s [%s]...", symbol, atype)
        results = {}

        # ══════════════════════════════════════════════════════════
        # [NEW] A & B: ĐĂNG KÝ VÀ ĐỌC DỮ LIỆU HMM TỪ DATABASE
        # ══════════════════════════════════════════════════════════
        hmm_regime = "UNKNOWN"
        hmm_conf = 0.0
        btc_hmm_regime = "UNKNOWN"
        
        if db is not None:
            try:
                # Import bên trong hàm để tránh lỗi Circular Import
                from sqlalchemy.dialects.postgresql import insert
                from core_api.models import TrackedSymbol, MarketRegime 

                # A. Tự động đăng ký mã coin cho HMM Worker quét
                stmt = insert(TrackedSymbol).values(symbol=symbol, is_active=True)
                stmt = stmt.on_conflict_do_nothing(index_elements=['symbol'])
                db.execute(stmt)
                db.commit()

                # B. Đọc bối cảnh thị trường HMM mới nhất
                regime_data = db.query(MarketRegime).filter(MarketRegime.symbol == symbol).first()
                if regime_data:
                    hmm_regime = regime_data.regime_name
                    hmm_conf = regime_data.confidence
                    log.info("  🧠 [HMM Context] %s: %s (Conf: %.1f%%)", symbol, hmm_regime, hmm_conf)

                # [NEW v6.9] Đọc THÊM regime CỦA RIÊNG BTC (không phải regime của
                # symbol đang xét) để dùng cho luật tương quan BTC-altcoin bên dưới.
                # regime_data ở trên là của symbol (đã được HMM worker mềm hoá theo
                # BTC), còn ở đây cần bản THÔ của chính BTC để so sánh.
                btc_hmm_regime = "UNKNOWN"
                if symbol != "BTCUSDT":
                    btc_regime_data = db.query(MarketRegime).filter(MarketRegime.symbol == "BTCUSDT").first()
                    if btc_regime_data:
                        btc_hmm_regime = btc_regime_data.regime_name
            except Exception as e:
                db.rollback()
                log.warning("  ⚠️ Lỗi giao tiếp HMM Database: %s", e)

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
        wy_1h  = results.get("1h", {}).get("wyckoff", {})
        elliott_4h = results.get("4h", {}).get("elliott", {})
        fi_1h  = results.get("1h", {}).get("fibo", {})
        vol_1h = results.get("1h", {}).get("volume", {})
        vol_4h = results.get("4h", {}).get("volume", {})

        # [NEW v6.9] Tương quan BTC-altcoin đo bằng returns thật (Pearson),
        # không chỉ dựa vào luật cứng của HMM worker.
        btc_corr = None
        btc_trend_now = "UNKNOWN"
        if atype == "CRYPTO" and symbol != "BTCUSDT":
            btc_corr = self._btc_correlation(symbol, results.get("1h", {}).get("close", []))
            btc_trend_now = self._btc_trend_now()
            log.info("  🔗 [BTC CORR] %s: corr=%s, BTC trend (EMA trực tiếp)=%s",
                     symbol, btc_corr, btc_trend_now)

        # ══════════════════════════════════════════════════════════
        # 1. KHỞI TẠO VÀ CHẠY KALMAN FILTER (Lọc nhiễu tìm True Price)
        # ══════════════════════════════════════════════════════════
        kalman_trend = "NEUTRAL"
        kalman_price = price
        kalman_data = {"detected": False}
        
        closes_1h = results.get("1h", {}).get("closes", [])
        if not closes_1h:
            closes_1h = results.get("1h", {}).get("close", [])

        if closes_1h and len(closes_1h) > 2:
            if not hasattr(self, 'quant'):
                try:
                    from quant_math import QuantRiskManager 
                    self.quant = QuantRiskManager()
                except ImportError as e:
                    log.error(f"Lỗi import QuantRiskManager: {e}") 

            if hasattr(self, 'quant'):
                try:
                    k_filter = self.quant.calculate_kalman_filter(closes_1h, r=0.05)
                    kalman_price = k_filter[-1]
                    prev_kalman = k_filter[-2]

                    if kalman_price > prev_kalman and price > kalman_price:
                        kalman_trend = "BULLISH"
                    elif kalman_price < prev_kalman and price < kalman_price:
                        kalman_trend = "BEARISH"

                    kalman_data = {
                        "detected": True,
                        "price": round(kalman_price, 4),
                        "trend": kalman_trend
                    }
                    log.info("  [Kalman] True Price: %.4f | Trend: %s", kalman_price, kalman_trend)
                except Exception as e:
                    log.warning("  ⚠️ Kalman Filter lỗi: %s", e)

        # ══════════════════════════════════════════════════════════
        # TÍNH ĐIỂM SMART SCORE
        # ══════════════════════════════════════════════════════════
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

        # 2. TÍCH HỢP KALMAN VÀO SMART SCORE
        if kalman_trend == "BULLISH": smart += 15
        elif kalman_trend == "BEARISH": smart -= 15

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
                
                liq_raw = fetcher.liquidation_levels(symbol, price)
                
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
                    
                    if swing_low > 0 and low_price < swing_low and close_price > swing_low:
                        detected_sweep = True
                        sweep_type = "BULLISH_SWEEP"
                        sweep_p = low_price
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

        # ══════════════════════════════════════════════════════════
        # [SMC] BỘ LỌC & KÍCH HOẠT VÀO LỆNH TỪ LIQUIDITY SWEEP
        # ══════════════════════════════════════════════════════════
        if sweep_data.get("detected"):
            sw_type = sweep_data.get("type")
            sw_price = sweep_data.get("price")
            
            if sw_type == "BULLISH_SWEEP":
                # 1. Chặn lệnh Short ném tiền qua cửa sổ
                if final == "SHORT":
                    log.warning(f"⛔ FILTER: Cá mập quét đáy lấy thanh khoản (Bullish Sweep @ {sw_price}) -> HỦY LỆNH SHORT")
                    final = "WAIT"
                    
                # 2. Bắt đáy: Nếu hệ thống đang phân vân (WAIT) nhưng có setup quét đáy -> Kích hoạt LONG
                elif final in ("LONG", "WAIT") and combined >= 45: # Nới lỏng điều kiện combined một chút để bắt râu
                    log.info(f"🎯 TRIGGER (SMC): Phá vỡ giả đáy cũ (Bullish Sweep @ {sw_price}) -> VÀO LỆNH LONG")
                    final = "LONG"
                    conf = round(min(95, conf + 15.0), 1) # Ép confidence lên cao để vượt qua các bộ lọc dưới
                    
            elif sw_type == "BEARISH_SWEEP":
                # 1. Chặn lệnh Long đu đỉnh
                if final == "LONG":
                    log.warning(f"⛔ FILTER: Cá mập quét đỉnh xả hàng (Bearish Sweep @ {sw_price}) -> HỦY LỆNH LONG")
                    final = "WAIT"
                    
                # 2. Bắt đỉnh: Chờ quét đỉnh xong quay đầu -> Kích hoạt SHORT
                elif final in ("SHORT", "WAIT") and combined <= 55: 
                    log.info(f"🎯 TRIGGER (SMC): Phá vỡ giả đỉnh cũ (Bearish Sweep @ {sw_price}) -> VÀO LỆNH SHORT")
                    final = "SHORT"
                    conf = round(min(95, conf + 15.0), 1)

        # ══════════════════════════════════════════════════════════
        # 2.6. [NEW v6.11] XÁC NHẬN ĐA KHUNG: 4H QUYẾT XU HƯỚNG LỚN,
        # 15M TÌM ĐIỂM VÀO THEO ĐÚNG XU HƯỚNG ĐÓ
        # ──────────────────────────────────────────────────────────
        # Trước đây avg_score/combined trộn đều cả 4 khung (15m/1h/4h/1d)
        # cùng lúc — không có thứ tự ưu tiên rõ ràng nào giữa "xu hướng lớn"
        # và "thời điểm vào cụ thể". Giờ tách bạch: 4H không rõ hướng hoặc
        # ngược tín hiệu -> không vào; 4H đồng thuận nhưng chính nến 15m chưa
        # xác nhận cùng hướng -> chờ thêm, không vội bắt đầu ngay giữa chừng.
        # ══════════════════════════════════════════════════════════
        trend_4h  = results.get("4h", {}).get("direction", "WAIT")
        trend_15m = results.get("15m", {}).get("direction", "WAIT")

        if final in ("LONG", "SHORT"):
            if trend_4h in ("LONG", "SHORT") and trend_4h != final:
                log.warning("  ⛔ [4H TREND] %s: Xu hướng lớn 4H=%s ngược với tín hiệu %s "
                            "-> hạ về WAIT (không đánh ngược xu hướng lớn).", symbol, trend_4h, final)
                final = "WAIT"
            elif trend_4h == final and trend_15m != final:
                log.info("  ⏳ [15M TRIGGER] %s: 4H đồng thuận %s nhưng nến 15m (%s) chưa xác nhận "
                         "điểm vào -> chờ nến 15m tiếp theo.", symbol, final, trend_15m)
                final = "WAIT"


       # ══════════════════════════════════════════════════════════
        # 2.5. ATR-BASED SL/TP (TỐI ƯU HÓA CHỐNG QUÉT RÂU - WHIPSAW)
        # ══════════════════════════════════════════════════════════
        atr_1h     = results.get("1h", {}).get("atr", price * 0.01)
        atr_pct_1h = results.get("1h", {}).get("atr_pct", 1.0)
        
        # A. Hệ số nhân động dựa trên HMM Regime
        atr_multiplier = 2.0  # Mức chuẩn cho Crypto (Trend Following)
        if hmm_regime == "SIDEWAYS":
            atr_multiplier = 2.5  # Đi ngang giật râu nhiều -> Nới rộng SL để tránh nhiễu

        # [NEW v6.11] LỊCH TIN CPI/PPI/NFP — chỉ áp dụng cho Crypto/Vàng (đúng
        # yêu cầu, không quét toàn bộ lịch kinh tế). Trong vùng ảnh hưởng tin:
        # SL siết gọn hơn (sl_tighten_mult<1) để nếu bị quét thanh khoản do
        # biến động tin tức thì lỗ nhỏ, và size sẽ giảm ở bước đặt lệnh (main.py).
        news_risk = {"active": False, "event": None, "size_mult": 1.0, "sl_tighten_mult": 1.0}
        if atype in ("CRYPTO", "GOLD"):
            try:
                from worker.economic_calendar import news_risk_adjustment
                news_risk = news_risk_adjustment()
            except Exception as e:
                log.debug("  Lịch tin tức lỗi (bỏ qua, không chặn): %s", e)

        # B. Tính % SL với trần (cap) được nâng lên 4.5% để chịu nhiệt Altcoin
        # Sàn dưới nâng lên 1.0% để tránh đặt SL quá sát khi thị trường đột ngột im ắng
        sl_atr_pct = max(1.0, min(4.5, atr_pct_1h * atr_multiplier))
        sl_atr_pct = sl_atr_pct * news_risk["sl_tighten_mult"]
        
        # C. Tỷ lệ R:R động
        tp1_pct    = sl_atr_pct * 1.5  # Tối thiểu R:R 1:1.5 cho TP1
        tp2_pct    = sl_atr_pct * 3.0  # R:R 1:3 cho TP2
        
        fibo4l     = results.get("4h", {}).get("fibo", {}).get("levels", {})

        atr_tp1 = round(price * (1 + tp1_pct / 100), 2)
        atr_tp2 = round(price * (1 + tp2_pct / 100), 2)
        atr_tp1_s = round(price * (1 - tp1_pct / 100), 2)
        atr_tp2_s = round(price * (1 - tp2_pct / 100), 2)

        fibo4h_trend = results.get("4h", {}).get("fibo", {}).get("trend", "")

        # ══════════════════════════════════════════════════════════
        # 3. TỐI ƯU STOPLOSS BẰNG KALMAN (Bảo vệ lệnh khỏi Whipsaw)
        # ══════════════════════════════════════════════════════════
        if final == "LONG":
            sl = round(price * (1 - sl_atr_pct / 100), 2)
            if kalman_data["detected"] and kalman_price < price:
                k_sl = kalman_price * 0.995
                if k_sl < price and ((price - k_sl) / price * 100) <= sl_atr_pct * 1.5:
                    sl = round(k_sl, 2)
                    log.info("  🛡️ Tối ưu SL LONG giấu dưới Kalman: %.4f", sl)

            if fibo4h_trend == "UPTREND":
                f272 = fibo4l.get("1.272")
                f618 = fibo4l.get("1.618")
                if (f272 and f618 and f272 > price * 1.005 and f618 > price * 1.005 and f618 > f272):
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
            if kalman_data["detected"] and kalman_price > price:
                k_sl = kalman_price * 1.005 
                if k_sl > price and ((k_sl - price) / price * 100) <= sl_atr_pct * 1.5:
                    sl = round(k_sl, 2)
                    log.info("  🛡️ Tối ưu SL SHORT giấu trên Kalman: %.4f", sl)

            if fibo4h_trend == "DOWNTREND":
                f272 = fibo4l.get("1.272")
                f618 = fibo4l.get("1.618")
                if (f272 and f618 and f272 < price * 0.995 and f618 < price * 0.995 and f618 < f272):
                    tp1 = round(f272, 2)
                    tp2 = round(f618, 2)
                else:
                    tp1 = atr_tp1_s
                    tp2 = atr_tp2_s
            else:
                tp1 = atr_tp1_s
                tp2 = atr_tp2_s

        else:
            sl  = round(price * (1 - sl_atr_pct / 100), 2)
            tp1 = atr_tp1
            tp2 = atr_tp2

        if final == "LONG":
            if tp2 <= tp1:
                tp1 = atr_tp1
                tp2 = atr_tp2
            if tp1 <= price: tp1 = atr_tp1
            if tp2 <= tp1: tp2 = round(tp1 * 1.015, 2)

        elif final == "SHORT":
            if tp2 >= tp1:
                tp1 = atr_tp1_s
                tp2 = atr_tp2_s
            if tp1 >= price: tp1 = atr_tp1_s
            if tp2 >= tp1: tp2 = round(tp1 * 0.985, 2)

        rr_ratio = round(tp1_pct / sl_atr_pct, 2) if sl_atr_pct > 0 else 2.0

        # ══════════════════════════════════════════════════════════
        # 3.5 [NEW v6.12] MỞ RỘNG 4 MỐC TP (thay vì 2) — giữ NGUYÊN cách tính
        # tp1/tp2 ở trên (kể cả nhánh ưu tiên Fibonacci khi trend 4H khớp), tp3/
        # tp4 nối tiếp theo đúng đơn vị khoảng cách tp1->tp2 để nhất quán dù
        # tp1/tp2 đến từ ATR hay Fibonacci — không tính lại từ đầu, tránh rủi ro
        # phá vỡ logic tp1/tp2 đã chạy ổn định.
        # ══════════════════════════════════════════════════════════
        leg_12 = abs(tp2 - tp1)
        if final == "LONG":
            tp3 = round(tp2 + leg_12 * 0.85, 2)
            tp4 = round(tp3 + leg_12 * 1.15, 2)
        elif final == "SHORT":
            tp3 = round(tp2 - leg_12 * 0.85, 2)
            tp4 = round(tp3 - leg_12 * 1.15, 2)
        else:
            tp3, tp4 = tp2, tp2

        candle_1h = results.get("1h", {}).get("candle", {})
        candle_4h = results.get("4h", {}).get("candle", {})
        ms_1h     = results.get("1h", {}).get("market_structure", {})
        ms_4h     = results.get("4h", {}).get("market_structure", {})

        all_bull_patterns = candle_1h.get("bull_patterns", []) + candle_4h.get("bull_patterns", [])
        all_bear_patterns = candle_1h.get("bear_patterns", []) + candle_4h.get("bear_patterns", [])

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


        # ══════════════════════════════════════════════════════════
        # ─── XÁC SUẤT (BAYES) & KỲ VỌNG TOÁN HỌC (EV) ───
        # ══════════════════════════════════════════════════════════
        base_odds = 0.818 
        likelihood = 1.0
        p_win = 0.45
        ev_ratio = 0.0

        # [NEW v6.5] Ngưỡng funding rate (%/8h, xem fetcher.CryptoFetcher.funding_rate).
        # Đặt thành hằng số riêng để sau này khung backtest/Optuna tối ưu tự động dò lại
        # thay vì phải sửa số ma thuật rải rác trong code.
        FUNDING_EXTREME_PCT = 0.05    # "đông" — áp dụng hệ số điều chỉnh likelihood
        FUNDING_DANGER_PCT  = 0.10    # "quá đông" — chặn cứng, hạ về WAIT bất kể EV

        if final == "LONG":
            if cvd_tr == "BULLISH": likelihood *= 1.3
            elif cvd_tr == "BULLISH_DIV": likelihood *= 1.5
            elif cvd_tr in ("BEARISH", "BEARISH_DIV"): likelihood *= 0.6
            
            if wy_4h.get("bias") == "BULLISH": likelihood *= 1.2
            elif wy_4h.get("bias") == "BEARISH": likelihood *= 0.7
            
            if wh_1h.get("detected") and wh_1h.get("type") == "WHALE_BUY": likelihood *= 1.4
            elif wh_1h.get("detected") and wh_1h.get("type") == "WHALE_SELL": likelihood *= 0.6
            
            if bo_1h.get("type") == "BREAKOUT_UP": likelihood *= 1.3
            
            if ob_data.get("detected") and ob_data.get("imbalance", 0) > 1.5: likelihood *= 1.15
            elif ob_data.get("detected") and ob_data.get("imbalance", 0) < -1.5: likelihood *= 0.85
            
            if sweep_data.get("detected") and sweep_data.get("type") == "BULLISH_SWEEP": likelihood *= 1.3
            
            if candle_summary["confirm_long"]: likelihood *= 1.2
            
            # 4. TÍCH HỢP KALMAN
            if kalman_trend == "BULLISH": likelihood *= 1.3
            elif kalman_trend == "BEARISH": likelihood *= 0.7

            # 5. [NEW] TÍCH HỢP HMM VÀO XÁC SUẤT BAYES
            if hmm_regime == "UPTREND": likelihood *= 1.4
            elif hmm_regime == "DOWNTREND": likelihood *= 0.5
            elif hmm_regime == "SIDEWAYS": likelihood *= 0.6

            # 6. [NEW v6.5] TÍCH HỢP OPEN INTEREST + FUNDING RATE
            # (oi_signal/funding đã được fetch từ trước nhưng chỉ nằm trong report,
            # chưa hề ảnh hưởng tới p_win/EV/quyết định vào lệnh — nay đã nối vào)
            if oi_signal == "LONG_BUILD": likelihood *= 1.25      # Tiền mới thật sự vào Long -> xác nhận
            elif oi_signal == "SHORT_BUILD": likelihood *= 0.75  # Tiền đang vào Short trong khi ta định LONG -> nghịch dòng tiền
            elif oi_signal == "SHORT_SQUEEZE": likelihood *= 1.3  # Short đang bị ép -> nhiên liệu đẩy giá lên tiếp
            elif oi_signal == "LONG_LIQ": likelihood *= 0.7      # Long đang bị thanh lý hàng loạt -> áp lực bán chưa hết

            if funding >= FUNDING_EXTREME_PCT:
                likelihood *= 0.75   # Long đã quá đông (funding cao) -> rủi ro bị squeeze ngược nếu vào thêm
            elif funding <= -FUNDING_EXTREME_PCT:
                likelihood *= 1.2    # Short đang quá đông (funding âm sâu) -> có lợi cho kịch bản Long (short squeeze)

            # 7. [NEW v6.10] WYCKOFF SPRING — wyckoff() đã phát hiện Spring trong
            # events[] từ lâu nhưng CHƯA hề ảnh hưởng likelihood (chỉ hiển thị).
            # Spring (đáy giả, volume thấp) là tín hiệu KẾT THÚC tích luỹ — đúng
            # loại setup nên được THƯỞNG dù đang SIDEWAYS, khác với 1 lệnh LONG
            # ngẫu nhiên giữa lúc sideways (đã bị phạt 0.6x ở trên).
            if any("SPRING" in e for e in wy_1h.get("events", [])):
                likelihood *= 1.6
                log.info("  🌱 [WYCKOFF SPRING] %s: đáy giả volume thấp -> tăng độ tin cậy LONG", symbol)

            # 8. [NEW v6.10] ELLIOTT WAVE — đã tính score_adj cho điểm kỹ thuật
            # thô (analyze_tf) nhưng CHƯA đưa vào lớp xác suất Bayes. Bổ sung ở
            # đây để nhất quán với các chỉ báo hướng khác (Fibonacci, Wyckoff...).
            if elliott_4h.get("trend") == "UPTREND": likelihood *= 1.15
            elif elliott_4h.get("trend") == "DOWNTREND": likelihood *= 0.85

            # 9. [FIX v6.13] TƯƠNG QUAN BTC-ALTCOIN — trước đây điều kiện xu
            # hướng BTC dựa vào btc_hmm_regime (bảng MarketRegime, HMM worker
            # cập nhật mỗi 5 phút) -- nhãn đó từng báo SIDEWAYS cho BTC ngay cả
            # khi Kalman/4H/breakout đều xác nhận BTC tăng rõ ràng, khiến cổng
            # này KHÔNG BAO GIỜ kích hoạt trong đợt vừa rồi dù tương quan cao.
            # Đổi sang btc_trend_now (tính trực tiếp từ EMA, không phụ thuộc
            # HMM worker). Fail-SAFE: nếu tính tương quan lỗi (btc_corr=None)
            # nhưng xu hướng BTC đã rõ -> vẫn phạt vừa phải (giả định tương
            # quan điển hình ~0.6 của altcoin lớn với BTC) thay vì bỏ qua hẳn.
            if symbol != "BTCUSDT" and btc_trend_now == "DOWNTREND":
                if btc_corr is not None and btc_corr >= 0.6:
                    likelihood *= 0.5
                    log.warning("  ⛔ [BTC CORR] %s tương quan mạnh (%.2f) với BTC đang DOWNTREND -> "
                                "giảm mạnh độ tin cậy LONG ngược xu thế BTC.", symbol, btc_corr)
                elif btc_corr is None:
                    likelihood *= 0.7
                    log.warning("  ⚠️ [BTC CORR] %s: không tính được tương quan nhưng BTC đang DOWNTREND rõ "
                                "-> vẫn giảm độ tin cậy LONG theo hướng an toàn.", symbol)

        elif final == "SHORT":
            if cvd_tr == "BEARISH": likelihood *= 1.3
            elif cvd_tr == "BEARISH_DIV": likelihood *= 1.5
            elif cvd_tr in ("BULLISH", "BULLISH_DIV"): likelihood *= 0.6
            
            if wy_4h.get("bias") == "BEARISH": likelihood *= 1.2
            elif wy_4h.get("bias") == "BULLISH": likelihood *= 0.7
            
            if wh_1h.get("detected") and wh_1h.get("type") == "WHALE_SELL": likelihood *= 1.4
            elif wh_1h.get("detected") and wh_1h.get("type") == "WHALE_BUY": likelihood *= 0.6
            
            if bo_1h.get("type") == "BREAKOUT_DOWN": likelihood *= 1.3
            
            if ob_data.get("detected") and ob_data.get("imbalance", 0) < -1.5: likelihood *= 1.15
            elif ob_data.get("detected") and ob_data.get("imbalance", 0) > 1.5: likelihood *= 0.85
            
            if sweep_data.get("detected") and sweep_data.get("type") == "BEARISH_SWEEP": likelihood *= 1.3
            
            if candle_summary["confirm_short"]: likelihood *= 1.2

            # 4. TÍCH HỢP KALMAN
            if kalman_trend == "BEARISH": likelihood *= 1.3
            elif kalman_trend == "BULLISH": likelihood *= 0.7

            # 5. [NEW] TÍCH HỢP HMM VÀO XÁC SUẤT BAYES
            if hmm_regime == "DOWNTREND": likelihood *= 1.4
            elif hmm_regime == "UPTREND": likelihood *= 0.5
            elif hmm_regime == "SIDEWAYS": likelihood *= 0.6

            # 6. [NEW v6.5] TÍCH HỢP OPEN INTEREST + FUNDING RATE
            if oi_signal == "SHORT_BUILD": likelihood *= 1.25
            elif oi_signal == "LONG_BUILD": likelihood *= 0.75
            elif oi_signal == "LONG_LIQ": likelihood *= 1.3      # Long đang bị thanh lý -> nhiên liệu đẩy giá xuống tiếp
            elif oi_signal == "SHORT_SQUEEZE": likelihood *= 0.7  # Short đang bị ép -> nghịch với kịch bản Short mới

            if funding <= -FUNDING_EXTREME_PCT:
                likelihood *= 0.75   # Short đã quá đông -> rủi ro bị squeeze ngược nếu vào thêm
            elif funding >= FUNDING_EXTREME_PCT:
                likelihood *= 1.2    # Long đang quá đông (funding dương cao) -> có lợi cho kịch bản Short

            # 7. [NEW v6.10] WYCKOFF UTAD (Upthrust After Distribution) — đỉnh
            # giả kèm volume mở rộng, dấu hiệu kết thúc phân phối. Thưởng dù
            # đang SIDEWAYS, đúng tinh thần "Spring/Upthrust là ngoại lệ hợp lý".
            if any("UTAD" in e for e in wy_1h.get("events", [])):
                likelihood *= 1.6
                log.info("  ⛰️ [WYCKOFF UTAD] %s: đỉnh giả volume mở rộng -> tăng độ tin cậy SHORT", symbol)

            # 8. [NEW v6.10] ELLIOTT WAVE
            if elliott_4h.get("trend") == "DOWNTREND": likelihood *= 1.15
            elif elliott_4h.get("trend") == "UPTREND": likelihood *= 0.85

            # 9. [FIX v6.13] TƯƠNG QUAN BTC-ALTCOIN — đây chính là nhánh gây ra
            # lỗi vừa báo (SHORT altcoin ngược lúc BTC đang tăng rõ): trước dùng
            # btc_hmm_regime (nhãn HMM worker, từng báo SIDEWAYS dù BTC rõ ràng
            # đang tăng theo Kalman/4H/breakout) nên KHÔNG BAO GIỜ kích hoạt.
            # Đổi sang btc_trend_now (EMA trực tiếp, không phụ thuộc HMM worker)
            # + fail-safe khi không tính được tương quan.
            if symbol != "BTCUSDT" and btc_trend_now == "UPTREND":
                if btc_corr is not None and btc_corr >= 0.6:
                    likelihood *= 0.5
                    log.warning("  ⛔ [BTC CORR] %s tương quan mạnh (%.2f) với BTC đang UPTREND -> "
                                "giảm mạnh độ tin cậy SHORT ngược xu thế BTC.", symbol, btc_corr)
                elif btc_corr is None:
                    likelihood *= 0.7
                    log.warning("  ⚠️ [BTC CORR] %s: không tính được tương quan nhưng BTC đang UPTREND rõ "
                                "-> vẫn giảm độ tin cậy SHORT theo hướng an toàn.", symbol)

        # [NEW v6.9] VOLUME GIẢ (Z-SCORE) — vol_1h.vol_suspicious tính ở
        # indicators.py (z-score bất thường + giá không theo kịp volume, kiểu
        # quét thanh khoản/wash volume). Đặt TRƯỚC khi likelihood -> p_win/EV để
        # thực sự ảnh hưởng quyết định, không chỉ để hiển thị. Áp dụng chung cho
        # cả LONG/SHORT vì đây là vấn đề CHẤT LƯỢNG tín hiệu, không phải hướng.
        if final in ("LONG", "SHORT") and vol_1h.get("vol_suspicious"):
            likelihood *= 0.7
            log.warning("  ⚠️ [VOLUME GIẢ] %s 1h: z-score=%.2f bất thường nhưng giá không xác nhận "
                        "-> giảm độ tin cậy tín hiệu.", symbol, vol_1h.get("vol_zscore", 0))

        if final in ("LONG", "SHORT"):
            bayes_odds = base_odds * likelihood
            p_win = bayes_odds / (1 + bayes_odds)
            
            reward_tp1_ratio = (abs(tp1 - price) / price) / (sl_atr_pct / 100) if sl_atr_pct > 0 else 2.0
            reward_tp2_ratio = (abs(tp2 - price) / price) / (sl_atr_pct / 100) if sl_atr_pct > 0 else 3.5
            avg_reward_ratio = (reward_tp1_ratio + reward_tp2_ratio) / 2
            
            ev_ratio = (p_win * avg_reward_ratio) - ((1 - p_win) * 1.0)
            
            log.info("  [Bayes EV] %s %s: P(win)=%.1f%%, EV_Ratio=%.2f (Likelihood=%.2f)",
                     final, symbol, p_win * 100, ev_ratio, likelihood)

            # [NEW v6.7] NGUYÊN NHÂN CHÍNH của "SL vẫn nhiều": EV_ratio dùng
            # avg_reward_ratio ~2.25 (trung bình TP1=1.5x và TP2=3.0x so với SL),
            # nên p_win < 50% VẪN qua được ngưỡng EV nếu R:R đẹp trên giấy — nhưng
            # muốn chốt được TP1 (nửa vị thế) trước khi SL, giá phải đi ĐỦ XA
            # theo hướng thuận (1.5x SL) TRƯỚC. Với p_win<50%, phần lớn lệnh thua
            # sẽ dính SL TOÀN BỘ (chưa kịp chốt gì) chứ không phải thua sau khi đã
            # khoá 1 phần lời — đúng cảm giác "SL vẫn nhiều". EV dương trên giấy
            # không đồng nghĩa tỉ lệ THẮNG > 50%. Thêm sàn p_win RIÊNG, độc lập với
            # EV, để ép hệ thống chỉ vào lệnh khi tự tin THẮNG nhiều hơn thua.
            MIN_P_WIN = 0.55   # có biên an toàn trên 50% vì bản thân p_win cũng chỉ là ước lượng
            if p_win < MIN_P_WIN:
                log.warning("  ⚠️ P(win)=%.1f%% < %.0f%% -> hạ về WAIT (EV đẹp trên giấy không đủ, "
                            "cần xác suất thắng thật > 50%% mới giữ đúng mục tiêu winrate).",
                            p_win * 100, MIN_P_WIN * 100)
                final = "WAIT"
                conf = round(conf * 0.8, 1)
            elif ev_ratio < 0.15:
                log.warning("  ⚠️ EV(%.2f) quá thấp, hạ cấp thành WAIT", ev_ratio)
                final = "WAIT"
                conf = round(conf * 0.8, 1)
            elif ev_ratio > 0.5:
                conf = round(min(95, conf + (ev_ratio * 5)), 1)

            # [NEW v6.5] CHẶN CỨNG khi funding quá đông cùng chiều lệnh định vào —
            # giống cơ chế chống FOMO tại vùng Premium/Discount: xác suất/EV có thể
            # vẫn đẹp trên giấy nhưng vào thêm vào phía đã quá tải là mồi cho squeeze.
            if final == "LONG" and funding >= FUNDING_DANGER_PCT:
                log.warning("  ⛔ [FUNDING QUÁ ĐÔNG] Long funding=%.3f%%/8h >= %.2f%% -> hạ về WAIT.",
                            funding, FUNDING_DANGER_PCT)
                final = "WAIT"
            elif final == "SHORT" and funding <= -FUNDING_DANGER_PCT:
                log.warning("  ⛔ [FUNDING QUÁ ĐÔNG] Short funding=%.3f%%/8h <= -%.2f%% -> hạ về WAIT.",
                            funding, FUNDING_DANGER_PCT)
                final = "WAIT"

            # [NEW v6.8] CHẶN CỨNG SAU CHUỖI THUA LIÊN TIẾP — bổ sung cho
            # apply_statistical_overlay (llm_agents.py), vốn CHỈ giảm confidence
            # tối đa ~30% (không bao giờ chặn hẳn) và CHỈ chạy SAU KHI đã tốn 4
            # lượt gọi LLM của pipeline 12-agent. Ở đây chặn NGAY tại engine,
            # trước khi vào pipeline — vừa tiết kiệm LLM call cho lệnh nhiều khả
            # năng bị lọc, vừa đảm bảo chuỗi thua thật sự tạm dừng thay vì chỉ bị
            # trừ điểm confidence (có thể vẫn đủ qua min_confidence của user).
            if final in ("LONG", "SHORT") and db is not None:
                STREAK_N = 3
                STREAK_COOLDOWN_HOURS = 4
                try:
                    from core_api.models import TradeJournal
                    recent = (db.query(TradeJournal)
                              .filter(TradeJournal.symbol == symbol,
                                      TradeJournal.direction == final)
                              .order_by(TradeJournal.timestamp.desc())
                              .limit(STREAK_N)
                              .all())
                    if len(recent) >= STREAK_N and all((r.pnl_pct or 0) <= 0 for r in recent):
                        hours_since = (datetime.utcnow() - recent[0].timestamp).total_seconds() / 3600
                        if hours_since < STREAK_COOLDOWN_HOURS:
                            log.warning("  ⛔ [COOLDOWN] %s %s: %d lệnh thua liên tiếp gần nhất "
                                        "(lệnh cuối cách %.1fh) -> tạm nghỉ %dh, hạ về WAIT.",
                                        symbol, final, STREAK_N, hours_since, STREAK_COOLDOWN_HOURS)
                            final = "WAIT"
                except Exception as e:
                    log.debug("  Streak cooldown check lỗi (bỏ qua, không chặn): %s", e)

        ev_data = {
            "p_win": round(p_win * 100, 1),
            "ev_ratio": round(ev_ratio, 2),
            "likelihood": round(likelihood, 2)
        }

        # [NEW v6.12] TỈ LỆ CHỐT MỖI MỐC TP — "tự động tính toán" theo p_win:
        # p_win càng cao (càng tự tin) -> chốt ít hơn ở mốc gần, để nhiều hơn
        # chạy xa; p_win càng sát sàn MIN_P_WIN (kém tự tin hơn dù vẫn qua được
        # cổng) -> chốt nhanh nhiều hơn ở mốc gần, ít rủi ro để phần lớn chạy xa.
        if p_win >= 0.65:
            tp_close_pcts = [0.25, 0.25, 0.25, 0.25]
        elif p_win >= 0.60:
            tp_close_pcts = [0.30, 0.25, 0.25, 0.20]
        else:
            tp_close_pcts = [0.40, 0.25, 0.20, 0.15]

        tp_levels = [
            {"level": 1, "price": tp1, "close_pct": tp_close_pcts[0]},
            {"level": 2, "price": tp2, "close_pct": tp_close_pcts[1]},
            {"level": 3, "price": tp3, "close_pct": tp_close_pcts[2]},
            {"level": 4, "price": tp4, "close_pct": tp_close_pcts[3]},
        ]

        return {
            "symbol": symbol, "asset_type": atype,
            "price": price, "final": final, "confidence": conf,
            "longs": longs, "shorts": shorts, "timeframes": results,
            # tp2 giữ nguyên nghĩa CŨ (mốc thứ 2) để không phá vỡ chỗ nào còn đọc
            # trực tiếp plan["tp2"] — muốn dùng đủ 4 mốc thì đọc plan["tp_levels"].
            "plan": {"entry": price, "sl": sl, "tp1": tp1, "tp2": tp2, "tp_levels": tp_levels},
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
            "kalman": kalman_data,
            "hmm": {"regime": hmm_regime, "confidence": hmm_conf}, # Trả về dữ liệu HMM để hiển thị trên Telegram nếu cần
            "btc_correlation": btc_corr, "btc_hmm_regime": btc_hmm_regime, "btc_trend_now": btc_trend_now,
            "news_risk": news_risk,
            "timestamp": datetime.now().strftime("%d/%m %H:%M"),
            "bayes_ev": ev_data,
        }


