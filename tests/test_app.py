import pytest

from biscuits import config, snapshot
from biscuits.app import create_app
from conftest import H, login, make_user


@pytest.fixture
def web(write_config, web_as):
    write_config()
    return web_as()


def test_shell_routes_and_static_assets(web):
    for path in ["/", "/home", "/my", "/all", "/search", "/log", "/archived", "/project/3", "/people/3"]:
        assert web.get(path).status_code == 200, path
    html = web.get("/").text
    assert 'data-base=""' in html
    for path in ["/static/js/main.js", "/static/biscuits.css", "/static/vendor/tabulator/tabulator.min.js"]:
        assert web.get(path).status_code == 200, path


def test_sidebar_shows_the_environment_and_version(write_config, web_as):
    from biscuits import __version__
    write_config()
    web = web_as()
    assert f'class="side-build"' in web.get("/").text
    assert f">v{__version__}</span>" in web.get("/").text
    # Naming the environment needs no restart.
    write_config(extra='[server]\nenvironment = "dev"\n')
    assert f">dev @ {__version__}</span>" in web.get("/").text


def test_healthz_needs_nothing_from_openproject(write_config):
    write_config(url="http://127.0.0.1:9")
    assert create_app().test_client().get("/healthz").json["status"] == "ok"


def test_starting_without_login_secrets_says_how_to_fix_it(write_config):
    path = write_config()
    path.write_text(path.read_text().split("[auth]")[0])
    with pytest.raises(config.ConfigError, match="add-auth-keys"):
        create_app()


def test_base_path(write_config):
    write_config(extra='[server]\nbase_path = "/biscuits/"\n')
    web = create_app().test_client()
    make_user()
    assert web.get("/").headers["Location"] == "/biscuits/"
    assert "/biscuits/login" in web.get("/biscuits/").headers["Location"]
    r = login(web, prefix="/biscuits")
    assert "Path=/biscuits" in r.headers["Set-Cookie"]
    html = web.get("/biscuits/").text
    assert 'data-base="/biscuits"' in html and "/biscuits/static/js/main.js" in html
    assert web.get("/biscuits/static/js/main.js").status_code == 200
    assert web.get("/my").status_code == 404


def test_snapshot_never_exposes_the_key(web):
    r = web.get("/api/snapshot")
    assert r.status_code == 200
    assert r.json["writes"] == {"mode": "preview", "write_projects": [], "all_projects": False}
    assert len(r.json["data"]["work_packages"]) == 2
    assert "secret-key-value" not in r.text


def test_posts_require_the_csrf_header(write_config, op, web_as):
    write_config("live", '"*"')
    web = web_as()
    assert web.post("/api/wp/1", json={"changes": {"subject": "x"}}).status_code == 403
    assert web.post("/api/refresh").status_code == 403
    assert op.writes == []
    assert web.post("/api/wp/1", json={"changes": {"subject": "x"}}, headers=H).json["sent"] is True


def test_openproject_down_is_a_json_error(write_config, web_as):
    write_config(url="http://127.0.0.1:9")
    web = web_as()
    r = web.get("/api/snapshot")
    assert r.status_code == 502 and "error" in r.json
    assert web.get("/api/wp/1").status_code == 502


def test_config_broken_after_startup_is_a_json_error(write_config, web_as):
    path = write_config()
    web = web_as()
    path.write_text("broken = [")
    r = web.post("/api/wp/1", json={"changes": {"subject": "x"}}, headers=H)
    assert r.status_code == 500 and "TOML" in r.json["error"]


def test_archiving_is_a_biscuits_flag_and_never_reaches_openproject(write_config, op, web_as):
    write_config("live", '"*"')
    web = web_as()
    assert web.get("/api/snapshot").json["archived_projects"] == []
    assert web.post("/api/project/1/archive", json={"archived": True}, headers=H).json["archived_projects"] == [1]
    assert web.post("/api/project/1/archive", json={"archived": True}, headers=H).json["archived_projects"] == [1]
    assert web.get("/api/snapshot").json["archived_projects"] == [1]
    assert web.post("/api/project/1/archive", json={"archived": False}, headers=H).json["archived_projects"] == []
    assert op.writes == []


def test_archiving_needs_the_csrf_header_and_a_real_boolean(web):
    assert web.post("/api/project/1/archive", json={"archived": True}).status_code == 403
    assert web.post("/api/project/1/archive", json={"archived": "yes"}, headers=H).status_code == 400


def test_snapshot_and_detail_name_who_created_the_task(web):
    row = web.get("/api/snapshot").json["data"]["work_packages"][0]
    assert (row["author_id"], row["author"]) == (9, "Cass Creator")
    detail = web.get("/api/wp/1").json["row"]
    assert (detail["author_id"], detail["author"]) == (9, "Cass Creator")


def test_a_task_with_no_author_link_has_a_blank_creator():
    row = snapshot.make_row({"id": 1, "_links": {}}, {}, {})
    assert (row["author_id"], row["author"]) == (None, None)


def test_a_live_write_keeps_the_creator_on_the_row(write_config, web_as):
    write_config("live", '"*"')
    web = web_as()
    r = web.post("/api/wp", json={"project_id": 1, "subject": "New"}, headers=H)
    assert r.json["row"]["author"] == "Cass Creator"
