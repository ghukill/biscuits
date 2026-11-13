"""Bulk edit (#3506) and moving several tasks at once (#3497): every task goes
through the same gate and log as a single edit, and each one succeeds or fails
on its own."""

import pytest

from biscuits import writes
from conftest import H


def test_each_task_is_its_own_gated_logged_write(write_config, op, conn):
    write_config("live", "[1]")
    out = writes.bulk_update(conn, [{"id": 1, "changes": {"status_id": 2}},
                                    {"id": 2, "changes": {"status_id": 2}}])  # stub: WP 2 is in project 2
    assert [(o["id"], o["ok"], o["sent"]) for o in out] == [(1, True, True), (2, True, False)]
    assert [w[:2] for w in op.writes] == [("PATCH", "/api/v3/work_packages/1")]
    assert [e["wp"] for e in writes.recent(5)] == [2, 1]


def test_a_failed_task_doesnt_stop_the_rest(write_config, op, conn):
    write_config("live", '"*"')
    out = writes.bulk_update(conn, [{"id": 1, "changes": {"due": "soon"}},
                                    {"id": 2, "changes": {"due": "2026-11-20"}}])
    assert out[0]["ok"] is False and "YYYY-MM-DD" in out[0]["error"]
    assert out[1]["ok"] is True and out[1]["sent"] is True
    assert [w[1] for w in op.writes] == ["/api/v3/work_packages/2"]


def test_a_rejected_key_stops_everything(write_config, op, conn):
    write_config("live", '"*"')
    op.deny_writes = 401
    with pytest.raises(writes.WriteError):
        writes.bulk_update(conn, [{"id": 1, "changes": {"status_id": 2}}, {"id": 2, "changes": {"status_id": 2}}])
    assert len([r for r in op.requests if r[0] == "PATCH"]) == 1


@pytest.mark.parametrize("items", [
    None, [], {"id": 1}, [{"id": "1", "changes": {"status_id": 2}}], [{"id": 1}], [{"id": 1, "changes": {}}],
    [{"id": 1, "changes": {"status_id": 2}}, {"id": 1, "changes": {"due": None}}],
    [{"id": n, "changes": {"status_id": 2}} for n in range(writes.MAX_BULK + 1)],
])
def test_a_malformed_bulk_edit_sends_nothing(write_config, op, conn, items):
    write_config("live", '"*"')
    with pytest.raises(writes.WriteError):
        writes.bulk_update(conn, items)
    assert op.writes == [] and writes.recent(5) == []


def test_the_api_reports_every_task(write_config, web_as, op):
    write_config("live", "[1]")
    web = web_as()
    r = web.post("/api/wp/bulk", headers=H, json={"items": [
        {"id": 1, "changes": {"parent_id": None}},   # e.g. moved out of a section
        {"id": 2, "changes": {"due": "later"}},
    ]})
    assert r.status_code == 200
    one, two = r.json["results"]
    assert one["ok"] and one["sent"] and one["row"]["id"] == 1
    assert not two["ok"] and two["error"] and two["row"] is None


def test_the_api_refuses_a_malformed_request(write_config, web_as, op):
    write_config("live", '"*"')
    web = web_as()
    r = web.post("/api/wp/bulk", headers=H, json={"items": []})
    assert r.status_code == 400 and r.json["ok"] is False
    assert op.writes == []


def test_bulk_edit_needs_the_csrf_header(write_config, web_as, op):
    write_config("live", '"*"')
    web = web_as()
    r = web.post("/api/wp/bulk", json={"items": [{"id": 1, "changes": {"status_id": 2}}]})
    assert r.status_code in (400, 403) and op.writes == []
