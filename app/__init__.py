"""Flask application factory.

Entry point:
    from app import create_app
    app = create_app()

For gunicorn:
    gunicorn --bind 0.0.0.0:5294 wsgi:app
"""
from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask, jsonify, render_template, send_from_directory

from app.config import Settings, get_settings
from app.extensions import cors, db, limiter, migrate


def create_app(settings: Settings | None = None) -> Flask:
    """Application factory."""
    settings = settings or get_settings()

    app = Flask(
        __name__,
        static_folder=str(Path(__file__).parent / "static"),
        template_folder=str(Path(__file__).parent.parent / "templates"),
    )

    # Core Flask config
    app.config["SECRET_KEY"] = settings.secret_key
    app.config["SQLALCHEMY_DATABASE_URI"] = settings.database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ECHO"] = settings.sqlalchemy_echo
    app.config["MAX_CONTENT_LENGTH"] = settings.max_content_length_mb * 1024 * 1024
    app.config["SETTINGS"] = settings

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    cors.init_app(
        app,
        resources={r"/api/*": {"origins": settings.cors_origins_list}},
        supports_credentials=True,
    )
    limiter.init_app(app)
    limiter.storage_uri = settings.rate_limit_storage_uri

    # Logging
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Sentry (production error tracking)
    if settings.sentry_dsn and settings.is_production:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            integrations=[FlaskIntegration()],
            environment=settings.env,
            traces_sample_rate=0.1,
        )

    # Register blueprints
    from app.auth.routes import auth_bp
    from app.auth.oauth import oauth_bp
    from app.users.routes import users_bp
    from app.content.routes import content_bp
    from app.study.routes import study_bp
    from app.ai.routes import ai_bp

    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(oauth_bp, url_prefix="/api/auth/oauth")
    app.register_blueprint(users_bp, url_prefix="/api/users")
    app.register_blueprint(content_bp, url_prefix="/api")
    app.register_blueprint(study_bp, url_prefix="/api")
    app.register_blueprint(ai_bp, url_prefix="/api/ai")

    # Health check
    @app.route("/healthz")
    def healthz():
        """Liveness + readiness probe for load balancers."""
        try:
            db.session.execute(db.text("SELECT 1"))
            return jsonify({"status": "ok", "db": "ok"}), 200
        except Exception as e:
            return jsonify({"status": "error", "db": str(e)}), 503

    # Frontend (serves templates/index.html)
    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/login")
    @app.route("/signup")
    def auth_pages():
        return render_template("auth.html")

    @app.route("/terms")
    def terms_page():
        return render_template("terms.html") if (Path(app.template_folder) / "terms.html").exists() else (
            "<h1>이용약관</h1><p>(준비 중)</p>"
        )

    @app.route("/privacy")
    def privacy_page():
        return render_template("privacy.html") if (Path(app.template_folder) / "privacy.html").exists() else (
            "<h1>개인정보처리방침</h1><p>(준비 중)</p>"
        )

    # Security headers
    @app.after_request
    def add_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    # Error handlers
    @app.errorhandler(400)
    def bad_request(e):
        return jsonify({"error": "Bad Request", "message": str(e)}), 400

    @app.errorhandler(401)
    def unauthorized(e):
        return jsonify({"error": "Unauthorized", "message": "인증이 필요합니다"}), 401

    @app.errorhandler(403)
    def forbidden(e):
        return jsonify({"error": "Forbidden", "message": "권한이 없습니다"}), 403

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "Not Found"}), 404

    @app.errorhandler(429)
    def ratelimit_exceeded(e):
        return jsonify({"error": "Too Many Requests", "message": str(e.description)}), 429

    @app.errorhandler(500)
    def server_error(e):
        return jsonify({"error": "Internal Server Error"}), 500

    return app
