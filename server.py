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
from queue import Queue
from srs import get_level_name, format_next_review

app = Flask(__name__)

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

            # 1) AI API 우선 (한국어 뜻 제공)
            settings = load_ai_settings()
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

            settings = load_ai_settings()
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


# 워커 스레드 시작 (데몬 모드 = 서버 종료 시 자동 종료)
meaning_thread = threading.Thread(target=meaning_worker, daemon=True)
meaning_thread.start()
sentence_trans_thread = threading.Thread(target=sentence_translation_worker, daemon=True)
sentence_trans_thread.start()
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


# ── API: AI Integration ─────────────────────────────────
# Gemini / Claude / ChatGPT 연동 프록시

AI_SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ai_settings.json")


def load_ai_settings():
    if os.path.exists(AI_SETTINGS_FILE):
        with open(AI_SETTINGS_FILE, "r") as f:
            return json_lib.loads(f.read())
    return {"provider": "", "api_key": ""}


def save_ai_settings_file(settings):
    os.makedirs(os.path.dirname(AI_SETTINGS_FILE), exist_ok=True)
    with open(AI_SETTINGS_FILE, "w") as f:
        f.write(json_lib.dumps(settings))


@app.route("/api/ai/settings", methods=["GET"])
def api_ai_settings_get():
    s = load_ai_settings()
    # 보안: API 키는 마스킹해서 반환
    masked = s.get("api_key", "")
    if masked and len(masked) > 8:
        masked = masked[:4] + "****" + masked[-4:]
    return jsonify({"provider": s.get("provider", ""), "api_key_masked": masked, "has_key": bool(s.get("api_key"))})


@app.route("/api/ai/settings", methods=["POST"])
def api_ai_settings_save():
    data = request.json
    save_ai_settings_file({"provider": data.get("provider", ""), "api_key": data.get("api_key", "")})
    return jsonify({"ok": True})


@app.route("/api/ai/test", methods=["POST"])
def api_ai_test():
    settings = load_ai_settings()
    if not settings.get("api_key"):
        return jsonify({"error": "API 키가 설정되지 않았습니다"}), 400
    try:
        result = call_ai(settings, "Say 'Hello! AI connection successful.' in one short sentence.")
        return jsonify({"result": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai/action", methods=["POST"])
def api_ai_action():
    settings = load_ai_settings()
    if not settings.get("api_key"):
        return jsonify({"error": "설정에서 AI API 키를 먼저 설정해주세요"}), 400

    data = request.json
    sentence = data.get("sentence", "")
    action = data.get("action", "")

    prompts = {
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

📝 퀴즈 1 - 빈칸 채우기
핵심 단어를 빈칸으로 만들고, 보기 3개를 제시하세요.

📝 퀴즈 2 - 어순 배열
단어를 섞어서 제시하고, 올바른 순서로 배열하는 문제를 만드세요.

📝 퀴즈 3 - 한→영 번역
한국어 번역을 보여주고, 영작하게 하세요.

각 퀴즈 뒤에 ✅ 정답과 해설을 한국어로 작성하세요.""",

        "grammar": f"""다음 영어 문장의 문법 구조를 한국어로 상세히 분석해주세요.

📌 문장: "{sentence}"

아래 형식으로 설명해주세요:

🔍 전체 문장 구조
- 문장의 기본 형태(1~5형식) 파악
- 주어(S) / 동사(V) / 목적어(O) / 보어(C) 등 표시

📖 핵심 문법 포인트
- 사용된 시제, 태(능동/수동), 조동사 등 설명
- 해당 문법이 왜 이 문장에서 사용되었는지 설명

🔗 구/절 분석
- 전치사구, 관계사절, 부사절 등이 있으면 분석
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

    prompt = prompts.get(action)
    if not prompt:
        return jsonify({"error": "알 수 없는 액션입니다"}), 400

    try:
        result = call_ai(settings, prompt)
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

    # 2단계: AI API 우선 (한국어 뜻 제공)
    settings = load_ai_settings()
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
