"""Read-only OpenProject API v3 client for biscuits.

SAFETY: this module can only issue HTTP GET. There is deliberately no generic
`request(method, ...)` helper here. Writes live in writes.py, which is gated by
the [writes] section of the config (default: preview, i.e. nothing is sent).

Config is looked up on each call (cheap, see config.py), so repointing at
another server is just an edit to the config file; no restart is needed.
"""

import base64
import json
import urllib.error
import urllib.parse
import urllib.request

from . import config as _config


class OPError(RuntimeError):
    def __init__(self, msg, status=None, body=None):
        super().__init__(msg)
        self.status = status
        self.body = body


def config():
    """Return (base_url, api_key)."""
    cfg = _config.load()
    return cfg.op_url, cfg.op_api_key


def auth_header(key):
    return "Basic " + base64.b64encode(f"apikey:{key}".encode()).decode()


def web_url(path=""):
    """Link into OP's own web UI (e.g. web_url('/work_packages/12'))."""
    return config()[0] + path


def get(path, params=None, timeout=30):
    """GET /api/v3{path}. The only way this module talks to OpenProject."""
    base, key = config()
    url = f"{base}/api/v3{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url, method="GET",
        headers={"Authorization": auth_header(key), "Accept": "application/hal+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise OPError(f"GET {path} -> HTTP {e.code}", status=e.code) from e
    except urllib.error.URLError as e:
        raise OPError(f"GET {path} -> {e.reason}") from e


def get_all(path, params=None, page_size=100):
    """Fetch every element of a collection.

    OP quirk: `offset` is a 1-based PAGE NUMBER, not a row offset. Also note
    /work_packages applies a default open-only filter unless `filters` is
    passed explicitly (pass filters='[]' for everything).
    """
    out, page = [], 1
    while True:
        p = dict(params or {}, pageSize=page_size, offset=page)
        d = get(path, p)
        els = d.get("_embedded", {}).get("elements", [])
        out += els
        if not els or page * page_size >= d.get("total", 0):
            return out
        page += 1
