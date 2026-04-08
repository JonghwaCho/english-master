"""OAuth 2.0 integrations: Google and Kakao.

Usage flow:
    1. Client navigates to GET /api/auth/oauth/google → redirects to Google consent
    2. Google redirects to /api/auth/oauth/google/callback with ?code=
    3. Backend exchanges code for tokens, fetches user profile
    4. Creates or finds matching User, issues JWT access + refresh cookie
    5. Redirects client to frontend with access_token in URL fragment

Setup:
    - Google: https://console.cloud.google.com → Credentials → OAuth 2.0 Client IDs
      Authorized redirect URI: http://127.0.0.1:5294/api/auth/oauth/google/callback
    - Kakao: https://developers.kakao.com → My Application → Product Settings → Kakao Login
      Redirect URI: http://127.0.0.1:5294/api/auth/oauth/kakao/callback
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests
from flask import Blueprint, current_app, jsonify, make_response, redirect, request
from sqlalchemy import select

from app.auth.routes import _create_session_and_tokens, _set_refresh_cookie, _user_public_dict
from app.config import get_settings
from app.db.models import OAuthAccount, User, UserSettings
from app.extensions import db

logger = logging.getLogger(__name__)

oauth_bp = Blueprint("oauth", __name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────
# Google OAuth
# ─────────────────────────────────────────────────────

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


@oauth_bp.route("/google", methods=["GET"])
def google_login():
    """Step 1: Redirect user to Google OAuth consent screen."""
    settings = get_settings()
    if not settings.google_client_id:
        return jsonify({"error": "oauth_not_configured", "message": "Google OAuth가 설정되지 않았습니다"}), 500

    state = secrets.token_urlsafe(24)
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    response = redirect(f"{GOOGLE_AUTH_URL}?{urlencode(params)}")
    response.set_cookie("oauth_state", state, max_age=600, httponly=True, samesite="Lax")
    return response


@oauth_bp.route("/google/callback", methods=["GET"])
def google_callback():
    """Step 2: Google redirects here with ?code=. Exchange for tokens and login."""
    settings = get_settings()
    code = request.args.get("code")
    state = request.args.get("state")
    saved_state = request.cookies.get("oauth_state")

    if not code or state != saved_state:
        return _redirect_with_error("invalid_oauth_state")

    # Exchange code for tokens
    try:
        token_res = requests.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": settings.google_redirect_uri,
            "grant_type": "authorization_code",
        }, timeout=10)
        token_res.raise_for_status()
        tokens = token_res.json()

        userinfo_res = requests.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
            timeout=10,
        )
        userinfo_res.raise_for_status()
        profile = userinfo_res.json()
    except Exception as e:
        logger.error("Google OAuth error: %s", e)
        return _redirect_with_error("google_oauth_failed")

    return _complete_oauth_login(
        provider="google",
        provider_user_id=profile["sub"],
        email=profile.get("email", "").lower(),
        name=profile.get("name"),
        email_verified=profile.get("email_verified", False),
        access_token=tokens.get("access_token"),
        refresh_token=tokens.get("refresh_token"),
    )


# ─────────────────────────────────────────────────────
# Kakao OAuth
# ─────────────────────────────────────────────────────

KAKAO_AUTH_URL = "https://kauth.kakao.com/oauth/authorize"
KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_USERINFO_URL = "https://kapi.kakao.com/v2/user/me"


@oauth_bp.route("/kakao", methods=["GET"])
def kakao_login():
    settings = get_settings()
    if not settings.kakao_client_id:
        return jsonify({"error": "oauth_not_configured", "message": "Kakao OAuth가 설정되지 않았습니다"}), 500

    state = secrets.token_urlsafe(24)
    params = {
        "client_id": settings.kakao_client_id,
        "redirect_uri": settings.kakao_redirect_uri,
        "response_type": "code",
        "state": state,
        "scope": "profile_nickname account_email",
    }
    response = redirect(f"{KAKAO_AUTH_URL}?{urlencode(params)}")
    response.set_cookie("oauth_state", state, max_age=600, httponly=True, samesite="Lax")
    return response


@oauth_bp.route("/kakao/callback", methods=["GET"])
def kakao_callback():
    settings = get_settings()
    code = request.args.get("code")
    state = request.args.get("state")
    saved_state = request.cookies.get("oauth_state")

    if not code or state != saved_state:
        return _redirect_with_error("invalid_oauth_state")

    try:
        token_res = requests.post(KAKAO_TOKEN_URL, data={
            "grant_type": "authorization_code",
            "client_id": settings.kakao_client_id,
            "client_secret": settings.kakao_client_secret,
            "redirect_uri": settings.kakao_redirect_uri,
            "code": code,
        }, timeout=10)
        token_res.raise_for_status()
        tokens = token_res.json()

        userinfo_res = requests.get(
            KAKAO_USERINFO_URL,
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
            timeout=10,
        )
        userinfo_res.raise_for_status()
        profile = userinfo_res.json()
    except Exception as e:
        logger.error("Kakao OAuth error: %s", e)
        return _redirect_with_error("kakao_oauth_failed")

    kakao_account = profile.get("kakao_account", {})
    email = (kakao_account.get("email") or "").lower()
    nickname = (profile.get("properties", {}) or {}).get("nickname")

    return _complete_oauth_login(
        provider="kakao",
        provider_user_id=str(profile["id"]),
        email=email or f"kakao_{profile['id']}@no-email.kakao",
        name=nickname,
        email_verified=bool(kakao_account.get("is_email_verified")),
        access_token=tokens.get("access_token"),
        refresh_token=tokens.get("refresh_token"),
    )


# ─────────────────────────────────────────────────────
# Shared OAuth login completion
# ─────────────────────────────────────────────────────


def _complete_oauth_login(
    *,
    provider: str,
    provider_user_id: str,
    email: str,
    name: str | None,
    email_verified: bool,
    access_token: str | None,
    refresh_token: str | None,
):
    """Find or create User, issue session, redirect to frontend."""
    # Find existing OAuth account
    oauth_row = db.session.scalar(
        select(OAuthAccount).where(
            OAuthAccount.provider == provider,
            OAuthAccount.provider_user_id == provider_user_id,
        )
    )

    if oauth_row:
        user = db.session.get(User, oauth_row.user_id)
        oauth_row.access_token = access_token
        oauth_row.refresh_token = refresh_token
    else:
        # Try to match by email
        user = db.session.scalar(select(User).where(User.email == email)) if email else None
        if not user:
            user = User(
                email=email,
                name=name,
                email_verified=email_verified,
                tier="free",
                consent_terms_at=_now(),
                consent_privacy_at=_now(),
            )
            db.session.add(user)
            db.session.flush()
            db.session.add(UserSettings(user_id=user.id, settings_json={}))

        db.session.add(OAuthAccount(
            user_id=user.id,
            provider=provider,
            provider_user_id=provider_user_id,
            provider_email=email,
            access_token=access_token,
            refresh_token=refresh_token,
        ))

    if user.status != "active":
        db.session.rollback()
        return _redirect_with_error("account_inactive")

    access_jwt, refresh_plain = _create_session_and_tokens(user)

    settings = get_settings()
    # Redirect to frontend with access token in fragment (not query string, for security)
    response = make_response(redirect(f"{settings.frontend_url}/#access_token={access_jwt}"))
    response.delete_cookie("oauth_state")
    _set_refresh_cookie(response, refresh_plain)
    return response


def _redirect_with_error(error_code: str):
    settings = get_settings()
    return redirect(f"{settings.frontend_url}/login?error={error_code}")
