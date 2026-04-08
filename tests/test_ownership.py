"""Multi-tenant ownership isolation tests.

CRITICAL: These tests prove that user A cannot access user B's data.
Every endpoint must filter by user_id and return 404 for cross-user access.
"""
import pytest

from app.db.models import Category, Sentence, Video, Word
from app.extensions import db


def test_categories_isolated(client, user_factory, auth_headers):
    user_a = user_factory(email="a@test.com")
    user_b = user_factory(email="b@test.com")

    # A creates a category
    headers_a = auth_headers("a@test.com")
    res = client.post("/api/categories", json={"name": "My Category A"}, headers=headers_a)
    assert res.status_code == 201
    cat_id = res.json["id"]

    # B cannot see A's category
    headers_b = auth_headers("b@test.com")
    res = client.get("/api/categories", headers=headers_b)
    assert res.status_code == 200
    assert res.json == []

    # B cannot delete A's category (404)
    res = client.delete(f"/api/categories/{cat_id}", headers=headers_b)
    assert res.status_code == 404


def test_videos_isolated(app, client, user_factory, auth_headers):
    user_a = user_factory(email="va@test.com")
    user_b = user_factory(email="vb@test.com")

    with app.app_context():
        # Insert video for user A directly
        v = Video(user_id=user_a.id, url="https://example.com/a", title="A's video", content_type="text")
        db.session.add(v)
        db.session.commit()
        video_id = v.id

    headers_b = auth_headers("vb@test.com")

    # B cannot see A's video
    res = client.get("/api/videos", headers=headers_b)
    assert res.status_code == 200
    assert res.json == []

    # B cannot access video info
    res = client.get(f"/api/videos/{video_id}/info", headers=headers_b)
    assert res.status_code == 404

    # B cannot delete
    res = client.delete(f"/api/videos/{video_id}", headers=headers_b)
    assert res.status_code == 404

    # A can access
    headers_a = auth_headers("va@test.com")
    res = client.get(f"/api/videos/{video_id}/info", headers=headers_a)
    assert res.status_code == 200
    assert res.json["title"] == "A's video"


def test_words_isolated(app, client, user_factory, auth_headers):
    user_a = user_factory(email="wa@test.com")
    user_b = user_factory(email="wb@test.com")

    headers_a = auth_headers("wa@test.com")
    res = client.post("/api/words/add", json={"word": "serendipity"}, headers=headers_a)
    assert res.status_code == 200
    word_id = res.json["id"]

    headers_b = auth_headers("wb@test.com")

    # B sees empty list
    res = client.get("/api/words/unknown", headers=headers_b)
    assert res.status_code == 200
    assert res.json == []

    # B cannot delete A's word
    res = client.delete(f"/api/words/{word_id}", headers=headers_b)
    assert res.status_code == 404

    # A can still see their word
    res = client.get("/api/words/unknown", headers=headers_a)
    assert len(res.json) == 1


def test_stats_per_user(app, client, user_factory, auth_headers):
    user_a = user_factory(email="sa@test.com")
    user_b = user_factory(email="sb@test.com")

    with app.app_context():
        v = Video(user_id=user_a.id, url="https://example.com/sa", title="A", content_type="text")
        db.session.add(v)
        db.session.flush()
        db.session.add(Sentence(video_id=v.id, text="Hello.", status="known"))
        db.session.add(Sentence(video_id=v.id, text="Bye.", status="unknown"))
        db.session.commit()

    # A sees 2 sentences
    headers_a = auth_headers("sa@test.com")
    res = client.get("/api/stats", headers=headers_a)
    assert res.json["total_sentences"] == 2
    assert res.json["known_sentences"] == 1
    assert res.json["unknown_sentences"] == 1

    # B sees 0
    headers_b = auth_headers("sb@test.com")
    res = client.get("/api/stats", headers=headers_b)
    assert res.json["total_sentences"] == 0
