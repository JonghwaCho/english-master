"""Migrate v1.3 SQLite data (single-user) to the new multi-tenant schema.

Strategy:
    1. Create or find a seed admin user (VIP tier) in the new DB
    2. For each table in the OLD SQLite file, copy all rows to the NEW DB,
       assigning every row to the seed admin's user_id
    3. Preserve primary key IDs where possible (for sentences/videos/words FKs)

Usage:
    python scripts/migrate_sqlite_to_postgres.py \\
        --source data/english_master.db \\
        --admin-email admin@englishmaster.kr \\
        --admin-password changeme-then-reset

The destination DB is configured via DATABASE_URL in .env (could be SQLite or Postgres).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make app package importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.auth.passwords import hash_password
from app.db.models import (
    AiCache,
    Category,
    OAuthAccount,
    Playlist,
    PlaylistVideo,
    Review,
    Sentence,
    StudyLog,
    User,
    UserSettings,
    Video,
    Word,
    WordMeaning,
    WordVideoLink,
)
from app.extensions import db


def _now():
    return datetime.now(timezone.utc)


def get_or_create_admin(email: str, password: str, name: str = "Admin") -> User:
    existing = db.session.scalar(db.select(User).where(User.email == email))
    if existing:
        print(f"[+] Found existing admin: {email} (id={existing.id})")
        return existing

    admin = User(
        email=email,
        password_hash=hash_password(password),
        name=name,
        tier="vip",
        email_verified=True,
        status="active",
        consent_terms_at=_now(),
        consent_privacy_at=_now(),
    )
    db.session.add(admin)
    db.session.flush()
    db.session.add(UserSettings(user_id=admin.id, settings_json={}))
    db.session.commit()
    print(f"[+] Created admin user: {email} (id={admin.id})")
    return admin


def _fetchall(conn: sqlite3.Connection, sql: str) -> list[sqlite3.Row]:
    cur = conn.execute(sql)
    return cur.fetchall()


def migrate(source_sqlite_path: Path, admin_email: str, admin_password: str):
    if not source_sqlite_path.exists():
        raise FileNotFoundError(f"Source SQLite DB not found: {source_sqlite_path}")

    old_conn = sqlite3.connect(str(source_sqlite_path))
    old_conn.row_factory = sqlite3.Row

    admin = get_or_create_admin(admin_email, admin_password)
    admin_id = admin.id

    # ── Categories ──
    cat_id_map = {}
    for row in _fetchall(old_conn, "SELECT id, name FROM categories"):
        cat = db.session.scalar(
            db.select(Category).where(
                Category.user_id == admin_id, Category.name == row["name"]
            )
        )
        if not cat:
            cat = Category(user_id=admin_id, name=row["name"])
            db.session.add(cat)
            db.session.flush()
        cat_id_map[row["id"]] = cat.id
    db.session.commit()
    print(f"[+] Migrated {len(cat_id_map)} categories")

    # ── Videos ──
    video_id_map = {}
    video_rows = _fetchall(old_conn, "SELECT * FROM videos")
    for row in video_rows:
        existing = db.session.scalar(
            db.select(Video).where(Video.user_id == admin_id, Video.url == row["url"])
        )
        if existing:
            video_id_map[row["id"]] = existing.id
            continue
        video = Video(
            user_id=admin_id,
            url=row["url"],
            video_id=row["video_id"] if "video_id" in row.keys() else None,
            title=row["title"] if "title" in row.keys() else None,
            content_type=row["content_type"] if "content_type" in row.keys() else "youtube",
            source_text=row["source_text"] if "source_text" in row.keys() else None,
            category_id=cat_id_map.get(row["category_id"]) if "category_id" in row.keys() else None,
        )
        db.session.add(video)
        db.session.flush()
        video_id_map[row["id"]] = video.id
    db.session.commit()
    print(f"[+] Migrated {len(video_id_map)} videos")

    # ── Sentences ──
    sentence_id_map = {}
    sent_rows = _fetchall(old_conn, "SELECT * FROM sentences")
    for row in sent_rows:
        new_video_id = video_id_map.get(row["video_id"])
        if not new_video_id:
            continue
        sent = Sentence(
            video_id=new_video_id,
            text=row["text"],
            paragraph_idx=row["paragraph_idx"] or 0,
            sentence_idx=row["sentence_idx"] or 0,
            status=row["status"] or "new",
            start_time=row["start_time"] if "start_time" in row.keys() else None,
            end_time=row["end_time"] if "end_time" in row.keys() else None,
            translation=row["translation"] if "translation" in row.keys() else None,
            unknown_count=row["unknown_count"] if "unknown_count" in row.keys() else 0,
        )
        db.session.add(sent)
        db.session.flush()
        sentence_id_map[row["id"]] = sent.id
    db.session.commit()
    print(f"[+] Migrated {len(sentence_id_map)} sentences")

    # ── Words ──
    word_id_map = {}
    word_rows = _fetchall(old_conn, "SELECT * FROM words")
    for row in word_rows:
        existing = db.session.scalar(
            db.select(Word).where(Word.user_id == admin_id, Word.word == row["word"])
        )
        if existing:
            word_id_map[row["id"]] = existing.id
            continue
        w = Word(user_id=admin_id, word=row["word"], status=row["status"] or "unknown")
        db.session.add(w)
        db.session.flush()
        word_id_map[row["id"]] = w.id
    db.session.commit()
    print(f"[+] Migrated {len(word_id_map)} words")

    # ── Reviews ──
    review_count = 0
    for row in _fetchall(old_conn, "SELECT * FROM reviews"):
        if row["item_type"] == "sentence":
            new_item_id = sentence_id_map.get(row["item_id"])
        elif row["item_type"] == "word":
            new_item_id = word_id_map.get(row["item_id"])
        else:
            continue
        if not new_item_id:
            continue
        review = Review(
            item_id=new_item_id,
            item_type=row["item_type"],
            level=row["level"] or 0,
            next_review=datetime.fromisoformat(row["next_review"]) if row["next_review"] else _now(),
            last_review=datetime.fromisoformat(row["last_review"]) if row["last_review"] else None,
            streak=row["streak"] or 0,
        )
        db.session.add(review)
        review_count += 1
    db.session.commit()
    print(f"[+] Migrated {review_count} reviews")

    # ── Playlists ──
    try:
        pl_id_map = {}
        for row in _fetchall(old_conn, "SELECT * FROM playlists"):
            pl = Playlist(
                user_id=admin_id,
                playlist_id=row["playlist_id"],
                title=row["title"],
                url=row["url"],
                category_id=cat_id_map.get(row["category_id"]) if row["category_id"] else None,
                enabled=bool(row["enabled"]) if "enabled" in row.keys() else True,
            )
            db.session.add(pl)
            db.session.flush()
            pl_id_map[row["id"]] = pl.id
        db.session.commit()
        print(f"[+] Migrated {len(pl_id_map)} playlists")

        for row in _fetchall(old_conn, "SELECT * FROM playlist_videos"):
            new_pl_id = pl_id_map.get(row["playlist_id"])
            if not new_pl_id:
                continue
            db.session.add(PlaylistVideo(
                playlist_id=new_pl_id,
                video_db_id=video_id_map.get(row["video_db_id"]) if row["video_db_id"] else None,
                youtube_video_id=row["youtube_video_id"],
            ))
        db.session.commit()
    except Exception as e:
        print(f"[!] Playlist migration skipped: {e}")
        db.session.rollback()

    # ── Word-Video Links ──
    try:
        for row in _fetchall(old_conn, "SELECT * FROM word_video_link"):
            new_word_id = word_id_map.get(row["word_id"])
            new_video_id = video_id_map.get(row["video_id"])
            if new_word_id and new_video_id:
                db.session.add(WordVideoLink(word_id=new_word_id, video_id=new_video_id))
        db.session.commit()
    except Exception as e:
        print(f"[!] Word-video link migration skipped: {e}")
        db.session.rollback()

    # ── Shared caches (no remap needed) ──
    try:
        for row in _fetchall(old_conn, "SELECT * FROM word_meanings"):
            db.session.merge(WordMeaning(
                word=row["word"],
                meaning=row["meaning"],
                source=row["source"] or "dict",
            ))
        db.session.commit()
        print("[+] Migrated word_meanings cache")
    except Exception as e:
        print(f"[!] Word meanings skipped: {e}")
        db.session.rollback()

    try:
        for row in _fetchall(old_conn, "SELECT * FROM ai_cache"):
            existing = db.session.scalar(
                db.select(AiCache).where(
                    AiCache.sentence_text == row["sentence_text"],
                    AiCache.action == row["action"],
                )
            )
            if not existing:
                db.session.add(AiCache(
                    sentence_text=row["sentence_text"],
                    action=row["action"],
                    result=row["result"],
                ))
        db.session.commit()
        print("[+] Migrated ai_cache")
    except Exception as e:
        print(f"[!] AI cache skipped: {e}")
        db.session.rollback()

    old_conn.close()
    print("\n[✓] Migration complete!")
    print(f"    Admin user: {admin_email} / {admin_password}")
    print("    Please log in and change the admin password immediately.")


def main():
    parser = argparse.ArgumentParser(description="Migrate v1.3 SQLite to v2 multi-tenant DB")
    parser.add_argument(
        "--source", type=Path, default=Path("data/english_master.db"),
        help="Path to v1.3 SQLite database",
    )
    parser.add_argument(
        "--admin-email", type=str, required=True, help="Seed admin email"
    )
    parser.add_argument(
        "--admin-password", type=str, required=True, help="Seed admin password (change after login!)"
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        # Ensure schema exists
        db.create_all()
        migrate(args.source, args.admin_email, args.admin_password)


if __name__ == "__main__":
    main()
