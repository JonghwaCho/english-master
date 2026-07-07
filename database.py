import sqlite3
import os
import hashlib
import threading
import calendar
from datetime import datetime, timedelta

# ── 현재 요청 사용자 컨텍스트 (멀티유저 격리) ──────────────
# 요청마다 server.py before_request에서 set_current_user()로 설정하고,
# 데이터 함수들은 _uid()로 현재 사용자를 읽어 자동으로 필터/기록한다.
# 백그라운드 워커는 요청 컨텍스트가 없으므로, 전역 캐시(word_meanings/ai_cache)만
# 다루거나, 플레이리스트 소유자를 명시적으로 set_current_user() 한다.
_ctx = threading.local()


def set_current_user(user_id):
    _ctx.user_id = user_id


def clear_current_user():
    _ctx.user_id = None


def _uid():
    return getattr(_ctx, "user_id", None)

# 데이터 저장 경로: 로컬은 프로젝트 내 data/, 클라우드 배포 시 DATA_DIR 환경변수로
# 영구 볼륨(예: /data)을 지정한다.
DATA_DIR = os.environ.get(
    "DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
)
DB_PATH = os.path.join(DATA_DIR, "english_master.db")


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

    # 학습 활동 로그 테이블 (일별 통계용, 과거 기록 유지)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS study_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            item_type TEXT NOT NULL CHECK(item_type IN ('sentence','word')),
            action TEXT NOT NULL,
            correct INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # 단어 뜻 캐싱 테이블
    conn.execute("""
        CREATE TABLE IF NOT EXISTS word_meanings (
            word TEXT PRIMARY KEY NOT NULL,
            meaning TEXT NOT NULL,
            source TEXT DEFAULT 'dict',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # 단어-영상 연결 테이블 (어떤 영상에서 단어를 저장했는지 추적)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS word_video_link (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            word_id INTEGER NOT NULL,
            video_id INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(word_id, video_id),
            FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE,
            FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE
        )
    """)

    # AI 결과 캐싱 테이블 (직독직해, 유사문장, 문법, 단어설명)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sentence_text TEXT NOT NULL,
            action TEXT NOT NULL,
            result TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(sentence_text, action)
        )
    """)

    # 기존 reviews 데이터를 study_log로 마이그레이션 (최초 1회)
    migrated = conn.execute("SELECT COUNT(*) as c FROM study_log").fetchone()["c"]
    if migrated == 0:
        conn.execute("""
            INSERT INTO study_log (item_id, item_type, action, correct, created_at)
            SELECT item_id, item_type, 'review',
                   CASE WHEN level > 0 THEN 1 ELSE 0 END,
                   last_review
            FROM reviews
            WHERE last_review IS NOT NULL
        """)
        conn.commit()

    # sentences 테이블에 unknown_count 컬럼 추가 (없을 경우)
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(sentences)").fetchall()]
    if "unknown_count" not in cols:
        conn.execute("ALTER TABLE sentences ADD COLUMN unknown_count INTEGER DEFAULT 0")
        # 기존 unknown 상태 문장들은 최소 1로 초기화
        conn.execute("UPDATE sentences SET unknown_count = 1 WHERE status = 'unknown' AND unknown_count = 0")
        conn.commit()

    # ── 사용자 인증 테이블 (멀티유저) ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT,           -- 이메일 가입 시 사용. OAuth 전용 계정은 NULL
            name TEXT DEFAULT '',
            google_id TEXT UNIQUE,        -- 구글 로그인 연동 시
            ai_provider TEXT DEFAULT '',  -- 사용자별 AI 설정 (없으면 서버 공용 키로 폴백)
            ai_key TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # ── Step 2: 멀티유저 데이터 격리 마이그레이션 ──
    _migrate_multiuser(conn)

    # ── 요금제·할당량(billing) 스키마 ──
    _migrate_billing(conn)

    # ── 이메일 인증 + 앱 설정(app_settings) 스키마 ──
    _migrate_email(conn)

    conn.commit()
    conn.close()


# 기본 요금제 정의 (최초 1회 시드). 이후 값은 관리자 도구에서 변경 가능.
# content_limit: 주기당 신규 콘텐츠 등록 허용 수
# period_type: 'lifetime'(free, 평생 누적) | 'monthly'(구독 시작일 기준 매월 리셋)
DEFAULT_PLANS = [
    ("free",    "무료",     0,     1, "lifetime", 0),
    ("basic",   "베이직",   3000,  5, "monthly",  1),
    ("premium", "프리미엄", 5000, 10, "monthly",  2),
    ("max",     "맥스",     7000, 20, "monthly",  3),
]


def _migrate_billing(conn):
    """요금제(plans) + 콘텐츠 등록 원장(content_registrations) 스키마를 준비한다.
    - users: plan_code / plan_started_at 컬럼 추가 (없으면)
    - plans: 요금제 정의 (관리자 편집 가능). 비어 있으면 기본값 시드
    - content_registrations: 콘텐츠 '학습 가능 등록' 이벤트 원장.
      영상을 삭제해도 원장은 남으므로 '삭제해도 카운트 복구 안 됨'을 보장한다.
    """
    ucols = [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "plan_code" not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN plan_code TEXT DEFAULT 'free'")
    if "plan_started_at" not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN plan_started_at TEXT")
    # 기존 사용자: 구독 시작일이 없으면 가입일로 채운다
    conn.execute("UPDATE users SET plan_started_at = created_at "
                 "WHERE plan_started_at IS NULL OR plan_started_at = ''")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS plans (
            code         TEXT PRIMARY KEY,        -- free/basic/premium/max
            name         TEXT NOT NULL,
            price        INTEGER NOT NULL DEFAULT 0,   -- 원/월
            content_limit INTEGER NOT NULL,            -- 주기당 등록 허용 수
            period_type  TEXT NOT NULL DEFAULT 'monthly',  -- 'monthly' | 'lifetime'
            sort_order   INTEGER NOT NULL DEFAULT 0,
            updated_at   TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS content_registrations (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            video_id     INTEGER,                  -- 영상 삭제돼도 원장은 보존
            registered_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, video_id)
        );
    """)

    if conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO plans (code, name, price, content_limit, period_type, sort_order) "
            "VALUES (?,?,?,?,?,?)", DEFAULT_PLANS)

    # 최초 도입 시: 이미 등록된 기존 콘텐츠를 원장에 백필한다(등록일=영상 생성일).
    # 이렇게 해야 기존 사용자의 현재 사용량이 정확히 반영된다.
    if conn.execute("SELECT COUNT(*) FROM content_registrations").fetchone()[0] == 0:
        conn.execute("""
            INSERT OR IGNORE INTO content_registrations (user_id, video_id, registered_at)
            SELECT user_id, id, created_at FROM videos WHERE user_id IS NOT NULL
        """)


# 이메일(SMTP) 설정 키 — app_settings 테이블에 저장되며 관리자 도구에서 편집한다.
EMAIL_SETTING_KEYS = ("email_enabled", "smtp_host", "smtp_port", "smtp_security",
                      "smtp_user", "smtp_password", "email_from", "email_from_name")


def _migrate_email(conn):
    """이메일 인증 컬럼 + 앱 설정(app_settings) 스키마를 준비한다.
    - users: email_verified / verify_token / verify_sent_at
    - 기존 사용자는 email_verified=1로 처리(락아웃 방지)
    - app_settings: 범용 키-값. 이메일(SMTP) 설정을 저장하며 관리자가 편집
    - 최초 시드는 환경변수(SMTP_HOST 등)가 있으면 반영, 없으면 빈 값
    """
    ucols = [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "email_verified" not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER DEFAULT 0")
        conn.execute("ALTER TABLE users ADD COLUMN verify_token TEXT")
        conn.execute("ALTER TABLE users ADD COLUMN verify_sent_at TEXT")
        # 기존 사용자/구글 계정은 인증된 것으로 간주(마이그레이션으로 락아웃되지 않도록)
        conn.execute("UPDATE users SET email_verified=1")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)

    # 이메일 설정 최초 시드(환경변수가 있으면 그것으로, 없으면 기본/빈 값)
    have = conn.execute(
        "SELECT COUNT(*) FROM app_settings WHERE key LIKE 'email_%' OR key LIKE 'smtp_%'"
    ).fetchone()[0]
    if have == 0:
        seed = {
            "email_enabled":   "1" if os.environ.get("SMTP_HOST") else "0",
            "smtp_host":       os.environ.get("SMTP_HOST", ""),
            "smtp_port":       os.environ.get("SMTP_PORT", "587"),
            "smtp_security":   os.environ.get("SMTP_SECURITY", "tls"),  # tls | ssl | none
            "smtp_user":       os.environ.get("SMTP_USER", ""),
            "smtp_password":   os.environ.get("SMTP_PASSWORD", ""),
            "email_from":      os.environ.get("EMAIL_FROM", ""),
            "email_from_name": os.environ.get("EMAIL_FROM_NAME", "English Master"),
        }
        conn.executemany("INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
                         list(seed.items()))


def _migrate_multiuser(conn):
    """user_id 컬럼이 없으면 사용자 격리 스키마로 마이그레이션한다.
    - videos/words/categories/playlists: user_id + 복합 UNIQUE로 테이블 재구축(id 보존)
    - sentences/reviews/study_log: user_id 컬럼 추가
    기존 데이터의 user_id는 NULL로 두고, 첫 가입자가 claim_orphan_data()로 인수한다.
    """
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(videos)").fetchall()]
    if "user_id" in cols:
        return  # 이미 마이그레이션됨

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript("""
        -- videos 재구축: UNIQUE(url) → UNIQUE(user_id, url)
        CREATE TABLE videos_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            url TEXT NOT NULL,
            video_id TEXT,
            title TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            category_id INTEGER,
            content_type TEXT DEFAULT 'youtube',
            source_text TEXT DEFAULT '',
            UNIQUE(user_id, url)
        );
        INSERT INTO videos_new (id, url, video_id, title, created_at, category_id, content_type, source_text)
            SELECT id, url, video_id, title, created_at, category_id, content_type, source_text FROM videos;
        DROP TABLE videos;
        ALTER TABLE videos_new RENAME TO videos;

        -- categories 재구축: UNIQUE(name) → UNIQUE(user_id, name)
        CREATE TABLE categories_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, name)
        );
        INSERT INTO categories_new (id, name, created_at)
            SELECT id, name, created_at FROM categories;
        DROP TABLE categories;
        ALTER TABLE categories_new RENAME TO categories;

        -- words 재구축: UNIQUE(word) → UNIQUE(user_id, word)
        CREATE TABLE words_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            word TEXT NOT NULL,
            status TEXT DEFAULT 'unknown',
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, word)
        );
        INSERT INTO words_new (id, word, status, created_at)
            SELECT id, word, status, created_at FROM words;
        DROP TABLE words;
        ALTER TABLE words_new RENAME TO words;

        -- playlists 재구축: UNIQUE(playlist_id) → UNIQUE(user_id, playlist_id)
        CREATE TABLE playlists_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            playlist_id TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            category_id INTEGER,
            enabled INTEGER DEFAULT 1,
            last_checked TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, playlist_id)
        );
        INSERT INTO playlists_new (id, playlist_id, title, url, category_id, enabled, last_checked, created_at)
            SELECT id, playlist_id, title, url, category_id, enabled, last_checked, created_at FROM playlists;
        DROP TABLE playlists;
        ALTER TABLE playlists_new RENAME TO playlists;
    """)

    # sentences/reviews/study_log: user_id 컬럼 추가
    for tbl in ("sentences", "reviews", "study_log"):
        try:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN user_id INTEGER")
        except Exception:
            pass

    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()


def claim_orphan_data(user_id):
    """소유자 없는(user_id IS NULL) 기존 데이터를 지정 사용자에게 귀속시킨다.
    첫 가입자가 기존 단일 사용자 데이터를 인수할 때 호출한다."""
    conn = get_conn()
    for tbl in ("videos", "categories", "playlists", "words", "sentences", "reviews", "study_log"):
        conn.execute(f"UPDATE {tbl} SET user_id=? WHERE user_id IS NULL", (user_id,))
    # 인수한 콘텐츠를 할당량 원장에도 반영(등록일=영상 생성일). 없으면 무시.
    conn.execute("""
        INSERT OR IGNORE INTO content_registrations (user_id, video_id, registered_at)
        SELECT user_id, id, created_at FROM videos WHERE user_id=?
    """, (user_id,))
    conn.commit()
    conn.close()


# ── Users (인증) ────────────────────────────────────────

def create_user(email, password_hash=None, name="", google_id=None):
    """새 사용자 생성. 성공 시 dict 반환, 이메일 중복 시 None."""
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, name, google_id) VALUES (?, ?, ?, ?)",
            (email.lower().strip(), password_hash, name, google_id),
        )
        conn.commit()
        uid = cur.lastrowid
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()
    return get_user_by_id(uid)


def get_user_by_id(user_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_email(email):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email.lower().strip(),)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_google_id(google_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE google_id = ?", (google_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def link_google_id(user_id, google_id):
    conn = get_conn()
    conn.execute("UPDATE users SET google_id = ? WHERE id = ?", (google_id, user_id))
    conn.commit()
    conn.close()


def update_user_ai_settings(user_id, provider, ai_key):
    conn = get_conn()
    conn.execute(
        "UPDATE users SET ai_provider = ?, ai_key = ? WHERE id = ?",
        (provider, ai_key, user_id),
    )
    conn.commit()
    conn.close()


def count_users():
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    conn.close()
    return n


def log_study_activity(item_id, item_type, action, correct=None):
    """학습/복습 활동을 로그에 기록 (action: 'study', 'review', 'bulk_study')"""
    conn = get_conn()
    conn.execute(
        "INSERT INTO study_log (item_id, item_type, action, correct, user_id) VALUES (?, ?, ?, ?, ?)",
        (item_id, item_type, action, 1 if correct else (0 if correct is False else None), _uid())
    )
    conn.commit()
    conn.close()


# ── Categories ──────────────────────────────────────────

def get_categories():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM categories WHERE user_id=? ORDER BY name", (_uid(),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_category(name):
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO categories (name, user_id) VALUES (?, ?)", (name, _uid()))
    conn.commit()
    row = conn.execute("SELECT id FROM categories WHERE name=? AND user_id=?", (name, _uid())).fetchone()
    conn.close()
    return row["id"] if row else None


def delete_category(category_id):
    conn = get_conn()
    conn.execute("UPDATE videos SET category_id=NULL WHERE category_id=? AND user_id=?", (category_id, _uid()))
    conn.execute("DELETE FROM categories WHERE id=? AND user_id=?", (category_id, _uid()))
    conn.commit()
    conn.close()


def rename_category(category_id, new_name):
    conn = get_conn()
    conn.execute("UPDATE categories SET name=? WHERE id=? AND user_id=?", (new_name, category_id, _uid()))
    conn.commit()
    conn.close()


def set_video_category(video_id, category_id):
    conn = get_conn()
    conn.execute("UPDATE videos SET category_id=? WHERE id=? AND user_id=?", (category_id, video_id, _uid()))
    conn.commit()
    conn.close()


# ── Videos ──────────────────────────────────────────────

def add_video(url, video_id, title):
    conn = get_conn()
    try:
        conn.execute("INSERT OR IGNORE INTO videos (url, video_id, title, user_id) VALUES (?,?,?,?)",
                      (url, video_id, title, _uid()))
        conn.commit()
        row = conn.execute("SELECT id FROM videos WHERE url=? AND user_id=?", (url, _uid())).fetchone()
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
            "INSERT OR IGNORE INTO videos (url, video_id, title, content_type, source_text, category_id, user_id) VALUES (?,?,?,?,?,?,?)",
            (url, None, title, content_type, text, category_id, _uid())
        )
        conn.commit()
        row = conn.execute("SELECT id FROM videos WHERE url=? AND user_id=?", (url, _uid())).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def get_video(video_id):
    """Get a single video/content by ID (현재 사용자 소유만)."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM videos WHERE id=? AND user_id=?", (video_id, _uid())).fetchone()
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
        WHERE v.user_id=?
    """
    params = [_uid()]
    if category_id is not None:
        if category_id == 0:
            query += " AND v.category_id IS NULL"
        else:
            query += " AND v.category_id=?"
            params.append(category_id)
    query += " GROUP BY v.id ORDER BY v.created_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_video(video_id):
    conn = get_conn()
    # 소유 확인: 내 영상이 아니면 아무것도 삭제하지 않음
    owned = conn.execute("SELECT 1 FROM videos WHERE id=? AND user_id=?", (video_id, _uid())).fetchone()
    if not owned:
        conn.close()
        return
    conn.execute("DELETE FROM reviews WHERE item_type='sentence' AND item_id IN (SELECT id FROM sentences WHERE video_id=?)", (video_id,))
    conn.execute("DELETE FROM sentences WHERE video_id=?", (video_id,))
    conn.execute("DELETE FROM videos WHERE id=? AND user_id=?", (video_id, _uid()))
    conn.commit()
    conn.close()


def delete_sentence(sentence_id):
    """Delete a single sentence and its associated reviews (현재 사용자 소유만)."""
    conn = get_conn()
    try:
        owned = conn.execute("SELECT 1 FROM sentences WHERE id=? AND user_id=?", (sentence_id, _uid())).fetchone()
        if not owned:
            return
        conn.execute("DELETE FROM reviews WHERE item_type='sentence' AND item_id=?", (sentence_id,))
        conn.execute("DELETE FROM sentences WHERE id=?", (sentence_id,))
        conn.commit()
    finally:
        conn.close()


# ── Sentences ───────────────────────────────────────────

def add_sentences(video_id, sentences):
    """sentences: list of (paragraph_idx, sentence_idx, text[, start_time, end_time])"""
    conn = get_conn()
    uid = _uid()
    data = []
    for item in sentences:
        if len(item) >= 5:
            p, s, t, st, et = item[0], item[1], item[2], item[3], item[4]
        else:
            p, s, t, st, et = item[0], item[1], item[2], None, None
        data.append((video_id, p, s, t, st, et, uid))
    conn.executemany(
        "INSERT INTO sentences (video_id, paragraph_idx, sentence_idx, text, start_time, end_time, user_id) VALUES (?,?,?,?,?,?,?)",
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
            WHERE s.video_id=? AND s.status='new' AND s.user_id=?
            ORDER BY s.paragraph_idx, s.sentence_idx
        """, (video_id, _uid())).fetchall()
    else:
        rows = conn.execute("""
            SELECT s.*, v.video_id as youtube_video_id, v.content_type
            FROM sentences s JOIN videos v ON s.video_id = v.id
            WHERE s.status='new' AND s.user_id=?
            ORDER BY s.video_id, s.paragraph_idx, s.sentence_idx
        """, (_uid(),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_paragraph_sentences(video_id, paragraph_idx):
    conn = get_conn()
    rows = conn.execute("""
        SELECT s.*, v.video_id as youtube_video_id, v.content_type
        FROM sentences s JOIN videos v ON s.video_id = v.id
        WHERE s.video_id=? AND s.paragraph_idx=? AND s.user_id=?
        ORDER BY s.sentence_idx
    """, (video_id, paragraph_idx, _uid())).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_paragraphs_for_study(video_id):
    conn = get_conn()
    rows = conn.execute("""
        SELECT DISTINCT paragraph_idx FROM sentences
        WHERE video_id=? AND status='new' AND user_id=?
        ORDER BY paragraph_idx
    """, (video_id, _uid())).fetchall()
    conn.close()
    return [r["paragraph_idx"] for r in rows]


def mark_sentence(sentence_id, status):
    """status: 'known' or 'unknown'
    When marked as 'unknown', also increment unknown_count to track repeated failures."""
    conn = get_conn()
    if status == 'unknown':
        conn.execute(
            "UPDATE sentences SET status=?, unknown_count = COALESCE(unknown_count, 0) + 1 WHERE id=? AND user_id=?",
            (status, sentence_id, _uid())
        )
    else:
        conn.execute("UPDATE sentences SET status=? WHERE id=? AND user_id=?", (status, sentence_id, _uid()))
    conn.commit()
    conn.close()
    # 학습 활동 로그 기록
    log_study_activity(sentence_id, 'sentence', 'study', status == 'known')


def reset_unknown_sentences(video_id):
    """DEPRECATED: No-op.
    Previously reset unknown sentences to 'new' and deleted their reviews,
    but this caused unknown sentences to disappear from the "모르는 문장" page.
    The UI now resets the visual 'marked-unknown' state on the fullplay screen only,
    while preserving the underlying data and unknown_count history.
    """
    return


def get_unknown_sentences(video_id=None):
    conn = get_conn()
    sql = """
        SELECT s.*, v.title as video_title, v.video_id as youtube_video_id, v.content_type,
               COALESCE(r.level, 0) as review_level,
               r.next_review
        FROM sentences s
        JOIN videos v ON s.video_id = v.id
        LEFT JOIN reviews r ON r.item_id = s.id AND r.item_type = 'sentence'
        WHERE s.status='unknown' AND s.user_id=?
    """
    params = [_uid()]
    if video_id:
        sql += " AND s.video_id = ?"
        params.append(video_id)
    # Sort: frequently-missed first (high unknown_count), then by review level/next_review
    sql += " ORDER BY COALESCE(s.unknown_count, 0) DESC, COALESCE(r.level, 0) ASC, COALESCE(r.next_review, '2000-01-01') ASC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_known_sentences():
    conn = get_conn()
    rows = conn.execute("""
        SELECT s.*, v.title as video_title
        FROM sentences s JOIN videos v ON s.video_id = v.id
        WHERE s.status='known' AND s.user_id=? ORDER BY s.id DESC
    """, (_uid(),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_translation(sentence_id):
    conn = get_conn()
    row = conn.execute("SELECT translation FROM sentences WHERE id=? AND user_id=?", (sentence_id, _uid())).fetchone()
    conn.close()
    if row and row["translation"]:
        return row["translation"]
    return None


def save_translation(sentence_id, translation):
    conn = get_conn()
    conn.execute("UPDATE sentences SET translation=? WHERE id=? AND user_id=?", (translation, sentence_id, _uid()))
    conn.commit()
    conn.close()


def get_all_sentences_for_video(video_id):
    """Returns ALL sentences for a video regardless of status, with youtube_video_id."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT s.*, v.video_id as youtube_video_id, v.title as video_title, v.content_type
        FROM sentences s JOIN videos v ON s.video_id = v.id
        WHERE s.video_id=? AND s.user_id=?
        ORDER BY s.paragraph_idx, s.sentence_idx
    """, (video_id, _uid())).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_sentences(video_id=None):
    conn = get_conn()
    if video_id:
        rows = conn.execute(
            "SELECT * FROM sentences WHERE video_id=? AND user_id=? ORDER BY paragraph_idx, sentence_idx",
            (video_id, _uid())
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM sentences WHERE user_id=? ORDER BY video_id, paragraph_idx, sentence_idx", (_uid(),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Words ───────────────────────────────────────────────

def add_unknown_word(word):
    word = word.lower().strip()
    if not word:
        return
    conn = get_conn()
    uid = _uid()
    conn.execute("INSERT OR IGNORE INTO words (word, status, user_id) VALUES (?, 'unknown', ?)", (word, uid))
    conn.commit()
    # Also schedule review
    now = datetime.now().isoformat()
    word_row = conn.execute("SELECT id FROM words WHERE word=? AND user_id=?", (word, uid)).fetchone()
    if word_row:
        conn.execute("""
            INSERT OR IGNORE INTO reviews (item_id, item_type, level, next_review, last_review, user_id)
            VALUES (?, 'word', 0, ?, ?, ?)
        """, (word_row["id"], now, now, uid))
        conn.commit()
    conn.close()


def get_unknown_words(video_id=None):
    conn = get_conn()
    if video_id:
        rows = conn.execute("""
            SELECT DISTINCT w.*,
                   COALESCE(r.level, 0) as review_level,
                   r.next_review
            FROM words w
            JOIN word_video_link wl ON wl.word_id = w.id
            LEFT JOIN reviews r ON r.item_id = w.id AND r.item_type = 'word'
            WHERE w.status='unknown' AND wl.video_id = ? AND w.user_id=?
            ORDER BY COALESCE(r.level, 0) ASC, COALESCE(r.next_review, '2000-01-01') ASC
        """, [video_id, _uid()]).fetchall()
    else:
        rows = conn.execute("""
            SELECT w.*,
                   COALESCE(r.level, 0) as review_level,
                   r.next_review
            FROM words w
            LEFT JOIN reviews r ON r.item_id = w.id AND r.item_type = 'word'
            WHERE w.status='unknown' AND w.user_id=?
            ORDER BY COALESCE(r.level, 0) ASC, COALESCE(r.next_review, '2000-01-01') ASC
        """, (_uid(),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_known_words():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM words WHERE status IN ('known','mastered') AND user_id=? ORDER BY word", (_uid(),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_word(word_id, status):
    conn = get_conn()
    conn.execute("UPDATE words SET status=? WHERE id=? AND user_id=?", (status, word_id, _uid()))
    conn.commit()
    conn.close()


def delete_word(word_id):
    """모르는 단어 목록 + 복습 목록에서 영구 삭제 (현재 사용자 소유만)"""
    conn = get_conn()
    owned = conn.execute("SELECT 1 FROM words WHERE id=? AND user_id=?", (word_id, _uid())).fetchone()
    if not owned:
        conn.close()
        return
    conn.execute("DELETE FROM reviews WHERE item_type='word' AND item_id=?", (word_id,))
    conn.execute("DELETE FROM words WHERE id=?", (word_id,))
    conn.commit()
    conn.close()


# ── Reviews (Spaced Repetition / SRS) ───────────────────
#
# SRS (Spaced Repetition System) - 에빙하우스 망각곡선 기반 간격 반복 학습
#
# Level 0: 즉시 복습 (방금 학습했거나 틀린 항목)
# Level 1: 1시간 후 복습
# Level 2: 1일(24시간) 후 복습
# Level 3: 2일(48시간) 후 복습
# Level 4: 4일(96시간) 후 복습
# Level 5: 7일(168시간) 후 복습
# Level 6: 15일(360시간) 후 복습
# Level 7: 30일(720시간) 후 → 완전 습득(Mastered)!
#
# 규칙:
# - 정답 시: level + 1 (다음 단계로 승급, 복습 간격 증가)
# - 오답 시: level → 0 (처음부터 다시 시작, streak 리셋)
# - Level 7 도달 시: 완전 습득으로 간주, 더 이상 복습 대기열에 나타나지 않음
#

def schedule_review(item_id, item_type, level=0):
    from srs import get_next_review_time
    now = datetime.now()
    next_review = get_next_review_time(level, now)
    conn = get_conn()
    conn.execute("""
        INSERT INTO reviews (item_id, item_type, level, next_review, last_review, streak, user_id)
        VALUES (?, ?, ?, ?, ?, 0, ?)
        ON CONFLICT(item_id, item_type) DO UPDATE SET
            level=excluded.level, next_review=excluded.next_review,
            last_review=excluded.last_review, streak=0
    """, (item_id, item_type, level, next_review.isoformat(), now.isoformat(), _uid()))
    conn.commit()
    conn.close()


def get_due_reviews(item_type=None, video_id=None):
    now = datetime.now().isoformat()
    uid = _uid()
    conn = get_conn()
    if item_type == "sentence":
        sql = """
            SELECT r.*, s.text, s.video_id, s.start_time, s.end_time,
                   v.title as video_title, v.video_id as youtube_video_id, v.content_type
            FROM reviews r
            JOIN sentences s ON r.item_id = s.id
            JOIN videos v ON s.video_id = v.id
            WHERE r.item_type='sentence' AND r.next_review <= ? AND r.level < 7 AND r.user_id=?
        """
        params = [now, uid]
        if video_id:
            sql += " AND s.video_id = ?"
            params.append(video_id)
        sql += " ORDER BY r.next_review"
        rows = conn.execute(sql, params).fetchall()
    elif item_type == "word":
        sql = """
            SELECT r.*, w.word as text
            FROM reviews r JOIN words w ON r.item_id = w.id
            WHERE r.item_type='word' AND r.next_review <= ? AND r.level < 7 AND r.user_id=?
        """
        params = [now, uid]
        if video_id:
            sql += " AND w.id IN (SELECT wl.word_id FROM word_video_link wl WHERE wl.video_id = ?)"
            params.append(video_id)
        sql += " ORDER BY r.next_review"
        rows = conn.execute(sql, params).fetchall()
    else:
        rows_s = conn.execute("""
            SELECT r.*, s.text, s.start_time, s.end_time,
                   'sentence' as display_type, v.title as video_title,
                   v.video_id as youtube_video_id, v.content_type
            FROM reviews r
            JOIN sentences s ON r.item_id = s.id
            JOIN videos v ON s.video_id = v.id
            WHERE r.item_type='sentence' AND r.next_review <= ? AND r.level < 7 AND r.user_id=?
        """, (now, uid)).fetchall()
        rows_w = conn.execute("""
            SELECT r.*, w.word as text, 'word' as display_type,
                   '' as video_title, '' as youtube_video_id
            FROM reviews r JOIN words w ON r.item_id = w.id
            WHERE r.item_type='word' AND r.next_review <= ? AND r.level < 7 AND r.user_id=?
        """, (now, uid)).fetchall()
        rows = list(rows_s) + list(rows_w)
    conn.close()
    return [dict(r) for r in rows]


def process_review(item_id, item_type, correct):
    from srs import get_next_review_time
    uid = _uid()
    conn = get_conn()
    review = conn.execute(
        "SELECT * FROM reviews WHERE item_id=? AND item_type=? AND user_id=?",
        (item_id, item_type, uid)
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
            conn.execute("UPDATE sentences SET status='unknown' WHERE id=? AND user_id=?", (item_id, uid))
        elif item_type == "word":
            conn.execute("UPDATE words SET status='unknown' WHERE id=? AND user_id=?", (item_id, uid))

    next_review = get_next_review_time(new_level, now)

    if new_level >= 7:
        # Mastered! Remove from review
        if item_type == "sentence":
            conn.execute("UPDATE sentences SET status='mastered' WHERE id=? AND user_id=?", (item_id, uid))
        elif item_type == "word":
            conn.execute("UPDATE words SET status='mastered' WHERE id=? AND user_id=?", (item_id, uid))

    conn.execute("""
        INSERT INTO reviews (item_id, item_type, level, next_review, last_review, streak, user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(item_id, item_type) DO UPDATE SET
            level=?, next_review=?, last_review=?, streak=?
    """, (item_id, item_type, new_level, next_review.isoformat(), now.isoformat(), new_streak, uid,
          new_level, next_review.isoformat(), now.isoformat(), new_streak))
    conn.commit()
    conn.close()

    # 학습 활동 로그 기록
    log_study_activity(item_id, item_type, 'review', correct)

    return {"level": new_level, "streak": new_streak, "next_review": next_review.isoformat()}


# ── Onboarding ──────────────────────────────────────────

def get_onboarding_status():
    """현재 사용자의 시작 단계 완료 여부 (콘텐츠 추가 / 첫 학습 / 첫 복습)."""
    conn = get_conn()
    uid = _uid()

    def has(q):
        return conn.execute(q, (uid,)).fetchone()[0] > 0

    status = {
        "has_content": has("SELECT COUNT(*) FROM videos WHERE user_id=?"),
        "has_studied": has("SELECT COUNT(*) FROM study_log WHERE user_id=? AND action IN ('study','bulk_study')"),
        "has_reviewed": has("SELECT COUNT(*) FROM study_log WHERE user_id=? AND action='review'"),
    }
    conn.close()
    return status


# ── Statistics ──────────────────────────────────────────

def get_stats(video_id=None):
    conn = get_conn()
    uid = _uid()
    if video_id:
        vp = [video_id, uid]  # video + user 필터 (sentences.user_id)
        total_sentences = conn.execute("SELECT COUNT(*) as c FROM sentences WHERE video_id=? AND user_id=?", vp).fetchone()["c"]
        known_sentences = conn.execute("SELECT COUNT(*) as c FROM sentences WHERE status='known' AND video_id=? AND user_id=?", vp).fetchone()["c"]
        unknown_sentences = conn.execute("SELECT COUNT(*) as c FROM sentences WHERE status='unknown' AND video_id=? AND user_id=?", vp).fetchone()["c"]
        mastered_sentences = conn.execute("SELECT COUNT(*) as c FROM sentences WHERE status='mastered' AND video_id=? AND user_id=?", vp).fetchone()["c"]
        new_sentences = conn.execute("SELECT COUNT(*) as c FROM sentences WHERE status='new' AND video_id=? AND user_id=?", vp).fetchone()["c"]
        total_words = conn.execute("SELECT COUNT(DISTINCT w.id) as c FROM words w JOIN word_video_link wl ON wl.word_id=w.id WHERE wl.video_id=? AND w.user_id=?", vp).fetchone()["c"]
        known_words = conn.execute("SELECT COUNT(DISTINCT w.id) as c FROM words w JOIN word_video_link wl ON wl.word_id=w.id WHERE w.status IN ('known','mastered') AND wl.video_id=? AND w.user_id=?", vp).fetchone()["c"]
        unknown_words = conn.execute("SELECT COUNT(DISTINCT w.id) as c FROM words w JOIN word_video_link wl ON wl.word_id=w.id WHERE w.status='unknown' AND wl.video_id=? AND w.user_id=?", vp).fetchone()["c"]
        due_reviews = conn.execute("""SELECT COUNT(*) as c FROM reviews r
            JOIN sentences s ON r.item_id=s.id AND r.item_type='sentence'
            WHERE r.next_review <= datetime('now') AND r.level < 7 AND s.video_id=? AND r.user_id=?""", vp).fetchone()["c"]
        sentence_due = due_reviews
        word_due = conn.execute("""SELECT COUNT(*) as c FROM reviews r
            JOIN word_video_link wl ON wl.word_id=r.item_id
            WHERE r.item_type='word' AND r.next_review <= datetime('now') AND r.level < 7 AND wl.video_id=? AND r.user_id=?""", vp).fetchone()["c"]
        total_videos = 1
    else:
        up = [uid]
        total_sentences = conn.execute("SELECT COUNT(*) as c FROM sentences WHERE user_id=?", up).fetchone()["c"]
        known_sentences = conn.execute("SELECT COUNT(*) as c FROM sentences WHERE status='known' AND user_id=?", up).fetchone()["c"]
        unknown_sentences = conn.execute("SELECT COUNT(*) as c FROM sentences WHERE status='unknown' AND user_id=?", up).fetchone()["c"]
        mastered_sentences = conn.execute("SELECT COUNT(*) as c FROM sentences WHERE status='mastered' AND user_id=?", up).fetchone()["c"]
        new_sentences = conn.execute("SELECT COUNT(*) as c FROM sentences WHERE status='new' AND user_id=?", up).fetchone()["c"]
        total_words = conn.execute("SELECT COUNT(*) as c FROM words WHERE user_id=?", up).fetchone()["c"]
        known_words = conn.execute("SELECT COUNT(*) as c FROM words WHERE status IN ('known','mastered') AND user_id=?", up).fetchone()["c"]
        unknown_words = conn.execute("SELECT COUNT(*) as c FROM words WHERE status='unknown' AND user_id=?", up).fetchone()["c"]
        due_reviews = conn.execute("SELECT COUNT(*) as c FROM reviews WHERE next_review <= datetime('now') AND level < 7 AND user_id=?", up).fetchone()["c"]
        total_videos = conn.execute("SELECT COUNT(*) as c FROM videos WHERE user_id=?", up).fetchone()["c"]
        sentence_due = conn.execute("SELECT COUNT(*) as c FROM reviews WHERE item_type='sentence' AND next_review <= datetime('now') AND level < 7 AND user_id=?", up).fetchone()["c"]
        word_due = conn.execute("SELECT COUNT(*) as c FROM reviews WHERE item_type='word' AND next_review <= datetime('now') AND level < 7 AND user_id=?", up).fetchone()["c"]

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
    uid = _uid()
    conn = get_conn()
    sentence_count = conn.execute(
        "SELECT COUNT(*) as c FROM reviews WHERE item_type='sentence' AND next_review <= ? AND level < 7 AND user_id=?",
        (now, uid)
    ).fetchone()["c"]
    word_count = conn.execute(
        "SELECT COUNT(*) as c FROM reviews WHERE item_type='word' AND next_review <= ? AND level < 7 AND user_id=?",
        (now, uid)
    ).fetchone()["c"]
    conn.close()
    return {"sentence_due": sentence_count, "word_due": word_count}


def get_analytics(video_id=None):
    """Returns analytics data for charts. If video_id is given, filter by that content."""
    conn = get_conn()
    uid = _uid()
    # 항상 사용자 필터를 적용하고, video_id가 주어지면 추가로 필터 (s. alias 기준)
    vid_filter_s = " AND s.user_id=?" + (" AND s.video_id=?" if video_id else "")
    def sp():
        return [uid, video_id] if video_id else [uid]
    vid_filter_s2 = vid_filter_s

    # 1. Overall mastery distribution (donut chart)
    mastered = conn.execute("SELECT COUNT(*) as c FROM sentences s WHERE status='mastered'" + vid_filter_s, sp()).fetchone()["c"]
    known = conn.execute("SELECT COUNT(*) as c FROM sentences s WHERE status='known'" + vid_filter_s, sp()).fetchone()["c"]
    learning = conn.execute("""
        SELECT COUNT(*) as c FROM sentences s
        JOIN reviews r ON r.item_id = s.id AND r.item_type='sentence'
        WHERE s.status='unknown' AND r.level > 0
    """ + vid_filter_s, sp()).fetchone()["c"]
    frequently_wrong = conn.execute("""
        SELECT COUNT(*) as c FROM reviews r
        JOIN sentences s ON r.item_id = s.id AND r.item_type='sentence'
        WHERE r.streak = 0 AND r.level = 0 AND r.last_review IS NOT NULL AND s.status='unknown'
    """ + vid_filter_s2, sp()).fetchone()["c"]
    total_unknown = conn.execute("SELECT COUNT(*) as c FROM sentences s WHERE status='unknown'" + vid_filter_s, sp()).fetchone()["c"]
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
        WHERE v.user_id=?
        GROUP BY v.id
        ORDER BY v.created_at DESC
    """, (uid,)).fetchall()
    content_progress = [dict(r) for r in content_progress]

    # 3. Learning progress over time (line chart - last 30 days, 문장/단어 분리)
    # study_log 테이블 사용 (과거 기록 유지), 없으면 reviews fallback
    has_study_log = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='study_log'"
    ).fetchone()

    if has_study_log:
        daily_raw = conn.execute("""
            SELECT DATE(created_at) as day, item_type, COUNT(*) as count
            FROM study_log
            WHERE DATE(created_at) >= DATE('now', '-30 days') AND user_id=?
            GROUP BY DATE(created_at), item_type
            ORDER BY day
        """, (uid,)).fetchall()
    else:
        daily_raw = conn.execute("""
            SELECT DATE(last_review) as day, item_type, COUNT(*) as count
            FROM reviews
            WHERE last_review IS NOT NULL
              AND DATE(last_review) >= DATE('now', '-30 days') AND user_id=?
            GROUP BY DATE(last_review), item_type
        ORDER BY day
    """, (uid,)).fetchall()
    daily_map = {}
    for row in daily_raw:
        day = row["day"]
        if day not in daily_map:
            daily_map[day] = {"day": day, "sentences": 0, "words": 0}
        if row["item_type"] == "sentence":
            daily_map[day]["sentences"] = row["count"]
        elif row["item_type"] == "word":
            daily_map[day]["words"] = row["count"]
    daily_progress = [daily_map[d] for d in sorted(daily_map.keys())]

    # 4. SRS level distribution (stacked bar chart)
    # Lv0~Lv6 각 레벨별 문장/단어 수를 정리
    srs_raw = conn.execute("""
        SELECT r.item_type, r.level, COUNT(*) as count
        FROM reviews r
        WHERE r.level < 7 AND r.user_id=?
        GROUP BY r.item_type, r.level
        ORDER BY r.level, r.item_type
    """, (uid,)).fetchall()
    # 레벨별로 문장/단어 수 합산
    srs_map = {}
    for row in srs_raw:
        lv = row["level"]
        if lv not in srs_map:
            srs_map[lv] = {"level": lv, "sentences": 0, "words": 0}
        if row["item_type"] == "sentence":
            srs_map[lv]["sentences"] = row["count"]
        elif row["item_type"] == "word":
            srs_map[lv]["words"] = row["count"]
    srs_distribution = [srs_map[lv] for lv in sorted(srs_map.keys())]

    # 5. Word mastery distribution
    if video_id:
        word_mastered = conn.execute("""SELECT COUNT(DISTINCT w.id) as c FROM words w
            JOIN word_video_link wl ON wl.word_id = w.id WHERE w.status='mastered' AND wl.video_id=? AND w.user_id=?""", [video_id, uid]).fetchone()["c"]
        word_known = conn.execute("""SELECT COUNT(DISTINCT w.id) as c FROM words w
            JOIN word_video_link wl ON wl.word_id = w.id WHERE w.status='known' AND wl.video_id=? AND w.user_id=?""", [video_id, uid]).fetchone()["c"]
        word_unknown = conn.execute("""SELECT COUNT(DISTINCT w.id) as c FROM words w
            JOIN word_video_link wl ON wl.word_id = w.id WHERE w.status='unknown' AND wl.video_id=? AND w.user_id=?""", [video_id, uid]).fetchone()["c"]
        word_freq_wrong = conn.execute("""SELECT COUNT(DISTINCT w.id) as c FROM reviews r
            JOIN words w ON r.item_id = w.id AND r.item_type='word'
            JOIN word_video_link wl ON wl.word_id = w.id
            WHERE r.streak = 0 AND r.level = 0 AND r.last_review IS NOT NULL AND w.status='unknown' AND wl.video_id=? AND w.user_id=?""", [video_id, uid]).fetchone()["c"]
    else:
        word_mastered = conn.execute("SELECT COUNT(*) as c FROM words WHERE status='mastered' AND user_id=?", (uid,)).fetchone()["c"]
        word_known = conn.execute("SELECT COUNT(*) as c FROM words WHERE status='known' AND user_id=?", (uid,)).fetchone()["c"]
        word_unknown = conn.execute("SELECT COUNT(*) as c FROM words WHERE status='unknown' AND user_id=?", (uid,)).fetchone()["c"]
        word_freq_wrong = conn.execute("""
            SELECT COUNT(*) as c FROM reviews r
            JOIN words w ON r.item_id = w.id AND r.item_type='word'
            WHERE r.streak = 0 AND r.level = 0 AND r.last_review IS NOT NULL AND w.status='unknown' AND w.user_id=?
        """, (uid,)).fetchone()["c"]

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
            "INSERT OR IGNORE INTO playlists (playlist_id, title, url, category_id, user_id) VALUES (?,?,?,?,?)",
            (playlist_id, title, url, category_id, _uid())
        )
        conn.commit()
        row = conn.execute("SELECT id FROM playlists WHERE playlist_id=? AND user_id=?", (playlist_id, _uid())).fetchone()
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
        WHERE p.user_id=?
        ORDER BY p.created_at DESC
    """, (_uid(),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_playlist(pl_id):
    conn = get_conn()
    row = conn.execute("""
        SELECT p.*, c.name as category_name
        FROM playlists p
        LEFT JOIN categories c ON p.category_id = c.id
        WHERE p.id=? AND p.user_id=?
    """, (pl_id, _uid())).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_playlist(pl_id):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM playlists WHERE id=? AND user_id=?", (pl_id, _uid()))
        conn.commit()
    finally:
        conn.close()


def update_playlist(pl_id, **kwargs):
    conn = get_conn()
    try:
        for key, val in kwargs.items():
            if key in ('enabled', 'category_id', 'title'):
                conn.execute(f"UPDATE playlists SET {key}=? WHERE id=? AND user_id=?", (val, pl_id, _uid()))
        conn.commit()
    finally:
        conn.close()


def update_playlist_last_checked(pl_id, last_checked):
    conn = get_conn()
    try:
        conn.execute("UPDATE playlists SET last_checked=? WHERE id=? AND user_id=?", (last_checked, pl_id, _uid()))
        conn.commit()
    finally:
        conn.close()


def get_enabled_playlists():
    """모든 사용자의 활성 플레이리스트 반환 (백그라운드 동기화 워커용).
    user_id를 포함하므로 워커가 소유자별로 set_current_user() 할 수 있다."""
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


# ── Admin (운영 도구) ────────────────────────────────────
# 주의: 아래 함수들은 의도적으로 _uid() 필터를 쓰지 않고 전체 사용자를 조회한다.
# 반드시 서버 측 is_admin 검증을 거친 라우트에서만 호출할 것.

def admin_list_users():
    """전체 사용자 목록 + 사용자별 데이터량. 민감정보(비번 해시/AI키)는 제외."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT u.id, u.email, u.name, u.created_at,
               (u.google_id IS NOT NULL) AS has_google,
               (u.ai_key IS NOT NULL AND u.ai_key != '') AS has_ai_key,
               (SELECT COUNT(*) FROM videos    WHERE user_id=u.id) AS videos,
               (SELECT COUNT(*) FROM sentences WHERE user_id=u.id) AS sentences,
               (SELECT COUNT(*) FROM words     WHERE user_id=u.id) AS words,
               (SELECT COUNT(*) FROM reviews   WHERE user_id=u.id) AS reviews,
               (SELECT MAX(created_at) FROM study_log WHERE user_id=u.id) AS last_active
        FROM users u
        ORDER BY u.created_at DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def admin_global_stats():
    """서비스 전체 요약 통계."""
    conn = get_conn()
    def one(q):
        return conn.execute(q).fetchone()[0]
    stats = {
        "total_users": one("SELECT COUNT(*) FROM users"),
        "users_with_google": one("SELECT COUNT(*) FROM users WHERE google_id IS NOT NULL"),
        "users_with_ai_key": one("SELECT COUNT(*) FROM users WHERE ai_key IS NOT NULL AND ai_key != ''"),
        "total_videos": one("SELECT COUNT(*) FROM videos"),
        "total_sentences": one("SELECT COUNT(*) FROM sentences"),
        "total_words": one("SELECT COUNT(*) FROM words"),
        "total_reviews": one("SELECT COUNT(*) FROM reviews"),
        "new_users_7d": one("SELECT COUNT(*) FROM users WHERE created_at >= datetime('now','-7 days')"),
        "active_users_7d": one("SELECT COUNT(DISTINCT user_id) FROM study_log WHERE created_at >= datetime('now','-7 days')"),
    }
    conn.close()
    return stats


# ══════════════════════════════════════════════════════════════════════
#  요금제 · 콘텐츠 등록 할당량 (Billing / Quota)
# ══════════════════════════════════════════════════════════════════════

def get_plans():
    """전체 요금제 목록(정렬순)."""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM plans ORDER BY sort_order, price").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_plan(code):
    """단일 요금제. 없으면 None."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM plans WHERE code=?", (code,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_plan(code, name=None, price=None, content_limit=None):
    """관리자: 요금제의 이름/가격/콘텐츠 한도를 변경한다."""
    sets, params = [], []
    if name is not None:
        sets.append("name=?"); params.append(str(name).strip())
    if price is not None:
        sets.append("price=?"); params.append(max(0, int(price)))
    if content_limit is not None:
        sets.append("content_limit=?"); params.append(max(0, int(content_limit)))
    if not sets:
        return False
    sets.append("updated_at=datetime('now')")
    params.append(code)
    conn = get_conn()
    cur = conn.execute(f"UPDATE plans SET {', '.join(sets)} WHERE code=?", params)
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def set_user_plan(user_id, plan_code):
    """관리자: 사용자의 요금제를 변경한다. 구독 시작일을 지금으로 리셋한다
    (월 할당량이 새 구독 시작일 기준으로 다시 계산됨)."""
    conn = get_conn()
    cur = conn.execute(
        "UPDATE users SET plan_code=?, plan_started_at=datetime('now') WHERE id=?",
        (plan_code, user_id))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def _add_months(dt, n):
    """dt에 n개월을 더한다(음수 가능). 짧은 달은 말일로 보정."""
    m = dt.month - 1 + n
    y = dt.year + m // 12
    m = m % 12 + 1
    d = min(dt.day, calendar.monthrange(y, m)[1])
    return dt.replace(year=y, month=m, day=d)


def _parse_dt(s):
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            return datetime.strptime(str(s)[:10], "%Y-%m-%d")
        except ValueError:
            return None


def _period_start(user, plan):
    """현재 할당량 주기의 시작 시각(문자열) 반환.
    - lifetime(free): None → 전 기간 누적으로 카운트
    - monthly: 구독 시작일(plan_started_at, 없으면 created_at) 기준으로
      '가장 최근 매월 기념일 <= 지금'을 계산한다.
    """
    if not plan or plan.get("period_type") == "lifetime":
        return None
    anchor = _parse_dt(user.get("plan_started_at")) or _parse_dt(user.get("created_at"))
    if not anchor:
        return None
    now = datetime.utcnow()
    months = (now.year - anchor.year) * 12 + (now.month - anchor.month)
    cand = _add_months(anchor, months)
    if cand > now:
        cand = _add_months(anchor, months - 1)
    return cand.strftime("%Y-%m-%d %H:%M:%S")


def get_usage(user_id):
    """사용자의 현재 주기 콘텐츠 등록 사용량을 계산한다.
    반환: plan_code, plan_name, period_type, limit, count, remaining,
          over(bool), period_start
    """
    user = get_user_by_id(user_id)
    plan_code = (user or {}).get("plan_code") or "free"
    plan = get_plan(plan_code) or get_plan("free") or {
        "code": "free", "name": "무료", "content_limit": 1, "period_type": "lifetime"}
    period_start = _period_start(user or {}, plan)

    conn = get_conn()
    if period_start is None:
        count = conn.execute(
            "SELECT COUNT(*) FROM content_registrations WHERE user_id=?",
            (user_id,)).fetchone()[0]
    else:
        count = conn.execute(
            "SELECT COUNT(*) FROM content_registrations WHERE user_id=? AND registered_at >= ?",
            (user_id, period_start)).fetchone()[0]
    conn.close()

    limit = plan["content_limit"]
    return {
        "plan_code": plan["code"] if "code" in plan else plan_code,
        "plan_name": plan["name"],
        "period_type": plan["period_type"],
        "limit": limit,
        "count": count,
        "remaining": max(0, limit - count),
        "over": count >= limit,
        "period_start": period_start,
    }


def record_content_registration(video_id):
    """현재 사용자의 콘텐츠 '학습 가능 등록'을 원장에 기록한다.
    (user_id, video_id)로 멱등 — 같은 콘텐츠 재등록은 카운트되지 않는다.
    새 등록 행이 실제로 삽입되면 True를 반환한다.
    """
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO content_registrations (user_id, video_id) VALUES (?,?)",
            (_uid(), video_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def undo_content_registration(video_id):
    """할당량 초과로 롤백할 때: 방금 기록한 등록 원장 행을 제거한다."""
    conn = get_conn()
    conn.execute("DELETE FROM content_registrations WHERE user_id=? AND video_id=?",
                 (_uid(), video_id))
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════════════════
#  이메일 인증 + 앱 설정 (Email verification / app_settings)
# ══════════════════════════════════════════════════════════════════════

def get_email_settings():
    """이메일(SMTP) 설정을 타입 변환해 반환한다. 관리자 도구에서 편집된 값."""
    conn = get_conn()
    ph = ",".join("?" * len(EMAIL_SETTING_KEYS))
    rows = conn.execute(
        f"SELECT key, value FROM app_settings WHERE key IN ({ph})",
        EMAIL_SETTING_KEYS).fetchall()
    conn.close()
    d = {r["key"]: r["value"] for r in rows}
    try:
        port = int(d.get("smtp_port") or 587)
    except (TypeError, ValueError):
        port = 587
    return {
        "enabled": d.get("email_enabled", "0") == "1",
        "smtp_host": d.get("smtp_host", "") or "",
        "smtp_port": port,
        "smtp_security": (d.get("smtp_security") or "tls").lower(),
        "smtp_user": d.get("smtp_user", "") or "",
        "smtp_password": d.get("smtp_password", "") or "",
        "email_from": (d.get("email_from") or d.get("smtp_user") or ""),
        "email_from_name": d.get("email_from_name", "") or "English Master",
    }


def update_email_settings(**fields):
    """관리자: 이메일 설정을 부분 갱신한다.
    허용 키: enabled(bool), smtp_host, smtp_port, smtp_security,
             smtp_user, smtp_password, email_from, email_from_name
    (smtp_password는 값이 있을 때만 넘기도록 호출부에서 제어)
    """
    mapping = {
        "enabled": "email_enabled",
        "smtp_host": "smtp_host", "smtp_port": "smtp_port",
        "smtp_security": "smtp_security", "smtp_user": "smtp_user",
        "smtp_password": "smtp_password", "email_from": "email_from",
        "email_from_name": "email_from_name",
    }
    conn = get_conn()
    for k, v in fields.items():
        if k not in mapping:
            continue
        if k == "enabled":
            val = "1" if v else "0"
        else:
            val = "" if v is None else str(v)
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            (mapping[k], val))
    conn.commit()
    conn.close()


def set_verify_token(user_id, token):
    """이메일 인증 토큰을 저장하고 발송 시각을 기록한다."""
    conn = get_conn()
    conn.execute("UPDATE users SET verify_token=?, verify_sent_at=datetime('now') WHERE id=?",
                 (token, user_id))
    conn.commit()
    conn.close()


def get_user_by_verify_token(token):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE verify_token=?", (token,)).fetchone()
    conn.close()
    return dict(row) if row else None


def mark_email_verified(user_id):
    """이메일 인증 완료 처리(토큰 소거)."""
    conn = get_conn()
    conn.execute("UPDATE users SET email_verified=1, verify_token=NULL WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
