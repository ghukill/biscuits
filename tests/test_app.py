import pytest

from biscuits.app import create_app

H = {"X-Biscuits": "1"}


@pytest.fixture
def web(write_config):
    write_config()
    return create_app().test_client()


def test_shell_routes_and_static_assets(web):
    for path in ["/", "/home", "/my", "/all", "/search", "/log", "/project/3", "/people/3"]:
        assert web.get(path).status_code == 200, path
    html = web.get("/").text
    assert 'data-base=""' in html
    for path in ["/static/js/main.js", "/static/biscuits.css", "/static/vendor/tabulator/tabulator.min.js"]:
        assert web.get(path).status_code == 200, path


def test_healthz_needs_nothing_from_openproject(web):
    assert web.get("/healthz").json["status"] == "ok"


def test_base_path(write_config):
    write_config(extra='[server]\nbase_path = "/biscuits/"\n')
    web = create_app().test_client()
    assert web.get("/").headers["Location"] == "/biscuits/"
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


def test_posts_require_the_csrf_header(write_config, op):
    write_config("live", '"*"')
    web = create_app().test_client()
    assert web.post("/api/wp/1", json={"changes": {"subject": "x"}}).status_code == 403
    assert web.post("/api/refresh").status_code == 403
    assert op.writes == []
    assert web.post("/api/wp/1", json={"changes": {"subject": "x"}}, headers=H).json["sent"] is True


def test_openproject_down_is_a_json_error(write_config):
    write_config(url="http://127.0.0.1:9")
    web = create_app().test_client()
    r = web.get("/api/snapshot")
    assert r.status_code == 502 and "error" in r.json
    assert web.get("/api/wp/1").status_code == 502


def test_config_broken_after_startup_is_a_json_error(write_config):
    path = write_config()
    web = create_app().test_client()
    path.write_text("broken = [")
    r = web.post("/api/wp/1", json={"changes": {"subject": "x"}}, headers=H)
    assert r.status_code == 500 and "TOML" in r.json["error"]
