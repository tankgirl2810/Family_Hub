"""Family Hub: a family front end for the home server: books, films and TV, and more to come."""
import secrets
from datetime import timedelta
from pathlib import Path

from flask import Flask, jsonify

from . import looks
from .config import Config
from .hub import Hub, start_background

__version__ = "0.1.0"


def _secret_key(data_dir):
    """A random key, created once and kept with the data so sign-ins survive restarts."""
    path = Path(data_dir) / "secret.key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


def create_app(config=None):
    config = config or Config()
    static = Path(__file__).parent / "static"
    app = Flask(__name__, static_folder=str(static), static_url_path="/static")
    app.config.update(
        SECRET_KEY=_secret_key(config.data_dir),
        SESSION_COOKIE_NAME="bookhub",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        MAX_CONTENT_LENGTH=64 * 1024 * 1024,
    )
    app.hub = Hub(config)

    from . import api, kosync, opds
    app.register_blueprint(api.bp)
    app.register_blueprint(kosync.bp)
    app.register_blueprint(opds.bp)

    @app.get("/")
    def index():
        html = (static / "index.html").read_text(encoding="utf-8")
        return looks.page(html, looks.current(app.hub.con())), 200, {"Cache-Control": "no-cache"}

    # The visuals at the top of Home, in a sandboxed frame (visuals.py). Nothing here is private: the frame is
    # only code and libraries, and the page sends it the colours and home town itself. No sign-in is asked for,
    # since a sandboxed frame does not carry the sign-in.
    @app.get("/visuals/<ver>/frame.html")
    def visual_frame(ver):
        from . import visuals
        if not visuals.known_version(app.hub, ver):
            return "Not found", 404
        return visuals.frame_html(app.hub, ver), 200, {
            "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff",
            # Its own scripts and the font only; it may fetch public data (weather, earthquakes) from anywhere.
            "Content-Security-Policy": "default-src 'none'; script-src 'self' 'unsafe-inline'; connect-src 'self' https:; "
                                       "style-src 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
                                       "img-src 'self' data: blob:; frame-ancestors 'self'"}

    @app.get("/visuals/<ver>/<path:rel>")
    def visual_file(ver, rel):
        from flask import send_file
        from . import visuals
        path = visuals.file_for(app.hub, ver, rel) if visuals.known_version(app.hub, ver) else None
        if path is None:
            return "Not found", 404
        r = send_file(path, mimetype=visuals.TYPES.get(path.suffix.lower(), "text/plain"), max_age=3600)
        # The frame's origin is "null" (sandboxed), so its fetches of the map outline count as cross-site.
        r.headers["Access-Control-Allow-Origin"] = "*"
        r.headers["X-Content-Type-Options"] = "nosniff"
        return r

    @app.teardown_appcontext
    def finish_db(exc):
        from . import db
        db.finish(app.hub.config.db_path, failed=exc is not None)

    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True, "version": __version__, "library": app.hub.library.available})

    with app.app_context():
        app.hub.load_tagpacks()
        app.hub.sort_new_books()
    if config.background_jobs:
        start_background(app.hub, app)
    return app
