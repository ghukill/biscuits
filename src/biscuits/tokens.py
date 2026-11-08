"""API tokens: how a program (an agent session, the Mattermost bots) signs in to
biscuits' MCP endpoint as a biscuits account.

A token acts exactly as its account does: OpenProject sees that account's own
key, and the [writes] gate and write log apply. Only a SHA-256 of each token is
stored. Tokens are 256 random bits, so a plain hash is enough, and the token
itself is shown once, when it's made.
"""

import hashlib
import re
import secrets
from datetime import datetime, timezone

from sqlalchemy import select

from . import db
from .db import ApiToken, User

PREFIX = "bsk_"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,63}$")


class TokenError(RuntimeError):
    pass


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now():
    return datetime.now(timezone.utc)


def create(user_id: int, name: str) -> tuple[ApiToken, str]:
    """Make a token for an account. Returns (row, token); the token isn't kept."""
    name = (name or "").strip()
    if not _NAME_RE.match(name):
        raise TokenError("Token names are 1-64 characters: letters, digits, spaces and . _ -")
    token = PREFIX + secrets.token_urlsafe(32)
    with db.session() as s:
        user = s.get(User, user_id)
        if user is None:
            raise TokenError("No such account")
        row = ApiToken(user_id=user_id, name=name, token_hash=_hash(token), created_at=_now())
        s.add(row)
    return row, token


def authenticate(token: str):
    """(account, token row) for a live token on an enabled account, else None."""
    if not token or not token.startswith(PREFIX):
        return None
    with db.session() as s:
        row = s.scalar(select(ApiToken).where(ApiToken.token_hash == _hash(token)))
        if row is None or row.revoked_at is not None:
            return None
        user = s.get(User, row.user_id)
        if user is None or user.disabled_at is not None:
            return None
        row.last_used_at = _now()
        return user, row


def all_tokens() -> list[tuple[ApiToken, str]]:
    """Every token with its account's username, newest first."""
    with db.session() as s:
        names = {u.id: u.username for u in s.scalars(select(User))}
        rows = s.scalars(select(ApiToken).order_by(ApiToken.id.desc()))
        return [(r, names.get(r.user_id, "?")) for r in rows]


def revoke(token_id: int) -> ApiToken:
    with db.session() as s:
        row = s.get(ApiToken, token_id)
        if row is None:
            raise TokenError("No such token")
        if row.revoked_at is None:
            row.revoked_at = _now()
        return row
