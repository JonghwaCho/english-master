"""Study, review, and word endpoints - all user-isolated."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from flask import Blueprint, g, jsonify, request
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.auth.decorators import require_auth
from app.db.models import Review, Sentence, StudyLog, Video, Word, WordMeaning, WordVideoLink
from app.extensions import db
from app.utils.srs import get_level_name, get_next_review_time, format_next_review

study_bp = Blueprint("study", __name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _user_owns_video(video_id: int, user_id: str) -> bool:
    return db.session.scalar(
        select(Video.id).where(Video.id == video_id, Video.user_id == user_id)
    ) is not None


def _user_owns_sentence(sentence_id: int, user_id: str) -> Optional[Sentence]:
    return db.session.scalar(
        select(Sentence)
        .join(Video, Sentence.video_id == Video.id)
        .where(Sentence.id == sentence_id, Video.user_id == user_id)
    )


def _user_owns_word(word_id: int, user_id: str) -> Optional[Word]:
    return db.session.scalar(
        select(Word).where(Word.id == word_id, Word.user_id == user_id)
    )


# ─────────────────────────────────────────────────────
# Study: Sentence marking
# ─────────────────────────────────────────────────────


@study_bp.route("/study/sentences", methods=["GET"])
@require_auth
def get_study_sentences():
    """Get sentences to study for a given video (new status only)."""
    video_id = request.args.get("video_id", type=int)
    if not video_id:
        return jsonify({"error": "video_id_required"}), 400
    if not _user_owns_video(video_id, g.current_user.id):
        return jsonify({"error": "not_found"}), 404

    sentences = db.session.scalars(
        select(Sentence)
        .where(Sentence.video_id == video_id, Sentence.status == "new")
        .order_by(Sentence.paragraph_idx, Sentence.sentence_idx)
    ).all()

    return jsonify([
        {
            "id": s.id,
            "text": s.text,
            "paragraph_idx": s.paragraph_idx,
            "sentence_idx": s.sentence_idx,
            "start_time": s.start_time,
            "end_time": s.end_time,
            "status": s.status,
        }
        for s in sentences
    ])


@study_bp.route("/study/mark", methods=["POST"])
@require_auth
def mark_sentence():
    data = request.get_json(silent=True) or {}
    sentence_id = data.get("sentence_id")
    status = data.get("status")

    if status not in ("known", "unknown"):
        return jsonify({"error": "invalid_status"}), 400

    sentence = _user_owns_sentence(sentence_id, g.current_user.id)
    if not sentence:
        return jsonify({"error": "not_found"}), 404

    if status == "unknown":
        sentence.status = "unknown"
        sentence.unknown_count = (sentence.unknown_count or 0) + 1
        _schedule_review(sentence.id, "sentence", level=0)
    else:
        sentence.status = "known"
        _schedule_review(sentence.id, "sentence", level=1)

    db.session.add(StudyLog(
        user_id=g.current_user.id,
        item_id=sentence.id,
        item_type="sentence",
        action="study",
        correct=(status == "known"),
    ))
    db.session.commit()
    return jsonify({"ok": True, "status": sentence.status, "unknown_count": sentence.unknown_count})


@study_bp.route("/study/paragraphs/<int:video_id>", methods=["GET"])
@require_auth
def get_paragraphs(video_id: int):
    if not _user_owns_video(video_id, g.current_user.id):
        return jsonify({"error": "not_found"}), 404
    rows = db.session.execute(
        select(Sentence.paragraph_idx, func.count(Sentence.id))
        .where(Sentence.video_id == video_id)
        .group_by(Sentence.paragraph_idx)
        .order_by(Sentence.paragraph_idx)
    ).all()
    return jsonify([{"paragraph_idx": p, "count": c} for p, c in rows])


# ─────────────────────────────────────────────────────
# Reviews (Spaced Repetition)
# ─────────────────────────────────────────────────────


def _schedule_review(item_id: int, item_type: str, level: int):
    """Insert or update a review row."""
    now = _now()
    next_review = get_next_review_time(level, now)

    existing = db.session.scalar(
        select(Review).where(Review.item_id == item_id, Review.item_type == item_type)
    )
    if existing:
        existing.level = level
        existing.next_review = next_review
        existing.last_review = now
        if level == 0:
            existing.streak = 0
    else:
        db.session.add(Review(
            item_id=item_id,
            item_type=item_type,
            level=level,
            next_review=next_review,
            last_review=now,
            streak=0,
        ))


@study_bp.route("/reviews", methods=["GET"])
@require_auth
def list_due_reviews():
    """List review items due for review (level < 7, next_review <= now)."""
    item_type = request.args.get("type", "sentence")
    video_id = request.args.get("video_id", type=int)
    now = _now()

    if item_type == "sentence":
        stmt = (
            select(Review, Sentence, Video)
            .join(Sentence, Review.item_id == Sentence.id)
            .join(Video, Sentence.video_id == Video.id)
            .where(
                Review.item_type == "sentence",
                Review.next_review <= now,
                Review.level < 7,
                Video.user_id == g.current_user.id,
            )
            .order_by(Review.next_review)
        )
        if video_id:
            stmt = stmt.where(Video.id == video_id)
        rows = db.session.execute(stmt).all()
        return jsonify([
            {
                "item_id": r.item_id,
                "item_type": "sentence",
                "text": s.text,
                "level": r.level,
                "level_name": get_level_name(r.level),
                "next_review": r.next_review.isoformat(),
                "video_id": v.id,
                "video_title": v.title,
                "youtube_video_id": v.video_id,
                "content_type": v.content_type,
                "start_time": s.start_time,
                "end_time": s.end_time,
                "translation": s.translation,
            }
            for r, s, v in rows
        ])
    else:
        rows = db.session.execute(
            select(Review, Word)
            .join(Word, Review.item_id == Word.id)
            .where(
                Review.item_type == "word",
                Review.next_review <= now,
                Review.level < 7,
                Word.user_id == g.current_user.id,
            )
            .order_by(Review.next_review)
        ).all()
        return jsonify([
            {
                "item_id": r.item_id,
                "item_type": "word",
                "text": w.word,
                "level": r.level,
                "level_name": get_level_name(r.level),
                "next_review": r.next_review.isoformat(),
            }
            for r, w in rows
        ])


@study_bp.route("/reviews/counts", methods=["GET"])
@require_auth
def review_counts():
    now = _now()
    sent_due = db.session.scalar(
        select(func.count(Review.id))
        .join(Sentence, Review.item_id == Sentence.id)
        .join(Video, Sentence.video_id == Video.id)
        .where(
            Review.item_type == "sentence",
            Review.next_review <= now,
            Review.level < 7,
            Video.user_id == g.current_user.id,
        )
    ) or 0
    word_due = db.session.scalar(
        select(func.count(Review.id))
        .join(Word, Review.item_id == Word.id)
        .where(
            Review.item_type == "word",
            Review.next_review <= now,
            Review.level < 7,
            Word.user_id == g.current_user.id,
        )
    ) or 0
    return jsonify({"sentence_due": sent_due, "word_due": word_due})


@study_bp.route("/reviews/remaining", methods=["GET"])
@require_auth
def reviews_remaining():
    """Count of pending reviews not yet due + next review time."""
    item_type = request.args.get("type", "sentence")
    now = _now()
    if item_type == "sentence":
        q = (
            select(Review)
            .join(Sentence, Review.item_id == Sentence.id)
            .join(Video, Sentence.video_id == Video.id)
            .where(
                Review.item_type == "sentence",
                Review.next_review > now,
                Review.level < 7,
                Video.user_id == g.current_user.id,
            )
            .order_by(Review.next_review)
        )
    else:
        q = (
            select(Review)
            .join(Word, Review.item_id == Word.id)
            .where(
                Review.item_type == "word",
                Review.next_review > now,
                Review.level < 7,
                Word.user_id == g.current_user.id,
            )
            .order_by(Review.next_review)
        )
    all_rows = db.session.scalars(q).all()
    next_time = format_next_review(all_rows[0].next_review.isoformat()) if all_rows else None
    return jsonify({"remaining": len(all_rows), "nextTime": next_time})


@study_bp.route("/reviews/all", methods=["GET"])
@require_auth
def list_all_reviews():
    """Get all review items regardless of schedule (level < 7)."""
    item_type = request.args.get("type", "sentence")

    if item_type == "sentence":
        rows = db.session.execute(
            select(Review, Sentence, Video)
            .join(Sentence, Review.item_id == Sentence.id)
            .join(Video, Sentence.video_id == Video.id)
            .where(
                Review.item_type == "sentence",
                Review.level < 7,
                Video.user_id == g.current_user.id,
            )
            .order_by(Review.level, Review.next_review)
        ).all()
        return jsonify([
            {
                "item_id": r.item_id,
                "item_type": "sentence",
                "text": s.text,
                "level": r.level,
                "video_title": v.title,
                "youtube_video_id": v.video_id,
                "start_time": s.start_time,
                "end_time": s.end_time,
            }
            for r, s, v in rows
        ])
    else:
        rows = db.session.execute(
            select(Review, Word)
            .join(Word, Review.item_id == Word.id)
            .where(
                Review.item_type == "word",
                Review.level < 7,
                Word.user_id == g.current_user.id,
            )
            .order_by(Review.level, Review.next_review)
        ).all()
        return jsonify([
            {"item_id": r.item_id, "item_type": "word", "text": w.word, "level": r.level}
            for r, w in rows
        ])


@study_bp.route("/reviews/process", methods=["POST"])
@require_auth
def process_review():
    """Process a review answer. Advances or resets SRS level."""
    data = request.get_json(silent=True) or {}
    item_id = data.get("item_id")
    item_type = data.get("item_type")
    correct = bool(data.get("correct"))

    if item_type not in ("sentence", "word"):
        return jsonify({"error": "invalid_item_type"}), 400

    # Ownership check
    if item_type == "sentence":
        if not _user_owns_sentence(item_id, g.current_user.id):
            return jsonify({"error": "not_found"}), 404
    else:
        if not _user_owns_word(item_id, g.current_user.id):
            return jsonify({"error": "not_found"}), 404

    review = db.session.scalar(
        select(Review).where(Review.item_id == item_id, Review.item_type == item_type)
    )
    if not review:
        return jsonify({"error": "review_not_found"}), 404

    if correct:
        review.level = min(review.level + 1, 7)
        review.streak += 1
    else:
        review.level = 0
        review.streak = 0

    review.next_review = get_next_review_time(review.level, _now())
    review.last_review = _now()

    # Update status on the target row
    if item_type == "sentence":
        s = db.session.get(Sentence, item_id)
        if s:
            if review.level >= 7:
                s.status = "mastered"
            elif correct:
                s.status = "known"
            else:
                s.status = "unknown"
                s.unknown_count = (s.unknown_count or 0) + 1
    else:
        w = db.session.get(Word, item_id)
        if w:
            if review.level >= 7:
                w.status = "mastered"
            elif correct:
                w.status = "known"
            else:
                w.status = "unknown"

    db.session.add(StudyLog(
        user_id=g.current_user.id,
        item_id=item_id,
        item_type=item_type,
        action="review",
        correct=correct,
    ))
    db.session.commit()

    return jsonify({
        "level": review.level,
        "level_name": get_level_name(review.level),
        "next_review": review.next_review.isoformat(),
    })


@study_bp.route("/reviews/videos", methods=["GET"])
@require_auth
def list_review_videos():
    """List videos that have review items available."""
    item_type = request.args.get("type", "sentence")
    if item_type == "sentence":
        rows = db.session.execute(
            select(Video.id, Video.title, func.count(Review.id).label("count"))
            .join(Sentence, Sentence.video_id == Video.id)
            .join(Review, and_(Review.item_id == Sentence.id, Review.item_type == "sentence"))
            .where(Video.user_id == g.current_user.id, Review.level < 7)
            .group_by(Video.id)
            .order_by(Video.title)
        ).all()
        return jsonify([{"id": vid, "title": title, "count": count} for vid, title, count in rows])
    return jsonify([])


# ─────────────────────────────────────────────────────
# Words
# ─────────────────────────────────────────────────────


@study_bp.route("/words/unknown", methods=["GET"])
@require_auth
def list_unknown_words():
    video_id = request.args.get("video_id", type=int)
    stmt = (
        select(Word, Review.level)
        .outerjoin(Review, and_(Review.item_id == Word.id, Review.item_type == "word"))
        .where(Word.user_id == g.current_user.id, Word.status == "unknown")
        .order_by(Review.level, Word.id.desc())
    )
    if video_id:
        # Filter words that came from this video
        stmt = stmt.join(WordVideoLink, WordVideoLink.word_id == Word.id).where(
            WordVideoLink.video_id == video_id
        )
    rows = db.session.execute(stmt).all()
    return jsonify([
        {"id": w.id, "word": w.word, "status": w.status, "review_level": lvl or 0}
        for w, lvl in rows
    ])


@study_bp.route("/words/known", methods=["GET"])
@require_auth
def list_known_words():
    rows = db.session.scalars(
        select(Word)
        .where(Word.user_id == g.current_user.id, Word.status.in_(["known", "mastered"]))
        .order_by(Word.id.desc())
    ).all()
    return jsonify([{"id": w.id, "word": w.word, "status": w.status} for w in rows])


@study_bp.route("/words/add", methods=["POST"])
@require_auth
def add_word():
    data = request.get_json(silent=True) or {}
    word_text = (data.get("word") or "").strip().lower()
    video_id = data.get("video_id")

    if not word_text:
        return jsonify({"error": "word_required"}), 400

    existing = db.session.scalar(
        select(Word).where(Word.user_id == g.current_user.id, Word.word == word_text)
    )
    if existing:
        word = existing
        if word.status == "known" or word.status == "mastered":
            word.status = "unknown"
    else:
        word = Word(user_id=g.current_user.id, word=word_text, status="unknown")
        db.session.add(word)
        db.session.flush()
        _schedule_review(word.id, "word", level=0)

    # Link to video if provided
    if video_id and _user_owns_video(video_id, g.current_user.id):
        link_exists = db.session.scalar(
            select(WordVideoLink).where(
                WordVideoLink.word_id == word.id, WordVideoLink.video_id == video_id
            )
        )
        if not link_exists:
            db.session.add(WordVideoLink(word_id=word.id, video_id=video_id))

    db.session.commit()
    return jsonify({"id": word.id, "word": word.word, "status": word.status})


@study_bp.route("/words/mark", methods=["POST"])
@require_auth
def mark_word():
    data = request.get_json(silent=True) or {}
    word_id = data.get("word_id")
    status = data.get("status")
    if status not in ("known", "unknown", "mastered"):
        return jsonify({"error": "invalid_status"}), 400

    word = _user_owns_word(word_id, g.current_user.id)
    if not word:
        return jsonify({"error": "not_found"}), 404
    word.status = status
    db.session.commit()
    return jsonify({"ok": True})


@study_bp.route("/words/<int:word_id>", methods=["DELETE"])
@require_auth
def delete_word(word_id: int):
    word = _user_owns_word(word_id, g.current_user.id)
    if not word:
        return jsonify({"error": "not_found"}), 404
    db.session.delete(word)
    # Also delete the review row
    db.session.execute(
        delete(Review).where(Review.item_id == word_id, Review.item_type == "word")
    )
    db.session.commit()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────
# Statistics
# ─────────────────────────────────────────────────────


@study_bp.route("/stats", methods=["GET"])
@require_auth
def get_stats():
    """Overall statistics for the current user."""
    video_id = request.args.get("video_id", type=int)
    user_id = g.current_user.id

    video_stmt = select(Video).where(Video.user_id == user_id)
    if video_id:
        video_stmt = video_stmt.where(Video.id == video_id)
    total_videos = db.session.scalar(select(func.count()).select_from(video_stmt.subquery())) or 0

    sent_base = select(Sentence).join(Video, Sentence.video_id == Video.id).where(Video.user_id == user_id)
    if video_id:
        sent_base = sent_base.where(Video.id == video_id)

    total_sentences = db.session.scalar(select(func.count()).select_from(sent_base.subquery())) or 0
    known_sentences = db.session.scalar(
        select(func.count()).select_from(sent_base.where(Sentence.status == "known").subquery())
    ) or 0
    unknown_sentences = db.session.scalar(
        select(func.count()).select_from(sent_base.where(Sentence.status == "unknown").subquery())
    ) or 0
    mastered_sentences = db.session.scalar(
        select(func.count()).select_from(sent_base.where(Sentence.status == "mastered").subquery())
    ) or 0
    new_sentences = db.session.scalar(
        select(func.count()).select_from(sent_base.where(Sentence.status == "new").subquery())
    ) or 0

    total_words = db.session.scalar(
        select(func.count(Word.id)).where(Word.user_id == user_id)
    ) or 0
    known_words = db.session.scalar(
        select(func.count(Word.id)).where(
            Word.user_id == user_id, Word.status.in_(["known", "mastered"])
        )
    ) or 0

    now = _now()
    due_reviews = db.session.scalar(
        select(func.count(Review.id))
        .join(Sentence, Review.item_id == Sentence.id)
        .join(Video, Sentence.video_id == Video.id)
        .where(
            Review.item_type == "sentence",
            Review.next_review <= now,
            Review.level < 7,
            Video.user_id == user_id,
        )
    ) or 0
    word_due_reviews = db.session.scalar(
        select(func.count(Review.id))
        .join(Word, Review.item_id == Word.id)
        .where(
            Review.item_type == "word",
            Review.next_review <= now,
            Review.level < 7,
            Word.user_id == user_id,
        )
    ) or 0

    return jsonify({
        "total_videos": total_videos,
        "total_sentences": total_sentences,
        "total_words": total_words,
        "known_sentences": known_sentences,
        "mastered_sentences": mastered_sentences,
        "unknown_sentences": unknown_sentences,
        "new_sentences": new_sentences,
        "due_reviews": due_reviews,
        "word_due_reviews": word_due_reviews,
        "known_words": known_words,
    })


@study_bp.route("/usage", methods=["GET"])
@require_auth
def get_usage():
    from app.quota import get_usage_summary
    return jsonify(get_usage_summary(g.current_user.id, g.current_user.tier))
