"""Authentication endpoints: signup, login, logout, refresh, email verification, password reset."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, jsonify, make_response, request
from jose import JWTError
from sqlalchemy import select

from app.auth.decorators import require_auth
from app.auth.emails import send_password_reset_email, send_verification_email
from app.auth.passwords import hash_password, verify_password
from app.auth.tokens import (
    create_access_token,
    create_email_verification_token,
    create_password_reset_token,
    create_refresh_token,
    decode_email_verification_token,
    decode_password_reset_token,
    hash_refresh_token,
)
from app.config import get_settings
from app.db.models import User, UserSession, UserSettings
from app.extensions import db, limiter

auth_bp = Blueprint("auth", __name__)

EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
MIN_PASSWORD_LENGTH = 8


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _set_refresh_cookie(response, refresh_plain: str, max_age_days: int = 30):
    settings = get_settings()
    response.set_cookie(
        "refresh_token",
        refresh_plain,
        max_age=max_age_days * 86400,
        httponly=True,
        secure=settings.is_production,
        samesite="Lax",
        path="/api/auth",
    )


def _create_session_and_tokens(user: User) -> tuple[str, str]:
    """Create a new session + access/refresh tokens. Commits to DB."""
    settings = get_settings()
    refresh_plain, refresh_hash = create_refresh_token()
    session_row = UserSession(
        user_id=user.id,
        refresh_token_hash=refresh_hash,
        user_agent=request.headers.get("User-Agent", "")[:500],
        ip_address=request.remote_addr,
        expires_at=_now() + timedelta(days=settings.jwt_refresh_token_days),
    )
    db.session.add(session_row)
    user.last_login_at = _now()
    db.session.commit()
    access_token = create_access_token(user.id, tier=user.tier)
    return access_token, refresh_plain


def _user_public_dict(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "tier": user.tier,
        "email_verified": user.email_verified,
        "locale": user.locale,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


# ─────────────────────────────────────────────────────
# Signup / Login
# ─────────────────────────────────────────────────────


@auth_bp.route("/signup", methods=["POST"])
@limiter.limit("5 per hour")
def signup():
    """Create a new user account with email + password."""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    name = (data.get("name") or "").strip() or None
    consent_terms = bool(data.get("consent_terms"))
    consent_privacy = bool(data.get("consent_privacy"))
    consent_marketing = bool(data.get("consent_marketing", False))

    # Validation
    if not email or not EMAIL_REGEX.match(email):
        return jsonify({"error": "invalid_email", "message": "올바른 이메일 주소를 입력해주세요"}), 400
    if len(password) < MIN_PASSWORD_LENGTH:
        return jsonify({"error": "weak_password", "message": f"비밀번호는 최소 {MIN_PASSWORD_LENGTH}자 이상이어야 합니다"}), 400
    if not consent_terms or not consent_privacy:
        return jsonify({"error": "consent_required", "message": "이용약관 및 개인정보처리방침에 동의해주세요"}), 400

    # Check duplicate
    existing = db.session.scalar(select(User).where(User.email == email))
    if existing:
        return jsonify({"error": "email_exists", "message": "이미 등록된 이메일입니다"}), 409

    now = _now()
    user = User(
        email=email,
        password_hash=hash_password(password),
        name=name,
        tier="free",
        email_verified=False,
        consent_terms_at=now,
        consent_privacy_at=now,
        consent_marketing_at=now if consent_marketing else None,
    )
    db.session.add(user)
    db.session.flush()  # assign user.id

    # Create empty settings row
    db.session.add(UserSettings(user_id=user.id, settings_json={}))
    db.session.commit()

    # Send verification email (async in prod; sync for now)
    verification_token = create_email_verification_token(user.id)
    send_verification_email(email, verification_token)

    access_token, refresh_plain = _create_session_and_tokens(user)

    response = make_response(jsonify({
        "user": _user_public_dict(user),
        "access_token": access_token,
    }))
    _set_refresh_cookie(response, refresh_plain)
    return response, 201


@auth_bp.route("/login", methods=["POST"])
@limiter.limit("10 per minute")
def login():
    """Authenticate with email + password."""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "missing_credentials", "message": "이메일과 비밀번호를 입력해주세요"}), 400

    user = db.session.scalar(select(User).where(User.email == email))
    if not user or not user.password_hash or not verify_password(password, user.password_hash):
        return jsonify({"error": "invalid_credentials", "message": "이메일 또는 비밀번호가 올바르지 않습니다"}), 401
    if user.status != "active":
        return jsonify({"error": "account_inactive", "message": "비활성 계정입니다"}), 403

    access_token, refresh_plain = _create_session_and_tokens(user)
    response = make_response(jsonify({
        "user": _user_public_dict(user),
        "access_token": access_token,
    }))
    _set_refresh_cookie(response, refresh_plain)
    return response


@auth_bp.route("/refresh", methods=["POST"])
def refresh():
    """Exchange a valid refresh cookie for a new access token."""
    refresh_plain = request.cookies.get("refresh_token")
    if not refresh_plain:
        return jsonify({"error": "no_refresh_token", "message": "리프레시 토큰이 없습니다"}), 401

    token_hash = hash_refresh_token(refresh_plain)
    session_row = db.session.scalar(
        select(UserSession).where(UserSession.refresh_token_hash == token_hash)
    )
    if not session_row or session_row.revoked_at:
        return jsonify({"error": "invalid_refresh_token", "message": "만료되었거나 유효하지 않은 세션입니다"}), 401
    # Normalize expires_at to timezone-aware for comparison (SQLite strips tz)
    exp = session_row.expires_at
    if exp and exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < _now():
        return jsonify({"error": "invalid_refresh_token", "message": "만료된 세션입니다"}), 401

    user = db.session.get(User, session_row.user_id)
    if not user or user.status != "active":
        return jsonify({"error": "account_inactive"}), 403

    access_token = create_access_token(user.id, tier=user.tier)
    return jsonify({
        "user": _user_public_dict(user),
        "access_token": access_token,
    })


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """Revoke the current refresh token."""
    refresh_plain = request.cookies.get("refresh_token")
    if refresh_plain:
        token_hash = hash_refresh_token(refresh_plain)
        session_row = db.session.scalar(
            select(UserSession).where(UserSession.refresh_token_hash == token_hash)
        )
        if session_row:
            session_row.revoked_at = _now()
            db.session.commit()

    response = make_response(jsonify({"ok": True}))
    response.delete_cookie("refresh_token", path="/api/auth")
    return response


# ─────────────────────────────────────────────────────
# Email verification
# ─────────────────────────────────────────────────────


@auth_bp.route("/verify-email", methods=["POST"])
def verify_email():
    data = request.get_json(silent=True) or {}
    token = data.get("token", "")
    try:
        payload = decode_email_verification_token(token)
    except JWTError:
        return jsonify({"error": "invalid_token", "message": "유효하지 않거나 만료된 인증 링크입니다"}), 400

    user = db.session.get(User, payload["sub"])
    if not user:
        return jsonify({"error": "user_not_found"}), 404

    user.email_verified = True
    db.session.commit()
    return jsonify({"ok": True, "message": "이메일이 인증되었습니다"})


@auth_bp.route("/resend-verification", methods=["POST"])
@limiter.limit("3 per hour")
@require_auth
def resend_verification():
    from flask import g
    user = g.current_user
    if user.email_verified:
        return jsonify({"message": "이미 인증된 이메일입니다"}), 200
    token = create_email_verification_token(user.id)
    send_verification_email(user.email, token)
    return jsonify({"ok": True, "message": "인증 메일이 재전송되었습니다"})


# ─────────────────────────────────────────────────────
# Password reset
# ─────────────────────────────────────────────────────


@auth_bp.route("/forgot-password", methods=["POST"])
@limiter.limit("5 per hour")
def forgot_password():
    """Request a password reset email. Always returns success (don't leak existence)."""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()

    user = db.session.scalar(select(User).where(User.email == email))
    if user and user.status == "active":
        token = create_password_reset_token(user.id)
        send_password_reset_email(email, token)

    return jsonify({"ok": True, "message": "해당 이메일로 재설정 링크가 전송되었습니다 (등록된 계정인 경우)"})


@auth_bp.route("/reset-password", methods=["POST"])
def reset_password():
    data = request.get_json(silent=True) or {}
    token = data.get("token", "")
    new_password = data.get("password", "")

    if len(new_password) < MIN_PASSWORD_LENGTH:
        return jsonify({"error": "weak_password", "message": f"비밀번호는 최소 {MIN_PASSWORD_LENGTH}자 이상"}), 400

    try:
        payload = decode_password_reset_token(token)
    except JWTError:
        return jsonify({"error": "invalid_token", "message": "유효하지 않거나 만료된 링크"}), 400

    user = db.session.get(User, payload["sub"])
    if not user:
        return jsonify({"error": "user_not_found"}), 404

    user.password_hash = hash_password(new_password)
    # Revoke all existing sessions
    for session_row in user.sessions:
        session_row.revoked_at = _now()
    db.session.commit()
    return jsonify({"ok": True, "message": "비밀번호가 변경되었습니다. 다시 로그인해주세요"})


# ─────────────────────────────────────────────────────
# Current user info
# ─────────────────────────────────────────────────────


@auth_bp.route("/me", methods=["GET"])
@require_auth
def me():
    from flask import g
    return jsonify(_user_public_dict(g.current_user))


@auth_bp.route("/me", methods=["DELETE"])
@require_auth
def delete_account():
    """Delete (soft) the current user account. PIPA-compliant."""
    from flask import g
    user = g.current_user
    user.status = "deleted"
    user.deleted_at = _now()
    # In real deletion flow, could also anonymize email/name
    user.email = f"deleted_{user.id}@deleted.invalid"
    user.name = None
    db.session.commit()

    response = make_response(jsonify({"ok": True, "message": "계정이 삭제되었습니다"}))
    response.delete_cookie("refresh_token", path="/api/auth")
    return response
