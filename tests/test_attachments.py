"""Attachments: listing, the download proxy, uploads through the write gate."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from biscuits import client, config, writes
from conftest import H, USER_KEY, attachment

PNG = b"\x89PNG\r\n\x1a\nfake"
LINKS = {"attachments": {"href": "/api/v3/work_packages/1/attachments"},
         "addAttachment": {"href": "/api/v3/work_packages/1/attachments", "method": "post"}}


@pytest.fixture
def web(write_config, web_as, op):
    write_config("live", '"*"')
    op.wp_links = LINKS
    op.attachments = {
        5: (attachment(5, "shot.png", "image/png", len(PNG)), PNG),
        6: (attachment(6, "notes.pdf", "application/pdf", 3), b"pdf"),
        7: (attachment(7, "logo.svg", "image/svg+xml", 6), b"<svg/>"),
    }
    return web_as()


def test_detail_lists_attachments_with_links_through_biscuits(web, op):
    d = web.get("/api/wp/1").json
    shot, notes, svg = d["attachments"]
    assert shot == {"id": 5, "name": "shot.png", "size": len(PNG), "content_type": "image/png",
                    "is_image": True, "created": "2026-11-07T10:00:00Z", "author": "Pat",
                    "url": "/api/attachment/5"}
    assert not notes["is_image"] and not svg["is_image"]  # SVG can carry script: download only
    assert d["can_attach"] is True and d["max_upload"] == writes.MAX_UPLOAD


def test_no_add_attachment_link_means_no_uploads(web, op):
    op.wp_links = {"attachments": LINKS["attachments"]}
    assert web.get("/api/wp/1").json["can_attach"] is False


def test_inline_images_in_descriptions_and_comments_point_at_biscuits(web, op):
    op.wp_description = {"raw": "![](/api/v3/attachments/5/content)",
                         "html": f'<p><img src="{op.url}/api/v3/attachments/5/content"> '
                                 '<a href="/api/v3/attachments/6/content">notes</a> '
                                 '<img src="https://elsewhere.test/api/v3/attachments/9/content"></p>'}
    op.activities = [{"id": 1, "comment": {"raw": "see", "html": '<img src="/api/v3/attachments/5/content">'},
                      "_links": {"user": {"href": "/api/v3/users/7"}}}]
    d = web.get("/api/wp/1").json
    html = d["description"]["html"]
    assert 'src="/api/attachment/5"' in html and 'href="/api/attachment/6"' in html
    assert "https://elsewhere.test/api/v3/attachments/9/content" in html  # not OP: left alone
    assert d["description"]["raw"] == "![](/api/v3/attachments/5/content)"  # OP's own form, for editing
    assert d["activities"][0]["comment_html"] == '<img src="/api/attachment/5">'


def test_an_image_is_shown_inline_with_the_persons_own_key(web, op):
    r = web.get("/api/attachment/5")
    assert r.status_code == 200 and r.data == PNG
    assert r.headers["Content-Type"] == "image/png"
    assert r.headers["Content-Disposition"].startswith("inline;")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in r.headers["Content-Security-Policy"]
    assert r.headers["Cache-Control"] == "private, max-age=300"
    assert op.keys_used_for("/attachments/") == {USER_KEY}


@pytest.mark.parametrize("att_id", [6, 7])
def test_other_files_are_always_downloads(web, att_id):
    r = web.get(f"/api/attachment/{att_id}")
    assert r.headers["Content-Type"] == "application/octet-stream"
    assert r.headers["Content-Disposition"].startswith("attachment;")


def test_download_can_be_asked_for_an_image_too(web):
    assert web.get("/api/attachment/5?download=1").headers["Content-Disposition"].startswith("attachment;")


def test_a_file_they_cant_see_is_not_found(web):
    r = web.get("/api/attachment/99")
    assert r.status_code == 404 and r.json["error"] == "You don't have access to this file."


def test_the_proxy_needs_a_login(write_config, op):
    from biscuits.app import create_app
    write_config()
    assert create_app().test_client().get("/api/attachment/5").status_code in (302, 401)


def _upload(web, data=PNG, name="shot.png", ctype="image/png", headers=H):
    import io
    return web.post("/api/wp/1/attachments", headers=headers, content_type="multipart/form-data",
                    data={"file": (io.BytesIO(data), name, ctype)})


def test_upload_goes_to_openproject_as_multipart_and_is_logged(web, op):
    r = _upload(web)
    assert r.status_code == 200 and r.json["sent"] is True
    assert r.json["attachment"]["id"] == 77 and r.json["attachment"]["url"] == "/api/attachment/77"
    (path, raw, ctype), = op.uploads
    assert path == "/api/v3/work_packages/1/attachments" and ctype.startswith("multipart/form-data; boundary=")
    assert b'name="metadata"' in raw and b'{"fileName": "shot.png"}' in raw
    assert b'name="file"; filename="shot.png"' in raw and PNG in raw
    assert op.keys_used_for("/work_packages/1/attachments") == {USER_KEY}
    entry = writes.recent(1)[0]
    assert entry["op"] == "attach" and entry["sent"] is True and entry["wp"] == 1
    assert entry["body"] == {"fileName": "shot.png", "contentType": "image/png", "size": len(PNG)}
    assert "fake" not in config.load().write_log_file.read_text()  # never the contents


def test_upload_in_preview_mode_sends_nothing(write_config, web, op):
    write_config("preview", '"*"')
    r = _upload(web)
    assert r.json["sent"] is False and r.json["reason"] == "preview mode" and r.json["attachment"] is None
    assert op.uploads == []
    assert writes.recent(1)[0]["op"] == "attach"


def test_upload_outside_the_write_enabled_projects_sends_nothing(write_config, web, op):
    write_config("live", "[2]")
    assert _upload(web).json["sent"] is False and op.uploads == []


def test_upload_needs_the_csrf_header(web, op):
    assert _upload(web, headers={}).status_code == 403 and op.uploads == []


def test_too_big_or_empty_is_refused_before_openproject(web, op, monkeypatch):
    monkeypatch.setattr(writes, "MAX_UPLOAD", 10)
    assert _upload(web, data=b"x" * 11).status_code == 413
    assert _upload(web, data=b"").status_code == 400
    assert op.uploads == []


def test_a_request_far_too_big_is_refused_unread(web, op, monkeypatch):
    monkeypatch.setattr(writes, "MAX_UPLOAD", 10)
    assert _upload(web, data=b"x" * 200_000).status_code == 413 and op.uploads == []


def test_no_file_is_a_clear_error(web, op):
    r = web.post("/api/wp/1/attachments", headers=H, content_type="multipart/form-data", data={})
    assert r.status_code == 400 and r.json["error"] == "No file was sent"


def test_openproject_refusing_an_upload_is_passed_on(web, op):
    op.deny_writes = 422
    r = _upload(web)
    assert r.status_code == 422 and r.json["ok"] is False
    assert writes.recent(1)[0]["ok"] is False


@pytest.mark.parametrize("given, kept", [
    ("../../etc/passwd", "passwd"), ("C:\\Users\\me\\shot.png", "shot.png"), (".hidden", "hidden"),
    ("", "file"), ("bad\x00\nname.txt", "badname.txt"), ("a" * 300 + ".png", "a" * 251 + ".png"),
])
def test_file_names_are_cleaned(given, kept):
    assert writes._file_name(given) == kept


def test_the_only_delete_is_for_one_task():
    # Deleting a task (#3464) is the one delete; files can't be removed.
    assert [n for n in dir(writes) if n.startswith(("delete", "remove", "detach"))] == ["delete_task"]


class _Recorder(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *_):
        pass

    def do_GET(self):
        _Recorder.seen.append(self.headers.get("Authorization"))
        self.send_response(200)
        self.send_header("Content-Length", "3")
        self.end_headers()
        self.wfile.write(b"ext")


def test_the_key_never_follows_a_redirect_off_openproject(web, op):
    other = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    threading.Thread(target=other.serve_forever, daemon=True).start()
    try:
        op.redirect_content = f"http://127.0.0.1:{other.server_port}/bucket/shot.png?sig=1"
        r = web.get("/api/attachment/5")
        assert r.status_code == 200 and r.data == b"ext"
        assert _Recorder.seen == [None]
    finally:
        other.shutdown()


def test_download_locations_elsewhere_get_no_key(write_config, op):
    write_config()
    seen = {}

    class Opener:
        def open(self, req, timeout=None):
            seen[req.full_url] = req.get_header("Authorization")
            return None

    import biscuits.client as c
    orig, c._download_opener = c._download_opener, Opener()
    try:
        conn = client.OP(USER_KEY)
        conn.download("/api/v3/attachments/5/content")
        conn.download("https://storage.test/x?sig=1")
    finally:
        c._download_opener = orig
    assert seen == {f"{op.url}/api/v3/attachments/5/content": client.auth_header(USER_KEY),
                    "https://storage.test/x?sig=1": None}
