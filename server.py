#!/usr/bin/env python3
"""English Master - Flask Backend Server"""

import os
import sys
import re
import time
import logging
import webbrowser
import threading
import urllib.request
import urllib.parse
import json as json_lib
from datetime import datetime
from flask import Flask, render_template, jsonify, request, session, redirect
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

import secrets
import database as db
import youtube_service as yt
import text_utils
import email_service
from queue import Queue
from srs import get_level_name, format_next_review

app = Flask(__name__)
# 세션 서명 키. 프로덕션에서는 반드시 SECRET_KEY 환경변수로 고정된 값을 지정할 것
# (미지정 시 재시작마다 세션이 무효화됨). Fly: `fly secrets set SECRET_KEY=...`
app.secret_key = os.environ.get("SECRET_KEY", "dev-insecure-change-me")

# 구글 OAuth 설정 (Google Cloud Console에서 발급 → fly secrets로 주입)
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

# 관리자 이메일 (콤마 구분). 미설정 시 첫 사용자(id=1, 소유자)가 관리자.
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}


# ── 인증 게이트 ─────────────────────────────────────────
# 로그인 관련 경로/정적 파일을 제외한 모든 요청은 로그인을 요구한다.
_PUBLIC_PREFIXES = ("/login", "/api/auth/", "/static/", "/health")


@app.before_request
def _require_login():
    path = request.path
    if path == "/login" or path.startswith(_PUBLIC_PREFIXES):
        return None
    uid = session.get("user_id")
    if not uid:
        if path.startswith("/api/"):
            return jsonify({"error": "unauthorized"}), 401
        return redirect("/login")
    # 이후 모든 DB 호출이 이 사용자로 자동 격리되도록 컨텍스트 설정
    db.set_current_user(uid)
    return None


@app.teardown_request
def _clear_user(exc=None):
    db.clear_current_user()


def current_user():
    uid = session.get("user_id")
    return db.get_user_by_id(uid) if uid else None


def is_admin(user):
    """관리자 여부. ADMIN_EMAILS가 설정돼 있으면 그 목록, 아니면 첫 사용자(id=1)."""
    if not user:
        return False
    if ADMIN_EMAILS:
        return (user.get("email") or "").lower() in ADMIN_EMAILS
    return user.get("id") == 1

# ── 단어 뜻 번역 백그라운드 큐 ──────────────────────────
meaning_queue = Queue()


def meaning_worker():
    """백그라운드 스레드: 큐에서 단어를 꺼내 뜻을 조회하고 DB에 캐싱"""
    while True:
        try:
            word = meaning_queue.get()
            if word is None:
                break
            w = word.lower().strip()
            if not w:
                meaning_queue.task_done()
                continue

            # 이미 캐시에 있으면 스킵
            conn = db.get_conn()
            cached = conn.execute("SELECT 1 FROM word_meanings WHERE word = ?", (w,)).fetchone()
            if cached:
                conn.close()
                meaning_queue.task_done()
                continue
            conn.close()

            meaning = None
            source = None

            # 1) AI API 우선 (한국어 뜻 제공) — 전역 캐시 생성이므로 서버 공용 키 사용
            settings = get_server_ai_settings()
            if settings.get("api_key"):
                try:
                    prompt = f'영어 단어 "{w}"의 뜻을 한국어로 간결하게 설명해주세요. 형식: [품사] 한국어뜻1, 뜻2. 2줄 이내로.'
                    meaning = call_ai(settings, prompt)
                    source = "ai"
                except:
                    pass

            # 2) AI 실패 시 무료 사전 API (영영사전 fallback)
            if not meaning:
                try:
                    url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{urllib.parse.quote(w)}"
                    req = urllib.request.Request(url)
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        data = json_lib.loads(resp.read().decode())
                        defs = []
                        for m in data[0].get("meanings", [])[:3]:
                            pos = m.get("partOfSpeech", "")
                            d = m.get("definitions", [{}])[0].get("definition", "")
                            if d:
                                defs.append(f"[{pos}] {d}")
                        if defs:
                            meaning = "\n".join(defs)
                            source = "dict"
                except:
                    pass

            # DB에 저장
            if meaning and source:
                try:
                    conn = db.get_conn()
                    conn.execute("INSERT OR REPLACE INTO word_meanings (word, meaning, source) VALUES (?, ?, ?)",
                                 (w, meaning, source))
                    conn.commit()
                    conn.close()
                    logging.info(f"[MeaningWorker] Cached: {w} ({source})")
                except:
                    pass

            meaning_queue.task_done()
        except Exception as e:
            logging.error(f"[MeaningWorker] Error: {e}")
            try:
                meaning_queue.task_done()
            except:
                pass


# ── 문장 번역 백그라운드 큐 ──────────────────────────────
sentence_trans_queue = Queue()


def sentence_translation_worker():
    """백그라운드 스레드: 큐에서 문장ID를 꺼내 AI로 번역하고 DB에 저장"""
    while True:
        try:
            sentence_id = sentence_trans_queue.get()
            if sentence_id is None:
                break

            conn = db.get_conn()
            row = conn.execute("SELECT text, translation FROM sentences WHERE id = ?", (sentence_id,)).fetchone()
            if not row:
                conn.close()
                sentence_trans_queue.task_done()
                continue

            text = row["text"]
            existing = (row["translation"] or "").strip()

            settings = get_server_ai_settings()  # 전역 캐시 생성 → 서버 공용 키
            if not settings.get("api_key"):
                print(f"[TransWorker] No API key, skipping sentence {sentence_id}", flush=True)
                conn.close()
                sentence_trans_queue.task_done()
                continue

            try:
                prompt = f'다음 영어 문장을 자연스러운 한국어로 번역해주세요. 번역문만 출력하세요.\n\n"{text}"'
                print(f"[TransWorker] Translating sentence {sentence_id}...", flush=True)
                translation = call_ai(settings, prompt)
                if translation:
                    conn.execute("UPDATE sentences SET translation = ? WHERE id = ?",
                                 (translation.strip(), sentence_id))
                    conn.commit()
                    print(f"[TransWorker] Done: {sentence_id} -> {translation.strip()[:60]}", flush=True)
            except Exception as e:
                print(f"[TransWorker] AI error for sentence {sentence_id}: {e}", flush=True)

            conn.close()
            sentence_trans_queue.task_done()
        except Exception as e:
            logging.error(f"[TransWorker] Error: {e}")
            try:
                sentence_trans_queue.task_done()
            except:
                pass


# ── AI 결과 사전 생성 백그라운드 큐 ──────────────────────────────
ai_precache_queue = Queue()

AI_PRECACHE_ACTIONS = ["literal", "grammar", "words"]  # 사전 캐시할 액션 목록

def ai_precache_worker():
    """백그라운드 스레드: 큐에서 sentence_id를 꺼내 AI 결과를 미리 생성/캐싱"""
    while True:
        try:
            sentence_id = ai_precache_queue.get()
            if sentence_id is None:
                break

            conn = db.get_conn()
            row = conn.execute("SELECT text FROM sentences WHERE id = ?", (sentence_id,)).fetchone()
            if not row:
                conn.close()
                ai_precache_queue.task_done()
                continue

            sentence_text = row["text"].strip()
            # 아이콘/이모지 제거
            import re as re_mod
            clean = re_mod.sub(r'[\u2600-\u27BF\U0001F300-\U0001F9FF♪♫♬♩♭♮♯🎵🎶🎤🎧🎼]', '', sentence_text).strip()
            if not clean or len(clean) < 3:
                conn.close()
                ai_precache_queue.task_done()
                continue

            settings = get_server_ai_settings()  # 전역 캐시 생성 → 서버 공용 키
            if not settings.get("api_key"):
                conn.close()
                ai_precache_queue.task_done()
                continue

            for action in AI_PRECACHE_ACTIONS:
                # 이미 캐시된 것 건너뛰기
                existing = conn.execute(
                    "SELECT id FROM ai_cache WHERE sentence_text = ? AND action = ?",
                    (clean, action)
                ).fetchone()
                if existing:
                    continue

                try:
                    prompt = _build_ai_prompt(clean, action)
                    if not prompt:
                        continue
                    result = call_ai(settings, prompt)
                    if result:
                        conn.execute(
                            "INSERT OR REPLACE INTO ai_cache (sentence_text, action, result) VALUES (?, ?, ?)",
                            (clean, action, result.strip())
                        )
                        conn.commit()
                        print(f"[AI-Cache] Pre-cached {action} for: {clean[:40]}...", flush=True)
                    time.sleep(1)  # API 부하 방지
                except Exception as e:
                    print(f"[AI-Cache] Error {action} for {clean[:30]}: {e}", flush=True)

            conn.close()
            ai_precache_queue.task_done()
        except Exception as e:
            logging.error(f"[AI-Cache] Worker error: {e}")
            try:
                ai_precache_queue.task_done()
            except:
                pass


# 워커 스레드 시작 (데몬 모드 = 서버 종료 시 자동 종료)
meaning_thread = threading.Thread(target=meaning_worker, daemon=True)
meaning_thread.start()
sentence_trans_thread = threading.Thread(target=sentence_translation_worker, daemon=True)
sentence_trans_thread.start()
ai_precache_thread = threading.Thread(target=ai_precache_worker, daemon=True)
ai_precache_thread.start()
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

# Disable caching for development
@app.after_request
def add_no_cache(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

# ── Pages ───────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/login")
def login_page():
    # 이미 로그인돼 있으면 앱으로
    if session.get("user_id"):
        return redirect("/")
    return render_template("login.html", google_enabled=bool(GOOGLE_CLIENT_ID))


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


# ── API: Auth ───────────────────────────────────────────

def _send_verification(user):
    """인증 토큰을 발급·저장하고 인증 메일을 보낸다.
    반환: (성공여부, 에러, dev_link)
      - dev_link: SMTP 미설정/발송실패 시, 로컬(app.debug)에서만 테스트용 링크를 노출
    """
    token = secrets.token_urlsafe(32)
    db.set_verify_token(user["id"], token)
    link = request.host_url.rstrip("/") + "/api/auth/verify?token=" + token
    settings = db.get_email_settings()
    subject, text, html = email_service.verification_email_bodies(link)
    ok, err = email_service.send_email(settings, user["email"], subject, text, html)
    dev_link = None
    if not ok:
        # 발송 실패(미설정 포함): 링크를 로그로 남겨 운영자가 흐름을 확인/테스트할 수 있게 함
        logging.warning(f"[verify] 메일 발송 실패({err}). {user['email']} 인증 링크: {link}")
        if app.debug or not settings.get("enabled"):
            dev_link = link
    return ok, err, dev_link


@app.route("/api/auth/signup", methods=["POST"])
def api_signup():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    name = (data.get("name") or "").strip()

    if not email or "@" not in email:
        return jsonify({"error": "올바른 이메일을 입력하세요."}), 400
    if len(password) < 8:
        return jsonify({"error": "비밀번호는 8자 이상이어야 합니다."}), 400
    if db.get_user_by_email(email):
        return jsonify({"error": "이미 가입된 이메일입니다."}), 409

    user = db.create_user(
        email=email,
        # pbkdf2:sha256 은 모든 환경의 hashlib에서 지원됨 (scrypt는 일부 빌드에 없음)
        password_hash=generate_password_hash(password, method="pbkdf2:sha256"),
        name=name,
    )
    if not user:
        return jsonify({"error": "가입에 실패했습니다."}), 500

    # 최초 운영자(첫 가입자 = 관리자)는 자동 인증한다.
    # 닭-달걀 방지: SMTP를 설정하려면 /admin 로그인이 필요한데, 로그인엔 인증이 필요하고,
    # 인증 메일 발송엔 SMTP 설정이 필요하다. 이 순환을 끊기 위해 첫 사용자는 인증 없이 통과.
    if db.count_users() == 1:
        db.claim_orphan_data(user["id"])
        db.mark_email_verified(user["id"])
        session["user_id"] = user["id"]
        return jsonify({"id": user["id"], "email": user["email"], "name": user["name"],
                        "verified": True})

    # 그 외 사용자: 인증 메일을 보내고 로그인은 보류(인증 완료 후 로그인 가능)
    ok, err, dev_link = _send_verification(user)
    resp = {
        "pending_verification": True,
        "email": user["email"],
        "message": "인증 메일을 보냈습니다. 메일함을 확인해 인증을 완료한 뒤 로그인하세요.",
    }
    if not ok:
        resp["message"] = ("가입은 완료됐지만 인증 메일 발송에 실패했습니다. "
                           "잠시 후 재발송하거나 운영자에게 문의하세요.")
    if dev_link:
        resp["dev_verify_url"] = dev_link
    return jsonify(resp)


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    user = db.get_user_by_email(email)
    if not user or not user.get("password_hash") or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "이메일 또는 비밀번호가 올바르지 않습니다."}), 401

    # 이메일 미인증 계정은 로그인 차단(구글 계정은 이미 검증되어 email_verified=1)
    if not user.get("email_verified"):
        return jsonify({
            "error": "email_unverified",
            "message": "이메일 인증이 필요합니다. 메일함의 인증 링크를 확인하세요.",
            "email": user["email"],
        }), 403

    session["user_id"] = user["id"]
    return jsonify({"id": user["id"], "email": user["email"], "name": user["name"]})


@app.route("/api/auth/verify", methods=["GET"])
def api_verify_email():
    """인증 링크 클릭 → 인증 완료 후 즉시 로그인시키고 앱으로 보낸다."""
    token = request.args.get("token", "")
    user = db.get_user_by_verify_token(token) if token else None
    if not user:
        return redirect("/login?verify=invalid")
    db.mark_email_verified(user["id"])
    session["user_id"] = user["id"]
    return redirect("/?verified=1")


@app.route("/api/auth/resend-verification", methods=["POST"])
def api_resend_verification():
    """인증 메일 재발송. 계정 존재 여부는 노출하지 않도록 항상 동일 응답."""
    email = ((request.get_json(silent=True) or {}).get("email") or "").strip().lower()
    user = db.get_user_by_email(email)
    resp = {"message": "인증 메일을 다시 보냈습니다. 메일함을 확인하세요."}
    if user and user.get("password_hash") and not user.get("email_verified"):
        ok, err, dev_link = _send_verification(user)
        if dev_link:
            resp["dev_verify_url"] = dev_link
    return jsonify(resp)


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"status": "ok"})


@app.route("/api/auth/me", methods=["GET"])
def api_me():
    user = current_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({
        "id": user["id"], "email": user["email"], "name": user["name"],
        "is_admin": is_admin(user),
    })


# ── 구글 OAuth 로그인 ────────────────────────────────────

def _google_redirect_uri():
    """콜백 URI. GOOGLE_REDIRECT_URI 환경변수 우선, 없으면 요청 호스트에서 유도.
    프로덕션(비 localhost)은 https 강제 (Google은 정확히 일치하는 URI를 요구)."""
    env = os.environ.get("GOOGLE_REDIRECT_URI")
    if env:
        return env
    root = request.host_url
    if not (root.startswith("http://localhost") or root.startswith("http://127.")):
        root = root.replace("http://", "https://", 1)
    return root.rstrip("/") + "/api/auth/google/callback"


@app.route("/api/auth/google")
def api_google_login():
    if not GOOGLE_CLIENT_ID:
        return redirect("/login?error=google_unconfigured")
    import secrets as _secrets
    state = _secrets.token_urlsafe(24)
    session["oauth_state"] = state
    params = urllib.parse.urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": _google_redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    })
    return redirect("https://accounts.google.com/o/oauth2/v2/auth?" + params)


@app.route("/api/auth/google/callback")
def api_google_callback():
    # CSRF: state 검증
    if not request.args.get("state") or request.args.get("state") != session.get("oauth_state"):
        return redirect("/login?error=state")
    session.pop("oauth_state", None)
    code = request.args.get("code")
    if not code:
        return redirect("/login?error=nocode")

    try:
        # 1) code → access_token 교환
        token_body = urllib.parse.urlencode({
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": _google_redirect_uri(),
            "grant_type": "authorization_code",
        }).encode()
        treq = urllib.request.Request("https://oauth2.googleapis.com/token", data=token_body)
        with urllib.request.urlopen(treq, timeout=10) as resp:
            tok = json_lib.loads(resp.read().decode())
        access_token = tok.get("access_token")
        if not access_token:
            return redirect("/login?error=token")
        # 2) 사용자 정보 조회
        ureq = urllib.request.Request(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        with urllib.request.urlopen(ureq, timeout=10) as resp:
            info = json_lib.loads(resp.read().decode())
    except Exception as e:
        logging.warning(f"Google OAuth error: {e}")
        return redirect("/login?error=google")

    google_id = str(info.get("id", ""))
    email = (info.get("email") or "").strip().lower()
    name = info.get("name", "")
    if not email or not google_id:
        return redirect("/login?error=noemail")

    # 기존 계정 연결 또는 신규 생성
    user = db.get_user_by_google_id(google_id) or db.get_user_by_email(email)
    if user:
        if not user.get("google_id"):
            db.link_google_id(user["id"], google_id)
    else:
        user = db.create_user(email=email, password_hash=None, name=name, google_id=google_id)
        if not user:
            return redirect("/login?error=create")
        # 구글이 이미 이메일을 검증했으므로 자동 인증 처리
        db.mark_email_verified(user["id"])
        # 첫 가입자는 기존 데이터 인수
        if db.count_users() == 1:
            db.claim_orphan_data(user["id"])

    session["user_id"] = user["id"]
    return redirect("/")


# ── Admin (운영 도구) ────────────────────────────────────

def _require_admin():
    """관리자가 아니면 (응답, 상태코드) 반환, 관리자면 None."""
    if not is_admin(current_user()):
        return jsonify({"error": "forbidden"}), 403
    return None


def _finalize_registration(db_video_id):
    """콘텐츠가 '학습 가능 상태'로 등록된 직후 호출한다.
    등록 이벤트를 할당량 원장에 기록하고 요금제 한도를 강제한다.
      - 이미 등록된 콘텐츠의 재등록이면: 아무 일도 하지 않음(할당량 소비 없음).
      - 새 등록이 한도를 초과하면: 방금 만든 콘텐츠를 롤백(삭제)하고
        (에러 응답, 402) 튜플을 반환한다.
      - 정상이면 None을 반환한다.
    호출부는: `err = _finalize_registration(id); return err if err else <성공응답>`
    """
    if not db_video_id:
        return None
    is_new = db.record_content_registration(db_video_id)
    if not is_new:
        return None  # 재등록 — 할당량 소비 없음
    usage = db.get_usage(session.get("user_id"))
    if usage["count"] > usage["limit"]:
        # 한도 초과: 방금 생성한 콘텐츠와 원장 기록을 되돌린다
        db.undo_content_registration(db_video_id)
        db.delete_video(db_video_id)
        period = "평생" if usage["period_type"] == "lifetime" else "이번 주기"
        return jsonify({
            "error": "quota_exceeded",
            "message": f"'{usage['plan_name']}' 요금제의 {period} 콘텐츠 등록 한도"
                       f"({usage['limit']}개)를 모두 사용했습니다. 요금제를 업그레이드하세요.",
            "usage": usage,
        }), 402
    return None


@app.route("/admin")
def admin_page():
    if not is_admin(current_user()):
        return redirect("/")
    return render_template("admin.html")


@app.route("/api/admin/users", methods=["GET"])
def api_admin_users():
    guard = _require_admin()
    if guard:
        return guard
    users = db.admin_list_users()
    # 각 사용자에 요금제/현재 주기 사용량 정보를 덧붙인다
    for u in users:
        usage = db.get_usage(u["id"])
        u["plan_code"] = usage["plan_code"]
        u["plan_name"] = usage["plan_name"]
        u["usage_count"] = usage["count"]
        u["usage_limit"] = usage["limit"]
        u["period_type"] = usage["period_type"]
    return jsonify(users)


@app.route("/api/admin/stats", methods=["GET"])
def api_admin_stats():
    guard = _require_admin()
    if guard:
        return guard
    return jsonify(db.admin_global_stats())


# ── API: Admin - 요금제 관리 ────────────────────────────

@app.route("/api/admin/plans", methods=["GET"])
def api_admin_get_plans():
    guard = _require_admin()
    if guard:
        return guard
    return jsonify(db.get_plans())


@app.route("/api/admin/plans/<code>", methods=["PUT"])
def api_admin_update_plan(code):
    """요금제 가격/콘텐츠 한도/이름 변경 (관리자)."""
    guard = _require_admin()
    if guard:
        return guard
    if not db.get_plan(code):
        return jsonify({"error": "존재하지 않는 요금제입니다"}), 404
    data = request.json or {}
    price = data.get("price")
    content_limit = data.get("content_limit")
    name = data.get("name")
    # 최소 유효성 검사
    if price is not None:
        try:
            price = int(price)
            if price < 0:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({"error": "가격은 0 이상의 정수여야 합니다"}), 400
    if content_limit is not None:
        try:
            content_limit = int(content_limit)
            if content_limit < 0:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({"error": "콘텐츠 한도는 0 이상의 정수여야 합니다"}), 400
    db.update_plan(code, name=name, price=price, content_limit=content_limit)
    return jsonify(db.get_plan(code))


@app.route("/api/admin/users/<int:user_id>/plan", methods=["PUT"])
def api_admin_set_user_plan(user_id):
    """사용자 요금제 변경 (관리자). PG 연동 전까지 수동 부여 수단."""
    guard = _require_admin()
    if guard:
        return guard
    plan_code = (request.json or {}).get("plan_code", "").strip()
    if not db.get_plan(plan_code):
        return jsonify({"error": "존재하지 않는 요금제입니다"}), 400
    if not db.get_user_by_id(user_id):
        return jsonify({"error": "존재하지 않는 사용자입니다"}), 404
    db.set_user_plan(user_id, plan_code)
    return jsonify(db.get_usage(user_id))


# ── API: Admin - 이메일(SMTP) 설정 ─────────────────────

def _email_settings_public(s):
    """비밀번호 값은 노출하지 않고 설정 여부(has_password)만 반환."""
    s = dict(s)
    s["has_password"] = bool(s.pop("smtp_password", ""))
    return s


@app.route("/api/admin/email-settings", methods=["GET"])
def api_admin_get_email():
    guard = _require_admin()
    if guard:
        return guard
    return jsonify(_email_settings_public(db.get_email_settings()))


@app.route("/api/admin/email-settings", methods=["PUT"])
def api_admin_update_email():
    """이메일(SMTP) 설정 변경 (관리자). 발송 주체를 운영자가 자유롭게 바꾼다."""
    guard = _require_admin()
    if guard:
        return guard
    data = request.json or {}
    fields = {}
    for k in ("smtp_host", "smtp_port", "smtp_security", "smtp_user",
              "email_from", "email_from_name"):
        if k in data:
            fields[k] = data[k]
    if "enabled" in data:
        fields["enabled"] = bool(data["enabled"])
    # 비밀번호는 값이 있을 때만 갱신(빈 값이면 기존 유지 → 마스킹 UX)
    if data.get("smtp_password"):
        fields["smtp_password"] = data["smtp_password"]
    db.update_email_settings(**fields)
    return jsonify(_email_settings_public(db.get_email_settings()))


@app.route("/api/admin/email-settings/test", methods=["POST"])
def api_admin_test_email():
    """현재 설정으로 관리자 본인에게 테스트 메일을 보낸다."""
    guard = _require_admin()
    if guard:
        return guard
    to = current_user()["email"]
    ok, err = email_service.send_email(
        db.get_email_settings(), to,
        "[English Master] 테스트 메일",
        "이 메일을 받으셨다면 SMTP 설정이 정상입니다.",
        "<p>이 메일을 받으셨다면 <b>SMTP 설정이 정상</b>입니다.</p>")
    if ok:
        return jsonify({"ok": True, "message": f"{to} 로 테스트 메일을 보냈습니다."})
    return jsonify({"ok": False, "error": err or "발송 실패"}), 400


# ── API: 내 요금제/사용량 (사용자) ──────────────────────

@app.route("/api/me/plan", methods=["GET"])
def api_my_plan():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({
        "usage": db.get_usage(uid),
        "plans": db.get_plans(),
    })


# ── API: Categories ────────────────────────────────────

@app.route("/api/categories", methods=["GET"])
def api_get_categories():
    return jsonify(db.get_categories())


@app.route("/api/categories", methods=["POST"])
def api_add_category():
    name = request.json.get("name", "").strip()
    if not name:
        return jsonify({"error": "이름을 입력하세요"}), 400
    cat_id = db.add_category(name)
    return jsonify({"id": cat_id, "name": name})


@app.route("/api/categories/<int:cat_id>", methods=["DELETE"])
def api_delete_category(cat_id):
    db.delete_category(cat_id)
    return jsonify({"ok": True})


@app.route("/api/categories/<int:cat_id>", methods=["PUT"])
def api_rename_category(cat_id):
    name = request.json.get("name", "").strip()
    if not name:
        return jsonify({"error": "이름을 입력하세요"}), 400
    db.rename_category(cat_id, name)
    return jsonify({"ok": True})


@app.route("/api/videos/<int:video_id>/category", methods=["PUT"])
def api_set_video_category(video_id):
    category_id = request.json.get("category_id")
    db.set_video_category(video_id, category_id)
    return jsonify({"ok": True})


# ── API: Videos ─────────────────────────────────────────

@app.route("/api/videos", methods=["GET"])
def api_get_videos():
    category_id = request.args.get("category_id", type=int)
    return jsonify(db.get_videos(category_id))


@app.route("/api/videos", methods=["POST"])
def api_add_video():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400

    # URL 정리: list, start_radio 등 불필요한 파라미터 제거
    video_id_from_url = yt.extract_video_id(url)
    if video_id_from_url:
        url = f"https://www.youtube.com/watch?v={video_id_from_url}"

    try:
        video_id, title, sentences = yt.process_video(url)
        db_video_id = db.add_video(url, video_id, title)

        existing = db.get_all_sentences(db_video_id)
        if existing:
            return jsonify({"message": "이미 추가된 영상입니다", "video_id": db_video_id, "sentences": len(existing)})

        db.add_sentences(db_video_id, sentences)
        quota_err = _finalize_registration(db_video_id)
        if quota_err:
            return quota_err
        return jsonify({
            "message": f"'{title}' 추가 완료!",
            "video_id": db_video_id,
            "sentences": len(sentences),
            "paragraphs": sentences[-1][0] + 1 if sentences else 0,
        })
    except Exception as e:
        error_msg = str(e)
        # 자막이 없는 경우 특별 응답
        if "Could not get transcript" in error_msg or "Could not retrieve a transcript" in error_msg:
            video_id = yt.extract_video_id(url)
            title = f"YouTube - {video_id}" if video_id else "Unknown"
            # YouTube 제목 가져오기 시도
            try:
                title = yt.get_video_title(video_id) or title
            except Exception:
                pass
            clean_title = _clean_youtube_title(title)
            return jsonify({
                "no_transcript": True,
                "video_id": video_id,
                "title": title,
                "clean_title": clean_title,
                "url": url,
                "message": "이 콘텐츠는 자막(가사)이 없습니다."
            })
        return jsonify({"error": error_msg}), 400


@app.route("/api/videos/add-with-lyrics", methods=["POST"])
def api_add_video_with_lyrics():
    """자막 없는 YouTube 영상에 가사/자막을 수동으로 추가"""
    data = request.json
    url = data.get("url", "").strip()
    lyrics = data.get("lyrics", "").strip()
    title = data.get("title", "").strip()

    if not url or not lyrics:
        return jsonify({"error": "URL과 가사가 필요합니다"}), 400

    video_id = yt.extract_video_id(url)
    if not video_id:
        return jsonify({"error": "유효하지 않은 YouTube URL입니다"}), 400

    # 텍스트 콘텐츠로 저장하되 YouTube URL 연결
    db_video_id, error = _save_text_content(
        title, lyrics, category_id=None,
        content_type='youtube_lyrics', url=url
    )
    if error:
        return jsonify({"error": error}), 400

    # YouTube video_id 업데이트
    conn = db.get_conn()
    conn.execute("UPDATE videos SET video_id=? WHERE id=?", (video_id, db_video_id))
    conn.commit()
    conn.close()

    quota_err = _finalize_registration(db_video_id)
    if quota_err:
        return quota_err

    count = len(db.get_all_sentences(db_video_id))
    return jsonify({
        "message": f"'{title}' 가사 추가 완료!",
        "video_id": db_video_id,
        "sentences": count
    })


@app.route("/api/lyrics/search", methods=["POST"])
def api_search_lyrics():
    """인터넷에서 가사를 자동으로 검색"""
    data = request.json
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"error": "제목이 필요합니다"}), 400

    # YouTube 제목에서 가수명과 곡명 추출 시도
    clean_title = _clean_youtube_title(title)

    lyrics = _search_lyrics_online(clean_title)
    if lyrics:
        return jsonify({"lyrics": lyrics, "title": clean_title})
    else:
        return jsonify({"lyrics": None, "clean_title": clean_title, "message": "가사를 찾을 수 없습니다. 제목을 수정하여 다시 검색하거나 직접 입력해주세요."})


def _clean_youtube_title(title):
    """YouTube 제목에서 가수명 - 곡명 형태로 정리"""
    clean = title
    # 괄호/대괄호 안 내용 제거: (가사), (Official Video), [MV], [Lyrics] 등
    clean = re.sub(r'\(.*?\)|\[.*?\]|\{.*?\}', '', clean)
    # 일반적인 YouTube 불필요 단어 제거
    noise_words = [
        'Official', 'Video', 'MV', 'M/V', 'Lyrics', 'Lyric',
        'HD', '4K', '1080p', '720p', 'Audio', 'HQ',
        'Full', 'Album', 'Version', 'Live', 'Concert',
        'Remastered', 'Remaster', 'with lyrics',
        '가사', '자막', '한글', '번역', '해석',
    ]
    for w in noise_words:
        clean = re.sub(r'\b' + re.escape(w) + r'\b', '', clean, flags=re.IGNORECASE)
    # 언더스코어, 콜론 등을 하이픈으로 변환 (가수_곡명, 가수: 곡명 → 가수 - 곡명)
    clean = re.sub(r'\s*[_:：]\s*', ' - ', clean)
    # 여러 하이픈/대시 통일
    clean = re.sub(r'\s*[-–—]+\s*', ' - ', clean)
    # 연속 공백 정리
    clean = re.sub(r'\s+', ' ', clean).strip()
    # 양 끝 하이픈 제거
    clean = clean.strip('- ').strip()
    return clean


def _search_lyrics_online(query):
    """인터넷에서 가사 검색 (lyrics.ovh API 사용, 여러 파싱 전략 시도)"""
    # 가수 - 곡명 분리 전략들
    strategies = []

    # 전략 1: 하이픈으로 분리 (가장 일반적: "Artist - Song")
    parts = re.split(r'\s*[-–—]\s*', query, maxsplit=1)
    if len(parts) == 2:
        strategies.append((parts[0].strip(), parts[1].strip()))
        # 전략 2: 순서 뒤집기 ("Song - Artist" 형태일 수도 있음)
        strategies.append((parts[1].strip(), parts[0].strip()))

    # 전략 3: "by" 로 분리 ("Song by Artist")
    by_parts = re.split(r'\s+by\s+', query, maxsplit=1, flags=re.IGNORECASE)
    if len(by_parts) == 2:
        strategies.append((by_parts[1].strip(), by_parts[0].strip()))

    # 전략 4: 하이픈 없으면 전체를 곡명으로, 앞부분을 아티스트로 추정
    if len(parts) < 2:
        words = query.split()
        if len(words) >= 3:
            # 앞 2단어를 아티스트, 나머지를 곡명으로
            strategies.append((' '.join(words[:2]), ' '.join(words[2:])))
        if len(words) >= 2:
            strategies.append((words[0], ' '.join(words[1:])))

    for artist, song in strategies:
        if not artist or not song:
            continue
        result = _try_lyrics_api(artist, song)
        if result:
            return result

    return None


def _try_lyrics_api(artist, song):
    """lyrics.ovh API로 가사 검색 시도"""
    try:
        encoded_artist = urllib.parse.quote(artist)
        encoded_song = urllib.parse.quote(song)
        api_url = f"https://api.lyrics.ovh/v1/{encoded_artist}/{encoded_song}"
        req = urllib.request.Request(api_url, headers={"User-Agent": "EnglishMaster/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json_lib.loads(resp.read().decode('utf-8'))
            if data.get("lyrics"):
                return data["lyrics"].strip()
    except Exception as e:
        logging.debug(f"Lyrics API failed for '{artist}' - '{song}': {e}")
    return None


@app.route("/api/videos/<int:video_id>", methods=["DELETE"])
def api_delete_video(video_id):
    db.delete_video(video_id)
    return jsonify({"ok": True})


@app.route("/api/videos/<int:video_id>/sentences")
def api_get_video_sentences(video_id):
    return jsonify(db.get_all_sentences_for_video(video_id))


@app.route("/api/videos/<int:video_id>/info")
def api_get_video_info(video_id):
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(dict(row))


# ── API: Study ──────────────────────────────────────────

@app.route("/api/study/paragraphs/<int:video_id>")
def api_get_paragraphs(video_id):
    paragraphs = db.get_paragraphs_for_study(video_id)
    return jsonify(paragraphs)


@app.route("/api/study/paragraph/<int:video_id>/<int:paragraph_idx>")
def api_get_paragraph(video_id, paragraph_idx):
    sentences = db.get_paragraph_sentences(video_id, paragraph_idx)
    return jsonify(sentences)


@app.route("/api/study/sentences")
def api_get_study_sentences():
    video_id = request.args.get("video_id", type=int)
    sentences = db.get_sentences_for_study(video_id)
    return jsonify(sentences)


@app.route("/api/study/mark", methods=["POST"])
def api_mark_sentence():
    data = request.json
    sid = data["sentence_id"]
    status = data["status"]  # 'known' or 'unknown'
    db.mark_sentence(sid, status)

    if status == "known":
        db.schedule_review(sid, "sentence", level=1)
    elif status == "unknown":
        db.schedule_review(sid, "sentence", level=0)
        # 백그라운드에서 AI 번역 (모르는 문장 → 고품질 번역 제공)
        sentence_trans_queue.put(sid)
        # 백그라운드에서 AI 결과 사전 생성 (직독직해, 문법, 단어)
        ai_precache_queue.put(sid)

    return jsonify({"ok": True})


@app.route("/api/study/reset-unknown", methods=["POST"])
def api_reset_unknown_sentences():
    """Reset all unknown sentences for a video back to 'new' status and reset their review level to 0"""
    data = request.json
    video_id = data.get("video_id")
    if not video_id:
        return jsonify({"error": "video_id required"}), 400
    db.reset_unknown_sentences(video_id)
    return jsonify({"ok": True})


# ── API: Words ──────────────────────────────────────────

@app.route("/api/words/unknown", methods=["GET"])
def api_get_unknown_words():
    video_id = request.args.get("video_id")
    return jsonify(db.get_unknown_words(video_id=int(video_id) if video_id else None))


@app.route("/api/words/known", methods=["GET"])
def api_get_known_words():
    return jsonify(db.get_known_words())


@app.route("/api/words/add", methods=["POST"])
def api_add_unknown_word():
    data = request.json
    word = data.get("word", "").strip()
    video_id = data.get("video_id")
    if word:
        db.add_unknown_word(word)
        # 단어-영상 연결 기록
        if video_id:
            try:
                conn = db.get_conn()
                word_row = conn.execute("SELECT id FROM words WHERE word=?", (word,)).fetchone()
                if word_row:
                    conn.execute("INSERT OR IGNORE INTO word_video_link (word_id, video_id) VALUES (?,?)",
                                 (word_row["id"], int(video_id)))
                    conn.commit()
                conn.close()
            except Exception:
                pass
        # 백그라운드 큐에 추가 → 별도 스레드가 뜻을 조회하여 DB에 캐싱
        meaning_queue.put(word)
    return jsonify({"ok": True})


@app.route("/api/words/mark", methods=["POST"])
def api_mark_word():
    data = request.json
    db.mark_word(data["word_id"], data["status"])
    return jsonify({"ok": True})


@app.route("/api/words/<int:word_id>", methods=["DELETE"])
def api_delete_word(word_id):
    db.delete_word(word_id)
    return jsonify({"ok": True})


# ── API: Reviews (SRS - 에빙하우스 망각곡선 기반 간격 반복) ──
# Lv0→즉시 | Lv1→1h | Lv2→1일 | Lv3→2일 | Lv4→4일 | Lv5→7일 | Lv6→15일 | Lv7→30일(완전습득)
# 정답→level+1, 오답→level=0 리셋

@app.route("/api/reviews")
def api_get_reviews():
    item_type = request.args.get("type")
    video_id = request.args.get("video_id")
    reviews = db.get_due_reviews(item_type, video_id=int(video_id) if video_id else None)
    for r in reviews:
        r["level_name"] = get_level_name(r.get("level", 0))
        r["next_review_text"] = format_next_review(r.get("next_review"))
    return jsonify(reviews)


@app.route("/api/reviews/remaining")
def api_reviews_remaining():
    """아직 due가 아닌 리뷰 항목 수와 다음 복습 시간"""
    item_type = request.args.get("type", "sentence")
    now = datetime.now().isoformat()
    conn = db.get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as c, MIN(next_review) as next_time FROM reviews WHERE item_type=? AND next_review > ? AND level < 7",
        (item_type, now)
    ).fetchone()
    conn.close()
    remaining = row["c"] if row else 0
    next_time = None
    if row and row["next_time"]:
        try:
            from datetime import datetime as dt
            nt = dt.fromisoformat(row["next_time"])
            diff = (nt - dt.now()).total_seconds()
            if diff < 60:
                next_time = f"{int(diff)}초 후"
            elif diff < 3600:
                next_time = f"{int(diff/60)}분 후"
            else:
                next_time = f"{int(diff/3600)}시간 {int((diff%3600)/60)}분 후"
        except:
            next_time = row["next_time"][:16]
    return jsonify({"remaining": remaining, "nextTime": next_time})


@app.route("/api/reviews/all")
def api_reviews_all():
    """스케줄 무시 - 전체 리뷰 항목 반환 (level < 7)"""
    item_type = request.args.get("type", "sentence")
    video_id = request.args.get("video_id")
    conn = db.get_conn()
    if item_type == "sentence":
        sql = """
            SELECT r.*, s.text, s.video_id, s.start_time, s.end_time,
                   v.title as video_title, v.video_id as youtube_video_id, v.content_type
            FROM reviews r
            JOIN sentences s ON r.item_id = s.id
            JOIN videos v ON s.video_id = v.id
            WHERE r.item_type='sentence' AND r.level < 7
        """
        params = []
        if video_id:
            sql += " AND s.video_id = ?"
            params.append(int(video_id))
        sql += " ORDER BY r.next_review"
        rows = conn.execute(sql, params).fetchall()
    else:
        sql = """
            SELECT r.*, w.word as text
            FROM reviews r JOIN words w ON r.item_id = w.id
            WHERE r.item_type='word' AND r.level < 7
        """
        params = []
        if video_id:
            sql += """ AND w.id IN (
                SELECT wl.word_id FROM word_video_link wl WHERE wl.video_id = ?
            )"""
            params.append(int(video_id))
        sql += " ORDER BY r.next_review"
        rows = conn.execute(sql, params).fetchall()
    conn.close()
    result = [dict(r) for r in rows]
    for r in result:
        r["level_name"] = get_level_name(r.get("level", 0))
    return jsonify(result)


@app.route("/api/reviews/process", methods=["POST"])
def api_process_review():
    data = request.json
    result = db.process_review(data["item_id"], data["item_type"], data["correct"])
    result["level_name"] = get_level_name(result["level"])
    return jsonify(result)


# ── API: Sentence management ─────────────────────────────

@app.route("/api/sentences/<int:sentence_id>", methods=["DELETE"])
def api_delete_sentence(sentence_id):
    db.delete_sentence(sentence_id)
    return jsonify({"ok": True})


# ── API: Unknown sentences ──────────────────────────────

@app.route("/api/sentences/unknown")
def api_unknown_sentences():
    video_id = request.args.get("video_id")
    return jsonify(db.get_unknown_sentences(video_id=int(video_id) if video_id else None))


@app.route("/api/sentences/known")
def api_known_sentences():
    return jsonify(db.get_known_sentences())


# ── API: Translation ────────────────────────────────────

@app.route("/api/words/translate")
def api_translate_word():
    word = request.args.get("word", "").strip()
    if not word:
        return jsonify({"error": "단어가 없습니다"}), 400
    try:
        encoded = urllib.parse.quote(word)
        api_url = f"https://api.mymemory.translated.net/get?q={encoded}&langpair=en|ko"
        req = urllib.request.Request(api_url, headers={"User-Agent": "EnglishMaster/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json_lib.loads(resp.read().decode())
            translation = data.get("responseData", {}).get("translatedText", "")
            if translation:
                return jsonify({"translation": translation})
            return jsonify({"error": "번역 실패"}), 500
    except Exception as e:
        return jsonify({"error": f"번역 오류: {str(e)}"}), 500


@app.route("/api/translate/<int:sentence_id>")
def api_translate(sentence_id):
    # Check cache first
    cached = db.get_translation(sentence_id)
    if cached:
        return jsonify({"translation": cached})

    # Get sentence text
    conn = db.get_conn()
    row = conn.execute("SELECT text FROM sentences WHERE id=?", (sentence_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "문장을 찾을 수 없습니다"}), 404

    text = row["text"]
    try:
        encoded = urllib.parse.quote(text)
        api_url = f"https://api.mymemory.translated.net/get?q={encoded}&langpair=en|ko"
        req = urllib.request.Request(api_url, headers={"User-Agent": "EnglishMaster/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json_lib.loads(resp.read().decode())
            translation = data.get("responseData", {}).get("translatedText", "")
            if translation:
                db.save_translation(sentence_id, translation)
                return jsonify({"translation": translation})
            return jsonify({"error": "번역 실패"}), 500
    except Exception as e:
        return jsonify({"error": f"번역 오류: {str(e)}"}), 500


# ── API: Text Content ──────────────────────────────────

def _save_text_content(title, text, category_id=None, content_type='text', url=None):
    """Shared helper: clean text, split into sentences, and save to DB."""
    # Step 1: Line-level cleaning (removes junk lines when text has newlines)
    text = text_utils.clean_pasted_text(text)
    # Step 2: Split into sentences
    raw_sentences = text_utils.split_into_sentences(text)
    # Step 3: Sentence-level filtering (catches junk embedded in single lines)
    raw_sentences = text_utils.filter_junk_sentences(raw_sentences)
    if not raw_sentences:
        return None, "유효한 문장을 찾을 수 없습니다"

    if not title:
        # Generate title from cleaned sentences, not raw text
        title = text_utils.generate_title(' '.join(raw_sentences))

    db_video_id = db.add_text_content(title, text, category_id, content_type=content_type, url=url)
    if not db_video_id:
        return None, "이미 추가된 콘텐츠입니다"

    existing = db.get_all_sentences(db_video_id)
    if existing:
        return db_video_id, None  # already has sentences

    sentence_data = text_utils.group_into_paragraphs(raw_sentences)
    db.add_sentences(db_video_id, sentence_data)
    return db_video_id, None


@app.route("/api/content/text", methods=["POST"])
def api_add_text_content():
    data = request.json
    title = data.get("title", "").strip()
    text = data.get("text", "").strip()
    category_id = data.get("category_id")

    if not text:
        return jsonify({"error": "내용을 입력하세요"}), 400

    db_video_id, error = _save_text_content(title, text, category_id)
    if error:
        return jsonify({"error": error}), 400

    quota_err = _finalize_registration(db_video_id)
    if quota_err:
        return quota_err

    row = db.get_video(db_video_id) if db_video_id else None
    final_title = row["title"] if row else (title or "콘텐츠")
    count = len(db.get_all_sentences(db_video_id))
    return jsonify({"message": f"'{final_title}' 추가 완료!", "video_id": db_video_id, "sentences": count})


@app.route("/api/content/url/preview", methods=["POST"])
def api_url_preview():
    """Preview URL content without saving."""
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL을 입력하세요"}), 400
    try:
        html, final_url = text_utils.fetch_url_content(url)
        page_title = text_utils.extract_title_from_html(html)
        text = text_utils.extract_text_from_html(html)
        word_count = len(text.split()) if text else 0
        if not text or word_count < 10:
            return jsonify({"error": "페이지에서 충분한 텍스트를 추출할 수 없습니다. 이 사이트는 JavaScript로 콘텐츠를 로드하는 것일 수 있습니다. 텍스트를 직접 복사하여 붙여넣기를 이용하세요."}), 400
        auto_title = page_title or text_utils.generate_title(text)
        return jsonify({"title": auto_title, "text": text, "word_count": word_count})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/content/url", methods=["POST"])
def api_add_url_content():
    """Add content from a URL."""
    data = request.json
    url = data.get("url", "").strip()
    title = data.get("title", "").strip()
    category_id = data.get("category_id")

    if not url:
        return jsonify({"error": "URL을 입력하세요"}), 400

    try:
        html, final_url = text_utils.fetch_url_content(url)
        text = text_utils.extract_text_from_html(html)
        if not text or len(text.split()) < 10:
            return jsonify({"error": "페이지에서 충분한 텍스트를 추출할 수 없습니다. 텍스트를 직접 복사하여 붙여넣기를 이용하세요."}), 400

        if not title:
            page_title = text_utils.extract_title_from_html(html)
            title = page_title or text_utils.generate_title(text)

        db_video_id, error = _save_text_content(title, text, category_id, content_type='text', url=url)
        if error:
            return jsonify({"error": error}), 400

        quota_err = _finalize_registration(db_video_id)
        if quota_err:
            return quota_err

        count = len(db.get_all_sentences(db_video_id))
        return jsonify({"message": f"'{title}' 추가 완료!", "video_id": db_video_id, "sentences": count})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/content/file", methods=["POST"])
def api_add_file_content():
    """Add content from an uploaded file."""
    if 'file' not in request.files:
        return jsonify({"error": "파일을 선택하세요"}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({"error": "파일을 선택하세요"}), 400

    title = request.form.get("title", "").strip()
    category_id = request.form.get("category_id") or None
    if category_id:
        try:
            category_id = int(category_id)
        except ValueError:
            category_id = None

    try:
        text = text_utils.extract_text_from_file(file, file.filename)
        if not text or len(text.split()) < 10:
            return jsonify({"error": "파일에서 충분한 텍스트를 추출할 수 없습니다"}), 400

        if not title:
            title = text_utils.generate_title(text)

        db_video_id, error = _save_text_content(title, text, category_id, content_type='text')
        if error:
            return jsonify({"error": error}), 400

        quota_err = _finalize_registration(db_video_id)
        if quota_err:
            return quota_err

        count = len(db.get_all_sentences(db_video_id))
        return jsonify({"message": f"'{title}' 추가 완료!", "video_id": db_video_id, "sentences": count})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


# ── API: Stats ──────────────────────────────────────────

@app.route("/api/stats")
def api_stats():
    video_id = request.args.get("video_id")
    return jsonify(db.get_stats(video_id=int(video_id) if video_id else None))


@app.route("/api/onboarding")
def api_onboarding():
    return jsonify(db.get_onboarding_status())


@app.route("/api/reviews/videos")
def api_review_videos():
    """복습 항목이 있는 콘텐츠(영상) 목록 반환"""
    item_type = request.args.get("type", "sentence")
    conn = db.get_conn()
    if item_type == "sentence":
        rows = conn.execute("""
            SELECT DISTINCT v.id, v.title FROM reviews r
            JOIN sentences s ON r.item_id = s.id
            JOIN videos v ON s.video_id = v.id
            WHERE r.item_type='sentence' AND r.level < 7
            ORDER BY v.title
        """).fetchall()
    else:
        rows = conn.execute("""
            SELECT DISTINCT v.id, v.title FROM word_video_link wl
            JOIN videos v ON wl.video_id = v.id
            JOIN words w ON wl.word_id = w.id
            JOIN reviews r ON r.item_id = w.id AND r.item_type='word'
            WHERE r.level < 7
            ORDER BY v.title
        """).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/reviews/counts")
def api_review_counts():
    return jsonify(db.get_due_review_counts())


@app.route("/api/analytics")
def api_analytics():
    video_id = request.args.get("video_id")
    return jsonify(db.get_analytics(video_id=int(video_id) if video_id else None))


# ── API: Playlists ──────────────────────────────────────

SYNC_INTERVAL_MINUTES = 30
sync_thread_running = False
last_sync_time = None


@app.route("/api/playlists", methods=["GET"])
def api_get_playlists():
    return jsonify({"playlists": db.get_playlists(), "last_sync_time": last_sync_time})


@app.route("/api/playlists", methods=["POST"])
def api_add_playlist():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL을 입력하세요"}), 400

    playlist_id = yt.extract_playlist_id(url)
    if not playlist_id:
        return jsonify({"error": "유효한 YouTube 재생목록 URL이 아닙니다"}), 400

    try:
        title = yt.get_playlist_title(playlist_id)
    except Exception as e:
        return jsonify({"error": f"재생목록을 가져올 수 없습니다: {e}"}), 400

    custom_title = data.get("title", "").strip() or title
    category_id = data.get("category_id") or None
    full_url = f"https://www.youtube.com/playlist?list={playlist_id}"
    db_id = db.add_playlist(playlist_id, custom_title, full_url, category_id)
    if not db_id:
        return jsonify({"error": "이미 등록된 재생목록입니다"}), 400
    return jsonify({"id": db_id, "title": custom_title, "playlist_id": playlist_id})


@app.route("/api/playlists/<int:pl_id>", methods=["DELETE"])
def api_delete_playlist(pl_id):
    db.delete_playlist(pl_id)
    return jsonify({"ok": True})


@app.route("/api/playlists/<int:pl_id>", methods=["PUT"])
def api_update_playlist(pl_id):
    data = request.json
    db.update_playlist(pl_id, **data)
    return jsonify({"ok": True})


@app.route("/api/playlists/<int:pl_id>/sync", methods=["POST"])
def api_sync_playlist(pl_id):
    result = sync_single_playlist(pl_id)
    return jsonify(result)


@app.route("/api/playlists/sync-all", methods=["POST"])
def api_sync_all_playlists():
    results = sync_all_playlists()
    return jsonify({"results": results})


@app.route("/api/playlists/status", methods=["GET"])
def api_playlist_sync_status():
    return jsonify({
        "running": sync_thread_running,
        "interval_minutes": SYNC_INTERVAL_MINUTES,
        "last_sync": last_sync_time,
    })


def sync_single_playlist(pl_id):
    """Sync one playlist. Returns dict with results."""
    playlist = db.get_playlist(pl_id)
    if not playlist:
        return {"error": "재생목록을 찾을 수 없습니다", "added": 0}

    try:
        entries = yt.fetch_playlist_feed(playlist["playlist_id"])
    except Exception as e:
        logging.warning(f"Playlist RSS fetch failed for {playlist['playlist_id']}: {e}")
        return {"error": str(e), "added": 0}

    existing_ids = db.get_playlist_video_ids(pl_id)
    new_count = 0
    errors = []

    # 정책(의도적): 플레이리스트로 추가되는 영상은 콘텐츠 등록 할당량에 포함하지 않는다.
    # → 여기서는 일부러 _finalize_registration/record_content_registration을 호출하지 않는다.
    #   (수동 등록 5경로에만 한도를 적용한다.) 자세한 이유는 docs/DECISION_LOG.md 참조.
    #   ⚠ 이는 버그가 아니라 결정된 정책이다. 무료 우회 남용이 문제되면 그때 재검토.
    for entry in entries:
        if entry["video_id"] in existing_ids:
            continue
        video_url = f"https://www.youtube.com/watch?v={entry['video_id']}"
        try:
            video_id, title, sentences = yt.process_video(video_url)
            db_video_id = db.add_video(video_url, video_id, title)
            if playlist.get("category_id"):
                db.set_video_category(db_video_id, playlist["category_id"])
            existing_sents = db.get_all_sentences(db_video_id)
            if not existing_sents:
                db.add_sentences(db_video_id, sentences)
            db.add_playlist_video(pl_id, db_video_id, entry["video_id"])
            new_count += 1
        except Exception as e:
            errors.append({"video_id": entry["video_id"], "error": str(e)})
            logging.info(f"Skipped video {entry['video_id']}: {e}")

    now = datetime.now().isoformat()
    db.update_playlist_last_checked(pl_id, now)

    return {"added": new_count, "errors": len(errors), "skipped_details": errors[:5]}


def sync_all_playlists():
    """Sync all enabled playlists (모든 사용자). 각 플레이리스트의 소유자로
    컨텍스트를 설정해, 새로 추가되는 영상이 소유자에게 귀속되도록 한다."""
    global last_sync_time
    playlists = db.get_enabled_playlists()
    results = []
    for pl in playlists:
        try:
            db.set_current_user(pl.get("user_id"))
            result = sync_single_playlist(pl["id"])
            result["playlist_title"] = pl["title"]
            results.append(result)
        finally:
            db.clear_current_user()
    last_sync_time = datetime.now().isoformat()
    return results


def _background_sync_loop():
    """Background thread: periodically sync all playlists."""
    global sync_thread_running
    sync_thread_running = True
    while True:
        time.sleep(SYNC_INTERVAL_MINUTES * 60)
        try:
            logging.info("Background playlist sync starting...")
            sync_all_playlists()
            logging.info("Background playlist sync complete.")
        except Exception as e:
            logging.warning(f"Background sync error: {e}")


# ── API: AI Integration ─────────────────────────────────
# Gemini / Claude / ChatGPT 연동 프록시

AI_SETTINGS_FILE = os.path.join(db.DATA_DIR, "ai_settings.json")


def get_server_ai_settings():
    """운영자(서버) 공용 키. 우선순위: 환경변수 → ai_settings.json 파일.
    배경 워커(전역 캐시 생성)와, 사용자가 개인 키를 설정하지 않았을 때의 폴백."""
    env_key = os.environ.get("SERVER_AI_KEY")
    if env_key:
        return {"provider": os.environ.get("SERVER_AI_PROVIDER", ""), "api_key": env_key}
    if os.path.exists(AI_SETTINGS_FILE):
        with open(AI_SETTINGS_FILE, "r") as f:
            return json_lib.loads(f.read())
    return {"provider": "", "api_key": ""}


def resolve_ai_settings():
    """상호작용 요청용: 현재 사용자가 개인 키를 설정했으면 그것을, 아니면 서버 공용 키를 사용."""
    u = current_user()
    if u and u.get("ai_key"):
        return {"provider": u.get("ai_provider", ""), "api_key": u.get("ai_key", "")}
    return get_server_ai_settings()


@app.route("/api/ai/settings", methods=["GET"])
def api_ai_settings_get():
    u = current_user()
    user_key = (u or {}).get("ai_key", "")
    masked = user_key
    if masked and len(masked) > 8:
        masked = masked[:4] + "****" + masked[-4:]
    server_available = bool(get_server_ai_settings().get("api_key"))
    return jsonify({
        "provider": (u or {}).get("ai_provider", ""),
        "api_key_masked": masked,
        "has_key": bool(user_key),
        "server_key_available": server_available,  # 개인 키 미설정 시 서버 키로 동작하는지
    })


@app.route("/api/ai/settings", methods=["POST"])
def api_ai_settings_save():
    data = request.json
    u = current_user()
    if not u:
        return jsonify({"error": "unauthorized"}), 401
    db.update_user_ai_settings(u["id"], data.get("provider", ""), data.get("api_key", ""))
    return jsonify({"ok": True})


@app.route("/api/ai/test", methods=["POST"])
def api_ai_test():
    settings = resolve_ai_settings()
    if not settings.get("api_key"):
        return jsonify({"error": "API 키가 설정되지 않았습니다 (개인 키 또는 서버 키 필요)"}), 400
    try:
        result = call_ai(settings, "Say 'Hello! AI connection successful.' in one short sentence.")
        return jsonify({"result": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _build_ai_prompt(sentence, action, quiz_types=None):
    """AI 액션별 프롬프트 생성 (캐시 워커와 API 공용)"""
    if quiz_types is None:
        quiz_types = ["선택", "어순", "번역"]

    quiz_type_map = {
        "선택": ("빈칸 채우기", "핵심 단어를 빈칸(____)으로 만들고, 보기를 1) 2) 3) 형식으로 3개 제시하세요."),
        "어순": ("어순 배열", "문장의 단어들을 섞어서 나열하고, 올바른 순서로 배열하는 문제를 만드세요. **단어:** 로 시작하세요."),
        "번역": ("한→영 번역", "한국어 번역을 보여주고 영어로 번역하게 하세요. **한국어:** 로 시작하세요."),
    }
    quiz_parts = []
    for i, qt in enumerate(quiz_types[:3], 1):
        title, instruction = quiz_type_map.get(qt, quiz_type_map["선택"])
        quiz_parts.append(f"📝 퀴즈 {i} - {title}\n{instruction}")
    quiz_body = "\n\n".join(quiz_parts)

    prompts = {
        "literal": f"""다음 영어 문장을 직독직해로 분석해주세요.

문장: "{sentence}"

아래 형식으로 **정확히** 작성해주세요 (태그와 구분자를 반드시 지켜주세요):

[WORDS]
영어단어나구::한국어뜻
영어단어나구::한국어뜻
[/WORDS]
[CHUNKS]
의미청크(2~4단어)::한국어뜻
의미청크(2~4단어)::한국어뜻
[/CHUNKS]
[FULL]
자연스러운 한국어 전체 번역 1줄
[/FULL]
[TIP]
직독직해 포인트 1~2줄
[/TIP]

규칙:
- [WORDS]: 문장의 모든 단어를 앞에서부터 순서대로 하나씩 분석 (빠뜨리지 마세요). 관사(a, the), 접속사(and, but), 감탄사(ooh, oh) 등도 반드시 포함
- [CHUNKS]: 같은 문장을 2~4단어씩 의미 덩어리로 끊어서 분석. 예: "I will::나는 ~할 것이다", "always love you::항상 당신을 사랑하다"
- 한국어 뜻은 간결하게 (1~5단어)
- :: 구분자를 반드시 사용""",

        "similar": f"""다음 영어 문장과 비슷한 패턴의 영어 문장 3개를 만들어주세요.

원문: "{sentence}"

형식:
1. [영어 문장]
   → [한국어 번역]
   💡 [원문과 비교하여 바뀐 부분 간단 설명]

2. [영어 문장]
   → [한국어 번역]
   💡 [변경점 설명]

3. [영어 문장]
   → [한국어 번역]
   💡 [변경점 설명]""",

        "quiz": f"""다음 영어 문장을 바탕으로 한국어 학습자를 위한 퀴즈 3개를 만들어주세요.

문장: "{sentence}"

{quiz_body}

각 퀴즈 뒤에 ✅ 정답: 으로 시작하는 정답과 해설을 한국어로 작성하세요.""",

        "grammar": f"""다음 영어 문장의 문법 구조를 한국어로 상세히 분석해주세요.

📌 문장: "{sentence}"

아래 형식으로 설명해주세요:

🔍 전체 문장 구조
- 문장의 기본 구조 (주어 + 동사 + 목적어 등)를 설명

📝 핵심 문법 요소
- 시제, 조동사, 접속사 등 핵심 문법 요소 설명
- 각 구성 요소가 문장에서 하는 역할

🔗 구문 분석
- 주어부, 술어부, 수식어 등을 구분
- 수식 관계 설명

💡 핵심 정리
- 이 문장에서 꼭 알아야 할 문법 핵심 1~2가지를 간결하게 정리

쉽고 명확하게 한국어로 설명해주세요.""",

        "words": f"""다음 영어 문장의 각 단어/표현을 한국어로 설명해주세요.

📌 문장: "{sentence}"

각 단어별로 아래 형식으로 설명:

🔤 [단어/표현]
- 뜻: [한국어 뜻]
- 품사: [명사/동사/형용사 등]
- 발음: [발음 힌트]
- 예문: [다른 예문 1개 + 한국어 번역]
- 💡 팁: [헷갈리기 쉬운 점이나 유사 표현]

중요하지 않은 단어(a, the, is 등)는 간단히 설명하고, 핵심 단어 위주로 상세히 설명해주세요."""
    }
    return prompts.get(action)


@app.route("/api/ai/action", methods=["POST"])
def api_ai_action():
    settings = resolve_ai_settings()
    if not settings.get("api_key"):
        return jsonify({"error": "설정에서 AI API 키를 먼저 설정해주세요"}), 400

    data = request.json
    sentence = data.get("sentence", "").strip()
    action = data.get("action", "")
    quiz_types = data.get("quizTypes", ["선택", "어순", "번역"])

    # 1) 캐시 체크 (퀴즈는 매번 새로 생성)
    if action != "quiz" and sentence:
        try:
            conn = db.get_conn()
            cached = conn.execute(
                "SELECT result FROM ai_cache WHERE sentence_text = ? AND action = ?",
                (sentence, action)
            ).fetchone()
            conn.close()
            if cached:
                print(f"[AI-Cache] HIT: {action} for {sentence[:40]}...", flush=True)
                return jsonify({"result": cached["result"], "cached": True})
        except Exception:
            pass

    # 2) 프롬프트 생성 (_build_ai_prompt 공용 함수 사용)
    prompt = _build_ai_prompt(sentence, action, quiz_types)
    if not prompt:
        return jsonify({"error": "알 수 없는 액션입니다"}), 400

    # 3) AI 호출
    try:
        result = call_ai(settings, prompt)
        # 4) 결과 캐싱 (퀴즈 제외)
        if result and action != "quiz":
            try:
                conn = db.get_conn()
                conn.execute(
                    "INSERT OR REPLACE INTO ai_cache (sentence_text, action, result) VALUES (?, ?, ?)",
                    (sentence, action, result.strip())
                )
                conn.commit()
                conn.close()
            except Exception:
                pass
        return jsonify({"result": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai/word-meaning")
def api_word_meaning():
    word = request.args.get("word", "").strip().lower()
    if not word:
        return jsonify({"error": "단어가 없습니다"}), 400

    conn = db.get_conn()

    # 1단계: DB 캐시 확인 (즉시 반환)
    cached = conn.execute("SELECT meaning FROM word_meanings WHERE word = ?", (word,)).fetchone()
    if cached:
        conn.close()
        return jsonify({"meaning": cached["meaning"], "source": "cache"})

    meaning = None
    source = None

    # 2단계: AI API 우선 (한국어 뜻 제공) — 사용자 키 우선, 없으면 서버 키
    settings = resolve_ai_settings()
    if settings.get("api_key"):
        try:
            prompt = f'영어 단어 "{word}"의 뜻을 한국어로 간결하게 설명해주세요. 형식: [품사] 한국어뜻1, 뜻2. 2줄 이내로.'
            meaning = call_ai(settings, prompt)
            source = "ai"
        except:
            pass

    # 3단계: AI 실패 시 무료 사전 API (영영사전 fallback)
    if not meaning:
        try:
            url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{urllib.parse.quote(word)}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json_lib.loads(resp.read().decode())
                defs = []
                for m in data[0].get("meanings", [])[:3]:
                    pos = m.get("partOfSpeech", "")
                    d = m.get("definitions", [{}])[0].get("definition", "")
                    if d:
                        defs.append(f"[{pos}] {d}")
                if defs:
                    meaning = "\n".join(defs)
                    source = "dict"
        except:
            pass

    if not meaning:
        meaning = "(뜻을 불러올 수 없습니다)"
        source = "none"

    # DB에 캐싱 (다음번엔 즉시 반환)
    if source in ("dict", "ai"):
        try:
            conn.execute("INSERT OR REPLACE INTO word_meanings (word, meaning, source) VALUES (?, ?, ?)",
                         (word, meaning, source))
            conn.commit()
        except:
            pass

    conn.close()
    return jsonify({"meaning": meaning, "source": source})


@app.route("/api/ai/word-meanings-batch", methods=["POST"])
def api_word_meanings_batch():
    """여러 단어의 뜻을 한번에 조회 (캐시 우선)"""
    words = request.json.get("words", [])
    if not words:
        return jsonify({"results": {}})

    conn = db.get_conn()
    results = {}

    # DB 캐시에서 한번에 조회
    placeholders = ",".join("?" * len(words))
    cached = conn.execute(
        f"SELECT word, meaning FROM word_meanings WHERE word IN ({placeholders})",
        [w.lower() for w in words]
    ).fetchall()
    for row in cached:
        results[row["word"]] = row["meaning"]

    # 캐시에 없는 단어만 사전 API로 조회
    missing = [w for w in words if w.lower() not in results]
    for word in missing[:20]:  # 최대 20개 제한
        w = word.lower()
        meaning = None
        source = None
        try:
            url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{urllib.parse.quote(w)}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json_lib.loads(resp.read().decode())
                defs = []
                for m in data[0].get("meanings", [])[:2]:
                    pos = m.get("partOfSpeech", "")
                    d = m.get("definitions", [{}])[0].get("definition", "")
                    if d:
                        defs.append(f"[{pos}] {d}")
                if defs:
                    meaning = "\n".join(defs)
                    source = "dict"
        except:
            pass

        if meaning:
            results[w] = meaning
            try:
                conn.execute("INSERT OR REPLACE INTO word_meanings (word, meaning, source) VALUES (?, ?, ?)",
                             (w, meaning, source))
            except:
                pass
        else:
            results[w] = w

    conn.commit()
    conn.close()
    return jsonify({"results": results})


def call_ai(settings, prompt, retries=3):
    """Gemini / Claude / ChatGPT API 호출 (429 에러 시 재시도)"""
    provider = settings["provider"]
    api_key = settings["api_key"]

    for attempt in range(retries + 1):
        try:
            return _call_ai_once(provider, api_key, prompt)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:
                wait = 5 * (attempt + 1)  # 5초, 10초, 15초 대기 후 재시도
                logging.info(f"AI API 429 rate limit, retry {attempt+1}/{retries} after {wait}s")
                time.sleep(wait)
                continue
            # 에러 본문 읽기
            error_body = ""
            try:
                error_body = e.read().decode()
            except:
                pass
            raise Exception(f"AI API 오류 ({e.code}): {error_body[:200] if error_body else str(e)}")
        except Exception as e:
            raise Exception(f"AI 연결 오류: {str(e)}")
    raise Exception("AI API 호출 실패 (재시도 초과)")


def _call_ai_once(provider, api_key, prompt):
    """실제 AI API 1회 호출"""
    if provider == "gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
        payload = json_lib.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json_lib.loads(resp.read().decode())
            return data["candidates"][0]["content"]["parts"][0]["text"]

    elif provider == "claude":
        url = "https://api.anthropic.com/v1/messages"
        body = json_lib.dumps({
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}]
        }).encode()
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01"
        }, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json_lib.loads(resp.read().decode())
            return data["content"][0]["text"]

    elif provider == "chatgpt":
        url = "https://api.openai.com/v1/chat/completions"
        body = json_lib.dumps({
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1024
        }).encode()
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json_lib.loads(resp.read().decode())
            return data["choices"][0]["message"]["content"]

    else:
        raise ValueError("AI 서비스가 선택되지 않았습니다. 설정에서 선택해주세요.")


# ── App initialization (import 시점에 실행 → gunicorn 등 WSGI 서버에서도 동작) ──
# gunicorn은 `__main__`을 실행하지 않으므로, DB 초기화와 백그라운드 스레드는
# 모듈 로드 시점에 시작해야 한다.

def init_app():
    db.init_db()
    # 플레이리스트 자동 동기화 스레드 (다중 워커 환경에서는 ENABLE_SYNC=0으로 끌 수 있음)
    if os.environ.get("ENABLE_SYNC", "1") == "1":
        threading.Thread(target=_background_sync_loop, daemon=True).start()


init_app()


# ── Main (로컬 개발 전용) ────────────────────────────────

def open_browser():
    webbrowser.open(f"http://127.0.0.1:{os.environ.get('PORT', '5294')}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5294"))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    print("\n  ╔══════════════════════════════════════╗")
    print("  ║   English Master 영어 마스터          ║")
    print(f"  ║   http://127.0.0.1:{port}              ║")
    print("  ╚══════════════════════════════════════╝\n")

    if os.environ.get("OPEN_BROWSER", "1") == "1":
        threading.Timer(1.0, open_browser).start()
    app.run(host="0.0.0.0", port=port, debug=debug, use_reloader=False)
