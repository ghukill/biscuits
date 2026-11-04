// The Asana-style task list: collapsible groups (sections / due buckets),
// rows with a round complete button, inline rename, click-to-open details,
// expandable subtasks to any depth, per-cell pickers, inline "Add task",
// and drag-to-section where the view allows it.

import { esc, h, avatar, friendlyDate, dueClass, hue, ICON, $$ } from "./util.js";
import { S, row, children, toggleComplete, update, create, liveProject, activeProjects, getJSON } from "./store.js";
import { pickPerson, pickDate, pickStatus, pickPriority, pickProject, statusPill, closePopover } from "./ui.js";
import { openTask, currentTask, go } from "./nav.js";

const EXP_KEY = "biscuits.expanded.v1";
const COL_KEY = "biscuits.collapsed.v1";
export const expanded = new Set(JSON.parse(localStorage.getItem(EXP_KEY) || "[]"));
const collapsed = new Set(JSON.parse(localStorage.getItem(COL_KEY) || "[]"));
export const saveExpanded = () => localStorage.setItem(EXP_KEY, JSON.stringify([...expanded].slice(-2000)));
const moreOpen = new Set();
const saveCol = () => localStorage.setItem(COL_KEY, JSON.stringify([...collapsed]));

const COLS = {
  assignee: { title: "Assignee", w: "minmax(130px, 170px)" },
  due: { title: "Due date", w: "110px" },
  project: { title: "Project", w: "minmax(120px, 190px)" },
  status: { title: "Status", w: "minmax(110px, 150px)" },
  priority: { title: "Priority", w: "100px" },
};

// OP only accepts project members as assignees, so ask once per project.
const memberCache = new Map();
export async function usersForPicker(pid) {
  if (!memberCache.has(pid)) {
    memberCache.set(pid, getJSON(`/api/assignees/${pid}`).then((d) => new Set(d.ids)).catch(() => null));
  }
  const members = await memberCache.get(pid);
  const all = [...S.users.values()].filter((u) => u.status !== "locked" && u.name !== "OpenProject Admin" && (!members || members.has(u.id)));
  return all.sort((a, b) => (b.id === S.meId) - (a.id === S.meId) || a.name.localeCompare(b.name));
}

/**
 * opts: {
 *   groups: [{ key, title, ids, add?: {project_id?, parent_id?, assignee_id?, due?}, sub?, dropParent? }],
 *   cols: ["assignee","due","project","status"],
 *   tree: true,        // show subtask carets
 *   crumbs: false,     // show "‹ parent" for rows whose parent isn't shown
 *   visible: (r)=>bool // which subtasks to show when expanded
 *   empty: "text",
 *   scope: "key prefix for collapsed groups",
 * }
 */
export function renderList(host, opts) {
  const state = host._list || (host._list = { editing: false, dirty: false });
  state.opts = opts;
  if (state.editing) { state.dirty = true; return; }
  const cols = opts.cols || ["assignee", "due", "project"];
  const tpl = `minmax(280px, 1fr) ${cols.map((c) => COLS[c].w).join(" ")}`;
  const sel = currentTask();
  const parts = [`<div class="tl" style="--cols:${tpl}">`,
    `<div class="tl-head"><div>Task name</div>${cols.map((c) => `<div>${COLS[c].title}</div>`).join("")}</div>`];
  let any = false;
  for (const g of opts.groups) {
    const ck = `${opts.scope || ""}:${g.key}`;
    const isCol = g.collapsed ?? collapsed.has(ck);
    const rows = g.ids.map(row).filter(Boolean);
    any ||= rows.length > 0 || !!g.add;
    if (g.title != null) {
      parts.push(`<div class="tl-group ${isCol ? "closed" : ""} ${g.tone || ""}" data-ck="${esc(ck)}" data-drop="${g.dropParent ?? ""}">
        <button class="gcaret" type="button" aria-label="Toggle section">${ICON.caret}</button>
        <span class="gtitle">${esc(g.title)}</span><span class="gcount">${rows.length}</span>
        ${g.sub ? `<span class="gsub">${esc(g.sub)}</span>` : ""}</div>`);
    }
    if (isCol) continue;
    parts.push(`<div class="tl-body" data-drop="${g.dropParent ?? ""}">`);
    const showAll = !g.limit || moreOpen.has(ck) || rows.length <= g.limit + 2;
    const slice = showAll ? rows : rows.slice(0, g.limit);
    for (const r of slice) renderRow(parts, r, 0, cols, opts, sel, new Set(g.ids));
    if (!showAll) parts.push(`<div class="tl-more" data-more="${esc(ck)}">Show ${rows.length - slice.length} more</div>`);
    else if (g.limit && rows.length > g.limit + 2) parts.push(`<div class="tl-more" data-less="${esc(ck)}">Show fewer</div>`);
    if (g.add) parts.push(`<div class="tl-add" data-add='${esc(JSON.stringify(g.add))}'><span class="plus">${ICON.plus}</span><span>Add task…</span></div>`);
    if (!rows.length && !g.add) parts.push(`<div class="tl-none">Nothing here</div>`);
    parts.push(`</div>`);
  }
  if (!any) parts.push(`<div class="tl-empty">${esc(opts.empty || "Nothing to show.")}</div>`);
  parts.push(`</div>`);
  host.innerHTML = parts.join("");
  if (!host._wired) wire(host);
}

function renderRow(parts, r, depth, cols, opts, sel, shown) {
  const kids = opts.tree === false ? [] : children(r.id).filter((c) => opts.visible ? opts.visible(c) : true);
  const allKids = children(r.id);
  const open = expanded.has(r.id) && kids.length > 0;
  const par = row(r.parent_id);
  const parName = par && (par.section != null ? (par.section === "Untitled section" ? "" : par.section) : par.subject);
  const crumb = opts.crumbs && depth === 0 && parName && !shown.has(par.id)
    ? `<span class="crumb" ${par.section != null ? "" : `data-open="${par.id}"`} title="${par.section != null ? "Section" : "Parent task"}">‹ ${esc(parName)}</span>` : "";
  const nOpen = allKids.filter((c) => !c.done).length;
  const cells = cols.map((c) => cell(c, r)).join("");
  parts.push(`<div class="tl-row ${r.done ? "done" : ""} ${sel === r.id ? "sel" : ""} ${r._saving ? "saving" : ""}" data-id="${r.id}" draggable="${opts.draggable ? "true" : "false"}" style="--depth:${depth}">
    <div class="c-name">
      <button class="caret ${open ? "open" : ""} ${kids.length ? "" : "none"}" type="button" tabindex="-1" aria-label="Show subtasks">${ICON.caret}</button>
      <button class="check ${r.done ? "on" : ""}" type="button" title="${r.done ? "Mark incomplete" : "Mark complete"}" aria-label="Complete">${ICON.check}</button>
      <span class="title" spellcheck="false">${esc(r.subject)}</span>
      ${allKids.length ? `<span class="subcount" title="${nOpen} open of ${allKids.length} subtasks">${nOpen || allKids.length}${ICON.sub}</span>` : ""}
      ${crumb}
      ${r._preview ? `<span class="badge-preview" title="Not saved to OpenProject (preview)">preview</span>` : ""}
      <span class="details">Details ${ICON.caret}</span>
    </div>${cells}</div>`);
  if (open) for (const k of kids) renderRow(parts, k, depth + 1, cols, opts, sel, shown);
}

function cell(c, r) {
  if (c === "assignee") return `<div class="cell c-assignee" data-pick="assignee">${avatar(r.assignee, "sm")}<span>${r.assignee ? esc(r.assignee) : ""}</span></div>`;
  if (c === "due") return `<div class="cell c-due ${dueClass(r)}" data-pick="due">${r.due ? esc(friendlyDate(r.due)) : `<span class="ghost-ic">${ICON.cal}</span>`}</div>`;
  if (c === "project") return `<div class="cell c-project" data-proj="${r.project_id}"><span class="ppill" style="--h:${hue(r.project || "")}">${esc(r.project || "")}</span></div>`;
  if (c === "status") return `<div class="cell c-status" data-pick="status">${statusPill({ name: r.status, color: r.status_color })}</div>`;
  if (c === "priority") return `<div class="cell c-prio" data-pick="priority">${r.priority && r.priority !== "Normal" ? `<span class="pill prio-${esc(r.priority)}">${esc(r.priority)}</span>` : ""}</div>`;
  return "";
}

export function pickFor(kind, anchor, r) {
  if (kind === "assignee") usersForPicker(r.project_id).then((users) => pickPerson(anchor, users, r.assignee_id, (v) => update(r.id, { assignee_id: v })));
  else if (kind === "due") pickDate(anchor, r, (ch) => update(r.id, ch));
  else if (kind === "status") pickStatus(anchor, [...S.statuses.values()].sort((a, b) => a.position - b.position), r.status_id, (v) => update(r.id, { status_id: v }));
  else if (kind === "priority") pickPriority(anchor, S.priorities, r.priority_id, (v) => update(r.id, { priority_id: v }));
}

// ---------- inline rename ----------
export function editTitle(host, el, r) {
  const state = host._list || {};
  state.editing = true;
  el.contentEditable = "plaintext-only";
  el.focus();
  const range = document.createRange(); range.selectNodeContents(el);
  const s = getSelection(); s.removeAllRanges(); s.addRange(range);
  const finish = (save) => {
    el.removeEventListener("blur", onBlur);
    el.removeEventListener("keydown", onKey);
    el.contentEditable = "false";
    state.editing = false;
    const v = el.textContent.replace(/\s+/g, " ").trim();
    if (save && v && v !== r.subject) update(r.id, { subject: v });
    else el.textContent = r.subject;
    if (state.dirty) { state.dirty = false; renderList(host, state.opts); }
  };
  const onBlur = () => finish(true);
  const onKey = (e) => {
    if (e.key === "Enter") { e.preventDefault(); finish(true); }
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(false); }
  };
  el.addEventListener("blur", onBlur);
  el.addEventListener("keydown", onKey);
}

// ---------- inline add (Enter keeps adding, like Asana) ----------
function startAdd(host, addEl) {
  const ctx = JSON.parse(addEl.dataset.add);
  const state = host._list;
  const needProject = !ctx.project_id && !ctx.parent_id;
  let project_id = ctx.project_id || null;
  const inp = h(`<div class="tl-add editing"><span class="check ghost">${ICON.check}</span><input placeholder="Write a task name" maxlength="255">${needProject ? `<button type="button" class="btn sm proj-btn">Pick project</button>` : ""}</div>`);
  addEl.replaceWith(inp);
  const field = inp.querySelector("input");
  state.editing = true;
  field.focus();
  const projBtn = inp.querySelector(".proj-btn");
  if (projBtn) {
    const last = +localStorage.getItem("biscuits.lastProject") || null;
    if (last && S.projects.get(last)) { project_id = last; projBtn.textContent = S.projects.get(last).name; }
    projBtn.addEventListener("mousedown", (e) => e.preventDefault());
    projBtn.addEventListener("click", () => pickProject(projBtn, activeProjects(), project_id, (v) => {
      project_id = v; projBtn.textContent = S.projects.get(v).name; localStorage.setItem("biscuits.lastProject", v); field.focus();
    }, { writable: liveProject }));
  }
  const close = () => {
    state.editing = false;
    renderList(host, state.opts);
  };
  field.addEventListener("keydown", async (e) => {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
    if (e.key === "Enter") {
      e.preventDefault();
      const v = field.value.trim();
      if (!v) return close();
      if (!project_id && !ctx.parent_id) { projBtn && projBtn.click(); return; }
      field.value = "";
      state.editing = false;
      await create({ ...ctx, project_id, subject: v });
      // keep adding: reopen the add row in the same group
      const again = [...host.querySelectorAll(".tl-add")].find((a) => a.dataset.add === addEl.dataset.add);
      if (again) startAdd(host, again);
    }
  });
  field.addEventListener("blur", () => setTimeout(() => {
    if (document.activeElement !== field && !document.querySelector(".pop") && !field.value.trim()) close();
  }, 150));
}

// ---------- events ----------
function wire(host) {
  host._wired = true;
  host.addEventListener("click", (e) => {
    const t = e.target;
    const g = t.closest(".tl-group");
    if (g) {
      const ck = g.dataset.ck;
      collapsed.has(ck) ? collapsed.delete(ck) : collapsed.add(ck);
      saveCol(); renderList(host, host._list.opts); return;
    }
    const more = t.closest(".tl-more");
    if (more) {
      more.dataset.more ? moreOpen.add(more.dataset.more) : moreOpen.delete(more.dataset.less);
      return renderList(host, host._list.opts);
    }
    const add = t.closest(".tl-add:not(.editing)");
    if (add) return startAdd(host, add);
    const rowEl = t.closest(".tl-row");
    if (!rowEl) return;
    const r = row(rowEl.dataset.id);
    if (!r) return;
    if (t.closest(".check")) return toggleComplete(r.id);
    if (t.closest(".caret")) {
      expanded.has(r.id) ? expanded.delete(r.id) : expanded.add(r.id);
      saveExpanded(); renderList(host, host._list.opts); return;
    }
    const crumb = t.closest(".crumb");
    if (crumb && crumb.dataset.open) return openTask(+crumb.dataset.open);
    const pick = t.closest("[data-pick]");
    if (pick) return pickFor(pick.dataset.pick, pick, r);
    const proj = t.closest("[data-proj]");
    if (proj && r.project_id) return go(`/project/${r.project_id}`);
    const title = t.closest(".title");
    if (title && title.isContentEditable) return;
    if (title && currentTask() === r.id) return editTitle(host, title, r);
    closePopover();
    openTask(r.id);
  });
  host.addEventListener("dblclick", (e) => {
    const title = e.target.closest(".title");
    const rowEl = e.target.closest(".tl-row");
    if (title && rowEl && !title.isContentEditable) editTitle(host, title, row(rowEl.dataset.id));
  });

  // drag a row onto a section (group header or body) to move it there
  let dragId = null;
  host.addEventListener("dragstart", (e) => {
    const rowEl = e.target.closest(".tl-row");
    if (!rowEl) return;
    dragId = +rowEl.dataset.id;
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", String(dragId));
    rowEl.classList.add("dragging");
  });
  host.addEventListener("dragend", () => { dragId = null; $$(".drop-on", host).forEach((x) => x.classList.remove("drop-on")); $$(".dragging", host).forEach((x) => x.classList.remove("dragging")); });
  const target = (e) => e.target.closest("[data-drop]:not([data-drop=''])");
  host.addEventListener("dragover", (e) => {
    const d = dragId && target(e);
    if (!d) return;
    e.preventDefault();
    $$(".drop-on", host).forEach((x) => x !== d && x.classList.remove("drop-on"));
    d.classList.add("drop-on");
  });
  host.addEventListener("drop", (e) => {
    const d = target(e);
    if (!d || !dragId) return;
    e.preventDefault();
    const r = row(dragId);
    const parent = d.dataset.drop === "root" ? null : +d.dataset.drop;
    if (r && r.parent_id !== parent) {
      update(r.id, { parent_id: parent }, { undoLabel: `Moved “${r.subject.slice(0, 40)}”` });
    }
  });
}
