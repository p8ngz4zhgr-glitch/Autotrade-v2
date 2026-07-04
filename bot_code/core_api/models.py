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

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
    pool_pre_ping=True,
)
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

    timestamp          = Column(DateTime, default=datetime.utcnow)


class SignalStats(Base):
    __tablename__ = "signal_stats"
    id                 = Column(Integer,  primary_key=True, index=True)
    symbol             = Column(String,   index=True)
    direction          = Column(String)
    win_rate           = Column(Float)
    total_trades       = Column(Integer)
    updated_at         = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(bind=engine)
