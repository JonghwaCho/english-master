"""Background sentence translation tasks."""
from __future__ import annotations

import logging

from app.workers.celery_app import celery, run_in_flask_app_context

logger = logging.getLogger(__name__)


@celery.task(name="app.workers.translation_tasks.translate_sentence")
@run_in_flask_app_context
def translate_sentence(sentence_id: int) -> dict:
    """Translate a single sentence to Korean and store in DB."""
    from app.ai.providers import AIError, call_ai
    from app.db.models import Sentence
    from app.extensions import db

    sentence = db.session.get(Sentence, sentence_id)
    if not sentence or sentence.translation:
        return {"ok": False, "reason": "not_found_or_already_translated"}

    prompt = f"다음 영어 문장을 자연스러운 한국어로 번역해주세요. 번역만 출력하세요.\n\n{sentence.text}"
    try:
        translation = call_ai(prompt).strip()
        sentence.translation = translation
        db.session.commit()
        return {"ok": True, "translation": translation}
    except AIError as e:
        logger.error("Translation failed for %s: %s", sentence_id, e)
        return {"ok": False, "error": str(e)}
