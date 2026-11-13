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
  /bulk?ids=        Bulk edit the selected tasks
Any of them accept ?task=<id> to open the task detail pane.

JSON API:
  GET  /api/snapshot         Everything the UI needs (cached, see snapshot.py)
  POST /api/refresh          Re-pull the snapshot (GETs only)
  GET  /api/wp/<id>          Live detail: description, attachments + activity feed
  POST /api/wp/<id>/attachments  Upload a file (multipart "file"), gated like other writes
  GET  /api/attachment/<id>  An attachment's contents, fetched with the person's key
                             (images inline, everything else as a download)
  POST /api/wp/<id>          Update fields          } gated by writes.py:
  POST /api/wp/bulk          Update several tasks   } (each one gated and logged)
  POST /api/wp/<id>/comment  Add a comment          } live only for allow-listed
  POST /api/wp               Create a task/subtask  } projects, else preview
  GET  /api/assignees/<pid>  Who can be assigned in a project
  GET  /api/writes           Recent write-log entries (admins: everyone's; others: their own)
  POST /api/project/<id>/archive   Archive or unarchive (biscuits only, not sent to OP)
  POST /api/project/<id>/sections  Add a section (gated by writes.py, like a task)
  POST /api/project/<id>/sections/order  Set the section order (biscuits only, not sent to OP)

Login and the account and admin APIs are in auth.py, which also gates every
route here. Every OpenProject call here uses the signed-in person's own key.
"""

import re
import time
import urllib.parse

from flask import Blueprint, Response, g, jsonify, render_template, request, url_for
from flask_login import current_user

from . import accounts, archive, client, opadmin, sections, snapshot, writes

bp = Blueprint(
    "biscuits",
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/static",
)


class NoKey(RuntimeError):
    """This account has no working OpenProject key yet."""


def current_op() -> client.OP:
    """The signed-in person's OpenProject connection."""
    if "op" not in g:
        key = accounts.get_key(current_user)
        if not key:
            raise NoKey()
        g.op = client.OP(key, actor={"user_id": current_user.id, "username": current_user.username,
                                     "op_user_id": current_user.op_user_id})
    return g.op


def _shell():
    resp = Response(render_template("biscuits/app.html"))
    resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp


@bp.get("/")
def index():
    return _shell()


@bp.get("/<any(home, my, all, search, log, archived, admin, account, bulk):view>")
@bp.get("/project/<int:pid>")
@bp.get("/people/<int:uid>")
def views(**_):
    return _shell()


# ---------- JSON API ----------

def _gate_info():
    mode, projects, everything = writes.gate()
    return {"mode": mode, "write_projects": sorted(projects), "all_projects": everything}


def _snapshot_response():
    op = current_op()
    data, fetched_at, error = snapshot.get(current_user.id, op)
    if data is None:  # a concurrent first fetch failed
        return jsonify(error=error or "No data from OpenProject yet"), 502
    visible = {p["id"] for p in data["projects"]}
    tasks = {r["id"] for r in data["work_packages"]}
    return jsonify(
        data=data,
        fetched_at=fetched_at,
        age_seconds=round(time.time() - fetched_at),
        stale_error=error,
        writes=_gate_info(),
        archived_projects=[p for p in archive.ids() if p in visible],
        section_order=[s for s in sections.order() if s in tasks],
        account={"username": current_user.username, "is_admin": current_user.is_admin},
    )


@bp.get("/api/snapshot")
def api_snapshot():
    return _snapshot_response()


@bp.post("/api/refresh")
def api_refresh():
    snapshot.refresh(current_user.id, current_op())
    return _snapshot_response()


def _activity(a, names):
    uid = writes._id(((a.get("_links") or {}).get("user") or {}).get("href"))
    c = a.get("comment") or {}
    return {
        "id": a.get("id"),
        "kind": "comment" if (c.get("raw") or "").strip() else "change",
        "user_id": uid,
        "user": names.get(uid) or opadmin.name_of(uid),
        "at": a.get("createdAt"),
        "comment_html": c.get("html") or "",
        "comment_raw": c.get("raw") or "",
        "details": [d.get("html") or d.get("raw") or "" for d in a.get("details") or []],
    }


# Shown in the browser as they are. Anything else (SVG included, since it can
# carry script) is only ever offered as a download.
INLINE_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif", "image/bmp"}


def _attachment(a):
    ctype = (a.get("contentType") or "").split(";")[0].strip().lower()
    author = ((a.get("_links") or {}).get("author") or {})
    return {
        "id": a.get("id"),
        "name": a.get("fileName"),
        "size": a.get("fileSize"),
        "content_type": ctype,
        "is_image": ctype in INLINE_IMAGE_TYPES,
        "created": a.get("createdAt"),
        "author": author.get("title"),
        "url": url_for("biscuits.api_attachment", att_id=a.get("id")),
    }


def _attachments(op, e):
    """The work package's attachments: embedded when OP includes them, else fetched."""
    emb = (e.get("_embedded") or {}).get("attachments")
    if emb is not None:
        return (emb.get("_embedded") or {}).get("elements", [])
    if "attachments" not in (e.get("_links") or {}):
        return []
    return op.get_all(f"/work_packages/{e['id']}/attachments")


def _local_images(html):
    """Point OP's attachment links (src and href) in rendered markdown at biscuits'
    own proxy, which fetches them with the signed-in person's key."""
    if not html:
        return html
    op_url = re.escape(client.base_url())
    proxy = url_for("biscuits.api_attachment", att_id=0)[:-1]
    return re.sub(rf'(\s(?:src|href)=")(?:{op_url})?/api/v3/attachments/(\d+)/content(")',
                  lambda m: f"{m.group(1)}{proxy}{m.group(2)}{m.group(3)}", html)


@bp.get("/api/wp/<int:wp_id>")
def api_wp(wp_id):
    op = current_op()
    try:
        e = op.get(f"/work_packages/{wp_id}")
        acts = op.get(f"/work_packages/{wp_id}/activities")
        atts = _attachments(op, e)
    except client.OPError as err:
        if err.status in (403, 404):
            return jsonify(error="You don't have access to this task."), err.status
        raise
    data, _, _ = snapshot.get(current_user.id, op)
    names = {u["id"]: u["name"] for u in (data or {}).get("users", [])}
    row = snapshot.upsert(current_user.id, e)
    description = dict(e.get("description") or {})
    description["html"] = _local_images(description.get("html"))
    activities = [_activity(a, names) for a in acts.get("_embedded", {}).get("elements", [])]
    for a in activities:
        a["comment_html"] = _local_images(a["comment_html"])
    return jsonify(
        row=row,
        description=description,
        attachments=[_attachment(a) for a in atts],
        can_attach="addAttachment" in (e.get("_links") or {}),
        max_upload=writes.MAX_UPLOAD,
        activities=activities,
        live=writes.writable(row["project_id"]),
    )


@bp.get("/api/attachment/<int:att_id>")
def api_attachment(att_id):
    """Stream one attachment from OP with the signed-in person's key, so OP decides
    who may see it. Nothing is cached on the server; the browser may keep a private
    copy for a few minutes. Only plain image types are shown inline."""
    op = current_op()
    try:
        meta = op.get(f"/attachments/{att_id}")
        href = ((meta.get("_links") or {}).get("downloadLocation") or {}).get("href")
        upstream = op.download(href or f"/api/v3/attachments/{att_id}/content")
    except client.OPError as err:
        if err.status in (403, 404):
            return jsonify(error="You don't have access to this file."), 404
        raise
    info = _attachment(meta)
    inline = info["is_image"] and not request.args.get("download")
    name = writes._file_name(info["name"])
    quoted = urllib.parse.quote(name, safe="")
    ascii_name = name.encode("ascii", "replace").decode().replace('"', "").replace("?", "_")

    def chunks():
        with upstream:
            while block := upstream.read(64 * 1024):
                yield block

    resp = Response(chunks(), content_type=info["content_type"] if inline else "application/octet-stream")
    resp.headers["Content-Disposition"] = (
        f"{'inline' if inline else 'attachment'}; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}")
    length = upstream.headers.get("Content-Length")
    if length:
        resp.headers["Content-Length"] = length
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"
    resp.headers["Cache-Control"] = "private, max-age=300"
    return resp


@bp.post("/api/wp/<int:wp_id>/attachments")
def api_attach(wp_id):
    # Refuse an oversized upload before reading it. The allowance covers the
    # multipart framing around the file.
    request.max_content_length = writes.MAX_UPLOAD + 64 * 1024
    f = request.files.get("file")
    if f is None:
        return jsonify(ok=False, error="No file was sent"), 400
    data = f.read(writes.MAX_UPLOAD + 1)
    try:
        out = writes.attach(current_op(), wp_id, f.filename, f.mimetype, data)
    except writes.WriteError as e:
        if e.status == 401:
            raise client.OPError(str(e), status=401) from e
        return jsonify(ok=False, error=str(e)), e.status if 400 <= e.status < 600 else 400
    res = out.get("result")
    att = _attachment(res) if out["sent"] and isinstance(res, dict) and res.get("id") else None
    return jsonify(ok=True, sent=out["sent"], reason=out["reason"], plan=out["plan"], attachment=att)


def _write_response(out):
    row = None
    res = out.get("result")
    if out["sent"] and isinstance(res, dict) and res.get("_type") == "WorkPackage":
        row = snapshot.upsert(current_user.id, res)
    return jsonify(ok=True, sent=out["sent"], reason=out["reason"], plan=out["plan"], row=row)


def _write(fn, *args):
    try:
        return _write_response(fn(current_op(), *args))
    except writes.WriteError as e:
        if e.status == 401:  # OP rejected their key, not "you need to log in"
            raise client.OPError(str(e), status=401) from e
        return jsonify(ok=False, error=str(e)), e.status if 400 <= e.status < 600 else 400


@bp.post("/api/wp/<int:wp_id>")
def api_update(wp_id):
    return _write(writes.update, wp_id, (request.get_json(silent=True) or {}).get("changes") or {})



@bp.post("/api/wp/bulk")
def api_bulk_update():
    """Bulk edit: {"items": [{"id", "changes"}]}. Each task succeeds or fails on its
    own, and the reply lists every outcome, so the UI can say exactly which failed."""
    op = current_op()
    try:
        outcomes = writes.bulk_update(op, (request.get_json(silent=True) or {}).get("items"))
    except writes.WriteError as e:
        if e.status == 401:
            raise client.OPError(str(e), status=401) from e
        return jsonify(ok=False, error=str(e)), 400
    results = []
    for o in outcomes:
        res = o.get("result")
        row = (snapshot.upsert(current_user.id, res)
               if o["ok"] and o["sent"] and isinstance(res, dict) and res.get("_type") == "WorkPackage" else None)
        results.append({"id": o["id"], "ok": o["ok"], "sent": o.get("sent", False),
                        "reason": o.get("reason"), "error": o.get("error"), "row": row})
    return jsonify(ok=True, results=results)

@bp.post("/api/wp/<int:wp_id>/comment")
def api_comment(wp_id):
    return _write(writes.comment, wp_id, (request.get_json(silent=True) or {}).get("text"))


@bp.post("/api/wp")
def api_create():
    return _write(writes.create, request.get_json(silent=True) or {})


@bp.post("/api/project/<int:pid>/archive")
def api_archive(pid):
    archived = (request.get_json(silent=True) or {}).get("archived")
    if not isinstance(archived, bool):
        return jsonify(ok=False, error="archived must be true or false"), 400
    # Archiving is shared, so it takes a biscuits admin or someone who may edit
    # the project in OpenProject.
    if not current_user.is_admin and "update" not in (current_op().get(f"/projects/{pid}").get("_links") or {}):
        return jsonify(ok=False, error="You don't have permission to archive this project."), 403
    archive.set_archived(pid, archived, by=current_user.id)
    return jsonify(ok=True, archived_projects=archive.ids())


@bp.post("/api/project/<int:pid>/sections")
def api_add_section(pid):
    data, _, _ = snapshot.get(current_user.id, current_op())
    type_id = next((t["id"] for t in (data or {}).get("types", []) if t["name"] == snapshot.SECTION_TYPE_NAME), None)
    if type_id is None:
        return jsonify(ok=False, error=f"OpenProject has no '{snapshot.SECTION_TYPE_NAME}' type"), 400
    return _write(writes.create_section, pid, (request.get_json(silent=True) or {}).get("name"), type_id)


@bp.post("/api/project/<int:pid>/sections/order")
def api_section_order(pid):
    ids = (request.get_json(silent=True) or {}).get("order")
    if (not isinstance(ids, list) or not ids or not all(type(i) is int for i in ids)
            or len(set(ids)) != len(ids)):
        return jsonify(ok=False, error="order must be a list of section ids"), 400
    # Nothing here reaches OpenProject, but the order is shared, so the write gate
    # still decides: a project in preview mode stays as it is for everyone.
    if not writes.writable(pid):
        return jsonify(ok=False, error="This project isn't write-enabled, so its sections can't be reordered."), 409
    data, _, _ = snapshot.get(current_user.id, current_op())
    rows = {r["id"]: r for r in (data or {}).get("work_packages", [])}
    picked = [rows.get(i) for i in ids]
    if not all(r and r["section"] is not None and r["project_id"] == pid for r in picked):
        return jsonify(ok=False, error="That list has something that isn't a section in this project. Try refreshing."), 400
    if not current_user.is_admin and not all(r["can_edit"] for r in picked):
        return jsonify(ok=False, error="You don't have permission to reorder these sections."), 403
    sections.set_order(pid, ids, by=current_user.id)
    return jsonify(ok=True)


@bp.get("/api/assignees/<int:pid>")
def api_assignees(pid):
    """People OP will accept as assignee in this project (its members)."""
    els = current_op().get_all(f"/projects/{pid}/available_assignees")
    people = [{"id": u["id"], "name": u.get("name")} for u in els if u.get("_type") == "User"]
    return jsonify(ids=[u["id"] for u in people], people=people)


@bp.get("/api/writes")
def api_writes():
    # Entries name tasks, so non-admins only get their own.
    entries = writes.recent(200, None if current_user.is_admin else current_user.id)
    return jsonify(entries=entries, writes=_gate_info())

