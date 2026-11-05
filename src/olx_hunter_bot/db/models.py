from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Naive UTC (SQLite has no tz support; everything in the DB is UTC)."""
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class HuntStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


class RunStatus(StrEnum):
    RUNNING = "running"
    OK = "ok"
    ERROR = "error"


class Chat(Base):
    """A Telegram chat talking to the bot (one for a personal bot)."""

    __tablename__ = "chats"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    agent_session_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Hunt(Base):
    """A standing request ("find me good headphones under 4000 UAH")."""

    __tablename__ = "hunts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    title: Mapped[str] = mapped_column(String(200))
    criteria: Mapped[dict[str, Any]] = mapped_column(JSON)
    interval_minutes: Mapped[int] = mapped_column(Integer)
    # Automatic re-checks every interval_minutes. Off by default: a hunt runs once
    # when created and then only on request, until the user switches this on.
    scheduled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    status: Mapped[HuntStatus] = mapped_column(String(16), default=HuntStatus.ACTIVE, index=True)
    next_run_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)


class SeenListing(Base):
    """Every listing the hunt agent evaluated — used for dedup across runs."""

    __tablename__ = "seen_listings"

    hunt_id: Mapped[int] = mapped_column(ForeignKey("hunts.id", ondelete="CASCADE"), primary_key=True)
    listing_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    url: Mapped[str] = mapped_column(String(500), default="")
    price_uah: Mapped[float | None] = mapped_column(Float)
    score: Mapped[int] = mapped_column(Integer, default=0)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class HuntRun(Base):
    __tablename__ = "hunt_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hunt_id: Mapped[int] = mapped_column(ForeignKey("hunts.id", ondelete="CASCADE"), index=True)
    trigger: Mapped[str] = mapped_column(String(16))  # scheduled | manual
    status: Mapped[RunStatus] = mapped_column(String(16), default=RunStatus.RUNNING)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    evaluated_count: Mapped[int] = mapped_column(Integer, default=0)
    notified_count: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
