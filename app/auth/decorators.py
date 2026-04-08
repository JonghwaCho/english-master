"""Authentication decorators: @require_auth, @require_tier.

Usage:
    from app.auth.decorators import require_auth
    from flask import g

    @bp.route("/me")
    @require_auth
    def me():
        return {"id": g.current_user.id, "email": g.current_user.email}
"""
from __future__ import annotations

from functools import wraps
from typing import Callable

from flask import g, request
from jose import JWTError

from app.auth.tokens import decode_access_token
from app.db.models import User
from app.extensions import db


def _extract_token() -> str | None:
    """Extract Bearer token from Authorization header."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):].strip()
    # Fallback: access_token cookie (for browser navigation)
    return request.cookies.get("access_token")


def require_auth(fn: Callable) -> Callable:
    """Decorator: ensure request has valid JWT and inject g.current_user."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = _extract_token()
        if not token:
            return {"error": "Unauthorized", "message": "로그인이 필요합니다"}, 401
        try:
            payload = decode_access_token(token)
        except JWTError as e:
            return {"error": "Unauthorized", "message": f"유효하지 않은 토큰: {e}"}, 401

        user_id = payload.get("sub")
        if not user_id:
            return {"error": "Unauthorized", "message": "토큰에 사용자 ID가 없습니다"}, 401

        user = db.session.get(User, user_id)
        if not user or user.status != "active":
            return {"error": "Unauthorized", "message": "계정을 찾을 수 없거나 비활성화되었습니다"}, 401

        g.current_user = user
        g.current_user_id = user.id
        return fn(*args, **kwargs)

    return wrapper


def require_tier(*allowed_tiers: str) -> Callable:
    """Decorator: ensure user tier is in allowed_tiers.

    Example:
        @require_tier('basic', 'heavy', 'vip')
        def ai_action():
            ...
    """

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = getattr(g, "current_user", None)
            if not user:
                return {"error": "Unauthorized", "message": "로그인이 필요합니다"}, 401
            if user.tier not in allowed_tiers:
                return {
                    "error": "Forbidden",
                    "message": f"이 기능은 {', '.join(allowed_tiers)} 플랜에서 사용 가능합니다",
                    "required_tiers": list(allowed_tiers),
                    "current_tier": user.tier,
                }, 403
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def current_user_id() -> str | None:
    """Helper: get current user ID from flask.g (or None if not authenticated)."""
    return getattr(g, "current_user_id", None)
