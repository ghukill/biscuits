// The account page, and the admin screen for managing accounts.
// Plain and deliberately small: list, create or link, reset password, edit key, disable.

import { $, $$, esc, ago, ICON } from "./util.js";
import { getJSON, post, S } from "./store.js";
import { toast } from "./ui.js";

const BASE = document.body.dataset.base;

function head(title, sub, icon = ICON.person) {
  return `<div class="vhead"><div class="vtitle"><span class="ptile-ic big">${icon}</span><div><h1>${esc(title)}</h1><div class="muted">${esc(sub)}</div></div></div></div>`;
}

// A key, masked until asked for.
function keyField(masked, url) {
  if (!masked) return `<span class="muted">None yet</span>`;
  return `<code class="key-val" data-key-url="${esc(url)}" data-masked="${esc(masked)}">${esc(masked)}</code>
    <button class="btn sm" type="button" data-act="reveal">Show</button>`;
}

async function toggleReveal(btn) {
  const code = btn.previousElementSibling;
  if (code.dataset.shown) {
    code.textContent = code.dataset.masked;
    delete code.dataset.shown;
    btn.textContent = "Show";
    return;
  }
  try {
    const { key } = await getJSON(code.dataset.keyUrl);
    code.textContent = key || "(can't be read)";
    code.dataset.shown = "1";
    btn.textContent = "Hide";
  } catch (e) { toast(e.message, { kind: "error" }); }
}

// ============ My account ============
export async function viewAccount(el) {
  el.dataset.key = "account";
  el.innerHTML = head("My account", "Your biscuits sign-in and OpenProject connection") + `<div class="vbody" id="acct"></div>`;
  const box = $("#acct", el);
  try {
    const { account: a } = await getJSON("/api/account");
    box.innerHTML = `<div class="card admin-card"><dl class="kv">
      <dt>Username</dt><dd>${esc(a.username)}</dd>
      <dt>Name</dt><dd>${esc(a.display_name)}</dd>
      <dt>Role</dt><dd>${a.is_admin ? "biscuits admin" : "Member"}</dd>
      <dt>OpenProject user</dt><dd>${a.op_user_id ? `${esc(a.op_user_name || "")} <span class="muted">#${a.op_user_id}</span>` : `<span class="muted">Not linked yet. Ask your biscuits admin.</span>`}</dd>
      <dt>OpenProject key</dt><dd>${keyField(a.key_masked, "/api/account/key")}</dd>
    </dl>
    <p class="muted card-note">biscuits uses this key to talk to OpenProject as you, so your changes show under your name. You don't normally need it.</p>
    <div class="admin-actions"><a class="btn sm" href="${BASE}/password">Change password</a></div></div>`;
    box.onclick = (e) => { const b = e.target.closest('[data-act="reveal"]'); if (b) toggleReveal(b); };
  } catch (e) {
    box.innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
  }
}

// ============ Admin ============
let opUsers = null;
let tokenTemplate = "";
const tokenCommand = (id) => (id ? tokenTemplate.replace("User.find(ID)", `User.find(${+id})`) : "Choose a user to see the command.");

function statusOf(u) {
  if (u.disabled) return `<span class="pill warn">disabled</span>`;
  if (u.key_problem) return `<span class="pill err">key can't be read</span>`;
  if (!u.op_user_id || !u.key_masked) return `<span class="pill warn">needs key</span>`;
  if (u.must_change_password) return `<span class="pill">new password pending</span>`;
  return `<span class="pill ok">active</span>`;
}

function userCard(u) {
  return `<div class="card admin-card ${u.disabled ? "is-disabled" : ""}" data-user="${u.id}">
    <div class="admin-user-head"><b>${esc(u.display_name)}</b> <span class="muted">${esc(u.username)}</span>${u.is_admin ? `<span class="tag">admin</span>` : ""} ${statusOf(u)}
      <span class="muted admin-seen">${u.last_login_at ? `Last signed in ${esc(ago(u.last_login_at + "Z"))}` : "Never signed in"}</span></div>
    <dl class="kv">
      <dt>OpenProject user</dt><dd>${u.op_user_id ? `${esc(u.op_user_name || "")} <span class="muted">#${u.op_user_id}</span>` : `<span class="muted">Not linked</span>`}</dd>
      <dt>OpenProject key</dt><dd>${keyField(u.key_masked, `/api/admin/users/${u.id}/key`)}</dd>
    </dl>
    <div class="admin-actions">
      <button class="btn sm" type="button" data-act="edit-key">${u.key_masked ? "Change key" : "Add key"}</button>
      <button class="btn sm" type="button" data-act="reset">Reset password</button>
      <button class="btn sm" type="button" data-act="toggle-admin">${u.is_admin ? "Remove admin" : "Make admin"}</button>
      ${u.disabled
        ? `<button class="btn sm" type="button" data-act="enable">Enable</button>`
        : `<button class="btn sm" type="button" data-act="disable">Disable</button>`}
    </div>
    <div class="admin-slot"></div>
  </div>`;
}

function keyForm(u) {
  const options = (opUsers || []).filter((o) => o.status !== "locked").map((o) =>
    `<option value="${o.id}" ${o.id === u.op_user_id ? "selected" : ""}>${esc(o.name)} (#${o.id})</option>`).join("");
  return `<form class="admin-form" data-form="key">
    <label>OpenProject user <select name="op_user_id" required><option value="">Choose…</option>${options}</select></label>
    <p class="muted card-note">Keys are made on the OpenProject server. Run this there, then paste the result:</p>
    <pre class="cmd" data-cmd>${esc(tokenCommand(u.op_user_id))}</pre>
    <label>API key <input name="key" type="password" autocomplete="off" required></label>
    <div class="admin-actions"><button class="btn sm primary" type="submit">Check and save</button><button class="btn sm ghost" type="button" data-act="cancel">Cancel</button></div>
  </form>`;
}

function passwordNotice(username, password) {
  return `<div class="p-banner warn admin-secret">Temporary password for <b>${esc(username)}</b>: <code>${esc(password)}</code><br>
    Share it privately. It's shown only once, and they'll choose their own when they first sign in.</div>`;
}

const createForm = () => `<form class="card admin-card admin-form" id="new-user" hidden>
    <h2>New account</h2>
    <label>Username <input name="username" autocomplete="off" autocapitalize="none" required></label>
    <label>OpenProject user <select name="link" required>
      <option value="">Choose…</option>
      <option value="new">Create a new OpenProject user…</option>
      ${(opUsers || []).filter((o) => o.status !== "locked").map((o) => `<option value="${o.id}">${esc(o.name)} (#${o.id})</option>`).join("")}
    </select></label>
    <div class="admin-new-op" hidden>
      <label>Login <input name="login" autocomplete="off"></label>
      <label>First name <input name="first_name" autocomplete="off"></label>
      <label>Last name <input name="last_name" autocomplete="off"></label>
      <label>Email <input name="email" type="email" autocomplete="off"></label>
    </div>
    <label class="toggle"><input type="checkbox" name="is_admin"> biscuits admin</label>
    <div class="admin-actions"><button class="btn sm primary" type="submit">Create account</button><button class="btn sm ghost" type="button" data-act="cancel-new">Cancel</button></div>
  </form>`;

export async function viewAdmin(el) {
  el.dataset.key = "admin";
  el.innerHTML = head("Accounts", "Who can sign in to biscuits, and the OpenProject user each one acts as") +
    `<div class="vtools admin-tools"><button class="btn sm primary" type="button" id="add-user">${ICON.plus} New account</button></div><div class="vbody" id="admin"></div>`;
  const box = $("#admin", el);
  let users = [];
  const paint = () => {
    box.innerHTML = createForm() + `<div id="notice"></div>` + users.map(userCard).join("");
    wireCreate();
  };
  const load = async () => {
    const [u, o] = await Promise.all([getJSON("/api/admin/users"), getJSON("/api/admin/op-users").catch((e) => ({ users: [], error: e.message }))]);
    users = u.users; opUsers = o.users; tokenTemplate = u.token_template;
    paint();
    if (o.error) notice(`<div class="p-banner err">${esc(o.error)}</div>`);
  };
  const notice = (html) => { $("#notice", box).innerHTML = html; };
  const find = (id) => users.find((u) => u.id === +id);

  function wireCreate() {
    const form = $("#new-user", box);
    form.elements.link.addEventListener("change", () => {
      const isNew = form.elements.link.value === "new";
      $(".admin-new-op", form).hidden = !isNew;
      for (const n of ["login", "first_name", "last_name", "email"]) form.elements[n].required = isNew;
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = Object.fromEntries(new FormData(form));
      const body = { username: f.username, is_admin: !!f.is_admin };
      if (f.link === "new") body.new_op_user = { login: f.login, first_name: f.first_name, last_name: f.last_name, email: f.email };
      else body.op_user_id = +f.link;
      try {
        const res = await post("/api/admin/users", body);
        await load();
        notice(passwordNotice(res.user.username, res.password));
      } catch (err) { toast(err.message, { kind: "error", ms: 8000 }); }
    });
  }

  $("#add-user", el).addEventListener("click", () => { const f = $("#new-user", box); f.hidden = !f.hidden; if (!f.hidden) f.elements.username.focus(); });

  box.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const act = b.dataset.act;
    if (act === "cancel-new") { $("#new-user", box).hidden = true; return; }
    if (act === "reveal") return toggleReveal(b);
    const card = b.closest("[data-user]");
    if (!card) return;
    const u = find(card.dataset.user);
    const slot = $(".admin-slot", card);
    const run = async (fn) => { try { await fn(); } catch (err) { toast(err.message, { kind: "error", ms: 8000 }); } };
    if (act === "cancel") { slot.innerHTML = ""; return; }
    if (act === "edit-key") { slot.innerHTML = keyForm(u); return; }
    if (act === "reset") {
      if (!confirm(`Reset the password for ${u.username}? They'll be signed out everywhere.`)) return;
      return run(async () => { const r = await post(`/api/admin/users/${u.id}/password`, {}); await load(); notice(passwordNotice(u.username, r.password)); });
    }
    if (act === "toggle-admin") return run(async () => { await post(`/api/admin/users/${u.id}`, { is_admin: !u.is_admin }); await load(); });
    if (act === "enable") return run(async () => { await post(`/api/admin/users/${u.id}/disabled`, { disabled: false }); await load(); notice(`<div class="p-banner">${esc(u.username)} is enabled again. If they were also locked in OpenProject, unlock them there too.</div>`); });
    if (act === "disable") {
      slot.innerHTML = `<div class="admin-form"><p>Disable <b>${esc(u.username)}</b>? They'll be signed out and can't sign in.</p>
        ${u.op_user_id ? `<label class="toggle"><input type="checkbox" data-lock checked> Also lock ${esc(u.op_user_name || "them")} in OpenProject, which turns off their key everywhere</label>` : ""}
        <div class="admin-actions"><button class="btn sm primary" type="button" data-act="disable-go">Disable</button><button class="btn sm ghost" type="button" data-act="cancel">Cancel</button></div></div>`;
      return;
    }
    if (act === "disable-go") {
      const lock = !!$("[data-lock]", slot)?.checked;
      return run(async () => {
        const r = await post(`/api/admin/users/${u.id}/disabled`, { disabled: true, lock_in_openproject: lock });
        await load();
        notice(`<div class="p-banner">${esc(u.username)} is disabled${r.op_locked ? " and locked in OpenProject" : ""}.</div>`);
      });
    }
  });

  box.addEventListener("change", (e) => {
    if (!e.target.matches('[data-form="key"] select')) return;
    $("[data-cmd]", e.target.closest("[data-user]")).textContent = tokenCommand(e.target.value);
  });

  box.addEventListener("submit", async (e) => {
    const form = e.target.closest('[data-form="key"]');
    if (!form) return;
    e.preventDefault();
    const u = find(form.closest("[data-user]").dataset.user);
    const f = Object.fromEntries(new FormData(form));
    try {
      await post(`/api/admin/users/${u.id}/key`, { key: f.key, op_user_id: +f.op_user_id });
      await load();
      notice(`<div class="p-banner">Key saved for ${esc(u.username)}. OpenProject confirmed it belongs to the right person.</div>`);
    } catch (err) { toast(err.message, { kind: "error", ms: 9000 }); }
  });

  try { await load(); } catch (e) { box.innerHTML = `<div class="error-box">${esc(e.message)}</div>`; }
}
