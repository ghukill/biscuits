// Multi-select (#3506, #3497): the tasks picked with Ctrl/Cmd-click, Shift-click or
// a row's checkbox. Shared by the list, the board and All tasks. The bar at the
// bottom of the screen offers Bulk edit, and dragging a picked task moves them all.

import { row, isSection, bulkUpdate } from "./store.js";
import { toast } from "./ui.js";

export const picked = new Set();   // task ids, in the order they were picked
let anchor = null;                 // where a Shift-click range starts
const subs = new Set();

export const onPicks = (fn) => subs.add(fn);
const changed = () => subs.forEach((fn) => fn());

// Unsaved rows (negative ids) and sections can't be bulk edited.
export const pickable = (r) => !!r && r.id > 0 && !isSection(r);
export const isPicked = (id) => picked.has(+id);
export const pickedRows = () => [...picked].map(row).filter(pickable);

export function togglePick(id) {
  id = +id;
  if (!pickable(row(id))) return;
  picked.has(id) ? picked.delete(id) : picked.add(id);
  anchor = id;
  changed();
}

// Shift-click: everything shown between the last pick and this one. `order` is
// the ids as they appear on screen.
export function pickRange(id, order) {
  id = +id;
  const a = order.indexOf(anchor), b = order.indexOf(id);
  if (anchor == null || a < 0 || b < 0) return togglePick(id);
  for (const x of order.slice(Math.min(a, b), Math.max(a, b) + 1)) if (pickable(row(x))) picked.add(x);
  anchor = id;
  changed();
}

export function setPicks(ids) {
  picked.clear();
  for (const id of ids) if (pickable(row(id))) picked.add(+id);
  anchor = null;
  changed();
}

// A deleted task can't stay picked.
export function unpick(ids) {
  let any = false;
  for (const id of ids) any = picked.delete(+id) || any;
  if (any) changed();
}

export function clearPicks() {
  if (!picked.size) return;
  picked.clear();
  anchor = null;
  changed();
}

// Picked tasks whose parent (or grandparent…) is also picked travel with it, so a
// move leaves them where they are under that parent.
export function topmost(rows) {
  const ids = new Set(rows.map((r) => r.id));
  return rows.filter((r) => {
    for (let p = row(r.parent_id), n = 0; p && n < 20; p = row(p.parent_id), n++) if (ids.has(p.id)) return false;
    return true;
  });
}

// A row's own click handler calls this first. True when the click was a pick
// (a modifier key or the checkbox), so the handler should stop there. order()
// gives the ids in the order they're shown, for Shift-click ranges.
export function pickClick(e, id, order) {
  const box = e.target.closest(".pick");
  if (!box && !e.metaKey && !e.ctrlKey && !e.shiftKey) return false;
  e.preventDefault();
  if (e.shiftKey) pickRange(id, order());
  else togglePick(id);
  return true;
}
export const domOrder = (host, selector) => () => [...host.querySelectorAll(selector)].map((el) => +el.dataset.id);

// Is this drag carrying the whole selection? Only when the dragged task is picked
// and there's more than one.
export const dragsPicks = (id) => picked.size > 1 && picked.has(+id);

// Show "N tasks" under the pointer while dragging a selection.
export function dragLabel(e) {
  const tag = document.createElement("div");
  tag.className = "drag-tag";
  tag.textContent = `${picked.size} tasks`;
  document.body.appendChild(tag);
  e.dataTransfer.setDragImage(tag, 12, 12);
  setTimeout(() => tag.remove(), 0);
}

// #3497: drop a selection onto a section (parent = its id) or onto "No section"
// (parent = null) in project `pid`. Only tasks from that project move, and
// subtasks whose parent is also picked stay with it.
export async function movePicked(parent, pid) {
  const all = pickedRows();
  const mine = topmost(all).filter((r) => r.project_id === pid);
  const locked = mine.filter((r) => r.can_edit === false);
  const moving = mine.filter((r) => r.can_edit !== false && r.parent_id !== parent);
  const elsewhere = all.filter((r) => r.project_id !== pid).length;
  const notes = [];
  if (elsewhere) notes.push(`${elsewhere} from other projects stayed put`);
  if (locked.length) notes.push(`${locked.length} you can't edit stayed put`);
  if (!moving.length) {
    if (notes.length) toast(`Nothing moved: ${notes.join("; ")}.`, { kind: "warn" });
    return;
  }
  const where = parent ? `“${(row(parent) || {}).section || "Untitled section"}”` : "No section";
  const out = await bulkUpdate(moving.map((r) => ({ id: r.id, changes: { parent_id: parent } })), {
    undoLabel: (n) => `Moved ${n} task${n === 1 ? "" : "s"} to ${where}${notes.length ? ` (${notes.join("; ")})` : ""}`,
  });
  if (out.failed.length) toast(`${out.failed.length} task${out.failed.length === 1 ? "" : "s"} didn't move: ${out.failed[0].error}`, { kind: "error", ms: 9000 });
}

export const pickBox = (r) => pickable(r)
  ? `<button class="pick ${isPicked(r.id) ? "on" : ""}" type="button" title="Select (or Ctrl/Cmd-click the task)" aria-label="Select task" aria-pressed="${isPicked(r.id)}"><svg viewBox="0 0 24 24"><path d="M7 12.5l3.2 3.2L17 9"/></svg></button>`
  : "";

// Repaint the picked state on whatever rows and cards are showing, without a redraw.
export function paintPicks(root) {
  document.body.classList.toggle("picking", picked.size > 0);
  for (const el of root.querySelectorAll(".tl-row[data-id], .bcard[data-id], .tabulator-row[data-id]")) {
    const on = picked.has(+el.dataset.id);
    el.classList.toggle("picked", on);
    const box = el.querySelector(".pick");
    if (box) { box.classList.toggle("on", on); box.setAttribute("aria-pressed", on); }
  }
}
