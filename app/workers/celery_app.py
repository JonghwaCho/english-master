"""Celery application instance and configuration.

Run worker:
    celery -A app.workers.celery_app worker --loglevel=info

Run beat (scheduler):
    celery -A app.workers.celery_app beat --loglevel=info
"""
from __future__ import annotations

from celery import Celery

from app.config import get_settings


def make_celery() -> Celery:
    settings = get_settings()
    celery = Celery(
        "english_master",
        broker=settings.celery_broker_url,
        backend=settings.celery_result_backend,
        include=[
            "app.workers.ai_tasks",
            "app.workers.translation_tasks",
            "app.workers.word_tasks",
        ],
    )
    celery.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="Asia/Seoul",
        enable_utc=True,
        task_track_started=True,
        task_time_limit=300,  # 5 min hard limit
        task_soft_time_limit=240,  # 4 min soft limit
        worker_max_tasks_per_child=1000,  # restart worker after N tasks (memory leak mitigation)
        # Queues
        task_routes={
            "app.workers.ai_tasks.*": {"queue": "ai"},
            "app.workers.translation_tasks.*": {"queue": "translation"},
            "app.workers.word_tasks.*": {"queue": "words"},
        },
        task_default_queue="default",
    )
    return celery


celery = make_celery()


def run_in_flask_app_context(fn):
    """Decorator: run task inside Flask app context so db/config work."""
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        from app import create_app
        app = create_app()
        with app.app_context():
            return fn(*args, **kwargs)
    return wrapper
