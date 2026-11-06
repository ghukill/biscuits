// Client-side data store: the snapshot, indexes, and every mutation.
// Mutations are optimistic: apply locally, re-render, then ask the server.
// The server either sends the change to OpenProject ("live", only for
// allow-listed projects) or logs it as a preview.

import { toast } from "./ui.js";

const BASE = document.body.dataset.base;
const HDRS = { "Content-Type": "application/json", "X-Biscuits": "1" };

export const S = {
  snap: null,
  fetchedAt: 0,
  rows: new Map(),       // id -> row
  kids: new Map(),       // parent id -> [child ids] (Asana/import order = id)
  statuses: new Map(),
  priorities: [],
  users: new Map(),      // id -> {id, name}
  projects: new Map(),
  archived: new Set(),   // project ids archived in biscuits
  sectionOrder: new Map(), // section id -> its stored position
  account: null,         // the signed-in biscuits account
  me: null, meId: null,
  writes: { mode: "preview", write_projects: [], all_projects: false },
  justDone: new Set(),   // completed this session: keep visible until view change
};

const listeners = new Set();
export const onChange = (fn) => listeners.add(fn);
export const emit = (ev = {}) => listeners.forEach((fn) => fn(ev));

async function api(path, opts = {}) {
  const res = await fetch(BASE + path, { ...opts, headers: { ...(opts.body ? HDRS : { "X-Biscuits": "1" }), ...(opts.headers || {}) } });
  let body = {};
  try { body = await res.json(); } catch (_) {}
  if (body.code === "login") {
    location.href = `${BASE}/login?next=${encodeURIComponent(location.pathname + location.search)}`;
    return new Promise(() => {}); // the page is going away
  }
  if (body.code === "must_change") {
    location.href = `${BASE}/password`;
    return new Promise(() => {});
  }
  if (!res.ok || body.ok === false) {
    const err = new Error(body.error || `HTTP ${res.status}`);
    err.code = body.code;
    throw err;
  }
  return body;
}
export const getJSON = (p) => api(p);
export const post = (p, body) => api(p, { method: "POST", body: JSON.stringify(body) });

// ---------- loading ----------
export function adopt(body) {
  S.snap = body.data;
  S.fetchedAt = body.fetched_at;
  if (body.writes) S.writes = body.writes;
  S.archived = new Set(body.archived_projects || []);
  S.sectionOrder = new Map((body.section_order || []).map((id, n) => [id, n]));
  if (body.account) S.account = body.account;
  S.me = S.snap.me; S.meId = S.snap.me_id;
  S.statuses = new Map(S.snap.statuses.map((s) => [s.id, s]));
  S.priorities = [...S.snap.priorities].sort((a, b) => a.position - b.position);
  S.projects = new Map(S.snap.projects.map((p) => [p.id, p]));
  S.users = new Map((S.snap.users || []).map((u) => [u.id, u]));
  // Fall back to names seen on tasks if /users wasn't readable.
  for (const r of S.snap.work_packages) {
    if (r.assignee_id && !S.users.has(r.assignee_id)) S.users.set(r.assignee_id, { id: r.assignee_id, name: r.assignee });
  }
  // Keep local-only rows and preview-edited rows across refreshes, so preview
  // edits don't silently vanish when fresh data arrives.
  const keep = [...S.rows.values()].filter((r) => r._local || r._preview);
  S.rows = new Map(S.snap.work_packages.map((r) => [r.id, r]));
  for (const r of keep) S.rows.set(r.id, r);
  reindex();
}

function reindex() {
  S.kids = new Map();
  for (const r of S.rows.values()) {
    if (r.parent_id && S.rows.has(r.parent_id)) {
      if (!S.kids.has(r.parent_id)) S.kids.set(r.parent_id, []);
      S.kids.get(r.parent_id).push(r.id);
    }
  }
  for (const arr of S.kids.values()) arr.sort(byOrder);
}
// Real ids in id order; unsaved local rows (negative ids) after, in creation order.
const byOrder = (a, b) => (a < 0) - (b < 0) || (a < 0 ? b - a : a - b);

export async function loadSnapshot() { adopt(await api("/api/snapshot")); }
export async function refresh() { adopt(await api("/api/refresh", { method: "POST" })); emit({ type: "reload" }); }

// ---------- queries ----------
export const row = (id) => S.rows.get(+id);
export const children = (id) => (S.kids.get(+id) || []).map((k) => S.rows.get(k));
export const allRows = () => [...S.rows.values()];
// Everyday views leave out tasks in archived projects. Search and the project itself still find them.
export const workingRows = () => allRows().filter((r) => !S.archived.has(r.project_id));
export function ancestors(r) {
  const out = [];
  let p = r && S.rows.get(r.parent_id);
  while (p && out.length < 20) { out.unshift(p); p = S.rows.get(p.parent_id); }
  return out;
}
export const isSection = (r) => !!(r && r.section != null && !r.parent_id);
export const sectionTitle = (r) => (r.section && r.section !== "Untitled section" ? r.section : "Untitled section");
// Stored positions first, then the rest in id order (see sections.py).
const positionOf = (r) => S.sectionOrder.get(r.id) ?? Number.MAX_SAFE_INTEGER;
export const sectionsOf = (pid) => allRows().filter((r) => r.project_id === pid && isSection(r))
  .sort((a, b) => positionOf(a) - positionOf(b) || a.id - b.id);
export const liveProject = (pid) => S.writes.mode === "live" && (S.writes.all_projects || S.writes.write_projects.includes(pid));
export const projectName = (pid) => (S.projects.get(pid) || {}).name || "";
const byName = (a, b) => a.name.localeCompare(b.name);
export const activeProjects = () => [...S.projects.values()].filter((p) => p.active && !S.archived.has(p.id)).sort(byName);
export const archivedProjects = () => [...S.projects.values()].filter((p) => p.active && S.archived.has(p.id)).sort(byName);
export const archivedInOP = () => [...S.projects.values()].filter((p) => !p.active).sort(byName);
export const openKids = (id) => children(id).filter((c) => !c.done).length;

// ---------- local application of a change ----------
const FIELDS = ["subject", "description", "start", "due", "status_id", "assignee_id", "priority_id", "parent_id"];

function applyLocal(r, ch) {
  if ("subject" in ch) {
    r.subject = ch.subject;
    if (r.section != null) r.section = (/^\[(.*)\]$/s.exec(r.subject) || [])[1]?.trim() ?? null;
  }
  if ("start" in ch) { r.start = ch.start || null; r.own_start = r.start; }
  if ("due" in ch) { r.due = ch.due || null; r.own_due = r.due; }
  if ("status_id" in ch) {
    const s = S.statuses.get(+ch.status_id) || {};
    r.status_id = +ch.status_id; r.status = s.name; r.status_color = s.color; r.done = !!s.done;
  }
  if ("assignee_id" in ch) {
    r.assignee_id = ch.assignee_id ? +ch.assignee_id : null;
    r.assignee = r.assignee_id ? (S.users.get(r.assignee_id) || {}).name : null;
  }
  if ("priority_id" in ch) {
    const p = S.priorities.find((x) => x.id === +ch.priority_id) || {};
    r.priority_id = p.id; r.priority = p.name; r.priority_pos = p.position;
  }
  if ("parent_id" in ch) {
    r.parent_id = ch.parent_id ? +ch.parent_id : null;
    const par = S.rows.get(r.parent_id);
    if (par) { r.project_id = par.project_id; r.project = par.project; }
    reindex();
  }
  if ("description" in ch) r._description = ch.description;
}

const snapshotOf = (r, keys) => Object.fromEntries(keys.map((k) => [k, k === "description" ? r._description : r[k]]));

function adoptServerRow(server) {
  const r = S.rows.get(server.id);
  if (!r) { S.rows.set(server.id, server); reindex(); return server; }
  const parentChanged = r.parent_id !== server.parent_id;
  Object.assign(r, server, { _preview: false });
  if (parentChanged) reindex();
  return r;
}

let previewNoticeShown = false;
function previewNotice(r, reason) {
  r._preview = true;
  if (previewNoticeShown) return;
  previewNoticeShown = true;
  toast(`Preview only: “${r.project}” isn't write-enabled, so this change stays in your browser and isn't saved to OpenProject.`, { kind: "warn", ms: 7000 });
}

// ---------- mutations ----------
export async function update(id, changes, opts = {}) {
  const r = row(id);
  if (!r) return;
  if (r.can_edit === false) {
    toast("You don't have permission to edit this task in OpenProject.", { kind: "warn" });
    emit({ type: "update", ids: [r.id] }); // puts back anything typed in place
    return;
  }
  const keys = Object.keys(changes).filter((k) => FIELDS.includes(k));
  if (!keys.length) return;
  const prev = snapshotOf(r, keys);
  applyLocal(r, changes);
  emit({ type: "update", ids: [r.id] });
  if (opts.undoLabel) {
    toast(opts.undoLabel, { action: "Undo", onAction: () => update(id, prev) });
  }
  if (r._local) return; // unsaved preview row: nothing to send
  try {
    const res = await api(`/api/wp/${r.id}`, { method: "POST", body: JSON.stringify({ changes }) });
    if (res.sent && res.row) adoptServerRow(res.row);
    else if (!res.sent) previewNotice(r, res.reason);
    emit({ type: "update", ids: [r.id] });
  } catch (e) {
    applyLocal(r, prev);
    emit({ type: "update", ids: [r.id] });
    toast(`Couldn't save: ${e.message}`, { kind: "error", ms: 8000 });
  }
}

export function toggleComplete(id) {
  const r = row(id);
  if (!r) return;
  if (r.done) {
    const back = r._reopenTo || S.snap.reopen_status_id;
    return update(id, { status_id: back }, { undoLabel: null });
  }
  r._reopenTo = r.status_id;
  S.justDone.add(r.id);
  return update(id, { status_id: S.snap.complete_status_id }, { undoLabel: `“${trim(r.subject)}” marked complete` });
}

let tmpId = -1;
export async function create({ project_id, parent_id, subject, assignee_id, due }) {
  subject = (subject || "").trim();
  if (!subject) return null;
  const par = parent_id ? row(parent_id) : null;
  project_id = par ? par.project_id : project_id;
  const id = tmpId--;
  const def = S.statuses.get(S.snap.reopen_status_id) || {};
  const r = {
    id, subject, section: null, project_id, project: projectName(project_id),
    type: "Task", status_id: def.id, status: def.name, status_color: def.color, done: false,
    priority: "Normal", assignee_id: assignee_id || null,
    assignee: assignee_id ? (S.users.get(+assignee_id) || {}).name : null,
    author_id: S.meId, author: S.me,
    due: due || null, start: null, parent_id: parent_id || null, pct: null,
    created: new Date().toISOString().slice(0, 10), _local: true, _saving: true,
  };
  S.rows.set(id, r); reindex();
  emit({ type: "create", ids: [id] });
  try {
    const res = await api("/api/wp", { method: "POST", body: JSON.stringify({ project_id, parent_id, subject, assignee_id, due }) });
    r._saving = false;
    if (res.sent && res.row) {
      // Swap the temp row for the real one.
      S.rows.delete(id);
      S.rows.set(res.row.id, res.row);
      reindex();
      emit({ type: "idchange", from: id, to: res.row.id, ids: [res.row.id] });
      return res.row.id;
    }
    previewNotice(r, res.reason);
    emit({ type: "update", ids: [id] });
    return id;
  } catch (e) {
    S.rows.delete(id); reindex();
    emit({ type: "update", ids: [] });
    toast(`Couldn't create task: ${e.message}`, { kind: "error", ms: 8000 });
    return null;
  }
}

export async function comment(id, text) {
  const r = row(id);
  if (r._local) return { sent: false };
  const res = await api(`/api/wp/${r.id}/comment`, { method: "POST", body: JSON.stringify({ text }) });
  if (!res.sent) previewNotice(r, res.reason);
  return res;
}

export async function setArchived(pid, archived) {
  const name = projectName(pid);
  try {
    const res = await api(`/api/project/${pid}/archive`, { method: "POST", body: JSON.stringify({ archived }) });
    S.archived = new Set(res.archived_projects);
    emit({ type: "reload" });
    toast(archived ? `“${name}” archived` : `“${name}” is back in your projects`, archived ? { action: "Undo", onAction: () => setArchived(pid, false) } : {});
  } catch (e) {
    toast(`Couldn't ${archived ? "archive" : "unarchive"} “${name}”: ${e.message}`, { kind: "error", ms: 8000 });
  }
}

// ---------- sections ----------
// A subject is capped at 255 characters, and the brackets count.
export const MAX_SECTION_NAME = 253;

export async function createSection(pid, name) {
  try {
    const res = await api(`/api/project/${pid}/sections`, { method: "POST", body: JSON.stringify({ name }) });
    if (!res.sent || !res.row) return toast("That section wasn't saved: this project isn't write-enabled.", { kind: "warn", ms: 8000 });
    S.rows.set(res.row.id, res.row);
    reindex();
    emit({ type: "create", ids: [res.row.id] });
  } catch (e) {
    toast(`Couldn't add the section: ${e.message}`, { kind: "error", ms: 8000 });
  }
}

export const renameSection = (id, name) => update(id, { subject: `[${name.slice(0, MAX_SECTION_NAME)}]` });

async function saveSectionOrder(pid, ids) {
  const before = S.sectionOrder;
  S.sectionOrder = new Map(before);
  ids.forEach((id, n) => S.sectionOrder.set(id, n));
  emit({ type: "update", ids: [] });
  try {
    await api(`/api/project/${pid}/sections/order`, { method: "POST", body: JSON.stringify({ order: ids }) });
    return true;
  } catch (e) {
    S.sectionOrder = before;
    emit({ type: "update", ids: [] });
    toast(`Couldn't move the section: ${e.message}`, { kind: "error", ms: 8000 });
    return false;
  }
}

// Move a section into another's place. Sections hidden from the current view keep their place too.
export async function moveSection(pid, id, targetId) {
  const before = sectionsOf(pid).map((s) => s.id);
  const to = before.indexOf(targetId);
  if (id === targetId || to < 0 || !before.includes(id)) return;
  const after = before.filter((s) => s !== id);
  after.splice(to, 0, id);
  if (await saveSectionOrder(pid, after)) {
    toast(`Moved “${trim(sectionTitle(row(id)))}”`, { action: "Undo", onAction: () => saveSectionOrder(pid, before) });
  }
}

export const detail = (id) => api(`/api/wp/${id}`);
export const writeLog = () => api("/api/writes");

const trim = (s, n = 48) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
