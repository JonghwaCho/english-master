"""Seed the plans table with free/basic/heavy/vip tier definitions."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.db.models import Plan
from app.extensions import db


PLANS = [
    {
        "code": "free",
        "name_ko": "무료",
        "price_krw": 0,
        "video_limit": 3,
        "ai_quota_monthly": 0,
        "features_json": {
            "ai_features": False,
            "playlist_sync": False,
            "export": False,
            "priority_support": False,
        },
    },
    {
        "code": "basic",
        "name_ko": "베이직",
        "price_krw": 4900,
        "video_limit": 20,
        "ai_quota_monthly": 50,
        "features_json": {
            "ai_features": True,
            "playlist_sync": True,
            "playlist_limit": 1,
            "export": False,
            "priority_support": False,
        },
    },
    {
        "code": "heavy",
        "name_ko": "헤비",
        "price_krw": 9900,
        "video_limit": 50,
        "ai_quota_monthly": 200,
        "features_json": {
            "ai_features": True,
            "playlist_sync": True,
            "playlist_limit": 5,
            "export": True,
            "priority_support": False,
        },
    },
    {
        "code": "vip",
        "name_ko": "VIP",
        "price_krw": 19900,
        "video_limit": 200,
        "ai_quota_monthly": -1,  # unlimited
        "features_json": {
            "ai_features": True,
            "playlist_sync": True,
            "playlist_limit": -1,
            "export": True,
            "priority_support": True,
            "priority_queue": True,
        },
    },
]


def main():
    app = create_app()
    with app.app_context():
        db.create_all()
        for plan_data in PLANS:
            existing = db.session.get(Plan, plan_data["code"])
            if existing:
                for k, v in plan_data.items():
                    setattr(existing, k, v)
            else:
                db.session.add(Plan(**plan_data))
        db.session.commit()
        print(f"[✓] Seeded {len(PLANS)} plans:")
        for p in db.session.scalars(db.select(Plan).order_by(Plan.price_krw)).all():
            print(f"    {p.code}: {p.name_ko} ₩{p.price_krw}/월, videos={p.video_limit}, AI={p.ai_quota_monthly}")


if __name__ == "__main__":
    main()
