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

def scan_news_and_check_kill_switch():
    """Scan RSS feeds and use LLM to check for Black Swan events."""
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
Bạn là chuyên gia phân tích rủi ro thị trường tài chính và Crypto.
Nhiệm vụ: Đọc danh sách các tin tức dưới đây và xác định xem có bất kỳ sự kiện 'Thiên nga đen' (Black Swan) nào đang xảy ra không.
Thiên nga đen bao gồm: Sàn giao dịch lớn (Binance, Coinbase...) bị hack hoặc sập, SEC hoặc chính phủ ra lệnh cấm toàn diện, chiến tranh thế giới nổ ra, tether (USDT) sụp đổ...
Chỉ báo hiệu nếu thực sự CỰC KỲ NGUY HIỂM ảnh hưởng sập toàn thị trường.

Tin tức:
{news_text}

Trạng thái trả về CHỈ DUY NHẤT 1 TỪ VỚI CHỮ IN HOA, TUYỆT ĐỐI KHÔNG VIẾT THÊM LÝ DO HAY CÂU TỪ NÀO KHÁC:
- NGUYHIEM (chỉ dùng nếu thực sự có thảm hoạ thiên nga đen cực kỳ khủng khiếp)
- ANTOAN (dùng cho mọi trường hợp bình thường hoặc tin tức biến động thông thường)
"""
    
    try:
        llm = LLMChain()
        system_prompt = "Bạn là trợ lý AI phân tích rủi ro. Chỉ trả lời duy nhất 1 từ: ANTOAN hoặc NGUYHIEM."
        response = llm.query(system_prompt, prompt, max_tokens=10)
        
        result = response.strip().upper()
        # Làm sạch kết quả: lấy từ đầu tiên
        first_word = re.sub(r'[^A-Z]', '', result.split()[0]) if result.split() else ""
        
        # Chỉ kích hoạt Kill Switch khi từ ĐẦU TIÊN là NGUYHIEM và câu trả lời ngắn (<20 ký tự) không chứa từ an toàn
        if first_word == "NGUYHIEM" and len(result) < 25 and not any(w in result for w in ["ANTOAN", "SAFE", "NORMAL", "KHONG"]):
            log.warning("🚨 [KILL SWITCH] PHÁT HIỆN TIN TỨC THIÊN NGA ĐEN! KÍCH HOẠT KILL SWITCH TOÀN HỆ THỐNG! (LLM: %s)", result)
            activate_kill_switch()
        else:
            log.info("✅ Tin tức thị trường an toàn. (LLM trả về: %s)", result[:60])
            
    except Exception as e:
        log.error(f"Lỗi khi phân tích tin tức qua LLM: {e}")

def activate_kill_switch():
    try:
        from core_api.models import engine
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text("UPDATE app_config SET bot_active = false, kill_switch = true"))
        log.critical("🛑 ĐÃ TẮT BOT (bot_active=False) VÌ THIÊN NGA ĐEN!")
    except Exception as e:
        log.error(f"Lỗi khi kích hoạt kill switch DB: {e}")

def init_default_app_config():
    """Tự động tạo bản ghi AppConfig mặc định (bot_active=True, kill_switch=False) nếu chưa có."""
    try:
        from core_api.models import engine
        from sqlalchemy import text
        with engine.begin() as conn:
            res = conn.execute(text("SELECT id FROM app_config LIMIT 1")).fetchone()
            if not res:
                conn.execute(text("INSERT INTO app_config (bot_active, kill_switch) VALUES (true, false)"))
                log.info("✅ Đã khởi tạo bản ghi AppConfig mặc định (bot_active=True, kill_switch=False).")
    except Exception as e:
        log.error(f"Lỗi khi khởi tạo AppConfig mặc định: {e}")

# Tự động khởi tạo cấu hình mặc định khi module được import
try:
    init_default_app_config()
except Exception:
    pass


