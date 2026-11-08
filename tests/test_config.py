import stat

import pytest

from biscuits import config


def test_defaults_put_state_next_to_the_config(write_config, tmp_path):
    write_config()
    cfg = config.load()
    assert cfg.writes_mode == "preview"
    assert cfg.write_projects == frozenset() and not cfg.write_all_projects
    assert (cfg.host, cfg.port, cfg.base_path) == ("127.0.0.1", 9173, "")
    assert cfg.data_dir == tmp_path


def test_missing_file_says_how_to_fix_it(tmp_path):
    config.set_path(tmp_path / "nope.toml")
    with pytest.raises(config.ConfigError, match="biscuits config init"):
        config.load()
    config.set_path(None)


@pytest.mark.parametrize("projects,expected", [("[1, 2]", ({1, 2}, False)), ('"*"', (set(), True))])
def test_project_allow_list(write_config, projects, expected):
    write_config("live", projects)
    cfg = config.load()
    assert (set(cfg.write_projects), cfg.write_all_projects) == expected


@pytest.mark.parametrize("kwargs,message", [
    ({"mode": "liv"}, "mode"),
    ({"projects": '"all"'}, "projects"),
    ({"projects": "[true]"}, "projects"),
    ({"projects": '["1"]'}, "projects"),
    ({"url": "example.org"}, "http"),
    ({"extra": "[server]\nport = 99999\n"}, "port"),
    ({"extra": '[server]\nbase_path = "x"\n'}, "base_path"),
])
def test_invalid_values_are_rejected(write_config, kwargs, message):
    write_config(**kwargs)
    with pytest.raises(config.ConfigError, match=message):
        config.load()


def test_edits_apply_without_restart_and_breakage_fails_closed(write_config):
    path = write_config("live", '"*"')
    assert config.load().writes_mode == "live"
    write_config("preview", "[]")
    assert config.load().writes_mode == "preview"
    path.write_text("not [valid toml")
    with pytest.raises(config.ConfigError, match="TOML"):
        config.load()


def test_render_round_trips_awkward_values(tmp_path):
    path = tmp_path / "sub" / "config.toml"
    key = 'we"ird\\key'
    config.write_new(path, config.render("https://op.test/", key, "live", "*", "0.0.0.0", 80, "/b/"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    config.set_path(path)
    cfg = config.load()
    config.set_path(None)
    assert (cfg.op_url, cfg.op_admin_token, cfg.write_all_projects) == ("https://op.test", key, True)
    assert cfg.secret_key and cfg.encryption_key  # new configs get login secrets
    assert (cfg.host, cfg.port, cfg.base_path) == ("0.0.0.0", 80, "/b")
    with pytest.raises(FileExistsError):
        config.write_new(path, "x")


def test_trusted_proxy_setting(write_config):
    write_config(extra='[server]\ntrusted_proxy = "127.0.0.1"\n')
    assert config.load().trusted_proxy == "127.0.0.1"


def test_environment_is_optional_and_must_be_a_short_name(write_config):
    write_config()
    assert config.load().environment is None
    write_config(extra='[server]\nenvironment = "prod"\n')
    assert config.load().environment == "prod"
    for bad in ('""', '"has space"', '"<b>x</b>"', '"' + "x" * 21 + '"', "3"):
        write_config(extra=f"[server]\nenvironment = {bad}\n")
        with pytest.raises(config.ConfigError, match="environment"):
            config.load()


def test_a_bad_encryption_key_is_rejected(write_config):
    path = write_config()
    text = path.read_text()
    path.write_text(text[:text.index("encryption_key")] + 'encryption_key = "nope"\n')
    with pytest.raises(config.ConfigError, match="encryption_key"):
        config.load()


def test_permission_warning(write_config):
    path = write_config()
    assert config.permission_problem(path) is None
    path.chmod(0o644)
    assert "chmod 600" in config.permission_problem(path)
