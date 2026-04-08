"""JWT token generation and verification.

Access tokens (short-lived, 15 min) are sent as Authorization: Bearer header.
Refresh tokens (30 days) are sent via httpOnly SameSite=Lax cookies and are
revocable via the user_sessions table.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from jose import JWTError, jwt

from app.config import get_settings


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(user_id: str, tier: str = "free", extra: Optional[dict] = None) -> str:
    """Create a short-lived access token."""
    settings = get_settings()
    now = _now()
    payload = {
        "sub": str(user_id),
        "tier": tier,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_access_token_minutes)).timestamp()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token() -> tuple[str, str]:
    """Create an opaque refresh token.

    Returns (plain_token, token_hash). Store the hash in DB; give the plain token
    to the client via httpOnly cookie. On refresh, hash the incoming token and
    compare.
    """
    plain = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(plain.encode("utf-8")).hexdigest()
    return plain, token_hash


def hash_refresh_token(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate an access token. Raises JWTError on failure."""
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if payload.get("type") != "access":
        raise JWTError("Invalid token type")
    return payload


def create_email_verification_token(user_id: str) -> str:
    """Create a 24-hour email verification token."""
    settings = get_settings()
    now = _now()
    payload = {
        "sub": str(user_id),
        "type": "email_verification",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=24)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_email_verification_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if payload.get("type") != "email_verification":
        raise JWTError("Invalid token type")
    return payload


def create_password_reset_token(user_id: str) -> str:
    """Create a 1-hour password reset token."""
    settings = get_settings()
    now = _now()
    payload = {
        "sub": str(user_id),
        "type": "password_reset",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_password_reset_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if payload.get("type") != "password_reset":
        raise JWTError("Invalid token type")
    return payload
