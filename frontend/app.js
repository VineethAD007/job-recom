// Frontend logic. All scoring happens in the Flask backend (POST /api/recommend).

// Served by Flask -> same origin. Opened via Live Server / file -> call Flask on port 8000.
const API_BASE = location.port === "8000" ? "" : "http://localhost:8000";

const DEFAULT = { skills: ["Python", "SQL", "Machine Learning", "Git"], exp: 0, city: "Bengaluru", mode: "Any", sal: 0 };
let state = JSON.parse(JSON.stringify(DEFAULT));
let profileKey = "";

let allSkills = [];
let reqId = 0;
let timer = null;

const $ = (id) => document.getElementById(id);
const lower = (s) => s.toLowerCase();
const has = (s) => state.skills.some((x) => lower(x) === lower(s));

function esc(text) {
  const d = document.createElement("div");
  d.textContent = String(text);
  return d.innerHTML;
}

function canon(s) {
  const m = allSkills.find((x) => lower(x) === lower(s));
  return m || s.trim();
}

function addSkill(s) {
  s = canon(s);
  if (s && !has(s)) { state.skills.push(s); update(); }
}

function removeSkill(s) {
  state.skills = state.skills.filter((x) => x !== s);
  update();
}

// ---------- profile panel ----------
function renderProfile() {
  $("mySkills").innerHTML = state.skills.length
    ? state.skills.map((s) => `<button type="button" class="chip on" data-rm="${esc(s)}" aria-label="Remove ${esc(s)}">${esc(s)}</button>`).join("")
    : '<span style="color:var(--ink-soft);font-size:13px">No skills yet. Add one above.</span>';

  $("sugSkills").innerHTML = allSkills.filter((s) => !has(s)).slice(0, 10)
    .map((s) => `<button type="button" class="chip sug" data-add="${esc(s)}">+ ${esc(s)}</button>`).join("");

  $("exp").value = state.exp;
  $("expVal").textContent = state.exp === 0 ? "Fresher" : state.exp + (state.exp === 1 ? " year" : state.exp === 10 ? "+ years" : " years");
  $("sal").value = state.sal;
  $("salVal").textContent = state.sal === 0 ? "No minimum" : "₹" + state.sal + " LPA";
  $("city").value = state.city;
  document.querySelectorAll("#mode button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === state.mode)));
  $("legend").hidden = !state.skills.length;
}

// ---------- results ----------
function ring(n) {
  const r = 32, c = 2 * Math.PI * r, off = c * (1 - n / 100);
  const cls = n >= 65 ? "" : n >= 40 ? "mid" : "low";
  return `<div class="ring ${cls}"><svg viewBox="0 0 76 76"><circle class="bg" cx="38" cy="38" r="${r}"/><circle class="fg" cx="38" cy="38" r="${r}" stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}"/></svg><b>${n}</b></div>`;
}

const PART_LABELS = { skills: "Skills", experience: "Experience", location: "Location", salary: "Salary" };

function jobCard(j) {
  const chips =
    j.required.map((s) => `<span class="sk ${j.matched_required.includes(s) ? "hit" : ""}">${esc(s)}</span>`).join("") +
    j.nice.map((s) => `<span class="sk nice ${j.matched_nice.includes(s) ? "hit" : ""}">${esc(s)}</span>`).join("");
  const bars = Object.keys(PART_LABELS).map((k) => {
    const v = Math.round(j.parts[k] * 100);
    return `<div class="bar"><span>${PART_LABELS[k]}</span><div class="track"><div style="width:${v}%"></div></div><em>${v}%</em></div>`;
  }).join("");
  const verdict = j.missing_required.length
    ? "Missing required: " + j.missing_required.map(esc).join(", ") + "."
    : "You meet every required skill.";
  return `<article class="job">
    <div class="score">${ring(j.score)}</div>
    <div style="min-width:0">
      <h3>${esc(j.title)}</h3>
      <div class="meta"><span>${esc(j.company)}</span><span>${esc(j.city)} · ${esc(j.mode)}</span><span>${j.exp_min}–${j.exp_max} yrs</span><span class="sal">₹${j.salary_min}–${j.salary_max} LPA</span></div>
      <div class="skills">${chips}</div>
      <details><summary>Why this score</summary><div class="verdict">${verdict}</div><div class="bars">${bars}</div></details>
    </div>
  </article>`;
}

function renderResults(data) {
  $("resCount").textContent = `${data.results.length} of ${data.total_jobs} example openings`;

  const gb = $("gapBox");
  if (data.skill_gaps.length) {
    gb.hidden = false;
    gb.innerHTML = `<strong>Skills to learn next</strong><div class="sub">Required in your top 5 matches but missing from your profile.</div><div class="chips">` +
      data.skill_gaps.map((g) => `<span class="chip">${esc(g.skill)} · ${g.jobs} ${g.jobs === 1 ? "job" : "jobs"}</span>`).join("") + "</div>";
  } else { gb.hidden = true; }

  if (!state.skills.length) { $("results").innerHTML = '<div class="empty">Add at least one skill to see ranked matches.</div>'; return; }
  if (!data.results.length) { $("results").innerHTML = '<div class="empty">No jobs match this work style. Choose Any to see all openings.</div>'; return; }
  $("results").innerHTML = data.results.map(jobCard).join("");
}

async function fetchResults() {
  const id = ++reqId;
  try {
    const res = await fetch(API_BASE + "/api/recommend", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skills: state.skills, experience: state.exp, city: state.city, mode: state.mode, min_salary: state.sal }),
    });
    if (!res.ok) throw new Error((await res.json()).error || res.statusText);
    const data = await res.json();
    if (id === reqId) renderResults(data); // ignore out-of-date responses
  } catch (err) {
    if (id === reqId) {
      $("results").innerHTML = `<div class="empty">Could not reach the backend (${esc(err.message)}).<br>Start it with <code>python backend/app.py</code> and reload.</div>`;
    }
  }
}

function update() {
  renderProfile();
  if (profileKey) {
    try { localStorage.setItem(profileKey, JSON.stringify(state)); } catch (e) { /* storage unavailable */ }
  }
  clearTimeout(timer);
  timer = setTimeout(fetchResults, 120); // debounce slider drags
}

// ---------- events ----------
$("skillIn").addEventListener("keydown", function (e) {
  if (e.key === "Enter" || e.key === ",") {
    e.preventDefault();
    const v = this.value.trim();
    if (v) { addSkill(v); this.value = ""; }
  }
});
$("skillIn").addEventListener("change", function () {
  const v = this.value.trim();
  if (v && allSkills.some((s) => lower(s) === lower(v))) { addSkill(v); this.value = ""; }
});
document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-rm],[data-add],#mode button,#reset");
  if (!t) return;
  if (t.dataset.rm) removeSkill(t.dataset.rm);
  else if (t.dataset.add) addSkill(t.dataset.add);
  else if (t.id === "reset") { state = JSON.parse(JSON.stringify(DEFAULT)); update(); }
  else if (t.dataset.v) { state.mode = t.dataset.v; update(); }
});
$("exp").addEventListener("input", function () { state.exp = +this.value; update(); });
$("sal").addEventListener("input", function () { state.sal = +this.value; update(); });
$("city").addEventListener("change", function () { state.city = this.value; update(); });

// ---------- start ----------
async function init() {
  try {
    const [skills, cities] = await Promise.all([
      fetch(API_BASE + "/api/skills", { credentials: "include" }).then((r) => r.json()),
      fetch(API_BASE + "/api/cities", { credentials: "include" }).then((r) => r.json()),
    ]);
    allSkills = skills;
    $("skillList").innerHTML = skills.map((s) => `<option value="${esc(s)}">`).join("");
    $("city").innerHTML = ["Anywhere", ...cities].map((c) => `<option>${esc(c)}</option>`).join("");
  } catch (err) {
    $("city").innerHTML = "<option>Anywhere</option>";
  }
  update();
}

// ---------- authentication ----------
let creatingAccount = false;

async function readResponse(response) {
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

function showAuthError(message) {
  $("authMessage").textContent = message;
}

function setAuthMode(create) {
  creatingAccount = create;
  $("authTitle").textContent = create ? "Create your account" : "Welcome back";
  $("authDescription").textContent = create
    ? "Create an account to find jobs matched to your skills."
    : "Sign in to find jobs matched to your skills.";
  $("authPassword").autocomplete = create ? "new-password" : "current-password";
  $("authSubmit").textContent = create ? "Create account" : "Sign in";
  $("authSwitchText").textContent = create ? "Already have an account?" : "New to Pathfinder?";
  $("authSwitch").textContent = create ? "Sign in" : "Create an account";
  showAuthError("");
}

function enterApp(email) {
  state = JSON.parse(JSON.stringify(DEFAULT));
  profileKey = `pathfinder-profile:${email}`;
  try {
    const saved = JSON.parse(localStorage.getItem(profileKey) || "null");
    if (saved && Array.isArray(saved.skills)) state = saved;
  } catch (e) { /* storage unavailable */ }
  $("accountEmail").textContent = email;
  $("authPage").hidden = true;
  $("appContent").hidden = false;
  init();
}

async function checkSession() {
  try {
    const data = await readResponse(await fetch(API_BASE + "/api/session", { credentials: "include" }));
    if (data.authenticated) {
      enterApp(data.email);
    } else {
      $("authPage").hidden = false;
    }
  } catch (err) {
    $("authPage").hidden = false;
    showAuthError(`Could not reach the backend (${err.message}). Start it with python backend\\app.py and reload.`);
  }
}

$("authSwitch").addEventListener("click", () => setAuthMode(!creatingAccount));
$("authForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  showAuthError("");
  $("authSubmit").disabled = true;
  const endpoint = creatingAccount ? "/api/register" : "/api/login";
  try {
    const data = await readResponse(await fetch(API_BASE + endpoint, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: $("authEmail").value, password: $("authPassword").value }),
    }));
    $("authForm").reset();
    enterApp(data.email);
  } catch (err) {
    showAuthError(err.message);
  } finally {
    $("authSubmit").disabled = false;
  }
});

$("logout").addEventListener("click", async () => {
  try {
    await readResponse(await fetch(API_BASE + "/api/logout", {
      method: "POST",
      credentials: "include",
    }));
    profileKey = "";
    $("appContent").hidden = true;
    $("authPage").hidden = false;
    setAuthMode(false);
  } catch (err) {
    window.alert(`Could not sign out (${err.message}). Please try again.`);
  }
});

checkSession();
