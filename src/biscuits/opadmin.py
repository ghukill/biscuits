"""The server's admin token: account management and name lookups only.

RULE: this is the only module that may use the admin token. It is never used for
reading or writing tasks, and never as a retry after a user's own key gets a 403.
Every task call goes out with that user's key, so OpenProject enforces what they
can see and records who they are. tests/test_login.py checks this.
"""

import secrets
import threading
import time

from . import client, config, writes

NAME_TTL = 300

_lock = threading.Lock()
_names: dict[int, str] = {}
_names_at = 0.0


def _op():
    return client.OP(config.load().op_admin_token, actor={"system": "admin token"})


def whoami():
    return _op().get("/users/me")


def users():
    """Everyone in OpenProject, for linking accounts. Admin screens only."""
    return [{"id": u["id"], "login": u.get("login"), "name": u.get("name"), "status": u.get("status")}
            for u in _op().get_all("/users") if u.get("_type", "User") == "User"]


def name_of(op_user_id):
    """The name of someone on a task the caller can already see, or None.

    Non-admin keys can't list users, so the author of an old comment may be
    unknown to them. Only ever returns the one name asked for.
    """
    global _names, _names_at
    if op_user_id is None:
        return None
    with _lock:
        if time.time() - _names_at > NAME_TTL:
            try:
                _names = {u["id"]: u["name"] for u in users()}
            except (client.OPError, config.ConfigError):
                pass  # keep what we have; a missing name just shows as "Someone"
            _names_at = time.time()
        return _names.get(op_user_id)


def create_user(login, first_name, last_name, email):
    """Create an OpenProject user. They sign in through biscuits, so nobody ever
    needs the OpenProject password generated here."""
    body = {"login": login, "firstName": first_name, "lastName": last_name, "email": email,
            "status": "active", "admin": False, "language": "en",
            "password": secrets.token_urlsafe(24) + "aA1!"}
    logged = {k: v for k, v in body.items() if k != "password"}
    return writes.account_write(_op(), "create-op-user", "POST", "/users", body, logged)


def lock_user(op_user_id):
    """Lock an OpenProject user, which kills their API keys immediately."""
    return writes.account_write(_op(), "lock-op-user", "POST", f"/users/{int(op_user_id)}/lock", {}, {})
