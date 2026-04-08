"""Complete SQLAlchemy models for English Master commercial SaaS.

All user-owned tables have `user_id` FK to users.id with CASCADE DELETE.
Shared caches (ai_cache, word_meanings) have no user_id for cross-user cost savings.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


def _uuid() -> str:
    """Return a random UUID as string (portable across SQLite and PostgreSQL)."""
    return str(uuid.uuid4())


# ─────────────────────────────────────────────────────
# Users & Authentication
# ─────────────────────────────────────────────────────


class User(Base, TimestampMixin):
    """Application user account."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # null for OAuth-only users
    name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    locale: Mapped[str] = mapped_column(String(10), default="ko_KR", nullable=False)
    tier: Mapped[str] = mapped_column(String(20), default="free", nullable=False)
    # status: active | suspended | deleted
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # PIPA consent tracking
    consent_terms_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    consent_privacy_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    consent_marketing_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    sessions: Mapped[list["UserSession"]] = relationship(
        "UserSession", back_populates="user", cascade="all, delete-orphan"
    )
    oauth_accounts: Mapped[list["OAuthAccount"]] = relationship(
        "OAuthAccount", back_populates="user", cascade="all, delete-orphan"
    )
    settings: Mapped[Optional["UserSettings"]] = relationship(
        "UserSettings", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    videos: Mapped[list["Video"]] = relationship(
        "Video", back_populates="user", cascade="all, delete-orphan"
    )
    words: Mapped[list["Word"]] = relationship(
        "Word", back_populates="user", cascade="all, delete-orphan"
    )
    categories: Mapped[list["Category"]] = relationship(
        "Category", back_populates="user", cascade="all, delete-orphan"
    )
    playlists: Mapped[list["Playlist"]] = relationship(
        "Playlist", back_populates="user", cascade="all, delete-orphan"
    )


class UserSession(Base, TimestampMixin):
    """Refresh token session for JWT revocation."""

    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    refresh_token_hash: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="sessions")


class OAuthAccount(Base, TimestampMixin):
    """Linked external OAuth accounts (Google, Kakao)."""

    __tablename__ = "oauth_accounts"
    __table_args__ = (
        UniqueConstraint("provider", "provider_user_id", name="uq_oauth_provider_user"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)  # google | kakao
    provider_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    access_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="oauth_accounts")


class UserSettings(Base, TimestampMixin):
    """Per-user UI and application settings (migrated from localStorage)."""

    __tablename__ = "user_settings"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # Stores all settings as JSON (ttsEnabled, videoEnabled, shortcutKeys, etc.)
    settings_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="settings")


# ─────────────────────────────────────────────────────
# Categories
# ─────────────────────────────────────────────────────


class Category(Base, TimestampMixin):
    """User-defined content category."""

    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_category_user_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="categories")
    videos: Mapped[list["Video"]] = relationship("Video", back_populates="category")


# ─────────────────────────────────────────────────────
# Videos & Content
# ─────────────────────────────────────────────────────


class Video(Base, TimestampMixin):
    """A content item: YouTube video, text article, or pasted text."""

    __tablename__ = "videos"
    __table_args__ = (
        UniqueConstraint("user_id", "url", name="uq_video_user_url"),
        Index("ix_video_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    video_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # YouTube video id
    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # content_type: 'youtube' | 'youtube_lyrics' | 'text'
    content_type: Mapped[str] = mapped_column(String(20), default="youtube", nullable=False)
    source_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )

    user: Mapped["User"] = relationship("User", back_populates="videos")
    category: Mapped[Optional["Category"]] = relationship("Category", back_populates="videos")
    sentences: Mapped[list["Sentence"]] = relationship(
        "Sentence", back_populates="video", cascade="all, delete-orphan"
    )


class Sentence(Base):
    """Individual sentence within a video/text content."""

    __tablename__ = "sentences"
    __table_args__ = (
        Index("ix_sentence_video_para_sent", "video_id", "paragraph_idx", "sentence_idx"),
        Index("ix_sentence_video_status", "video_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    paragraph_idx: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sentence_idx: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # status: 'new' | 'known' | 'unknown' | 'mastered'
    status: Mapped[str] = mapped_column(String(20), default="new", nullable=False)
    start_time: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    end_time: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    translation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    unknown_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    video: Mapped["Video"] = relationship("Video", back_populates="sentences")


# ─────────────────────────────────────────────────────
# Words
# ─────────────────────────────────────────────────────


class Word(Base, TimestampMixin):
    """User's vocabulary word (unknown, known, or mastered)."""

    __tablename__ = "words"
    __table_args__ = (
        UniqueConstraint("user_id", "word", name="uq_word_user_word"),
        Index("ix_word_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    word: Mapped[str] = mapped_column(String(255), nullable=False)
    # status: 'unknown' | 'known' | 'mastered'
    status: Mapped[str] = mapped_column(String(20), default="unknown", nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="words")


class WordVideoLink(Base, TimestampMixin):
    """Tracks which video a word was learned from."""

    __tablename__ = "word_video_link"
    __table_args__ = (
        UniqueConstraint("word_id", "video_id", name="uq_wvl_word_video"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    word_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("words.id", ondelete="CASCADE"), nullable=False, index=True
    )
    video_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True
    )


# ─────────────────────────────────────────────────────
# Spaced Repetition (Ebbinghaus)
# ─────────────────────────────────────────────────────


class Review(Base, TimestampMixin):
    """Spaced-repetition review schedule entry for a sentence or word."""

    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("item_id", "item_type", name="uq_review_item"),
        Index("ix_review_next_review", "next_review"),
        CheckConstraint("item_type IN ('sentence', 'word')", name="ck_review_item_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    item_type: Mapped[str] = mapped_column(String(20), nullable=False)
    level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_review: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_review: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    streak: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


# ─────────────────────────────────────────────────────
# Playlists
# ─────────────────────────────────────────────────────


class Playlist(Base, TimestampMixin):
    """YouTube playlist subscription for auto-sync."""

    __tablename__ = "playlists"
    __table_args__ = (
        UniqueConstraint("user_id", "playlist_id", name="uq_playlist_user_pl"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    playlist_id: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    category_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_checked: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="playlists")
    playlist_videos: Mapped[list["PlaylistVideo"]] = relationship(
        "PlaylistVideo", back_populates="playlist", cascade="all, delete-orphan"
    )


class PlaylistVideo(Base, TimestampMixin):
    """Join table between playlist and video."""

    __tablename__ = "playlist_videos"
    __table_args__ = (
        UniqueConstraint("playlist_id", "youtube_video_id", name="uq_plv_playlist_yt"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    playlist_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("playlists.id", ondelete="CASCADE"), nullable=False, index=True
    )
    video_db_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("videos.id", ondelete="SET NULL"), nullable=True
    )
    youtube_video_id: Mapped[str] = mapped_column(String(50), nullable=False)

    playlist: Mapped["Playlist"] = relationship("Playlist", back_populates="playlist_videos")


# ─────────────────────────────────────────────────────
# Activity Logging
# ─────────────────────────────────────────────────────


class StudyLog(Base):
    """Activity log for analytics (per-user via item_id → video → user)."""

    __tablename__ = "study_log"
    __table_args__ = (
        Index("ix_study_log_item", "item_id", "item_type"),
        Index("ix_study_log_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    item_type: Mapped[str] = mapped_column(String(20), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)  # study | review | bulk_study
    correct: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ─────────────────────────────────────────────────────
# Shared Caches (no user_id - shared across all users)
# ─────────────────────────────────────────────────────


class WordMeaning(Base, TimestampMixin):
    """Global cache of word meanings (shared across all users)."""

    __tablename__ = "word_meanings"

    word: Mapped[str] = mapped_column(String(255), primary_key=True)
    meaning: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="dict", nullable=False)  # ai | dict | none


class AiCache(Base, TimestampMixin):
    """Global cache of AI responses keyed by (sentence_text, action)."""

    __tablename__ = "ai_cache"
    __table_args__ = (
        UniqueConstraint("sentence_text", "action", name="uq_ai_cache_sent_action"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sentence_text: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    result: Mapped[str] = mapped_column(Text, nullable=False)


# ─────────────────────────────────────────────────────
# Billing & Subscription (Phase 2 preview, tables created now)
# ─────────────────────────────────────────────────────


class Plan(Base, TimestampMixin):
    """Subscription plan (free, basic, heavy, vip)."""

    __tablename__ = "plans"

    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    name_ko: Mapped[str] = mapped_column(String(100), nullable=False)
    price_krw: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    video_limit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # per month
    ai_quota_monthly: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # -1 = unlimited
    features_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Subscription(Base, TimestampMixin):
    """User's current subscription state."""

    __tablename__ = "subscriptions"
    __table_args__ = (
        Index("ix_sub_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_code: Mapped[str] = mapped_column(
        String(20), ForeignKey("plans.code"), nullable=False
    )
    # status: trialing | active | past_due | canceled
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    toss_billing_key: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # encrypted


class Payment(Base, TimestampMixin):
    """Individual payment record."""

    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("toss_order_id", name="uq_payment_order"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subscription_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True
    )
    toss_payment_key: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    toss_order_id: Mapped[str] = mapped_column(String(200), nullable=False)
    amount_krw: Mapped[int] = mapped_column(Integer, nullable=False)
    # status: requested | paid | failed | canceled
    status: Mapped[str] = mapped_column(String(20), default="requested", nullable=False)
    failure_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    receipt_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)


class UsageCounter(Base, TimestampMixin):
    """Monthly usage counters per user for tier enforcement."""

    __tablename__ = "usage_counters"
    __table_args__ = (
        UniqueConstraint("user_id", "period_ym", name="uq_usage_user_period"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    period_ym: Mapped[str] = mapped_column(String(7), nullable=False)  # 'YYYY-MM'
    videos_added: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ai_calls_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class AiUsageLog(Base):
    """Audit log of every AI API call for billing/debugging."""

    __tablename__ = "ai_usage_log"
    __table_args__ = (
        Index("ix_ai_log_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    tokens_in: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cost_krw: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
