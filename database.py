import sqlite3
import os
import hashlib
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "english_master.db")


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE NOT NULL,
            video_id TEXT,
            title TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS sentences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            paragraph_idx INTEGER DEFAULT 0,
            sentence_idx INTEGER DEFAULT 0,
            status TEXT DEFAULT 'new',
            FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS words (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            word TEXT UNIQUE NOT NULL,
            status TEXT DEFAULT 'unknown',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            item_type TEXT NOT NULL CHECK(item_type IN ('sentence','word')),
            level INTEGER DEFAULT 0,
            next_review TEXT NOT NULL,
            last_review TEXT,
            streak INTEGER DEFAULT 0,
            UNIQUE(item_id, item_type)
        );
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    # Migration: add category_id to videos
    try:
        conn.execute("ALTER TABLE videos ADD COLUMN category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL")
    except Exception:
        pass
    # Migration: add timing columns if not exist
    try:
        conn.execute("ALTER TABLE sentences ADD COLUMN start_time REAL DEFAULT 0")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE sentences ADD COLUMN end_time REAL DEFAULT 0")
    except Exception:
        pass
    # Migration: add translation column if not exist
    try:
        conn.execute("ALTER TABLE sentences ADD COLUMN translation TEXT DEFAULT ''")
    except Exception:
        pass
    # Migration: add content_type to videos
    try:
        conn.execute("ALTER TABLE videos ADD COLUMN content_type TEXT DEFAULT 'youtube'")
    except Exception:
        pass
    # Migration: add source_text to videos
    try:
        conn.execute("ALTER TABLE videos ADD COLUMN source_text TEXT DEFAULT ''")
    except Exception:
        pass

    # Playlists tables
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS playlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            playlist_id TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            category_id INTEGER,
            enabled INTEGER DEFAULT 1,
            last_checked TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS playlist_videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            playlist_id INTEGER NOT NULL,
            video_db_id INTEGER NOT NULL,
            youtube_video_id TEXT NOT NULL,
            added_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
            FOREIGN KEY (video_db_id) REFERENCES videos(id) ON DELETE CASCADE,
            UNIQUE(playlist_id, youtube_video_id)
        );
    """)

    conn.commit()
    conn.close()


# ── Categories ──────────────────────────────────────────

def get_categories():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM categories ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_category(name):
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (name,))
    conn.commit()
    row = conn.execute("SELECT id FROM categories WHERE name=?", (name,)).fetchone()
    conn.close()
    return row["id"] if row else None


def delete_category(category_id):
    conn = get_conn()
    conn.execute("UPDATE videos SET category_id=NULL WHERE category_id=?", (category_id,))
    conn.execute("DELETE FROM categories WHERE id=?", (category_id,))
    conn.commit()
    conn.close()


def rename_category(category_id, new_name):
    conn = get_conn()
    conn.execute("UPDATE categories SET name=? WHERE id=?", (new_name, category_id))
    conn.commit()
    conn.close()


def set_video_category(video_id, category_id):
    conn = get_conn()
    conn.execute("UPDATE videos SET category_id=? WHERE id=?", (category_id, video_id))
    conn.commit()
    conn.close()


# ── Videos ──────────────────────────────────────────────

def add_video(url, video_id, title):
    conn = get_conn()
    try:
        conn.execute("INSERT OR IGNORE INTO videos (url, video_id, title) VALUES (?,?,?)",
                      (url, video_id, title))
        conn.commit()
        row = conn.execute("SELECT id FROM videos WHERE url=?", (url,)).fetchone()
        return row["id"]
    finally:
        conn.close()


def add_text_content(title, text, category_id=None, content_type='text', url=None):
    """Add article/text content. Returns video_id or None if duplicate."""
    if url is None:
        text_hash = hashlib.md5(text.encode()).hexdigest()
        url = 'text://' + text_hash
    conn = get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO videos (url, video_id, title, content_type, source_text, category_id) VALUES (?,?,?,?,?,?)",
            (url, None, title, content_type, text, category_id)
        )
        conn.commit()
        row = conn.execute("SELECT id FROM videos WHERE url=?", (url,)).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def get_video(video_id):
    """Get a single video/content by ID."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_videos(category_id=None):
    conn = get_conn()
    query = """
        SELECT v.*, c.name as category_name,
               COUNT(s.id) as total_sentences,
               SUM(CASE WHEN s.status='known' THEN 1 ELSE 0 END) as known_count,
               SUM(CASE WHEN s.status='unknown' THEN 1 ELSE 0 END) as unknown_count,
               SUM(CASE WHEN s.status='new' THEN 1 ELSE 0 END) as new_count,
               SUM(CASE WHEN s.status='mastered' THEN 1 ELSE 0 END) as mastered_count
        FROM videos v
        LEFT JOIN sentences s ON v.id = s.video_id
        LEFT JOIN categories c ON v.category_id = c.id
    """
    params = ()
    if category_id is not None:
        if category_id == 0:
            query += " WHERE v.category_id IS NULL"
        else:
            query += " WHERE v.category_id=?"
            params = (category_id,)
    query += " GROUP BY v.id ORDER BY v.created_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_video(video_id):
    conn = get_conn()
    conn.execute("DELETE FROM reviews WHERE item_type='sentence' AND item_id IN (SELECT id FROM sentences WHERE video_id=?)", (video_id,))
    conn.execute("DELETE FROM sentences WHERE video_id=?", (video_id,))
    conn.execute("DELETE FROM videos WHERE id=?", (video_id,))
    conn.commit()
    conn.close()


def delete_sentence(sentence_id):
    """Delete a single sentence and its associated reviews."""
    conn = get_conn()
    try:
        conn.execute("DELETE FROM reviews WHERE item_type='sentence' AND item_id=?", (sentence_id,))
        conn.execute("DELETE FROM sentences WHERE id=?", (sentence_id,))
        conn.commit()
    finally:
        conn.close()


# ── Sentences ───────────────────────────────────────────

def add_sentences(video_id, sentences):
    """sentences: list of (paragraph_idx, sentence_idx, text[, start_time, end_time])"""
    conn = get_conn()
    data = []
    for item in sentences:
        if len(item) >= 5:
            p, s, t, st, et = item[0], item[1], item[2], item[3], item[4]
        else:
            p, s, t, st, et = item[0], item[1], item[2], None, None
        data.append((video_id, p, s, t, st, et))
    conn.executemany(
        "INSERT INTO sentences (video_id, paragraph_idx, sentence_idx, text, start_time, end_time) VALUES (?,?,?,?,?,?)",
        data
    )
    conn.commit()
    conn.close()


def get_sentences_for_study(video_id=None):
    conn = get_conn()
    if video_id:
        rows = conn.execute("""
            SELECT s.*, v.video_id as youtube_video_id, v.content_type
            FROM sentences s JOIN videos v ON s.video_id = v.id
            WHERE s.video_id=? AND s.status='new'
            ORDER BY s.paragraph_idx, s.sentence_idx
        """, (video_id,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT s.*, v.video_id as youtube_video_id, v.content_type
            FROM sentences s JOIN videos v ON s.video_id = v.id
            WHERE s.status='new'
            ORDER BY s.video_id, s.paragraph_idx, s.sentence_idx
        """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_paragraph_sentences(video_id, paragraph_idx):
    conn = get_conn()
    rows = conn.execute("""
        SELECT s.*, v.video_id as youtube_video_id, v.content_type
        FROM sentences s JOIN videos v ON s.video_id = v.id
        WHERE s.video_id=? AND s.paragraph_idx=?
        ORDER BY s.sentence_idx
    """, (video_id, paragraph_idx)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_paragraphs_for_study(video_id):
    conn = get_conn()
    rows = conn.execute("""
        SELECT DISTINCT paragraph_idx FROM sentences
        WHERE video_id=? AND status='new'
        ORDER BY paragraph_idx
    """, (video_id,)).fetchall()
    conn.close()
    return [r["paragraph_idx"] for r in rows]


def mark_sentence(sentence_id, status):
    """status: 'known' or 'unknown'"""
    conn = get_conn()
    conn.execute("UPDATE sentences SET status=? WHERE id=?", (status, sentence_id))
    conn.commit()
    conn.close()


def get_unknown_sentences():
    conn = get_conn()
    rows = conn.execute("""
        SELECT s.*, v.title as video_title, v.video_id as youtube_video_id, v.content_type,
               COALESCE(r.level, 0) as review_level,
               r.next_review
        FROM sentences s
        JOIN videos v ON s.video_id = v.id
        LEFT JOIN reviews r ON r.item_id = s.id AND r.item_type = 'sentence'
        WHERE s.status='unknown'
        ORDER BY COALESCE(r.level, 0) ASC, COALESCE(r.next_review, '2000-01-01') ASC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_known_sentences():
    conn = get_conn()
    rows = conn.execute("""
        SELECT s.*, v.title as video_title
        FROM sentences s JOIN videos v ON s.video_id = v.id
        WHERE s.status='known' ORDER BY s.id DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_translation(sentence_id):
    conn = get_conn()
    row = conn.execute("SELECT translation FROM sentences WHERE id=?", (sentence_id,)).fetchone()
    conn.close()
    if row and row["translation"]:
        return row["translation"]
    return None


def save_translation(sentence_id, translation):
    conn = get_conn()
    conn.execute("UPDATE sentences SET translation=? WHERE id=?", (translation, sentence_id))
    conn.commit()
    conn.close()


def get_all_sentences_for_video(video_id):
    """Returns ALL sentences for a video regardless of status, with youtube_video_id."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT s.*, v.video_id as youtube_video_id, v.title as video_title, v.content_type
        FROM sentences s JOIN videos v ON s.video_id = v.id
        WHERE s.video_id=?
        ORDER BY s.paragraph_idx, s.sentence_idx
    """, (video_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_sentences(video_id=None):
    conn = get_conn()
    if video_id:
        rows = conn.execute(
            "SELECT * FROM sentences WHERE video_id=? ORDER BY paragraph_idx, sentence_idx",
            (video_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM sentences ORDER BY video_id, paragraph_idx, sentence_idx").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Words ───────────────────────────────────────────────

def add_unknown_word(word):
    word = word.lower().strip()
    if not word:
        return
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO words (word, status) VALUES (?, 'unknown')", (word,))
    conn.commit()
    # Also schedule review
    now = datetime.now().isoformat()
    word_row = conn.execute("SELECT id FROM words WHERE word=?", (word,)).fetchone()
    if word_row:
        conn.execute("""
            INSERT OR IGNORE INTO reviews (item_id, item_type, level, next_review, last_review)
            VALUES (?, 'word', 0, ?, ?)
        """, (word_row["id"], now, now))
        conn.commit()
    conn.close()


def get_unknown_words():
    conn = get_conn()
    rows = conn.execute("""
        SELECT w.*,
               COALESCE(r.level, 0) as review_level,
               r.next_review
        FROM words w
        LEFT JOIN reviews r ON r.item_id = w.id AND r.item_type = 'word'
        WHERE w.status='unknown'
        ORDER BY COALESCE(r.level, 0) ASC, COALESCE(r.next_review, '2000-01-01') ASC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_known_words():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM words WHERE status IN ('known','mastered') ORDER BY word").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_word(word_id, status):
    conn = get_conn()
    conn.execute("UPDATE words SET status=? WHERE id=?", (status, word_id))
    conn.commit()
    conn.close()


# ── Reviews (Spaced Repetition) ─────────────────────────

def schedule_review(item_id, item_type, level=0):
    from srs import get_next_review_time
    now = datetime.now()
    next_review = get_next_review_time(level, now)
    conn = get_conn()
    conn.execute("""
        INSERT INTO reviews (item_id, item_type, level, next_review, last_review, streak)
        VALUES (?, ?, ?, ?, ?, 0)
        ON CONFLICT(item_id, item_type) DO UPDATE SET
            level=excluded.level, next_review=excluded.next_review,
            last_review=excluded.last_review, streak=0
    """, (item_id, item_type, level, next_review.isoformat(), now.isoformat()))
    conn.commit()
    conn.close()


def get_due_reviews(item_type=None):
    now = datetime.now().isoformat()
    conn = get_conn()
    if item_type == "sentence":
        rows = conn.execute("""
            SELECT r.*, s.text, s.video_id, s.start_time, s.end_time,
                   v.title as video_title, v.video_id as youtube_video_id, v.content_type
            FROM reviews r
            JOIN sentences s ON r.item_id = s.id
            JOIN videos v ON s.video_id = v.id
            WHERE r.item_type='sentence' AND r.next_review <= ? AND r.level < 7
            ORDER BY r.next_review
        """, (now,)).fetchall()
    elif item_type == "word":
        rows = conn.execute("""
            SELECT r.*, w.word as text
            FROM reviews r JOIN words w ON r.item_id = w.id
            WHERE r.item_type='word' AND r.next_review <= ? AND r.level < 7
            ORDER BY r.next_review
        """, (now,)).fetchall()
    else:
        rows_s = conn.execute("""
            SELECT r.*, s.text, s.start_time, s.end_time,
                   'sentence' as display_type, v.title as video_title,
                   v.video_id as youtube_video_id, v.content_type
            FROM reviews r
            JOIN sentences s ON r.item_id = s.id
            JOIN videos v ON s.video_id = v.id
            WHERE r.item_type='sentence' AND r.next_review <= ? AND r.level < 7
        """, (now,)).fetchall()
        rows_w = conn.execute("""
            SELECT r.*, w.word as text, 'word' as display_type,
                   '' as video_title, '' as youtube_video_id
            FROM reviews r JOIN words w ON r.item_id = w.id
            WHERE r.item_type='word' AND r.next_review <= ? AND r.level < 7
        """, (now,)).fetchall()
        rows = list(rows_s) + list(rows_w)
    conn.close()
    return [dict(r) for r in rows]


def process_review(item_id, item_type, correct):
    from srs import get_next_review_time
    conn = get_conn()
    review = conn.execute(
        "SELECT * FROM reviews WHERE item_id=? AND item_type=?",
        (item_id, item_type)
    ).fetchone()

    now = datetime.now()
    if correct:
        new_level = (review["level"] + 1) if review else 1
        new_streak = (review["streak"] + 1) if review else 1
    else:
        new_level = 0
        new_streak = 0
        # Mark sentence/word as unknown again
        if item_type == "sentence":
            conn.execute("UPDATE sentences SET status='unknown' WHERE id=?", (item_id,))
        elif item_type == "word":
            conn.execute("UPDATE words SET status='unknown' WHERE id=?", (item_id,))

    next_review = get_next_review_time(new_level, now)

    if new_level >= 7:
        # Mastered! Remove from review
        if item_type == "sentence":
            conn.execute("UPDATE sentences SET status='mastered' WHERE id=?", (item_id,))
        elif item_type == "word":
            conn.execute("UPDATE words SET status='mastered' WHERE id=?", (item_id,))

    conn.execute("""
        INSERT INTO reviews (item_id, item_type, level, next_review, last_review, streak)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(item_id, item_type) DO UPDATE SET
            level=?, next_review=?, last_review=?, streak=?
    """, (item_id, item_type, new_level, next_review.isoformat(), now.isoformat(), new_streak,
          new_level, next_review.isoformat(), now.isoformat(), new_streak))
    conn.commit()
    conn.close()
    return {"level": new_level, "streak": new_streak, "next_review": next_review.isoformat()}


# ── Statistics ──────────────────────────────────────────

def get_stats():
    conn = get_conn()
    total_sentences = conn.execute("SELECT COUNT(*) as c FROM sentences").fetchone()["c"]
    known_sentences = conn.execute("SELECT COUNT(*) as c FROM sentences WHERE status='known'").fetchone()["c"]
    unknown_sentences = conn.execute("SELECT COUNT(*) as c FROM sentences WHERE status='unknown'").fetchone()["c"]
    mastered_sentences = conn.execute("SELECT COUNT(*) as c FROM sentences WHERE status='mastered'").fetchone()["c"]
    new_sentences = conn.execute("SELECT COUNT(*) as c FROM sentences WHERE status='new'").fetchone()["c"]

    total_words = conn.execute("SELECT COUNT(*) as c FROM words").fetchone()["c"]
    known_words = conn.execute("SELECT COUNT(*) as c FROM words WHERE status IN ('known','mastered')").fetchone()["c"]
    unknown_words = conn.execute("SELECT COUNT(*) as c FROM words WHERE status='unknown'").fetchone()["c"]

    due_reviews = conn.execute(
        "SELECT COUNT(*) as c FROM reviews WHERE next_review <= datetime('now') AND level < 7"
    ).fetchone()["c"]

    total_videos = conn.execute("SELECT COUNT(*) as c FROM videos").fetchone()["c"]

    sentence_due = conn.execute(
        "SELECT COUNT(*) as c FROM reviews WHERE item_type='sentence' AND next_review <= datetime('now') AND level < 7"
    ).fetchone()["c"]
    word_due = conn.execute(
        "SELECT COUNT(*) as c FROM reviews WHERE item_type='word' AND next_review <= datetime('now') AND level < 7"
    ).fetchone()["c"]

    conn.close()
    return {
        "total_videos": total_videos,
        "total_sentences": total_sentences,
        "known_sentences": known_sentences,
        "unknown_sentences": unknown_sentences,
        "mastered_sentences": mastered_sentences,
        "new_sentences": new_sentences,
        "studied_sentences": known_sentences + unknown_sentences + mastered_sentences,
        "total_words": total_words,
        "known_words": known_words,
        "unknown_words": unknown_words,
        "due_reviews": due_reviews,
        "sentence_due_reviews": sentence_due,
        "word_due_reviews": word_due,
    }


def get_due_review_counts():
    """Returns separate due counts for sentences and words."""
    now = datetime.now().isoformat()
    conn = get_conn()
    sentence_count = conn.execute(
        "SELECT COUNT(*) as c FROM reviews WHERE item_type='sentence' AND next_review <= ? AND level < 7",
        (now,)
    ).fetchone()["c"]
    word_count = conn.execute(
        "SELECT COUNT(*) as c FROM reviews WHERE item_type='word' AND next_review <= ? AND level < 7",
        (now,)
    ).fetchone()["c"]
    conn.close()
    return {"sentence_due": sentence_count, "word_due": word_count}


def get_analytics():
    """Returns analytics data for charts."""
    conn = get_conn()

    # 1. Overall mastery distribution (donut chart)
    mastered = conn.execute("SELECT COUNT(*) as c FROM sentences WHERE status='mastered'").fetchone()["c"]
    known = conn.execute("SELECT COUNT(*) as c FROM sentences WHERE status='known'").fetchone()["c"]
    learning = conn.execute("""
        SELECT COUNT(*) as c FROM sentences s
        JOIN reviews r ON r.item_id = s.id AND r.item_type='sentence'
        WHERE s.status='unknown' AND r.level > 0
    """).fetchone()["c"]
    frequently_wrong = conn.execute("""
        SELECT COUNT(*) as c FROM reviews r
        JOIN sentences s ON r.item_id = s.id AND r.item_type='sentence'
        WHERE r.streak = 0 AND r.level = 0 AND r.last_review IS NOT NULL AND s.status='unknown'
    """).fetchone()["c"]
    total_unknown = conn.execute("SELECT COUNT(*) as c FROM sentences WHERE status='unknown'").fetchone()["c"]
    pure_unknown = max(0, total_unknown - learning - frequently_wrong)

    mastery_distribution = {
        "mastered": mastered,
        "known": known,
        "learning": learning,
        "unknown": pure_unknown,
        "frequently_wrong": frequently_wrong
    }

    # 2. Per-content progress (bar chart)
    content_progress = conn.execute("""
        SELECT v.id, v.title, v.content_type,
               COUNT(s.id) as total,
               SUM(CASE WHEN s.status='mastered' THEN 1 ELSE 0 END) as mastered,
               SUM(CASE WHEN s.status='known' THEN 1 ELSE 0 END) as known,
               SUM(CASE WHEN s.status='unknown' THEN 1 ELSE 0 END) as unknown,
               SUM(CASE WHEN s.status='new' THEN 1 ELSE 0 END) as new_count
        FROM videos v
        LEFT JOIN sentences s ON v.id = s.video_id
        GROUP BY v.id
        ORDER BY v.created_at DESC
    """).fetchall()
    content_progress = [dict(r) for r in content_progress]

    # 3. Learning progress over time (line chart - last 30 days)
    daily_progress = conn.execute("""
        SELECT DATE(last_review) as day, COUNT(*) as count
        FROM reviews
        WHERE last_review IS NOT NULL
          AND DATE(last_review) >= DATE('now', '-30 days')
        GROUP BY DATE(last_review)
        ORDER BY day
    """).fetchall()
    daily_progress = [dict(r) for r in daily_progress]

    # 4. SRS level distribution (stacked bar chart)
    srs_distribution = conn.execute("""
        SELECT r.item_type, r.level, COUNT(*) as count
        FROM reviews r
        WHERE r.level < 7
        GROUP BY r.item_type, r.level
        ORDER BY r.item_type, r.level
    """).fetchall()
    srs_distribution = [dict(r) for r in srs_distribution]

    # 5. Word mastery distribution
    word_mastered = conn.execute("SELECT COUNT(*) as c FROM words WHERE status='mastered'").fetchone()["c"]
    word_known = conn.execute("SELECT COUNT(*) as c FROM words WHERE status='known'").fetchone()["c"]
    word_unknown = conn.execute("SELECT COUNT(*) as c FROM words WHERE status='unknown'").fetchone()["c"]
    word_freq_wrong = conn.execute("""
        SELECT COUNT(*) as c FROM reviews r
        JOIN words w ON r.item_id = w.id AND r.item_type='word'
        WHERE r.streak = 0 AND r.level = 0 AND r.last_review IS NOT NULL AND w.status='unknown'
    """).fetchone()["c"]

    word_distribution = {
        "mastered": word_mastered,
        "known": word_known,
        "unknown": max(0, word_unknown - word_freq_wrong),
        "frequently_wrong": word_freq_wrong
    }

    conn.close()

    return {
        "mastery_distribution": mastery_distribution,
        "word_distribution": word_distribution,
        "content_progress": content_progress,
        "daily_progress": daily_progress,
        "srs_distribution": srs_distribution
    }


# ── Playlists ──────────────────────────────────────────

def add_playlist(playlist_id, title, url, category_id=None):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO playlists (playlist_id, title, url, category_id) VALUES (?,?,?,?)",
            (playlist_id, title, url, category_id)
        )
        conn.commit()
        row = conn.execute("SELECT id FROM playlists WHERE playlist_id=?", (playlist_id,)).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def get_playlists():
    conn = get_conn()
    rows = conn.execute("""
        SELECT p.*, c.name as category_name,
               (SELECT COUNT(*) FROM playlist_videos pv WHERE pv.playlist_id = p.id) as video_count
        FROM playlists p
        LEFT JOIN categories c ON p.category_id = c.id
        ORDER BY p.created_at DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_playlist(pl_id):
    conn = get_conn()
    row = conn.execute("""
        SELECT p.*, c.name as category_name
        FROM playlists p
        LEFT JOIN categories c ON p.category_id = c.id
        WHERE p.id=?
    """, (pl_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_playlist(pl_id):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM playlists WHERE id=?", (pl_id,))
        conn.commit()
    finally:
        conn.close()


def update_playlist(pl_id, **kwargs):
    conn = get_conn()
    try:
        for key, val in kwargs.items():
            if key in ('enabled', 'category_id', 'title'):
                conn.execute(f"UPDATE playlists SET {key}=? WHERE id=?", (val, pl_id))
        conn.commit()
    finally:
        conn.close()


def update_playlist_last_checked(pl_id, last_checked):
    conn = get_conn()
    try:
        conn.execute("UPDATE playlists SET last_checked=? WHERE id=?", (last_checked, pl_id))
        conn.commit()
    finally:
        conn.close()


def get_enabled_playlists():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM playlists WHERE enabled=1").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_playlist_video(pl_id, video_db_id, youtube_video_id):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO playlist_videos (playlist_id, video_db_id, youtube_video_id) VALUES (?,?,?)",
            (pl_id, video_db_id, youtube_video_id)
        )
        conn.commit()
    finally:
        conn.close()


def get_playlist_video_ids(pl_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT youtube_video_id FROM playlist_videos WHERE playlist_id=?", (pl_id,)
    ).fetchall()
    conn.close()
    return {r["youtube_video_id"] for r in rows}
