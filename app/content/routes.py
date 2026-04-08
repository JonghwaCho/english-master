"""Content endpoints: videos, categories, sentences, text content, playlists.

All endpoints require authentication and filter by `g.current_user.id`.
Cross-user access returns 404 to prevent enumeration.
"""
from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.auth.decorators import require_auth
from app.db.models import Category, Playlist, PlaylistVideo, Sentence, UsageCounter, Video
from app.extensions import db
from app.quota import enforce_video_quota
from app.utils.youtube_service import extract_video_id, process_video

content_bp = Blueprint("content", __name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────
# Categories
# ─────────────────────────────────────────────────────


@content_bp.route("/categories", methods=["GET"])
@require_auth
def list_categories():
    rows = db.session.scalars(
        select(Category).where(Category.user_id == g.current_user.id).order_by(Category.name)
    ).all()
    return jsonify([{"id": c.id, "name": c.name} for c in rows])


@content_bp.route("/categories", methods=["POST"])
@require_auth
def create_category():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name_required"}), 400

    cat = Category(user_id=g.current_user.id, name=name)
    db.session.add(cat)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "duplicate_name", "message": "이미 존재하는 카테고리입니다"}), 409
    return jsonify({"id": cat.id, "name": cat.name}), 201


@content_bp.route("/categories/<int:category_id>", methods=["PUT"])
@require_auth
def rename_category(category_id: int):
    cat = db.session.scalar(
        select(Category).where(
            Category.id == category_id, Category.user_id == g.current_user.id
        )
    )
    if not cat:
        return jsonify({"error": "not_found"}), 404

    data = request.get_json(silent=True) or {}
    new_name = (data.get("name") or "").strip()
    if not new_name:
        return jsonify({"error": "name_required"}), 400
    cat.name = new_name
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "duplicate_name"}), 409
    return jsonify({"id": cat.id, "name": cat.name})


@content_bp.route("/categories/<int:category_id>", methods=["DELETE"])
@require_auth
def delete_category(category_id: int):
    cat = db.session.scalar(
        select(Category).where(
            Category.id == category_id, Category.user_id == g.current_user.id
        )
    )
    if not cat:
        return jsonify({"error": "not_found"}), 404
    db.session.delete(cat)
    db.session.commit()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────
# Videos
# ─────────────────────────────────────────────────────


def _video_to_dict(video: Video, with_stats: bool = False) -> dict:
    data = {
        "id": video.id,
        "url": video.url,
        "video_id": video.video_id,
        "title": video.title,
        "content_type": video.content_type,
        "category_id": video.category_id,
        "created_at": video.created_at.isoformat() if video.created_at else None,
    }
    if with_stats:
        # Compute simple stats
        stats = db.session.execute(
            select(
                func.count(Sentence.id).label("total"),
                func.count(Sentence.id).filter(Sentence.status == "known").label("known"),
                func.count(Sentence.id).filter(Sentence.status == "unknown").label("unknown"),
                func.count(Sentence.id).filter(Sentence.status == "new").label("new"),
                func.count(Sentence.id).filter(Sentence.status == "mastered").label("mastered"),
            ).where(Sentence.video_id == video.id)
        ).one()
        data["stats"] = {
            "total": stats.total,
            "known": stats.known,
            "unknown": stats.unknown,
            "new": stats.new,
            "mastered": stats.mastered,
        }
    return data


@content_bp.route("/videos", methods=["GET"])
@require_auth
def list_videos():
    category_id = request.args.get("category_id", type=int)
    stmt = select(Video).where(Video.user_id == g.current_user.id).order_by(Video.created_at.desc())
    if category_id:
        stmt = stmt.where(Video.category_id == category_id)
    videos = db.session.scalars(stmt).all()
    return jsonify([_video_to_dict(v, with_stats=True) for v in videos])


@content_bp.route("/videos/<int:video_id>/info", methods=["GET"])
@require_auth
def get_video_info(video_id: int):
    video = db.session.scalar(
        select(Video).where(Video.id == video_id, Video.user_id == g.current_user.id)
    )
    if not video:
        return jsonify({"error": "not_found"}), 404
    return jsonify(_video_to_dict(video))


@content_bp.route("/videos/<int:video_id>/sentences", methods=["GET"])
@require_auth
def get_video_sentences(video_id: int):
    video = db.session.scalar(
        select(Video).where(Video.id == video_id, Video.user_id == g.current_user.id)
    )
    if not video:
        return jsonify({"error": "not_found"}), 404

    sentences = db.session.scalars(
        select(Sentence)
        .where(Sentence.video_id == video_id)
        .order_by(Sentence.paragraph_idx, Sentence.sentence_idx)
    ).all()

    return jsonify([
        {
            "id": s.id,
            "text": s.text,
            "paragraph_idx": s.paragraph_idx,
            "sentence_idx": s.sentence_idx,
            "status": s.status,
            "start_time": s.start_time,
            "end_time": s.end_time,
            "translation": s.translation,
            "unknown_count": s.unknown_count,
            "youtube_video_id": video.video_id,
            "video_title": video.title,
            "content_type": video.content_type,
        }
        for s in sentences
    ])


@content_bp.route("/videos", methods=["POST"])
@require_auth
@enforce_video_quota
def add_video():
    """Add a YouTube video by URL. Fetches transcript and creates sentences."""
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    category_id = data.get("category_id")

    if not url:
        return jsonify({"error": "url_required"}), 400

    try:
        video_id_str, title, sentences_data = process_video(url)
    except Exception as e:
        return jsonify({"error": "transcript_failed", "message": str(e)}), 400

    # Check duplicate
    existing = db.session.scalar(
        select(Video).where(Video.user_id == g.current_user.id, Video.url == url)
    )
    if existing:
        return jsonify({"error": "duplicate", "message": "이미 등록된 영상입니다"}), 409

    video = Video(
        user_id=g.current_user.id,
        url=url,
        video_id=video_id_str,
        title=title,
        content_type="youtube",
        category_id=category_id,
    )
    db.session.add(video)
    db.session.flush()

    for para_idx, sent_idx, text, start_t, end_t in sentences_data:
        db.session.add(Sentence(
            video_id=video.id,
            text=text,
            paragraph_idx=para_idx,
            sentence_idx=sent_idx,
            start_time=start_t,
            end_time=end_t,
        ))

    db.session.commit()
    return jsonify(_video_to_dict(video)), 201


@content_bp.route("/videos/<int:video_id>", methods=["DELETE"])
@require_auth
def delete_video(video_id: int):
    video = db.session.scalar(
        select(Video).where(Video.id == video_id, Video.user_id == g.current_user.id)
    )
    if not video:
        return jsonify({"error": "not_found"}), 404
    db.session.delete(video)
    db.session.commit()
    return jsonify({"ok": True})


@content_bp.route("/videos/<int:video_id>/category", methods=["PUT"])
@require_auth
def set_video_category(video_id: int):
    video = db.session.scalar(
        select(Video).where(Video.id == video_id, Video.user_id == g.current_user.id)
    )
    if not video:
        return jsonify({"error": "not_found"}), 404
    data = request.get_json(silent=True) or {}
    category_id = data.get("category_id")
    if category_id is not None:
        cat = db.session.scalar(
            select(Category).where(Category.id == category_id, Category.user_id == g.current_user.id)
        )
        if not cat:
            return jsonify({"error": "category_not_found"}), 404
    video.category_id = category_id
    db.session.commit()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────
# Text content (paste, URL, file upload)
# ─────────────────────────────────────────────────────


@content_bp.route("/content/text", methods=["POST"])
@require_auth
@enforce_video_quota
def add_text_content():
    from app.utils.text_utils import split_into_sentences, group_into_paragraphs, generate_title

    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    title = (data.get("title") or "").strip()
    category_id = data.get("category_id")

    if not text:
        return jsonify({"error": "text_required"}), 400

    if not title:
        title = generate_title(text)

    sentences = split_into_sentences(text)
    if not sentences:
        return jsonify({"error": "no_sentences"}), 400

    sentences = group_into_paragraphs(sentences)

    # Generate synthetic URL to satisfy UNIQUE constraint
    import hashlib
    url_hash = hashlib.md5(text[:500].encode()).hexdigest()[:16]
    synthetic_url = f"text://{g.current_user.id}/{url_hash}"

    existing = db.session.scalar(
        select(Video).where(Video.user_id == g.current_user.id, Video.url == synthetic_url)
    )
    if existing:
        return jsonify({"error": "duplicate"}), 409

    video = Video(
        user_id=g.current_user.id,
        url=synthetic_url,
        title=title,
        content_type="text",
        source_text=text,
        category_id=category_id,
    )
    db.session.add(video)
    db.session.flush()

    for item in sentences:
        # group_into_paragraphs returns (para_idx, sent_idx, text, start, end)
        para_idx, sent_idx, sentence_text = item[0], item[1], item[2]
        start_t = item[3] if len(item) > 3 else None
        end_t = item[4] if len(item) > 4 else None
        db.session.add(Sentence(
            video_id=video.id,
            text=sentence_text,
            paragraph_idx=para_idx,
            sentence_idx=sent_idx,
            start_time=start_t,
            end_time=end_t,
        ))

    db.session.commit()
    return jsonify(_video_to_dict(video)), 201


# ─────────────────────────────────────────────────────
# Sentences (per-user via ownership chain)
# ─────────────────────────────────────────────────────


def _sentence_owned_by_user(sentence_id: int, user_id: str) -> Sentence | None:
    """Return sentence if owned by user via video.user_id chain."""
    return db.session.scalar(
        select(Sentence)
        .join(Video, Sentence.video_id == Video.id)
        .where(Sentence.id == sentence_id, Video.user_id == user_id)
    )


@content_bp.route("/sentences/<int:sentence_id>", methods=["DELETE"])
@require_auth
def delete_sentence(sentence_id: int):
    sentence = _sentence_owned_by_user(sentence_id, g.current_user.id)
    if not sentence:
        return jsonify({"error": "not_found"}), 404
    db.session.delete(sentence)
    db.session.commit()
    return jsonify({"ok": True})


@content_bp.route("/sentences/unknown", methods=["GET"])
@require_auth
def list_unknown_sentences():
    video_id = request.args.get("video_id", type=int)
    stmt = (
        select(Sentence, Video.title, Video.video_id, Video.content_type)
        .join(Video, Sentence.video_id == Video.id)
        .where(Video.user_id == g.current_user.id, Sentence.status == "unknown")
        .order_by(Sentence.unknown_count.desc(), Sentence.id.desc())
    )
    if video_id:
        stmt = stmt.where(Video.id == video_id)
    rows = db.session.execute(stmt).all()
    return jsonify([
        {
            "id": s.id,
            "text": s.text,
            "status": s.status,
            "start_time": s.start_time,
            "end_time": s.end_time,
            "unknown_count": s.unknown_count,
            "video_id": s.video_id,
            "video_title": title,
            "youtube_video_id": yt_id,
            "content_type": ctype,
            "translation": s.translation,
        }
        for s, title, yt_id, ctype in rows
    ])


@content_bp.route("/sentences/known", methods=["GET"])
@require_auth
def list_known_sentences():
    rows = db.session.execute(
        select(Sentence, Video.title)
        .join(Video, Sentence.video_id == Video.id)
        .where(Video.user_id == g.current_user.id, Sentence.status.in_(["known", "mastered"]))
        .order_by(Sentence.id.desc())
    ).all()
    return jsonify([
        {"id": s.id, "text": s.text, "status": s.status, "video_title": title}
        for s, title in rows
    ])
