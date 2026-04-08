"""Pytest fixtures: Flask app, DB, test users."""
from __future__ import annotations

import os
import tempfile

import pytest

from app import create_app
from app.auth.passwords import hash_password
from app.config import Settings
from app.db.models import User, UserSettings
from app.extensions import db


@pytest.fixture
def app():
    """Create a test Flask app with a fresh in-memory SQLite DB per test."""
    test_db_fd, test_db_path = tempfile.mkstemp(suffix=".db")
    settings = Settings(
        env="development",
        debug=True,
        database_url=f"sqlite:///{test_db_path}",
        secret_key="test-secret-key-at-least-32-characters-long",
        jwt_secret="test-jwt-secret-at-least-32-characters-long",
        rate_limit_storage_uri="memory://",
    )
    app = create_app(settings=settings)
    app.config["TESTING"] = True

    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()

    os.close(test_db_fd)
    os.unlink(test_db_path)


@pytest.fixture
def client(app):
    return app.test_client()


class _UserProxy:
    """Detached user reference: stores scalar fields so tests can read them
    without requiring a live SQLAlchemy session."""

    def __init__(self, id, email, tier):
        self.id = id
        self.email = email
        self.tier = tier


@pytest.fixture
def user_factory(app):
    """Factory to create test users. Returns a lightweight proxy with id/email/tier."""
    counter = [0]

    def _make(email: str | None = None, password: str = "testpass123", tier: str = "free"):
        counter[0] += 1
        email = email or f"user{counter[0]}@test.com"
        with app.app_context():
            user = User(
                email=email,
                password_hash=hash_password(password),
                tier=tier,
                email_verified=True,
                status="active",
                name=f"Test User {counter[0]}",
            )
            db.session.add(user)
            db.session.flush()
            db.session.add(UserSettings(user_id=user.id, settings_json={}))
            db.session.commit()
            return _UserProxy(id=user.id, email=user.email, tier=user.tier)

    return _make


@pytest.fixture
def auth_headers(client):
    """Factory to login and return Authorization headers."""
    def _login(email: str, password: str = "testpass123"):
        res = client.post("/api/auth/login", json={"email": email, "password": password})
        assert res.status_code == 200, f"Login failed: {res.json}"
        access_token = res.json["access_token"]
        return {"Authorization": f"Bearer {access_token}"}

    return _login
