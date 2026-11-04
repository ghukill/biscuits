// Small shared helpers: escaping, dates, avatars, DOM building.

export const $ = (s, root = document) => root.querySelector(s);
export const $$ = (s, root = document) => [...root.querySelectorAll(s)];

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ---------- dates (all local, YYYY-MM-DD strings) ----------
export const iso = (d) => {
  const z = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}`;
};
export const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
export const parseISO = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
export const TODAY = (() => { const d = new Date(); d.setHours(0, 0, 0, 0); return d; })();
export const T = iso(TODAY);
const mondayOf = (d) => addDays(d, -((d.getDay() + 6) % 7));
export const RANGES = {
  week: [iso(mondayOf(TODAY)), iso(addDays(mondayOf(TODAY), 6))],
  nextWeek: [iso(addDays(mondayOf(TODAY), 7)), iso(addDays(mondayOf(TODAY), 13))],
  month: [iso(new Date(TODAY.getFullYear(), TODAY.getMonth(), 1)),
          iso(new Date(TODAY.getFullYear(), TODAY.getMonth() + 1, 0))],
  nextMonth: [iso(new Date(TODAY.getFullYear(), TODAY.getMonth() + 1, 1)),
              iso(new Date(TODAY.getFullYear(), TODAY.getMonth() + 2, 0))],
};
export const inRange = (v, [a, b]) => !!v && v >= a && v <= b;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

// Asana-ish friendly dates: Today, Tomorrow, Yesterday, weekday names this week, else "Nov 12".
export function friendlyDate(s) {
  if (!s) return "";
  const d = parseISO(s);
  const n = Math.round((d - TODAY) / 86400000);
  if (n === 0) return "Today";
  if (n === 1) return "Tomorrow";
  if (n === -1) return "Yesterday";
  if (n > 1 && n < 7) return DAYS[d.getDay()];
  return `${MONTHS[d.getMonth()]} ${d.getDate()}` + (d.getFullYear() !== TODAY.getFullYear() ? `, ${d.getFullYear()}` : "");
}
export function shortDate(s) {
  if (!s) return "";
  const d = parseISO(s);
  return `${MONTHS[d.getMonth()]} ${d.getDate()}` + (d.getFullYear() !== TODAY.getFullYear() ? `, ${d.getFullYear()}` : "");
}
export function longDate(s) {
  if (!s) return "";
  const d = parseISO(s);
  return `${DAYS[d.getDay()]}, ${MONTHS[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
}
export function dueClass(r) {
  if (!r.due || r.done) return "";
  if (r.due < T) return "overdue";
  if (r.due === T || r.due === iso(addDays(TODAY, 1))) return "soon";
  return "";
}
export function ago(ts) {
  if (!ts) return "";
  const sec = (Date.now() - new Date(ts).getTime()) / 1000;
  if (sec < 60) return "just now";
  if (sec < 3600) return `${Math.round(sec / 60)} min ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)} h ago`;
  if (sec < 86400 * 7) return `${Math.round(sec / 86400)} d ago`;
  return friendlyDate(iso(new Date(ts)));
}

// ---------- people ----------
export const hue = (s) => { let h = 0; for (const c of String(s)) h = (h * 31 + c.charCodeAt(0)) % 360; return h; };
export const initials = (n) => String(n || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
export function avatar(name, size = "") {
  if (!name) return `<span class="avatar empty ${size}" title="Unassigned"><svg viewBox="0 0 24 24"><circle cx="12" cy="9" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/></svg></span>`;
  return `<span class="avatar ${size}" style="--h:${hue(name)}" title="${esc(name)}">${esc(initials(name))}</span>`;
}

// ---------- DOM ----------
export function h(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

// Strip anything active from OP-rendered HTML before inserting it.
export function safeHTML(html) {
  const doc = new DOMParser().parseFromString(`<div>${html || ""}</div>`, "text/html");
  doc.querySelectorAll("script,style,iframe,object,embed,form,link,meta").forEach((n) => n.remove());
  doc.querySelectorAll("*").forEach((n) => {
    for (const a of [...n.attributes]) {
      const v = a.value.trim().toLowerCase();
      if (a.name.startsWith("on") || ((a.name === "href" || a.name === "src") && v.startsWith("javascript:"))) n.removeAttribute(a.name);
    }
    if (n.tagName === "A") { n.setAttribute("target", "_blank"); n.setAttribute("rel", "noopener"); }
  });
  return doc.body.firstElementChild.innerHTML;
}

export const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

export const isTyping = () => {
  const a = document.activeElement;
  return a && (/INPUT|TEXTAREA|SELECT/.test(a.tagName) || a.isContentEditable);
};

export const ICON = {
  check: `<svg viewBox="0 0 24 24"><path d="M7 12.5l3.2 3.2L17 9"/></svg>`,
  caret: `<svg viewBox="0 0 24 24"><path d="M9 6l6 6-6 6"/></svg>`,
  sub: `<svg viewBox="0 0 24 24"><path d="M6 4v9a3 3 0 0 0 3 3h9"/><path d="M15 13l3 3-3 3"/></svg>`,
  cal: `<svg viewBox="0 0 24 24"><rect x="4" y="5" width="16" height="15" rx="3"/><path d="M8 3v4M16 3v4M4 10h16"/></svg>`,
  plus: `<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>`,
  x: `<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>`,
  link: `<svg viewBox="0 0 24 24"><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/></svg>`,
  ext: `<svg viewBox="0 0 24 24"><path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"/></svg>`,
  up: `<svg viewBox="0 0 24 24"><path d="M6 15l6-6 6 6"/></svg>`,
  down: `<svg viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"/></svg>`,
  comment: `<svg viewBox="0 0 24 24"><path d="M5 18l-1 3 4-2h9a3 3 0 0 0 3-3V7a3 3 0 0 0-3-3H7a3 3 0 0 0-3 3v8"/></svg>`,
  home: `<svg viewBox="0 0 24 24"><path d="M4 11l8-7 8 7"/><path d="M6 10v10h12V10"/></svg>`,
  mine: `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5"/><path d="M8.5 12.2l2.4 2.4 4.6-4.8"/></svg>`,
  list: `<svg viewBox="0 0 24 24"><path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r=".8"/><circle cx="4.5" cy="12" r=".8"/><circle cx="4.5" cy="18" r=".8"/></svg>`,
  board: `<svg viewBox="0 0 24 24"><rect x="4" y="4" width="5" height="16" rx="1.5"/><rect x="11" y="4" width="5" height="11" rx="1.5"/><rect x="18" y="4" width="2.5" height="7" rx="1"/></svg>`,
  search: `<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>`,
  refresh: `<svg viewBox="0 0 24 24"><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/></svg>`,
  log: `<svg viewBox="0 0 24 24"><path d="M7 4h10a2 2 0 0 1 2 2v14l-3-2-3 2-3-2-3 2V6a2 2 0 0 1 2-2z"/><path d="M9 9h6M9 13h4"/></svg>`,
  person: `<svg viewBox="0 0 24 24"><circle cx="12" cy="9" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/></svg>`,
  folder: `<svg viewBox="0 0 24 24"><path d="M4 7a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/></svg>`,
  dots: `<svg viewBox="0 0 24 24"><circle cx="6" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="18" cy="12" r="1"/></svg>`,
  side: `<svg viewBox="0 0 24 24"><rect x="4" y="5" width="16" height="14" rx="2"/><path d="M9 5v14"/></svg>`,
};
