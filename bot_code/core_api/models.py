# ═══════════════════════════════════════════════════════════
# MODELS — Database tables for Postgres / SQLite
# ═══════════════════════════════════════════════════════════
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text, create_engine, Index
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./trading_bot.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Phân tách cấu hình riêng cho SQLite (Local) và Postgres (Cloud/Render)
if "sqlite" in DATABASE_URL:
    engine_kwargs = {
        "connect_args": {"check_same_thread": False},
        "pool_pre_ping": True,
    }
else:
    engine_kwargs = {
        "pool_pre_ping": True,     # Ping kiểm tra trước khi query
        "pool_recycle": 1800,      # Tự động reset kết nối mỗi 30 phút
        "pool_size": 10,           # Kích thước Pool tối ưu cho Render
        "max_overflow": 20,
        "pool_use_lifo": True,     # Luôn tái sử dụng các đường truyền mới nhất
        "connect_args": {          # BỔ SUNG: TCP Keepalives để chống rớt SSL trên Cloud
            "keepalives": 1,
            "keepalives_idle": 30,      # Gửi gói keepalive nếu rảnh 30s
            "keepalives_interval": 10,  # Khoảng cách giữa các lần gửi 10s
            "keepalives_count": 5       # Thử tối đa 5 lần
        }
    }

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id                    = Column(Integer,  primary_key=True, index=True)
    telegram_id           = Column(String,   unique=True, index=True)
    is_active             = Column(Boolean,  default=True)
    is_locked             = Column(Boolean,  default=False)
    exchange              = Column(String,   default="BINGX")
    api_key               = Column(String)
    api_secret_encrypted  = Column(String)

    capital               = Column(Float,    default=0.0)
    tier                  = Column(String,   default="TIER1")

    min_confidence        = Column(Float,    default=68.0)
    max_risk_pct          = Column(Float,    default=2.0)
    max_positions         = Column(Integer,  default=2)
    leverage              = Column(Integer,  default=5)
    auto_trade            = Column(Boolean,  default=False)

    registered_at         = Column(DateTime, default=datetime.utcnow)
    last_balance_update   = Column(DateTime, default=datetime.utcnow)
    total_pnl             = Column(Float,    default=0.0)


class TradeJournal(Base):
    __tablename__ = "trade_journal"
    id                 = Column(Integer,  primary_key=True, index=True)
    symbol             = Column(String,   index=True)
    user_id            = Column(String,   index=True, nullable=True)
    tier               = Column(String,   nullable=True)
    direction          = Column(String)

    outcome            = Column(String,   nullable=True)
    entry_price        = Column(Float,    nullable=True)
    exit_price         = Column(Float,    nullable=True)
    pnl_pct            = Column(Float,    default=0.0)
    pnl_usd            = Column(Float,    default=0.0)
    lesson             = Column(Text,     default="")
    context            = Column(String,   nullable=True)
    timestamp          = Column(DateTime, default=datetime.utcnow)


class SignalStats(Base):
    __tablename__ = "signal_stats"
    id                 = Column(Integer,  primary_key=True, index=True)
    symbol             = Column(String,   index=True)
    direction          = Column(String)
    win_rate           = Column(Float)
    total_trades       = Column(Integer)
    updated_at         = Column(DateTime, default=datetime.utcnow)

# ═══════════════════════════════════════════════════════════
# MODELS CHO HMM WORKER (MARKET REGIME DETECTOR)
# ═══════════════════════════════════════════════════════════

class TrackedSymbol(Base):
    __tablename__ = "tracked_symbols"
    id                 = Column(Integer,  primary_key=True, index=True)
    symbol             = Column(String,   unique=True, index=True)
    is_active          = Column(Boolean,  default=True)

class MarketRegime(Base):
    __tablename__ = "market_regimes"
    id                 = Column(Integer,  primary_key=True, index=True)
    symbol             = Column(String,   unique=True, index=True)
    current_regime     = Column(Integer)  
    regime_name        = Column(String)      
    confidence         = Column(Float)
    updated_at         = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# Tạo bảng
Base.metadata.create_all(bind=engine)
