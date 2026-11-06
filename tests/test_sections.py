"""Adding, renaming and ordering sections, against the stub OpenProject."""

import pytest

from biscuits import db, sections, writes
from conftest import H, wp


def section(id, project=1, name="Grants", can_edit=True):
    links = {"type": {"href": "/api/v3/types/3", "title": "Summary task"}}
    if can_edit:
        links["update"] = {"href": f"/api/v3/work_packages/{id}"}
    return wp(id, project, subject=f"[{name}]", links=links)


def post_order(web, order, project=1):
    return web.post(f"/api/project/{project}/sections/order", json={"order": order}, headers=H)


def test_new_section_is_a_bracketed_top_level_summary_task(write_config, op, web_as):
    write_config("live", '"*"')
    web = web_as()
    r = web.post("/api/project/1/sections", json={"name": "  Grants   submitted "}, headers=H)
    assert r.json["sent"] is True
    method, path, body = op.writes[0]
    assert (method, path) == ("POST", "/api/v3/work_packages")
    assert body["subject"] == "[Grants submitted]"
    assert body["_links"]["type"]["href"] == "/api/v3/types/3"
    assert body["_links"]["project"]["href"] == "/api/v3/projects/1"
    assert "parent" not in body["_links"]


def test_adding_a_section_respects_the_write_gate(write_config, op, web_as):
    write_config("preview", '"*"')
    r = web_as().post("/api/project/1/sections", json={"name": "Grants"}, headers=H)
    assert r.json["sent"] is False
    assert op.writes == []


@pytest.mark.parametrize("name", [None, "", "   "])
def test_a_section_needs_a_name(write_config, op, web_as, name):
    write_config("live", '"*"')
    assert web_as().post("/api/project/1/sections", json={"name": name}, headers=H).status_code == 400
    assert op.writes == []


def test_a_long_section_name_keeps_its_closing_bracket(write_config, op, conn):
    write_config("live", '"*"')
    writes.create_section(conn, 1, "x" * 400, 3)
    subject = op.writes[0][2]["subject"]
    assert len(subject) == writes.MAX_SUBJECT and subject.startswith("[") and subject.endswith("]")


def test_renaming_a_section_is_an_ordinary_gated_update(write_config, op, conn):
    write_config("live", '"*"')
    writes.update(conn, 1, {"subject": "[New name]"})
    assert op.writes[0][1] == "/api/v3/work_packages/1"
    assert op.writes[0][2]["subject"] == "[New name]"


def test_order_is_saved_in_biscuits_and_never_reaches_openproject(write_config, op, web_as):
    write_config("live", '"*"')
    op.extra_wps = [section(10), section(11), section(12)]
    web = web_as()
    assert web.get("/api/snapshot").json["section_order"] == []
    assert post_order(web, [12, 10, 11]).json["ok"] is True
    assert web.get("/api/snapshot").json["section_order"] == [12, 10, 11]
    assert post_order(web, [11, 12, 10]).status_code == 200
    assert web.get("/api/snapshot").json["section_order"] == [11, 12, 10]
    assert op.writes == []


def test_a_section_left_out_loses_its_stored_position(write_config, op, web_as):
    write_config("live", '"*"')
    op.extra_wps = [section(10), section(11)]
    web = web_as()
    post_order(web, [11, 10])
    post_order(web, [10])
    assert web.get("/api/snapshot").json["section_order"] == [10]


def test_orders_in_different_projects_stay_separate(write_config):
    write_config("live", '"*"')
    sections.set_order(1, [12, 10])
    sections.set_order(2, [21, 20])
    sections.set_order(1, [10, 12])
    assert sections.order() == [10, 12, 21, 20]


def test_a_section_moved_to_another_project_keeps_one_position(write_config):
    write_config("live", '"*"')
    sections.set_order(1, [10, 11])
    sections.set_order(2, [10])
    assert sections.order() == [11, 10]


def test_snapshot_only_lists_sections_this_person_can_see(write_config, op, web_as):
    write_config("live", '"*"')
    op.extra_wps = [section(10)]
    web = web_as()
    sections.set_order(1, [10, 999])
    assert web.get("/api/snapshot").json["section_order"] == [10]


@pytest.mark.parametrize("order", [None, [], "10", [10, 10], [10, "11"], [True], [10.5]])
def test_a_malformed_order_is_refused(write_config, op, web_as, order):
    write_config("live", '"*"')
    op.extra_wps = [section(10), section(11)]
    assert post_order(web_as(), order).status_code == 400


@pytest.mark.parametrize("bad", [1, 20, 999], ids=["plain task", "other project", "unknown"])
def test_only_this_projects_sections_can_be_ordered(write_config, op, web_as, bad):
    write_config("live", '"*"')
    op.extra_wps = [section(10), section(20, project=2)]
    web = web_as()
    assert post_order(web, [10, bad]).status_code == 400
    assert sections.order() == []


def test_order_needs_the_csrf_header(write_config, op, web_as):
    write_config("live", '"*"')
    op.extra_wps = [section(10)]
    assert web_as().post("/api/project/1/sections/order", json={"order": [10]}).status_code == 403


def test_order_stays_put_in_a_preview_project(write_config, op, web_as):
    write_config("live", "[2]")
    op.extra_wps = [section(10)]
    assert post_order(web_as(), [10]).status_code == 409
    assert sections.order() == []


def test_order_needs_permission_to_edit_the_sections(write_config, op, web_as):
    write_config("live", '"*"')
    op.extra_wps = [section(10), section(11, can_edit=False)]
    web = web_as(admin=False)
    assert post_order(web, [11, 10]).status_code == 403
    assert sections.order() == []


def test_a_biscuits_admin_can_order_sections_they_cant_edit(write_config, op, web_as):
    write_config("live", '"*"')
    op.extra_wps = [section(10, can_edit=False)]
    assert post_order(web_as(admin=True), [10]).status_code == 200


def test_order_rows_are_in_the_biscuits_database(write_config):
    write_config("live", '"*"')
    sections.set_order(1, [10], by=5)
    with db.session() as s:
        row = s.get(db.SectionPosition, 10)
        assert (row.project_id, row.position, row.updated_by) == (1, 0, 5)
