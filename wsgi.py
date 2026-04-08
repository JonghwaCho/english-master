"""Gunicorn WSGI entry point.

Run with:
    gunicorn --bind 0.0.0.0:5294 --workers 3 wsgi:app

Development:
    python wsgi.py
"""
from app import create_app
from app.config import get_settings

app = create_app()

if __name__ == "__main__":
    settings = get_settings()
    app.run(
        host=settings.host,
        port=settings.port,
        debug=settings.debug,
    )
