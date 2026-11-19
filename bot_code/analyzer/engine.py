# ═══════════════════════════════════════════════════════════
# PURE TRADERSZ SIGNAL ENGINE — NAKED CHART PRICE ACTION
# ═══════════════════════════════════════════════════════════
# Chỉ sử dụng phương pháp TraderSZ:
#   - Macro Opens: Year Open (YO), Monthly Open (MO), Weekly Open (WO), Daily Open (DO)
#   - Dealing Ranges: PDH/PDL, Monday High/Low, PWH/PWL, Asian Range
#   - 50% Equilibrium (EQ) & No-Trade Zone
#   - Deviation / SFP (Swing Failure Pattern) & "Return to Range"
#   - Precision Wick SL & Range-to-Range Targets (TP1 = EQ 50%, TP2 = Biên đối diện)
# ═══════════════════════════════════════════════════════════

import logging
import threading
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, Optional

from analyzer.config import Config
from analyzer.fetcher import CryptoFetcher, StockFetcher
from analyzer.indicators import PriceActionPrimitives
from analyzer.traderssz_framework import TradersszFramework

log = logging.getLogger("analyzer.engine")
cfg = Config()


class SignalEngine:
    # Các khung thời gian cốt lõi của TraderSZ: 15m (Precision), 1h/4h (SFP Confirmation), 1d (HTF Opens & Ranges)
    TIMEFRAMES = ["15m", "1h", "4h", "1d"]

    _executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="traderssz-tf")
    _btc_cache_lock = threading.Lock()
    _btc_context_cache: Optional[Dict[str, Any]] = None
    _btc_cache_ts: float = 0.0
    _btc_cache_ttl: float = 120.0

    def __init__(self):
        self.crypto = CryptoFetcher()
        self.stock = StockFetcher()

    @classmethod
    def _update_btc_cache(cls, btc_ctx: Dict[str, Any]):
        with cls._btc_cache_lock:
            cls._btc_context_cache = btc_ctx
            cls._btc_cache_ts = time.time()

    def _get_btc_context(self) -> Optional[Dict[str, Any]]:
        """Lấy thiên hướng dẫn dắt thị trường của BTC (cached 120s để đồng bộ toàn bộ Altcoin Crypto)."""
        now = time.time()
        with self._btc_cache_lock:
            if self._btc_context_cache is not None and (now - self._btc_cache_ts) < self._btc_cache_ttl:
                return dict(self._btc_context_cache)
        try:
            btc_klines = self._fetch_all_klines("BTCUSDT", self.crypto)
            btc_price = self.crypto.price("BTCUSDT")
            if btc_price <= 0:
                for tf in ("15m", "1h", "4h", "1d"):
                    kl = btc_klines.get(tf)
                    if kl and kl.get("close"):
                        btc_price = float(kl["close"][-1])
                        break
            if btc_price <= 0:
                return None
            btc_ctx = TradersszFramework.evaluate_btc_bias(btc_klines, float(btc_price))
            self._update_btc_cache(btc_ctx)
            return dict(btc_ctx)
        except Exception as e:
            log.warning("Không thể lấy BTC Leader Bias (bỏ qua lọc đồng pha): %s", e)
            return None

    def _fetcher(self, symbol: str):
        s_clean = symbol.upper().replace("-", "").replace("_", "")
        if "NCCOGOLD" in s_clean or "GOLD" in s_clean or "XAU" in s_clean:
            return self.stock, "GOLD"
        if "NCCOXAG" in s_clean or "SILVER" in s_clean or "XAG" in s_clean:
            return self.stock, "SILVER"
        if s_clean in ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "HYPEUSDT"):
            return self.crypto, "CRYPTO"
        if (s_clean in ("TSLA", "NVDA", "SPY", "QQQ")
                or any(k in s_clean for k in ("TSLA", "NVDA", "SPY", "QQQ"))
                or s_clean.startswith("NCSK")):
            return self.stock, "STOCK"
        return self.crypto, "CRYPTO"

    def is_tradeable(self, symbol: str):
        s_clean = symbol.upper().replace("-", "").replace("_", "")
        if ("NCCOGOLD" in s_clean or "GOLD" in s_clean or "XAU" in s_clean
                or "NCCOXAG" in s_clean or "SILVER" in s_clean or "XAG" in s_clean):
            return self.stock.is_gold_open()
        if s_clean in ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "HYPEUSDT"):
            return True, "Crypto 24/7"
        if (s_clean in ("TSLA", "NVDA", "SPY", "QQQ")
                or any(k in s_clean for k in ("TSLA", "NVDA", "SPY", "QQQ"))
                or s_clean.startswith("NCSK")):
            return self.stock.market_open()
        return True, "OK"

    def _fetch_all_klines(self, symbol: str, fetcher) -> Dict[str, Optional[dict]]:
        """Tải song song dữ liệu nến 15m, 1h, 4h và 1d (1d lấy 370 ngày để tính Year Open chính xác)."""
        limits = {"15m": 120, "1h": 150, "4h": 120, "1d": 370}
        out: Dict[str, Optional[dict]] = {}
        futures = {
            self._executor.submit(fetcher.klines, symbol, tf, limits[tf]): tf
            for tf in self.TIMEFRAMES
        }
        for fut in as_completed(futures):
            tf = futures[fut]
            try:
                out[tf] = fut.result()
            except Exception as e:
                log.warning("Fetch klines %s [%s] lỗi: %s", symbol, tf, e)
                out[tf] = None
        return out

    @staticmethod
    def _build_tf_summary(
        klines_by_tf: Dict[str, Optional[dict]],
        traderssz_res: Dict[str, Any],
    ) -> Dict[str, Dict[str, Any]]:
        """Tóm tắt trạng thái thuần Price Action theo từng khung thời gian."""
        htf_data = traderssz_res.get("htf_opens_and_ranges", {})
        rh = htf_data.get("range_high", 0.0)
        rl = htf_data.get("range_low", 0.0)
        eq = htf_data.get("eq_50", 0.0)
        active_setup = traderssz_res.get("setup", {})
        active_tf = active_setup.get("tf") if active_setup.get("detected") else None
        active_dir = active_setup.get("direction", "WAIT")

        summary: Dict[str, Dict[str, Any]] = {}
        for tf in ("15m", "1h", "4h", "1d"):
            kl = klines_by_tf.get(tf)
            if not kl or not kl.get("close"):
                summary[tf] = {"direction": "WAIT", "score": 50, "note": "Thiếu dữ liệu"}
                continue

            c = kl["close"][-1]
            o = kl["open"][-1]
            rej = PriceActionPrimitives.candle_rejection(o, kl["high"][-1], kl["low"][-1], c)

            if active_tf == tf and active_dir in ("LONG", "SHORT"):
                tf_dir = active_dir
                tf_score = int(traderssz_res.get("confidence", 80))
                st = active_setup.get("setup_type", "")
                if st == "RANGE_BREAKOUT_CONTINUATION":
                    note = "Breakout Tiếp Diễn"
                elif st == "LEVEL_RECLAIM_SR_FLIP":
                    note = "Reclaim & S/R Flip"
                else:
                    note = f"SFP {st}"
            elif eq > 0:
                if c >= rh:
                    tf_dir = "LONG" if active_dir == "LONG" else "WAIT"
                    tf_score = 65 if active_dir == "LONG" else 45
                    note = "Phá vỡ trên RH" if active_dir == "LONG" else "Vượt RH — chờ Breakout hoặc Return"
                elif c <= rl:
                    tf_dir = "SHORT" if active_dir == "SHORT" else "WAIT"
                    tf_score = 65 if active_dir == "SHORT" else 45
                    note = "Phá vỡ dưới RL" if active_dir == "SHORT" else "Thủng RL — chờ Breakout hoặc Return"
                elif c > eq and rej["is_bearish_pin"]:
                    tf_dir = "SHORT"
                    tf_score = 65
                    note = "Từ chối vùng Premium"
                elif c < eq and rej["is_bullish_pin"]:
                    tf_dir = "LONG"
                    tf_score = 65
                    note = "Rút chân vùng Discount"
                else:
                    tf_dir = "WAIT"
                    tf_score = 50
                    note = "Trong Range"
            else:
                tf_dir = "WAIT"
                tf_score = 50
                note = "Naked PA"

            summary[tf] = {"direction": tf_dir, "score": tf_score, "note": note}
        return summary

    def full_analysis(self, symbol: str, db=None) -> Dict[str, Any]:
        fetcher, atype = self._fetcher(symbol)
        mkt_open, mkt_note = self.is_tradeable(symbol)

        # 1. Lấy dữ liệu nến đa khung + giá realtime
        klines_by_tf = self._fetch_all_klines(symbol, fetcher)
        live_price = fetcher.price(symbol)

        if live_price <= 0:
            for tf in ("15m", "1h", "4h", "1d"):
                kl = klines_by_tf.get(tf)
                if kl and kl.get("close"):
                    live_price = float(kl["close"][-1])
                    break

        if live_price <= 0:
            raise ValueError(f"Không lấy được giá cho {symbol}")

        price = round(float(live_price), 4)

        # 2. Xác định BTC Leader Bias (chỉ áp dụng cho Altcoin Crypto, không áp dụng cho Chứng khoán/Vàng)
        is_btc = "BTC" in symbol.upper()
        btc_ctx: Optional[Dict[str, Any]] = None
        if atype == "CRYPTO" and not is_btc:
            btc_ctx = self._get_btc_context()

        # 3. Chạy Pure TraderSZ Engine (Opens + Ranges + 50% EQ + Deviation/SFP + BTC Alignment)
        traderssz_res = TradersszFramework.evaluate_traderssz_complete(
            klines_by_tf=klines_by_tf,
            current_price=price,
            sl_buffer_pct=getattr(cfg, "TRADERSSZ_SL_BUFFER_PCT", 0.0040),
            min_rr=getattr(cfg, "TRADERSSZ_MIN_RR", 1.5),
            btc_bias=btc_ctx,
        )

        if atype == "CRYPTO" and is_btc:
            btc_ctx = TradersszFramework.evaluate_btc_bias(
                btc_klines_by_tf=klines_by_tf,
                btc_price=price,
                btc_result=traderssz_res,
            )
            self._update_btc_cache(btc_ctx)
            traderssz_res["btc_bias"] = btc_ctx

        final = traderssz_res["signal"]
        conf = float(traderssz_res["confidence"])
        htf_data = traderssz_res["htf_opens_and_ranges"]
        htf_bias = traderssz_res["htf_bias"]
        setup = traderssz_res["setup"]
        session_ctx = traderssz_res["session"]

        if setup.get("setup_type") == "BTC_ALIGNMENT_BLOCK":
            log.info(
                "  🛡️ [BTC ALIGNMENT] %s: Chặn %s (%s %s) vì BTC đang %s (%s)",
                symbol,
                setup.get("blocked_direction", "?"),
                str(setup.get("blocked_tf", "?")).upper(),
                setup.get("blocked_range", ""),
                (btc_ctx or {}).get("bias", "?"),
                (btc_ctx or {}).get("reason", ""),
            )

        # 3. Dữ liệu phái sinh / Orderbook (chỉ dùng kiểm tra thanh khoản cản sát TP1, không dùng chỉ báo trễ)
        oi_delta = 0.0
        funding = 0.0
        oi_signal = "NEUTRAL"
        oi_desc = "Price Action thuần"
        ob_raw = {}
        ob_data = {"detected": False, "support_wall": 0.0, "resist_wall": 0.0, "imbalance": 0.0}

        if atype == "CRYPTO":
            try:
                _, oi_delta = fetcher.open_interest(symbol)
                funding = fetcher.funding_rate(symbol)
                ob_raw = fetcher.order_book(symbol, depth=50)
                if ob_raw.get("ok"):
                    bid_walls = ob_raw.get("bid_walls", [])
                    ask_walls = ob_raw.get("ask_walls", [])
                    ob_data = {
                        "detected": bool(bid_walls or ask_walls),
                        "support_wall": bid_walls[0]["price"] if bid_walls else ob_raw.get("best_bid", 0.0),
                        "resist_wall": ask_walls[0]["price"] if ask_walls else ob_raw.get("best_ask", 0.0),
                        "imbalance": ob_raw.get("imbalance", 0.0),
                    }
                    if abs(funding) >= 0.05:
                        oi_signal = "CROWDED_LONG" if funding > 0 else "CROWDED_SHORT"
                        oi_desc = f"Funding {funding:+.4f}%/8h"
            except Exception as e:
                log.debug("Derivatives context %s: %s", symbol, e)

        # 4. Theo dõi lịch vĩ mô tham khảo (TraderSZ: giá thể hiện trước tin tức — không chặn kèo Price Action thuần)
        news_risk = {"active": False, "event": "", "size_mult": 1.0, "pause_trading": False}
        try:
            from worker.economic_calendar import news_risk_adjustment
            nr = news_risk_adjustment()
            if isinstance(nr, dict):
                news_risk = nr
        except Exception:
            pass

        if news_risk.get("pause_trading") and final != "WAIT":
            log.warning("📰 [NEWS PAUSE] %s: Tạm hoãn mở mới %s vì đang trong cửa sổ tin lớn (%s)",
                        symbol, final, news_risk.get("event"))
            final = "WAIT"
            conf = 45.0
            traderssz_res["signal"] = "WAIT"
            traderssz_res["reason"] = f"⏸️ Tạm đứng ngoài trong cửa sổ tin lớn ({news_risk.get('event')}) theo cấu hình."

        # 5. Tính Kế hoạch Lệnh chuẩn TraderSZ:
        #    - Entry: Giá hiện tại khi nến Return to Range xác nhận
        #    - SL: Sau đỉnh/đáy râu Deviation (Precision Wick SL)
        #    - TP1 (50%): Trục giữa 50% Equilibrium (EQ)
        #    - TP2 (50%): Biên đối diện của Range (Range Low nếu Short / Range High nếu Long)
        h1_kl = klines_by_tf.get("1h") or klines_by_tf.get("15m")
        atr_val = (
            PriceActionPrimitives.true_range_volatility(h1_kl["high"], h1_kl["low"], h1_kl["close"])
            if h1_kl else price * 0.012
        )

        if final in ("LONG", "SHORT") and setup.get("detected"):
            sl = float(setup["sl"])
            tp1 = float(setup["tp1"])
            tp2 = float(setup["tp2"])
            sl_pct = float(setup["sl_pct"])
            rr_ratio = float(setup["rr_tp2"])
        else:
            # Kế hoạch tham khảo khi WAIT (dựa trên biên Range hiện tại)
            rh = float(htf_data.get("range_high", price * 1.02))
            rl = float(htf_data.get("range_low", price * 0.98))
            eq = float(htf_data.get("eq_50", price))
            if htf_data.get("market_zone") == "PREMIUM":
                sl = round(rh * 1.003, 4)
                tp1 = round(eq, 4)
                tp2 = round(rl, 4)
            else:
                sl = round(rl * 0.997, 4)
                tp1 = round(eq, 4)
                tp2 = round(rh, 4)
            sl_pct = round(abs(price - sl) / price * 100, 2) if price > 0 else 1.5
            rr_ratio = round(abs(tp2 - price) / max(abs(price - sl), 1e-6), 2)

        is_a_plus_range = (
            setup.get("detected")
            and setup.get("setup_type") in ("SFP_WICK_REJECTION", "DEVIATION_RETURN_TO_RANGE")
            and conf >= 84.0
            and rr_ratio >= 2.3
        )
        tp1_close_pct = 0.40 if is_a_plus_range else 0.50
        tp2_close_pct = round(1.0 - tp1_close_pct, 2)
        tp_levels = [
            {"level": 1, "price": round(tp1, 4), "close_pct": tp1_close_pct},
            {"level": 2, "price": round(tp2, 4), "close_pct": tp2_close_pct},
        ]

        # 6. Xác định Regime & Đòn bẩy linh động (10x - 20x; riêng Vàng/Bạc cố định 50x cho 2 chiều LONG/SHORT)
        is_htf_aligned = bool(traderssz_res.get("is_htf_aligned", False))
        sym_up = str(symbol or "").upper()
        is_precious_metal = atype in ("GOLD", "SILVER") or any(
            k in sym_up for k in ("NCCOGOLD", "NCCOXAG", "XAU", "XAG", "GOLD", "SILVER")
        )

        if final == "WAIT":
            trade_regime = "NO_TRADE_RANGE"
        elif setup.get("setup_type") == "RANGE_BREAKOUT_CONTINUATION":
            trade_regime = "TREND_FOLLOWING"
        elif is_htf_aligned and setup.get("tf") in ("1d", "4h", "1h") and rr_ratio >= 2.5 and conf >= 84.0:
            trade_regime = "TREND_FOLLOWING"
        else:
            trade_regime = "RANGE_DEVIATION"

        if is_precious_metal:
            rec_leverage = 50
            lev_note = "Vàng/Bạc cố định 50x (2 chiều LONG/SHORT)"
        else:
            if final == "WAIT":
                rec_leverage = 10
                lev_note = "Chờ quét biên Range (10x)"
            elif trade_regime == "TREND_FOLLOWING":
                rec_leverage = 18 if conf < 88.0 else 20
                lev_note = f"TraderSZ A+ HTF Confluence ({rec_leverage}x)"
            elif setup.get("tf") in ("4h", "1h") and conf >= 78.0:
                rec_leverage = 15 if is_htf_aligned else 12
                lev_note = f"TraderSZ Range SFP ({rec_leverage}x)"
            else:
                rec_leverage = 10
                lev_note = "Range Sweep Phòng Thủ (10x)"

            # Anti-Liquidation Shield: Khống chế trong biên 10x - 20x theo khoảng cách SL
            if sl_pct > 0.001:
                max_safe_by_sl = max(10, int(60.0 / sl_pct))
                rec_leverage = min(rec_leverage, max_safe_by_sl)
            rec_leverage = max(10, min(20, rec_leverage))

        # 7. Expectancy (EV) thuần dựa trên Xác suất SFP & Risk:Reward
        p_win = round(min(0.80, max(0.40, conf / 100.0 * 0.78)), 3)
        ev_ratio = round((p_win * rr_ratio) - (1.0 - p_win), 3) if final != "WAIT" else 0.0
        bayes_ev = {
            "p_win": round(p_win * 100, 1),
            "rr": round(rr_ratio, 2),
            "ev_ratio": ev_ratio,
            "likelihood": round(conf / 50.0, 2),
        }

        sweep_type = "NONE"
        if setup.get("detected"):
            if setup.get("setup_type") == "RANGE_BREAKOUT_CONTINUATION":
                sweep_type = "BULLISH_BREAKOUT" if final == "LONG" else "BEARISH_BREAKOUT"
            else:
                sweep_type = "BULLISH_SWEEP" if final == "LONG" else "BEARISH_SWEEP"
        liquidity_sweep = {
            "detected": bool(setup.get("detected", False)),
            "type": sweep_type,
            "price": float(setup.get("sweep_extreme", 0.0)) if setup.get("detected") else 0.0,
            "swept_level": float(setup.get("swept_level", 0.0)) if setup.get("detected") else 0.0,
            "tf": setup.get("tf", ""),
            "range_label": setup.get("range_label", ""),
        }

        tf_summary = self._build_tf_summary(klines_by_tf, traderssz_res)
        monday_mult = float(htf_data.get("monday_size_mult", 1.0) or 1.0)
        news_mult = float(news_risk.get("size_mult", 1.0) or 1.0)
        size_mult = round(max(0.65, min(1.0, news_mult * monday_mult)), 2)

        btc_tag = f" | BTC:{btc_ctx.get('bias', 'NEUTRAL')}" if (atype == "CRYPTO" and btc_ctx) else ""
        log.info(
            "  [TRADERSSZ] %s: %s %.1f%% | Zone:%s (%.1f%%) | Range:%s [$%.2f-$%.2f EQ:$%.2f] | SL:%.2f%% RR:1:%.2f%s",
            symbol, final, conf,
            htf_data.get("market_zone"), htf_data.get("range_pos_pct", 50.0),
            htf_data.get("range_name"), htf_data.get("range_low", 0), htf_data.get("range_high", 0), htf_data.get("eq_50", 0),
            sl_pct, rr_ratio, btc_tag,
        )

        return {
            "symbol": symbol,
            "asset_type": atype,
            "price": price,
            "final": final,
            "confidence": conf,
            "timeframes": tf_summary,
            "plan": {
                "entry": price,
                "entry_mode": setup.get("entry_mode", "MARKET") if setup.get("detected") else "MARKET",
                "limit_entry": float(setup.get("limit_entry", price) or price) if setup.get("detected") else price,
                "sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "q25": float(htf_data.get("q25", 0) or 0),
                "q75": float(htf_data.get("q75", 0) or 0),
                "tp_levels": tp_levels,
                "recommended_leverage": rec_leverage,
            },
            "rr_ratio": rr_ratio,
            "sl_pct": sl_pct,
            "atr": round(atr_val, 4),
            "traderssz": traderssz_res,
            "btc_context": btc_ctx,
            "liquidity_sweep": liquidity_sweep,
            "orderbook": ob_data,
            "oi_signal": oi_signal,
            "oi_desc": oi_desc,
            "oi_delta": round(oi_delta, 3),
            "funding": round(funding, 4),
            "mkt_open": mkt_open,
            "mkt_note": mkt_note,
            "news_risk": news_risk,
            "trade_regime": trade_regime,
            "position_size_mult": size_mult,
            "dynamic_leverage": rec_leverage,
            "leverage_note": lev_note,
            "bayes_ev": bayes_ev,
            "hmm": {"regime": htf_bias.get("bias", "RANGE_NEUTRAL"), "confidence": conf},
            "timestamp": datetime.now().strftime("%d/%m %H:%M"),
        }
