"""Snapshot of the OpenProject data biscuits needs, cached in memory + on disk.

The whole instance is small (~2.2k WPs), so we pull everything and do all
tree-building / filtering / sorting locally.

Each account has its own snapshot, fetched with its own key, so it holds exactly
what OpenProject lets that person see. A snapshot is only ever fetched during
that person's own requests.

- In-memory copy is served instantly; refreshed when older than TTL.
- On-disk copy (snapshots/<account id>.json in the data dir, mode 600) means a
  server restart shows data immediately, then refreshes.
- Only one refresh per account runs at a time.
- After a live write, `upsert()` patches the affected row in place so the UI
  doesn't need a full re-pull.
- If OpenProject stops accepting the key, the copy is dropped: someone locked in
  OpenProject must not go on reading a cached copy.
"""

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import client, config

TTL_SECONDS = 120

# Statuses that are not flagged isClosed in OP but that are used to mean
# "finished". Treated as done for the "hide finished" toggle.
DONE_STATUS_NAMES = {"completed"}

# Asana-style "Mark complete" / "reopen" map onto these OP statuses.
COMPLETE_STATUS_NAME = "Closed"
REOPEN_STATUS_NAME = "New"

# The Asana import turned each Asana *section* into a top-level
# "Summary task" whose subject is wrapped in [brackets].
SECTION_TYPE_NAME = "Summary task"
_SECTION_RE = re.compile(r"^\[(.*)\]$", re.S)

_lock = threading.Lock()
_states: dict[int, "_State"] = {}


class _State:
    def __init__(self):
        self.lock = threading.Lock()
        self.refreshing = threading.Lock()
        self.data = None
        self.fetched_at = 0.0
        self.error = None


def _state(uid) -> _State:
    with _lock:
        return _states.setdefault(uid, _State())


def _id_from_href(href):
    if not href:
        return None
    try:
        return int(href.rstrip("/").rsplit("/", 1)[-1])
    except ValueError:
        return None


def _link(e, key):
    lk = (e.get("_links") or {}).get(key) or {}
    return _id_from_href(lk.get("href")), lk.get("title")


def section_name(subject):
    m = _SECTION_RE.match(subject or "")
    return m.group(1).strip() if m else None


def make_row(e, status_meta, prio_meta):
    pid, pname = _link(e, "project")
    sid, sname = _link(e, "status")
    prid, prname = _link(e, "priority")
    tid, tname = _link(e, "type")
    aid, aname = _link(e, "assignee")
    rid, rname = _link(e, "responsible")
    author_id, author = _link(e, "author")
    parent_id, _ = _link(e, "parent")
    sm = status_meta.get(sid, {})
    subject = e.get("subject") or ""
    sec = section_name(subject) if (not parent_id and tname == SECTION_TYPE_NAME) else None
    links = e.get("_links") or {}
    # OP keeps computing derived dates from subtasks even for a manually
    # scheduled parent. There, a blank own date means blank: it has to stay blank.
    rollup = not e.get("scheduleManually")
    return {
        "id": e["id"],
        "subject": subject,
        "section": sec,  # non-null => render as an Asana-style section header
        "project_id": pid, "project": pname,
        "type_id": tid, "type": tname,
        "status_id": sid, "status": sname,
        "status_color": sm.get("color"),
        "done": sm.get("done", False),
        "priority_id": prid, "priority": prname,
        "priority_pos": prio_meta.get(prid, {}).get("position"),
        "assignee_id": aid, "assignee": aname,
        "responsible_id": rid, "responsible": rname,
        "author_id": author_id, "author": author,  # who created the task
        "start": e.get("startDate") or (e.get("derivedStartDate") if rollup else None),
        "due": e.get("dueDate") or (e.get("derivedDueDate") if rollup else None),
        "own_start": e.get("startDate"), "own_due": e.get("dueDate"),
        "pct": e.get("percentageDone"),
        "updated": (e.get("updatedAt") or "")[:10],
        "updated_at": e.get("updatedAt"),
        "created": (e.get("createdAt") or "")[:10],
        "parent_id": parent_id,
        # What this person's OP permissions allow, so the UI can say so up front.
        "can_edit": "update" in links or "updateImmediately" in links,
        "can_comment": "addComment" in links,
        "can_delete": "delete" in links,
    }


def _metas(statuses, priorities):
    status_meta = {
        s["id"]: {
            "name": s["name"],
            "color": s.get("color"),
            "closed": bool(s.get("isClosed")),
            "done": bool(s.get("isClosed")) or s["name"].lower() in DONE_STATUS_NAMES,
            "position": s.get("position"),
        }
        for s in statuses
    }
    prio_meta = {p["id"]: {"name": p["name"], "position": p.get("position"),
                           "color": p.get("color")} for p in priorities}
    return status_meta, prio_meta


def _people(op):
    """Who this person can see. Only OP admins may list /users."""
    try:
        return op.get_all("/users")
    except client.OPError as e:
        if e.status == 401:
            raise
    try:
        return op.get_all("/principals")
    except client.OPError as e:
        if e.status == 401:
            raise
        return []


def _fetch(op):
    with ThreadPoolExecutor(max_workers=5) as ex:
        f_wps = ex.submit(op.get_all, "/work_packages", {"filters": "[]"})
        f_projects = ex.submit(op.get_all, "/projects", {"filters": "[]"})
        f_statuses = ex.submit(op.get, "/statuses")
        f_priorities = ex.submit(op.get, "/priorities")
        f_types = ex.submit(op.get, "/types")
        f_users = ex.submit(_people, op)
        f_me = ex.submit(op.get, "/users/me")
        wps, projects = f_wps.result(), f_projects.result()
        statuses = f_statuses.result()["_embedded"]["elements"]
        priorities = f_priorities.result()["_embedded"]["elements"]
        types = f_types.result()["_embedded"]["elements"]
        users = f_users.result()
        me = f_me.result()

    status_meta, prio_meta = _metas(statuses, priorities)
    by_name = {v["name"]: k for k, v in status_meta.items()}
    people = {u["id"]: {"id": u["id"], "name": u["name"], "status": u.get("status")}
              for u in users if u.get("_type") == "User"}
    if me.get("id") is not None:
        people.setdefault(me["id"], {"id": me["id"], "name": me.get("name"), "status": me.get("status")})
    return {
        "work_packages": [make_row(e, status_meta, prio_meta) for e in wps],
        "projects": [
            {"id": p["id"], "name": p["name"], "identifier": p.get("identifier"),
             "active": p.get("active"),
             "can_manage": "update" in (p.get("_links") or {})}
            for p in projects
        ],
        "statuses": [dict(id=k, **v) for k, v in status_meta.items()],
        "priorities": [dict(id=k, **v) for k, v in prio_meta.items()],
        "types": [{"id": t["id"], "name": t["name"]} for t in types],
        "users": list(people.values()),
        "complete_status_id": by_name.get(COMPLETE_STATUS_NAME),
        "reopen_status_id": by_name.get(REOPEN_STATUS_NAME),
        "op_url": client.web_url(),
        "me": me.get("name"),
        "me_id": me.get("id"),
    }


def _path(uid):
    return config.load().snapshot_dir / f"{int(uid)}.json"


def _load_disk(uid, st):
    try:
        with open(_path(uid)) as f:
            blob = json.load(f)
        st.data, st.fetched_at = blob["data"], blob["fetched_at"]
    except (OSError, ValueError, KeyError):
        pass


def _save_disk(uid, st):
    path = _path(uid)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = f"{path}.tmp"
    # Task data is private: 0600 from the first byte.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"fetched_at": st.fetched_at, "data": st.data}, f)
    os.replace(tmp, path)


def forget(uid):
    """Drop an account's snapshot, in memory and on disk."""
    with _lock:
        _states.pop(uid, None)
    try:
        os.unlink(_path(uid))
    except OSError:
        pass


def refresh(uid, op):
    """Fetch a fresh snapshot (blocking). Concurrent callers wait for one fetch."""
    st = _state(uid)
    if not st.refreshing.acquire(blocking=False):
        with st.refreshing:  # someone else is fetching; wait for them
            return
    try:
        t0 = time.time()
        data = _fetch(op)
        with st.lock:
            data["fetch_seconds"] = round(time.time() - t0, 2)
            st.data, st.fetched_at, st.error = data, time.time(), None
        with _lock:
            current = _states.get(uid) is st
        if current:  # not forgotten while we were fetching
            _save_disk(uid, st)
    except client.OPError as e:
        if e.status == 401:
            forget(uid)
        st.error = str(e)
        raise
    except Exception as e:  # keep serving the stale copy
        st.error = str(e)
        raise
    finally:
        st.refreshing.release()


def get(uid, op, max_age=TTL_SECONDS):
    """Return (data, fetched_at, error). Fetches if missing or too old."""
    st = _state(uid)
    if st.data is None:
        _load_disk(uid, st)
    age = time.time() - st.fetched_at
    if st.data is None:
        refresh(uid, op)  # nothing to show yet: must block
    elif age > max_age and not st.refreshing.locked():
        # stale-while-revalidate: serve what we have, refresh in background
        threading.Thread(target=_safe_refresh, args=(uid, op), daemon=True).start()
    return st.data, st.fetched_at, st.error


def _row_from_element(uid, e):
    """Turn a WP HAL element into a biscuits row using the cached metadata."""
    data = _state(uid).data or {}
    status_meta = {s["id"]: s for s in data.get("statuses", [])}
    prio_meta = {p["id"]: p for p in data.get("priorities", [])}
    return make_row(e, status_meta, prio_meta)


def upsert(uid, e):
    """Patch (or add) one WP in this account's snapshot after a live write."""
    row = _row_from_element(uid, e)
    st = _state(uid)
    with st.lock:
        if st.data is not None:
            wps = st.data["work_packages"]
            for i, r in enumerate(wps):
                if r["id"] == row["id"]:
                    wps[i] = row
                    break
            else:
                wps.append(row)
    return row


def drop(wp_id):
    """Remove a deleted task from every account's cached snapshot. A task deleted in
    OpenProject is gone for everyone, so no one should keep seeing it until their
    next refresh."""
    with _lock:
        states = list(_states.items())
    for uid, st in states:
        with st.lock:
            if st.data is None:
                continue
            before = len(st.data["work_packages"])
            st.data["work_packages"] = [r for r in st.data["work_packages"] if r["id"] != wp_id]
            if len(st.data["work_packages"]) == before:
                continue
        _save_disk(uid, st)


def _safe_refresh(uid, op):
    try:
        refresh(uid, op)
    except Exception:
        pass
