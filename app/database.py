"""Durable data layer (C1 + C3).

ALL state that must survive a server restart lives here, in Postgres on the
managed free tier (Neon) — never on the host's ephemeral disk. SQLite is used
only as a zero-setup local-dev default.

The Post.status column is the heart of the C3 fix: a durable approval/publish
state machine so a sleeping host can never lose a draft, post at the wrong time,
or double-post.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from app.config import get_settings

settings = get_settings()

# pool_pre_ping survives Neon's scale-to-zero idle cold-starts.
engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


class PostStatus(str, enum.Enum):
    DRAFTED = "DRAFTED"                    # generated, verification running
    PENDING_APPROVAL = "PENDING_APPROVAL"  # waiting on the human gate
    APPROVED = "APPROVED"                  # human approved, has publish_at
    PUBLISHING = "PUBLISHING"              # poller is publishing now (lock)
    PUBLISHED = "PUBLISHED"                # live on Instagram
    FAILED = "FAILED"                      # publish failed after retries
    REJECTED = "REJECTED"                  # human rejected; planner advances


class PostFormat(str, enum.Enum):
    CAROUSEL = "CAROUSEL"
    STORY = "STORY"
    SINGLE = "SINGLE"
    # REEL deferred per scope decision.


class Post(Base):
    __tablename__ = "posts"
    # Date-keyed guard: at most one published-track post per calendar day.
    __table_args__ = (UniqueConstraint("post_date", name="uq_posts_post_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD

    # What was picked
    pillar: Mapped[str] = mapped_column(String(32), default="feature", index=True)
    feature_id: Mapped[str] = mapped_column(String(64), index=True)
    angle_id: Mapped[str] = mapped_column(String(64), index=True)
    format: Mapped[PostFormat] = mapped_column(Enum(PostFormat), default=PostFormat.CAROUSEL)

    # Generated content
    caption: Mapped[Optional[str]] = mapped_column(Text)
    hashtags: Mapped[Optional[list]] = mapped_column(JSON)        # max 5 (C5)
    alt_text: Mapped[Optional[str]] = mapped_column(Text)
    image_urls: Mapped[Optional[list]] = mapped_column(JSON)      # public URLs, 1..n slides
    caption_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)  # dedup (C5/dedup)

    # Traits captured at write-time (feeds the Sprint 6 learning loop)
    traits: Mapped[Optional[dict]] = mapped_column(JSON)

    # State machine (C3)
    status: Mapped[PostStatus] = mapped_column(
        Enum(PostStatus), default=PostStatus.DRAFTED, index=True
    )
    verification: Mapped[Optional[dict]] = mapped_column(JSON)  # grammar/copyright/aso/dedup results
    reject_reason: Mapped[Optional[str]] = mapped_column(Text)

    # Publishing
    publish_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    creation_id: Mapped[Optional[str]] = mapped_column(String(128))  # Meta container id (idempotency)
    ig_media_id: Mapped[Optional[str]] = mapped_column(String(128))  # final published id
    permalink: Mapped[Optional[str]] = mapped_column(String(512))
    utm_content: Mapped[Optional[str]] = mapped_column(String(128), index=True)  # {feature}-{angle}-{id}

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    metrics: Mapped[list["PostMetric"]] = relationship(back_populates="post")


class PostMetric(Base):
    """Time-series insights (Sprint 6). Pulled at T+24h and T+72h."""
    __tablename__ = "post_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    follower_count_at_capture: Mapped[Optional[int]] = mapped_column(Integer)
    # 2026-correct metric names (old impressions/profile_views are deprecated)
    reach: Mapped[Optional[int]] = mapped_column(Integer)
    views: Mapped[Optional[int]] = mapped_column(Integer)
    saved: Mapped[Optional[int]] = mapped_column(Integer)
    shares: Mapped[Optional[int]] = mapped_column(Integer)
    total_interactions: Mapped[Optional[int]] = mapped_column(Integer)
    bio_link_clicked: Mapped[Optional[int]] = mapped_column(Integer)
    profile_visits: Mapped[Optional[int]] = mapped_column(Integer)

    post: Mapped[Post] = relationship(back_populates="metrics")


class Install(Base):
    """Install attribution joined on utm_content (Sprint 5)."""
    __tablename__ = "installs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    utm_content: Mapped[str] = mapped_column(String(128), index=True)
    source: Mapped[str] = mapped_column(String(32))  # play_console | revenuecat | shortlink
    count: Mapped[int] = mapped_column(Integer, default=0)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Learning(Base):
    """Extracted 'post DNA' of top performers, injected back into prompts (Sprint 6)."""
    __tablename__ = "learnings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    week_of: Mapped[str] = mapped_column(String(10))
    pattern: Mapped[dict] = mapped_column(JSON)
    performance_score: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AppToken(Base):
    """Encrypted IG token + refresh bookkeeping (C1: survives redeploys)."""
    __tablename__ = "app_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    value: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    refreshed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


def init_db() -> None:
    """Create tables if absent. Alembic migrations come in Sprint 1."""
    Base.metadata.create_all(engine)
