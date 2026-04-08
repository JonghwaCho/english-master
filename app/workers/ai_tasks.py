"""Background AI precache tasks."""
from __future__ import annotations

import logging

from app.workers.celery_app import celery, run_in_flask_app_context

logger = logging.getLogger(__name__)

AI_PRECACHE_ACTIONS = ["literal", "grammar", "words"]


@celery.task(name="app.workers.ai_tasks.precache_sentence")
@run_in_flask_app_context
def precache_sentence(sentence_id: int) -> dict:
    """Pre-generate AI results for a sentence (literal, grammar, words)."""
    from sqlalchemy import select
    from app.ai.prompts import build_prompt
    from app.ai.providers import AIError, call_ai
    from app.db.models import AiCache, Sentence
    from app.extensions import db

    sentence = db.session.get(Sentence, sentence_id)
    if not sentence:
        return {"ok": False, "error": "sentence_not_found"}

    results = {}
    for action in AI_PRECACHE_ACTIONS:
        # Skip if already cached
        cached = db.session.scalar(
            select(AiCache).where(
                AiCache.sentence_text == sentence.text, AiCache.action == action
            )
        )
        if cached:
            results[action] = "cached"
            continue
        try:
            result = call_ai(build_prompt(action, sentence.text))
            db.session.add(AiCache(
                sentence_text=sentence.text, action=action, result=result
            ))
            db.session.commit()
            results[action] = "generated"
        except AIError as e:
            logger.error("AI precache failed for %s/%s: %s", sentence_id, action, e)
            results[action] = f"error: {e}"
    return {"ok": True, "sentence_id": sentence_id, "results": results}
