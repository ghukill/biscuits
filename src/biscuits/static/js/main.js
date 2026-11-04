// biscuits shell: boot, routing, sidebar, typeahead search, keyboard.

import { $, $$, esc, avatar, hue, isTyping, friendlyDate, ICON, debounce } from "./util.js";
import { S, loadSnapshot, refresh, onChange, activeProjects, liveProject, row, toggleComplete } from "./store.js";
import { setHandler, route, currentTask, openTask, closeTask, go, href } from "./nav.js";
import { renderPane, wirePane, hooks, paneOpen, followId } from "./pane.js";
import { viewHome, viewPeople, viewProject, viewSearch, viewLog, searchIndex } from "./views.js";
import { viewAll, patchAll, reselectAll, unmountAll } from "./all.js";
import { popoverOpen, closePopover, toast } from "./ui.js";

const view = $("#view");
let lastKey = "";

function routeKey(rt) { return `${rt.name}:${rt.id || ""}:${rt.name === "project" ? rt.q.get("view") || "" : ""}:${rt.name === "search" ? rt.q.get("q") : ""}`; }

function renderView(rt) {
  if (rt.name !== "all") unmountAll();
  switch (rt.name) {
    case "my": return viewPeople(view, S.meId, { mine: true });
    case "people": return rt.id === S.meId ? viewPeople(view, S.meId, { mine: true }) : viewPeople(view, rt.id);
    case "project": return viewProject(view, rt.id, rt.q);
    case "all": return viewAll(view);
    case "search": return viewSearch(view, rt.q);
    case "log": return viewLog(view);
    default: return viewHome(view);
  }
}

function render({ paneOnly } = {}) {
  if (!S.snap) return;
  const rt = route();
  const key = routeKey(rt);
  if (key !== lastKey) {
    S.justDone.clear();
    lastKey = key;
    view.dataset.key = "";
    renderView(rt);
    document.title = titleFor(rt) + " · biscuits";
  } else if (paneOnly) {
    if (rt.name === "all") reselectAll();
    else $$(".tl-row, .bcard", view).forEach((el) => el.classList.toggle("sel", +el.dataset.id === currentTask()));
  } else if (rt.name !== "log") {
    renderView(rt);
  }
  renderPane(currentTask());
  paintSidebar(rt);
  if (rt.name === "search") $("#q").value = rt.q.get("q") || "";
}

function titleFor(rt) {
  if (rt.name === "project") return (S.projects.get(rt.id) || {}).name || "Project";
  if (rt.name === "people") return (S.users.get(rt.id) || {}).name || "People";
  return { my: "My tasks", all: "All tasks", search: "Search", log: "Write log" }[rt.name] || "Home";
}

// ---------- sidebar ----------
function paintSidebar(rt) {
  const open = new Map();
  for (const r of S.rows.values()) if (!r.done && r.section == null) open.set(r.project_id, (open.get(r.project_id) || 0) + 1);
  const mine = [...S.rows.values()].filter((r) => !r.done && r.section == null && r.assignee_id === S.meId).length;
  const link = (path, ic, label, active, extra = "") =>
    `<a href="${href(path)}" data-nav class="${active ? "active" : ""}">${ic}<span>${label}</span>${extra}</a>`;
  $("#side-main").innerHTML =
    link("/home", ICON.home, "Home", rt.name === "home") +
    link("/my", ICON.mine, "My tasks", rt.name === "my" || (rt.name === "people" && rt.id === S.meId), `<span class="n">${mine}</span>`) +
    link("/all", ICON.list, "All tasks", rt.name === "all");
  const projects = activeProjects().sort((a, b) => liveProject(b.id) - liveProject(a.id) || a.name.localeCompare(b.name));
  $("#side-projects").innerHTML = projects.map((p) =>
    `<a href="${href(`/project/${p.id}`)}" data-nav class="${rt.name === "project" && rt.id === p.id ? "active" : ""}" title="${esc(p.name)}">
      <span class="pdot" style="--h:${hue(p.name)}"></span><span class="nm">${esc(p.name)}</span>${liveProject(p.id) && !S.writes.all_projects ? `<span class="live-tag">live</span>` : ""}<span class="n">${open.get(p.id) || ""}</span></a>`).join("");
  $("#side-foot").innerHTML = link("/log", ICON.log, "Write log", rt.name === "log") +
    `<div class="mode-note ${S.writes.mode}">${S.writes.mode === "live"
      ? S.writes.all_projects ? "Changes are saved to OpenProject."
      : `Saving to OpenProject is on for <b>${S.writes.write_projects.map((id) => esc((S.projects.get(id) || {}).name || "#" + id)).join(", ")}</b>. Changes anywhere else are previews.`
      : "Preview mode: changes aren't saved to OpenProject."}</div>`;
}

// ---------- sync indicator ----------
function ago(sec) { return sec < 45 ? "just now" : sec < 90 ? "a minute ago" : sec < 3600 ? `${Math.round(sec / 60)} min ago` : `${Math.round(sec / 3600)} h ago`; }
function paintSync(state, msg) {
  const el = $("#sync");
  el.className = "sync " + (state || "");
  $("#sync-text").textContent = msg ?? `Synced ${ago(Date.now() / 1000 - S.fetchedAt)}`;
}
setInterval(() => { if ($("#sync").classList.contains("ok")) paintSync("ok"); }, 30000);
$("#sync-btn").addEventListener("click", async () => {
  paintSync("busy", "Syncing…");
  try { await refresh(); paintSync("ok"); } catch (e) { paintSync("err", "Sync failed"); $("#sync").title = e.message; }
});

// ---------- typeahead search ----------
const q = $("#q"), ta = $("#typeahead");
let taItems = [], taIdx = 0;
function paintTA() {
  const v = q.value.trim();
  if (!v) { ta.hidden = true; return; }
  const res = searchIndex(v, { limit: 8 });
  taItems = [
    ...res.projects.slice(0, 3).map((p) => ({ kind: "Project", go: () => go(`/project/${p.id}`), html: `<span class="ptile-ic sm" style="--h:${hue(p.name)}">${ICON.list}</span><b>${esc(p.name)}</b>` })),
    ...res.people.slice(0, 3).map((u) => ({ kind: "Person", go: () => go(`/people/${u.id}`), html: `${avatar(u.name, "sm")}<b>${esc(u.name)}</b>` })),
    ...res.tasks.map((r) => ({ kind: "Task", go: () => openTask(r.id), html: `<span class="check mini ${r.done ? "on" : ""}">${ICON.check}</span><span class="ta-t ${r.done ? "done" : ""}">${esc(r.subject)}</span><span class="muted">${esc(r.project || "")}${r.due ? " · " + esc(friendlyDate(r.due)) : ""}</span>` })),
  ];
  taIdx = Math.min(taIdx, Math.max(0, taItems.length - 1));
  ta.innerHTML = taItems.map((it, i) => `<div class="ta-item ${i === taIdx ? "focus" : ""}" data-i="${i}"><span class="stype">${it.kind}</span>${it.html}</div>`).join("") +
    `<div class="ta-item ta-all ${taIdx === taItems.length ? "focus" : ""}" data-i="${taItems.length}">${ICON.search} See all results for “${esc(v)}”${res.total ? ` <span class="muted">(${res.total} tasks)</span>` : ""}</div>`;
  ta.hidden = false;
}
const searchAll = () => { ta.hidden = true; go("/search", { q: q.value.trim() }); q.blur(); };
q.addEventListener("input", debounce(() => { taIdx = 0; paintTA(); }, 80));
q.addEventListener("focus", paintTA);
q.addEventListener("blur", () => setTimeout(() => (ta.hidden = true), 150));
q.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") { taIdx = Math.min(taIdx + 1, taItems.length); paintTA(); e.preventDefault(); }
  else if (e.key === "ArrowUp") { taIdx = Math.max(taIdx - 1, 0); paintTA(); e.preventDefault(); }
  else if (e.key === "Enter") { e.preventDefault(); if (taIdx < taItems.length && taItems[taIdx]) { taItems[taIdx].go(); ta.hidden = true; q.blur(); } else searchAll(); }
  else if (e.key === "Escape") { q.value = ""; ta.hidden = true; q.blur(); }
});
ta.addEventListener("mousedown", (e) => {
  const it = e.target.closest(".ta-item");
  if (!it) return;
  e.preventDefault();
  const i = +it.dataset.i;
  if (i < taItems.length) { taItems[i].go(); ta.hidden = true; q.blur(); } else searchAll();
});

// ---------- keyboard ----------
function step(dir) {
  const rows = $$(".tl-row, .bcard, .tabulator-row", view)
    .map((el) => +(el.dataset.id || (el.querySelector(".wp-id") || {}).textContent?.slice(1)))
    .filter((id, i, a) => id && a.indexOf(id) === i);
  if (!rows.length) return;
  const cur = rows.indexOf(currentTask());
  const next = rows[cur < 0 ? (dir > 0 ? 0 : rows.length - 1) : Math.max(0, Math.min(rows.length - 1, cur + dir))];
  openTask(next, { replace: true });
  const el = view.querySelector(`.tl-row[data-id="${next}"], .bcard[data-id="${next}"]`);
  if (el) el.scrollIntoView({ block: "nearest" });
}
hooks.step = step;

document.addEventListener("keydown", (e) => {
  if (popoverOpen()) return;
  if (e.key === "/" && !isTyping()) { e.preventDefault(); q.focus(); q.select(); return; }
  if (e.key === "Escape") {
    if (isTyping()) { document.activeElement.blur(); return; }
    if (paneOpen()) { closeTask(); return; }
  }
  if (isTyping()) return;
  if ((e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "j" || e.key === "k") && !e.altKey && !e.metaKey && !e.ctrlKey) {
    e.preventDefault(); step(e.key === "ArrowDown" || e.key === "j" ? 1 : -1); return;
  }
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && currentTask()) { e.preventDefault(); toggleComplete(currentTask()); return; }
  if (e.key === "?" ) { toast("Shortcuts: / search · ↑↓ or j/k move between tasks · Ctrl+Enter complete · Esc close · double-click a name to rename", { ms: 9000 }); }
});

$("#side-toggle").addEventListener("click", () => {
  document.body.classList.toggle("side-collapsed");
  localStorage.setItem("biscuits.side", document.body.classList.contains("side-collapsed") ? "0" : "1");
});
if (localStorage.getItem("biscuits.side") === "0") document.body.classList.add("side-collapsed");
view.addEventListener("rerender", () => render({}));

// ---------- store -> UI ----------
onChange((ev) => {
  if (ev.type === "idchange") followId(ev.from, ev.to);
  const rt = route();
  if (rt.name === "all") patchAll(ev);
  else if (rt.name !== "log") renderView(rt);
  renderPane(currentTask());
  paintSidebar(rt);
});

// ---------- boot ----------
async function boot() {
  wirePane();
  setHandler(render);
  paintSync("busy", "Loading…");
  try {
    await loadSnapshot();
  } catch (e) {
    view.innerHTML = `<div class="error-box">Couldn't reach OpenProject: ${esc(e.message)}</div>`;
    paintSync("err", "Offline");
    return;
  }
  paintSync("ok");
  render({});
  // The server serves its cached copy instantly and refreshes in the
  // background when stale; pick that up quietly a bit later.
  if (Date.now() / 1000 - S.fetchedAt > 120) setTimeout(async () => {
    const before = S.fetchedAt;
    try { await loadSnapshot(); } catch (_) { return; }
    if (S.fetchedAt > before) {
      const rt = route();
      if (rt.name === "all") patchAll({ type: "reload" }); else if (rt.name !== "log") renderView(rt);
      renderPane(currentTask()); paintSidebar(rt); paintSync("ok");
    }
  }, 15000);
}
boot();
