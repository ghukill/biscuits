"""The biscuits Flask application."""

from flask import Flask, jsonify, redirect

from . import __version__, client, config
from .bp import bp


def create_app() -> Flask:
    cfg = config.load()
    # All static files belong to the blueprint, which may be mounted at "/".
    app = Flask(__name__, static_folder=None)
    app.register_blueprint(bp, url_prefix=cfg.base_path or None)

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok", version=__version__)

    if cfg.base_path:
        @app.get("/")
        def root():
            return redirect(cfg.base_path + "/")

    @app.errorhandler(config.ConfigError)
    def config_error(e):
        return jsonify(ok=False, error=str(e)), 500

    @app.errorhandler(client.OPError)
    def op_error(e):
        return jsonify(ok=False, error=str(e)), 502

    return app
