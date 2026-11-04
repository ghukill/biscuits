"""Per-user login: who gets in, and whose key and data each person's requests use."""

import ast
import json
from pathlib import Path

import pytest

import biscuits
from biscuits import accounts, config, snapshot
from biscuits.app import create_app
from conftest import ADMIN_TOKEN, H, PASSWORD, login, make_user

ALICE = {"id": 21, "name": "Alice", "wps": [1]}
BOB = {"id": 22, "name": "Bob", "wps": [2]}


@pytest.fixture
def team(write_config, op, web_as):
    """Two non-admin people who each see one task, as on a real instance."""
    write_config("live", '"*"')
    op.people = {"alice-key": ALICE, "bob-key": BOB,
                 ADMIN_TOKEN: {"id": 7, "name": "Pat", "wps": [], "admin": True}}
    return (web_as("alice", key="alice-key", op_user_id=21, admin=False),
            web_as("bob", key="bob-key", op_user_id=22, admin=False))


# ---------- getting in ----------

def test_nothing_is_reachable_without_signing_in(write_config, op):
    write_config("live", '"*"')
    web = create_app().test_client()
    for path in ["/", "/home", "/my", "/admin", "/account", "/project/1"]:
        r = web.get(path)
        assert r.status_code == 302 and "/login" in r.headers["Location"], path
    for path in ["/api/snapshot", "/api/wp/1", "/api/writes", "/api/account", "/api/account/key", "/api/admin/users"]:
        r = web.get(path)
        assert r.status_code == 401 and r.json["code"] == "login", path
    assert web.post("/api/wp/1", json={"changes": {"subject": "x"}}, headers=H).status_code == 401
    assert op.requests == []
    assert web.get("/login").status_code == 200
    assert web.get("/static/js/auth.js").status_code == 200
    assert web.get("/healthz").status_code == 200


def test_wrong_passwords_and_unknown_names_look_the_same(write_config):
    write_config()
    make_user()
    web = create_app().test_client()
    a, b = login(web, "pat", "wrong password"), login(web, "nobody", "wrong password")
    assert a.status_code == b.status_code == 401 and a.json == b.json


def test_login_itself_needs_the_csrf_header(write_config):
    write_config()
    make_user()
    r = create_app().test_client().post("/api/login", json={"username": "pat", "password": PASSWORD})
    assert r.status_code == 403


def test_failed_logins_are_rate_limited(write_config):
    write_config()
    make_user()
    web = create_app().test_client()
    for _ in range(5):
        assert login(web, "pat", "wrong password").status_code == 401
    # Even the right password is refused for now.
    assert login(web).status_code == 429


def test_rate_limit_is_also_per_address(write_config):
    write_config()
    web = create_app().test_client()
    for n in range(20):
        login(web, f"name{n}", "wrong password")
    assert login(web, "another", "wrong password").status_code == 429


def test_disabled_accounts_cant_sign_in_and_lose_their_session(write_config):
    write_config()
    user = make_user("alice", op_user_id=21, admin=False)
    make_user("admin2", op_user_id=7)
    web = create_app().test_client()
    assert login(web, "alice").status_code == 200
    assert web.get("/api/account").status_code == 200
    accounts.set_disabled(user.id, True)
    assert web.get("/api/account").status_code == 401
    assert login(web, "alice").status_code == 401


@pytest.mark.parametrize("target", ["//evil.example/x", "https://evil.example/", "/\\evil.example"])
def test_login_never_redirects_off_site(write_config, target):
    write_config()
    html = create_app().test_client().get("/login", query_string={"next": target}).text
    assert 'data-next="/"' in html


def test_login_keeps_a_local_destination(write_config):
    write_config()
    web = create_app().test_client()
    assert "/project/3?task=9" in web.get("/project/3?task=9").headers["Location"].replace("%3F", "?").replace("%3D", "=")
    assert 'data-next="/project/3?task=9"' in web.get("/login", query_string={"next": "/project/3?task=9"}).text.replace("&#61;", "=")


# ---------- passwords ----------

def test_first_sign_in_forces_a_password_change(write_config):
    write_config()
    make_user(must_change=True)
    web = create_app().test_client()
    assert login(web).json["must_change"] is True
    r = web.get("/api/snapshot")
    assert r.status_code == 403 and r.json["code"] == "must_change"
    assert "/password" in web.get("/home").headers["Location"]
    assert web.get("/password").status_code == 200
    r = web.post("/api/account/password", json={"current": PASSWORD, "new": "a new long password"}, headers=H)
    assert r.json["ok"] is True
    assert web.get("/api/snapshot").status_code == 200
    assert login(create_app().test_client(), password="a new long password").json["must_change"] is False


@pytest.mark.parametrize("current,new,why", [
    ("wrong password", "a new long password", "current password"),
    (PASSWORD, "short", "at least"),
    (PASSWORD, PASSWORD, "different"),
])
def test_bad_password_changes_are_refused(web_as, write_config, current, new, why):
    write_config()
    r = web_as().post("/api/account/password", json={"current": current, "new": new}, headers=H)
    assert r.status_code == 400 and why in r.json["error"]


def test_changing_a_password_signs_out_other_browsers_only(web_as, write_config):
    write_config()
    here = web_as()
    there = create_app().test_client()
    login(there)
    here.post("/api/account/password", json={"current": PASSWORD, "new": "a new long password"}, headers=H)
    assert here.get("/api/account").status_code == 200
    assert there.get("/api/account").status_code == 401


def test_admin_reset_gives_a_temporary_password_and_signs_them_out(web_as, write_config):
    write_config()
    admin = web_as("pat")
    other = web_as("alice", op_user_id=21, admin=False)
    uid = accounts.by_username("alice").id
    temp = admin.post(f"/api/admin/users/{uid}/password", json={}, headers=H).json["password"]
    assert other.get("/api/account").status_code == 401
    fresh = create_app().test_client()
    assert login(fresh, "alice", temp).json["must_change"] is True


def test_passwords_are_hashed(write_config):
    write_config()
    assert PASSWORD not in make_user().password_hash


# ---------- cookies and HTTPS ----------

def test_cookie_flags_on_plain_http(write_config):
    write_config()
    make_user()
    cookie = login(create_app().test_client()).headers["Set-Cookie"]
    assert cookie.startswith("biscuits_session=")
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Secure" not in cookie


def test_cookie_is_secure_and_banner_clears_on_https(write_config):
    write_config()
    make_user()
    web = create_app().test_client()
    cookie = web.post("/api/login", json={"username": "pat", "password": PASSWORD}, headers=H,
                      base_url="https://biscuits.test").headers["Set-Cookie"]
    assert "Secure" in cookie
    assert "isn&#39;t HTTPS" not in web.get("/login", base_url="https://biscuits.test").text
    assert "isn&#39;t HTTPS" not in web.get("/home", base_url="https://biscuits.test").text
    assert "isn't HTTPS" not in web.get("/home", base_url="https://biscuits.test").text


def test_banner_shows_on_http_and_forwarded_proto_alone_is_not_believed(web_as, write_config):
    write_config()
    web = web_as()
    assert "isn't HTTPS" in web.get("/home").text
    assert "isn't HTTPS" in web.get("/home", headers={"X-Forwarded-Proto": "https"}).text


# ---------- whose key, whose data ----------

def test_each_person_only_gets_what_their_own_key_can_see(team):
    alice, bob = team
    assert [r["id"] for r in alice.get("/api/snapshot").json["data"]["work_packages"]] == [1]
    assert [r["id"] for r in bob.get("/api/snapshot").json["data"]["work_packages"]] == [2]
    assert alice.get("/api/snapshot").json["data"]["me"] == "Alice"
    r = alice.get("/api/wp/2")
    assert r.status_code == 404 and "access" in r.json["error"]


def test_snapshots_are_separate_private_files(team, write_config):
    alice, bob = team
    alice.get("/api/snapshot"), bob.get("/api/snapshot")
    uid = accounts.by_username("alice").id
    path = config.load().snapshot_dir / f"{uid}.json"
    assert path.stat().st_mode & 0o077 == 0
    assert [r["id"] for r in json.loads(path.read_text())["data"]["work_packages"]] == [1]


def test_snapshot_is_only_fetched_on_that_persons_own_request(team, op):
    alice, _ = team
    alice.get("/api/snapshot")
    assert "bob-key" not in op.keys_used_for("/work_packages")


def test_writes_use_the_users_key_and_are_logged_with_who(team, op):
    alice, _ = team
    assert alice.post("/api/wp/1", json={"changes": {"subject": "x"}}, headers=H).json["sent"] is True
    assert alice.post("/api/wp/1/comment", json={"text": "hello"}, headers=H).json["sent"] is True
    assert alice.post("/api/wp", json={"subject": "new", "project_id": 1}, headers=H).json["sent"] is True
    assert op.keys_used_for("/work_packages") == {"alice-key"}
    actor = alice.get("/api/writes").json["entries"][0]["actor"]
    assert actor["username"] == "alice" and actor["op_user_id"] == 21


def test_the_admin_token_never_touches_tasks(team, op):
    alice, _ = team
    op.activities = [{"id": 1, "_links": {"user": {"href": "/api/v3/users/8"}}}]
    alice.get("/api/snapshot")
    alice.get("/api/wp/1")
    alice.get("/api/wp/2")  # a task they can't see
    alice.post("/api/wp/1", json={"changes": {"subject": "x"}}, headers=H)
    alice.post("/api/wp/2", json={"changes": {"subject": "x"}}, headers=H)
    alice.get("/api/assignees/1")
    assert ADMIN_TOKEN not in op.keys_used_for("/work_packages")
    assert ADMIN_TOKEN not in op.keys_used_for("/projects")


def test_a_refused_write_is_not_retried_with_the_admin_token(team, op):
    alice, _ = team
    op.deny_writes = 403
    r = alice.post("/api/wp/1", json={"changes": {"subject": "x"}}, headers=H)
    assert r.status_code == 403
    writes_sent = [req for req in op.requests if req[0] != "GET"]
    assert [w[2] for w in writes_sent] == ["alice-key"]


def test_only_opadmin_may_touch_the_admin_token():
    """config.py defines it; nothing else but opadmin.py may read it."""
    src = Path(biscuits.__file__).parent
    users = set()
    for path in src.rglob("*.py"):
        tree = ast.parse(path.read_text())
        if any(isinstance(n, ast.Attribute) and n.attr == "op_admin_token" for n in ast.walk(tree)):
            users.add(path.name)
    assert users == {"opadmin.py", "cli.py"}  # cli.py only prints it masked


def test_cli_only_masks_the_admin_token():
    text = (Path(biscuits.__file__).parent / "cli.py").read_text()
    lines = [ln for ln in text.splitlines() if "op_admin_token" in ln]
    assert len(lines) == 1 and "[-4:]" in lines[0] and "'*' * 8" in lines[0]


def test_names_on_old_comments_come_through_for_non_admins(team, op):
    alice, _ = team
    op.activities = [{"id": 1, "_links": {"user": {"href": "/api/v3/users/8"}}, "comment": {"raw": "hi"}}]
    assert alice.get("/api/wp/1").json["activities"][0]["user"] == "Old Author"


def test_browser_only_gets_the_one_name_it_needs(team, op):
    alice, _ = team
    body = alice.get("/api/snapshot").text
    assert "Old Author" not in body  # only an admin token can list that person
    assert '"Alice"' in body


def test_non_admins_get_people_from_principals(team):
    alice, _ = team
    users = alice.get("/api/snapshot").json["data"]["users"]
    assert [u["name"] for u in users] == ["Alice"]  # and not the group


def test_write_log_shows_non_admins_only_their_own(team, web_as):
    alice, bob = team
    alice.post("/api/wp/1", json={"changes": {"subject": "alice edit"}}, headers=H)
    bob.post("/api/wp/2", json={"changes": {"subject": "bob edit"}}, headers=H)
    assert [e["actor"]["username"] for e in alice.get("/api/writes").json["entries"]] == ["alice"]
    admin = web_as("root", key=ADMIN_TOKEN, op_user_id=7, admin=True)
    assert {e["actor"]["username"] for e in admin.get("/api/writes").json["entries"]} == {"alice", "bob"}


def test_write_gate_still_applies_on_top(team, write_config, op):
    alice, _ = team
    write_config("preview", '"*"')
    assert alice.post("/api/wp/1", json={"changes": {"subject": "x"}}, headers=H).json["sent"] is False
    assert op.writes == []


# ---------- keys ----------

def test_keys_are_encrypted_at_rest_and_never_in_the_snapshot(team):
    alice, _ = team
    db_bytes = config.load().db_file.read_bytes()
    assert b"alice-key" not in db_bytes
    assert "alice-key" not in alice.get("/api/snapshot").text
    assert "alice-key" not in alice.get("/api/account").text


def test_a_leaked_database_alone_doesnt_give_up_keys(team, write_config):
    user = accounts.by_username("alice")
    assert accounts.get_key(user) == "alice-key"
    other = __import__("cryptography.fernet", fromlist=["Fernet"]).Fernet.generate_key().decode()
    path = config.load().path
    path.write_text(path.read_text().replace(config.load().encryption_key, other))
    assert accounts.get_key(user) is None


def test_you_can_reveal_your_own_key_but_not_someone_elses(team):
    alice, _ = team
    r = alice.get("/api/account/key")
    assert r.json["key"] == "alice-key" and r.headers["Cache-Control"] == "no-store"
    bob_id = accounts.by_username("bob").id
    assert alice.get(f"/api/admin/users/{bob_id}/key").status_code == 403


def test_saving_a_key_checks_whose_it_is(team, web_as):
    root = web_as("root", key=ADMIN_TOKEN, op_user_id=7, admin=True)
    uid = accounts.by_username("alice").id
    url = f"/api/admin/users/{uid}/key"
    r = root.post(url, json={"key": "bob-key", "op_user_id": 21}, headers=H)
    assert r.status_code == 400 and "Bob" in r.json["error"]
    r = root.post(url, json={"key": "no-such-key", "op_user_id": 21}, headers=H)
    assert r.status_code == 400 and "didn't accept" in r.json["error"]
    assert accounts.get_key(accounts.by_username("alice")) == "alice-key"  # unchanged
    r = root.post(url, json={"key": "alice-key", "op_user_id": 21}, headers=H)
    assert r.json["user"]["op_user_name"] == "Alice"
    assert root.get(url).json["key"] == "alice-key"


def test_two_accounts_cant_share_an_openproject_user(team, web_as):
    root = web_as("root", key=ADMIN_TOKEN, op_user_id=7, admin=True)
    uid = accounts.by_username("alice").id
    r = root.post(f"/api/admin/users/{uid}/key", json={"key": "bob-key", "op_user_id": 22}, headers=H)
    assert r.status_code == 400 and "already linked" in r.json["error"]


def test_a_revoked_key_gives_a_clear_state_and_drops_the_cached_copy(team, op):
    alice, _ = team
    assert alice.get("/api/snapshot").status_code == 200
    uid = accounts.by_username("alice").id
    assert (config.load().snapshot_dir / f"{uid}.json").exists()
    del op.people["alice-key"]  # locked in OpenProject
    r = alice.post("/api/refresh", headers=H)
    assert r.status_code == 409 and r.json["code"] == "key_invalid" and "admin" in r.json["error"]
    assert not (config.load().snapshot_dir / f"{uid}.json").exists()
    assert alice.get("/api/snapshot").json["code"] == "key_invalid"  # no stale copy served
    r = alice.post("/api/wp/1", json={"changes": {"subject": "x"}}, headers=H)
    assert r.status_code == 409 and r.json["code"] == "key_invalid"
    assert alice.get("/api/account").status_code == 200  # still signed in, not bounced to login


def test_background_refresh_with_a_revoked_key_also_drops_the_copy(team, op):
    alice, _ = team
    alice.get("/api/snapshot")
    uid = accounts.by_username("alice").id
    del op.people["alice-key"]
    from biscuits import client
    snapshot._safe_refresh(uid, client.OP("alice-key"))
    assert uid not in snapshot._states


def test_an_account_with_no_key_gets_a_clear_state(write_config, web_as):
    write_config()
    web = web_as("newbie", key=None, op_user_id=None, admin=False)
    r = web.get("/api/snapshot")
    assert r.status_code == 409 and r.json["code"] == "no_key"
    assert web.get("/home").status_code == 200
    assert web.get("/api/account").json["account"]["key_masked"] is None


# ---------- admin screens ----------

@pytest.mark.parametrize("method,path", [
    ("get", "/api/admin/users"), ("get", "/api/admin/op-users"), ("post", "/api/admin/users"),
    ("post", "/api/admin/users/1"), ("post", "/api/admin/users/1/key"), ("get", "/api/admin/users/1/key"),
    ("post", "/api/admin/users/1/password"), ("post", "/api/admin/users/1/disabled"),
])
def test_admin_apis_refuse_non_admins(web_as, write_config, method, path):
    write_config()
    web = web_as("alice", admin=False)
    r = getattr(web, method)(path, headers=H, **({"json": {}} if method == "post" else {}))
    assert r.status_code == 403


def test_create_account_linking_an_existing_openproject_user(web_as, write_config):
    write_config("live", '"*"')
    admin = web_as()
    r = admin.post("/api/admin/users", json={"username": "New.Person", "op_user_id": 8}, headers=H)
    assert r.json["user"]["username"] == "new.person"
    assert r.json["user"]["op_user_name"] == "Old Author"
    assert r.headers["Cache-Control"] == "no-store"
    assert login(create_app().test_client(), "new.person", r.json["password"]).json["must_change"] is True
    assert "User.find(ID)" in admin.get("/api/admin/users").json["token_template"]


def test_create_account_with_a_new_openproject_user(web_as, write_config, op):
    write_config("live", '"*"')
    admin = web_as()
    new_op = {"login": "np", "first_name": "New", "last_name": "Person", "email": "np@example.invalid"}
    r = admin.post("/api/admin/users", json={"username": "np", "new_op_user": new_op}, headers=H)
    assert r.json["user"]["op_user_id"] == 50
    method, path, body = op.writes[0]
    assert (method, path) == ("POST", "/api/v3/users") and body["admin"] is False
    log = config.load().write_log_file.read_text()
    assert body["password"] not in log and '"create-op-user"' in log
    assert op.requests[-1][2] == ADMIN_TOKEN


def test_a_bad_username_creates_nothing_in_openproject(web_as, write_config, op):
    write_config("live", '"*"')
    admin = web_as()
    new_op = {"login": "np", "first_name": "N", "last_name": "P", "email": "np@example.invalid"}
    assert admin.post("/api/admin/users", json={"username": "!", "new_op_user": new_op}, headers=H).status_code == 400
    assert op.writes == []


def test_disable_can_lock_in_openproject_with_the_admin_token(web_as, write_config, op):
    write_config("live", '"*"')
    admin = web_as()
    web_as("alice", op_user_id=21, admin=False)
    uid = accounts.by_username("alice").id
    r = admin.post(f"/api/admin/users/{uid}/disabled", json={"disabled": True, "lock_in_openproject": True}, headers=H)
    assert r.json["op_locked"] is True
    assert op.writes[-1][:2] == ("POST", "/api/v3/users/21/lock")
    assert op.requests[-1][2] == ADMIN_TOKEN
    r = admin.post(f"/api/admin/users/{uid}/disabled", json={"disabled": False}, headers=H)
    assert r.json["user"]["disabled"] is False


def test_preview_mode_doesnt_lock_in_openproject(web_as, write_config, op):
    write_config()
    admin = web_as()
    web_as("alice", op_user_id=21, admin=False)
    uid = accounts.by_username("alice").id
    r = admin.post(f"/api/admin/users/{uid}/disabled", json={"disabled": True, "lock_in_openproject": True}, headers=H)
    assert r.status_code == 502 and "preview" in r.json["error"]
    assert op.writes == []


def test_you_cant_lock_yourself_out(web_as, write_config):
    write_config()
    admin = web_as()
    uid = accounts.by_username("pat").id
    assert admin.post(f"/api/admin/users/{uid}/disabled", json={"disabled": True}, headers=H).status_code == 400
    assert admin.post(f"/api/admin/users/{uid}", json={"is_admin": False}, headers=H).status_code == 400


def test_the_last_admin_is_protected(write_config):
    write_config()
    user = make_user()
    with pytest.raises(accounts.AccountError, match="last admin"):
        accounts.set_disabled(user.id, True)
    with pytest.raises(accounts.AccountError, match="last admin"):
        accounts.update(user.id, is_admin=False)


# ---------- archiving, now shared ----------

def test_archiving_needs_edit_rights_on_the_project(team, op):
    alice, _ = team
    assert alice.post("/api/project/1/archive", json={"archived": True}, headers=H).status_code == 403
    op.project_links = {"update": {"href": "/api/v3/projects/1"}}
    assert alice.post("/api/project/1/archive", json={"archived": True}, headers=H).json["archived_projects"] == [1]


def test_archived_ids_are_limited_to_projects_you_can_see(team, web_as):
    alice, _ = team
    admin = web_as("root", key=ADMIN_TOKEN, op_user_id=7, admin=True)
    admin.post("/api/project/999/archive", json={"archived": True}, headers=H)
    assert alice.get("/api/snapshot").json["archived_projects"] == []
