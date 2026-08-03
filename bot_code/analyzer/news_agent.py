import logging
import feedparser
import time
import re
from analyzer.llm_agents import LLMChain

log = logging.getLogger("analyzer.news_agent")

RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/"
]

_NEWS_RISK_STATE = {
    "active": False,
    "event": None,
    "size_mult": 1.0,
    "sl_tighten_mult": 1.0,
    "last_check": 0
}

def get_news_agent_risk() -> dict:
    """Trả về trạng thái rủi ro tin tức được phân tích bởi LLM News Agent."""
    # Hết hiệu lực sau 2 tiếng nếu không có cập nhật mới
    if _NEWS_RISK_STATE["active"] and (time.time() - _NEWS_RISK_STATE.get("last_check", 0) > 7200):
        _NEWS_RISK_STATE["active"] = False
        _NEWS_RISK_STATE["event"] = None
        _NEWS_RISK_STATE["size_mult"] = 1.0
        _NEWS_RISK_STATE["sl_tighten_mult"] = 1.0
    return dict(_NEWS_RISK_STATE)

def scan_news_and_check_kill_switch():
    """Scan RSS feeds and use LLM to check for high-risk news events.
    Khi phát hiện tin xấu, tự động giảm vốn vào lệnh (size_mult=0.5) và siết SL thay vì tắt bot.
    """
    all_news = []
    
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:  # Top 5 recent news
                all_news.append(f"- {entry.title}: {entry.get('description', '')[:200]}")
        except Exception as e:
            log.error(f"Lỗi khi đọc feed RSS {feed_url}: {e}")

    if not all_news:
        return

    news_text = "\n".join(all_news)
    prompt = f"""
Bạn là chuyên gia phân tích rủi ro tin tức thị trường tài chính và Crypto.
Nhiệm vụ: Đọc danh sách các tin tức dưới đây và xác định xem có tin tức xấu / rủi ro biến động cao (như FUD lớn, tin đồn phá sản, lạm phát tăng vọt, chiến tranh, chính phủ cấm đoán, hack sàn,...) hay không.

Tin tức:
{news_text}

Trạng thái trả về CHỈ DUY NHẤT 1 TỪ VỚI CHỮ IN HOA, TUYỆT ĐỐI KHÔNG VIẾT THÊM LÝ DO HAY CÂU TỪ NÀO KHÁC:
- NGUYHIEM (nếu có tin xấu hoặc rủi ro biến động thị trường xấu; hệ thống sẽ tự động giảm 50% vốn vào lệnh để quản trị rủi ro nhưng KHÔNG tắt bot)
- ANTOAN (dùng cho mọi trường hợp bình thường hoặc tin tức biến động thông thường)
"""
    
    try:
        llm = LLMChain()
        system_prompt = "Bạn là trợ lý AI phân tích rủi ro. Chỉ trả lời duy nhất 1 từ: ANTOAN hoặc NGUYHIEM."
        response = llm.query(system_prompt, prompt, max_tokens=10)
        
        result = response.strip().upper()
        # Làm sạch kết quả: lấy từ đầu tiên
        first_word = re.sub(r'[^A-Z]', '', result.split()[0]) if result.split() else ""
        
        if first_word == "NGUYHIEM" and len(result) < 25 and not any(w in result for w in ["ANTOAN", "SAFE", "NORMAL", "KHONG"]):
            _NEWS_RISK_STATE["active"] = True
            _NEWS_RISK_STATE["event"] = "Tin xấu thị trường (AI Scanner)"
            _NEWS_RISK_STATE["size_mult"] = 0.5  # Giảm 50% vốn vào lệnh
            _NEWS_RISK_STATE["sl_tighten_mult"] = 0.7  # Siết SL 70%
            _NEWS_RISK_STATE["last_check"] = time.time()
            log.warning("📰 [NEWS RISK] PHÁT HIỆN TIN XẤU THỊ TRƯỜNG! Tự động giảm 50%% vốn vào lệnh (size_mult=0.5) & siết SL, BOT VẪN CHẠY BÌNH THƯỜNG. (LLM: %s)", result)
        else:
            _NEWS_RISK_STATE["active"] = False
            _NEWS_RISK_STATE["event"] = None
            _NEWS_RISK_STATE["size_mult"] = 1.0
            _NEWS_RISK_STATE["sl_tighten_mult"] = 1.0
            _NEWS_RISK_STATE["last_check"] = time.time()
            log.info("✅ News Agent: Tin tức thị trường an toàn. Sử dụng 100%% vốn tiêu chuẩn. (LLM: %s)", result[:60])
            
    except Exception as e:
        log.error(f"Lỗi khi phân tích tin tức qua LLM: {e}")

def activate_kill_switch():
    try:
        from core_api.models import engine
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text("UPDATE app_config SET bot_active = false, kill_switch = true"))
        log.critical("🛑 ĐÃ TẮT BOT (bot_active=False) THEO YÊU CẦU DỪNG KHẨN CẤP!")
    except Exception as e:
        log.error(f"Lỗi khi kích hoạt kill switch DB: {e}")

def init_default_app_config():
    """Tự động đảm bảo bot_active=True, kill_switch=False khi khởi động hệ thống."""
    try:
        from core_api.models import engine
        from sqlalchemy import text
        with engine.begin() as conn:
            res = conn.execute(text("SELECT id FROM app_config LIMIT 1")).fetchone()
            if not res:
                conn.execute(text("INSERT INTO app_config (bot_active, kill_switch) VALUES (true, false)"))
            else:
                conn.execute(text("UPDATE app_config SET bot_active = true, kill_switch = false"))
            log.info("✅ Đã khởi tạo/khôi phục AppConfig (bot_active=True, kill_switch=False).")
    except Exception as e:
        log.error(f"Lỗi khi khởi tạo AppConfig mặc định: {e}")

# Tự động khởi tạo cấu hình mặc định khi module được import
try:
    init_default_app_config()
except Exception:
    pass



