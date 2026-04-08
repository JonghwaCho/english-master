"""Tier quota enforcement tests."""
import pytest


def test_free_user_video_limit(app, client, user_factory, auth_headers):
    """Free tier is limited to 3 videos per month."""
    user_factory(email="free@test.com", tier="free")
    headers = auth_headers("free@test.com")

    # Add 3 text contents (free limit)
    for i in range(3):
        res = client.post("/api/content/text", json={
            "title": f"Test {i}",
            "text": f"This is test content number {i}. It has multiple sentences. Enough for testing.",
        }, headers=headers)
        assert res.status_code == 201, f"Failed at {i}: {res.json}"

    # 4th should fail with 402
    res = client.post("/api/content/text", json={
        "title": "Overflow",
        "text": "This should be rejected due to quota. Multiple sentences here. Final one.",
    }, headers=headers)
    assert res.status_code == 402
    assert res.json["error"] == "quota_exceeded"
    assert res.json["quota_type"] == "video"


def test_usage_endpoint(client, user_factory, auth_headers):
    user_factory(email="usage@test.com", tier="basic")
    headers = auth_headers("usage@test.com")

    res = client.get("/api/usage", headers=headers)
    assert res.status_code == 200
    assert res.json["videos"]["limit"] == 20
    assert res.json["videos"]["used"] == 0
    assert res.json["ai_calls"]["limit"] == 50


def test_ai_quota_blocks_free_tier(client, user_factory, auth_headers):
    user_factory(email="noai@test.com", tier="free")
    headers = auth_headers("noai@test.com")
    res = client.post("/api/ai/action", json={
        "action": "literal",
        "sentence": "Hello world.",
    }, headers=headers)
    # Free tier has ai_quota=0, should get 402
    assert res.status_code == 402
    assert res.json["quota_type"] == "ai"
