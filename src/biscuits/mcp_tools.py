"""The MCP tools served by biscuits itself (see mcp_http.py).

Same names, arguments and output as the direct server (mcp_server.py), but built
on biscuits' own code: reads go through client.OP with the calling account's own
key, and writes go through writes.py, so the [writes] gate and the write log
cover them like any other biscuits user. They also know biscuits' own data: which
projects are archived, and the order of each project's sections.

Each tool takes the caller's connection and its arguments, and returns a
JSON-able result. A failure is a dict with "_error", as in mcp_server.py.
"""

import base64
import binascii
import json
import mimetypes
import re

from . import archive, client, opadmin, sections, snapshot, writes
from .mcp_schema import LIST_SECTIONS, TOOLS as _SHARED

MAX_ATTACHMENT = writes.MAX_UPLOAD


def _attachment_schema(tool):
    if tool["name"] != "add_attachment":
        return tool
    return {**tool, "description": (
        "Attach a file (e.g. a screenshot or mockup) to a work package. Give content_base64 plus "
        "file_name, or file_path for a file on your own machine (the `biscuits mcp` client reads it "
        "and sends the contents). Max 10 MB. Returns the attachment's id and a `markdown` snippet "
        "that shows it inline when put in a description or comment. Attachments can't be removed "
        "through this server.")}


TOOLS = [_attachment_schema(t) for t in _SHARED] + [LIST_SECTIONS]


class ToolError(RuntimeError):
    pass


# ---------------------------------------------------------------- helpers

def _paged(op, path, params=None, limit=200):
    """Up to `limit` rows of a collection. OP's `offset` is a page number, so the
    page size stays the same across requests."""
    out, page, size = [], 1, max(1, min(100, limit))
    while len(out) < limit:
        d = op.get(path, dict(params or {}, pageSize=size, offset=page))
        els = d.get("_embedded", {}).get("elements", [])
        out += els
        if not els or page * size >= d.get("total", 0):
            break
        page += 1
    return out[:limit]


def _title(e, key):
    return ((e.get("_links") or {}).get(key) or {}).get("title") or None


def fmt_wp(e):
    if not isinstance(e, dict):
        return {}
    return {
        "id": e.get("id"),
        "subject": e.get("subject"),
        "project": _title(e, "project"),
        "type": _title(e, "type"),
        "status": _title(e, "status"),
        "assignee": _title(e, "assignee"),
        "author": _title(e, "author"),
        "start_on": e.get("startDate"),
        "due_on": e.get("dueDate"),
        "pct_done": e.get("percentageDone"),
        "lockVersion": e.get("lockVersion"),
        "updated_at": (e.get("updatedAt") or "")[:19],
    }


def fmt_attachment(a):
    return {
        "id": a.get("id"),
        "file_name": a.get("fileName"),
        "size": a.get("fileSize"),
        "content_type": a.get("contentType"),
        "author": _title(a, "author"),
        "created_at": (a.get("createdAt") or "")[:19],
        # Downloading needs an OpenProject login or API key.
        "download_url": f"{client.base_url()}/api/v3/attachments/{a.get('id')}/content",
        # Put this in a description or comment to show the file there.
        "markdown": f"{'!' if str(a.get('contentType') or '').startswith('image/') else ''}"
                    f"[{a.get('fileName')}](/api/v3/attachments/{a.get('id')}/content)",
    }


def _lookup(op, path, value, what):
    """The id of a status, priority or type given by name or id."""
    for e in op.get(path).get("_embedded", {}).get("elements", []):
        if str(e["id"]) == str(value) or e["name"].lower() == str(value).lower():
            return e["id"]
    raise ToolError(f"{what} '{value}' not found")


def _sent(out):
    """The result of a write, or an error saying it wasn't sent and why."""
    if not out["sent"]:
        raise ToolError(f"biscuits didn't send this to OpenProject ({out['reason']}), so nothing "
                        "changed. Its [writes] settings decide which projects can be changed.")
    return out.get("result") or {}


def _section_of(op, e, depth=20):
    """The section a task is in: its top-level ancestor, when that's a section."""
    seen = 0
    while seen < depth:
        href = ((e.get("_links") or {}).get("parent") or {}).get("href")
        if not href:
            break
        e = op.get(f"/work_packages/{writes._id(href)}")
        seen += 1
    if seen == 0:
        return None
    name = snapshot.section_name(e.get("subject"))
    if _title(e, "type") != snapshot.SECTION_TYPE_NAME or name is None:
        return None
    return {"id": e.get("id"), "name": name}


# ---------------------------------------------------------------- tools

def list_projects(op, active_only=True, limit=200):
    # Boolean filter values on this OP build are "t"/"f".
    filt = [{"active": {"operator": "=", "values": ["t"]}}] if active_only else []
    archived = set(archive.ids())
    rows = [{"id": e["id"], "identifier": e.get("identifier"), "name": e.get("name"),
             "active": e.get("active"), "archived": e["id"] in archived}
            for e in _paged(op, "/projects", {"filters": json.dumps(filt)}, limit=limit)]
    # Archived in biscuits means out of everyday views, so "active" leaves them out.
    return [r for r in rows if not (active_only and r["archived"])]


def search_work_packages(op, project_id=None, subject_contains=None, query=None, status=None,
                         wp_type=None, open_only=True, limit=25):
    filt = []
    if project_id:
        filt.append({"project": {"operator": "=", "values": [str(project_id)]}})
    if query:
        filt.append({"subjectOrId": {"operator": "**", "values": [query]}})
    if subject_contains:
        filt.append({"subject": {"operator": "~", "values": [subject_contains]}})
    if status:
        filt.append({"status": {"operator": "=", "values": [s for s in re.split(r"[,|]", status) if s]}})
    elif open_only:
        filt.append({"status": {"operator": "o", "values": []}})
    if wp_type:
        filt.append({"type": {"operator": "=", "values": [t for t in re.split(r"[,|]", wp_type) if t]}})
    # An explicit `filters` (even "[]") turns off OP's default open-only filter.
    return [fmt_wp(e) for e in _paged(op, "/work_packages", {"filters": json.dumps(filt)}, limit=limit)]


def get_work_package(op, work_package_id):
    e = op.get(f"/work_packages/{int(work_package_id)}")
    out = fmt_wp(e)
    out["description"] = (e.get("description") or {}).get("raw") or ""
    out["url"] = client.web_url(f"/work_packages/{int(work_package_id)}")
    atts = (e.get("_embedded") or {}).get("attachments")
    els = ((atts.get("_embedded") or {}).get("elements", []) if atts is not None
           else op.get_all(f"/work_packages/{int(work_package_id)}/attachments"))
    out["attachments"] = [fmt_attachment(a) for a in els]
    out["section"] = _section_of(op, e)
    return out


def get_comments(op, work_package_id):
    d = op.get(f"/work_packages/{int(work_package_id)}/activities")
    out = []
    for e in d.get("_embedded", {}).get("elements", []):
        if e.get("comment") is None:
            continue
        user = ((e.get("_links") or {}).get("user") or {})
        out.append({"id": e.get("id"),
                    # Non-admin keys often get no name here; biscuits can look it up.
                    "author": user.get("title") or opadmin.name_of(writes._id(user.get("href"))),
                    "created": (e.get("createdAt") or "")[:19],
                    "comment": ((e.get("comment") or {}).get("raw") or "")[:2000]})
    return out if out else {"note": "no comments on work package", "total_activities": d.get("total", 0)}


def _fields(op, description=None, status=None, priority=None, assignee_id=None, start_on=None,
            due_on=None):
    f = {}
    if description is not None:
        f["description"] = description
    if status:
        f["status_id"] = _lookup(op, "/statuses", status, "status")
    if priority:
        f["priority_id"] = _lookup(op, "/priorities", priority, "priority")
    if assignee_id:
        f["assignee_id"] = assignee_id
    if start_on:
        f["start"] = start_on
    if due_on:
        f["due"] = due_on
    return f


def create_work_package(op, project_id, subject, description=None, wp_type="Task", status=None,
                        assignee_id=None, priority=None, start_on=None, due_on=None, parent_id=None):
    fields = {"project_id": project_id, "subject": subject, "parent_id": parent_id,
              "type_id": _lookup(op, "/types", wp_type or "Task", "type"),
              **_fields(op, description or None, status, priority, assignee_id, start_on, due_on)}
    e = _sent(writes.create(op, fields, notify=False))
    return {**fmt_wp(e), "url": client.web_url(f"/work_packages/{e.get('id')}")}


def update_work_package(op, work_package_id, subject=None, description=None, status=None,
                        assignee_id=None, priority=None, start_on=None, due_on=None,
                        parent_id=None, clear_parent=False, comment=None):
    changes = _fields(op, description, status, priority, assignee_id, start_on, due_on)
    if subject:
        changes["subject"] = subject
    if clear_parent:
        changes["parent_id"] = None
    elif parent_id:
        changes["parent_id"] = parent_id
    if changes:
        e = _sent(writes.update(op, work_package_id, changes, notify=False))
    elif comment:
        e = op.get(f"/work_packages/{int(work_package_id)}")
    else:
        raise ToolError("Nothing to change")
    if comment:
        _sent(writes.comment(op, work_package_id, comment, notify=False))
    return fmt_wp(e)


def add_comment(op, work_package_id, comment):
    e = _sent(writes.comment(op, work_package_id, comment, notify=False))
    return {"activity_id": e.get("id"), "work_package_id": work_package_id, "commented": True}


def add_attachment(op, work_package_id, file_path=None, content_base64=None, file_name=None,
                   content_type=None):
    if file_path:
        # This server can't see the caller's files, and mustn't read its own.
        raise ToolError("file_path only works through the `biscuits mcp` client, which reads the file "
                        "on your machine. Send content_base64 and file_name instead.")
    if not content_base64:
        raise ToolError("give exactly one of file_path or content_base64")
    try:
        data = base64.b64decode(content_base64, validate=True)
    except (ValueError, binascii.Error):
        raise ToolError("content_base64 isn't valid base64")
    if not file_name:
        raise ToolError("file_name is required with content_base64")
    if not data:
        raise ToolError("the file is empty")
    if len(data) > MAX_ATTACHMENT:
        raise ToolError(f"file is over {MAX_ATTACHMENT // (1024 * 1024)} MB")
    name = writes._file_name(file_name)
    ctype = content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
    e = _sent(writes.attach(op, work_package_id, name, ctype, data, notify=False))
    return {"work_package_id": work_package_id, "attached": True, **fmt_attachment(e)}


def count_work_packages(op, project_id=None):
    all_f = {"filters": "[]", "pageSize": 1}
    open_f = {"filters": json.dumps([{"status": {"operator": "o", "values": []}}]), "pageSize": 1}

    def one(pid):
        a = op.get(f"/projects/{pid}/work_packages", all_f)
        o = op.get(f"/projects/{pid}/work_packages", open_f)
        return {"total": a.get("total"), "open": o.get("total")}

    if project_id:
        return {"project_id": project_id, **one(project_id)}
    projects = _paged(op, "/projects", {"filters": json.dumps([{"active": {"operator": "=", "values": ["t"]}}])},
                      limit=500)
    rows, tot, opn = [], 0, 0
    for p in projects:
        try:
            r = one(p["id"])
        except client.OPError:
            continue  # archived in OpenProject: 403 even for admins
        tot += r["total"] or 0
        opn += r["open"] or 0
        rows.append({"project_id": p["id"], "project": p["name"], **r})
    return {"sum_active_projects": tot, "sum_active_projects_open": opn,
            "per_project": sorted(rows, key=lambda r: -(r["total"] or 0))}


def list_statuses(op):
    return [{"id": s["id"], "name": s["name"], "isClosed": s.get("isClosed", False)}
            for s in op.get("/statuses").get("_embedded", {}).get("elements", [])]


def list_types(op):
    return [{"id": t["id"], "name": t["name"]} for t in op.get("/types").get("_embedded", {}).get("elements", [])]


def list_priorities(op):
    return [{"id": p["id"], "name": p["name"], "isDefault": p.get("isDefault", False)}
            for p in op.get("/priorities").get("_embedded", {}).get("elements", [])]


def list_users(op):
    els = _paged(op, "/principals", {"filters": json.dumps([{"type": {"operator": "=", "values": ["User"]}}])})
    return [{"id": e.get("id"), "name": e.get("name"), "email": e.get("email")} for e in els]


def list_sections(op, project_id):
    filt = [{"project": {"operator": "=", "values": [str(int(project_id))]}},
            {"type": {"operator": "=", "values": [str(_lookup(op, "/types", snapshot.SECTION_TYPE_NAME, "type"))]}}]
    found = []
    for e in op.get_all("/work_packages", {"filters": json.dumps(filt)}):
        name = snapshot.section_name(e.get("subject"))
        top = not ((e.get("_links") or {}).get("parent") or {}).get("href")
        if name is not None and top and _title(e, "type") == snapshot.SECTION_TYPE_NAME:
            found.append({"id": e["id"], "name": name})
    rank = {sid: n for n, sid in enumerate(sections.order())}
    # As biscuits shows them: positioned sections first, then the rest by id.
    return sorted(found, key=lambda s: (s["id"] not in rank, rank.get(s["id"], 0), s["id"]))


DISPATCH = {
    "list_projects": lambda op, a: list_projects(op, a.get("active_only", True), a.get("limit", 200)),
    "search_work_packages": lambda op, a: search_work_packages(op, **a),
    "get_work_package": lambda op, a: get_work_package(op, a["work_package_id"]),
    "get_comments": lambda op, a: get_comments(op, a["work_package_id"]),
    "create_work_package": lambda op, a: create_work_package(op, **a),
    "update_work_package": lambda op, a: update_work_package(op, **a),
    "add_comment": lambda op, a: add_comment(op, a["work_package_id"], a["comment"]),
    "add_attachment": lambda op, a: add_attachment(op, **a),
    "count_work_packages": lambda op, a: count_work_packages(op, a.get("project_id")),
    "list_statuses": lambda op, a: list_statuses(op),
    "list_types": lambda op, a: list_types(op),
    "list_priorities": lambda op, a: list_priorities(op),
    "list_users": lambda op, a: list_users(op),
    "list_sections": lambda op, a: list_sections(op, a["project_id"]),
}


def call(op, name, arguments):
    """Run a tool. Returns (is_error, result)."""
    fn = DISPATCH.get(name)
    if fn is None:
        return True, {"_error": f"unknown tool: {name}"}
    try:
        return False, fn(op, arguments or {})
    except (ToolError, writes.WriteError) as e:
        return True, {"_error": str(e)}
    except client.OPError as e:
        return True, {"_error": str(e)}
    except (TypeError, KeyError, ValueError) as e:
        return True, {"_error": f"{name}: bad arguments ({e})"}
