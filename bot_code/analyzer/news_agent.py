import logging
import feedparser
import time
from analyzer.llm_agents import LLMChain
from core_api.models import AppConfig
from sqlalchemy.orm import Session
from core_api.models import SessionLocal, AppConfig

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
                all_news.append(f"- {entry.title}: {entry.description[:200]}")
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

Trạng thái trả về chỉ ĐÚNG 1 từ:
- NGUYHIEM: Nếu có tin tức thiên nga đen.
- ANTOAN: Nếu thị trường bình thường hoặc chỉ là tin tức xấu thông thường.
"""
    
    # Init LLM
    try:
        # Giả định lấy API key từ env hoặc config
        llm = LLMChain()
        # Fake system prompt
        system_prompt = "Bạn là trợ lý AI chuyên phân tích tin tức tài chính."
        response = llm.query(system_prompt, prompt, max_tokens=10)
        
        result = response.strip().upper()
        if "NGUYHIEM" in result:
            log.warning("🚨 [KILL SWITCH] PHÁT HIỆN TIN TỨC THIÊN NGA ĐEN! KÍCH HOẠT KILL SWITCH TOÀN HỆ THỐNG!")
            activate_kill_switch()
        else:
            log.info("✅ Tin tức thị trường an toàn.")
            
    except Exception as e:
        log.error(f"Lỗi khi phân tích tin tức qua LLM: {e}")

def activate_kill_switch():
    db: Session = SessionLocal()
    try:
        cfg = db.query(AppConfig).first()
        if cfg:
            cfg.bot_active = False
            db.commit()
            log.critical("🛑 ĐÃ TẮT BOT (bot_active=False) VÌ THIÊN NGA ĐEN!")
    except Exception as e:
        log.error(f"Lỗi khi kích hoạt kill switch DB: {e}")
    finally:
        db.close()
