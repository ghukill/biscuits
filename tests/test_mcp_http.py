"""biscuits' own MCP endpoint (/mcp), against the stub OpenProject."""

import json

import pytest

from biscuits import accounts, archive, config, db, mcp_schema, mcp_tools, sections, tokens, writes
from biscuits.app import create_app

from conftest import ADMIN_TOKEN, USER_KEY, collection, make_user, wp

BOT_KEY = "bot-op-key"
SHARED_NAMES = [t["name"] for t in mcp_schema.TOOLS]


@pytest.fixture
def mcp(write_config, op):
    """A test client and a token for a non-admin "biscuits-agent" account."""
    write_config("live", '"*"')
    user = make_user("biscuits-agent", key=BOT_KEY, op_user_id=16, admin=False)
    _, token = tokens.create(user.id, "tests")

    class M:
        def __init__(self):
            self.client = create_app().test_client()
            self.token = token
            self.user = user

        def post(self, body, token=..., addr="127.0.0.1", headers=None):
            h = {} if token is None else {"Authorization": f"Bearer {self.token if token is ... else token}"}
            return self.client.post("/mcp", data=json.dumps(body), content_type="application/json",
                                    headers={**h, **(headers or {})}, environ_base={"REMOTE_ADDR": addr})

        def rpc(self, method, params=None, **kw):
            return self.post({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}, **kw)

        def call(self, name, **arguments):
            r = self.rpc("tools/call", {"name": name, "arguments": arguments})
            assert r.status_code == 200, r.data
            result = r.get_json()["result"]
            return result["isError"], json.loads(result["content"][0]["text"])

    return M()


# ---------- who may call ----------

@pytest.mark.parametrize("token", [None, "bsk_wrong", "not-even-a-token"])
def test_a_token_is_required(mcp, op, token):
    r = mcp.rpc("tools/list", token=token)
    assert r.status_code == 401 and r.headers["WWW-Authenticate"].startswith("Bearer")
    assert op.requests == []


def test_a_signed_in_browser_session_is_not_enough(mcp, web_as):
    c = web_as("pat")
    r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers={"X-Biscuits": "1"})
    assert r.status_code == 401


def test_revoked_tokens_and_disabled_accounts_stop_working(mcp):
    assert mcp.rpc("tools/list").status_code == 200
    row, = [r for r, _ in tokens.all_tokens()]
    tokens.revoke(row.id)
    assert mcp.rpc("tools/list").status_code == 401

    _, mcp.token = tokens.create(mcp.user.id, "again")
    assert mcp.rpc("tools/list").status_code == 200
    accounts.set_disabled(mcp.user.id, True)
    assert mcp.rpc("tools/list").status_code == 401


@pytest.mark.parametrize("addr, ok", [
    ("127.0.0.1", True), ("100.89.196.22", True), ("100.127.0.1", True), ("fd7a:115c:a1e3::5", True),
    ("8.8.8.8", False), ("192.168.1.10", False), ("100.128.0.1", False), ("2001:db8::1", False),
])
def test_only_this_machine_and_the_tailnet_by_default(mcp, op, addr, ok):
    r = mcp.rpc("tools/list", addr=addr)
    assert r.status_code == (200 if ok else 403)


def test_allow_from_is_configurable(mcp, write_config):
    write_config("live", '"*"', extra='[mcp]\nallow_from = ["192.168.1.0/24"]\n')
    assert mcp.rpc("tools/list", addr="192.168.1.10").status_code == 200
    assert mcp.rpc("tools/list", addr="127.0.0.1").status_code == 403


def test_a_wrong_address_is_refused_before_the_token_is_looked_at(mcp):
    assert mcp.rpc("tools/list", token="bsk_wrong", addr="8.8.8.8").status_code == 403


def test_bad_tokens_are_rate_limited(mcp):
    for _ in range(accounts.RateLimiter.MAX_PER_NAME):
        assert mcp.rpc("tools/list", token="bsk_wrong").status_code == 401
    assert mcp.rpc("tools/list").status_code == 429


def test_cross_origin_requests_are_refused(mcp):
    assert mcp.rpc("tools/list", headers={"Origin": "https://evil.example"}).status_code == 403


def test_an_account_without_a_key_gets_a_clear_error(mcp):
    user = make_user("keyless", key=None, op_user_id=30, admin=False)
    _, token = tokens.create(user.id, "t")
    r = mcp.rpc("tools/list", token=token)
    assert r.status_code == 403 and "isn't connected to OpenProject" in r.get_json()["error"]["message"]


def test_only_a_hash_of_the_token_is_stored(mcp):
    with db.session() as s:
        rows = s.query(db.ApiToken).all()
    assert mcp.token.startswith("bsk_") and all(mcp.token not in r.token_hash for r in rows)
    assert mcp.token not in config.load().db_file.read_bytes().decode("latin-1")


# ---------- protocol ----------

def test_initialize_and_notifications(mcp):
    r = mcp.rpc("initialize", {"protocolVersion": "2025-03-26"})
    result = r.get_json()["result"]
    assert result["protocolVersion"] == "2025-03-26" and result["serverInfo"]["name"] == "biscuits"
    assert mcp.rpc("initialize", {"protocolVersion": "1999-01-01"}).get_json()["result"]["protocolVersion"] == "2025-06-18"
    r = mcp.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert r.status_code == 202 and r.data == b""


def test_batches_unknown_methods_and_bad_json(mcp):
    r = mcp.post([{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "id": 2, "method": "nope"}])
    one, two = r.get_json()
    assert one["result"] == {} and two["error"]["code"] == -32601
    r = mcp.client.post("/mcp", data="{not json", content_type="application/json",
                        headers={"Authorization": f"Bearer {mcp.token}"})
    assert r.status_code == 400


def test_get_is_not_offered(mcp):
    assert mcp.client.get("/mcp").status_code == 405


def test_the_tools_keep_their_names_and_arguments(mcp):
    tools = mcp.rpc("tools/list").get_json()["result"]["tools"]
    assert [t["name"] for t in tools] == SHARED_NAMES + ["list_sections"]
    shared = {t["name"]: t["inputSchema"] for t in mcp_schema.TOOLS}
    assert all(t["inputSchema"] == shared[t["name"]] for t in tools if t["name"] in shared)
    assert set(mcp_tools.DISPATCH) == set(SHARED_NAMES) | {"list_sections"}


def test_it_mounts_under_the_base_path(write_config, op):
    write_config("live", '"*"', extra='[server]\nbase_path = "/biscuits"\n')
    user = make_user("bot", key=BOT_KEY, op_user_id=16, admin=False)
    _, token = tokens.create(user.id, "t")
    c = create_app().test_client()
    r = c.post("/biscuits/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
               headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


# ---------- reads ----------

def test_reads_use_the_accounts_own_key_never_the_admin_token(mcp, op):
    err, task = mcp.call("get_work_package", work_package_id=1)
    assert not err and task["id"] == 1 and task["url"] == f"{op.url}/work_packages/1"
    err, found = mcp.call("search_work_packages", query="task")
    assert not err and [t["id"] for t in found] == [1, 2]
    assert {key for _, _, key in op.requests} == {BOT_KEY}
    assert ADMIN_TOKEN not in {key for _, _, key in op.requests}


def test_what_the_account_cant_see_is_an_error(mcp, op):
    op.people = {BOT_KEY: {"id": 16, "name": "Biscuits Agent", "wps": [1]}}
    err, out = mcp.call("get_work_package", work_package_id=2)
    assert err and "404" in out["_error"]


def test_comments_get_names_even_when_the_key_cant_see_them(mcp, op):
    op.activities = [{"id": 3, "comment": {"raw": "hi"}, "createdAt": "2026-11-08T10:00:00Z",
                      "_links": {"user": {"href": "/api/v3/users/7"}}}]
    err, comments = mcp.call("get_comments", work_package_id=1)
    assert not err and comments[0]["author"] == "Pat" and comments[0]["comment"] == "hi"


def test_projects_say_whether_theyre_archived_in_biscuits(mcp, op):
    err, projects = mcp.call("list_projects")
    assert not err and projects == [{"id": 1, "identifier": None, "name": "P", "active": True, "archived": False}]
    archive.set_archived(1, True)
    assert mcp.call("list_projects")[1] == []
    assert mcp.call("list_projects", active_only=False)[1][0]["archived"] is True


def test_sections_come_in_biscuits_order(mcp, op):
    def section(id, name, parent=None):
        links = {"type": {"href": "/api/v3/types/3", "title": "Summary task"}}
        if parent:
            links["parent"] = {"href": f"/api/v3/work_packages/{parent}"}
        return wp(id, 1, subject=f"[{name}]", links=links)

    op.extra_wps = [section(10, "Doing"), section(11, "Done"), section(12, "To do"), section(13, "Nested", parent=10)]
    assert [s["name"] for s in mcp.call("list_sections", project_id=1)[1]] == ["Doing", "Done", "To do"]
    sections.set_order(1, [12, 10])
    assert mcp.call("list_sections", project_id=1)[1] == [
        {"id": 12, "name": "To do"}, {"id": 10, "name": "Doing"}, {"id": 11, "name": "Done"}]


def test_a_task_knows_its_section():
    rows = {
        5: {"id": 5, "subject": "Leaf", "_links": {"parent": {"href": "/api/v3/work_packages/4"}}},
        4: {"id": 4, "subject": "Mid", "_links": {"parent": {"href": "/api/v3/work_packages/3"}}},
        3: {"id": 3, "subject": "[To do]", "_links": {"type": {"title": "Summary task"}}},
        9: {"id": 9, "subject": "Plain parent", "_links": {"type": {"title": "Task"}}},
    }

    class FakeOP:
        def get(self, path, params=None):
            return rows[int(path.rsplit("/", 1)[1])]

    assert mcp_tools._section_of(FakeOP(), rows[5]) == {"id": 3, "name": "To do"}
    assert mcp_tools._section_of(FakeOP(), rows[3]) is None  # a section isn't in one
    orphan = {"id": 8, "_links": {"parent": {"href": "/api/v3/work_packages/9"}}}
    assert mcp_tools._section_of(FakeOP(), orphan) is None


# ---------- writes ----------

def test_writes_go_through_the_gate_and_the_log_as_the_account(mcp, op):
    err, out = mcp.call("add_comment", work_package_id=1, comment="Hello")
    assert not err and out["commented"]
    (method, path, body), = op.writes
    assert (method, path) == ("POST", "/api/v3/work_packages/1/activities?notify=false")
    assert body == {"comment": {"raw": "Hello"}}
    assert op.keys_used_for("/work_packages/1/activities") == {BOT_KEY}
    entry = writes.recent(1)[0]
    assert entry["sent"] and entry["actor"] == {"user_id": mcp.user.id, "username": "biscuits-agent",
                                                "op_user_id": 16, "via": "mcp", "token": "tests"}


@pytest.mark.parametrize("mode, projects", [("preview", '"*"'), ("live", "[2]")])
def test_a_closed_gate_sends_nothing_and_says_so(mcp, op, write_config, mode, projects):
    write_config(mode, projects)
    err, out = mcp.call("update_work_package", work_package_id=1, subject="Renamed")
    assert err and "didn't send this to OpenProject" in out["_error"]
    assert op.writes == []
    assert writes.recent(1)[0]["sent"] is False  # still logged


def test_create_sends_everything_it_was_given(mcp, op):
    err, out = mcp.call("create_work_package", project_id=1, subject="New", assignee_id=16, status="New",
                        priority="Normal", start_on="2026-11-09", due_on="2026-11-10", description="Body")
    assert not err and out["id"] == 1 and out["url"].endswith("/work_packages/1")
    (method, path, body), = op.writes
    assert (method, path) == ("POST", "/api/v3/work_packages?notify=false")
    assert body["_links"]["assignee"] == {"href": "/api/v3/users/16"}
    assert body["_links"]["status"] == {"href": "/api/v3/statuses/1"}
    assert body["_links"]["priority"] == {"href": "/api/v3/priorities/1"}
    assert body["_links"]["type"] == {"href": "/api/v3/types/1"}
    assert (body["startDate"], body["dueDate"], body["description"]) == ("2026-11-09", "2026-11-10", {"raw": "Body"})


def test_create_without_a_description_doesnt_send_one(mcp, op):
    assert not mcp.call("create_work_package", project_id=1, subject="New")[0]
    assert "description" not in op.writes[0][2]


@pytest.mark.parametrize("args, error", [
    ({"status": "Nonsense"}, "status 'Nonsense' not found"),
    ({"wp_type": "Nonsense"}, "type 'Nonsense' not found"),
    ({"due_on": "tomorrow"}, "YYYY-MM-DD"),
])
def test_create_refuses_bad_input_without_sending(mcp, op, args, error):
    err, out = mcp.call("create_work_package", project_id=1, subject="New", **args)
    assert err and error in out["_error"]
    assert op.writes == []


def test_update_with_a_comment_does_both(mcp, op):
    err, _ = mcp.call("update_work_package", work_package_id=1, assignee_id=16, comment="Over to you")
    assert not err
    (m1, p1, b1), (m2, p2, b2) = op.writes
    assert (m1, p1) == ("PATCH", "/api/v3/work_packages/1?notify=false")
    assert b1["lockVersion"] == 3 and b1["_links"]["assignee"] == {"href": "/api/v3/users/16"}
    assert (m2, p2, b2) == ("POST", "/api/v3/work_packages/1/activities?notify=false", {"comment": {"raw": "Over to you"}})


def test_update_can_just_comment(mcp, op):
    assert not mcp.call("update_work_package", work_package_id=1, comment="Only this")[0]
    assert [w[0] for w in op.writes] == ["POST"]
    assert mcp.call("update_work_package", work_package_id=1)[0]  # nothing at all is an error


def test_update_can_clear_the_parent(mcp, op):
    assert not mcp.call("update_work_package", work_package_id=1, clear_parent=True)[0]
    assert op.writes[0][2]["_links"]["parent"] == {"href": None}


def test_attachments_come_as_base64_not_a_path_on_the_server(mcp, op, tmp_path):
    secret = tmp_path / "server-file.txt"
    secret.write_text("the server's own file")
    err, out = mcp.call("add_attachment", work_package_id=1, file_path=str(secret))
    assert err and "biscuits mcp" in out["_error"]
    assert op.uploads == []

    err, out = mcp.call("add_attachment", work_package_id=1, content_base64="aGVsbG8=", file_name="dir/notes.txt")
    assert not err and out["attached"] and out["id"] == 77
    (path, raw, _), = op.uploads
    assert path == "/api/v3/work_packages/1/attachments?notify=false"
    assert b'filename="notes.txt"' in raw and b"Content-Type: text/plain" in raw and b"\r\nhello\r\n" in raw
    assert writes.recent(1)[0]["body"] == {"fileName": "notes.txt", "contentType": "text/plain", "size": 5}


@pytest.mark.parametrize("args, error", [
    ({}, "exactly one"),
    ({"content_base64": "aGk="}, "file_name is required"),
    ({"content_base64": "not base64!", "file_name": "x"}, "valid base64"),
])
def test_attachment_input_is_checked(mcp, op, args, error):
    err, out = mcp.call("add_attachment", work_package_id=1, **args)
    assert err and error in out["_error"]
    assert op.uploads == []


def test_bad_arguments_are_a_tool_error_not_a_crash(mcp):
    err, out = mcp.call("get_work_package")
    assert err and "bad arguments" in out["_error"]
    err, out = mcp.call("delete_everything")
    assert err and "unknown tool" in out["_error"]


def test_counts(mcp, op):
    err, out = mcp.call("count_work_packages", project_id=1)
    assert not err and out == {"project_id": 1, "total": 2, "open": 2}
