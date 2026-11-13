// Bulk edit (#3506): change several tasks at once. Reached from the selection bar
// at /bulk?ids=1,2,3&from=<where to go back to>.
//
// Each field shows the value the tasks share, or ‹multiple values›. Only fields
// you change are sent, and only to tasks whose value differs. "Review changes"
// checks every task first (permissions, dates, project members), sums up what
// will happen, and nothing is saved until you confirm.

import { $, esc, h, hue, shortDate, ICON } from "./util.js";
import { S, row, isSection, sectionsOf, sectionTitle, liveProject, projectName, bulkUpdate } from "./store.js";
import { picked, pickedRows, setPicks, clearPicks } from "./select.js";
import { usersForPicker } from "./list.js";
import { go, setParam } from "./nav.js";
import { toast } from "./ui.js";

const MIXED = "__mixed";   // the tasks don't share a value
const NONE = "";           // no value: unassigned, no date, no section

const plural = (n, one, many = one + "s") => `${n} ${n === 1 ? one : many}`;
const tasksN = (n) => plural(n, "task");

// ---------- fields ----------
// value(r) gives a task's current value as a string, so values compare simply.
// toChange(v) turns a picked value back into what the server takes.
const num = (v) => (v === NONE ? null : +v);
const date = (v) => v || null;
const sectionOf = (r) => {
  const p = row(r.parent_id);
  if (!p) return NONE;
  return isSection(p) ? String(p.id) : `sub:${p.id}`;
};

function fieldDefs(rows) {
  const pids = [...new Set(rows.map((r) => r.project_id))];
  const onePid = pids.length === 1 ? pids[0] : null;
  return [
    { key: "status_id", label: "Status", type: "select", value: (r) => String(r.status_id ?? NONE), toChange: num,
      options: () => [...S.statuses.values()].sort((a, b) => a.position - b.position).map((s) => [String(s.id), s.name]) },
    { key: "assignee_id", label: "Assignee", type: "select", value: (r) => String(r.assignee_id ?? NONE), toChange: num,
      options: () => [[NONE, "Unassigned"], ...assignable(pids).map((u) => [String(u.id), u.name])] },
    { key: "priority_id", label: "Priority", type: "select", value: (r) => String(r.priority_id ?? NONE), toChange: num,
      options: () => S.priorities.map((p) => [String(p.id), p.name]) },
    { key: "start", label: "Start date", type: "date", value: (r) => r.own_start || NONE, toChange: date },
    { key: "due", label: "Due date", type: "date", value: (r) => r.own_due || r.due || NONE, toChange: date },
    { key: "parent_id", label: "Section", type: "select", value: sectionOf, toChange: num,
      off: onePid ? null : "These tasks are in different projects, so they can't be moved to one section.",
      options: () => onePid ? [[NONE, "No section"], ...sectionsOf(onePid).filter((s) => !s.done).map((s) => [String(s.id), sectionTitle(s)])] : [] },
  ];
}

// Who can be assigned: the members of every project involved (OpenProject only
// takes a project's members). Filled in once the member lists arrive.
const members = new Map();   // project id -> Set of user ids, or null if unknown
function assignable(pids) {
  const lists = pids.map((p) => members.get(p));
  const users = [...S.users.values()].filter((u) => u.status !== "locked" && u.name !== "OpenProject Admin");
  return users.filter((u) => lists.every((m) => !m || m.has(u.id)))
    .sort((a, b) => (b.id === S.meId) - (a.id === S.meId) || a.name.localeCompare(b.name));
}
async function loadMembers(pids) {
  await Promise.all(pids.filter((p) => !members.has(p)).map(async (p) => {
    const users = await usersForPicker(p).catch(() => null);
    members.set(p, users ? new Set(users.map((u) => u.id)) : null);
  }));
}

function label(f, v) {
  if (v === MIXED) return "‹multiple values›";
  if (f.key === "status_id") return (S.statuses.get(+v) || {}).name || "—";
  if (f.key === "assignee_id") return v === NONE ? "Unassigned" : (S.users.get(+v) || {}).name || `User #${v}`;
  if (f.key === "priority_id") return (S.priorities.find((p) => p.id === +v) || {}).name || "—";
  if (f.type === "date") return v === NONE ? "no date" : shortDate(v);
  if (f.key === "parent_id") {
    if (v === NONE) return "No section";
    if (v.startsWith("sub:")) return `Subtask of “${(row(v.slice(4)) || {}).subject || "#" + v.slice(4)}”`;
    const s = row(v);
    return s ? `“${sectionTitle(s)}”` : `#${v}`;
  }
  return v;
}

// ---------- state ----------
// One screen at a time: st.edits holds {key: value} for the fields changed so far.
let st = null;

export function viewBulk(el, q) {
  const from = q.get("from") || "/home";
  if (!st || st.el !== el || el.dataset.key !== "bulk") st = { el, from, edits: {}, phase: "edit" };
  st.from = from;
  el.dataset.key = "bulk";
  paint();
  loadMembers([...new Set(pickedRows().map((r) => r.project_id))]).then(() => { if (st && st.phase === "edit") paint(); });
}

function rowsNow() { return pickedRows(); }

function shared(f, rows) {
  const vals = new Set(rows.map(f.value));
  return vals.size === 1 ? [...vals][0] : MIXED;
}

function paint() {
  const { el } = st;
  const rows = rowsNow();
  const back = `<button class="btn sm" type="button" data-act="cancel">${ICON.x} ${st.phase === "done" ? "Close" : "Cancel"}</button>`;
  const pids = [...new Set(rows.map((r) => r.project_id))];
  el.innerHTML = `
    <div class="vhead"><div class="vtitle"><span class="ptile-ic big">${ICON.list}</span><div><h1>Bulk edit</h1>
      <div class="muted">${rows.length ? `${tasksN(rows.length)} in ${plural(pids.length, "project")}` : "Nothing selected"}</div></div></div>
      <div class="vtools">${back}</div></div>
    <div class="vbody bulk" id="bulk"></div>`;
  const body = $("#bulk", el);
  el.querySelector("[data-act=cancel]").addEventListener("click", leave);
  if (!rows.length) {
    body.innerHTML = `<div class="card bulk-empty">No tasks are selected. Go back, pick tasks with their checkboxes or Ctrl/Cmd-click, then choose <b>Bulk edit</b>.</div>`;
    return;
  }
  body.appendChild(taskList(rows));
  if (st.phase === "edit") body.appendChild(form(rows));
  else if (st.phase === "review") body.appendChild(review(rows));
  else body.appendChild(results());
}

// Back to where the selection was made. The selection comes along: whatever is
// still picked (nothing after a successful save) stays picked there.
function leave() {
  const to = st.from;
  st = null;
  go(to.split("?")[0], Object.fromEntries(new URLSearchParams(to.split("?")[1] || "")));
}

// ---------- the selected tasks ----------
function taskList(rows) {
  const can = st.phase === "edit";
  const box = h(`<div class="card bulk-tasks"><div class="card-head"><h2>${tasksN(rows.length)}</h2>${can ? `<span class="muted">Remove any you don't want to change.</span>` : ""}</div>
    <ul>${rows.map((r) => `<li data-id="${r.id}">
      <span class="tl-num">#${r.id}</span><span class="bt-title">${esc(r.subject)}</span>
      <span class="ppill" style="--h:${hue(r.project || "")}">${esc(r.project || "")}</span>
      ${liveProject(r.project_id) ? "" : `<span class="badge-preview" title="Saving to OpenProject is off for this project">preview</span>`}
      ${can ? `<button class="icon-btn bt-x" type="button" title="Leave this task out" aria-label="Leave out #${r.id}">${ICON.x}</button>` : ""}</li>`).join("")}</ul></div>`);
  box.addEventListener("click", (e) => {
    const x = e.target.closest(".bt-x");
    if (!x) return;
    const id = +x.closest("li").dataset.id;
    setPicks([...picked].filter((p) => p !== id));
    setParam("ids", [...picked].join(","));
    paint();
  });
  return box;
}

// ---------- editing ----------
function form(rows) {
  const defs = fieldDefs(rows);
  const card = h(`<div class="card bulk-form"><div class="card-head"><h2>Change</h2><span class="muted">Fields you leave alone stay as they are on each task.</span></div><div class="bf-grid"></div>
    <div class="bf-foot"><button class="btn primary" type="button" data-act="review" disabled>Review changes</button><span class="muted bf-note"></span></div></div>`);
  const grid = card.querySelector(".bf-grid");
  for (const f of defs) grid.appendChild(fieldRow(f, rows, () => paintFoot(card, defs, rows)));
  card.querySelector("[data-act=review]").addEventListener("click", () => { st.phase = "review"; paint(); });
  paintFoot(card, defs, rows);
  return card;
}

function fieldRow(f, rows, onChange) {
  const cur = shared(f, rows);
  const edited = f.key in st.edits;
  const wrap = h(`<div class="bf-row ${edited ? "edited" : ""}" data-key="${f.key}"><label class="bf-label" for="bf-${f.key}">${f.label}</label><div class="bf-ctl"></div><div class="bf-hint"></div></div>`);
  const ctl = wrap.querySelector(".bf-ctl"), hint = wrap.querySelector(".bf-hint");
  if (f.off) {
    ctl.innerHTML = `<span class="muted">${esc(label(f, cur))}</span>`;
    hint.textContent = f.off;
    return wrap;
  }
  const value = () => (f.key in st.edits ? st.edits[f.key] : cur);
  const set = (v) => {
    if (v === cur) delete st.edits[f.key]; else st.edits[f.key] = v;
    wrap.classList.toggle("edited", f.key in st.edits);
    paintHint();
    onChange();
  };
  const paintHint = () => {
    if (!(f.key in st.edits)) {
      hint.innerHTML = cur === MIXED ? `<span class="mixed">These tasks have different values.</span>` : "";
      return;
    }
    const n = rows.filter((r) => f.value(r) !== st.edits[f.key]).length;
    hint.innerHTML = `${n ? `Changes ${tasksN(n)}` : "No task changes: they all have this already"} <button class="linkish bf-reset" type="button">Reset</button>`;
    hint.querySelector(".bf-reset").addEventListener("click", () => { delete st.edits[f.key]; const fresh = fieldRow(f, rows, onChange); wrap.replaceWith(fresh); onChange(); });
  };
  if (f.type === "select") {
    const opts = f.options();
    if (cur !== MIXED && !opts.some(([v]) => v === cur)) opts.unshift([cur, label(f, cur)]);  // e.g. a locked assignee
    const sel = h(`<select id="bf-${f.key}">${cur === MIXED ? `<option value="${MIXED}">‹multiple values›</option>` : ""}${opts.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("")}</select>`);
    sel.value = value();
    sel.addEventListener("change", () => set(sel.value));
    ctl.appendChild(sel);
  } else {
    const v = value();
    const inp = h(`<input type="date" id="bf-${f.key}" value="${v === MIXED ? "" : esc(v)}">`);
    const mixedTag = h(`<span class="mixed-tag">‹multiple values›</span>`);
    const clear = h(`<button class="btn sm ghost" type="button">Clear date</button>`);
    const showTag = () => { mixedTag.hidden = !(value() === MIXED); };
    inp.addEventListener("change", () => { set(inp.value || NONE); showTag(); });
    clear.addEventListener("click", () => { inp.value = ""; set(NONE); showTag(); });
    ctl.append(inp, mixedTag, clear);
    showTag();
  }
  paintHint();
  return wrap;
}

function paintFoot(card, defs, rows) {
  const p = plan(rows, defs);
  const btn = card.querySelector("[data-act=review]");
  btn.disabled = !Object.keys(st.edits).length;
  card.querySelector(".bf-note").textContent = Object.keys(st.edits).length
    ? `${tasksN(p.items.length)} will change${p.skipped.length ? `, ${plural(p.skipped.length, "can't", "can't")}` : ""}.`
    : "Change a field to begin.";
}

// ---------- the plan and its checks ----------
function plan(rows, defs = fieldDefs(rows)) {
  const edited = defs.filter((f) => f.key in st.edits && !f.off);
  const ids = new Set(rows.map((r) => r.id));
  const out = { items: [], skipped: [], same: [], notes: [], perField: new Map(edited.map((f) => [f.key, { change: 0, same: 0 }])) };
  const unsubbed = [];
  for (const r of rows) {
    const changes = {}, shown = [];
    for (const f of edited) {
      const now = f.value(r), want = st.edits[f.key];
      if (f.key === "parent_id" && carried(r, ids)) continue;   // moves with its picked parent
      if (now === want) { out.perField.get(f.key).same++; continue; }
      changes[f.key] = f.toChange(want);
      shown.push([f, now, want]);
      out.perField.get(f.key).change++;
    }
    if (!shown.length) { out.same.push(r); continue; }
    const why = problem(r, changes);
    if (why) { out.skipped.push({ r, why }); for (const [f] of shown) out.perField.get(f.key).change--; continue; }
    if ("parent_id" in changes && sectionOf(r).startsWith("sub:")) unsubbed.push(r);
    out.items.push({ r, changes, shown });
  }
  if (unsubbed.length) out.notes.push(`${plural(unsubbed.length, "subtask")} will move out of ${unsubbed.length === 1 ? "its parent task" : "their parent tasks"} and into the section.`);
  const preview = out.items.filter((it) => !liveProject(it.r.project_id)).length;
  if (preview) out.notes.push(`${tasksN(preview)} ${preview === 1 ? "is" : "are"} in a project where saving to OpenProject is off, so ${preview === 1 ? "its change stays" : "their changes stay"} in your browser as a preview.`);
  return out;
}

// A picked task whose parent (or grandparent…) is also picked goes wherever that parent goes.
function carried(r, ids) {
  for (let p = row(r.parent_id), n = 0; p && n < 20; p = row(p.parent_id), n++) if (ids.has(p.id)) return true;
  return false;
}

function problem(r, ch) {
  if (r.can_edit === false) return "You don't have permission to edit this task in OpenProject.";
  const start = "start" in ch ? ch.start : r.own_start || null;
  const due = "due" in ch ? ch.due : r.own_due || r.due || null;
  if (start && due && start > due) return `Its start date (${shortDate(start)}) would be after its due date (${shortDate(due)}).`;
  if (ch.assignee_id) {
    const m = members.get(r.project_id);
    if (m && !m.has(ch.assignee_id)) return `${(S.users.get(ch.assignee_id) || {}).name || "That person"} isn't a member of ${projectName(r.project_id)}.`;
  }
  if ("parent_id" in ch && ch.parent_id === r.id) return "A task can't be its own section.";
  return null;
}

// ---------- review and confirm ----------
function review(rows) {
  const defs = fieldDefs(rows);
  const p = plan(rows, defs);
  const lines = defs.filter((f) => f.key in st.edits && !f.off).map((f) => {
    const c = p.perField.get(f.key);
    return `<li><b>${f.label}</b> → ${esc(label(f, st.edits[f.key]))}: ${c.change ? tasksN(c.change) : "no tasks"}${c.same ? ` <span class="muted">(${c.same} already ${c.same === 1 ? "has" : "have"} it)</span>` : ""}</li>`;
  }).join("");
  const card = h(`<div class="card bulk-review">
    <div class="card-head"><h2>${p.items.length ? `Ready to change ${tasksN(p.items.length)}` : "Nothing to change"}</h2></div>
    <ul class="br-lines">${lines}</ul>
    ${p.notes.map((n) => `<p class="br-note">${esc(n)}</p>`).join("")}
    ${p.skipped.length ? `<div class="br-skip"><h3>${plural(p.skipped.length, "task")} won't be changed</h3><ul>${p.skipped.map(({ r, why }) => `<li><span class="tl-num">#${r.id}</span> ${esc(r.subject)}: <span class="muted">${esc(why)}</span></li>`).join("")}</ul></div>` : ""}
    ${p.same.length ? `<p class="muted">${tasksN(p.same.length)} already ${p.same.length === 1 ? "has" : "have"} every value, so ${p.same.length === 1 ? "it's" : "they're"} left alone.</p>` : ""}
    ${p.items.length ? `<details class="br-detail"><summary>Every change</summary><table><thead><tr><th>Task</th><th>Field</th><th>Now</th><th>After</th></tr></thead><tbody>
      ${p.items.map(({ r, shown }) => shown.map(([f, now, want], i) => `<tr>${i ? "<td></td>" : `<td><span class="tl-num">#${r.id}</span> ${esc(r.subject)}</td>`}<td>${f.label}</td><td>${esc(label(f, now))}</td><td>${esc(label(f, want))}</td></tr>`).join("")).join("")}
    </tbody></table></details>` : ""}
    <div class="bf-foot"><button class="btn" type="button" data-act="edit">Back to editing</button>
      ${p.items.length ? `<button class="btn primary" type="button" data-act="apply">Change ${tasksN(p.items.length)}</button>` : ""}</div></div>`);
  card.querySelector("[data-act=edit]").addEventListener("click", () => { st.phase = "edit"; paint(); });
  const go_ = card.querySelector("[data-act=apply]");
  if (go_) go_.addEventListener("click", () => apply(p, go_));
  return card;
}

async function apply(p, btn) {
  btn.disabled = true;
  btn.textContent = `Saving ${tasksN(p.items.length)}…`;
  st.phase = "saving";
  const res = await bulkUpdate(p.items.map(({ r, changes }) => ({ id: r.id, changes })), {
    undoLabel: (n) => `Changed ${tasksN(n)}`,
  });
  if (!st) return;   // left the screen while saving
  if (!res.failed.length) {
    clearPicks();
    st.phase = "done";
    return leave();
  }
  st.phase = "done";
  st.result = res;
  const ok = res.saved.length + res.preview.length;
  if (ok) toast(`${ok} of ${ok + res.failed.length} tasks changed; ${res.failed.length} didn't save.`, { kind: "warn", ms: 8000 });
  // Keep the ones that failed selected, to try again.
  setPicks(res.failed.map((f) => f.id));
  setParam("ids", [...picked].join(","));
  paint();
}

function results() {
  const { saved, preview, failed } = st.result;
  const ok = saved.length + preview.length;
  const card = h(`<div class="card bulk-review">
    <div class="card-head"><h2>${ok ? `Changed ${ok} of ${ok + failed.length} tasks` : "No tasks were changed"}</h2></div>
    <div class="br-skip"><h3>${plural(failed.length, "task")} didn't save</h3><ul>${failed.map(({ id, error }) => `<li><span class="tl-num">#${id}</span> ${esc((row(id) || {}).subject || "")}: <span class="muted">${esc(error || "Unknown error")}</span></li>`).join("")}</ul></div>
    <p class="muted">The tasks that didn't save are still selected, so you can try them again.</p>
    <div class="bf-foot"><button class="btn primary" type="button" data-act="retry">Edit these again</button></div></div>`);
  card.querySelector("[data-act=retry]").addEventListener("click", () => { st.phase = "edit"; st.edits = {}; paint(); });
  return card;
}
