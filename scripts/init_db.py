"""Initialize the database schema using SQLAlchemy create_all.

For production, use Alembic migrations instead:
    alembic upgrade head
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.extensions import db


def main():
    app = create_app()
    with app.app_context():
        db.create_all()
        print("[✓] Database schema initialized")
        print(f"    DB URL: {app.config['SQLALCHEMY_DATABASE_URI']}")


if __name__ == "__main__":
    main()
