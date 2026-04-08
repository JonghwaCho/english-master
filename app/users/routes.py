"""User profile, settings, and subscription endpoints."""
from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from app.auth.decorators import require_auth
from app.db.models import UserSettings
from app.extensions import db

users_bp = Blueprint("users", __name__)


@users_bp.route("/settings", methods=["GET"])
@require_auth
def get_settings_endpoint():
    """Get per-user UI/application settings (migrated from localStorage)."""
    settings_row = db.session.get(UserSettings, g.current_user.id)
    if not settings_row:
        settings_row = UserSettings(user_id=g.current_user.id, settings_json={})
        db.session.add(settings_row)
        db.session.commit()
    return jsonify(settings_row.settings_json or {})


@users_bp.route("/settings", methods=["PUT"])
@require_auth
def put_settings():
    """Replace the entire settings JSON."""
    data = request.get_json(silent=True) or {}
    settings_row = db.session.get(UserSettings, g.current_user.id)
    if not settings_row:
        settings_row = UserSettings(user_id=g.current_user.id, settings_json={})
        db.session.add(settings_row)
    settings_row.settings_json = data
    db.session.commit()
    return jsonify({"ok": True})


@users_bp.route("/settings", methods=["PATCH"])
@require_auth
def patch_settings():
    """Merge partial settings into existing JSON."""
    data = request.get_json(silent=True) or {}
    settings_row = db.session.get(UserSettings, g.current_user.id)
    if not settings_row:
        settings_row = UserSettings(user_id=g.current_user.id, settings_json={})
        db.session.add(settings_row)
    current = dict(settings_row.settings_json or {})
    current.update(data)
    settings_row.settings_json = current
    db.session.commit()
    return jsonify(current)


@users_bp.route("/profile", methods=["GET"])
@require_auth
def profile():
    u = g.current_user
    return jsonify({
        "id": u.id,
        "email": u.email,
        "name": u.name,
        "tier": u.tier,
        "email_verified": u.email_verified,
        "locale": u.locale,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
    })


@users_bp.route("/profile", methods=["PATCH"])
@require_auth
def update_profile():
    data = request.get_json(silent=True) or {}
    u = g.current_user
    if "name" in data:
        u.name = (data["name"] or "").strip()[:100] or None
    if "locale" in data:
        u.locale = data["locale"][:10]
    db.session.commit()
    return jsonify({
        "id": u.id,
        "email": u.email,
        "name": u.name,
        "locale": u.locale,
    })
