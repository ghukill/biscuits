// Tiny history-API router. Paths live under the blueprint prefix
// (data-base on <body>); ?task=<id> opens the detail pane on any view.

const BASE = document.body.dataset.base;
let handler = () => {};
export const setHandler = (fn) => { handler = fn; };

export function route() {
  const path = location.pathname.slice(BASE.length).replace(/\/+$/, "") || "/home";
  const q = new URLSearchParams(location.search);
  let m;
  if ((m = path.match(/^\/project\/(\d+)$/))) return { name: "project", id: +m[1], q };
  if ((m = path.match(/^\/people\/(\d+)$/))) return { name: "people", id: +m[1], q };
  const name = path.slice(1) || "home";
  return { name: ["home", "my", "all", "search", "log"].includes(name) ? name : "home", q };
}

export const currentTask = () => +new URLSearchParams(location.search).get("task") || null;

export function href(path, params = {}) {
  const q = new URLSearchParams(params);
  for (const [k, v] of [...q]) if (v == null || v === "") q.delete(k);
  const s = q.toString();
  return BASE + path + (s ? "?" + s : "");
}

export function openTask(id, { replace = false } = {}) {
  const q = new URLSearchParams(location.search);
  if (id) q.set("task", id); else q.delete("task");
  const url = location.pathname + (q.toString() ? "?" + q : "");
  (replace || (!!id && !!currentTask()) ? history.replaceState : history.pushState).call(history, {}, "", url);
  handler({ paneOnly: true });
}
export const closeTask = () => openTask(null);

export function go(path, params = {}, { keepTask = false } = {}) {
  if (keepTask && currentTask()) params.task = currentTask();
  history.pushState({}, "", href(path, params));
  handler({});
}

export function setParam(k, v) {
  const q = new URLSearchParams(location.search);
  if (v == null || v === "") q.delete(k); else q.set(k, v);
  history.replaceState({}, "", location.pathname + (q.toString() ? "?" + q : ""));
}

window.addEventListener("popstate", () => handler({}));
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[data-nav]");
  if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
  e.preventDefault();
  history.pushState({}, "", a.getAttribute("href"));
  handler({});
});
