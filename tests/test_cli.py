import socket
import stat

import pytest
from click.testing import CliRunner

from biscuits import accounts, client, config, daemon
from biscuits.cli import main
from conftest import ADMIN_TOKEN, make_user


def run(*args, **kwargs):
    return CliRunner().invoke(main, [str(a) for a in args], **kwargs)


@pytest.fixture(autouse=True)
def reset_path():
    yield
    config.set_path(None)


def test_init_from_env_file_keeps_settings_and_is_private(tmp_path):
    env = tmp_path / "op.env"
    env.write_text(
        "# comment\nOPENPROJECT_URL=https://op.test\nOPENPROJECT_API_KEY=abc123\n"
        "BISCUITS_WRITES=live\nBISCUITS_WRITE_PROJECTS=*\n"
    )
    path = tmp_path / "c" / "config.toml"
    r = run("--config", path, "config", "init", "--from-env", env)
    assert r.exit_code == 0, r.output
    assert "LIVE for all projects" in r.output
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # Refuses to clobber.
    assert run("--config", path, "config", "init", "--from-env", env).exit_code == 1


def test_init_defaults_to_preview(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENPROJECT_API_KEY", "abc")
    r = run("--config", tmp_path / "config.toml", "config", "init", "--url", "https://op.test")
    assert r.exit_code == 0, r.output
    assert "preview" in r.output


def test_show_masks_the_key(write_config):
    r = run("--config", write_config(), "config", "show")
    assert r.exit_code == 0 and "secret-key-value" not in r.output


def test_missing_config_is_one_clean_line(tmp_path):
    r = run("--config", tmp_path / "x.toml", "status")
    assert r.exit_code == 1 and "biscuits config init" in r.output
    assert "Traceback" not in r.output


def test_doctor_passes_then_fails_when_op_is_down(write_config):
    path = write_config()
    make_user()
    r = run("--config", path, "doctor")
    assert r.exit_code == 0, r.output
    assert "as Pat" in r.output and "1 active account" in r.output
    r = run("--config", write_config(url="http://127.0.0.1:9"), "doctor")
    assert r.exit_code == 1 and "FAIL" in r.output


def test_doctor_fails_without_an_admin_or_login_secrets(write_config):
    path = write_config()
    assert "no admin account" in run("--config", path, "doctor").output
    path.write_text(path.read_text().split("[auth]")[0])
    r = run("--config", path, "doctor")
    assert r.exit_code == 1 and "add-auth-keys" in r.output


def test_doctor_warns_when_the_admin_token_is_someones_own_key(write_config):
    path = write_config()
    make_user(key=ADMIN_TOKEN, op_user_id=7)
    assert "dedicated account is better" in run("--config", path, "doctor").output


def test_add_auth_keys_upgrades_an_old_config_once(write_config):
    path = write_config()
    path.write_text(path.read_text().split("[auth]")[0].replace("admin_token", "api_key"))
    assert "Added" in run("--config", path, "config", "add-auth-keys").output
    cfg = config.load()
    assert cfg.secret_key and cfg.encryption_key and cfg.op_admin_token == ADMIN_TOKEN
    before = path.read_text()
    assert "Nothing changed" in run("--config", path, "config", "add-auth-keys").output
    assert path.read_text() == before


def test_snapshot_info_lists_each_account(write_config, op):
    from biscuits import snapshot
    path = write_config()
    assert "No snapshots" in run("--config", path, "snapshot", "info").output
    snapshot.refresh(3, client.OP("k"))
    assert "account 3: Pat, 2 work packages" in run("--config", path, "snapshot", "info").output


def test_writes_commands(write_config):
    path = write_config()
    from biscuits import writes
    writes.update(client.OP("k", actor={"username": "pat"}), 1, {"subject": "x"})
    assert "1 not sent" in run("--config", path, "writes", "status").output
    output = run("--config", path, "writes", "log").output
    assert "PATCH /work_packages/1" in output and "pat" in output


def test_users_add_first_admin_and_list(write_config):
    path = write_config()
    r = run("--config", path, "users", "add", "Graham", "--admin")
    assert r.exit_code == 0, r.output
    temp = r.output.split("Temporary password: ")[1].split()[0]
    user = accounts.authenticate("graham", temp)
    assert user.is_admin and user.must_change_password
    assert "graham" in run("--config", path, "users", "list").output
    assert run("--config", path, "users", "add", "graham").exit_code == 1


def test_users_set_key_validates_and_a_bad_key_leaves_no_account(write_config, op):
    path = write_config()
    op.people = {"alice-key": {"id": 21, "name": "Alice", "wps": []}}
    r = run("--config", path, "users", "add", "alice", "--op-user", 99, "--key-stdin", input="alice-key\n")
    assert r.exit_code == 1 and "belongs to Alice" in r.output
    assert accounts.by_username("alice") is None
    r = run("--config", path, "users", "add", "alice", "--op-user", 21, "--key-stdin", input="alice-key\n")
    assert r.exit_code == 0, r.output
    user = accounts.by_username("alice")
    assert accounts.get_key(user) == "alice-key" and user.display_name == "Alice"
    r = run("--config", path, "users", "set-key", "alice", "--key-stdin", input="wrong\n")
    assert r.exit_code == 1 and "didn't accept" in r.output


def test_users_reset_disable_enable(write_config):
    path = write_config()
    make_user("root")
    make_user("alice", op_user_id=21, admin=False)
    temp = run("--config", path, "users", "reset-password", "alice").output.split(": ")[1].strip()
    assert accounts.authenticate("alice", temp)
    assert "disabled" in run("--config", path, "users", "disable", "alice").output
    assert accounts.authenticate("alice", temp) is None
    assert "enabled" in run("--config", path, "users", "enable", "alice").output
    assert run("--config", path, "users", "disable", "nobody").exit_code == 1


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_daemon_lifecycle(write_config):
    port = _free_port()
    path = write_config(extra=f"[server]\nport = {port}\n")
    cfg = config.load()
    base = ("--config", path)
    assert run(*base, "status").exit_code == 1
    assert "not running" in run(*base, "stop").output

    r = run(*base, "serve", "-d")
    assert r.exit_code == 0, r.output
    try:
        pid = daemon.running_pid(cfg)
        assert pid and daemon.health("127.0.0.1", port)
        assert f"pid {pid}" in run(*base, "status").output
        # A second copy is refused, in both modes, and doesn't disturb the first.
        assert "already running" in run(*base, "serve", "-d").output
        assert "already running" in run(*base, "serve").output
        assert daemon.running_pid(cfg) == pid

        r = run(*base, "restart")
        assert r.exit_code == 0, r.output
        new_pid = daemon.running_pid(cfg)
        assert new_pid != pid and daemon.health("127.0.0.1", port)
    finally:
        assert "stopped" in run(*base, "stop").output
    assert daemon.running_pid(cfg) is None
    assert not daemon.health("127.0.0.1", port)
    assert "biscuits stopped" in cfg.log_file.read_text()


def test_port_in_use_fails_with_a_message(write_config):
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        path = write_config(extra=f"[server]\nport = {port}\n")
        r = run("--config", path, "serve", "-d")
    assert r.exit_code == 1 and "exited during startup" in r.output
    assert "Can't listen" in config.load().log_file.read_text()


def test_tokens_add_list_and_revoke(write_config, tmp_path):
    write_config()
    make_user("biscuits-agent", admin=False, op_user_id=16)
    r = run("tokens", "add", "biscuits-agent", "--name", "mattermost")
    assert r.exit_code == 0, r.output
    token = r.output.strip().splitlines()[-1]
    assert token.startswith("bsk_")

    out = tmp_path / "agent.token"
    r = run("tokens", "add", "biscuits-agent", "--name", "agent session", "--out", out)
    assert r.exit_code == 0 and "bsk_" not in r.output
    assert stat.S_IMODE(out.stat().st_mode) == 0o600 and out.read_text().startswith("bsk_")

    r = run("tokens", "list")
    assert "mattermost" in r.output and "agent session" in r.output and "never used" in r.output
    assert token not in r.output

    r = run("tokens", "revoke", "1")
    assert r.exit_code == 0 and "revoked" in r.output
    assert "REVOKED" in run("tokens", "list").output


def test_tokens_need_an_account_with_a_key(write_config):
    write_config()
    make_user("keyless", key=None, admin=False)
    assert "No account called nobody" in run("tokens", "add", "nobody", "--name", "x").output
    r = run("tokens", "add", "keyless", "--name", "x")
    assert r.exit_code == 1 and "no OpenProject key" in r.output
    r = run("tokens", "revoke", "99")
    assert r.exit_code == 1 and "No such token" in r.output
