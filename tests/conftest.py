"""Tests run against a stub OpenProject; nothing touches a real server."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from biscuits import config, snapshot


def wp(id=1, project=1, subject="A task"):
    return {
        "_type": "WorkPackage", "id": id, "subject": subject, "lockVersion": 3,
        "_links": {
            "project": {"href": f"/api/v3/projects/{project}", "title": "P"},
            "status": {"href": "/api/v3/statuses/1", "title": "New"},
            "priority": {"href": "/api/v3/priorities/1", "title": "Normal"},
            "type": {"href": "/api/v3/types/1", "title": "Task"},
        },
    }


def collection(*els):
    return {"total": len(els), "_embedded": {"elements": list(els)}}


class StubOP:
    """Records non-GET requests in .writes and answers the GETs biscuits makes."""

    def __init__(self):
        self.writes = []
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

            def do_GET(self):
                path = self.path.split("?")[0].removeprefix("/api/v3")
                if path == "/work_packages":
                    return self._send(collection(wp(1, 1), wp(2, 2)))
                if path.startswith("/work_packages/") and path.endswith("/activities"):
                    return self._send(collection())
                if path.startswith("/work_packages/"):
                    n = int(path.rsplit("/", 1)[-1])
                    return self._send(wp(n, project=n))  # WP n lives in project n
                if path == "/projects":
                    return self._send(collection({"id": 1, "name": "P", "active": True}))
                if path == "/statuses":
                    return self._send(collection({"id": 1, "name": "New"}))
                if path == "/priorities":
                    return self._send(collection({"id": 1, "name": "Normal"}))
                if path == "/types":
                    return self._send(collection({"id": 1, "name": "Task"}))
                if path == "/users":
                    return self._send(collection({"_type": "User", "id": 7, "name": "Pat"}))
                if path == "/users/me":
                    return self._send({"id": 7, "name": "Pat", "admin": True})
                self._send({"message": "nope"}, 404)

            def _write(self):
                n = int(self.headers.get("Content-Length") or 0)
                stub.writes.append((self.command, self.path, json.loads(self.rfile.read(n))))
                self._send(wp(1, 1, subject="Changed"))

            do_PATCH = do_POST = do_DELETE = _write

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


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
            f'[openproject]\nurl = "{url or op.url}"\napi_key = "secret-key-value"\n'
            f'[writes]\nmode = "{mode}"\nprojects = {projects}\n{extra}'
        )
        path.chmod(0o600)
        # Rewrites within a timestamp tick must still be noticed by the cache.
        config.set_path(path)
        return path

    yield write
    config.set_path(None)


@pytest.fixture(autouse=True)
def reset_snapshot():
    snapshot._state.update(data=None, fetched_at=0.0, error=None)
