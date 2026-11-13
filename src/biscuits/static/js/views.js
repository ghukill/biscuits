// Views: Home, My tasks / People, Project (list + board), Search, Write log.
// Each view renders into #view and is re-run whenever the store changes.

import { $, $$, esc, h, avatar, friendlyDate, longDate, dueClass, hue, ago, T, RANGES, inRange, ICON } from "./util.js";
import { S, row, children, allRows, workingRows, isSection, sectionTitle, sectionsOf, createSection, renameSection, moveSection, MAX_SECTION_NAME, liveProject, activeProjects, archivedProjects, archivedInOP, projectName, update, create, writeLog, setArchived } from "./store.js";
import { renderList, pickFor } from "./list.js";
import { menu, askText, toast } from "./ui.js";
import { href, go, openTask, currentTask, setParam } from "./nav.js";
import { pickBox, pickClick, domOrder, isPicked, dragsPicks, dragLabel, movePicked } from "./select.js";

// ---------- shared: completion filter (Asana's "Incomplete / Completed / All") ----------
const COMP_KEY = "biscuits.completion.v1";
const compPrefs = JSON.parse(localStorage.getItem(COMP_KEY) || "{}");
const compOf = (scope) => compPrefs[scope] || "incomplete";
const COMP_LABEL = { incomplete: "Incomplete tasks", completed: "Completed tasks", all: "All tasks" };
export function visibleFn(mode) {
  if (mode === "completed") return (r) => r.done;
  if (mode === "all") return () => true;
  return (r) => !r.done || S.justDone.has(r.id);
}
function compButton(scope) {
  return `<button class="btn sm" data-comp="${esc(scope)}" type="button">${ICON.mine}<span>${COMP_LABEL[compOf(scope)]}</span>${ICON.down}</button>`;
}
function wireComp(el, rerender) {
  $$("[data-comp]", el).forEach((b) => b.addEventListener("click", () => {
    const scope = b.dataset.comp;
    menu(b, Object.entries(COMP_LABEL).map(([v, l]) => ({ value: v, label: l })), {
      search: false, current: compOf(scope),
      onPick: (v) => { compPrefs[scope] = v; localStorage.setItem(COMP_KEY, JSON.stringify(compPrefs)); rerender(); },
    });
  }));
}

const byDue = (a, b) => (a.due || "9999") .localeCompare(b.due || "9999") || a.id - b.id;
const tasksOnly = (r) => !isSection(r);

// The view element is reused between renders; build a stable skeleton per
// view key and only re-render the list part, so scroll position survives.
function frame(el, key, html) {
  if (el.dataset.key !== key) {
    el.dataset.key = key;
    el.innerHTML = html;
    el.scrollTop = 0;
    return true;
  }
  return false;
}

// ============ Home ============
const BUCKETS = [
  { key: "overdue", title: "Overdue", tone: "rose", test: (r) => r.due && r.due < T },
  { key: "today", title: "Today", tone: "peach", test: (r) => r.due === T },
  { key: "week", title: "This week", tone: "sky", test: (r) => inRange(r.due, RANGES.week), sub: `${friendlyDate(RANGES.week[0])} – ${friendlyDate(RANGES.week[1])}` },
  { key: "nextweek", title: "Next week", tone: "sage", test: (r) => inRange(r.due, RANGES.nextWeek), sub: `${friendlyDate(RANGES.nextWeek[0])} – ${friendlyDate(RANGES.nextWeek[1])}` },
  { key: "month", title: "This month", test: (r) => inRange(r.due, RANGES.month) },
  { key: "nextmonth", title: "Next month", test: (r) => inRange(r.due, RANGES.nextMonth) },
];

export function viewHome(el) {
  const scope = localStorage.getItem("biscuits.homeScope") || "mine";
  const first = (S.me || "there").split(/\s+/)[0];
  const hr = new Date().getHours();
  const greet = hr < 12 ? "Good morning" : hr < 18 ? "Good afternoon" : "Good evening";
  const fresh = frame(el, "home", `
    <div class="home">
      <div class="home-hero"><div class="muted">${esc(longDate(T))}</div><h1>${greet}, ${esc(first)}</h1><div class="home-stats" id="home-stats"></div></div>
      <div class="home-grid">
        <section class="card home-buckets">
          <div class="card-head"><h2>Upcoming work</h2>
            <div class="seg" id="scope"><button data-s="mine">My tasks</button><button data-s="all">Everyone</button></div></div>
          <p class="card-note">Buckets overlap on purpose, so a task due tomorrow shows under both “This week” and “This month”.</p>
          <div id="home-list"></div>
        </section>
        <section class="card home-projects"><div class="card-head"><h2>Projects</h2></div><div class="ptiles" id="ptiles"></div></section>
      </div>
    </div>`);
  if (fresh) {
    $$("#scope button", el).forEach((b) => b.addEventListener("click", () => { localStorage.setItem("biscuits.homeScope", b.dataset.s); viewHome(el); }));
  }
  $$("#scope button", el).forEach((b) => b.classList.toggle("on", b.dataset.s === scope));

  const vis = visibleFn("incomplete");
  const pool = workingRows().filter(tasksOnly).filter(vis).filter((r) => scope === "all" || r.assignee_id === S.meId);
  const mineOpen = workingRows().filter((r) => tasksOnly(r) && !r.done && r.assignee_id === S.meId);
  const stat = (n, l, cls = "") => `<div class="stat ${cls}"><b>${n}</b><span>${l}</span></div>`;
  $("#home-stats", el).innerHTML =
    stat(mineOpen.length, "open tasks assigned to you") +
    stat(mineOpen.filter(BUCKETS[0].test).length, "overdue", "rose") +
    stat(mineOpen.filter((r) => inRange(r.due, [T, RANGES.week[1]])).length, "due this week", "sky") +
    stat(S.justDone.size, "completed just now", "sage");

  renderList($("#home-list", el), {
    scope: "home",
    groups: BUCKETS.map((b) => ({ key: b.key, title: b.title, tone: b.tone, sub: b.sub, limit: 8, ids: pool.filter(b.test).sort(byDue).map((r) => r.id) })),
    cols: scope === "all" ? ["assignee", "due", "project"] : ["due", "project"],
    crumbs: true, visible: vis,
    empty: "Nothing scheduled. Enjoy the quiet.",
  });

  const open = new Map(), over = new Map();
  for (const r of workingRows()) if (tasksOnly(r) && !r.done) {
    open.set(r.project_id, (open.get(r.project_id) || 0) + 1);
    if (r.due && r.due < T) over.set(r.project_id, (over.get(r.project_id) || 0) + 1);
  }
  $("#ptiles", el).innerHTML = activeProjects().map((p) => `
    <a class="ptile" href="${href(`/project/${p.id}`)}" data-nav style="--h:${hue(p.name)}">
      <span class="ptile-ic">${ICON.list}</span>
      <span class="ptile-name">${esc(p.name)}</span>
      <span class="ptile-meta">${open.get(p.id) || 0} open${over.get(p.id) ? ` · <em>${over.get(p.id)} overdue</em>` : ""}${liveProject(p.id) ? ` · <span class="live-tag">live</span>` : ""}</span>
    </a>`).join("");
}

// ============ My tasks / someone's tasks ============
const DUE_GROUPS = [
  { key: "overdue", title: "Overdue", tone: "rose", test: (r) => r.due && r.due < T },
  { key: "today", title: "Today", tone: "peach", test: (r) => r.due === T, add: { due: T } },
  { key: "week", title: "This week", tone: "sky", test: (r) => r.due > T && r.due <= RANGES.week[1] },
  { key: "nextweek", title: "Next week", tone: "sage", test: (r) => inRange(r.due, RANGES.nextWeek) },
  { key: "later", title: "Later", test: (r) => r.due > RANGES.nextWeek[1] },
  { key: "nodate", title: "No due date", test: (r) => !r.due, add: {} },
];

export function viewPeople(el, uid, { mine = false } = {}) {
  const u = S.users.get(uid) || { name: mine ? S.me : `User #${uid}` };
  const scope = mine ? "my" : "people";
  const fresh = frame(el, `people:${uid}`, `
    <div class="vhead">
      <div class="vtitle">${avatar(u.name, "lg")}<div><h1>${mine ? "My tasks" : esc(u.name)}</h1><div class="muted" id="vsub"></div></div></div>
      <div class="vtools" id="vtools">${compButton(scope)}</div>
    </div>
    <div class="vbody" id="list"></div>`);
  if (fresh) wireComp(el, () => { el.dataset.key = ""; viewPeople(el, uid, { mine }); });
  const mode = compOf(scope);
  const vis = visibleFn(mode);
  const mineRows = workingRows().filter((r) => tasksOnly(r) && r.assignee_id === uid);
  const shown = mineRows.filter(vis);
  $("#vsub", el).textContent = `${mineRows.filter((r) => !r.done).length} open · ${mineRows.filter((r) => r.done).length} completed`;
  const groups = mode === "completed"
    ? [{ key: "completed", title: "Completed", ids: shown.sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || "")).map((r) => r.id) }]
    : DUE_GROUPS.map((g) => ({ key: g.key, title: g.title, tone: g.tone, ids: shown.filter(g.test).sort(byDue).map((r) => r.id), add: g.add ? { ...g.add, assignee_id: uid } : null }));
  renderList($("#list", el), {
    scope: `${scope}:${uid}`, groups, cols: ["due", "project", "status"], crumbs: true, visible: vis,
    empty: mode === "completed" ? "No completed tasks yet." : "You're all caught up!",
  });
}

// ============ Project ============
function projectGroups(pid, vis) {
  const roots = allRows().filter((r) => r.project_id === pid && !(r.parent_id && row(r.parent_id)));
  // A closed section with nothing to show would just be an empty header.
  const sections = sectionsOf(pid).filter((s) => !s.done || children(s.id).some(vis));
  const loose = roots.filter((r) => !isSection(r) && vis(r)).sort((a, b) => a.id - b.id);
  const groups = [];
  if (loose.length || !sections.length) {
    groups.push({ key: "root", title: sections.length ? "No section" : null, ids: loose.map((r) => r.id), add: { project_id: pid }, dropParent: "root" });
  }
  for (const s of sections) {
    groups.push({ key: `s${s.id}`, title: sectionTitle(s), sectionId: s.id, ids: children(s.id).filter(vis).map((r) => r.id), add: { parent_id: s.id }, dropParent: s.id, manage: liveProject(pid) && s.can_edit !== false });
  }
  return groups;
}

function promptAddSection(anchor, pid) {
  askText(anchor, { label: "New section name", action: "Add section", maxlength: MAX_SECTION_NAME, onSubmit: (name) => createSection(pid, name) });
}

// Up and down step past the sections showing now, so a move always shows.
function openSectionMenu(anchor, pid, id, groups, [earlier, later]) {
  const shown = groups.filter((g) => g.sectionId).map((g) => g.sectionId);
  const at = shown.indexOf(id);
  const items = [{ value: "rename", label: "Rename section" }];
  if (at > 0) items.push({ value: "earlier", label: earlier });
  if (at < shown.length - 1) items.push({ value: "later", label: later });
  menu(anchor, items, {
    search: false,
    onPick: (v) => {
      if (v === "earlier") return moveSection(pid, id, shown[at - 1]);
      if (v === "later") return moveSection(pid, id, shown[at + 1]);
      const s = row(id);
      askText(anchor, { label: "Section name", value: s.section || "", maxlength: MAX_SECTION_NAME, onSubmit: (name) => { if (name !== s.section) renameSection(id, name); } });
    },
  });
}

export function viewProject(el, pid, q) {
  const p = S.projects.get(pid);
  if (!p) { el.dataset.key = ""; el.innerHTML = `<div class="tl-empty">That project isn't available. Archived projects can't be read through the API.</div>`; return; }
  const board = q.get("view") === "board";
  const live = liveProject(pid);
  const archived = S.archived.has(pid);
  const fresh = frame(el, `project:${pid}:${board ? "b" : "l"}:${archived ? "a" : ""}`, `
    ${!p.active ? `<div class="p-banner warn">This project is archived in OpenProject, so biscuits can't read its tasks. To get it back, unarchive it in OpenProject.</div>` : ""}
    ${archived ? `<div class="p-banner warn">${ICON.archive} This project is archived. It's hidden from Home, My tasks, All tasks and the sidebar, but search still finds it and you can still edit it.</div>` : ""}
    <div class="vhead">
      <div class="vtitle"><span class="ptile-ic big" style="--h:${hue(p.name)}">${ICON.list}</span><div>
        <h1>${esc(p.name)} <span class="p-mode ${live ? "live" : "preview"}" title="${live ? "Edits here are saved to OpenProject" : "Edits here are a preview only (not saved)"}">${live ? "Live" : "Preview"}</span></h1>
        <div class="tabs"><a href="${href(`/project/${pid}`)}" data-nav class="${board ? "" : "on"}">${ICON.list} List</a><a href="${href(`/project/${pid}`, { view: "board" })}" data-nav class="${board ? "on" : ""}">${ICON.board} Board</a></div></div></div>
      <div class="vtools">${compButton("project")}${p.active && (p.can_manage || S.account?.is_admin) ? `<button class="btn sm" id="archive-btn" type="button">${ICON.archive} ${archived ? "Unarchive" : "Archive"}</button>` : ""}<a class="btn sm" href="${esc(S.snap.op_url)}/projects/${esc(p.identifier)}/work_packages" target="_blank" rel="noopener">${ICON.ext} OpenProject</a></div>
    </div>
    <div class="vbody ${board ? "board-wrap" : ""}" id="list"></div>`);
  if (fresh) {
    wireComp(el, () => { el.dataset.key = ""; viewProject(el, pid, q); });
    const ab = $("#archive-btn", el);
    if (ab) ab.addEventListener("click", () => setArchived(pid, !archived));
  }
  const vis = visibleFn(compOf("project"));
  const groups = projectGroups(pid, vis);
  const sectionActions = {
    addSection: live ? (button) => promptAddSection(button, pid) : null,
    moveSection: (id, target) => moveSection(pid, id, target),
  };
  if (board) {
    return renderBoard($("#list", el), groups, vis, {
      ...sectionActions,
      sectionMenu: (button, id) => openSectionMenu(button, pid, id, groups, ["Move left", "Move right"]),
    });
  }
  renderList($("#list", el), {
    scope: `p${pid}`, groups, cols: ["assignee", "due", "status", "priority"], visible: vis, draggable: true,
    empty: "No tasks in this project yet.",
    ...sectionActions,
    sectionMenu: (button, id) => openSectionMenu(button, pid, id, groups, ["Move up", "Move down"]),
  });
}

// ---------- Board (columns = sections, like Asana) ----------
function renderBoard(host, groups, vis, actions) {
  host._actions = actions;
  if (host._editing) return;
  const sel = currentTask();
  host.innerHTML = `<div class="board">${groups.map((g) => `
    <div class="bcol" data-drop="${g.dropParent}" ${g.sectionId ? `data-section="${g.sectionId}"` : ""}>
      <div class="bcol-head" ${g.manage ? `draggable="true"` : ""}><span class="bcol-title">${esc(g.title || "No section")}</span><span class="gcount">${g.ids.length}</span>${g.manage ? `<button class="section-menu" type="button" title="Section actions" aria-label="Section actions">${ICON.dots}</button>` : ""}</div>
      <div class="bcol-body">
        ${g.ids.map(row).filter(Boolean).map((r) => card(r, sel, vis)).join("")}
        <div class="bcard-add" data-add='${esc(JSON.stringify(g.add))}'>${ICON.plus} Add task</div>
      </div>
    </div>`).join("")}${actions.addSection ? `<button class="btn sm add-section" type="button">${ICON.plus} Add section</button>` : ""}</div>`;
  if (host._wired) return;
  host._wired = true;
  host.addEventListener("mousedown", (e) => { if (e.shiftKey && e.target.closest(".bcard")) e.preventDefault(); });
  host.addEventListener("click", (e) => {
    const t = e.target;
    const addSection = t.closest(".add-section");
    if (addSection) return host._actions.addSection(addSection);
    const sectionMenu = t.closest(".section-menu");
    if (sectionMenu) return host._actions.sectionMenu(sectionMenu, +sectionMenu.closest(".bcol").dataset.section);
    const add = t.closest(".bcard-add");
    if (add) return boardAdd(host, add);
    const c = t.closest(".bcard");
    if (!c) return;
    const r = row(c.dataset.id);
    if (pickClick(e, r.id, domOrder(host, ".bcard[data-id]"))) return;
    const pk = t.closest("[data-pick]");
    if (pk) return pickFor(pk.dataset.pick, pk, r);
    openTask(r.id);
  });
  // A drop can re-render the board and remove the dragged element, and then no
  // dragend arrives, so both of these are also cleared at each start and drop.
  let dragId = null, dragSection = null;
  host.addEventListener("dragstart", (e) => {
    dragId = dragSection = null;
    const head = e.target.closest(".bcol-head");
    if (head) {
      const col = head.closest(".bcol");
      dragSection = +col.dataset.section;
      col.classList.add("dragging");
      e.dataTransfer.setData("text/plain", String(dragSection));
      return;
    }
    const c = e.target.closest(".bcard"); if (!c) return; dragId = +c.dataset.id; c.classList.add("dragging"); e.dataTransfer.setData("text/plain", String(dragId));
    if (dragsPicks(dragId)) dragLabel(e);
  });
  host.addEventListener("dragend", () => { dragId = dragSection = null; $$(".drop-on,.dragging", host).forEach((x) => x.classList.remove("drop-on", "dragging")); });
  const dropCol = (e) => (dragSection ? e.target.closest(".bcol[data-section]") : dragId && e.target.closest(".bcol"));
  host.addEventListener("dragover", (e) => { const col = dropCol(e); if (!col) return; e.preventDefault(); $$(".drop-on", host).forEach((x) => x !== col && x.classList.remove("drop-on")); col.classList.add("drop-on"); });
  host.addEventListener("drop", (e) => {
    const col = dropCol(e);
    const movedSection = dragSection, movedCard = dragId;
    dragSection = dragId = null;
    if (col && movedSection) {
      e.preventDefault();
      return host._actions.moveSection(movedSection, +col.dataset.section);
    }
    if (!col || !movedCard) return;
    e.preventDefault();
    const r = row(movedCard);
    const parent = col.dataset.drop === "root" ? null : +col.dataset.drop;
    if (r && dragsPicks(r.id)) return movePicked(parent, parent ? row(parent).project_id : r.project_id);
    if (r && r.parent_id !== parent) update(r.id, { parent_id: parent }, { undoLabel: `Moved “${r.subject.slice(0, 40)}”` });
  });
}

function card(r, sel, vis) {
  const kids = children(r.id);
  const openK = kids.filter((k) => !k.done).length;
  return `<div class="bcard ${r.done ? "done" : ""} ${sel === r.id ? "sel" : ""} ${isPicked(r.id) ? "picked" : ""}" data-id="${r.id}" draggable="true">
    <div class="bcard-top"><span class="bcard-title">${esc(r.subject)}</span>${pickBox(r)}</div>
    <div class="bcard-meta">
      <span data-pick="assignee">${avatar(r.assignee, "xs")}</span>
      <span class="c-due ${dueClass(r)}" data-pick="due">${r.due ? esc(friendlyDate(r.due)) : ""}</span>
      ${kids.length ? `<span class="subcount" title="${openK} open of ${kids.length}">${openK}/${kids.length}${ICON.sub}</span>` : ""}
      ${r.status && r.status !== "New" && !r.done ? `<span class="pill status sm" style="--c:${esc(r.status_color || "#adb5bd")}">${esc(r.status)}</span>` : ""}
      ${r._preview || r._local ? `<span class="badge-preview">preview</span>` : ""}
    </div></div>`;
}

function boardAdd(host, addEl) {
  const ctx = JSON.parse(addEl.dataset.add);
  const f = h(`<div class="bcard editing"><textarea rows="2" placeholder="Write a task name" maxlength="255"></textarea></div>`);
  addEl.replaceWith(f);
  const t = f.querySelector("textarea");
  host._editing = true;
  t.focus();
  const done = () => { host._editing = false; host.dispatchEvent(new CustomEvent("rerender", { bubbles: true })); };
  t.addEventListener("keydown", async (e) => {
    if (e.key === "Escape") { e.stopPropagation(); done(); }
    if (e.key === "Enter") {
      e.preventDefault();
      const v = t.value.trim();
      host._editing = false;
      if (v) await create({ ...ctx, subject: v }); else done();
    }
  });
  t.addEventListener("blur", () => setTimeout(() => { if (host._editing && !t.value.trim()) done(); }, 120));
}

// ============ Search ============
export function searchIndex(q, { limit = 50 } = {}) {
  const terms = q.toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return { tasks: [], projects: [], people: [] };
  const has = (s) => terms.every((t) => s.includes(t));
  const projects = [...S.projects.values()].filter((p) => has(p.name.toLowerCase()));
  const people = [...S.users.values()].filter((u) => u.name !== "OpenProject Admin" && has(u.name.toLowerCase()));
  const tasks = [];
  for (const r of allRows()) {
    if (isSection(r)) continue;
    const hay = `#${r.id} ${r.subject} ${r.project} ${r.assignee || ""} ${r.status}`.toLowerCase();
    if (has(hay)) tasks.push(r);
  }
  const score = (r) => (r.done ? 1 : 0);
  tasks.sort((a, b) => score(a) - score(b) || (b.updated_at || "").localeCompare(a.updated_at || ""));
  return { tasks: tasks.slice(0, limit), total: tasks.length, projects, people };
}

export function viewSearch(el, q) {
  const query = q.get("q") || "";
  const res = searchIndex(query, { limit: 400 });
  frame(el, `search:${query}`, `<div class="vhead"><div class="vtitle"><span class="ptile-ic big">${ICON.search}</span><div><h1>Search</h1><div class="muted" id="vsub"></div></div></div></div><div class="vbody" id="sres"></div>`);
  $("#vsub", el).textContent = query ? `${res.total || 0} tasks, ${res.projects.length} projects, ${res.people.length} people match “${query}”` : "Type in the search box above.";
  const parts = [];
  if (res.projects.length || res.people.length) {
    parts.push(`<div class="sgrid">`);
    for (const p of res.projects) parts.push(`<a class="sitem" href="${href(`/project/${p.id}`)}" data-nav><span class="stype">Project</span><span class="ptile-ic" style="--h:${hue(p.name)}">${ICON.list}</span><b>${esc(p.name)}</b>${p.active && !S.archived.has(p.id) ? "" : `<span class="muted">archived</span>`}</a>`);
    for (const u of res.people) parts.push(`<a class="sitem" href="${href(`/people/${u.id}`)}" data-nav><span class="stype">Person</span>${avatar(u.name, "sm")}<b>${esc(u.name)}</b></a>`);
    parts.push(`</div>`);
  }
  const host = h(`<div></div>`);
  $("#sres", el).innerHTML = parts.join("");
  $("#sres", el).appendChild(host);
  renderList(host, {
    scope: "search", tree: true, crumbs: true, visible: () => true,
    groups: [{ key: "tasks", title: `Tasks${res.total > res.tasks.length ? ` (first ${res.tasks.length})` : ""}`, ids: res.tasks.map((r) => r.id) }],
    cols: ["assignee", "due", "project", "status"],
    empty: query ? "No tasks match." : "",
  });
}

// ============ Archived projects ============
export function viewArchived(el) {
  frame(el, "archived", `<div class="vhead"><div class="vtitle"><span class="ptile-ic big">${ICON.archive}</span><div><h1>Archived projects</h1><div class="muted">Finished projects, out of your everyday views. Nothing is deleted.</div></div></div></div><div class="vbody" id="arch"></div>`);
  const mine = archivedProjects(), inOP = archivedInOP();
  const openCount = (pid) => allRows().filter((r) => r.project_id === pid && tasksOnly(r) && !r.done).length;
  $("#arch", el).innerHTML = `
    <div class="card arch-card"><div class="ptiles">${mine.map((p) => `
      <a class="ptile" href="${href(`/project/${p.id}`)}" data-nav style="--h:${hue(p.name)}">
        <span class="ptile-ic">${ICON.list}</span><span class="ptile-name">${esc(p.name)}</span><span class="ptile-meta">${openCount(p.id)} open</span>
      </a>`).join("") || `<div class="muted arch-none">Nothing here yet. Use the Archive button on a project's page.</div>`}</div></div>
    ${inOP.length ? `<div class="card arch-card"><div class="card-head"><h2>Archived in OpenProject</h2></div>
      <p class="card-note">Archived inside OpenProject itself, which hides their tasks from biscuits. To get one back, unarchive it in OpenProject.</p>
      <ul class="arch-list">${inOP.map((p) => `<li>${esc(p.name)}</li>`).join("")}</ul></div>` : ""}`;
}

// ============ Write log ============
export async function viewLog(el) {
  frame(el, "log", `<div class="vhead"><div class="vtitle"><span class="ptile-ic big">${ICON.log}</span><div><h1>Write log</h1><div class="muted">${S.account?.is_admin ? "Every change biscuits made or would have made to OpenProject, newest first." : "Your changes through biscuits, newest first."}</div></div></div>
    <div class="vtools"><button class="btn sm" id="log-reload">${ICON.refresh} Reload</button></div></div><div class="vbody" id="log"></div>`);
  $("#log-reload", el).onclick = () => viewLog(el);
  const box = $("#log", el);
  try {
    const { entries, writes } = await writeLog();
    const allowed = writes.all_projects ? "all projects" : writes.write_projects.map((id) => projectName(id) || `#${id}`).join(", ") || "none";
    box.innerHTML = `<div class="card log-gate">Mode: <b>${esc(writes.mode)}</b>. Write-enabled projects: <b>${esc(allowed)}</b>. Changes anywhere else are previews and are never sent.</div>
      <div class="card"><table class="log"><thead><tr><th>When</th><th>What</th><th>Result</th><th>Request</th></tr></thead><tbody>
      ${entries.map((e) => `<tr><td>${esc(e.ts.replace("T", " "))}${e.actor?.username ? `<div class="muted">${esc(e.actor.username)}</div>` : ""}</td><td><b>${esc(e.op)}</b> ${e.wp ? `<a href="#" data-open="${e.wp}">#${e.wp}</a>` : ""}<div class="muted">${esc(e.subject_before || "")}</div></td>
        <td>${e.sent ? (e.ok === false ? `<span class="pill err">failed</span><div class="muted">${esc(e.error || "")}</div>` : `<span class="pill ok">sent</span>`) : `<span class="pill warn">preview</span><div class="muted">${esc(e.reason)}</div>`}</td>
        <td><code>${esc(e.method)} ${esc(e.path)}</code><details><summary>body</summary><pre>${esc(JSON.stringify(e.body, null, 2))}</pre></details></td></tr>`).join("")}
      </tbody></table></div>`;
    box.querySelectorAll("[data-open]").forEach((a) => a.addEventListener("click", (ev) => { ev.preventDefault(); openTask(+a.dataset.open); }));
  } catch (e) {
    box.innerHTML = `<div class="error-box">Couldn't load the log: ${esc(e.message)}</div>`;
  }
}
