"""Login, the gate in front of every route, and the account and admin APIs.

Every POST needs the X-Biscuits header, including login: browsers won't send a
custom header cross-site without a CORS preflight, which we never approve. The
pages post with fetch, so there are no plain HTML forms to protect separately.

  GET  /login, /password             Sign-in and change-password pages
  POST /api/login, /api/logout
  GET  /api/account                  Your account
  GET  /api/account/key              Your OpenProject key, unmasked
  POST /api/account/password         Change your password
  GET  /api/admin/users              Accounts, and POST to create one
  POST /api/admin/users/<id>         Change name or admin flag
  GET/POST /api/admin/users/<id>/key  Reveal or save a key (saving validates it)
  POST /api/admin/users/<id>/password  Reset to a new temporary password
  POST /api/admin/users/<id>/disabled  Disable (optionally locking in OP) or enable
  GET  /api/admin/op-users           OpenProject users, for linking
"""

from functools import wraps
from urllib.parse import urlparse

from flask import jsonify, redirect, render_template, request, session, url_for
from flask_login import LoginManager, current_user, login_user, logout_user

from . import accounts, client, config, opadmin, writes
from .bp import bp

login_manager = LoginManager()
login_manager.session_protection = None  # the session cookie settings do this job


@login_manager.user_loader
def _load_user(session_id):
    try:
        user_id, version = (int(p) for p in session_id.split(":"))
    except ValueError:
        return None
    user = accounts.get(user_id)
    if user is None or user.disabled_at is not None or user.session_version != version:
        return None
    return user


PUBLIC = {"biscuits.static", "biscuits.login_page", "biscuits.api_login"}
ALLOWED_DURING_FORCED_CHANGE = {"biscuits.password_page", "biscuits.api_password", "biscuits.api_logout"}


def _is_api():
    return "/api/" in request.path


@bp.before_request
def _gate():
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("X-Biscuits") != "1":
        return jsonify(ok=False, error="Missing X-Biscuits header"), 403
    if request.endpoint in PUBLIC:
        return None
    if not current_user.is_authenticated:
        if _is_api():
            return jsonify(ok=False, code="login", error="Please sign in."), 401
        return redirect(url_for("biscuits.login_page", next=request.full_path.rstrip("?")))
    if current_user.must_change_password and request.endpoint not in ALLOWED_DURING_FORCED_CHANGE:
        if _is_api():
            return jsonify(ok=False, code="must_change", error="Please choose a new password first."), 403
        return redirect(url_for("biscuits.password_page"))
    return None


def _safe_next(target):
    """Only ever redirect within biscuits after login."""
    home = url_for("biscuits.index")
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return home
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return home
    return target


@bp.get("/login")
def login_page():
    if current_user.is_authenticated:
        return redirect(_safe_next(request.args.get("next")))
    return render_template("biscuits/auth.html", mode="login", next=_safe_next(request.args.get("next")))


@bp.get("/password")
def password_page():
    return render_template("biscuits/auth.html", mode="password", next=url_for("biscuits.index"),
                           forced=current_user.must_change_password)


@bp.post("/api/login")
def api_login():
    body = request.get_json(silent=True) or {}
    username = str(body.get("username") or "").strip().lower()
    address = request.remote_addr or "?"
    if accounts.limiter.blocked(address, username):
        return jsonify(ok=False, error="Too many failed attempts. Please wait a few minutes."), 429
    user = accounts.authenticate(username, str(body.get("password") or ""))
    if user is None:
        accounts.limiter.fail(address, username)
        return jsonify(ok=False, error="That username and password don't match."), 401
    accounts.limiter.succeed(address, username)
    session.permanent = True
    login_user(user)
    return jsonify(ok=True, must_change=user.must_change_password)


@bp.post("/api/logout")
def api_logout():
    logout_user()
    return jsonify(ok=True)


def _account_json(user, admin_view=False):
    out = {
        "id": user.id, "username": user.username, "display_name": user.display_name,
        "is_admin": user.is_admin, "must_change_password": user.must_change_password,
        "op_user_id": user.op_user_id, "op_user_name": user.op_user_name,
        "key_masked": accounts.mask(accounts.get_key(user)),
        "key_problem": bool(user.op_key_enc) and accounts.get_key(user) is None,
    }
    if admin_view:
        out.update(
            disabled=user.disabled_at is not None,
            last_login_at=user.last_login_at.isoformat() if user.last_login_at else None,
            created_at=user.created_at.isoformat(),
        )
    return out


def _secret(payload):
    resp = jsonify(payload)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.get("/api/account")
def api_account():
    return jsonify(ok=True, account=_account_json(current_user))


@bp.get("/api/account/key")
def api_account_key():
    return _secret({"ok": True, "key": accounts.get_key(current_user)})


@bp.post("/api/account/password")
def api_password():
    body = request.get_json(silent=True) or {}
    try:
        user = accounts.change_password(current_user.id, body.get("current"), body.get("new"))
    except accounts.AccountError as e:
        return jsonify(ok=False, error=str(e)), 400
    # Changing the password signs out other browsers; keep this one signed in.
    session.permanent = True
    login_user(user)
    return jsonify(ok=True)


# ---------- admin ----------

def admin_only(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
            return jsonify(ok=False, error="Only biscuits admins can do this."), 403
        try:
            return fn(*args, **kwargs)
        except accounts.AccountError as e:
            return jsonify(ok=False, error=str(e)), 400
        except writes.WriteError as e:
            return jsonify(ok=False, error=f"OpenProject: {e}"), 502
    return wrapper


def _token_command(op_user_id):
    token_name = "biscuits"
    code = f"puts Token::API.create!(user: User.find({op_user_id}), token_name: '{token_name}').plain_value"
    return f'docker exec {config.load().op_container} bundle exec rails runner "{code}"'


@bp.get("/api/admin/users")
@admin_only
def api_admin_users():
    return jsonify(ok=True, users=[_account_json(u, admin_view=True) for u in accounts.all_users()],
                   token_template=_token_command("ID"))


@bp.get("/api/admin/op-users")
@admin_only
def api_admin_op_users():
    try:
        return jsonify(ok=True, users=opadmin.users())
    except client.OPError as e:
        return jsonify(ok=False, error=f"The admin token couldn't list OpenProject users: {e}"), 502


@bp.post("/api/admin/users")
@admin_only
def api_admin_create():
    body = request.get_json(silent=True) or {}
    op_user_id, op_user_name = body.get("op_user_id"), None
    new_op = body.get("new_op_user")
    accounts.check_new(body.get("username"), op_user_id)  # before anything is created in OP
    if new_op:
        created = opadmin.create_user(new_op.get("login"), new_op.get("first_name"),
                                      new_op.get("last_name"), new_op.get("email"))
        op_user_id, op_user_name = created["id"], created.get("name")
    elif op_user_id is not None:
        op_user_id = int(op_user_id)
        op_user_name = next((u["name"] for u in opadmin.users() if u["id"] == op_user_id), None)
    user, password = accounts.create(
        body.get("username"), display_name=body.get("display_name") or op_user_name,
        is_admin=bool(body.get("is_admin")), op_user_id=op_user_id, op_user_name=op_user_name)
    return _secret({"ok": True, "user": _account_json(user, admin_view=True), "password": password})


@bp.post("/api/admin/users/<int:user_id>")
@admin_only
def api_admin_update(user_id):
    body = request.get_json(silent=True) or {}
    if user_id == current_user.id and body.get("is_admin") is False:
        raise accounts.AccountError("You can't remove your own admin rights")
    user = accounts.update(user_id, display_name=body.get("display_name"), is_admin=body.get("is_admin"))
    return jsonify(ok=True, user=_account_json(user, admin_view=True))


@bp.get("/api/admin/users/<int:user_id>/key")
@admin_only
def api_admin_key(user_id):
    user = accounts.get(user_id)
    if user is None:
        raise accounts.AccountError("No such account")
    return _secret({"ok": True, "key": accounts.get_key(user)})


@bp.post("/api/admin/users/<int:user_id>/key")
@admin_only
def api_admin_set_key(user_id):
    body = request.get_json(silent=True) or {}
    op_user_id = body.get("op_user_id")
    user = accounts.set_key(user_id, body.get("key"), int(op_user_id) if op_user_id is not None else None)
    return jsonify(ok=True, user=_account_json(user, admin_view=True))


@bp.post("/api/admin/users/<int:user_id>/password")
@admin_only
def api_admin_reset(user_id):
    return _secret({"ok": True, "password": accounts.reset_password(user_id)})


@bp.post("/api/admin/users/<int:user_id>/disabled")
@admin_only
def api_admin_disable(user_id):
    body = request.get_json(silent=True) or {}
    disabled = bool(body.get("disabled"))
    if disabled and user_id == current_user.id:
        raise accounts.AccountError("You can't disable your own account")
    user = accounts.set_disabled(user_id, disabled)
    op_locked = False
    if disabled and body.get("lock_in_openproject") and user.op_user_id:
        opadmin.lock_user(user.op_user_id)
        op_locked = True
    return jsonify(ok=True, user=_account_json(user, admin_view=True), op_locked=op_locked)

