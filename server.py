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
from flask import Flask, render_template, jsonify, request

import database as db
import youtube_service as yt
import text_utils
from srs import get_level_name, format_next_review

app = Flask(__name__)
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

    try:
        video_id, title, sentences = yt.process_video(url)
        db_video_id = db.add_video(url, video_id, title)

        existing = db.get_all_sentences(db_video_id)
        if existing:
            return jsonify({"message": "이미 추가된 영상입니다", "video_id": db_video_id, "sentences": len(existing)})

        db.add_sentences(db_video_id, sentences)
        return jsonify({
            "message": f"'{title}' 추가 완료!",
            "video_id": db_video_id,
            "sentences": len(sentences),
            "paragraphs": sentences[-1][0] + 1 if sentences else 0,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400


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

    return jsonify({"ok": True})


# ── API: Words ──────────────────────────────────────────

@app.route("/api/words/unknown", methods=["GET"])
def api_get_unknown_words():
    return jsonify(db.get_unknown_words())


@app.route("/api/words/known", methods=["GET"])
def api_get_known_words():
    return jsonify(db.get_known_words())


@app.route("/api/words/add", methods=["POST"])
def api_add_unknown_word():
    data = request.json
    word = data.get("word", "").strip()
    if word:
        db.add_unknown_word(word)
    return jsonify({"ok": True})


@app.route("/api/words/mark", methods=["POST"])
def api_mark_word():
    data = request.json
    db.mark_word(data["word_id"], data["status"])
    return jsonify({"ok": True})


# ── API: Reviews ────────────────────────────────────────

@app.route("/api/reviews")
def api_get_reviews():
    item_type = request.args.get("type")
    reviews = db.get_due_reviews(item_type)
    for r in reviews:
        r["level_name"] = get_level_name(r.get("level", 0))
        r["next_review_text"] = format_next_review(r.get("next_review"))
    return jsonify(reviews)


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
    return jsonify(db.get_unknown_sentences())


@app.route("/api/sentences/known")
def api_known_sentences():
    return jsonify(db.get_known_sentences())


# ── API: Translation ────────────────────────────────────

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

        count = len(db.get_all_sentences(db_video_id))
        return jsonify({"message": f"'{title}' 추가 완료!", "video_id": db_video_id, "sentences": count})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


# ── API: Stats ──────────────────────────────────────────

@app.route("/api/stats")
def api_stats():
    return jsonify(db.get_stats())


@app.route("/api/reviews/counts")
def api_review_counts():
    return jsonify(db.get_due_review_counts())


@app.route("/api/analytics")
def api_analytics():
    return jsonify(db.get_analytics())


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
    """Sync all enabled playlists."""
    global last_sync_time
    playlists = db.get_enabled_playlists()
    results = []
    for pl in playlists:
        result = sync_single_playlist(pl["id"])
        result["playlist_title"] = pl["title"]
        results.append(result)
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


# ── Main ────────────────────────────────────────────────

def open_browser():
    webbrowser.open("http://127.0.0.1:5294")


if __name__ == "__main__":
    db.init_db()
    print("\n  ╔══════════════════════════════════════╗")
    print("  ║   English Master 영어 마스터 v1.0     ║")
    print("  ║   http://127.0.0.1:5294              ║")
    print("  ╚══════════════════════════════════════╝\n")

    # Start background playlist sync thread
    sync_thread = threading.Thread(target=_background_sync_loop, daemon=True)
    sync_thread.start()

    threading.Timer(1.0, open_browser).start()
    app.run(host="127.0.0.1", port=5294, debug=True, use_reloader=False)
