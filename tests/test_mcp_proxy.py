"""`biscuits mcp`, the stdio client that passes everything to a biscuits server."""

import json
import os
import subprocess
import sys
import threading

import pytest
from werkzeug.serving import make_server

from biscuits import tokens, writes
from biscuits.app import create_app

from conftest import make_user

BOT_KEY = "bot-op-key"


@pytest.fixture
def served(write_config, op, tmp_path):
    """A real biscuits server on a free port, and a token file for its bot account."""
    write_config("live", '"*"')
    user = make_user("biscuits-agent", key=BOT_KEY, op_user_id=16, admin=False)
    _, token = tokens.create(user.id, "proxy-test")
    token_file = tmp_path / "token"
    token_file.write_text(token + "\n")
    server = make_server("127.0.0.1", 0, create_app(), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield {"url": f"http://127.0.0.1:{server.server_port}/mcp", "token_file": str(token_file), "token": token}
    server.shutdown()


def clean_env(tmp_path, **extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OPENPROJECT_", "BISCUITS_"))}
    env["HOME"] = str(tmp_path / "home")  # no ~/.biscuits to fall back on
    env.update(extra)
    return env


def run(messages, env, cwd):
    proc = subprocess.run([sys.executable, "-m", "biscuits", "mcp"],
                          input="".join(json.dumps(m) + "\n" for m in messages),
                          capture_output=True, text=True, env=env, cwd=cwd, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return [json.loads(line) for line in proc.stdout.splitlines()], proc.stderr


def msg(id, method, params=None):
    return {"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}


def tool(id, name, **arguments):
    return msg(id, "tools/call", {"name": name, "arguments": arguments})


def test_end_to_end_through_biscuits(served, op, tmp_path):
    shot = tmp_path / "mockup.png"
    shot.write_bytes(b"\x89PNGdata")
    env = clean_env(tmp_path, BISCUITS_MCP_URL=served["url"], BISCUITS_MCP_TOKEN_FILE=served["token_file"])
    replies, stderr = run([
        msg(1, "initialize", {"protocolVersion": "2025-06-18"}),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        msg(2, "tools/list"),
        tool(3, "get_work_package", work_package_id=1),
        tool(4, "add_comment", work_package_id=1, comment="via the proxy"),
        tool(5, "add_attachment", work_package_id=1, file_path=str(shot)),
    ], env, tmp_path)
    init, listing, task, comment, attached = replies  # nothing for the notification
    assert init["result"]["serverInfo"]["name"] == "biscuits"
    assert "list_sections" in [t["name"] for t in listing["result"]["tools"]]
    assert json.loads(task["result"]["content"][0]["text"])["id"] == 1
    assert not comment["result"]["isError"]
    assert not attached["result"]["isError"], attached
    assert "forwarding to" in stderr

    # It all reached OpenProject as the bot, through biscuits' gate and log.
    assert {key for _, _, key in op.requests} == {BOT_KEY}
    (_, raw, _), = op.uploads
    assert b'filename="mockup.png"' in raw and b"\x89PNGdata" in raw
    assert [e["op"] for e in writes.recent(5)] == ["attach", "comment"]
    assert all(e["actor"]["via"] == "mcp" for e in writes.recent(5))
    assert served["token"] not in stderr


def test_the_token_can_come_from_the_environment(served, tmp_path):
    env = clean_env(tmp_path, BISCUITS_MCP_URL=served["url"], BISCUITS_MCP_TOKEN=served["token"])
    (reply,), _ = run([msg(1, "ping")], env, tmp_path)
    assert reply["result"] == {}


def test_a_bad_token_is_an_error_reply(served, tmp_path):
    env = clean_env(tmp_path, BISCUITS_MCP_URL=served["url"], BISCUITS_MCP_TOKEN="bsk_nope")
    (reply,), _ = run([msg(1, "tools/list")], env, tmp_path)
    assert "valid biscuits API token" in reply["error"]["message"]


def test_a_missing_local_file_is_refused_before_sending(served, op, tmp_path):
    env = clean_env(tmp_path, BISCUITS_MCP_URL=served["url"], BISCUITS_MCP_TOKEN_FILE=served["token_file"])
    (reply,), _ = run([tool(1, "add_attachment", work_package_id=1, file_path="/no/such/file.png")], env, tmp_path)
    assert reply["result"]["isError"] and "no such file" in reply["result"]["content"][0]["text"]
    assert op.uploads == []


def test_an_unreachable_server_is_an_error_reply_not_a_crash(tmp_path):
    env = clean_env(tmp_path, BISCUITS_MCP_URL="http://127.0.0.1:9/mcp", BISCUITS_MCP_TOKEN="bsk_x")
    (reply,), _ = run([msg(1, "initialize")], env, tmp_path)
    assert "can't reach biscuits" in reply["error"]["message"]


def test_not_configured_says_what_to_set(tmp_path):
    (reply,), stderr = run([msg(1, "tools/list")], clean_env(tmp_path), tmp_path)
    assert "BISCUITS_MCP_URL" in reply["error"]["message"] and "BISCUITS_MCP_URL" in stderr
    assert not (tmp_path / "home").exists()  # it didn't create a biscuits directory


def test_old_setups_still_work_for_now_and_are_told_to_switch(tmp_path):
    env = clean_env(tmp_path, OPENPROJECT_URL="https://op.test", OPENPROJECT_API_KEY="k")
    (listing,), stderr = run([msg(1, "tools/list")], env, tmp_path)
    assert "add_attachment" in [t["name"] for t in listing["result"]["tools"]]
    assert "talking to OpenProject directly" in stderr


def test_it_imports_only_the_standard_library():
    code = (
        "import sys; base = set(sys.modules); import biscuits.mcp_proxy; "
        "new = {m.split('.')[0] for m in set(sys.modules) - base}; "
        "print(sorted(new - set(sys.stdlib_module_names) - {'biscuits'}))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"
