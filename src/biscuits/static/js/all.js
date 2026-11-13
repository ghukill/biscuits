// "All tasks": the original power-user tree table (Tabulator), now wired to
// the task pane and the complete button. Flat tree across all projects,
// header filters that keep a row if any descendant matches.

import { $, esc, avatar, friendlyDate, dueClass, hue, T, RANGES, inRange, ICON } from "./util.js";
import { S, workingRows, row, toggleComplete } from "./store.js";
import { openTask, currentTask } from "./nav.js";
import { pickFor } from "./list.js";
import { pickBox, pickClick, isPicked } from "./select.js";

const DUE_WINDOWS = {
  "Overdue": (r) => !!r.due && r.due < T && !r.done,
  "Today": (r) => r.due === T,
  "This week": (r) => inRange(r.due, RANGES.week),
  "Next week": (r) => inRange(r.due, RANGES.nextWeek),
  "This month": (r) => inRange(r.due, RANGES.month),
  "Next month": (r) => inRange(r.due, RANGES.nextMonth),
  "No due date": (r) => !r.due,
};
const UNASSIGNED = "— unassigned —";
const PICKS = { assignee: "assignee", due: "due", status: "status", priority: "priority" };

const deep = (test) => {
  const f = (hv, _rv, rd) => test(hv, rd) || (rd._children || []).some((c) => f(hv, null, c));
  return f;
};
const isEmpty = (hv) => hv == null || hv === "" || (Array.isArray(hv) && hv.length === 0);
const textFilter = (field) => deep((hv, rd) => String(rd[field] ?? "").toLowerCase().includes(String(hv).toLowerCase()));
const listFilter = (field, blank) => deep((hv, rd) => isEmpty(hv) || (Array.isArray(hv) ? hv : [hv]).includes(rd[field] ?? blank));
const dueFilter = deep((hv, rd) => isEmpty(hv) || (Array.isArray(hv) ? hv : [hv]).some((k) => DUE_WINDOWS[k] && DUE_WINDOWS[k](rd)));

let table = null;
let host = null;

const copyRow = (r) => { const { _children, ...rest } = r; return { ...rest }; };
function buildTree() {
  const byId = new Map(workingRows().map((r) => [r.id, copyRow(r)]));
  const roots = [];
  for (const r of byId.values()) {
    const p = r.parent_id && byId.get(r.parent_id);
    if (p) (p._children ||= []).push(r); else roots.push(r);
  }
  return roots;
}
const distinct = (field, blank) => [...new Set(workingRows().map((r) => r[field] ?? blank))].filter((v) => v != null).sort((a, b) => String(a).localeCompare(String(b)));

const fmt = {
  pick: (c) => { const r = row(c.getRow().getData().id); return r ? pickBox(r) : ""; },
  check: (c) => { const r = c.getRow().getData(); return r.section != null ? "" : `<button class="check ${r.done ? "on" : ""}" type="button">${ICON.check}</button>`; },
  subject: (c) => {
    const r = c.getRow().getData();
    const n = (r._children || []).length;
    return `<span class="subject ${r.section != null ? "is-section" : ""}" title="${esc(r.subject)}">${esc(r.section != null ? r.section : r.subject)}</span>` +
      (n ? `<span class="subcount">${n}${ICON.sub}</span>` : "") + `<span class="details">Details ${ICON.caret}</span>`;
  },
  status: (c) => { const r = c.getRow().getData(); return r.status ? `<span class="pill status" style="--c:${esc(r.status_color || "#adb5bd")}">${esc(r.status)}</span>` : ""; },
  priority: (c) => { const v = c.getValue(); return v && v !== "Normal" ? `<span class="pill prio-${esc(v)}">${esc(v)}</span>` : ""; },
  person: (c) => { const v = c.getValue(); return `<span class="person">${avatar(v, "sm")}${v ? esc(v) : ""}</span>`; },
  date: (c) => { const v = c.getValue(); return v ? `<span class="date">${esc(friendlyDate(v))}</span>` : `<span class="nobody">—</span>`; },
  due: (c) => { const r = c.getRow().getData(); return r.due ? `<span class="c-due ${dueClass(r)}">${esc(friendlyDate(r.due))}</span>` : `<span class="nobody">—</span>`; },
  id: (c) => `<a class="wp-id" href="${esc(S.snap.op_url)}/work_packages/${c.getValue()}" target="_blank" rel="noopener" title="Open in OpenProject">#${c.getValue()}</a>`,
  project: (c) => `<span class="ppill" style="--h:${hue(c.getValue() || "")}">${esc(c.getValue() || "")}</span>`,
};

function columns() {
  const list = (field, blank) => ({
    headerFilter: "list", headerFilterParams: { values: distinct(field, blank), multiselect: true, clearable: true },
    headerFilterFunc: listFilter(field, blank), headerFilterPlaceholder: "any",
  });
  const statusPos = Object.fromEntries([...S.statuses.values()].map((s) => [s.id, s.position]));
  const nullsLast = { sorterParams: { alignEmptyValues: "bottom" } };
  return [
    { title: "", field: "_pick", width: 34, headerSort: false, formatter: fmt.pick, cssClass: "col-pick", resizable: false },
    { title: "", field: "done", width: 44, headerSort: false, formatter: fmt.check, cssClass: "col-check", resizable: false },
    { title: "Task name", field: "subject", minWidth: 320, widthGrow: 4, formatter: fmt.subject, headerFilter: "input", headerFilterFunc: textFilter("subject"), headerFilterPlaceholder: "filter…" },
    { title: "Project", field: "project", minWidth: 150, widthGrow: 1.3, formatter: fmt.project, ...list("project") },
    { title: "Assignee", field: "assignee", minWidth: 160, widthGrow: 1, formatter: fmt.person, ...list("assignee", UNASSIGNED), ...nullsLast, cssClass: "pickable" },
    { title: "Due", field: "due", width: 120, formatter: fmt.due, ...nullsLast, headerFilter: "list",
      headerFilterParams: { values: Object.keys(DUE_WINDOWS), multiselect: true, clearable: true }, headerFilterFunc: dueFilter, headerFilterPlaceholder: "any", cssClass: "pickable" },
    { title: "Status", field: "status", width: 150, formatter: fmt.status, ...list("status"), cssClass: "pickable",
      sorter: (a, b, ra, rb) => (statusPos[ra.getData().status_id] ?? 99) - (statusPos[rb.getData().status_id] ?? 99) },
    { title: "Priority", field: "priority", width: 110, formatter: fmt.priority, ...list("priority"), cssClass: "pickable",
      sorter: (a, b, ra, rb) => (ra.getData().priority_pos ?? 0) - (rb.getData().priority_pos ?? 0) },
    { title: "Accountable", field: "responsible", minWidth: 150, visible: false, formatter: fmt.person, ...list("responsible", UNASSIGNED), ...nullsLast },
    { title: "Created by", field: "author", minWidth: 150, visible: false, formatter: fmt.person, ...list("author"), ...nullsLast },
    { title: "Start", field: "start", width: 110, formatter: fmt.date, visible: false, ...nullsLast },
    { title: "Updated", field: "updated", width: 110, formatter: fmt.date, visible: false },
    { title: "Created", field: "created", width: 110, formatter: fmt.date, visible: false },
    { title: "#", field: "id", width: 80, sorter: "number", formatter: fmt.id, headerFilter: "input", headerFilterFunc: textFilter("id"), headerFilterPlaceholder: "id" },
  ];
}

function walk(rows, fn) { for (const r of rows) { fn(r); walk(r.getTreeChildren(), fn); } }
// The ids in the order they're shown, including rows scrolled out of view, for
// Shift-click ranges. Collapsed subtasks aren't shown, so they aren't included.
function shownOrder() {
  const out = [];
  const visit = (rows) => { for (const r of rows) { out.push(r.getData().id); if (r.isTreeExpanded()) visit(r.getTreeChildren()); } };
  visit(table.getRows("active"));
  return out;
}
function setExpanded(open) {
  table.blockRedraw();
  try {
    walk(table.getRows("active"), (r) => {
      if (!r.getTreeChildren().length) return;
      if (open && !r.isTreeExpanded()) r.treeExpand();
      if (!open && r.isTreeExpanded()) r.treeCollapse();
    });
  } finally { table.restoreRedraw(); }
}

const searchable = (r) => [r.id, r.subject, r.project, r.assignee, r.responsible, r.status, r.type].join(" \u0001 ").toLowerCase();
const quick = deep((terms, rd) => terms.every((t) => searchable(rd).includes(t)));
const notDone = (rd) => !rd.done || (rd._children || []).some(notDone);

function applyFilters() {
  const terms = $("#all-q").value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const hideDone = !$("#all-done").checked;
  localStorage.setItem("biscuits.all.showDone", hideDone ? "0" : "1");
  if (!terms.length && !hideDone) table.clearFilter(false);
  else table.setFilter((rd) => (!hideDone || notDone(rd)) && (!terms.length || quick(terms, null, rd)));
}
const filtersActive = () => !!$("#all-q").value.trim() || table.getHeaderFilters().length > 0;

export function viewAll(el) {
  if (el.dataset.key === "all" && table) return;
  el.dataset.key = "all";
  el.innerHTML = `
    <div class="vhead"><div class="vtitle"><span class="ptile-ic big">${ICON.list}</span><div><h1>All tasks</h1><div class="muted" id="all-count"></div></div></div>
      <div class="vtools">
        <label class="mini-search">${ICON.search}<input id="all-q" placeholder="Filter this table…" autocomplete="off"></label>
        <label class="toggle"><input type="checkbox" id="all-done"> Show finished</label>
        <button class="btn sm" id="all-exp">${ICON.down} Expand</button>
        <button class="btn sm" id="all-col">${ICON.up} Collapse</button>
        <button class="btn sm" id="all-clear">${ICON.x} Clear</button>
        <div class="popover" id="cols-pop"><button class="btn sm" id="cols-btn">${ICON.dots} Columns</button><div class="popover-panel" id="cols-panel"></div></div>
      </div></div>
    <div class="card table-card"><div id="all-table"></div></div>`;
  host = el;
  $("#all-done").checked = localStorage.getItem("biscuits.all.showDone") === "1";
  table = new Tabulator("#all-table", {
    data: buildTree(), columns: columns(), layout: "fitColumns", height: "calc(100vh - 190px)", index: "id",
    dataTree: true, dataTreeStartExpanded: false, dataTreeElementColumn: "subject", dataTreeChildIndent: 18,
    dataTreeSort: true, dataTreeFilter: true, initialSort: [{ column: "due", dir: "asc" }],
    persistence: { sort: true, headerFilter: true, columns: ["width", "visible"] }, persistenceID: "biscuits-all-v1",
    placeholder: "Nothing matches. Try loosening a filter.",
    rowFormatter: (r) => {
      const d = r.getData(), e = r.getElement();
      e.classList.toggle("done", !!d.done);
      e.classList.toggle("tree-child", !!r.getTreeParent());
      e.classList.toggle("sel", d.id === currentTask());
      e.classList.toggle("picked", isPicked(d.id));
      e.dataset.id = d.id;
      e.classList.toggle("section-row", d.section != null);
    },
  });
  table.on("cellClick", (e, cell) => {
    const d = cell.getRow().getData();
    if (e.target.closest("a, .tabulator-data-tree-control")) return;
    if (pickClick(e, d.id, shownOrder)) return;
    if (e.target.closest(".check")) return toggleComplete(d.id);
    const pick = PICKS[cell.getColumn().getField()];
    const r = row(d.id);
    if (pick && r && d.section == null) return pickFor(pick, cell.getElement(), r);
    openTask(d.id);
  });
  let was = false;
  const onFiltered = () => {
    const now = filtersActive();
    if (now) setExpanded(true); else if (was) setExpanded(false);
    was = now;
    count();
  };
  table.on("tableBuilt", () => { buildColumnsMenu(); applyFilters(); count(); });
  table.on("dataFiltered", () => setTimeout(onFiltered, 0));
  let t;
  $("#all-q").addEventListener("input", () => { clearTimeout(t); t = setTimeout(applyFilters, 150); });
  $("#all-done").addEventListener("change", applyFilters);
  $("#all-exp").onclick = () => setExpanded(true);
  $("#all-col").onclick = () => setExpanded(false);
  $("#all-clear").onclick = () => { $("#all-q").value = ""; table.clearHeaderFilter(); applyFilters(); };
}

function count() {
  if (!table) return;
  const n = workingRows().length;
  $("#all-count").textContent = `${n} work packages · ${table.getDataCount("active")} top-level rows shown`;
}

function buildColumnsMenu() {
  const panel = $("#cols-panel");
  panel.innerHTML = "";
  for (const col of table.getColumns()) {
    const def = col.getDefinition();
    if (def.field === "subject" || def.field === "done") continue;
    const lab = document.createElement("label");
    lab.innerHTML = `<input type="checkbox" ${col.isVisible() ? "checked" : ""}> ${esc(def.title)}`;
    lab.querySelector("input").addEventListener("change", (e) => { e.target.checked ? col.show() : col.hide(); table.redraw(); });
    panel.appendChild(lab);
  }
  $("#cols-btn").onclick = (e) => { e.stopPropagation(); $("#cols-pop").classList.toggle("open"); };
  document.addEventListener("click", (e) => { const p = $("#cols-pop"); if (p && !p.contains(e.target)) p.classList.remove("open"); });
}

// Called by main on store changes while this view is mounted.
export function patchAll(ev) {
  if (!table || !host || host.dataset.key !== "all") return;
  if (ev.type === "reload" || ev.type === "create" || ev.type === "idchange") { table.replaceData(buildTree()); return; }
  const ids = new Set(ev.ids || []);
  let structural = false;
  walk(table.getRows(), (r) => {
    const d = r.getData();
    if (ids.has(d.id)) {
      const src = row(d.id);
      if (!src) return;
      if (src.parent_id !== d.parent_id) structural = true;
      else r.update(copyRow(src));
    }
  });
  if (structural) table.replaceData(buildTree());
}
export function reselectAll() {
  if (!table || !host || host.dataset.key !== "all") return;
  const sel = currentTask();
  walk(table.getRows(), (r) => r.getElement() && r.getElement().classList.toggle("sel", r.getData().id === sel));
}
export function unmountAll() { if (table) { table.destroy(); table = null; } }
