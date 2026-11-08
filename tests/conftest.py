"""Tests run against a stub OpenProject; nothing touches a real server."""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography.fernet import Fernet

from biscuits import accounts, client, config, db, opadmin, snapshot
from biscuits.app import create_app

ADMIN_TOKEN = "secret-key-value"
USER_KEY = "user-key-value"
SECRET_KEY = "s" * 48
ENCRYPTION_KEY = Fernet.generate_key().decode()
PASSWORD = "correct horse battery"
H = {"X-Biscuits": "1"}


def wp(id=1, project=1, subject="A task", links=None):
    return {
        "_type": "WorkPackage", "id": id, "subject": subject, "lockVersion": 3,
        "createdAt": "2026-11-02T14:30:00Z",
        "_links": {
            "project": {"href": f"/api/v3/projects/{project}", "title": "P"},
            "status": {"href": "/api/v3/statuses/1", "title": "New"},
            "priority": {"href": "/api/v3/priorities/1", "title": "Normal"},
            "type": {"href": "/api/v3/types/1", "title": "Task"},
            "author": {"href": "/api/v3/users/9", "title": "Cass Creator"},
            **(links or {}),
        },
    }


def attachment(id=5, name="shot.png", ctype="image/png", size=4):
    return {"_type": "Attachment", "id": id, "fileName": name, "fileSize": size, "contentType": ctype,
            "createdAt": "2026-11-07T10:00:00Z",
            "_links": {"author": {"href": "/api/v3/users/7", "title": "Pat"},
                       "downloadLocation": {"href": f"/api/v3/attachments/{id}/content"}}}


def collection(*els):
    return {"total": len(els), "_embedded": {"elements": list(els)}}


class StubOP:
    """Records every request, with the key it used, and answers the GETs biscuits makes.

    By default any key is user #7 "Pat", an admin who can see everything. Fill in
    .people to have only those keys work, each as its own non-admin user with its
    own visible work packages, like a real instance.
    """

    def __init__(self):
        self.requests = []       # (method, path, key)
        self.writes = []         # (method, path, body)
        self.people = {}         # key -> {"id", "name", "wps": [ids], "admin": bool}
        self.deny_writes = None  # an HTTP status to answer writes with
        self.activities = []
        self.project_links = {}
        self.extra_wps = []      # more elements for the /work_packages list
        self.wp_links = {}       # extra _links on a single work package (e.g. addAttachment)
        self.wp_description = None
        self.attachments = {}    # id -> (metadata, bytes); listed on every work package
        self.uploads = []        # (path, raw multipart body, content type)
        self.redirect_content = None  # send /content requests here instead
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def _send(self, payload, status=200):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _key(self):
                raw = (self.headers.get("Authorization") or "").removeprefix("Basic ")
                return base64.b64decode(raw).decode().removeprefix("apikey:")

            def _person(self):
                """(known, person). person is None for the default admin Pat."""
                if not stub.people:
                    return True, None
                who = stub.people.get(self._key())
                return who is not None, who

            def do_GET(self):
                path = self.path.split("?")[0].removeprefix("/api/v3")
                stub.requests.append(("GET", path, self._key()))
                known, who = self._person()
                if not known:
                    return self._send({"message": "unauthorized"}, 401)
                sees = (lambda n: n in who["wps"]) if who else (lambda n: True)
                admin = who is None or who.get("admin")
                if path == "/work_packages":
                    return self._send(collection(*[wp(n, n) for n in (1, 2) if sees(n)], *stub.extra_wps))
                if path.startswith("/work_packages/"):
                    n = int(path.split("/")[2])
                    if not sees(n):
                        return self._send({"message": "not found"}, 404)
                    if path.endswith("/activities"):
                        return self._send(collection(*stub.activities))
                    if path.endswith("/attachments"):
                        return self._send(collection(*[m for m, _ in stub.attachments.values()]))
                    e = wp(n, project=n, links=stub.wp_links)  # WP n lives in project n
                    if stub.wp_description:
                        e["description"] = stub.wp_description
                    return self._send(e)
                if path.startswith("/attachments/"):
                    n = int(path.split("/")[2])
                    if n not in stub.attachments:
                        return self._send({"message": "not found"}, 404)
                    meta, data = stub.attachments[n]
                    if not path.endswith("/content"):
                        return self._send(meta)
                    if stub.redirect_content:
                        self.send_response(302)
                        self.send_header("Location", stub.redirect_content)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", meta["contentType"])
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    return self.wfile.write(data)
                if path == "/projects":
                    return self._send(collection({"id": 1, "name": "P", "active": True}))
                if path.startswith("/projects/") and path.endswith("/work_packages"):
                    return self._send(collection(*[wp(n, n) for n in (1, 2) if sees(n)]))
                if path.startswith("/projects/") and path.endswith("/available_assignees"):
                    return self._send(collection({"_type": "User", "id": 7, "name": "Pat"}))
                if path.startswith("/projects/"):
                    return self._send({"id": 1, "name": "P", "_links": stub.project_links})
                if path == "/statuses":
                    return self._send(collection({"id": 1, "name": "New"}))
                if path == "/priorities":
                    return self._send(collection({"id": 1, "name": "Normal"}))
                if path == "/types":
                    return self._send(collection({"id": 1, "name": "Task"}, {"id": 3, "name": "Summary task"}))
                if path == "/users":
                    if not admin:
                        return self._send({"message": "forbidden"}, 403)
                    return self._send(collection({"_type": "User", "id": 7, "name": "Pat"},
                                                 {"_type": "User", "id": 8, "name": "Old Author"}))
                if path == "/principals":
                    return self._send(collection({"_type": "User", "id": who["id"], "name": who["name"]},
                                                 {"_type": "Group", "id": 90, "name": "A group"}))
                if path == "/users/me":
                    if who:
                        return self._send({"id": who["id"], "name": who["name"], "admin": bool(who.get("admin"))})
                    return self._send({"id": 7, "name": "Pat", "admin": True})
                self._send({"message": "nope"}, 404)

            def _write(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n)
                ctype = self.headers.get("Content-Type") or ""
                multipart = ctype.startswith("multipart/form-data")
                body = {"_multipart": len(raw)} if multipart else json.loads(raw)
                stub.requests.append((self.command, self.path.removeprefix("/api/v3"), self._key()))
                known, _ = self._person()
                if not known:
                    return self._send({"message": "unauthorized"}, 401)
                if stub.deny_writes:
                    return self._send({"message": "denied"}, stub.deny_writes)
                if multipart:
                    stub.uploads.append((self.path, raw, ctype))
                    stub.writes.append((self.command, self.path, body))
                    return self._send(attachment(77, "shot.png", "image/png", len(raw)), 201)
                stub.writes.append((self.command, self.path, body))
                if self.path.endswith("/users"):
                    return self._send({"_type": "User", "id": 50, "name": "New Person"})
                self._send(wp(1, 1, subject="Changed"))

            do_PATCH = do_POST = do_DELETE = _write

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def keys_used_for(self, prefix):
        return {key for _, path, key in self.requests if path.startswith(prefix)}


@pytest.fixture
def op():
    stub = StubOP()
    yield stub
    stub.server.shutdown()


@pytest.fixture
def write_config(tmp_path, op):
    """Write a config file and point biscuits at it; call again to change it."""
    path = tmp_path / "config.toml"

    def write(mode="preview", projects="[]", extra="", url=None):
        path.write_text(
            f'[openproject]\nurl = "{url or op.url}"\nadmin_token = "{ADMIN_TOKEN}"\n'
            f'[writes]\nmode = "{mode}"\nprojects = {projects}\n'
            f'[auth]\nsecret_key = "{SECRET_KEY}"\nencryption_key = "{ENCRYPTION_KEY}"\n{extra}'
        )
        path.chmod(0o600)
        # Rewrites within a timestamp tick must still be noticed by the cache.
        config.set_path(path)
        return path

    yield write
    config.set_path(None)


@pytest.fixture(autouse=True)
def reset_state():
    snapshot._states.clear()
    opadmin._names, opadmin._names_at = {}, 0.0
    accounts.limiter = accounts.RateLimiter()


@pytest.fixture
def conn():
    """A connection like a signed-in user's, for testing writes directly."""
    return client.OP(USER_KEY, actor={"user_id": 1, "username": "pat", "op_user_id": 7})


def make_user(username="pat", key=USER_KEY, op_user_id=7, admin=True, must_change=False):
    """An account with a key already in place, without asking OpenProject about it."""
    user, _ = accounts.create(username, password=PASSWORD, is_admin=admin, op_user_id=op_user_id,
                              op_user_name=username.title(), must_change=must_change)
    if key:
        with db.session() as s:
            s.get(db.User, user.id).op_key_enc = accounts._fernet().encrypt(key.encode()).decode()
    return accounts.get(user.id)


def login(client_, username="pat", password=PASSWORD, prefix=""):
    return client_.post(f"{prefix}/api/login", json={"username": username, "password": password}, headers=H)


@pytest.fixture
def web_as():
    """A signed-in test client for an account made with make_user."""
    def build(username="pat", **kwargs):
        make_user(username, **kwargs)
        c = create_app().test_client()
        assert login(c, username).status_code == 200
        return c
    return build
