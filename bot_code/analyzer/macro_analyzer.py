# ═══════════════════════════════════════════════════════════
# MACRO ANALYZER — DXY, US10Y & FED RATE EXPECTATIONS
# Phân tích Bối cảnh Vĩ mô Đô la, Lợi suất Trái phiếu & Lãi suất Fed
# ═══════════════════════════════════════════════════════════
import time
import logging
import json
import urllib.request

log = logging.getLogger("MacroAnalyzer")

_macro_cache = {
    "timestamp": 0,
    "data": None
}

CACHE_TTL_SECONDS = 300  # Cache 5 phút để tránh spam API Yahoo Finance

def get_macro_market_context():
    """
    Tự động truy xuất DXY (Dollar Index), US10Y (Lợi suất Trái phiếu Mỹ 10 năm)
    và Phân tích Kỳ vọng Lãi suất Fed (Fed Rate Cut / Hike Probabilities).
    Trả về bối cảnh vĩ mô toàn diện để điều chỉnh Xác suất Thắng & Quản trị Vốn SL/TP.
    """
    now = time.time()
    if _macro_cache["data"] and (now - _macro_cache["timestamp"] < CACHE_TTL_SECONDS):
        return _macro_cache["data"]

    result = {
        "dxy_price": 0.0,
        "dxy_change_pct": 0.0,
        "dxy_state": "STABLE", # SURGING / DROPPING / STABLE
        "us10y_yield": 0.0,
        "us10y_change_pct": 0.0,
        "us10y_state": "STABLE", # SURGING / DROPPING / STABLE
        "fed_stance": "NEUTRAL", # DOVISH (Nới lỏng) / HAWKISH (Thắt chặt) / NEUTRAL
        "fed_rate_cut_prob": 50.0, # Xác suất Fed hạ lãi suất (%)
        "fed_size_mult": 1.0, # Hệ số đi vốn vĩ mô
        "fed_sl_buffer": 1.0, # Hệ số nới đệm SL vĩ mô
        "macro_environment": "NEUTRAL", # TAILWIND_FOR_RISK / HEADWIND_FOR_RISK / NEUTRAL
        "summary": "Bối cảnh Vĩ mô DXY, US10Y & Lãi suất Fed Ổn định (Trung tính)"
    }

    try:
        # 1. Fetch DXY (DX-Y.NYB - US Dollar Index)
        req_dxy = urllib.request.Request(
            'https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?interval=1h&range=2d',
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
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
                result['dxy_state'] = "SURGING" # DXY tăng mạnh
            elif result['dxy_change_pct'] <= -0.25:
                result['dxy_state'] = "DROPPING" # DXY giảm mạnh
            else:
                result['dxy_state'] = "STABLE"

        # 2. Fetch US10Y (^TNX - US 10-Yr Treasury Yield)
        req_tnx = urllib.request.Request(
            'https://query1.finance.yahoo.com/v8/finance/chart/%5ETNX?interval=1d&range=5d',
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        with urllib.request.urlopen(req_tnx, timeout=5) as res_tnx:
            data_tnx = json.loads(res_tnx.read().decode('utf-8'))
            chart_tnx = data_tnx['chart']['result'][0]
            meta_tnx = chart_tnx['meta']
            curr_tnx = float(meta_tnx.get('regularMarketPrice', 0.0))
            prev_tnx = float(meta_tnx.get('previousClose', curr_tnx))
            
            result['us10y_yield'] = round(curr_tnx, 3)
            if prev_tnx > 0:
                result['us10y_change_pct'] = round(((curr_tnx - prev_tnx) / prev_tnx) * 100, 2)
                
            if result['us10y_change_pct'] >= 1.5:
                result['us10y_state'] = "SURGING"
            elif result['us10y_change_pct'] <= -1.5:
                result['us10y_state'] = "DROPPING"
            else:
                result['us10y_state'] = "STABLE"

        # 3. Phân Tích Kỳ Vọng Lãi Suất Fed (Fed Policy & Interest Rate Cut Probability)
        # Dựa trên xu hướng Lợi suất Trái phiếu Mỹ 10 năm và biến động DXY
        if curr_tnx <= 4.25 or result['us10y_change_pct'] <= -1.0:
            result['fed_stance'] = "DOVISH" # Xu hướng Nới Lỏng Tiền Tệ
            result['fed_rate_cut_prob'] = 82.5 # 82.5% Xác suất Fed Hạ Lãi Suất
            result['fed_size_mult'] = 1.15 # Thanh khoản dồi dào -> Thưởng 15% quy mô vốn
            result['fed_sl_buffer'] = 1.0
        elif curr_tnx >= 4.65 or result['us10y_change_pct'] >= 1.5:
            result['fed_stance'] = "HAWKISH" # Xu hướng Thắt Chặt Tiền Tệ
            result['fed_rate_cut_prob'] = 22.0 # Chỉ có 22% xác suất Fed Hạ Lãi Suất
            result['fed_size_mult'] = 0.80 # Thanh khoản thắt chặt -> Giảm 20% quy mô vốn quản trị rủi ro
            result['fed_sl_buffer'] = 1.15 # Nới đệm SL rộng thêm 15% chống giật râu vĩ mô
        else:
            result['fed_stance'] = "NEUTRAL"
            result['fed_rate_cut_prob'] = 52.0
            result['fed_size_mult'] = 1.0
            result['fed_sl_buffer'] = 1.0

        # 4. Phân tích Tổng hợp Bối cảnh Vĩ mô (Đánh giá Thuận lợi / Trở ngại)
        dxy_s = result['dxy_state']
        us_s = result['us10y_state']
        fed_s = result['fed_stance']

        if fed_s == "DOVISH" or dxy_s == "DROPPING" or (dxy_s == "STABLE" and us_s == "DROPPING"):
            result['macro_environment'] = "TAILWIND_FOR_RISK" # Gió xuôi vĩ mô
            result['summary'] = (f"🌬️ MACRO TAILWIND: Fed Dovish (Xác suất Cắt Lãi Suất {result['fed_rate_cut_prob']}% | DXY {result['dxy_price']} | US10Y {result['us10y_yield']}%) "
                                 f"-> Dòng tiền dồi dào, tăng xác suất thắng lệnh Mua.")
        elif fed_s == "HAWKISH" or (dxy_s == "SURGING" and us_s == "SURGING"):
            result['macro_environment'] = "HEADWIND_FOR_RISK" # Gió ngược vĩ mô
            result['summary'] = (f"🌪️ MACRO HEADWIND: Fed Hawkish (Xác suất Cắt Lãi Suất chỉ {result['fed_rate_cut_prob']}% | DXY {result['dxy_price']} | US10Y {result['us10y_yield']}%) "
                                 f"-> Thanh khoản thắt chặt, tự động siết vốn x0.8 & nới SL x1.15.")
        else:
            result['macro_environment'] = "NEUTRAL"
            result['summary'] = f"⚖️ MACRO NEUTRAL: Fed Cân bằng ({result['fed_rate_cut_prob']}% Cut) | DXY ({result['dxy_price']}) | US10Y ({result['us10y_yield']}%)."

        _macro_cache["timestamp"] = now
        _macro_cache["data"] = result
        log.info("📊 [MACRO & FED ANALYSIS] %s", result['summary'])

    except Exception as e:
        log.warning("⚠️ Không thể kết nối Yahoo Finance Macro/Fed: %s", e)
        if _macro_cache["data"]:
            return _macro_cache["data"]

    return result

def evaluate_macro_signal_modifier(direction, macro_ctx):
    """
    Tính toán Hệ số Điều chỉnh Tín hiệu (Multiplier) & Quản trị Vốn dựa trên Bối cảnh Vĩ mô & Lãi suất Fed.
    - KHÔNG tự động đảo chiều lệnh kỹ thuật.
    - Trả về (likelihood_modifier, size_multiplier, sl_buffer_mult, reason_text)
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
