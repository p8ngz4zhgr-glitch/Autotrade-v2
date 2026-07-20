# ═══════════════════════════════════════════════════════════
# ECONOMIC CALENDAR — worker/economic_calendar.py
# ═══════════════════════════════════════════════════════════
"""
Lọc RIÊNG tin kinh tế có ảnh hưởng lớn tới Crypto/Vàng (CPI, PPI, Non-Farm
Payrolls, FOMC/lãi suất Fed, PCE) — không cần theo dõi toàn bộ lịch kinh tế
thế giới hay các loại ngoại hối khác, đúng yêu cầu gốc.

NGUỒN DỮ LIỆU (theo thứ tự ưu tiên):
1. Finnhub /calendar/economic — cần FINNHUB_API_KEY (miễn phí, đăng ký tại
   finnhub.io, free tier 60 call/phút). Đây là API THẬT, có tài liệu công
   khai — nhưng tôi KHÔNG có mạng để tự gọi thử trong lúc code, nên hãy kiểm
   tra 1 lần bằng tay (curl) trước khi tin tưởng hoàn toàn vào parsing bên
   dưới; cấu trúc response có thể đã đổi so với lúc tôi tra cứu.
2. Fallback tự tính NFP: LUÔN là thứ 6 đầu tiên mỗi tháng, 8:30 sáng giờ ET
   — quy luật cố định, tính được mà không cần API, dùng zoneinfo để quy đổi
   ET->UTC đúng theo DST (không hard-code lệch giờ mùa đông/hè).
3. Fallback CPI/PPI thủ công: KHÔNG theo quy luật cố định (BLS công bố lịch
   riêng mỗi năm, xem https://www.bls.gov/schedule/) — cần tự cập nhật
   MANUAL_CPI_PPI_DATES định kỳ nếu không dùng Finnhub.

Fail-open: bất kỳ lỗi nào (thiếu key, API lỗi, thiếu tzdata...) đều rơi về
"không có tin lớn sắp tới" — KHÔNG được để lỗi module này chặn toàn bộ hệ
thống giao dịch.
"""
import os
import logging
from datetime import datetime, timedelta, date, time as dtime, timezone

log = logging.getLogger("EconCalendar")

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
FINNHUB_BASE = "https://finnhub.io/api/v1"

# Chỉ các loại tin có ảnh hưởng lớn tới Crypto (risk-on/risk-off qua kỳ vọng
# lãi suất Fed) và Vàng (tài sản trú ẩn, nhạy với lãi suất thực) — không lấy
# toàn bộ lịch kinh tế, không lấy tin ngoại hối khác theo đúng yêu cầu.
HIGH_IMPACT_KEYWORDS = [
    "cpi", "consumer price", "ppi", "producer price",
    "nonfarm", "non-farm", "payroll",
    "fomc", "fed interest", "federal funds", "fed chair", "fed rate",
    "pce", "personal consumption",
    "unemployment rate", "gdp",
]

# Cập nhật tay theo lịch công bố CPI/PPI thật của BLS (bls.gov/schedule) nếu
# KHÔNG cấu hình FINNHUB_API_KEY — để trống thì hệ thống chỉ còn NFP tự tính.
MANUAL_CPI_PPI_DATES: list[str] = [
    # "2026-08-12",  # ví dụ định dạng — điền ngày thật + giờ công bố (thường 8:30 ET)
]


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
        # Thiếu tzdata trên môi trường tối giản -> xấp xỉ ET=UTC-5 (bỏ qua DST,
        # sai lệch tối đa 1h, vẫn đủ dùng cho khung "trước/sau vài giờ" bên dưới)
        log.warning("⚠️ zoneinfo lỗi (%s), xấp xỉ NFP theo UTC-5 cố định.", e)
        for month in range(1, 13):
            d = date(year, month, 1)
            while d.weekday() != 4:
                d += timedelta(days=1)
            out.append(datetime.combine(d, dtime(13, 30)))
    return out


def _fetch_finnhub(days_ahead: int) -> list:
    if not FINNHUB_API_KEY:
        return []
    import requests
    now = datetime.utcnow()
    end = now + timedelta(days=days_ahead)
    r = requests.get(
        f"{FINNHUB_BASE}/calendar/economic",
        params={"from": now.strftime("%Y-%m-%d"), "to": end.strftime("%Y-%m-%d"), "token": FINNHUB_API_KEY},
        timeout=10,
    )
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


def get_high_impact_events(days_ahead: int = 7) -> list:
    """[{"name":.., "time": "YYYY-MM-DDTHH:MM:SS", "source":..}], fail-open -> []."""
    now = datetime.utcnow()
    end = now + timedelta(days=days_ahead)

    try:
        events = _fetch_finnhub(days_ahead)
        if events:
            return events
    except Exception as e:
        log.warning("⚠️ Finnhub economic calendar lỗi (%s) -> dùng lịch dự phòng NFP/thủ công.", e)

    events = []
    for dt in _nfp_datetimes_utc(now.year) + _nfp_datetimes_utc(now.year + 1):
        if now <= dt <= end:
            events.append({"name": "Non-Farm Payrolls (tự tính)", "time": dt.isoformat(), "source": "manual_nfp"})
    for ds in MANUAL_CPI_PPI_DATES:
        try:
            dt = datetime.strptime(ds, "%Y-%m-%d")
            if now <= dt <= end:
                events.append({"name": "CPI/PPI (danh sách thủ công)", "time": dt.isoformat(), "source": "manual_list"})
        except Exception:
            continue
    return events


def news_risk_adjustment(hours_before: int = 12, hours_after: int = 6) -> dict:
    """
    Kết quả dùng trực tiếp cho engine.py/bingx_trader.py:
    {"active": bool, "event": str|None, "size_mult": float, "sl_tighten_mult": float}
    - size_mult 0.5: giảm 50% khối lượng giao dịch trong vùng ảnh hưởng tin.
    - sl_tighten_mult 0.7: SL siết còn 70% khoảng cách bình thường (gọn hơn,
      lỗ nhỏ hơn nếu bị quét thanh khoản do biến động tin tức).
    Fail-open: lỗi bất kỳ -> {"active": False, size_mult=1.0, sl_tighten_mult=1.0}
    """
    try:
        events = get_high_impact_events(days_ahead=3)
        now = datetime.utcnow()
        for e in events:
            try:
                et = datetime.fromisoformat(e["time"])
            except Exception:
                continue
            if (et - timedelta(hours=hours_before)) <= now <= (et + timedelta(hours=hours_after)):
                log.warning("📰 [NEWS WINDOW] Đang trong vùng ảnh hưởng tin: %s (%s) -> giảm size, siết SL.",
                            e["name"], e["time"])
                return {"active": True, "event": e["name"], "size_mult": 0.5, "sl_tighten_mult": 0.7}
        return {"active": False, "event": None, "size_mult": 1.0, "sl_tighten_mult": 1.0}
    except Exception as ex:
        log.warning("⚠️ news_risk_adjustment lỗi (%s) -> fail-open, không điều chỉnh gì.", ex)
        return {"active": False, "event": None, "size_mult": 1.0, "sl_tighten_mult": 1.0}
