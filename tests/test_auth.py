"""Authentication flow tests."""
import pytest


def test_signup_success(client):
    res = client.post("/api/auth/signup", json={
        "email": "newuser@test.com",
        "password": "securepass123",
        "name": "New User",
        "consent_terms": True,
        "consent_privacy": True,
    })
    assert res.status_code == 201
    assert "access_token" in res.json
    assert res.json["user"]["email"] == "newuser@test.com"
    assert res.json["user"]["tier"] == "free"


def test_signup_missing_consent(client):
    res = client.post("/api/auth/signup", json={
        "email": "nouser@test.com",
        "password": "securepass123",
        "consent_terms": False,
        "consent_privacy": True,
    })
    assert res.status_code == 400
    assert res.json["error"] == "consent_required"


def test_signup_invalid_email(client):
    res = client.post("/api/auth/signup", json={
        "email": "not-an-email",
        "password": "securepass123",
        "consent_terms": True,
        "consent_privacy": True,
    })
    assert res.status_code == 400
    assert res.json["error"] == "invalid_email"


def test_signup_weak_password(client):
    res = client.post("/api/auth/signup", json={
        "email": "weak@test.com",
        "password": "short",
        "consent_terms": True,
        "consent_privacy": True,
    })
    assert res.status_code == 400
    assert res.json["error"] == "weak_password"


def test_signup_duplicate_email(client):
    client.post("/api/auth/signup", json={
        "email": "dup@test.com",
        "password": "securepass123",
        "consent_terms": True,
        "consent_privacy": True,
    })
    res = client.post("/api/auth/signup", json={
        "email": "dup@test.com",
        "password": "securepass123",
        "consent_terms": True,
        "consent_privacy": True,
    })
    assert res.status_code == 409
    assert res.json["error"] == "email_exists"


def test_login_success(client, user_factory):
    user = user_factory(email="login@test.com")
    res = client.post("/api/auth/login", json={
        "email": "login@test.com",
        "password": "testpass123",
    })
    assert res.status_code == 200
    assert "access_token" in res.json


def test_login_wrong_password(client, user_factory):
    user_factory(email="wrong@test.com")
    res = client.post("/api/auth/login", json={
        "email": "wrong@test.com",
        "password": "wrong-password",
    })
    assert res.status_code == 401
    assert res.json["error"] == "invalid_credentials"


def test_login_nonexistent(client):
    res = client.post("/api/auth/login", json={
        "email": "noone@test.com",
        "password": "whatever123",
    })
    assert res.status_code == 401


def test_me_requires_auth(client):
    res = client.get("/api/auth/me")
    assert res.status_code == 401


def test_me_with_token(client, user_factory, auth_headers):
    user_factory(email="me@test.com")
    headers = auth_headers("me@test.com")
    res = client.get("/api/auth/me", headers=headers)
    assert res.status_code == 200
    assert res.json["email"] == "me@test.com"


def test_logout_clears_cookie(client, user_factory):
    user_factory(email="logout@test.com")
    client.post("/api/auth/login", json={
        "email": "logout@test.com",
        "password": "testpass123",
    })
    res = client.post("/api/auth/logout")
    assert res.status_code == 200


def test_refresh_with_valid_cookie(client, user_factory):
    user_factory(email="refresh@test.com")
    login_res = client.post("/api/auth/login", json={
        "email": "refresh@test.com",
        "password": "testpass123",
    })
    assert login_res.status_code == 200
    # Cookies are set on the test client automatically
    res = client.post("/api/auth/refresh")
    assert res.status_code == 200
    assert "access_token" in res.json
