"""Tier-based quota enforcement.

Decorators:
    @enforce_video_quota — increment videos_added counter, reject if over monthly limit
    @enforce_ai_quota — increment ai_calls_used counter, reject if over monthly limit

Usage:
    from app.quota import enforce_video_quota

    @bp.route("/videos", methods=["POST"])
    @require_auth
    @enforce_video_quota
    def add_video():
        ...
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import wraps
from typing import Callable

from flask import g, jsonify
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Plan, UsageCounter
from app.extensions import db


def _current_period_ym() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _get_or_create_counter(user_id: str) -> UsageCounter:
    period = _current_period_ym()
    counter = db.session.scalar(
        select(UsageCounter).where(
            UsageCounter.user_id == user_id, UsageCounter.period_ym == period
        )
    )
    if not counter:
        counter = UsageCounter(user_id=user_id, period_ym=period)
        db.session.add(counter)
        db.session.flush()
    return counter


def _get_limits_for_tier(tier: str) -> tuple[int, int]:
    """Return (video_limit, ai_quota) for a tier. From DB if present, else config."""
    settings = get_settings()
    plan = db.session.get(Plan, tier)
    if plan:
        return plan.video_limit, plan.ai_quota_monthly

    # Fallback to config
    tier_limits = {
        "free": (settings.free_video_limit, settings.free_ai_quota),
        "basic": (settings.basic_video_limit, settings.basic_ai_quota),
        "heavy": (settings.heavy_video_limit, settings.heavy_ai_quota),
        "vip": (settings.vip_video_limit, settings.vip_ai_quota),
    }
    return tier_limits.get(tier, (0, 0))


def enforce_video_quota(fn: Callable) -> Callable:
    """Decorator: check monthly video limit, increment on success."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = g.current_user
        counter = _get_or_create_counter(user.id)
        video_limit, _ = _get_limits_for_tier(user.tier)

        if video_limit != -1 and counter.videos_added >= video_limit:
            return jsonify({
                "error": "quota_exceeded",
                "quota_type": "video",
                "message": f"이번 달 콘텐츠 등록 한도({video_limit}개)를 초과했습니다. 업그레이드가 필요합니다.",
                "limit": video_limit,
                "used": counter.videos_added,
                "tier": user.tier,
            }), 402

        # Run the endpoint
        result = fn(*args, **kwargs)

        # If response is a tuple with 2xx status, increment counter
        if isinstance(result, tuple):
            _, status = result[0], result[1]
        else:
            status = getattr(result, "status_code", 200)

        if 200 <= int(status) < 300:
            counter.videos_added += 1
            db.session.commit()

        return result

    return wrapper


def enforce_ai_quota(fn: Callable) -> Callable:
    """Decorator: check monthly AI call limit, increment on success."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = g.current_user
        counter = _get_or_create_counter(user.id)
        _, ai_quota = _get_limits_for_tier(user.tier)

        if ai_quota == 0:
            return jsonify({
                "error": "feature_not_available",
                "quota_type": "ai",
                "message": "AI 기능은 Basic 이상 플랜에서 사용 가능합니다",
                "tier": user.tier,
                "required_tier": "basic",
            }), 402

        if ai_quota != -1 and counter.ai_calls_used >= ai_quota:
            return jsonify({
                "error": "quota_exceeded",
                "quota_type": "ai",
                "message": f"이번 달 AI 호출 한도({ai_quota}회)를 초과했습니다",
                "limit": ai_quota,
                "used": counter.ai_calls_used,
                "tier": user.tier,
            }), 402

        result = fn(*args, **kwargs)

        if isinstance(result, tuple):
            _, status = result[0], result[1]
        else:
            status = getattr(result, "status_code", 200)

        if 200 <= int(status) < 300:
            counter.ai_calls_used += 1
            db.session.commit()

        return result

    return wrapper


def get_usage_summary(user_id: str, tier: str) -> dict:
    """Return current month's usage + limits."""
    counter = _get_or_create_counter(user_id)
    video_limit, ai_quota = _get_limits_for_tier(tier)
    return {
        "period": counter.period_ym,
        "videos": {
            "used": counter.videos_added,
            "limit": video_limit,
            "remaining": max(0, video_limit - counter.videos_added) if video_limit != -1 else None,
            "unlimited": video_limit == -1,
        },
        "ai_calls": {
            "used": counter.ai_calls_used,
            "limit": ai_quota,
            "remaining": max(0, ai_quota - counter.ai_calls_used) if ai_quota > 0 else 0,
            "unlimited": ai_quota == -1,
        },
    }
