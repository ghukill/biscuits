// Shared UI bits: toasts, anchored popovers, and the pickers used by rows,
// the board, and the task pane (person, date, status, priority, project).

import { $, esc, h, avatar, iso, addDays, TODAY, RANGES, longDate } from "./util.js";

// ---------- toasts ----------
export function toast(msg, { action, onAction, kind = "", ms = 5000 } = {}) {
  let host = $("#toasts");
  const el = h(`<div class="toast ${kind}"><span>${esc(msg)}</span>${action ? `<button type="button">${esc(action)}</button>` : ""}<button class="tx" type="button" aria-label="Dismiss">×</button></div>`);
  const close = () => { el.classList.add("out"); setTimeout(() => el.remove(), 200); };
  if (action) el.querySelector("button").addEventListener("click", () => { onAction && onAction(); close(); });
  el.querySelector(".tx").addEventListener("click", close);
  host.appendChild(el);
  setTimeout(close, ms);
}

// ---------- popover ----------
let openPop = null;
export function closePopover() {
  if (openPop) { const p = openPop; openPop = null; p.el.remove(); p.onClose && p.onClose(); }
}
export const popoverOpen = () => !!openPop;

export function popover(anchor, content, { onClose, width } = {}) {
  closePopover();
  const el = h(`<div class="pop" role="dialog"></div>`);
  if (width) el.style.width = width + "px";
  el.appendChild(content);
  document.body.appendChild(el);
  const a = anchor.getBoundingClientRect();
  const w = el.offsetWidth, ht = el.offsetHeight;
  let left = Math.min(a.left, window.innerWidth - w - 12);
  let top = a.bottom + 6;
  if (top + ht > window.innerHeight - 12) top = Math.max(12, a.top - ht - 6);
  el.style.left = Math.max(12, left) + "px";
  el.style.top = top + "px";
  openPop = { el, onClose, anchor };
  setTimeout(() => (el.querySelector("input,textarea,button") || el).focus(), 0);
  return el;
}
document.addEventListener("mousedown", (e) => {
  if (openPop && !openPop.el.contains(e.target) && !openPop.anchor.contains(e.target)) closePopover();
}, true);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && openPop) { e.stopPropagation(); e.preventDefault(); closePopover(); }
}, true);

// ---------- generic searchable menu ----------
// items: [{value, label, html?, hint?}]
export function menu(anchor, items, { onPick, placeholder = "Search…", current, search = true, footer } = {}) {
  const box = h(`<div class="menu">${search ? `<input class="menu-q" placeholder="${esc(placeholder)}" autocomplete="off">` : ""}<div class="menu-list"></div>${footer ? `<div class="menu-foot"></div>` : ""}</div>`);
  const list = box.querySelector(".menu-list");
  const q = box.querySelector(".menu-q");
  let shown = [], idx = 0;
  const paint = () => {
    const t = (q ? q.value : "").trim().toLowerCase();
    shown = items.filter((i) => !t || i.label.toLowerCase().includes(t));
    idx = Math.max(0, Math.min(idx, shown.length - 1));
    list.innerHTML = shown.map((i, n) =>
      `<button type="button" class="menu-item ${n === idx ? "focus" : ""} ${i.value === current ? "current" : ""}" data-n="${n}">${i.html || esc(i.label)}${i.hint ? `<small>${esc(i.hint)}</small>` : ""}</button>`).join("")
      || `<div class="menu-empty">No matches</div>`;
  };
  const pick = (n) => { const it = shown[n]; if (!it) return; closePopover(); onPick(it.value, it); };
  list.addEventListener("click", (e) => { const b = e.target.closest(".menu-item"); if (b) pick(+b.dataset.n); });
  box.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { idx = Math.min(idx + 1, shown.length - 1); paint(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { idx = Math.max(idx - 1, 0); paint(); e.preventDefault(); }
    else if (e.key === "Enter") { pick(idx); e.preventDefault(); }
  });
  if (q) q.addEventListener("input", () => { idx = 0; paint(); });
  if (footer) box.querySelector(".menu-foot").appendChild(footer);
  paint();
  return popover(anchor, box, { width: 280 });
}

// ---------- ask for one line of text ----------
export function askText(anchor, { label, value = "", placeholder = "", action = "Save", maxlength = 255, onSubmit }) {
  const box = h(`<form class="ask"><label>${esc(label)}<input value="${esc(value)}" placeholder="${esc(placeholder)}" maxlength="${maxlength}" autocomplete="off"></label><div class="ask-actions"><button class="btn sm primary">${esc(action)}</button></div></form>`);
  const field = box.querySelector("input");
  box.addEventListener("submit", (e) => {
    e.preventDefault();
    const v = field.value.trim();
    if (!v) return field.focus();
    closePopover();
    onSubmit(v);
  });
  const el = popover(anchor, box, { width: 300 });
  setTimeout(() => field.select(), 0);
  return el;
}

// ---------- specific pickers ----------
export function pickPerson(anchor, users, current, onPick) {
  const items = [
    { value: null, label: "No assignee", html: `${avatar(null, "sm")}<span>No assignee</span>` },
    ...users.map((u) => ({ value: u.id, label: u.name, html: `${avatar(u.name, "sm")}<span>${esc(u.name)}</span>` })),
  ];
  return menu(anchor, items, { onPick, current, placeholder: "Assign to…" });
}

export function pickStatus(anchor, statuses, current, onPick) {
  const items = statuses.map((s) => ({ value: s.id, label: s.name, html: statusPill(s) }));
  return menu(anchor, items, { onPick, current, placeholder: "Status…" });
}

export function pickPriority(anchor, priorities, current, onPick) {
  const items = [...priorities].reverse().map((p) => ({ value: p.id, label: p.name, html: `<span class="pill prio-${esc(p.name)}">${esc(p.name)}</span>` }));
  return menu(anchor, items, { onPick, current, search: false });
}

export function pickProject(anchor, projects, current, onPick, { writable } = {}) {
  const items = projects.map((p) => ({
    value: p.id, label: p.name,
    html: `<span class="pdot" style="--h:${hueOf(p.name)}"></span><span>${esc(p.name)}</span>`,
    hint: writable && writable(p.id) ? "live" : "",
  }));
  return menu(anchor, items, { onPick, current, placeholder: "Project…" });
}
const hueOf = (s) => { let x = 0; for (const c of s) x = (x * 31 + c.charCodeAt(0)) % 360; return x; };

// Asana-like due date picker: quick picks + start/due fields.
export function pickDate(anchor, r, onPick) {
  const quick = [
    ["Today", iso(TODAY)],
    ["Tomorrow", iso(addDays(TODAY, 1))],
    ["Next week", RANGES.nextWeek[0]],
    ["In 2 weeks", iso(addDays(TODAY, 14))],
  ];
  const box = h(`<div class="datepick">
    <div class="dp-quick">${quick.map(([l, v]) => `<button type="button" class="btn sm" data-v="${v}">${l}<small>${longDate(v).split(",")[0]}</small></button>`).join("")}</div>
    <label>Due date <input type="date" class="dp-due" value="${esc(r.own_due || r.due || "")}"></label>
    <label>Start date <input type="date" class="dp-start" value="${esc(r.own_start || "")}"></label>
    <div class="dp-actions"><button type="button" class="btn sm ghost dp-clear">Clear dates</button><button type="button" class="btn sm primary dp-save">Save</button></div>
  </div>`);
  const due = box.querySelector(".dp-due"), start = box.querySelector(".dp-start");
  const done = (ch) => { closePopover(); onPick(ch); };
  box.querySelectorAll(".dp-quick button").forEach((b) => b.addEventListener("click", () => done({ due: b.dataset.v })));
  box.querySelector(".dp-clear").addEventListener("click", () => done({ due: null, start: null }));
  const save = () => {
    const ch = {};
    if (due.value !== (r.own_due || r.due || "")) ch.due = due.value || null;
    if (start.value !== (r.own_start || "")) ch.start = start.value || null;
    const s = "start" in ch ? ch.start : r.own_start, d = "due" in ch ? ch.due : r.due;
    if (s && d && s > d) { start.setCustomValidity("Start is after due"); start.reportValidity(); return; }
    if (Object.keys(ch).length) done(ch); else closePopover();
  };
  box.querySelector(".dp-save").addEventListener("click", save);
  box.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); save(); } });
  return popover(anchor, box, { width: 300 });
}

export function statusPill(s) {
  if (!s || !s.name) return "";
  const c = s.color || "#adb5bd";
  return `<span class="pill status" style="--c:${esc(c)}">${esc(s.name)}</span>`;
}
