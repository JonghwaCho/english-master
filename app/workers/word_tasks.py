"""Background word meaning cache tasks."""
from __future__ import annotations

import logging

import requests

from app.workers.celery_app import celery, run_in_flask_app_context

logger = logging.getLogger(__name__)


@celery.task(name="app.workers.word_tasks.cache_word_meaning")
@run_in_flask_app_context
def cache_word_meaning(word: str) -> dict:
    """Fetch and cache a word's Korean meaning (AI → dict API fallback)."""
    from app.ai.prompts import build_word_meaning_prompt
    from app.ai.providers import AIError, call_ai
    from app.db.models import WordMeaning
    from app.extensions import db

    word = word.strip().lower()
    if not word:
        return {"ok": False, "error": "empty"}

    existing = db.session.get(WordMeaning, word)
    if existing:
        return {"ok": True, "cached": True}

    meaning = None
    source = None
    try:
        meaning = call_ai(build_word_meaning_prompt(word)).strip()
        source = "ai"
    except AIError:
        try:
            r = requests.get(
                f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}", timeout=5
            )
            if r.status_code == 200:
                data = r.json()
                meanings_list = data[0].get("meanings", [])
                parts = []
                for m in meanings_list[:2]:
                    pos = m.get("partOfSpeech", "")
                    defs = m.get("definitions", [])
                    if defs:
                        parts.append(f"[{pos}] {defs[0].get('definition', '')}")
                meaning = " / ".join(parts) or None
                source = "dict"
        except Exception as e:
            logger.error("Dict API failed for %s: %s", word, e)

    if meaning:
        db.session.merge(WordMeaning(word=word, meaning=meaning, source=source))
        db.session.commit()
        return {"ok": True, "word": word, "meaning": meaning, "source": source}

    return {"ok": False, "error": "all_providers_failed"}
