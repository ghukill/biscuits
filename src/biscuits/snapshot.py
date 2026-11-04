"""Snapshot of the OpenProject data biscuits needs, cached in memory + on disk.

The whole instance is small (~2.2k WPs), so we pull everything and do all
tree-building / filtering / sorting locally.

- In-memory copy is served instantly; refreshed when older than TTL.
- On-disk copy (snapshot.json in the data dir) means a server restart shows
  data immediately, then refreshes.
- Only one refresh runs at a time.
- After a live write, `upsert()` patches the affected row in place so the UI
  doesn't need a full re-pull.
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
_SECTION_RE = re.compile(r"^\[(.*)\]$", re.S)

_lock = threading.Lock()
_refreshing = threading.Lock()
_state = {"data": None, "fetched_at": 0.0, "error": None}


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
    parent_id, _ = _link(e, "parent")
    sm = status_meta.get(sid, {})
    subject = e.get("subject") or ""
    sec = section_name(subject) if (not parent_id and tname == "Summary task") else None
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
        "start": e.get("startDate") or (e.get("derivedStartDate") if rollup else None),
        "due": e.get("dueDate") or (e.get("derivedDueDate") if rollup else None),
        "own_start": e.get("startDate"), "own_due": e.get("dueDate"),
        "pct": e.get("percentageDone"),
        "updated": (e.get("updatedAt") or "")[:10],
        "updated_at": e.get("updatedAt"),
        "created": (e.get("createdAt") or "")[:10],
        "parent_id": parent_id,
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


def _fetch():
    with ThreadPoolExecutor(max_workers=5) as ex:
        f_wps = ex.submit(client.get_all, "/work_packages", {"filters": "[]"})
        f_projects = ex.submit(client.get_all, "/projects", {"filters": "[]"})
        f_statuses = ex.submit(client.get, "/statuses")
        f_priorities = ex.submit(client.get, "/priorities")
        f_types = ex.submit(client.get, "/types")
        f_users = ex.submit(client.get_all, "/users")
        f_me = ex.submit(client.get, "/users/me")
        wps, projects = f_wps.result(), f_projects.result()
        statuses = f_statuses.result()["_embedded"]["elements"]
        priorities = f_priorities.result()["_embedded"]["elements"]
        types = f_types.result()["_embedded"]["elements"]
        try:
            users = f_users.result()
        except client.OPError:  # non-admin keys can't list users
            users = []
        me = f_me.result()

    status_meta, prio_meta = _metas(statuses, priorities)
    by_name = {v["name"]: k for k, v in status_meta.items()}
    return {
        "work_packages": [make_row(e, status_meta, prio_meta) for e in wps],
        "projects": [
            {"id": p["id"], "name": p["name"], "identifier": p.get("identifier"),
             "active": p.get("active")}
            for p in projects
        ],
        "statuses": [dict(id=k, **v) for k, v in status_meta.items()],
        "priorities": [dict(id=k, **v) for k, v in prio_meta.items()],
        "types": [{"id": t["id"], "name": t["name"]} for t in types],
        "users": [{"id": u["id"], "name": u["name"], "status": u.get("status")}
                  for u in users if u.get("_type") == "User"],
        "complete_status_id": by_name.get(COMPLETE_STATUS_NAME),
        "reopen_status_id": by_name.get(REOPEN_STATUS_NAME),
        "op_url": client.web_url(),
        "me": me.get("name"),
        "me_id": me.get("id"),
    }


def _load_disk():
    try:
        with open(config.load().snapshot_file) as f:
            blob = json.load(f)
        _state.update(data=blob["data"], fetched_at=blob["fetched_at"])
    except (OSError, ValueError, KeyError):
        pass


def _save_disk():
    path = config.load().snapshot_file
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = f"{path}.tmp"
    # Task data is private: 0600 from the first byte.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"fetched_at": _state["fetched_at"], "data": _state["data"]}, f)
    os.replace(tmp, path)


def refresh():
    """Fetch a fresh snapshot (blocking). Concurrent callers wait for one fetch."""
    if not _refreshing.acquire(blocking=False):
        with _refreshing:  # someone else is fetching; wait for them
            return
    try:
        t0 = time.time()
        data = _fetch()
        with _lock:
            _state.update(data=data, fetched_at=time.time(), error=None)
            _state["data"]["fetch_seconds"] = round(time.time() - t0, 2)
        _save_disk()
    except Exception as e:  # keep serving the stale copy
        _state["error"] = str(e)
        raise
    finally:
        _refreshing.release()


def get(max_age=TTL_SECONDS):
    """Return (data, fetched_at, error). Fetches if missing or too old."""
    if _state["data"] is None:
        _load_disk()
    age = time.time() - _state["fetched_at"]
    if _state["data"] is None:
        refresh()  # nothing to show yet: must block
    elif age > max_age and not _refreshing.locked():
        # stale-while-revalidate: serve what we have, refresh in background
        threading.Thread(target=_safe_refresh, daemon=True).start()
    return _state["data"], _state["fetched_at"], _state["error"]


def row_from_element(e):
    """Turn a WP HAL element into a biscuits row using the cached metadata."""
    data = _state["data"] or {}
    status_meta = {s["id"]: s for s in data.get("statuses", [])}
    prio_meta = {p["id"]: p for p in data.get("priorities", [])}
    return make_row(e, status_meta, prio_meta)


def upsert(e):
    """Patch (or add) one WP in the cached snapshot after a live write."""
    row = row_from_element(e)
    with _lock:
        data = _state["data"]
        if data is not None:
            wps = data["work_packages"]
            for i, r in enumerate(wps):
                if r["id"] == row["id"]:
                    wps[i] = row
                    break
            else:
                wps.append(row)
    return row


def _safe_refresh():
    try:
        refresh()
    except Exception:
        pass
