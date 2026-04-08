"""Flask extensions singletons. Initialized in the app factory.

Keeping these out of app/__init__.py prevents circular imports
because blueprints can import them without importing the app.
"""
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate

from app.db.base import db

migrate = Migrate()
cors = CORS()
limiter = Limiter(key_func=get_remote_address, default_limits=["200 per minute"])

__all__ = ["db", "migrate", "cors", "limiter"]
