// The sign-in and change-password pages.

const BASE = document.body.dataset.base;
const NEXT = document.body.dataset.next;
const loginForm = document.querySelector("#login-form");
const passwordForm = document.querySelector("#password-form");

async function post(path, body) {
  const res = await fetch(BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Biscuits": "1" },
    body: JSON.stringify(body),
  });
  let data = {};
  try { data = await res.json(); } catch (_) {}
  if (!res.ok || data.ok === false) throw new Error(data.error || `Something went wrong (HTTP ${res.status})`);
  return data;
}

function showError(id, message) {
  const el = document.querySelector(id);
  el.textContent = message || "";
  el.hidden = !message;
}

let justSignedInWith = null;

loginForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  showError("#auth-error", "");
  const { username, password } = Object.fromEntries(new FormData(loginForm));
  try {
    const res = await post("/api/login", { username, password });
    if (!res.must_change) { location.href = NEXT; return; }
    // First sign-in: they've just typed the current password, so don't ask again.
    justSignedInWith = password;
    loginForm.hidden = true;
    document.querySelector(".auth-note")?.remove();
    passwordForm.hidden = false;
    document.querySelector("#password-lead").textContent = "Welcome! Please choose your own password to continue.";
    passwordForm.elements.current.closest("label").hidden = true;
    passwordForm.elements.current.required = false;
    passwordForm.elements.new.focus();
  } catch (err) {
    showError("#auth-error", err.message);
  }
});

passwordForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  showError("#password-error", "");
  const f = Object.fromEntries(new FormData(passwordForm));
  if (f.new !== f.again) { showError("#password-error", "The two new passwords don't match."); return; }
  try {
    await post("/api/account/password", { current: justSignedInWith ?? f.current, new: f.new });
    location.href = NEXT;
  } catch (err) {
    showError("#password-error", err.message);
  }
});
