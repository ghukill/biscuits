import json
import os
import stat
import sys

import pytest
from click.testing import CliRunner

from biscuits import agent
from biscuits.cli import main

KEY = "agent-key-value"
BOT = {"id": 16, "name": "Biscuits Agent", "wps": [1, 2], "email": "bot@example.test"}


def section(tmp_path, token=KEY, project="56"):
    return f'[agent]\ntoken = "{token}"\nproject = {project}\nworkdir = "{tmp_path}"\n'


@pytest.fixture
def launched(monkeypatch):
    """Capture what would have been exec'd, instead of starting Claude Code."""
    calls = []
    monkeypatch.setattr(agent.shutil, "which", lambda name: "/bin/claude")
    monkeypatch.setattr(agent.os, "execv", lambda path, argv: calls.append(argv))
    monkeypatch.setattr(agent.os, "chdir", lambda path: None)
    return calls


def run():
    return CliRunner().invoke(main, ["agent"])


def test_session_is_signed_in_as_the_agent_and_lists_its_tasks(write_config, op, tmp_path, launched):
    op.people = {KEY: BOT}
    path = write_config(extra=section(tmp_path))
    r = run()
    assert r.exit_code == 0, r.output

    argv = launched[0]
    assert "--strict-mcp-config" in argv
    assert KEY not in " ".join(argv)  # stays out of `ps`
    greeting = argv[-1]
    assert "I have 2 tasks assigned to me" in greeting and "A task" in greeting

    mcp = argv[argv.index("--mcp-config") + 1]
    assert stat.S_IMODE(os.stat(mcp).st_mode) == 0o600
    server = json.load(open(mcp))["mcpServers"]["openproject"]
    assert server["command"] == sys.executable and server["args"] == ["-m", "biscuits", "mcp-direct"]
    assert server["env"]["OPENPROJECT_API_KEY"] == KEY
    assert {key for _, _, key in op.requests} == {KEY}  # never the admin token
    assert path.exists()


def test_an_old_mcp_server_setting_is_ignored(write_config, op, tmp_path, launched):
    op.people = {KEY: BOT}
    write_config(extra=section(tmp_path) + 'mcp_server = "/no/such/server.py"\n')
    assert run().exit_code == 0
    mcp = launched[0][launched[0].index("--mcp-config") + 1]
    assert json.load(open(mcp))["mcpServers"]["openproject"]["args"] == ["-m", "biscuits", "mcp-direct"]


def test_refuses_an_openproject_admin_token(write_config, op, tmp_path, launched):
    op.people = {KEY: {**BOT, "admin": True}}
    write_config(extra=section(tmp_path))
    r = run()
    assert r.exit_code == 1 and "non-admin" in r.output
    assert not launched


def test_without_an_agent_section_says_how_to_fix_it(write_config, launched):
    write_config()
    r = run()
    assert r.exit_code == 1 and "[agent]" in r.output
    assert not launched


def test_a_bad_token_is_a_clean_error(write_config, op, tmp_path, launched):
    op.people = {"someone-else": BOT}
    write_config(extra=section(tmp_path))
    r = run()
    assert r.exit_code == 1 and "401" in r.output
    assert not launched


def test_project_must_be_an_id(write_config, tmp_path, launched):
    write_config(extra=section(tmp_path, project='"56"'))
    r = run()
    assert r.exit_code == 1 and "project must be a project id" in r.output


def test_config_show_never_prints_the_token(write_config, tmp_path):
    write_config(extra=section(tmp_path))
    r = CliRunner().invoke(main, ["config", "show"])
    assert "project 56, token set" in r.output and KEY not in r.output


def test_docs_folder_is_passed_to_the_session(write_config, op, tmp_path, launched):
    op.people = {KEY: BOT}
    write_config(extra=section(tmp_path) + f'docs = "{tmp_path}/planning"\n')
    assert run().exit_code == 0
    argv = launched[0]
    assert argv[argv.index("--add-dir") + 1] == f"{tmp_path}/planning"
    assert f"outside the repo in {tmp_path}/planning" in argv[argv.index("--append-system-prompt") + 1]


def test_docs_folder_is_optional(write_config, op, tmp_path, launched):
    op.people = {KEY: BOT}
    write_config(extra=section(tmp_path))
    assert run().exit_code == 0
    argv = launched[0]
    assert "--add-dir" not in argv
    assert "Planning docs" not in argv[argv.index("--append-system-prompt") + 1]


def test_the_rules_cover_dev_deploys_and_releases():
    from biscuits.agent import _ROLE
    role = " ".join(_ROLE.split())
    assert "Don't change the version: only a release does that." in role
    assert "merge your branch into the `dev` branch" in role
    assert "Don't merge to main, push, release or touch prod unless admin says to in this session." in role
    assert "`release` skill" in role


def test_with_an_mcp_url_the_session_goes_through_biscuits(write_config, op, tmp_path, launched):
    op.people = {KEY: BOT}
    token_file = tmp_path / "agent.token"
    token_file.write_text("bsk_tok\n")
    write_config(extra=section(tmp_path) + f'mcp_url = "http://your-biscuits-host:8000/mcp/"\n'
                                          f'mcp_token_file = "{token_file}"\n')
    assert run().exit_code == 0
    argv = launched[0]
    mcp = argv[argv.index("--mcp-config") + 1]
    assert stat.S_IMODE(os.stat(mcp).st_mode) == 0o600
    server = json.load(open(mcp))["mcpServers"]["openproject"]
    assert server == {"command": sys.executable, "args": ["-m", "biscuits", "mcp"],
                      "env": {"BISCUITS_MCP_URL": "http://your-biscuits-host:8000/mcp",
                              "BISCUITS_MCP_TOKEN_FILE": str(token_file)}}
    text = open(mcp).read()
    assert "bsk_tok" not in text and KEY not in text  # neither the token nor the OpenProject key


def test_a_missing_token_file_stops_the_launch(write_config, op, tmp_path, launched):
    op.people = {KEY: BOT}
    write_config(extra=section(tmp_path) + f'mcp_url = "http://x/mcp"\nmcp_token_file = "{tmp_path}/nope"\n')
    r = run()
    assert r.exit_code == 1 and "doesn't exist" in r.output
    assert not launched


@pytest.mark.parametrize("extra, error", [
    ('mcp_url = "http://x/mcp"\n', "go together"),
    ('mcp_token_file = "/x"\n', "go together"),
    ('mcp_url = "x/mcp"\nmcp_token_file = "/x"\n', "must start with http"),
])
def test_mcp_settings_are_checked(write_config, tmp_path, launched, extra, error):
    write_config(extra=section(tmp_path) + extra)
    r = run()
    assert r.exit_code == 1 and error in r.output
