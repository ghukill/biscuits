"""biscuits: an Asana-flavoured front-end for OpenProject.

Paths below are relative to server.base_path in the config ("/" by default).

The UI is a single-page shell; these paths all serve it and the client-side
router picks the view:
  /                 Home (greeting + time buckets)
  /my               My tasks (Asana-style, grouped by due date)
  /all              All tasks (filterable tree table)
  /project/<id>     Project: list (sections) or ?view=board
  /people/<id>      Someone else's tasks
  /search?q=        Search results
  /log              Write log (what biscuits sent / would have sent)
  /archived         Archived projects
Any of them accept ?task=<id> to open the task detail pane.

JSON API:
  GET  /api/snapshot         Everything the UI needs (cached, see snapshot.py)
  POST /api/refresh          Re-pull the snapshot (GETs only)
  GET  /api/wp/<id>          Live detail: description + activity feed
  POST /api/wp/<id>          Update fields          } gated by writes.py:
  POST /api/wp/<id>/comment  Add a comment          } live only for allow-listed
  POST /api/wp               Create a task/subtask  } projects, else preview
  GET  /api/assignees/<pid>  Who can be assigned in a project
  GET  /api/writes           Recent write-log entries
  POST /api/project/<id>/archive   Archive or unarchive (biscuits only, not sent to OP)
"""

import time

from flask import Blueprint, Response, abort, jsonify, render_template, request

from . import archive, client, snapshot, writes

bp = Blueprint(
    "biscuits",
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/static",
)


def _shell():
    resp = Response(render_template("biscuits/app.html"))
    resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp


@bp.get("/")
def index():
    return _shell()


@bp.get("/<any(home, my, all, search, log, archived):view>")
@bp.get("/project/<int:pid>")
@bp.get("/people/<int:uid>")
def views(**_):
    return _shell()


# ---------- JSON API ----------

def _gate_info():
    mode, projects, everything = writes.gate()
    return {"mode": mode, "write_projects": sorted(projects), "all_projects": everything}


@bp.get("/api/snapshot")
def api_snapshot():
    try:
        data, fetched_at, error = snapshot.get()
    except client.OPError as e:
        return jsonify(error=str(e)), 502
    if data is None:  # a concurrent first fetch failed
        return jsonify(error=error or "No data from OpenProject yet"), 502
    return jsonify(
        data=data,
        fetched_at=fetched_at,
        age_seconds=round(time.time() - fetched_at),
        stale_error=error,
        writes=_gate_info(),
        archived_projects=archive.ids(),
    )


@bp.post("/api/refresh")
def api_refresh():
    _guard()
    try:
        snapshot.refresh()
    except client.OPError as e:
        return jsonify(ok=False, error=str(e)), 502
    return api_snapshot()


def _activity(a, names):
    uid = writes._id(((a.get("_links") or {}).get("user") or {}).get("href"))
    c = a.get("comment") or {}
    return {
        "id": a.get("id"),
        "kind": "comment" if (c.get("raw") or "").strip() else "change",
        "user_id": uid,
        "user": names.get(uid),
        "at": a.get("createdAt"),
        "comment_html": c.get("html") or "",
        "comment_raw": c.get("raw") or "",
        "details": [d.get("html") or d.get("raw") or "" for d in a.get("details") or []],
    }


@bp.get("/api/wp/<int:wp_id>")
def api_wp(wp_id):
    try:
        e = client.get(f"/work_packages/{wp_id}")
        acts = client.get(f"/work_packages/{wp_id}/activities")
    except client.OPError as err:
        return jsonify(error=str(err)), err.status or 502
    data, _, _ = snapshot.get()
    names = {u["id"]: u["name"] for u in (data or {}).get("users", [])}
    row = snapshot.upsert(e)
    emb = e.get("_embedded") or {}
    return jsonify(
        row=row,
        description=e.get("description") or {},
        attachments=[{"name": a.get("fileName"), "size": a.get("fileSize")}
                     for a in ((emb.get("attachments") or {}).get("_embedded") or {}).get("elements", [])],
        activities=[_activity(a, names) for a in acts.get("_embedded", {}).get("elements", [])],
        live=writes.writable(row["project_id"]),
    )


def _guard():
    # Cheap CSRF guard: browsers won't send a custom header cross-site
    # without a CORS preflight, which we never approve.
    if request.headers.get("X-Biscuits") != "1":
        abort(403)


def _write_response(out):
    row = None
    res = out.get("result")
    if out["sent"] and isinstance(res, dict) and res.get("_type") == "WorkPackage":
        row = snapshot.upsert(res)
    return jsonify(ok=True, sent=out["sent"], reason=out["reason"], plan=out["plan"], row=row)


def _write(fn, *args):
    _guard()
    try:
        return _write_response(fn(*args))
    except writes.WriteError as e:
        return jsonify(ok=False, error=str(e)), e.status if 400 <= e.status < 600 else 400


@bp.post("/api/wp/<int:wp_id>")
def api_update(wp_id):
    return _write(writes.update, wp_id, (request.get_json(silent=True) or {}).get("changes") or {})


@bp.post("/api/wp/<int:wp_id>/comment")
def api_comment(wp_id):
    return _write(writes.comment, wp_id, (request.get_json(silent=True) or {}).get("text"))


@bp.post("/api/wp")
def api_create():
    return _write(writes.create, request.get_json(silent=True) or {})


@bp.post("/api/project/<int:pid>/archive")
def api_archive(pid):
    _guard()
    archived = (request.get_json(silent=True) or {}).get("archived")
    if not isinstance(archived, bool):
        return jsonify(ok=False, error="archived must be true or false"), 400
    archive.set_archived(pid, archived)
    return jsonify(ok=True, archived_projects=archive.ids())


@bp.get("/api/assignees/<int:pid>")
def api_assignees(pid):
    """People OP will accept as assignee in this project (its members)."""
    try:
        els = client.get_all(f"/projects/{pid}/available_assignees")
    except client.OPError as e:
        return jsonify(error=str(e)), e.status or 502
    return jsonify(ids=[u["id"] for u in els if u.get("_type") == "User"])


@bp.get("/api/writes")
def api_writes():
    return jsonify(entries=writes.recent(200), writes=_gate_info())

