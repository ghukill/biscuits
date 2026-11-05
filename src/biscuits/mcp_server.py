"""OpenProject MCP server (stdio, zero-dependency stdlib), run by `biscuits mcp`.

Wraps OpenProject API v3 as MCP tools so pi/Henon instances can manage
lab tasks (projects, work packages, comments) over MCP instead of curl.

Deliberately separate from the web app: it imports only the standard library and
never reads the biscuits config, so it keeps working when the web app is down or
not set up. Its writes use its own key, and don't pass through `writes.py`.

Credentials: reads OPENPROJECT_URL and OPENPROJECT_API_KEY from env, falling back
to the file named by OPENPROJECT_ENV_FILE (KEY=value lines).

Stdout is the MCP JSON-RPC transport (newline-delimited); all diagnostics go to stderr.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

SERVER_INFO = {"name": "openproject-mcp", "version": "0.2.0"}


def load_creds():
    url = os.environ.get("OPENPROJECT_URL")
    key = os.environ.get("OPENPROJECT_API_KEY")
    env_file = os.environ.get("OPENPROJECT_ENV_FILE")
    if env_file and not (url and key):
        try:
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    v = v.strip().strip('"').strip("'")
                    if k == "OPENPROJECT_URL" and not url:
                        url = v
                    elif k == "OPENPROJECT_API_KEY" and not key:
                        key = v
        except OSError:
            pass
    url = (url or "").rstrip("/")
    return url, key or ""


OP_URL, OP_KEY = load_creds()
_api = urllib.parse.urlparse(OP_URL).path.strip("/")
BASE = OP_URL + _api + "/api/v3" if _api else OP_URL + "/api/v3"
AUTH = "Basic " + __import__("base64").b64encode(f"apikey:{OP_KEY}".encode()).decode()


def api(method, path, body=None, params=None):
    query = ""
    if params:
        query = "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(
        BASE + path + query,
        method=method,
        headers={"Authorization": AUTH, "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode()[:500]
        except Exception:
            detail = ""
        return {"_error": f"{method} {path} -> HTTP {e.code}", "detail": detail}
    except Exception as e:
        return {"_error": f"{method} {path} -> {e}"}


def paged(path, params=None, limit=200):
    """GET all pages of a collection, up to `limit` rows.

    OP API v3 `offset` is a 1-based PAGE NUMBER (not a row offset), so the page
    size must stay constant across requests. Fixed 2026-11-02: previously this
    advanced offset by pageSize, so page 2 was requested as page 101 -> empty,
    silently capping every caller at 100 rows.
    """
    out = []
    page_size = max(1, min(100, limit))
    page = 1
    while len(out) < limit:
        p = dict(params or {})
        p["pageSize"] = page_size
        p["offset"] = page
        d = api("GET", path, params=p)
        if "_error" in d:
            return d
        els = d.get("_embedded", {}).get("elements", [])
        out += els
        total = d.get("total", 0)
        if not els or page * page_size >= total:
            break
        page += 1
    return out[:limit]


def fmt_wp(e):
    if not isinstance(e, dict):
        return {}
    links = e.get("_links", {})
    def link_title(key):
        return links.get(key, {}).get("title") or None
    return {
        "id": e.get("id"),
        "subject": e.get("subject"),
        "project": link_title("project"),
        "type": link_title("type"),
        "status": link_title("status"),
        "assignee": link_title("assignee"),
        "author": link_title("author"),
        "start_on": e.get("startDate"),
        "due_on": e.get("dueDate"),
        "pct_done": e.get("percentageDone"),
        "lockVersion": e.get("lockVersion"),
        "updated_at": (e.get("updatedAt") or "")[:19],
    }


# ---------------------------------------------------------------- tools

def t_list_projects(active_only=True):
    # NOTE: boolean filter values on this OP build are "t"/"f", not "true"/"false"
    filt = [{"active": {"operator": "=", "values": ["t"]}}] if active_only else []
    els = paged("/projects", {"filters": json.dumps(filt)})
    if isinstance(els, dict):
        return els
    return [{"id": e["id"], "identifier": e.get("identifier"), "name": e.get("name"),
             "active": e.get("active")} for e in els]


def t_search_work_packages(project_id=None, subject_contains=None, query=None, status=None,
                           wp_type=None, open_only=True, limit=25):
    filt = []
    if project_id:
        filt.append({"project": {"operator": "=", "values": [str(project_id)]}})
    if query:
        # full-text search: matches subject AND work package id (verified working on this build)
        filt.append({"subjectOrId": {"operator": "**", "values": [query]}})
    if subject_contains:
        filt.append({"subject": {"operator": "~", "values": [subject_contains]}})
    if status:
        filt.append({"status": {"operator": "=", "values": [s for s in re.split(r"[,|]", status) if s]}})
    elif open_only:
        filt.append({"status": {"operator": "o", "values": []}})
    if wp_type:
        filt.append({"type": {"operator": "=", "values": [str(t) for t in re.split(r"[,|]", wp_type) if t]}})
    # NOTE: sortBy shorthand "id:desc" is rejected; the JSON form sortBy=[["id","desc"]] works (verified 2026-11-02).
    # NOTE: we always send an explicit `filters` param (even "[]"), which disables OP's
    # default open-only filter -- so open_only=False really does include closed WPs.
    els = paged("/work_packages", {"filters": json.dumps(filt)}, limit=limit)
    if isinstance(els, dict):
        return els
    return [fmt_wp(e) for e in els[:]]


def t_get_work_package(work_package_id):
    e = api("GET", f"/work_packages/{work_package_id}")
    if "_error" in e:
        return e
    out = fmt_wp(e)
    desc = e.get("description", {}) or {}
    out["description"] = desc.get("raw") or ""
    out["url"] = (OP_URL + "/work_packages/" + str(work_package_id))
    return out


def _desc(text):
    return {"format": "markdown", "raw": text or ""} if text else None


def t_list_priorities():
    els = api("GET", "/priorities").get("_embedded", {}).get("elements", [])
    return [{"id": p["id"], "name": p["name"], "isDefault": p.get("isDefault", False)} for p in els]


def _resolve_href(vocab_path, cache_key, value):
    """Resolve a name-or-id to an entity href; returns (ok, href_or_error)."""
    els = api("GET", vocab_path).get("_embedded", {}).get("elements", [])
    for e in els:
        if str(e["id"]) == str(value) or e["name"].lower() == str(value).lower():
            return True, f"/api/v3/{cache_key}/{e['id']}"
    return False, f"'{value}' not found"


def t_create_work_package(project_id, subject, description=None, wp_type="Task",
                          status=None, assignee_id=None, priority=None, start_on=None, due_on=None,
                          parent_id=None):
    body = {"subject": subject, "type": {"href": "/api/v3/types/1"}}
    d = _desc(description)
    if d:
        body["description"] = d  # NOTE: this OP build 500s on "description": null
    # resolve type name -> id
    if wp_type and str(wp_type) not in ("1",):
        types = api("GET", "/types")
        if "_error" not in types:
            for t in types.get("_embedded", {}).get("elements", []):
                if str(t["id"]) == str(wp_type) or t["name"].lower() == str(wp_type).lower():
                    body["type"] = {"href": f"/api/v3/types/{t['id']}"}
                    break
            else:
                return {"_error": f"type '{wp_type}' not found (valid: Task, Milestone, Summary task, Feature, Epic, User story, Bug)"}
    if status:
        ok, href_or = _resolve_href("/statuses", "statuses", status)
        if not ok:
            return {"_error": href_or}
        body["status"] = {"href": href_or}
    if priority:
        ok, href_or = _resolve_href("/priorities", "priorities", priority)
        if not ok:
            return {"_error": href_or}
        body["priority"] = {"href": href_or}
    if assignee_id:
        body["assignee"] = {"href": f"/api/v3/users/{assignee_id}"}
    if start_on:
        body["startDate"] = start_on
    if due_on:
        body["dueDate"] = due_on
    if parent_id:
        body["parent"] = {"href": f"/api/v3/work_packages/{parent_id}"}
    e = api("POST", f"/projects/{project_id}/work_packages", body=body, params={"notify": "false"})
    if "_error" in e:
        return e
    out = fmt_wp(e)
    out["url"] = (OP_URL + "/work_packages/" + str(e.get("id")))
    return out


def t_update_work_package(work_package_id, subject=None, description=None, status=None,
                          assignee_id=None, priority=None, start_on=None, due_on=None,
                          parent_id=None, clear_parent=False, comment=None):
    cur = api("GET", f"/work_packages/{work_package_id}")
    if "_error" in cur:
        return cur
    body = {"lockVersion": cur["lockVersion"]}
    if description is not None:
        body["description"] = _desc(description)  # omitted entirely when not updating
    if subject:
        body["subject"] = subject
    if status:
        ok, href_or = _resolve_href("/statuses", "statuses", status)
        if not ok:
            return {"_error": href_or}
        body["status"] = {"href": href_or}
    if priority:
        ok, href_or = _resolve_href("/priorities", "priorities", priority)
        if not ok:
            return {"_error": href_or}
        body["priority"] = {"href": href_or}
    if assignee_id:
        body["assignee"] = {"href": f"/api/v3/users/{assignee_id}"}
    if start_on:
        body["startDate"] = start_on
    if due_on:
        body["dueDate"] = due_on
    if clear_parent:
        body["parent"] = None
    elif parent_id:
        body["parent"] = {"href": f"/api/v3/work_packages/{parent_id}"}
    e = api("PATCH", f"/work_packages/{work_package_id}", body=body, params={"notify": "false"})
    if "_error" in e:
        return e
    if comment:
        api("POST", f"/work_packages/{work_package_id}/activities",
            body={"comment": {"format": "markdown", "raw": comment}}, params={"notify": "false"})
    return fmt_wp(e)


def t_add_comment(work_package_id, comment):
    e = api("POST", f"/work_packages/{work_package_id}/activities",
            body={"comment": {"format": "markdown", "raw": comment}}, params={"notify": "false"})
    if "_error" in e:
        return e
    return {"activity_id": e.get("id"), "work_package_id": work_package_id, "commented": True}


def t_count_work_packages(project_id=None):
    """Per-project work-package counts: `total` (all statuses) and `open`.

    NOTE: with no `filters` param OP applies its default open-only filter, so we
    pass filters=[] for the all-status total and status `o` for open (fixed
    2026-11-02; previously "total" silently meant open-only). Archived projects
    return 403 even for admins and are skipped.
    """
    all_f = {"filters": "[]", "pageSize": 1}
    open_f = {"filters": json.dumps([{"status": {"operator": "o", "values": []}}]), "pageSize": 1}

    def one(pid):
        a = api("GET", f"/projects/{pid}/work_packages", params=all_f)
        if "total" not in a:
            return a
        o = api("GET", f"/projects/{pid}/work_packages", params=open_f)
        return {"total": a["total"], "open": o.get("total")}

    if project_id:
        r = one(project_id)
        return {"project_id": project_id, **r} if "total" in r else r
    els = paged("/projects", {"filters": json.dumps([{"active": {"operator": "=", "values": ["t"]}}])}, limit=500)
    if isinstance(els, dict):
        return els
    rows, tot, opn = [], 0, 0
    for p in els:
        r = one(p["id"])
        if "total" not in r:
            continue
        tot += r["total"]
        opn += r["open"] or 0
        rows.append({"project_id": p["id"], "project": p["name"], **r})
    return {"sum_active_projects": tot, "sum_active_projects_open": opn,
            "per_project": sorted(rows, key=lambda r: -r["total"])}


def t_get_comments(work_package_id):
    d = api("GET", f"/work_packages/{work_package_id}/activities")
    if "_error" in d:
        return d
    out = []
    for e in d.get("_embedded", {}).get("elements", []):
        if e.get("comment") is not None:  # CommentActivity elements
            out.append({"id": e.get("id"), "author": (e.get("_links", {}).get("user", {}) or {}).get("title"),
                        "created": (e.get("createdAt") or "")[:19],
                        "comment": (e["comment"].get("raw") or "")[:2000]})
    return out if out else {"note": "no comments on work package", "total_activities": d.get("total", 0)}


def t_list_statuses():
    els = api("GET", "/statuses").get("_embedded", {}).get("elements", [])
    return [{"id": s["id"], "name": s["name"], "isClosed": s.get("isClosed", False)} for s in els]


def t_list_types():
    els = api("GET", "/types").get("_embedded", {}).get("elements", [])
    return [{"id": t["id"], "name": t["name"]} for t in els]


def t_list_users():
    els = paged("/principals", {"filters": json.dumps([{"type": {"operator": "=", "values": ["User"]}}])})
    if isinstance(els, dict):
        return els
    return [{"id": e.get("id"), "name": e.get("name"), "email": e.get("email")} for e in els]


def t_get_openproject_not_configured(*a, **k):
    return {"_error": "OPENPROJECT_URL / OPENPROJECT_API_KEY not configured"}


TOOLS = [
    {
        "name": "list_projects",
        "description": "List OpenProject projects (id, identifier, name).",
        "inputSchema": {"type": "object", "properties": {"active_only": {"type": "boolean", "default": True},
                                                         "limit": {"type": "integer", "default": 200}}},
    },
    {
        "name": "search_work_packages",
        "description": "Search/filter work packages. Returns id, subject, project, type, status, assignee, author, dates, pct_done, lockVersion.",
        "inputSchema": {"type": "object", "properties": {
            "project_id": {"type": "integer", "description": "restrict to a project id"},
            "subject_contains": {"type": "string", "description": "substring match on subject"},
            "query": {"type": "string", "description": "full-text search on subject OR work-package id (Cribbed from AndyEverything repo; verified on this build)"},
            "status": {"type": "string", "description": "status name(s) or id(s), comma-separated (e.g. 'new,in progress')"},
            "wp_type": {"type": "string", "description": "type name(s) or id(s), comma-separated"},
            "open_only": {"type": "boolean", "default": True, "description": "if no explicit status given, only open statuses"},
            "limit": {"type": "integer", "default": 25}}},
    },
    {
        "name": "get_work_package",
        "description": "Get full details (incl. description) of one work package by id.",
        "inputSchema": {"type": "object", "properties": {"work_package_id": {"type": "integer"}}, "required": ["work_package_id"]},
    },
    {
        "name": "create_work_package",
        "description": "Create a work package in a project. wp_type: Task, Milestone, Summary task, Feature, Epic, User story, Bug (or type id). status: status name or id.",
        "inputSchema": {"type": "object", "properties": {
            "project_id": {"type": "integer"}, "subject": {"type": "string"},
            "description": {"type": "string", "description": "markdown body"}, "wp_type": {"type": "string", "default": "Task"},
            "status": {"type": "string"}, "assignee_id": {"type": "integer"},
            "priority": {"type": "string", "description": "priority name or id (Low/Normal/High/Immediate)"},
            "start_on": {"type": "string", "description": "YYYY-MM-DD"}, "due_on": {"type": "string", "description": "YYYY-MM-DD"},
            "parent_id": {"type": "integer"}},
            "required": ["project_id", "subject"]},
    },
    {
        "name": "update_work_package",
        "description": "Update a work package (handles lockVersion automatically). Optionally attach a comment in the same call.",
        "inputSchema": {"type": "object", "properties": {
            "work_package_id": {"type": "integer"}, "subject": {"type": "string"}, "description": {"type": "string"},
            "status": {"type": "string", "description": "status name or id"}, "assignee_id": {"type": "integer"},
            "priority": {"type": "string", "description": "priority name or id (Low/Normal/High/Immediate)"},
            "start_on": {"type": "string"}, "due_on": {"type": "string"}, "parent_id": {"type": "integer"},
            "clear_parent": {"type": "boolean"}, "comment": {"type": "string", "description": "markdown comment to add"}},
            "required": ["work_package_id"]},
    },
    {
        "name": "add_comment",
        "description": "Add a markdown comment/note to a work package.",
        "inputSchema": {"type": "object", "properties": {"work_package_id": {"type": "integer"}, "comment": {"type": "string"}},
                        "required": ["work_package_id", "comment"]},
    },
    {
        "name": "count_work_packages",
        "description": "Count work packages per project: total (all statuses) and open, plus sums across active projects. Archived projects are excluded (API returns 403).",
        "inputSchema": {"type": "object", "properties": {"project_id": {"type": "integer", "description": "count a single project"}}},
    },
    {
        "name": "get_comments",
        "description": "Read the comments (comment activities) on a work package, oldest-first.",
        "inputSchema": {"type": "object", "properties": {"work_package_id": {"type": "integer"}}, "required": ["work_package_id"]},
    },
    {
        "name": "list_statuses",
        "description": "List valid work-package statuses (id, name, isClosed).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_types",
        "description": "List work-package types (id, name).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_priorities",
        "description": "List work-package priorities (id, name).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_users",
        "description": "List users/principals (id, name) for assignee_id fields.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

DISPATCH = {t["name"]: None for t in TOOLS}
DISPATCH.update({
    "list_projects": lambda a: t_list_projects(a.get("active_only", True)),
    "search_work_packages": lambda a: t_search_work_packages(**a),
    "get_work_package": lambda a: t_get_work_package(a["work_package_id"]),
    "get_comments": lambda a: t_get_comments(a["work_package_id"]),
    "create_work_package": lambda a: t_create_work_package(**a),
    "update_work_package": lambda a: t_update_work_package(**a),
    "add_comment": lambda a: t_add_comment(a["work_package_id"], a["comment"]),
    "count_work_packages": lambda a: t_count_work_packages(a.get("project_id")),
    "list_statuses": lambda a: t_list_statuses(),
    "list_priorities": lambda a: t_list_priorities(),
    "list_types": lambda a: t_list_types(),
    "list_users": lambda a: t_list_users(),
})

# ---------------------------------------------------------------- MCP plumbing

def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def rpc(method, params=None, req_id=None):
    if method == "initialize":
        p = params or {}
        return {"protocolVersion": p.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name = (params or {}).get("name")
        args = (params or {}).get("arguments") or {}
        fn = DISPATCH.get(name)
        if fn is None:
            return {"isError": True, "content": [{"type": "text", "text": f"unknown tool: {name}"}]}
        try:
            if not OP_URL or not OP_KEY:
                result = {"_error": "OPENPROJECT_URL / OPENPROJECT_API_KEY not configured"}
            else:
                result = fn(args)
        except Exception as e:  # never crash the transport
            result = {"_error": f"{name} failed: {e}"}
        is_err = isinstance(result, dict) and "_error" in result
        return {"isError": is_err,
                "content": [{"type": "text", "text": json.dumps(result, indent=1, default=str)}]}
    return None


def main():
    sys.stderr.write(f"openproject-mcp: {BASE}\n")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = req.get("method", "")
        if method.startswith("notifications/"):
            continue
        try:
            resp = rpc(method, req.get("params"), req.get("id"))
        except Exception as e:
            resp = {"isError": True, "content": [{"type": "text", "text": f"server error: {e}"}]}
        if resp is not None:
            send({"jsonrpc": "2.0", "id": req.get("id"), "result": resp})
