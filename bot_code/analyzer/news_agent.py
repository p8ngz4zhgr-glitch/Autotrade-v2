import logging
import feedparser
import time
from analyzer.llm_agents import LLMChain
from sqlalchemy.orm import Session
import core_api.models as models_mod

SessionLocal = getattr(models_mod, "SessionLocal", None)
AppConfig = getattr(models_mod, "AppConfig", None)

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

Trạng thái trả về CHỈ DUY NHẤT 1 từ, KHÔNG GIẢI THÍCH THÊM:
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
        if "NGUYHIEM" in result and "ANTOAN" not in result and "SAFE" not in result:
            log.warning("🚨 [KILL SWITCH] PHÁT HIỆN TIN TỨC THIÊN NGA ĐEN! KÍCH HOẠT KILL SWITCH TOÀN HỆ THỐNG!")
            activate_kill_switch()
        else:
            log.info("✅ Tin tức thị trường an toàn. (LLM trả về: %s)", result[:60])
            
    except Exception as e:
        log.error(f"Lỗi khi phân tích tin tức qua LLM: {e}")

def activate_kill_switch():
    try:
        from core_api.models import SessionLocal as SL, AppConfig as AC
        db: Session = SL()
        try:
            cfg = db.query(AC).first()
            if not cfg:
                cfg = AC(bot_active=False, kill_switch=True)
                db.add(cfg)
            else:
                cfg.bot_active = False
                cfg.kill_switch = True
            db.commit()
            log.critical("🛑 ĐÃ TẮT BOT (bot_active=False) VÌ THIÊN NGA ĐEN!")
        finally:
            db.close()
    except Exception as e:
        log.error(f"Lỗi khi kích hoạt kill switch DB: {e}")

def init_default_app_config():
    """Tự động tạo bản ghi AppConfig mặc định (bot_active=True, kill_switch=False) nếu chưa có."""
    try:
        from core_api.models import SessionLocal as SL, AppConfig as AC
        db: Session = SL()
        try:
            cfg = db.query(AC).first()
            if not cfg:
                cfg = AC(bot_active=True, kill_switch=False)
                db.add(cfg)
                db.commit()
                log.info("✅ Đã khởi tạo bản ghi AppConfig mặc định (bot_active=True, kill_switch=False).")
        finally:
            db.close()
    except Exception as e:
        log.error(f"Lỗi khi khởi tạo AppConfig mặc định: {e}")

# Tự động khởi tạo cấu hình mặc định khi module được import
try:
    init_default_app_config()
except Exception:
    pass

