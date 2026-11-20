# ═══════════════════════════════════════════════════════════
# MACRO & INTERMARKET ANALYZER — DXY, US10Y, FED & ALT/BTC RS
# Phân tích Bối cảnh Liên thị trường (TraderSZ Multi-Asset Confluence)
# ═══════════════════════════════════════════════════════════
import time
import logging
import json
import urllib.request
from typing import Dict, Any, Optional, List

log = logging.getLogger("MacroAnalyzer")

_macro_cache = {
    "timestamp": 0,
    "data": None
}

CACHE_TTL_SECONDS = 300  # Cache 5 phút để tránh spam API Yahoo Finance


def _analyze_yahoo_dealing_range(chart_obj: Dict[str, Any], curr_price: float) -> Dict[str, Any]:
    """
    Áp dụng TraderSZ Dealing Range & SFP lên biểu đồ DXY / US10Y (1H / 1D):
    - Xác định Range High / Low / 50% EQ trong 5 phiên gần nhất.
    - Nhận diện Từ chối kháng cự (BEARISH_REJECTION / SFP Đỉnh) hoặc Reclaim hỗ trợ (BULLISH_RECLAIM).
    """
    out = {
        "range_high": 0.0,
        "range_low": 0.0,
        "eq_50": 0.0,
        "pos_pct": 50.0,
        "pa_signal": "NEUTRAL",  # BEARISH_REJECTION / BULLISH_RECLAIM / NEUTRAL
    }
    try:
        quote = ((chart_obj.get("indicators") or {}).get("quote") or [{}])[0]
        raw_o = quote.get("open") or []
        raw_h = quote.get("high") or []
        raw_l = quote.get("low") or []
        raw_c = quote.get("close") or []

        bars: List[tuple] = []
        for o, h, l, c in zip(raw_o, raw_h, raw_l, raw_c):
            if o is not None and h is not None and l is not None and c is not None:
                bars.append((float(o), float(h), float(l), float(c)))

        if len(bars) < 8:
            return out

        closed = bars[:-1]
        window = closed[-min(48, len(closed)):]
        rh = max(b[1] for b in window[:-2]) if len(window) >= 6 else max(b[1] for b in window)
        rl = min(b[2] for b in window[:-2]) if len(window) >= 6 else min(b[2] for b in window)
        if rh <= rl or rl <= 0:
            return out

        eq = (rh + rl) / 2.0
        ref_price = curr_price if curr_price > 0 else closed[-1][3]
        pos_pct = (ref_price - rl) / (rh - rl) * 100.0

        c2_o, c2_h, c2_l, c2_c = closed[-1]
        c3_h, c3_l, c3_c = closed[-2][1], closed[-2][2], closed[-2][3]
        span = max(c2_h - c2_l, 1e-9)
        upper_wick = c2_h - max(c2_o, c2_c)
        lower_wick = min(c2_o, c2_c) - c2_l

        # Bearish Rejection / SFP đỉnh Range trên DXY hoặc US10Y -> Cửa sổ Risk-On cho Crypto/Vàng/Stock
        swept_rh = (c2_h >= rh * 0.9995 and c2_c < rh) or (c3_h > rh and c2_c < rh)
        bearish_reject_top = pos_pct >= 68.0 and (
            swept_rh
            or (upper_wick / span >= 0.35 and c2_c <= c2_o)
            or (c2_c < c2_o and ref_price < c2_c)
        )

        # Bullish Reclaim / Bounce đáy Range trên DXY hoặc US10Y -> Áp lực Risk-Off cho Crypto/Vàng/Stock
        swept_rl = (c2_l <= rl * 1.0005 and c2_c > rl) or (c3_l < rl and c2_c > rl)
        bullish_bounce_bot = pos_pct <= 32.0 and (
            swept_rl
            or (lower_wick / span >= 0.35 and c2_c >= c2_o)
            or (c2_c > c2_o and ref_price > c2_c)
        )
        bullish_break_rh = ref_price > rh * 1.0015 and c2_c > rh

        if bearish_reject_top or (ref_price < rl * 0.9985 and c2_c < rl):
            pa_sig = "BEARISH_REJECTION"
        elif bullish_bounce_bot or bullish_break_rh:
            pa_sig = "BULLISH_RECLAIM"
        else:
            pa_sig = "NEUTRAL"

        out = {
            "range_high": round(rh, 3),
            "range_low": round(rl, 3),
            "eq_50": round(eq, 3),
            "pos_pct": round(pos_pct, 1),
            "pa_signal": pa_sig,
        }
    except Exception:
        pass
    return out


def get_macro_market_context():
    """
    Tự động truy xuất DXY (Dollar Index), US10Y (Lợi suất Trái phiếu Mỹ 10 năm),
    Phân tích Cấu trúc Dealing Range liên thị trường (TraderSZ Intermarket) & Kỳ vọng Lãi suất Fed.
    """
    now = time.time()
    if _macro_cache["data"] and (now - _macro_cache["timestamp"] < CACHE_TTL_SECONDS):
        return _macro_cache["data"]

    result = {
        "dxy_price": 0.0,
        "dxy_change_pct": 0.0,
        "dxy_state": "STABLE",  # SURGING / DROPPING / STABLE
        "dxy_pa_signal": "NEUTRAL",  # BEARISH_REJECTION / BULLISH_RECLAIM / NEUTRAL
        "dxy_range_pos_pct": 50.0,
        "us10y_yield": 0.0,
        "us10y_change_pct": 0.0,
        "us10y_state": "STABLE",  # SURGING / DROPPING / STABLE
        "us10y_pa_signal": "NEUTRAL",  # BEARISH_REJECTION / BULLISH_RECLAIM / NEUTRAL
        "us10y_range_pos_pct": 50.0,
        "fed_stance": "NEUTRAL",  # DOVISH / HAWKISH / NEUTRAL
        "fed_rate_cut_prob": 50.0,
        "fed_size_mult": 1.0,
        "fed_sl_buffer": 1.0,
        "macro_environment": "NEUTRAL",  # TAILWIND_FOR_RISK / HEADWIND_FOR_RISK / NEUTRAL
        "intermarket_bias": "NEUTRAL",   # RISK_ON / RISK_OFF / NEUTRAL
        "summary": "Bối cảnh Vĩ mô DXY, US10Y & Lãi suất Fed Ổn định (Trung tính)"
    }

    try:
        # 1. Fetch DXY (DX-Y.NYB - US Dollar Index, 5 ngày 1H để tính Dealing Range)
        req_dxy = urllib.request.Request(
            'https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?interval=1h&range=5d',
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        curr_dxy = 0.0
        with urllib.request.urlopen(req_dxy, timeout=5) as res_dxy:
            data_dxy = json.loads(res_dxy.read().decode('utf-8'))
            chart_dxy = data_dxy['chart']['result'][0]
            meta_dxy = chart_dxy['meta']
            curr_dxy = float(meta_dxy.get('regularMarketPrice', 0.0))
            prev_dxy = float(meta_dxy.get('chartPreviousClose', curr_dxy))

            result['dxy_price'] = round(curr_dxy, 3)
            if prev_dxy > 0:
                result['dxy_change_pct'] = round(((curr_dxy - prev_dxy) / prev_dxy) * 100, 2)

            if result['dxy_change_pct'] >= 0.25:
                result['dxy_state'] = "SURGING"
            elif result['dxy_change_pct'] <= -0.25:
                result['dxy_state'] = "DROPPING"
            else:
                result['dxy_state'] = "STABLE"

            dxy_pa = _analyze_yahoo_dealing_range(chart_dxy, curr_dxy)
            result['dxy_pa_signal'] = dxy_pa['pa_signal']
            result['dxy_range_pos_pct'] = dxy_pa['pos_pct']

        # 2. Fetch US10Y (^TNX - US 10-Yr Treasury Yield, 5 ngày 1H)
        req_tnx = urllib.request.Request(
            'https://query1.finance.yahoo.com/v8/finance/chart/%5ETNX?interval=1h&range=5d',
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        curr_tnx = 0.0
        with urllib.request.urlopen(req_tnx, timeout=5) as res_tnx:
            data_tnx = json.loads(res_tnx.read().decode('utf-8'))
            chart_tnx = data_tnx['chart']['result'][0]
            meta_tnx = chart_tnx['meta']
            curr_tnx = float(meta_tnx.get('regularMarketPrice', 0.0))
            prev_tnx = float(meta_tnx.get('previousClose', meta_tnx.get('chartPreviousClose', curr_tnx)))

            result['us10y_yield'] = round(curr_tnx, 3)
            if prev_tnx > 0:
                result['us10y_change_pct'] = round(((curr_tnx - prev_tnx) / prev_tnx) * 100, 2)

            if result['us10y_change_pct'] >= 1.5:
                result['us10y_state'] = "SURGING"
            elif result['us10y_change_pct'] <= -1.5:
                result['us10y_state'] = "DROPPING"
            else:
                result['us10y_state'] = "STABLE"

            tnx_pa = _analyze_yahoo_dealing_range(chart_tnx, curr_tnx)
            result['us10y_pa_signal'] = tnx_pa['pa_signal']
            result['us10y_range_pos_pct'] = tnx_pa['pos_pct']

        # 3. Phân Tích Kỳ Vọng Lãi Suất Fed
        if curr_tnx <= 4.25 or result['us10y_change_pct'] <= -1.0:
            result['fed_stance'] = "DOVISH"
            result['fed_rate_cut_prob'] = 82.5
            result['fed_size_mult'] = 1.15
            result['fed_sl_buffer'] = 1.0
        elif curr_tnx >= 4.65 or result['us10y_change_pct'] >= 1.5:
            result['fed_stance'] = "HAWKISH"
            result['fed_rate_cut_prob'] = 22.0
            result['fed_size_mult'] = 0.80
            result['fed_sl_buffer'] = 1.15
        else:
            result['fed_stance'] = "NEUTRAL"
            result['fed_rate_cut_prob'] = 52.0
            result['fed_size_mult'] = 1.0
            result['fed_sl_buffer'] = 1.0

        # 4. TraderSZ Intermarket Bias (Nghịch biến DXY + US10Y với Crypto/Vàng/Stock)
        dxy_s = result['dxy_state']
        us_s = result['us10y_state']
        dxy_pa_sig = result['dxy_pa_signal']
        tnx_pa_sig = result['us10y_pa_signal']
        fed_s = result['fed_stance']

        risk_on_score = 0
        if dxy_pa_sig == "BEARISH_REJECTION" or dxy_s == "DROPPING":
            risk_on_score += 2
        elif dxy_pa_sig == "BULLISH_RECLAIM" or dxy_s == "SURGING":
            risk_on_score -= 2

        if tnx_pa_sig == "BEARISH_REJECTION" or us_s == "DROPPING":
            risk_on_score += 1
        elif tnx_pa_sig == "BULLISH_RECLAIM" or us_s == "SURGING":
            risk_on_score -= 1

        if risk_on_score >= 2:
            result['intermarket_bias'] = "RISK_ON"
        elif risk_on_score <= -2:
            result['intermarket_bias'] = "RISK_OFF"
        else:
            result['intermarket_bias'] = "NEUTRAL"

        if fed_s == "DOVISH" or result['intermarket_bias'] == "RISK_ON":
            result['macro_environment'] = "TAILWIND_FOR_RISK"
            result['summary'] = (
                f"🌬️ RISK-ON TAILWIND: DXY {result['dxy_price']} ({dxy_pa_sig}) | "
                f"US10Y {result['us10y_yield']}% ({tnx_pa_sig}) | Fed {fed_s}"
            )
        elif fed_s == "HAWKISH" or result['intermarket_bias'] == "RISK_OFF":
            result['macro_environment'] = "HEADWIND_FOR_RISK"
            result['summary'] = (
                f"🌪️ RISK-OFF HEADWIND: DXY {result['dxy_price']} ({dxy_pa_sig}) | "
                f"US10Y {result['us10y_yield']}% ({tnx_pa_sig}) | Fed {fed_s}"
            )
        else:
            result['macro_environment'] = "NEUTRAL"
            result['summary'] = (
                f"⚖️ INTERMARKET NEUTRAL: DXY {result['dxy_price']} ({result['dxy_range_pos_pct']}%) | "
                f"US10Y {result['us10y_yield']}% ({result['us10y_range_pos_pct']}%)"
            )

        _macro_cache["timestamp"] = now
        _macro_cache["data"] = result
        log.info("📊 [MACRO & INTERMARKET] %s", result['summary'])

    except Exception as e:
        log.warning("⚠️ Không thể kết nối Yahoo Finance Macro/Fed: %s", e)
        if _macro_cache["data"]:
            return _macro_cache["data"]

    return result


def evaluate_intermarket_confluence(
    symbol: str,
    asset_type: str,
    direction: str,
    macro_ctx: Optional[Dict[str, Any]] = None,
    btc_ctx: Optional[Dict[str, Any]] = None,
    rs_ctx: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Đánh giá Hợp lưu Liên Thị Trường chuẩn video TraderSZ (DXY + US10Y + BTC Leader + ALT/BTC RS):
    - Trả về đầy đủ conf_adj / conf_delta, size_mult, opposes_macro, rs_vs_btc, aligned, warnings, label, summary.
    """
    direction_u = str(direction or "WAIT").upper()
    conf_delta = 0.0
    size_mult = 1.0
    opposes_macro = False
    notes: List[str] = []
    warnings: List[str] = []

    im_bias = str((macro_ctx or {}).get("intermarket_bias", "NEUTRAL")).upper()
    dxy_pa = str((macro_ctx or {}).get("dxy_pa_signal", "NEUTRAL")).upper()
    tnx_pa = str((macro_ctx or {}).get("us10y_pa_signal", "NEUTRAL")).upper()

    if direction_u == "LONG":
        if im_bias == "RISK_ON":
            conf_delta += 2.5
            notes.append(f"DXY/US10Y từ chối đỉnh ({dxy_pa}/{tnx_pa}) ủng hộ LONG")
        elif im_bias == "RISK_OFF":
            conf_delta -= 2.5
            size_mult = min(size_mult, 0.80)
            opposes_macro = True
            warnings.append(f"DXY/US10Y đang bật tăng ({dxy_pa}/{tnx_pa}) ngược chiều LONG")
    elif direction_u == "SHORT":
        if im_bias == "RISK_OFF":
            conf_delta += 2.5
            notes.append(f"DXY/US10Y tăng mạnh ({dxy_pa}/{tnx_pa}) ủng hộ SHORT")
        elif im_bias == "RISK_ON":
            conf_delta -= 2.5
            size_mult = min(size_mult, 0.80)
            opposes_macro = True
            warnings.append(f"DXY/US10Y đang suy yếu ({dxy_pa}/{tnx_pa}) ngược chiều SHORT")

    rs_val = float((rs_ctx or {}).get("rs_vs_btc", (rs_ctx or {}).get("rs_4h_pct", 0.0)) or 0.0)
    rs_state = str((rs_ctx or {}).get("state", "")).upper()
    if not rs_state and rs_ctx:
        rs_state = "OUTPERFORMING" if rs_val >= 0.45 else ("LAGGING" if rs_val <= -0.45 else "NEUTRAL")
    majors_align = bool((rs_ctx or {}).get("majors_aligned", False))

    # Sức mạnh tương đối ALT/BTC (Relative Strength trong video TraderSZ: SOL/ETH vs BTC)
    if str(asset_type or "").upper() == "CRYPTO" and "BTC" not in str(symbol or "").upper() and rs_ctx:
        if direction_u == "LONG":
            if rs_state == "OUTPERFORMING" or rs_val >= 0.45:
                conf_delta += 2.0
                notes.append(f"ALT/BTC mạnh hơn BTC ({rs_val:+.2f}%)")
            elif rs_state == "LAGGING" or rs_val <= -1.0:
                conf_delta -= 2.0
                size_mult = min(size_mult, 0.85)
                warnings.append(f"ALT/BTC yếu hơn BTC ({rs_val:+.2f}%)")
        elif direction_u == "SHORT":
            if rs_state == "LAGGING" or rs_val <= -0.45:
                conf_delta += 2.0
                notes.append(f"ALT/BTC yếu hơn BTC ({rs_val:+.2f}%)")
            elif rs_state == "OUTPERFORMING" or rs_val >= 1.0:
                conf_delta -= 2.0
                size_mult = min(size_mult, 0.85)
                warnings.append(f"ALT/BTC đang khỏe hơn BTC ({rs_val:+.2f}%)")

        if majors_align and conf_delta > 0:
            conf_delta += 1.0
            notes.append("BTC+Majors đồng thuận")

    aligned = conf_delta >= 1.5 and not warnings
    summary = " · ".join(notes or warnings) or f"Intermarket {im_bias}"
    return {
        "intermarket_bias": im_bias,
        "dxy_pa_signal": dxy_pa,
        "us10y_pa_signal": tnx_pa,
        "conf_adj": round(conf_delta, 1),
        "conf_delta": round(conf_delta, 1),
        "size_mult": round(max(0.75, min(1.05, size_mult)), 2),
        "opposes_macro": opposes_macro,
        "rs_vs_btc": round(rs_val, 2),
        "aligned": aligned,
        "notes": notes,
        "warnings": warnings,
        "label": summary,
        "summary": summary,
    }


def evaluate_macro_signal_modifier(direction, macro_ctx):
    """
    Tính toán Hệ số Điều chỉnh Tín hiệu (Multiplier) & Quản trị Vốn dựa trên Bối cảnh Vĩ mô & Lãi suất Fed.
    """
    if not macro_ctx or not isinstance(macro_ctx, dict):
        return 1.0, 1.0, 1.0, "NEUTRAL"

    env = macro_ctx.get("macro_environment", "NEUTRAL")
    fed_size = macro_ctx.get("fed_size_mult", 1.0)
    fed_sl_buff = macro_ctx.get("fed_sl_buffer", 1.0)
    cut_prob = macro_ctx.get("fed_rate_cut_prob", 50.0)

    if direction == "LONG":
        if env == "TAILWIND_FOR_RISK":
            return 1.15, fed_size, 1.0, f"Gió xuôi vĩ mô (Fed Dovish {cut_prob}% Cut, DXY/US10Y hạ) -> Củng cố xác suất thắng LONG (+15%)"
        elif env == "HEADWIND_FOR_RISK":
            return 0.88, fed_size, fed_sl_buff, f"Gió ngược vĩ mô (Fed Hawkish {cut_prob}% Cut, DXY/US10Y tăng) -> Yêu cầu kỹ thuật mạnh hơn & Giảm vốn x{fed_size}"
    elif direction == "SHORT":
        if env == "HEADWIND_FOR_RISK":
            return 1.15, 1.1, 1.0, f"Gió ngược vĩ mô (Fed Hawkish {cut_prob}% Cut, DXY/US10Y tăng) -> Củng cố xác suất thắng SHORT (+15%)"
        elif env == "TAILWIND_FOR_RISK":
            return 0.88, 0.8, 1.15, f"Gió xuôi vĩ mô (Fed Dovish {cut_prob}% Cut, DXY/US10Y hạ) -> Yêu cầu kỹ thuật SHORT mạnh hơn & Giảm vốn x0.8"

    return 1.0, 1.0, 1.0, "Vĩ mô trung tính -> Giữ nguyên trọng số kỹ thuật thuần túy"
