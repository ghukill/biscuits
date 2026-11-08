"""`biscuits mcp`. The HTTP layer is replaced, so nothing is ever sent."""

import base64
import importlib
import io
import json
import os
import subprocess
import sys
import urllib.parse

import pytest

from biscuits import mcp_server

URL = "https://op.test"
KEY = "mcp-key-value"
TOOL_NAMES = [
    "list_projects", "search_work_packages", "get_work_package", "create_work_package",
    "update_work_package", "add_comment", "add_attachment", "count_work_packages", "get_comments",
    "list_statuses", "list_types", "list_priorities", "list_users",
]


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeHTTP:
    """Stands in for urlopen: records each request and answers from `routes`."""

    def __init__(self):
        self.requests = []  # (method, path, query, body, authorization)
        self.routes = {
            ("GET", "/api/v3/types"): {"_embedded": {"elements": [{"id": 1, "name": "Task"}]}},
            ("GET", "/api/v3/work_packages/5"): {"id": 5, "lockVersion": 3},
        }

    def __call__(self, req, timeout=None):
        parts = urllib.parse.urlsplit(req.full_url)
        multipart = (req.get_header("Content-type") or "").startswith("multipart/")
        body = req.data if multipart else (json.loads(req.data) if req.data else None)
        query = urllib.parse.parse_qs(parts.query)
        self.requests.append((req.get_method(), parts.path, query, body, req.get_header("Authorization")))
        answer = self.routes.get((req.get_method(), parts.path), {"id": 5, "subject": "x"})
        if callable(answer):
            answer = answer(query)
        return Response(json.dumps(answer).encode())

    def sent(self, method):
        return [r for r in self.requests if r[0] == method]


@pytest.fixture
def clean_env(monkeypatch):
    for name in ("OPENPROJECT_URL", "OPENPROJECT_API_KEY", "OPENPROJECT_ENV_FILE"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture
def http(clean_env):
    """The server with stub credentials and a fake HTTP layer."""
    clean_env.setenv("OPENPROJECT_URL", URL)
    clean_env.setenv("OPENPROJECT_API_KEY", KEY)
    importlib.reload(mcp_server)
    fake = FakeHTTP()
    clean_env.setattr(mcp_server.urllib.request, "urlopen", fake)
    yield fake
    clean_env.delenv("OPENPROJECT_URL")
    clean_env.delenv("OPENPROJECT_API_KEY")
    importlib.reload(mcp_server)


def call(name, **arguments):
    result = mcp_server.rpc("tools/call", {"name": name, "arguments": arguments})
    return result, json.loads(result["content"][0]["text"])


def test_tool_list_matches_the_old_server():
    assert [t["name"] for t in mcp_server.rpc("tools/list")["tools"]] == TOOL_NAMES
    assert mcp_server.SERVER_INFO == {"name": "openproject-mcp", "version": "0.3.0"}
    assert set(mcp_server.DISPATCH) == set(TOOL_NAMES)
    assert all(mcp_server.DISPATCH.values())


def test_create_sends_the_assignee_as_a_user(http):
    result, _ = call("create_work_package", project_id=9, subject="New", assignee_id=16)
    assert not result["isError"]
    (_, path, query, body, auth), = http.sent("POST")
    assert path == "/api/v3/projects/9/work_packages" and query["notify"] == ["false"]
    assert body["assignee"] == {"href": "/api/v3/users/16"}
    assert "description" not in body  # this OpenProject build answers 500 to a null
    assert auth == "Basic " + base64.b64encode(f"apikey:{KEY}".encode()).decode()


def test_update_sends_the_assignee_as_a_user_with_the_current_lock_version(http):
    result, _ = call("update_work_package", work_package_id=5, assignee_id=16)
    assert not result["isError"]
    (_, path, _, body, _), = http.sent("PATCH")
    assert path == "/api/v3/work_packages/5"
    assert body == {"lockVersion": 3, "assignee": {"href": "/api/v3/users/16"}}


def test_update_can_add_a_comment_in_the_same_call(http):
    call("update_work_package", work_package_id=5, comment="Hello")
    (_, path, _, body, _), = http.sent("POST")
    assert path == "/api/v3/work_packages/5/activities"
    assert body == {"comment": {"format": "markdown", "raw": "Hello"}}


def test_paging_treats_offset_as_a_page_number(http):
    def page(query):
        first = (int(query["offset"][0]) - 1) * int(query["pageSize"][0])
        rows = range(first, min(first + 100, 250))
        return {"total": 250, "_embedded": {"elements": [{"id": i} for i in rows]}}

    http.routes[("GET", "/api/v3/projects")] = page
    assert [e["id"] for e in mcp_server.paged("/projects", limit=250)] == list(range(250))
    assert [r[2]["offset"] for r in http.requests] == [["1"], ["2"], ["3"]]
    assert {r[2]["pageSize"][0] for r in http.requests} == {"100"}


def test_an_http_error_comes_back_as_a_tool_error(http, monkeypatch):
    def refuse(req, timeout=None):
        raise mcp_server.urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(b"no"))

    monkeypatch.setattr(mcp_server.urllib.request, "urlopen", refuse)
    result, payload = call("get_work_package", work_package_id=5)
    assert result["isError"] and payload["_error"] == "GET /work_packages/5 -> HTTP 403"


def test_an_unknown_tool_is_an_error():
    assert mcp_server.rpc("tools/call", {"name": "delete_everything"})["isError"]


def test_credentials_come_from_the_environment(clean_env):
    clean_env.setenv("OPENPROJECT_URL", URL + "/")
    clean_env.setenv("OPENPROJECT_API_KEY", KEY)
    assert mcp_server.load_creds() == (URL, KEY)


def test_credentials_come_from_the_named_file(clean_env, tmp_path):
    env_file = tmp_path / "op.env"
    env_file.write_text(f'# a comment\nOTHER=x\nOPENPROJECT_URL="{URL}/"\nOPENPROJECT_API_KEY=\'{KEY}\'\n')
    clean_env.setenv("OPENPROJECT_ENV_FILE", str(env_file))
    assert mcp_server.load_creds() == (URL, KEY)


def test_the_environment_wins_over_the_file(clean_env, tmp_path):
    env_file = tmp_path / "op.env"
    env_file.write_text("OPENPROJECT_URL=https://file.test\nOPENPROJECT_API_KEY=file-key\n")
    clean_env.setenv("OPENPROJECT_ENV_FILE", str(env_file))
    clean_env.setenv("OPENPROJECT_API_KEY", KEY)
    assert mcp_server.load_creds() == ("https://file.test", KEY)


@pytest.mark.parametrize("env_file", [None, "/no/such/file.env"])
def test_no_credentials_is_not_a_crash(clean_env, env_file):
    if env_file:
        clean_env.setenv("OPENPROJECT_ENV_FILE", env_file)
    assert mcp_server.load_creds() == ("", "")


def test_it_imports_only_the_standard_library():
    code = (
        "import sys; base = set(sys.modules); import biscuits.mcp_server; "
        "new = {m.split('.')[0] for m in set(sys.modules) - base}; "
        "print(sorted(new - set(sys.stdlib_module_names) - {'biscuits'}))"
    )
    run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert run.stdout.strip() == "[]"


def test_starts_and_lists_tools_with_no_credentials_or_biscuits_config(tmp_path):
    """The real command, in a process with nothing to go on. No web server either."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OPENPROJECT_", "BISCUITS_"))}
    env["HOME"] = str(tmp_path)  # so ~/.biscuits/config.toml doesn't exist
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_projects", "arguments": {}}},
    ]
    run = subprocess.run(
        [sys.executable, "-m", "biscuits", "mcp-direct"],
        input="".join(json.dumps(m) + "\n" for m in messages),
        capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60,
    )
    assert run.returncode == 0, run.stderr
    init, tools, listing = [json.loads(line) for line in run.stdout.splitlines()]
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert [t["name"] for t in tools["result"]["tools"]] == TOOL_NAMES
    assert listing["result"]["isError"]
    assert "not configured" in listing["result"]["content"][0]["text"]
    assert list(tmp_path.iterdir()) == []  # it didn't create a biscuits directory either


def test_work_packages_name_who_created_them(http):
    http.routes[("GET", "/api/v3/work_packages/5")] = {
        "id": 5, "_links": {"author": {"href": "/api/v3/users/9", "title": "Cass Creator"}}}
    _, task = call("get_work_package", work_package_id=5)
    assert task["author"] == "Cass Creator"


ATT = {"id": 31, "fileName": "shot.png", "fileSize": 4, "contentType": "image/png",
       "createdAt": "2026-11-07T10:00:00Z", "_links": {"author": {"title": "Biscuits Agent"}}}


def test_get_work_package_lists_attachments(http):
    http.routes[("GET", "/api/v3/work_packages/5/attachments")] = {"total": 1, "_embedded": {"elements": [ATT]}}
    http.routes[("GET", "/api/v3/work_packages/5")] = {"id": 5, "_links": {}}
    _, task = call("get_work_package", work_package_id=5)
    assert task["attachments"] == [{
        "id": 31, "file_name": "shot.png", "size": 4, "content_type": "image/png", "author": "Biscuits Agent",
        "created_at": "2026-11-07T10:00:00", "download_url": f"{URL}/api/v3/attachments/31/content",
        "markdown": "![shot.png](/api/v3/attachments/31/content)"}]


def test_embedded_attachments_need_no_second_request(http):
    http.routes[("GET", "/api/v3/work_packages/5")] = {
        "id": 5, "_embedded": {"attachments": {"_embedded": {"elements": [ATT]}}}}
    _, task = call("get_work_package", work_package_id=5)
    assert [a["id"] for a in task["attachments"]] == [31]
    assert len(http.requests) == 1


def test_add_attachment_uploads_a_local_file_as_multipart(http, tmp_path):
    shot = tmp_path / "mockup.png"
    shot.write_bytes(b"\x89PNGdata")
    http.routes[("POST", "/api/v3/work_packages/5/attachments")] = {**ATT, "fileName": "mockup.png"}
    result, out = call("add_attachment", work_package_id=5, file_path=str(shot))
    assert not result["isError"]
    assert out["attached"] and out["id"] == 31 and out["markdown"] == "![mockup.png](/api/v3/attachments/31/content)"
    (_, path, _, body, auth), = http.sent("POST")
    assert path == "/api/v3/work_packages/5/attachments"
    assert b'{"fileName": "mockup.png"}' in body and b'filename="mockup.png"' in body
    assert b"Content-Type: image/png" in body and b"\x89PNGdata" in body
    assert auth == "Basic " + base64.b64encode(f"apikey:{KEY}".encode()).decode()


def test_add_attachment_takes_base64_content(http):
    content = base64.b64encode(b"hello").decode()
    result, _ = call("add_attachment", work_package_id=5, content_base64=content, file_name="dir/notes.txt")
    assert not result["isError"]
    (_, _, _, body, _), = http.sent("POST")
    assert b'filename="notes.txt"' in body and b"Content-Type: text/plain" in body and b"\r\nhello\r\n" in body


@pytest.mark.parametrize("args, error", [
    ({}, "exactly one"),
    ({"file_path": "/x", "content_base64": "aGk="}, "exactly one"),
    ({"file_path": "/no/such/file.png"}, "no such file"),
    ({"content_base64": "aGk="}, "file_name is required"),
    ({"content_base64": "not base64!", "file_name": "x"}, "valid base64"),
    ({"content_base64": "", "file_name": "x"}, "exactly one"),
])
def test_add_attachment_refuses_bad_input_without_sending(http, args, error):
    result, out = call("add_attachment", work_package_id=5, **args)
    assert result["isError"] and error in out["_error"]
    assert http.sent("POST") == []


def test_add_attachment_refuses_big_files(http, tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "MAX_ATTACHMENT", 3)
    big = tmp_path / "big.bin"
    big.write_bytes(b"1234")
    assert call("add_attachment", work_package_id=5, file_path=str(big))[0]["isError"]
    assert call("add_attachment", work_package_id=5, content_base64="MTIzNA==", file_name="b")[0]["isError"]
    assert http.sent("POST") == []
