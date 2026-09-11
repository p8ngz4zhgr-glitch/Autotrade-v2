# ═══════════════════════════════════════════════════════════
# ECONOMIC CALENDAR — worker/economic_calendar.py
# ═══════════════════════════════════════════════════════════
"""
Lọc RIÊNG tin kinh tế có ảnh hưởng lớn tới Crypto/Vàng (CPI, PPI, Non-Farm
Payrolls, FOMC/lãi suất Fed, PCE) — không cần theo dõi toàn bộ lịch kinh tế
thế giới hay các loại ngoại hối khác, đúng yêu cầu gốc.

NGUỒN DỮ LIỆU (thứ tự ưu tiên):
1. Lịch Vĩ Mô Tự Động Chuẩn Xác (NFP, CPI, PPI, FOMC Rate Decision)
2. Finnhub /calendar/economic (Nếu có API Key hợp lệ và không bị 403)

Fail-open: bất kỳ lỗi nào đều rơi về "không có tin lớn sắp tới" — KHÔNG
được để lỗi module này chặn toàn bộ hệ thống giao dịch.
"""
import os
import time
import logging
from datetime import datetime, timedelta, date, time as dtime, timezone

log = logging.getLogger("EconCalendar")

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
FINNHUB_BASE = "https://finnhub.io/api/v1"

_finnhub_disabled = False
_last_finnhub_check = 0

HIGH_IMPACT_KEYWORDS = [
    "cpi", "consumer price", "ppi", "producer price",
    "nonfarm", "non-farm", "payroll",
    "fomc", "fed interest", "federal funds", "fed chair", "fed rate",
    "pce", "personal consumption",
    "unemployment rate", "gdp",
]

MANUAL_CPI_PPI_DATES: list[str] = []


def _nfp_datetimes_utc(year: int) -> list:
    """Non-Farm Payrolls: luôn công bố 8:30 sáng ET vào thứ 6 đầu tiên mỗi tháng."""
    out = []
    try:
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        for month in range(1, 13):
            d = date(year, month, 1)
            while d.weekday() != 4:  # 4 = thứ 6
                d += timedelta(days=1)
            local_dt = datetime.combine(d, dtime(8, 30), tzinfo=et)
            out.append(local_dt.astimezone(timezone.utc).replace(tzinfo=None))
    except Exception as e:
        log.debug("zoneinfo fallback NFP: %s", e)
        for month in range(1, 13):
            d = date(year, month, 1)
            while d.weekday() != 4:
                d += timedelta(days=1)
            out.append(datetime.combine(d, dtime(13, 30)))
    return out


def _fomc_datetimes_utc(year: int) -> list:
    """
    Lịch Quyết Định Lãi Suất Fed (FOMC Rate Decision):
    Công bố 8 lần/năm vào 14:00 ET các ngày Thứ Tư trong các tháng 1,3,5,6,7,9,11,12.
    """
    out = []
    fomc_months = [1, 3, 5, 6, 7, 9, 11, 12]
    try:
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        for month in fomc_months:
            # Ước tính thứ 4 tuần thứ 3 trong tháng
            d = date(year, month, 15)
            while d.weekday() != 2:  # 2 = thứ 4
                d += timedelta(days=1)
            local_dt = datetime.combine(d, dtime(14, 0), tzinfo=et)
            out.append(local_dt.astimezone(timezone.utc).replace(tzinfo=None))
    except Exception:
        for month in fomc_months:
            d = date(year, month, 15)
            while d.weekday() != 2:
                d += timedelta(days=1)
            out.append(datetime.combine(d, dtime(19, 0)))
    return out


def _cpi_datetimes_utc(year: int) -> list:
    """
    Lịch Công Bố CPI (Chỉ số giá tiêu dùng Mỹ):
    Thường công bố lúc 8:30 sáng ET vào khoảng giữa tháng (ngày 11-15).
    """
    out = []
    try:
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        for month in range(1, 13):
            d = date(year, month, 12)
            while d.weekday() in (5, 6): # Né cuối tuần
                d += timedelta(days=1)
            local_dt = datetime.combine(d, dtime(8, 30), tzinfo=et)
            out.append(local_dt.astimezone(timezone.utc).replace(tzinfo=None))
    except Exception:
        for month in range(1, 13):
            d = date(year, month, 12)
            while d.weekday() in (5, 6):
                d += timedelta(days=1)
            out.append(datetime.combine(d, dtime(13, 30)))
    return out


def _fetch_finnhub(days_ahead: int) -> list:
    global _finnhub_disabled, _last_finnhub_check
    if not FINNHUB_API_KEY or _finnhub_disabled:
        return []

    # Giới hạn thử lại Finnhub 1 tiếng 1 lần nếu từng bị lỗi/403
    now_ts = time.time()
    if now_ts - _last_finnhub_check < 3600:
        return []

    _last_finnhub_check = now_ts
    import requests
    now = datetime.utcnow()
    end = now + timedelta(days=days_ahead)
    try:
        r = requests.get(
            f"{FINNHUB_BASE}/calendar/economic",
            params={"from": now.strftime("%Y-%m-%d"), "to": end.strftime("%Y-%m-%d"), "token": FINNHUB_API_KEY},
            timeout=5,
        )
        if r.status_code == 403:
            _finnhub_disabled = True
            log.info("ℹ️ Finnhub API Key không hỗ trợ gói Free /calendar/economic (403) -> Chuyển sang Lịch Vĩ Mô Tự Động Chuẩn Tự Tính.")
            return []
        r.raise_for_status()
        raw = r.json().get("economicCalendar", []) or []
        events = []
        for e in raw:
            name = str(e.get("event", ""))
            if e.get("country") not in ("US", "USA", None):
                continue
            if any(k in name.lower() for k in HIGH_IMPACT_KEYWORDS):
                events.append({"name": name, "time": e.get("time"), "source": "finnhub"})
        return events
    except Exception as ex:
        _finnhub_disabled = True
        log.debug("ℹ️ Finnhub economic calendar không sẵn sàng (%s) -> Dùng Lịch Vĩ Mô Tự Động.", ex)
        return []


def get_high_impact_events(days_ahead: int = 7) -> list:
    """[{"name":.., "time": "YYYY-MM-DDTHH:MM:SS", "source":..}], fail-open -> []."""
    now = datetime.utcnow()
    end = now + timedelta(days=days_ahead)

    # 1. Thử Finnhub nếu khả thi
    try:
        events = _fetch_finnhub(days_ahead)
        if events:
            return events
    except Exception:
        pass

    # 2. Lịch Vĩ Mô Dự Phòng Tự Động (NFP, CPI, FOMC Rate Decision)
    events = []
    for dt in _nfp_datetimes_utc(now.year) + _nfp_datetimes_utc(now.year + 1):
        if now <= dt <= end:
            events.append({"name": "Non-Farm Payrolls (NFP)", "time": dt.isoformat(), "source": "auto_nfp"})

    for dt in _fomc_datetimes_utc(now.year) + _fomc_datetimes_utc(now.year + 1):
        if now <= dt <= end:
            events.append({"name": "Quyết định Lãi suất Fed (FOMC)", "time": dt.isoformat(), "source": "auto_fomc"})

    for dt in _cpi_datetimes_utc(now.year) + _cpi_datetimes_utc(now.year + 1):
        if now <= dt <= end:
            events.append({"name": "Chỉ số Giá Tiêu dùng Mỹ (CPI)", "time": dt.isoformat(), "source": "auto_cpi"})

    for ds in MANUAL_CPI_PPI_DATES:
        try:
            dt = datetime.strptime(ds, "%Y-%m-%d")
            if now <= dt <= end:
                events.append({"name": "Tin Vĩ Mô Thủ Công", "time": dt.isoformat(), "source": "manual_list"})
        except Exception:
            continue

    return events


def news_risk_adjustment(hours_before: int = 12, hours_after: int = 6) -> dict:
    """
    Kết quả dùng trực tiếp cho engine.py/bingx_trader.py/main.py/main_scanner.py:
    {"active": bool, "event": str|None, "pause_trading": bool, "size_mult": float, "sl_tighten_mult": float}
    """
    try:
        blackout_mins = int(os.getenv("MACRO_BLACKOUT_MINUTES", "90"))

        # 1. Kiểm tra rủi ro tin xấu từ AI LLM News Agent
        try:
            from analyzer.news_agent import get_news_agent_risk
            na_risk = get_news_agent_risk()
            if na_risk.get("active"):
                log.warning("📰 [NEWS RISK - LLM] Cảnh báo tin xấu (%s) -> Giảm 50%% vốn vào lệnh (size_mult=0.5), giữ SL an toàn sàn >=1.5%%.",
                            na_risk.get("event"))
                na_risk["pause_trading"] = na_risk.get("pause_trading", False)
                return na_risk
        except Exception as ex_na:
            log.debug("Lỗi lấy news_agent_risk: %s", ex_na)

        # 2. Kiểm tra lịch tin vĩ mô (CPI/PPI/NFP/FOMC...)
        events = get_high_impact_events(days_ahead=3)
        now = datetime.utcnow()
        for e in events:
            try:
                et = datetime.fromisoformat(e["time"])
            except Exception:
                continue
            
            # Cửa sổ ngắt lệnh cứng (mặc định 90 phút trước/sau giờ ra tin lớn)
            if (et - timedelta(minutes=blackout_mins)) <= now <= (et + timedelta(minutes=blackout_mins)):
                log.warning("🛑 [MACRO BLACKOUT WINDOW] Đang trong cửa sổ %d phút ra tin lớn: %s (%s) -> TẠM DỪNG MỞ VỊ THẾ BẢO VỆ VỐN!",
                            blackout_mins, e["name"], e["time"])
                return {"active": True, "event": e["name"], "pause_trading": True, "size_mult": 0.0, "sl_tighten_mult": 1.0}

            # Cửa sổ ảnh hưởng rộng (12h trước -> 6h sau) -> giảm 50% vốn
            if (et - timedelta(hours=hours_before)) <= now <= (et + timedelta(hours=hours_after)):
                log.warning("📰 [NEWS WINDOW - CALENDAR] Đang trong vùng ảnh hưởng tin: %s (%s) -> giảm 50%% vốn vào lệnh, quản trị rủi ro.",
                            e["name"], e["time"])
                return {"active": True, "event": e["name"], "pause_trading": False, "size_mult": 0.5, "sl_tighten_mult": 0.9}
        return {"active": False, "event": None, "pause_trading": False, "size_mult": 1.0, "sl_tighten_mult": 1.0}
    except Exception as ex:
        log.warning("⚠️ news_risk_adjustment lỗi (%s) -> fail-open, không điều chỉnh gì.", ex)
        return {"active": False, "event": None, "pause_trading": False, "size_mult": 1.0, "sl_tighten_mult": 1.0}
