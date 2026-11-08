"""Gated write layer for biscuits. The ONLY code that sends non-GET requests to OP.

Safety model, set in the config file's [writes] section:

* mode       "preview" (default) | "live"
* projects   list of OP project ids that may be written in live mode, or "*"
  for every project.

Every operation is first *planned* (method, path, JSON body). It is sent only
when the mode is live AND every project it touches is allow-listed. Otherwise
the plan is logged and returned unsent, and the UI applies the change locally
as a preview.

Writes go out with the acting user's own key, so OpenProject applies their
permissions on top of this gate and records them as the author.

There is no delete operation, on purpose. Every plan, sent or not, is appended
to write-log.jsonl in the data dir for review, with who made it.
"""

import json
import os
import threading
import time
import urllib.error
import urllib.request

from . import client, config

_log_lock = threading.Lock()

# Text fields are capped so a stray paste can't send megabytes to OP.
MAX_SUBJECT = 255
MAX_TEXT = 50_000
# Uploads are capped too. OpenProject has its own limit (Administration →
# Attachments), and refuses anything bigger with its own message.
MAX_UPLOAD = 10 * 1024 * 1024


class WriteError(RuntimeError):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


def gate():
    """(mode, projects, all_projects). all_projects is True when the allow-list is "*"."""
    cfg = config.load()
    return cfg.writes_mode, set(cfg.write_projects), cfg.write_all_projects


def writable(pid, gate_result=None):
    mode, projects, everything = gate_result or gate()
    return mode == "live" and pid is not None and (everything or pid in projects)


def _id(href):
    try:
        return int(str(href).rstrip("/").rsplit("/", 1)[-1])
    except (ValueError, TypeError):
        return None


def _log(entry):
    path = config.load().write_log_file
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    line = json.dumps(entry, default=str)
    with _log_lock:
        new = not os.path.exists(path)
        with open(path, "a") as f:
            f.write(line + "\n")
        if new:
            os.chmod(path, 0o600)


def recent(n=100, user_id=None):
    """Newest entries first. With user_id, only those made by that account."""
    try:
        with open(config.load().write_log_file) as f:
            lines = f.readlines()
    except OSError:
        return []
    out = []
    for ln in reversed(lines):
        if len(out) >= n:
            break
        try:
            entry = json.loads(ln)
        except ValueError:
            continue
        if user_id is None or (entry.get("actor") or {}).get("user_id") == user_id:
            out.append(entry)
    return out


def _send(op, method, path, body, raw=None):
    """Send JSON `body`, or with raw=(bytes, content_type) send those bytes instead."""
    data, ctype = raw if raw else (json.dumps(body).encode(), "application/json")
    req = urllib.request.Request(
        f"{client.base_url()}/api/v3{path}", method=method, data=data,
        headers={"Authorization": client.auth_header(op.key),
                 "Content-Type": ctype, "Accept": "application/hal+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode()).get("message")
        except Exception:
            detail = None
        raise WriteError(detail or f"OpenProject said HTTP {e.code}", status=e.code) from e
    except urllib.error.URLError as e:
        raise WriteError(f"Couldn't reach OpenProject: {e.reason}", status=502) from e


def _execute(conn, op, method, path, body, projects_touched, meta=None, raw=None):
    """Plan, gate, log and maybe send one write. With `raw`, the log and the
    returned plan get `body` (a summary) while `raw` is what goes to OP."""
    g = gate()
    mode, allowed, everything = g
    touched = {p for p in projects_touched if p is not None}
    if mode != "live":
        send, reason = False, "preview mode"
    elif not touched or not all(writable(p, g) for p in touched):
        send, reason = False, "outside the write-enabled projects"
    else:
        send, reason = True, "live"
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "op": op, "sent": send,
             "reason": reason, "method": method, "path": path, "body": body,
             "projects": sorted(touched), "actor": conn.actor, **(meta or {})}
    result = None
    if send:
        try:
            result = _send(conn, method, path, body, raw)
            entry["ok"] = True
        except WriteError as e:
            entry.update(ok=False, error=str(e))
            _log(entry)
            raise
    _log(entry)
    return {"sent": send, "reason": reason,
            "plan": {"method": method, "path": path, "body": body}, "result": result}


def account_write(conn, op, method, path, body, logged_body):
    """An account-management write (opadmin.py). There's no project to gate on, but
    preview mode still means nothing is sent. The log gets logged_body, not body."""
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "op": op, "method": method, "path": path,
             "body": logged_body, "projects": [], "actor": conn.actor}
    if gate()[0] != "live":
        _log({**entry, "sent": False, "reason": "preview mode"})
        raise WriteError("biscuits is in preview mode, so this wasn't sent to OpenProject")
    try:
        result = _send(conn, method, path, body)
    except WriteError as e:
        _log({**entry, "sent": True, "reason": "live", "ok": False, "error": str(e)})
        raise
    _log({**entry, "sent": True, "reason": "live", "ok": True})
    return result


def _wp(conn, wp_id):
    try:
        return conn.get(f"/work_packages/{int(wp_id)}")
    except client.OPError as e:
        raise WriteError(f"Couldn't load #{wp_id}: {e}", status=e.status or 502)


def _date(v, name):
    if v in (None, ""):
        return None
    v = str(v)
    if len(v) != 10 or v[4] != "-" or v[7] != "-":
        raise WriteError(f"{name} must be YYYY-MM-DD")
    return v


def _int_or_none(v, name):
    if v in (None, ""):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        raise WriteError(f"{name} must be an id")


def update(conn, wp_id, changes):
    """PATCH a work package. `changes` uses biscuits field names."""
    cur = _wp(conn, wp_id)
    body = {"lockVersion": cur["lockVersion"]}
    links = {}
    touched = {_id(cur["_links"]["project"]["href"])}
    for k, v in (changes or {}).items():
        if k == "subject":
            v = str(v or "").strip()
            if not v:
                raise WriteError("A task needs a name")
            body["subject"] = v[:MAX_SUBJECT]
        elif k == "description":
            body["description"] = {"raw": str(v or "")[:MAX_TEXT]}
        elif k == "start":
            body["startDate"] = _date(v, "Start date")
        elif k == "due":
            body["dueDate"] = _date(v, "Due date")
        elif k == "status_id":
            links["status"] = {"href": f"/api/v3/statuses/{int(v)}"}
        elif k == "priority_id":
            links["priority"] = {"href": f"/api/v3/priorities/{int(v)}"}
        elif k == "assignee_id":
            v = _int_or_none(v, "Assignee")
            links["assignee"] = {"href": f"/api/v3/users/{v}" if v else None}
        elif k == "parent_id":
            v = _int_or_none(v, "Parent")
            if v == int(wp_id):
                raise WriteError("A task can't be its own parent")
            if v:
                touched.add(_id(_wp(conn, v)["_links"]["project"]["href"]))
            links["parent"] = {"href": f"/api/v3/work_packages/{v}" if v else None}
        else:
            raise WriteError(f"Field '{k}' can't be edited from biscuits yet")
    if body.get("startDate") and body.get("dueDate") and body["startDate"] > body["dueDate"]:
        raise WriteError("Start date is after the due date")
    if "startDate" in body or "dueDate" in body:
        # Tasks keep exactly the dates they're given, as in Asana. Otherwise OP
        # silently ignores date edits on a parent whose dates roll up from its
        # subtasks, and moves dates that fall on a non-working day.
        body["ignoreNonWorkingDays"] = True
        if not cur.get("scheduleManually"):
            body["scheduleManually"] = True
    if links:
        body["_links"] = links
    if len(body) == 1:
        raise WriteError("Nothing to change")
    return _execute(conn, "update", "PATCH", f"/work_packages/{int(wp_id)}", body, touched,
                    {"wp": int(wp_id), "subject_before": cur.get("subject")})


def comment(conn, wp_id, text):
    text = str(text or "").strip()
    if not text:
        raise WriteError("Comment is empty")
    cur = _wp(conn, wp_id)
    body = {"comment": {"raw": text[:MAX_TEXT]}}
    return _execute(conn, "comment", "POST", f"/work_packages/{int(wp_id)}/activities", body,
                    {_id(cur["_links"]["project"]["href"])}, {"wp": int(wp_id)})


def create(conn, fields):
    """Create a task (optionally as a sub-task). OP quirk: never send description null."""
    subject = str(fields.get("subject") or "").strip()
    if not subject:
        raise WriteError("A task needs a name")
    project_id = _int_or_none(fields.get("project_id"), "Project")
    parent_id = _int_or_none(fields.get("parent_id"), "Parent")
    if parent_id:
        project_id = _id(_wp(conn, parent_id)["_links"]["project"]["href"])
    if not project_id:
        raise WriteError("Pick a project for the new task")
    links = {"project": {"href": f"/api/v3/projects/{project_id}"},
             "type": {"href": f"/api/v3/types/{int(fields.get('type_id') or 1)}"}}
    if parent_id:
        links["parent"] = {"href": f"/api/v3/work_packages/{parent_id}"}
    assignee = _int_or_none(fields.get("assignee_id"), "Assignee")
    if assignee:
        links["assignee"] = {"href": f"/api/v3/users/{assignee}"}
    body = {"subject": subject[:MAX_SUBJECT], "_links": links}
    due = _date(fields.get("due"), "Due date")
    if due:
        body["dueDate"] = due
        body["ignoreNonWorkingDays"] = True  # keep the date as given, see update()
    if fields.get("description"):
        body["description"] = {"raw": str(fields["description"])[:MAX_TEXT]}
    return _execute(conn, "create", "POST", "/work_packages", body, {project_id},
                    {"parent": parent_id})


def _file_name(name):
    """A plain file name: no folders, no control characters, at most 255 characters."""
    name = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip().lstrip(".")
    return name[-MAX_SUBJECT:] or "file"


def _multipart(fields):
    """multipart/form-data from (name, filename_or_None, content_type, bytes) parts."""
    boundary = "biscuits" + os.urandom(16).hex()
    out = bytearray()
    for name, filename, ctype, data in fields:
        disp = f'form-data; name="{name}"'
        if filename is not None:
            disp += f'; filename="{filename.replace(chr(34), "")}"'
        out += f"--{boundary}\r\nContent-Disposition: {disp}\r\nContent-Type: {ctype}\r\n\r\n".encode()
        out += data + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def attach(conn, wp_id, file_name, content_type, data):
    """Upload a file to a work package. Like every write, it's gated and logged
    (name, size and type, never the contents), and there is no way to remove one."""
    if not data:
        raise WriteError("That file is empty")
    if len(data) > MAX_UPLOAD:
        raise WriteError(f"Files can be up to {MAX_UPLOAD // (1024 * 1024)} MB", status=413)
    name = _file_name(file_name)
    ctype = str(content_type or "").strip() or "application/octet-stream"
    cur = _wp(conn, wp_id)
    summary = {"fileName": name, "contentType": ctype, "size": len(data)}
    raw = _multipart([("metadata", None, "application/json", json.dumps({"fileName": name}).encode()),
                      ("file", name, ctype, data)])
    return _execute(conn, "attach", "POST", f"/work_packages/{int(wp_id)}/attachments", summary,
                    {_id(cur["_links"]["project"]["href"])}, {"wp": int(wp_id)}, raw=raw)


def create_section(conn, project_id, name, type_id):
    """Create a section: a top-level Summary task with a [bracketed] name."""
    name = " ".join(str(name or "").split())
    if not name:
        raise WriteError("A section needs a name")
    # Cap the name, not the subject, so the closing bracket always survives.
    subject = f"[{name[:MAX_SUBJECT - 2]}]"
    return create(conn, {"project_id": project_id, "subject": subject, "type_id": type_id})
