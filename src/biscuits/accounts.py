"""biscuits accounts: username + password, each linked to an OpenProject user
and holding that user's API key, encrypted with [auth] encryption_key.

Keys are encrypted so that a leaked database alone isn't enough: the encryption
key lives in the config file.
"""

import re
import secrets
import threading
import time
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select
from werkzeug.security import check_password_hash, generate_password_hash

from . import client, config, db, snapshot
from .db import User

MIN_PASSWORD = 10
_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._@-]{1,63}$")
# Checked when the username is unknown, so that takes as long as a wrong password.
_DUMMY_HASH = generate_password_hash(secrets.token_urlsafe(16))


class AccountError(RuntimeError):
    pass


def _fernet():
    key = config.load().encryption_key
    if not key:
        raise config.ConfigError("[auth] encryption_key is missing. Run `biscuits config add-auth-keys`.")
    return Fernet(key)


def _now():
    return datetime.now(timezone.utc)


def temp_password():
    return secrets.token_urlsafe(12)


def _check_password(password):
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        raise AccountError(f"Passwords need at least {MIN_PASSWORD} characters")


def _need(s, user_id) -> User:
    user = s.get(User, user_id)
    if user is None:
        raise AccountError("No such account")
    return user


def check_new(username, op_user_id=None):
    """Raise AccountError if an account can't be created with these. Returns the clean username."""
    username = (username or "").strip().lower()
    if not _USERNAME_RE.match(username):
        raise AccountError("Usernames are 2-64 characters: letters, digits, and . _ @ -")
    with db.session() as s:
        if s.scalar(select(User).where(User.username == username)):
            raise AccountError(f"There is already an account called {username}")
        if op_user_id is not None and s.scalar(select(User).where(User.op_user_id == op_user_id)):
            raise AccountError("That OpenProject user is already linked to an account")
    return username


def create(username, *, display_name=None, is_admin=False, op_user_id=None, op_user_name=None,
           password=None, must_change=True):
    """Create an account. Returns (user, password), generating a password if none is given."""
    username = check_new(username, op_user_id)
    password = password or temp_password()
    _check_password(password)
    with db.session() as s:
        user = User(username=username, display_name=display_name or username, is_admin=is_admin,
                    password_hash=generate_password_hash(password), must_change_password=must_change,
                    op_user_id=op_user_id, op_user_name=op_user_name, created_at=_now())
        s.add(user)
    return user, password


def get(user_id):
    with db.session() as s:
        return s.get(User, user_id)


def by_username(username):
    with db.session() as s:
        return s.scalar(select(User).where(User.username == (username or "").strip().lower()))


def all_users():
    with db.session() as s:
        return list(s.scalars(select(User).order_by(User.username)))


def count_admins():
    with db.session() as s:
        return s.scalar(select(func.count()).select_from(User).where(User.is_admin, User.disabled_at.is_(None)))


def authenticate(username, password):
    """The account, if the credentials are right and it isn't disabled. Else None."""
    user = by_username(username)
    ok = check_password_hash(user.password_hash if user else _DUMMY_HASH, password or "")
    if not (ok and user and user.disabled_at is None):
        return None
    with db.session() as s:
        s.get(User, user.id).last_login_at = _now()
    return user


def change_password(user_id, current, new):
    with db.session() as s:
        user = _need(s, user_id)
        if not check_password_hash(user.password_hash, current or ""):
            raise AccountError("Your current password isn't right")
        if new == current:
            raise AccountError("Pick a password different from the current one")
        _check_password(new)
        user.password_hash = generate_password_hash(new)
        user.must_change_password = False
        user.session_version += 1  # signs out their other browsers
        return user


def reset_password(user_id):
    """Admin reset: returns a temporary password, and signs the person out everywhere."""
    password = temp_password()
    with db.session() as s:
        user = _need(s, user_id)
        user.password_hash = generate_password_hash(password)
        user.must_change_password = True
        user.session_version += 1
    return password


def update(user_id, *, display_name=None, is_admin=None):
    with db.session() as s:
        user = _need(s, user_id)
        if display_name:
            user.display_name = display_name.strip()
        if is_admin is not None:
            if user.is_admin and not is_admin and user.disabled_at is None and count_admins() <= 1:
                raise AccountError("This is the last admin account")
            user.is_admin = is_admin
        return user


def validate_key(key, op_user_id):
    """Make sure a key belongs to the OpenProject user it's being saved for, so a
    mis-pasted key can't attribute someone's edits to the wrong person.
    Returns that user's name."""
    key = (key or "").strip()
    if not key:
        raise AccountError("Paste the API key")
    try:
        me = client.OP(key).get("/users/me")
    except client.OPError as e:
        if e.status in (401, 403):
            raise AccountError("OpenProject didn't accept that key") from e
        raise AccountError(f"Couldn't check the key with OpenProject: {e}") from e
    if me.get("id") != op_user_id:
        raise AccountError(
            f"That key belongs to {me.get('name') or 'someone else'} (OpenProject user "
            f"#{me.get('id')}), not OpenProject user #{op_user_id}")
    return me.get("name")


def set_key(user_id, key, op_user_id=None):
    """Save a user's API key after validating it. op_user_id links or relinks them."""
    with db.session() as s:
        user = _need(s, user_id)
        target = op_user_id if op_user_id is not None else user.op_user_id
        if target is None:
            raise AccountError("Link this account to an OpenProject user first")
        clash = s.scalar(select(User).where(User.op_user_id == target, User.id != user_id))
        if clash:
            raise AccountError(f"That OpenProject user is already linked to {clash.username}")
        name = validate_key(key, target)
        relinked = user.op_user_id != target
        user.op_user_id, user.op_user_name = target, name
        user.op_key_enc = _fernet().encrypt(key.strip().encode()).decode()
    if relinked:
        snapshot.forget(user_id)
    return get(user_id)


def get_key(user):
    """The user's API key, or None if there isn't one or it can't be decrypted."""
    if not user.op_key_enc:
        return None
    try:
        return _fernet().decrypt(user.op_key_enc.encode()).decode()
    except InvalidToken:
        return None


def mask(key):
    return f"{key[:6]}…{key[-4:]}" if key and len(key) > 14 else ("…" if key else None)


def set_disabled(user_id, disabled):
    with db.session() as s:
        user = _need(s, user_id)
        if disabled and user.is_admin and user.disabled_at is None and count_admins() <= 1:
            raise AccountError("This is the last admin account")
        user.disabled_at = _now() if disabled else None
        user.session_version += 1
    snapshot.forget(user_id)
    return get(user_id)


class RateLimiter:
    """Failed logins, per client address + username and per client address.

    In memory: it resets on restart, which is fine for slowing down guessing on a
    single-process server.
    """

    WINDOW = 300
    MAX_PER_NAME = 5
    MAX_PER_ADDRESS = 20

    def __init__(self):
        self._lock = threading.Lock()
        self._fails: dict[tuple, list[float]] = {}

    def _recent(self, key, now):
        hits = [t for t in self._fails.get(key, []) if now - t < self.WINDOW]
        if hits:
            self._fails[key] = hits
        else:
            self._fails.pop(key, None)
        return hits

    def blocked(self, address, username):
        now = time.time()
        with self._lock:
            return (len(self._recent((address, username), now)) >= self.MAX_PER_NAME
                    or len(self._recent((address,), now)) >= self.MAX_PER_ADDRESS)

    def fail(self, address, username):
        now = time.time()
        with self._lock:
            for key in ((address, username), (address,)):
                self._fails.setdefault(key, []).append(now)

    def succeed(self, address, username):
        with self._lock:
            self._fails.pop((address, username), None)


limiter = RateLimiter()
