"""The biscuits Flask application."""

from datetime import timedelta

from flask import Flask, jsonify, redirect, request
from flask.sessions import SecureCookieSessionInterface

from . import __version__, auth, build, client, config
from .bp import NoKey, bp

SESSION_DAYS = 14


class _SessionInterface(SecureCookieSessionInterface):
    """Mark the cookie Secure whenever this request really came over HTTPS, so
    biscuits works on plain HTTP too. See [server] trusted_proxy."""

    def get_cookie_secure(self, app):
        return request.is_secure


def create_app() -> Flask:
    cfg = config.load()
    if not (cfg.secret_key and cfg.encryption_key):
        raise config.ConfigError(
            f"{cfg.path} has no [auth] secrets, which login needs. "
            "Add them with `biscuits config add-auth-keys`.")
    # All static files belong to the blueprint, which may be mounted at "/".
    app = Flask(__name__, static_folder=None)
    app.secret_key = cfg.secret_key
    app.session_interface = _SessionInterface()
    app.config.update(
        # Cookies aren't separated by port, so don't share the default name with
        # anything else on this host.
        SESSION_COOKIE_NAME="biscuits_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_PATH=cfg.base_path or "/",
        PERMANENT_SESSION_LIFETIME=timedelta(days=SESSION_DAYS),
    )
    auth.login_manager.init_app(app)
    app.register_blueprint(bp, url_prefix=cfg.base_path or None)

    @app.context_processor
    def _build_label():
        # Re-read per request, so renaming the environment needs no restart.
        env = config.load().environment
        v = build.version_label()
        return {"build_label": f"{env} @ {v}" if env else f"v{v}"}

    @app.get("/healthz")
    def healthz():
        # `version` is the release version, which deploy_release checks.
        return jsonify(status="ok", version=__version__, build=build.version_label())

    if cfg.base_path:
        @app.get("/")
        def root():
            return redirect(cfg.base_path + "/")

    @app.errorhandler(config.ConfigError)
    def config_error(e):
        return jsonify(ok=False, error=str(e)), 500

    @app.errorhandler(NoKey)
    def no_key(_):
        return jsonify(ok=False, code="no_key", error=(
            "Your account isn't connected to OpenProject yet. Ask your biscuits admin to set it up.")), 409

    @app.errorhandler(client.OPError)
    def op_error(e):
        if e.status == 401:
            # Their OP key was revoked, or they were locked in OP. This isn't a
            # 401 to the browser, which would mean "log in to biscuits".
            return jsonify(ok=False, code="key_invalid", error=(
                "OpenProject isn't accepting your account's connection any more. "
                "Ask your biscuits admin to fix it.")), 409
        if e.status in (403, 404):
            return jsonify(ok=False, error=str(e)), e.status
        return jsonify(ok=False, error=str(e)), 502

    return app
