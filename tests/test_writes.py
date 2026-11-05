"""The write gate is the safety boundary, so test it against real HTTP."""

import json

import pytest

from biscuits import config, snapshot, writes


def test_preview_sends_nothing_but_is_logged(write_config, op, conn):
    write_config("preview", '"*"')
    out = writes.update(conn, 1, {"subject": "New name"})
    assert out["sent"] is False and out["reason"] == "preview mode"
    assert op.writes == []
    assert writes.recent(1)[0]["sent"] is False


def test_live_only_for_allow_listed_projects(write_config, op, conn):
    write_config("live", "[1]")
    assert writes.update(conn, 1, {"subject": "x"})["sent"] is True
    assert writes.update(conn, 2, {"subject": "x"})["sent"] is False  # stub: WP 2 is in project 2
    assert [w[1] for w in op.writes] == ["/api/v3/work_packages/1"]


def test_moving_under_a_parent_needs_both_projects_allowed(write_config, op, conn):
    write_config("live", "[1]")
    assert writes.update(conn, 1, {"parent_id": 2})["sent"] is False
    write_config("live", "[1, 2]")
    assert writes.update(conn, 1, {"parent_id": 2})["sent"] is True


def test_star_allows_everything(write_config, op, conn):
    write_config("live", '"*"')
    assert writes.comment(conn, 5, "hello")["sent"] is True
    assert op.writes[0][:2] == ("POST", "/api/v3/work_packages/5/activities")


def test_gate_change_applies_to_the_next_write(write_config, op, conn):
    write_config("live", '"*"')
    assert writes.update(conn, 1, {"subject": "x"})["sent"] is True
    write_config("preview", '"*"')
    assert writes.update(conn, 1, {"subject": "y"})["sent"] is False
    assert len(op.writes) == 1


def test_broken_config_blocks_writes(write_config, op, conn):
    path = write_config("live", '"*"')
    path.write_text("broken = [")
    with pytest.raises(config.ConfigError):
        writes.update(conn, 1, {"subject": "x"})
    assert op.writes == []


def test_write_log_is_private_and_excludes_the_key(write_config, op, conn):
    write_config("live", '"*"')
    writes.update(conn, 1, {"subject": "x"})
    log = config.load().write_log_file
    assert log.stat().st_mode & 0o077 == 0
    assert "secret-key-value" not in log.read_text()
    assert json.loads(log.read_text().splitlines()[0])["op"] == "update"


def test_no_delete_operation_exists():
    assert not hasattr(writes, "delete")


@pytest.mark.parametrize("changes", [{"subject": " "}, {"nope": 1}, {"due": "soon"}, {}])
def test_bad_edits_are_refused(write_config, op, conn, changes):
    write_config("live", '"*"')
    with pytest.raises(writes.WriteError):
        writes.update(conn, 1, changes)
    assert op.writes == []


def test_date_edits_take_a_rolled_up_parent_out_of_automatic_scheduling(write_config, op, conn):
    write_config("live", '"*"')
    writes.update(conn, 1, {"due": None, "start": None})  # stub WPs aren't scheduleManually
    body = op.writes[0][2]
    assert body["dueDate"] is None and body["startDate"] is None
    assert body["scheduleManually"] is True


def test_date_edits_keep_weekend_dates(write_config, op, conn):
    write_config("live", '"*"')
    writes.update(conn, 1, {"due": "2026-11-21"})
    assert op.writes[0][2]["ignoreNonWorkingDays"] is True
    writes.create(conn, {"subject": "x", "project_id": 1, "due": "2026-11-21"})
    assert op.writes[1][2]["ignoreNonWorkingDays"] is True


def test_other_edits_leave_scheduling_alone(write_config, op, conn):
    write_config("live", '"*"')
    writes.update(conn, 1, {"subject": "x"})
    assert "scheduleManually" not in op.writes[0][2]
    assert "ignoreNonWorkingDays" not in op.writes[0][2]


@pytest.mark.parametrize("manual,expected", [(True, None), (False, "2026-12-15")])
def test_derived_dates_only_show_for_rolled_up_tasks(manual, expected):
    e = {"id": 1, "subject": "Parent", "scheduleManually": manual, "_links": {},
         "dueDate": None, "derivedDueDate": "2026-12-15",
         "startDate": None, "derivedStartDate": "2026-12-15"}
    row = snapshot.make_row(e, {}, {})
    assert row["due"] == expected and row["start"] == expected


def test_the_creator_cannot_be_changed(write_config, op, conn):
    write_config("live", '"*"')
    with pytest.raises(writes.WriteError):
        writes.update(conn, 1, {"author_id": 8})
    assert op.writes == []
