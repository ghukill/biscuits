import socket
import stat

import pytest
from click.testing import CliRunner

from biscuits import config, daemon
from biscuits.cli import main


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
    r = run("--config", write_config(), "doctor")
    assert r.exit_code == 0, r.output
    assert "as Pat" in r.output and "admin" in r.output and "2 work packages" in r.output
    r = run("--config", write_config(url="http://127.0.0.1:9"), "doctor")
    assert r.exit_code == 1 and "FAIL" in r.output


def test_snapshot_commands(write_config):
    path = write_config()
    assert run("--config", path, "snapshot", "info").exit_code == 1
    r = run("--config", path, "snapshot", "refresh")
    assert r.exit_code == 0 and "2 work packages" in r.output
    assert "work packages:  2" in run("--config", path, "snapshot", "info").output


def test_writes_commands(write_config):
    path = write_config()
    from biscuits import writes
    writes.update(1, {"subject": "x"})
    assert "1 not sent" in run("--config", path, "writes", "status").output
    assert "PATCH /work_packages/1" in run("--config", path, "writes", "log").output


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
