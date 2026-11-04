// Asana-style task detail pane (slides in on the right).
// Shows snapshot data instantly, then loads description + activity live.
// Blocks re-render independently so typing in one never gets clobbered.

import { $, esc, h, avatar, friendlyDate, shortDate, longDate, dueClass, hue, ago, safeHTML, ICON } from "./util.js";
import { S, row, children, ancestors, update, toggleComplete, create, comment, detail, liveProject, isSection, sectionTitle } from "./store.js";
import { toast, statusPill } from "./ui.js";
import { pickFor, expanded, saveExpanded } from "./list.js";
import { openTask, closeTask, href, go } from "./nav.js";

const pane = () => $("#pane");
const cache = new Map();     // id -> {description, activities, attachments, live, at}
let paneId = null;
let showAll = false;
let editingDesc = false;
export const hooks = { step: () => {} };

export function paneOpen() { return paneId != null; }
export function paneTaskId() { return paneId; }

export function renderPane(id) {
  const el = pane();
  if (!id) {
    paneId = null;
    document.body.classList.remove("pane-open");
    el.innerHTML = "";
    return;
  }
  const r = row(id);
  if (!r) {
    // Not in the snapshot (e.g. archived project): fetch it directly.
    document.body.classList.add("pane-open");
    el.innerHTML = `<div class="p-empty">Task #${esc(id)} isn't in biscuits' copy of OpenProject. It may be in an archived project. <a href="${esc(S.snap.op_url)}/work_packages/${esc(id)}" target="_blank" rel="noopener">Open it in OpenProject ${ICON.ext}</a></div>`;
    paneId = id;
    return;
  }
  const changed = paneId !== r.id;
  if (changed) { paneId = r.id; showAll = false; editingDesc = false; el.innerHTML = skeleton(); }
  document.body.classList.add("pane-open");
  paint();
  if (changed) {
    el.querySelector(".p-scroll").scrollTop = 0;
    load(r.id);
  }
}

function skeleton() {
  return `<div class="p-top" data-b="top"></div>
    <div class="p-scroll">
      <div data-b="banner"></div>
      <div class="p-crumbs" data-b="crumbs"></div>
      <div class="p-titlewrap" data-b="title"></div>
      <div class="p-fields" data-b="fields"></div>
      <div class="p-sec" data-b="desc"></div>
      <div class="p-sec" data-b="subtasks"></div>
      <div class="p-sec p-activity" data-b="activity"></div>
    </div>
    <div class="p-compose" data-b="compose"></div>`;
}

const BLOCKS = { top, banner, crumbs, title, fields, desc, subtasks, activity, compose };

function paint() {
  const el = pane();
  const r = row(paneId);
  if (!r) return;
  for (const [name, fn] of Object.entries(BLOCKS)) {
    const b = el.querySelector(`[data-b="${name}"]`);
    if (!b) continue;
    if (b.contains(document.activeElement) && document.activeElement !== document.body &&
        /INPUT|TEXTAREA/.test(document.activeElement.tagName)) continue;
    b.innerHTML = fn(r);
  }
  autosize(el.querySelector(".p-title"));
}

async function load(id) {
  try {
    const d = await detail(id);
    if (d.row) {
      const r = row(id);
      if (r && !r._preview) Object.assign(r, d.row);
    }
    cache.set(id, { ...d, at: Date.now(), local: (cache.get(id) || {}).local || [] });
  } catch (e) {
    cache.set(id, { error: e.message, activities: [], local: [] });
  }
  if (paneId === id) paint();
}

// ---------- blocks ----------
function top(r) {
  const live = liveProject(r.project_id) && !r._local;
  return `<button class="btn complete ${r.done ? "on" : ""}" data-act="complete" type="button">${ICON.check}<span>${r.done ? "Completed" : "Mark complete"}</span></button>
    <span class="p-mode ${live ? "live" : "preview"}" title="${live ? "Edits to this project are saved to OpenProject" : "This project isn't write-enabled yet: edits are a preview only"}">${live ? "Live" : "Preview"}</span>
    <span class="p-spacer"></span>
    <button class="icon-btn" data-act="prev" title="Previous task (↑)">${ICON.up}</button>
    <button class="icon-btn" data-act="next" title="Next task (↓)">${ICON.down}</button>
    <button class="icon-btn" data-act="copy" title="Copy task link">${ICON.link}</button>
    ${r._local ? "" : `<a class="icon-btn" href="${esc(S.snap.op_url)}/work_packages/${r.id}" target="_blank" rel="noopener" title="Open in OpenProject">${ICON.ext}</a>`}
    <button class="icon-btn" data-act="close" title="Close details (Esc)">${ICON.x}</button>`;
}

function banner(r) {
  const bits = [];
  if (r.done) bits.push(`<div class="p-banner done">${ICON.check} This task is completed.</div>`);
  if (r._local) bits.push(`<div class="p-banner warn">This task only exists in your browser (preview). It isn't saved to OpenProject.</div>`);
  else if (r._preview) bits.push(`<div class="p-banner warn">You've made preview edits to this task. They aren't saved to OpenProject.</div>`);
  const c = cache.get(r.id);
  if (c && c.error) bits.push(`<div class="p-banner err">Couldn't load details: ${esc(c.error)}</div>`);
  return bits.join("");
}

function crumbs(r) {
  const parts = [`<a href="${href(`/project/${r.project_id}`)}" data-nav class="ppill" style="--h:${hue(r.project || "")}">${esc(r.project || "")}</a>`];
  for (const a of ancestors(r)) {
    parts.push(isSection(a)
      ? `<a href="${href(`/project/${r.project_id}`)}" data-nav class="crumb-sec">${esc(sectionTitle(a))}</a>`
      : `<a href="#" data-open="${a.id}">${esc(a.subject)}</a>`);
  }
  return parts.join(`<span class="sep">›</span>`);
}

function title(r) {
  return `<textarea class="p-title" rows="1" spellcheck="true" maxlength="255" aria-label="Task name">${esc(r.subject)}</textarea>`;
}

function field(label, body, pick, cls = "") {
  return `<div class="f-row"><div class="f-label">${label}</div><div class="f-val ${cls} ${pick ? "pickable" : ""}" ${pick ? `data-pick="${pick}"` : ""}>${body}</div></div>`;
}

function fields(r) {
  const date = r.due
    ? `${ICON.cal}<span class="${dueClass(r)}">${r.start && r.start !== r.due ? `${esc(shortDate(r.start))} – ${esc(shortDate(r.due))}` : esc(friendlyDate(r.due))}</span><small>${esc(longDate(r.due))}</small>`
    : `<span class="muted">${ICON.cal} No due date</span>`;
  const out = [
    field("Assignee", r.assignee ? `${avatar(r.assignee, "sm")}<span>${esc(r.assignee)}</span>` : `${avatar(null, "sm")}<span class="muted">No assignee</span>`, "assignee"),
    field("Due date", date, "due"),
    field("Projects", `<a href="${href(`/project/${r.project_id}`)}" data-nav class="ppill" style="--h:${hue(r.project || "")}">${esc(r.project || "")}</a>`),
    field("Status", statusPill({ name: r.status, color: r.status_color }) || `<span class="muted">—</span>`, "status"),
    field("Priority", r.priority ? `<span class="pill prio-${esc(r.priority)}">${esc(r.priority)}</span>` : "—", "priority"),
  ];
  if (r.responsible) out.push(field("Accountable", `${avatar(r.responsible, "sm")}<span>${esc(r.responsible)}</span>`));
  if (r.pct != null && r.pct > 0) out.push(field("Progress", `<div class="bar"><i style="width:${r.pct}%"></i></div><small>${r.pct}%</small>`));
  if (r.type && r.type !== "Task") out.push(field("Type", esc(r.type)));
  return out.join("");
}

function desc(r) {
  const c = cache.get(r.id) || {};
  const raw = r._description != null ? r._description : (c.description || {}).raw || "";
  if (editingDesc) {
    return `<div class="p-h">Description</div>
      <textarea class="p-desc-edit" placeholder="What is this task about?">${esc(raw)}</textarea>
      <div class="p-desc-actions"><span class="muted">Markdown · Ctrl+Enter to save</span><button class="btn sm ghost" data-act="desc-cancel">Cancel</button><button class="btn sm primary" data-act="desc-save">Save</button></div>`;
  }
  let body;
  if (r._description != null) body = `<div class="p-desc-raw">${esc(r._description) || ""}</div>`;
  else if (!c.at && !c.error) body = `<div class="muted shimmer">Loading…</div>`;
  else body = (c.description || {}).html ? `<div class="p-desc-html">${safeHTML(c.description.html)}</div>` : "";
  return `<div class="p-h">Description</div><div class="p-desc" data-act="desc-edit" title="Click to edit">${body || `<span class="muted">What is this task about? Click to add details.</span>`}</div>
    ${c.attachments && c.attachments.length ? `<div class="p-attach">${c.attachments.map((a) => `<span class="att">📎 ${esc(a.name)}</span>`).join("")} <span class="muted">(open in OpenProject to view)</span></div>` : ""}`;
}

// Subtasks render as a collapsible tree (any depth), so the whole hierarchy
// under a task can be explored in place without opening another task.
function descendants(id) {
  const out = [];
  for (const k of children(id)) out.push(k, ...descendants(k.id));
  return out;
}

function stRows(id, depth, out) {
  for (const k of children(id)) {
    const kids = children(k.id);
    const open = kids.length && expanded.has(k.id);
    out.push(`<div class="st-row ${k.done ? "done" : ""}" data-id="${k.id}" style="--depth:${depth}">
      <button class="caret ${open ? "open" : ""} ${kids.length ? "" : "none"}" data-act="st-caret" type="button" aria-label="Show subtasks">${ICON.caret}</button>
      <button class="check ${k.done ? "on" : ""}" data-act="st-check" type="button">${ICON.check}</button>
      <span class="st-title">${esc(k.subject)}</span>
      ${kids.length ? `<span class="subcount" title="${kids.filter((c) => !c.done).length} open of ${kids.length}">${kids.length}${ICON.sub}</span>` : ""}
      ${k._preview || k._local ? `<span class="badge-preview">preview</span>` : ""}
      <span class="st-due ${dueClass(k)}" data-act="st-due">${k.due ? esc(friendlyDate(k.due)) : `<span class="ghost-ic">${ICON.cal}</span>`}</span>
      <span class="st-who" data-act="st-who">${avatar(k.assignee, "xs")}</span>
    </div>`);
    if (open) stRows(k.id, depth + 1, out);
  }
  return out;
}

function subtasks(r) {
  if (isSection(r)) return "";
  const kids = children(r.id);
  const all = descendants(r.id);
  const nested = all.length > kids.length;
  const nDone = kids.filter((k) => k.done).length;
  const anyOpen = all.some((k) => expanded.has(k.id) && children(k.id).length);
  return `<div class="p-h">Subtasks ${kids.length ? `<span class="muted">${nDone}/${kids.length} done${nested ? ` · ${all.length} in all` : ""}</span>` : ""}
      ${nested ? `<span class="act-tabs"><button data-act="st-all" data-open-all="${anyOpen ? 0 : 1}">${anyOpen ? "Collapse all" : "Expand all"}</button></span>` : ""}</div>
    <div class="st-list">${stRows(r.id, 0, []).join("")}</div>
    <div class="st-add"><span class="plus">${ICON.plus}</span><input placeholder="Add subtask" maxlength="255" data-act="st-add"></div>`;
}

const ASANA_RE = /^\*\[Asana import — (\d{4}-\d{2}-\d{2}) — (.+?)\]\*\s*/;
const AUTO_RE = /^_Updated automatically by/;

function activity(r) {
  const c = cache.get(r.id);
  if (!c || (!c.at && !c.error)) return `<div class="p-h">Activity</div><div class="muted shimmer">Loading activity…</div>`;
  const items = [...(c.activities || []), ...(c.local || [])];
  const comments = items.filter((a) => a.kind === "comment" && !AUTO_RE.test(a.comment_raw));
  const changes = items.length - comments.length;
  const list = showAll ? items : comments;
  const html = list.map((a) => {
    const asana = a.comment_raw && a.comment_raw.match(ASANA_RE);
    const who = asana ? asana[2] : (a.user || "Someone");
    const when = asana ? friendlyDate(asana[1]) : ago(a.at);
    if (a.kind === "comment" && !AUTO_RE.test(a.comment_raw)) {
      let body = a.local ? `<p>${esc(a.comment_raw).replace(/\n/g, "<br>")}</p>` : safeHTML(a.comment_html);
      if (asana) body = body.replace(/^\s*<p[^>]*>\s*<em>\[Asana import[^<]*<\/em>\s*<\/p>/, "");
      return `<div class="act comment">${avatar(who, "sm")}<div class="act-body">
        <div class="act-meta"><b>${esc(who)}</b> <span class="muted">${esc(when)}</span>${asana ? `<span class="tag">from Asana</span>` : ""}${a.local ? `<span class="badge-preview">${a.sent ? "sent" : "preview"}</span>` : ""}</div>
        <div class="act-text">${body}</div></div></div>`;
    }
    const det = a.details && a.details.length ? a.details.map((d) => safeHTML(d)).join("; ") : "updated this task";
    return `<div class="act change"><span class="dot"></span><div><b>${esc(who)}</b> ${det} <span class="muted">· ${esc(ago(a.at))}</span></div></div>`;
  }).join("");
  return `<div class="p-h">Activity
      <span class="act-tabs"><button class="${showAll ? "" : "on"}" data-act="tab-comments">Comments ${comments.length}</button><button class="${showAll ? "on" : ""}" data-act="tab-all">All activity</button></span></div>
    ${html || `<div class="muted">${showAll ? "No activity yet." : "No comments yet."}${!showAll && changes ? ` <a href="#" data-act="tab-all">Show ${changes} changes</a>` : ""}</div>`}`;
}

function compose(r) {
  return `${avatar(S.me, "sm")}<textarea class="p-comment" rows="1" placeholder="Add a comment…" data-act="comment"></textarea><button class="btn sm primary" data-act="send" type="button">Comment</button>`;
}

// ---------- behaviour ----------
function autosize(t) { if (t) { t.style.height = "auto"; t.style.height = t.scrollHeight + "px"; } }

function copyText(s) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(s);
  const t = h(`<textarea style="position:fixed;opacity:0"></textarea>`); t.value = s;
  document.body.appendChild(t); t.select(); document.execCommand("copy"); t.remove();
  return Promise.resolve();
}

async function sendComment() {
  const t = pane().querySelector(".p-comment");
  const text = t.value.trim();
  if (!text) return;
  const id = paneId;
  t.disabled = true;
  try {
    const res = await comment(id, text);
    t.value = ""; autosize(t);
    const c = cache.get(id) || { activities: [], local: [] };
    if (res.sent) { await load(id); toast("Comment added"); }
    else {
      c.local = [...(c.local || []), { kind: "comment", local: true, sent: false, user: S.me, at: new Date().toISOString(), comment_raw: text }];
      cache.set(id, c);
    }
    showAll = showAll && true;
    paint();
  } catch (e) {
    toast(`Couldn't comment: ${e.message}`, { kind: "error" });
  } finally { t.disabled = false; t.focus(); }
}

async function saveDesc() {
  const t = pane().querySelector(".p-desc-edit");
  if (!t) return;
  const id = paneId;
  const c = cache.get(id) || {};
  const before = (row(id)._description != null ? row(id)._description : (c.description || {}).raw) || "";
  editingDesc = false;
  if (t.value !== before) {
    await update(id, { description: t.value });
    const r = row(id);
    if (r && !r._preview && !r._local) { delete r._description; await load(id); }
  }
  t.blur();
  paint();
}

export function wirePane() {
  const el = pane();
  el.addEventListener("click", async (e) => {
    const t = e.target;
    const r = row(paneId);
    const open = t.closest("[data-open]");
    if (open) { e.preventDefault(); return openTask(+open.dataset.open); }
    const pick = t.closest(".f-val[data-pick]");
    if (pick && r) return pickFor(pick.dataset.pick, pick, r);
    const act = t.closest("[data-act]");
    if (!act || !r) return;
    const a = act.dataset.act;
    const st = t.closest(".st-row");
    if (a === "complete") return toggleComplete(r.id);
    if (a === "close") return closeTask();
    if (a === "prev") return hooks.step(-1);
    if (a === "next") return hooks.step(1);
    if (a === "copy") {
      await copyText(location.origin + href(`/project/${r.project_id}`, { task: r.id }));
      return toast("Task link copied");
    }
    if (a === "desc-edit" && !t.closest("a")) { editingDesc = true; paint(); const ta = el.querySelector(".p-desc-edit"); ta.focus(); autosize(ta); return; }
    if (a === "desc-cancel") { editingDesc = false; document.activeElement.blur(); return paint(); }
    if (a === "desc-save") return saveDesc();
    if (a === "tab-all") { e.preventDefault(); showAll = true; return paint(); }
    if (a === "tab-comments") { showAll = false; return paint(); }
    if (a === "send") return sendComment();
    if (a === "st-all") {
      const openAll = act.dataset.openAll === "1";
      for (const k of descendants(r.id)) if (children(k.id).length) openAll ? expanded.add(k.id) : expanded.delete(k.id);
      saveExpanded(); return paint();
    }
    if (st) {
      const k = row(st.dataset.id);
      if (a === "st-caret") { expanded.has(k.id) ? expanded.delete(k.id) : expanded.add(k.id); saveExpanded(); return paint(); }
      if (a === "st-check") return toggleComplete(k.id);
      if (a === "st-due") return pickFor("due", act, k);
      if (a === "st-who") return pickFor("assignee", act, k);
    }
  });
  el.addEventListener("click", (e) => {
    const st = e.target.closest(".st-row");
    if (st && !e.target.closest("[data-act]")) openTask(+st.dataset.id);
  });
  el.addEventListener("input", (e) => {
    if (e.target.matches(".p-title, .p-comment, .p-desc-edit")) autosize(e.target);
  });
  el.addEventListener("keydown", async (e) => {
    const t = e.target;
    if (t.matches(".p-title") && (e.key === "Enter" || e.key === "Escape")) {
      e.preventDefault();
      if (e.key === "Escape") t.value = row(paneId).subject;
      t.blur();
    }
    if (t.matches(".p-comment") && e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); sendComment(); }
    if (t.matches(".p-desc-edit")) {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); saveDesc(); }
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); editingDesc = false; t.blur(); paint(); }
    }
    if (t.matches('[data-act="st-add"]') && e.key === "Enter") {
      e.preventDefault();
      const v = t.value.trim();
      if (!v) return;
      t.value = "";
      await create({ parent_id: paneId, subject: v });
      paint();
      pane().querySelector('[data-act="st-add"]').focus();
    }
    if (t.matches('[data-act="st-add"]') && e.key === "Escape") { e.stopPropagation(); t.value = ""; t.blur(); }
  });
  el.addEventListener("focusout", (e) => {
    const t = e.target;
    if (t.matches(".p-title")) {
      const r = row(paneId);
      const v = t.value.replace(/\s+/g, " ").trim();
      if (r && v && v !== r.subject) update(r.id, { subject: v });
      else if (r) t.value = r.subject;
      setTimeout(paint, 0);
    }
  });
}

// When a temp (preview) id is replaced by a real one, follow it.
export function followId(from, to) {
  if (paneId === from) openTask(to, { replace: true });
}
