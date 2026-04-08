"""AI endpoint: cached AI actions with per-user quota enforcement."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request
from sqlalchemy import select

from app.ai.prompts import build_prompt, build_word_meaning_prompt
from app.ai.providers import AIError, call_ai
from app.auth.decorators import require_auth
from app.db.models import AiCache, AiUsageLog, WordMeaning
from app.extensions import db
from app.quota import enforce_ai_quota

logger = logging.getLogger(__name__)

ai_bp = Blueprint("ai", __name__)


def _log_usage(user_id: str, action: str, provider: str, success: bool, error: str = None):
    db.session.add(AiUsageLog(
        user_id=user_id,
        provider=provider,
        action=action,
        success=success,
        error_message=error,
    ))
    # Commit handled by enforce_ai_quota or caller


@ai_bp.route("/action", methods=["POST"])
@require_auth
@enforce_ai_quota
def ai_action():
    """Run an AI action for a sentence with caching.

    Request: {action: 'literal'|'grammar'|'words'|'similar'|'quiz', sentence: str}
    """
    data = request.get_json(silent=True) or {}
    action = data.get("action", "")
    sentence = (data.get("sentence") or "").strip()

    if action not in ("literal", "grammar", "words", "similar", "quiz"):
        return jsonify({"error": "invalid_action"}), 400
    if not sentence:
        return jsonify({"error": "sentence_required"}), 400

    # Cache lookup (shared across users)
    if action != "quiz":  # quizzes are regenerated each time
        cached = db.session.scalar(
            select(AiCache).where(
                AiCache.sentence_text == sentence, AiCache.action == action
            )
        )
        if cached:
            return jsonify({"action": action, "result": cached.result, "cached": True})

    # Call AI
    from app.config import get_settings
    provider = get_settings().ai_provider_default
    try:
        result = call_ai(build_prompt(action, sentence), provider=provider)
    except AIError as e:
        _log_usage(g.current_user.id, action, provider, success=False, error=str(e))
        db.session.commit()
        return jsonify({"error": "ai_failed", "message": str(e)}), 503

    # Cache
    if action != "quiz":
        db.session.add(AiCache(sentence_text=sentence, action=action, result=result))

    _log_usage(g.current_user.id, action, provider, success=True)
    db.session.commit()
    return jsonify({"action": action, "result": result, "cached": False})


@ai_bp.route("/word-meaning", methods=["GET"])
@require_auth
@enforce_ai_quota
def word_meaning():
    """Get Korean meaning for a single English word (cached across all users)."""
    word = (request.args.get("word") or "").strip().lower()
    if not word:
        return jsonify({"error": "word_required"}), 400

    cached = db.session.get(WordMeaning, word)
    if cached:
        return jsonify({"word": word, "meaning": cached.meaning, "source": cached.source, "cached": True})

    # Try AI first
    from app.config import get_settings
    provider = get_settings().ai_provider_default
    try:
        meaning = call_ai(build_word_meaning_prompt(word), provider=provider).strip()
        source = "ai"
    except AIError:
        # Fallback to free dictionary API
        import requests
        try:
            r = requests.get(
                f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}", timeout=5
            )
            if r.status_code == 200:
                data = r.json()
                meanings = data[0].get("meanings", [])
                parts = []
                for m in meanings[:2]:
                    pos = m.get("partOfSpeech", "")
                    defs = m.get("definitions", [])
                    if defs:
                        parts.append(f"[{pos}] {defs[0].get('definition', '')}")
                meaning = " / ".join(parts) or "(뜻 없음)"
                source = "dict"
            else:
                return jsonify({"error": "not_found"}), 404
        except Exception as e:
            return jsonify({"error": "lookup_failed", "message": str(e)}), 503

    # Cache
    cached = WordMeaning(word=word, meaning=meaning, source=source)
    db.session.merge(cached)
    db.session.commit()

    return jsonify({"word": word, "meaning": meaning, "source": source, "cached": False})


@ai_bp.route("/word-meanings-batch", methods=["POST"])
@require_auth
def word_meanings_batch():
    """Batch lookup - does NOT count against AI quota (cache-only, no fresh calls per word)."""
    data = request.get_json(silent=True) or {}
    words = data.get("words", [])
    if not isinstance(words, list):
        return jsonify({"error": "words_must_be_array"}), 400

    results = {}
    for w in words:
        w_lower = (w or "").strip().lower()
        if not w_lower:
            continue
        cached = db.session.get(WordMeaning, w_lower)
        if cached:
            results[w_lower] = {"meaning": cached.meaning, "source": cached.source}
        else:
            results[w_lower] = {"meaning": None, "source": None}

    return jsonify(results)


@ai_bp.route("/usage", methods=["GET"])
@require_auth
def get_ai_usage():
    """Return current user's AI usage summary."""
    from app.quota import get_usage_summary
    return jsonify(get_usage_summary(g.current_user.id, g.current_user.tier))
