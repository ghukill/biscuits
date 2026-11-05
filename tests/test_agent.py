import json
import os
import stat

import pytest
from click.testing import CliRunner

from biscuits import agent
from biscuits.cli import main

KEY = "agent-key-value"
BOT = {"id": 16, "name": "Biscuits Agent", "wps": [1, 2], "email": "bot@example.test"}


def section(tmp_path, token=KEY, project="56"):
    server = tmp_path / "server.py"
    server.write_text("")
    return f'[agent]\ntoken = "{token}"\nproject = {project}\nmcp_server = "{server}"\nworkdir = "{tmp_path}"\n'


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
    assert server["env"]["OPENPROJECT_API_KEY"] == KEY
    assert {key for _, _, key in op.requests} == {KEY}  # never the admin token
    assert path.exists()



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
