const API_BASE =
  ["localhost", "127.0.0.1"].includes(location.hostname) && location.port === "5500"
    ? "http://localhost:8000"
    : "";

const DEFAULT_PROFILE = {
  skills: ["Python", "SQL", "Machine Learning", "Git"],
  exp: 0,
  city: "Bengaluru",
  mode: "Any",
  sal: 0,
};

let state = structuredClone(DEFAULT_PROFILE);
let profileKey = "";
let csrfToken = "";
let allSkills = [];
let allCities = [];
let interactions = {};
let applications = [];
let latestRecommendations = [];
let activeSearch = "";
let activeModeFilter = "Any";
let activeView = "dashboard";
let creatingAccount = false;
let profileSaveTimer = null;
let recommendationTimer = null;
let recommendationRequest = 0;
let applicationRequest = 0;
let roadmapRequest = 0;
let roadmapData = null;
const APPLICATION_STATUSES = ["Interested", "Applied", "Assessment", "Interview", "Offered", "Rejected"];

const $ = (id) => document.getElementById(id);
const lower = (value) => String(value).toLowerCase();
const hasSkill = (skill) =>
  state.skills.some((selected) => lower(selected) === lower(skill));

function esc(value) {
  const element = document.createElement("div");
  element.textContent = String(value);
  return element.innerHTML;
}

async function apiFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.method && options.method !== "GET") {
    headers.set("X-CSRF-Token", csrfToken);
  }
  return fetch(API_BASE + path, {
    ...options,
    headers,
    credentials: "include",
  });
}

async function readJson(response) {
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

function showToast(message) {
  const toast = $("toast");
  toast.textContent = message;
  toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toast.hidden = true;
  }, 2600);
}

function closeMobileNavigation() {
  $("sidebar").classList.remove("open");
  $("mobileMenu").setAttribute("aria-expanded", "false");
  $("sidebarOverlay").hidden = true;
}

function navigate(view, jobId = null) {
  activeView = view;
  closeMobileNavigation();

  document.querySelectorAll("[data-view-name]").forEach((section) => {
    section.hidden = section.dataset.viewName !== view;
  });
  document.querySelectorAll(".nav-item[data-view]").forEach((item) => {
    item.classList.toggle("active", item.dataset.view === view);
  });

  if (view === "details" && jobId) {
    window.location.hash = `details/${jobId}`;
  } else if (view !== "details") {
    window.location.hash = view;
  }

  if (view === "recommendations") fetchResults();
  if (view === "saved") renderSavedJobs();
  if (view === "tracker") renderApplicationTracker();
  if (view === "roadmap") loadCareerRoadmap();
  if (view === "dashboard") renderDashboard();
  if (view === "profile") renderProfile();
  if (view === "details" && jobId) renderJobDetails(jobId);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function showLanding() {
  $("appContent").hidden = true;
  $("authPage").hidden = true;
  $("landingPage").hidden = false;
  window.location.hash = "landing";
  closeMobileNavigation();
  refreshOpeningCount();
}

async function refreshOpeningCount() {
  try {
    const data = await readJson(await fetch(`${API_BASE}/api/health`));
    $("openingCount").textContent = `${data.jobs} current openings`;
  } catch (_error) {
    $("openingCount").textContent = "Current openings unavailable";
  }
}

function showAuth(create = false) {
  $("landingPage").hidden = true;
  $("appContent").hidden = true;
  $("authPage").hidden = false;
  setAuthMode(create);
  window.location.hash = create ? "register" : "login";
  window.setTimeout(() => $("authEmail").focus(), 50);
}

function setAuthMode(create) {
  creatingAccount = create;
  $("authKicker").textContent = create ? "Join Pathfinder" : "Welcome back";
  $("authTitle").textContent = create ? "Create your account" : "Sign in to Pathfinder";
  $("authDescription").textContent = create
    ? "Create a profile and start discovering better role matches."
    : "Continue to your personalized job matches.";
  $("authPassword").autocomplete = create ? "new-password" : "current-password";
  $("authSubmit").firstChild.textContent = create ? "Create account " : "Sign in ";
  $("authSwitchText").textContent = create ? "Already have an account?" : "New to Pathfinder?";
  $("authSwitch").textContent = create ? "Sign in" : "Create an account";
  $("authMessage").textContent = "";
}

async function toggleSavedJob(jobId) {
  const nextStatus = interactions[jobId] === "saved" ? null : "saved";
  try {
    const response = nextStatus
      ? await apiFetch(`/api/jobs/${jobId}/interaction`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: "saved" }),
        })
      : await apiFetch(`/api/jobs/${jobId}/interaction`, { method: "DELETE" });

    await readJson(response);
    if (nextStatus) interactions[jobId] = "saved";
    else delete interactions[jobId];

    document.querySelectorAll(`[data-save-job="${jobId}"]`).forEach((button) => {
      button.classList.toggle("saved", Boolean(nextStatus));
      button.setAttribute("aria-pressed", String(Boolean(nextStatus)));
      button.textContent = nextStatus ? "♥" : "♡";
    });
    updateSavedCounts();
    if (activeView === "saved") renderSavedJobs();
    showToast(nextStatus ? "Job saved" : "Job removed from saved");
  } catch (error) {
    showToast(error.message);
  }
}

function updateSavedCounts() {
  const savedCount = Object.values(interactions).filter((status) => status === "saved").length;
  $("savedCount").textContent = String(savedCount);
  $("navSavedCount").textContent = String(savedCount);
}

async function createApplication(jobId) {
  try {
    const response = await apiFetch("/api/applications", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: jobId }),
    });
    const data = await readJson(response);
    applications.push(data.application);
    $("navApplicationCount").textContent = String(applications.length);
    document.querySelectorAll(`[data-track-job="${jobId}"]`).forEach((button) => {
      button.disabled = true;
      button.textContent = "In tracker";
    });
    showToast("Job added to your application tracker");
    if (activeView === "tracker") renderApplicationTracker();
  } catch (error) {
    showToast(error.message);
  }
}

function initialsForTitle(title) {
  return String(title)
    .split(/\s+/)
    .filter((word) => !["Engineer", "Developer", "Analyst"].includes(word))
    .slice(0, 2)
    .map((word) => word[0])
    .join("")
    .toUpperCase() || "PJ";
}

function ringClass(score) {
  if (score >= 65) return "";
  if (score >= 40) return "mid";
  return "low";
}

function matchLabel(score) {
  if (score >= 80) return "Strong match";
  if (score >= 65) return "Good match";
  if (score >= 40) return "Partial match";
  return "Low match";
}

function jobCard(job) {
  const matchedRequired = job.matched_required || [];
  const missingRequired = job.missing_required || [];
  const hasSkillAnalysis =
    Array.isArray(job.matched_required) && Array.isArray(job.missing_required);
  const hasScore = Number.isFinite(job.score);
  const requiredSkills = job.required
    .map((skill) => {
      const className = hasSkillAnalysis
        ? matchedRequired.includes(skill) ? "hit" : missingRequired.includes(skill) ? "missing" : ""
        : "";
      return `<span class="skill-tag ${className}">${esc(skill)}</span>`;
    })
    .join("");
  const saved = interactions[job.id] === "saved";
  const isTracked = applications.some((application) => application.job_id === job.id);

  return `
    <article class="job-card">
      <div class="job-card-head">
        <div class="company-lockup">
          <div class="company-logo-card">${esc(initialsForTitle(job.company))}</div>
          <div><h3>${esc(job.title)}</h3><p>${esc(job.company)}</p></div>
        </div>
        <button type="button" class="save-job ${saved ? "saved" : ""}" data-save-job="${job.id}" aria-label="${saved ? "Remove saved job" : "Save job"}" aria-pressed="${saved}">${saved ? "♥" : "♡"}</button>
      </div>
      <div class="job-meta">
        <span>${esc(job.city)} · ${esc(job.mode)}</span>
        <span>${job.exp_min}–${job.exp_max} yrs</span>
        <span>₹${job.salary_min}–${job.salary_max} LPA</span>
      </div>
      <div class="job-skills">${requiredSkills}</div>
      <div class="job-card-bottom">
        <div class="salary-block"><span>Salary range</span><strong>₹${job.salary_min}–${job.salary_max} LPA</strong></div>
        <div class="match-score">
          ${hasScore
            ? `<div class="match-score-ring ${ringClass(job.score)}" style="--ring:${job.score * 3.6}deg"><strong>${job.score}%</strong></div>
               <div><span>Match</span><strong>${matchLabel(job.score)}</strong></div>`
            : `<div class="unavailable">Match score unavailable</div>`}
        </div>
      </div>
      <div class="job-card-actions">
        <button type="button" class="view-job-button" data-view-job="${job.id}">View details</button>
        <button type="button" class="apply-status" data-track-job="${job.id}" ${isTracked ? "disabled" : ""}>${isTracked ? "In tracker" : "Track application"}</button>
      </div>
    </article>`;
}

function filteredRecommendations() {
  if (!activeSearch) return latestRecommendations;
  const term = lower(activeSearch);
  return latestRecommendations.filter((job) =>
    [job.title, job.company, job.city, job.mode, ...job.required, ...job.nice]
      .some((value) => lower(value).includes(term))
  );
}

function renderResults(data) {
  const gapBox = $("gapBox");
  if (data.skill_gaps && data.skill_gaps.length) {
    gapBox.hidden = false;
    gapBox.innerHTML = `
      <strong>Skills to learn next</strong>
      <div class="sub">Missing required skills appearing across your strongest matches.</div>
      <div class="gap-chips">${data.skill_gaps.map((gap) =>
        `<span class="gap-chip">${esc(gap.skill)} · ${gap.jobs} ${gap.jobs === 1 ? "job" : "jobs"} · Priority ${gap.priority}%</span>`
      ).join("")}</div>`;
  } else {
    gapBox.hidden = true;
  }

  const visible = filteredRecommendations();
  if (!state.skills.length) {
    $("results").innerHTML = `<div class="empty-state"><strong>Add your skills</strong>Add at least one skill to receive ranked matches.</div>`;
  } else if (!visible.length) {
    $("results").innerHTML = `<div class="empty-state"><strong>No matching openings</strong>Try another work preference or search term.</div>`;
  } else {
    $("results").innerHTML = visible.map(jobCard).join("");
  }

  $("recommendationSummary").textContent =
    `${visible.length} of ${data.total_jobs} example openings match the current filters.`;
  $("navJobCount").textContent = String(data.total_matches || visible.length);
  $("loadMore").hidden = true;
}

function recommendationPayload(mode = activeModeFilter) {
  return {
    skills: state.skills,
    experience: state.exp,
    city: state.city,
    mode,
    min_salary: state.sal,
    preferred_mode: state.mode,
    limit: 50,
  };
}

async function fetchResults() {
  const requestId = ++recommendationRequest;
  try {
    const response = await apiFetch("/api/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...recommendationPayload(),
        sort: $("sortResults").value,
        offset: 0,
      }),
    });
    const data = await readJson(response);
    if (requestId !== recommendationRequest) return;
    latestRecommendations = data.results;
    renderResults(data);
    renderDashboard();
  } catch (error) {
    if (requestId === recommendationRequest) {
      $("results").innerHTML = `<div class="empty-state"><strong>Could not load recommendations</strong>${esc(error.message)}</div>`;
    }
  }
}

function scheduleRecommendations() {
  window.clearTimeout(recommendationTimer);
  recommendationTimer = window.setTimeout(fetchResults, 180);
  scheduleProfileSave();
}

function profileCompletion() {
  const checks = [
    state.skills.length > 0,
    Number.isFinite(Number(state.exp)),
    state.city && state.city !== "Anywhere",
    state.mode && state.mode !== "Any",
    Number(state.sal) > 0,
  ];
  const complete = checks.filter(Boolean).length;
  return Math.round((complete / checks.length) * 100);
}

function renderDashboard() {
  const savedCount = Object.values(interactions).filter((status) => status === "saved").length;
  const average = latestRecommendations.length
    ? Math.round(
        latestRecommendations.reduce((sum, job) => sum + job.score, 0) /
          latestRecommendations.length
      )
    : 0;
  const completion = profileCompletion();
  const firstName = ($("accountEmail").textContent || "there").split("@")[0];

  $("welcomeHeading").textContent = `Welcome back, ${firstName}`;
  $("profileCompletion").textContent = `${completion}%`;
  $("profileProgress").style.width = `${completion}%`;
  $("recommendedCount").textContent = String(latestRecommendations.length);
  $("recommendedSubtext").textContent = latestRecommendations.length
    ? "Based on your current profile"
    : "Waiting for profile matches";
  $("averageMatch").textContent = `${average}%`;
  $("savedCount").textContent = String(savedCount);
  $("navSavedCount").textContent = String(savedCount);
  $("snapshotPercent").textContent = `${completion}%`;
  $("snapshotRing").style.setProperty("--percent", `${completion * 3.6}deg`);
  $("snapshotSkills").textContent = String(state.skills.length);
  $("snapshotExperience").textContent =
    Number(state.exp) === 0 ? "Fresher" : `${state.exp} year${Number(state.exp) === 1 ? "" : "s"}`;
  $("snapshotLocation").textContent = state.city || "Anywhere";
  $("snapshotMode").textContent = state.mode || "Any";

  const recent = latestRecommendations.slice(0, 5);
  $("recentRecommendations").innerHTML = recent.length
    ? recent.map((job) => `
        <article class="recent-job">
          <div class="recent-logo">${esc(initialsForTitle(job.company))}</div>
          <div class="recent-job-main"><strong>${esc(job.title)}</strong><span>${esc(job.company)} · ${esc(job.city)}</span></div>
          <div class="recent-score"><strong>${job.score}%</strong><span>match</span></div>
          <button type="button" data-view-job="${job.id}" aria-label="View ${esc(job.title)}">View</button>
        </article>`)
      : `<div class="empty-state"><strong>No recommendations yet</strong>Complete your profile to generate matches.</div>`;
}

function renderProfile() {
  $("mySkills").innerHTML = state.skills.length
    ? state.skills.map((skill) =>
        `<button type="button" class="skill-chip selected" data-remove-skill="${esc(skill)}" aria-label="Remove ${esc(skill)}">${esc(skill)} ×</button>`
      ).join("")
    : `<span class="gap-chip">No skills selected</span>`;

  $("sugSkills").innerHTML = allSkills
    .filter((skill) => !hasSkill(skill))
    .slice(0, 12)
    .map((skill) => `<button type="button" class="skill-chip" data-add-skill="${esc(skill)}">+ ${esc(skill)}</button>`)
    .join("");

  $("exp").value = state.exp;
  $("expVal").textContent =
    Number(state.exp) === 0
      ? "Fresher"
      : `${state.exp} ${Number(state.exp) === 1 ? "year" : "years"}`;
  $("sal").value = state.sal;
  $("salVal").textContent =
    Number(state.sal) === 0 ? "No minimum" : `₹${state.sal} LPA`;
  $("city").value = state.city;

  document.querySelectorAll("#mode button").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.v === state.mode));
  });

  const completion = profileCompletion();
  $("profileSnapshotPercent").textContent = `${completion}%`;
  $("profileSnapshotRing").style.setProperty("--percent", `${completion * 3.6}deg`);
  $("profileProgressText").textContent = completion === 100
    ? "Your profile is complete."
    : "Add more profile details to improve matching.";

  const checks = {
    checkSkills: state.skills.length > 0,
    checkExperience: Number.isFinite(Number(state.exp)),
    checkLocation: state.city && state.city !== "Anywhere",
    checkMode: state.mode && state.mode !== "Any",
    checkSalary: Number(state.sal) > 0,
  };
  Object.entries(checks).forEach(([id, complete]) => {
    const item = $(id);
    item.classList.toggle("complete", Boolean(complete));
    item.firstElementChild.textContent = complete ? "✓" : "○";
  });
}

async function persistProfile() {
  await readJson(await apiFetch("/api/profile", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(state),
  }));
  localStorage.setItem(profileKey, JSON.stringify(state));
}

function scheduleProfileSave() {
  if (!profileKey) return;
  window.clearTimeout(profileSaveTimer);
  profileSaveTimer = window.setTimeout(async () => {
    try {
      await persistProfile();
    } catch (_error) {
      showToast("Profile changes could not be saved");
    }
  }, 450);
}

async function saveProfileNow() {
  window.clearTimeout(profileSaveTimer);
  renderProfile();
  try {
    await persistProfile();
    showToast("Profile saved");
    await fetchResults();
  } catch (error) {
    showToast(`Profile changes could not be saved: ${error.message}`);
  }
}

function updateProfile(mutator) {
  mutator();
  renderProfile();
  scheduleRecommendations();
}

function addSkill(skill) {
  skill = String(skill).trim();
  if (!skill || hasSkill(skill)) return;
  state.skills = [...state.skills, allSkills.find((item) => lower(item) === lower(skill)) || skill].slice(0, 50);
  updateProfile(() => {});
  $("skillIn").value = "";
}

function removeSkill(skill) {
  state.skills = state.skills.filter((item) => item !== skill);
  updateProfile(() => {});
}

async function renderSavedJobs() {
  try {
    const [data, recommendationData] = await Promise.all([
      readJson(await apiFetch("/api/saved-jobs")),
      readJson(await apiFetch("/api/recommend", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(recommendationPayload("Any")),
      })),
    ]);
    const recommendationsById = new Map(
      recommendationData.results.map((job) => [job.id, job])
    );
    const saved = data.interactions
      .filter((job) => job.status === "saved")
      .map((job) => ({ ...job, ...recommendationsById.get(job.id) }));
    $("savedJobs").innerHTML = saved.length
      ? saved.map(jobCard).join("")
      : `<div class="empty-state"><strong>No saved jobs yet</strong>Save roles from recommendations to find them here.</div>`;
  } catch (error) {
    $("savedJobs").innerHTML = `<div class="empty-state"><strong>Could not load saved jobs</strong>${esc(error.message)}</div>`;
  }
}

async function renderApplicationTracker() {
  const requestId = ++applicationRequest;
  const status = $("applicationStatusFilter").value;
  const startDate = $("applicationStartDate").value;
  const endDate = $("applicationEndDate").value;
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);

  try {
    const [listData, stats] = await Promise.all([
      readJson(await apiFetch(`/api/applications${params.size ? `?${params}` : ""}`)),
      readJson(await apiFetch("/api/application-stats")),
    ]);
    if (requestId !== applicationRequest) return;
    applications = listData.applications;
    const counts = stats.statuses;
    const inProgress = counts.Interested + counts.Applied + counts.Assessment + counts.Interview;
    const outcomes = counts.Offered + counts.Rejected;
    $("trackerTotal").textContent = String(stats.total);
    $("trackerProgress").textContent = String(inProgress);
    $("trackerOffers").textContent = String(counts.Offered);
    $("trackerResponse").textContent = `${stats.total ? Math.round((outcomes / stats.total) * 100) : 0}%`;
    $("navApplicationCount").textContent = String(stats.total);

    $("applicationBoard").innerHTML = APPLICATION_STATUSES.map((stage) => {
      const stageApplications = applications.filter((application) => application.status === stage);
      return `<section class="pipeline-column">
        <header><span class="status-dot status-${stage.toLowerCase()}"></span><h2>${stage}</h2><b>${stageApplications.length}</b></header>
        <div class="pipeline-cards">${stageApplications.length
          ? stageApplications.map(applicationCard).join("")
          : `<div class="pipeline-empty">No applications</div>`}</div>
      </section>`;
    }).join("");

    const timeline = [...applications].sort((a, b) =>
      `${b.application_date}${b.updated_at}`.localeCompare(`${a.application_date}${a.updated_at}`)
    );
    $("applicationTimeline").innerHTML = timeline.length
      ? timeline.map((application) => `<article class="timeline-item">
          <span class="status-dot status-${application.status.toLowerCase()}"></span>
          <div><strong>${esc(application.job.title)}</strong><span>${esc(application.job.company)} · ${esc(application.status)}</span></div>
          <time datetime="${esc(application.application_date)}">${formatApplicationDate(application.application_date)}</time>
        </article>`).join("")
      : `<div class="timeline-empty">Your application activity will appear here.</div>`;
  } catch (error) {
    if (requestId === applicationRequest) {
      $("applicationBoard").innerHTML = `<div class="empty-state"><strong>Could not load applications</strong>${esc(error.message)}</div>`;
      $("applicationTimeline").innerHTML = "";
    }
  }
}

function applicationCard(application) {
  const statusOptions = APPLICATION_STATUSES.map((status) =>
    `<option value="${status}" ${status === application.status ? "selected" : ""}>${status}</option>`
  ).join("");
  return `<article class="application-card" data-application-card="${application.id}">
    <div class="application-card-heading">
      <div><h3>${esc(application.job.title)}</h3><span>${esc(application.job.company)}</span></div>
      <button type="button" class="application-delete" data-delete-application="${application.id}" aria-label="Remove ${esc(application.job.title)}">×</button>
    </div>
    <div class="application-card-meta">${esc(application.job.city)} · ${esc(application.job.mode)}</div>
    <label>Status<select data-application-status>${statusOptions}</select></label>
    <label>Application date<input type="date" value="${esc(application.application_date)}" data-application-date></label>
    <label>Notes<textarea maxlength="2000" rows="2" placeholder="Add a reminder or note..." data-application-notes>${esc(application.notes)}</textarea></label>
    <button type="button" class="application-save" data-save-application="${application.id}">Save updates</button>
  </article>`;
}

function formatApplicationDate(value) {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year, month - 1, day).toLocaleDateString(undefined, {
    month: "short", day: "numeric", year: "numeric",
  });
}

async function saveApplication(applicationId) {
  const card = document.querySelector(`[data-application-card="${applicationId}"]`);
  try {
    const data = await readJson(await apiFetch(`/api/applications/${applicationId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        status: card.querySelector("[data-application-status]").value,
        application_date: card.querySelector("[data-application-date]").value,
        notes: card.querySelector("[data-application-notes]").value,
      }),
    }));
    applications = applications.map((application) =>
      application.id === applicationId ? data.application : application
    );
    showToast("Application updated");
    renderApplicationTracker();
  } catch (error) {
    showToast(error.message);
  }
}

async function deleteApplication(applicationId) {
  try {
    await readJson(await apiFetch(`/api/applications/${applicationId}`, { method: "DELETE" }));
    const application = applications.find((item) => item.id === applicationId);
    applications = applications.filter((application) => application.id !== applicationId);
    if (application) {
      document.querySelectorAll(`[data-track-job="${application.job_id}"]`).forEach((button) => {
        button.disabled = false;
        button.textContent = "Track application";
      });
    }
    showToast("Application removed");
    renderApplicationTracker();
  } catch (error) {
    showToast(error.message);
  }
}

function renderRoadmapRoleOptions(roadmap) {
  const selectedIds = new Set(roadmap.selected_job_ids || []);
  $("roadmapRoleOptions").innerHTML = roadmap.role_options.map((job) => `
    <label class="roadmap-role-option">
      <input type="checkbox" data-roadmap-job="${job.id}" ${selectedIds.has(job.id) ? "checked" : ""}>
      <span><strong>${esc(job.title)}</strong><small>${esc(job.company)} · ${job.required.length} required skills</small></span>
    </label>`).join("");
}

function renderCareerRoadmap(roadmap) {
  roadmapData = roadmap;
  renderRoadmapRoleOptions(roadmap);
  const hasTargets = roadmap.roles.length > 0;
  $("roadmapContent").hidden = !hasTargets;
  $("roadmapMessage").textContent = hasTargets
    ? `${roadmap.roles.length} target role${roadmap.roles.length === 1 ? "" : "s"} selected.`
    : "Choose the job roles you want to work toward, then build your roadmap.";
  if (!hasTargets) return;

  const stats = roadmap.stats;
  const completed = stats.completed_count;
  const stepCount = roadmap.steps.length;
  const completion = stepCount ? Math.round((completed / stepCount) * 100) : 100;
  $("roadmapCompatibility").textContent = `${stats.average_compatibility}%`;
  $("roadmapSkillsDone").textContent = `${completed} / ${stepCount}`;
  $("roadmapLearningCount").textContent = `${stats.learning_count} in progress`;
  $("roadmapHours").textContent = `${stats.estimated_hours_remaining} hrs`;
  $("roadmapProgressLabel").textContent = `${completion}% complete`;
  $("roadmapProgressBar").style.width = `${completion}%`;

  $("roadmapRoleMatches").innerHTML = roadmap.roles.map((role) => `
    <article class="roadmap-match-card">
      <div><strong>${esc(role.title)}</strong><span>${esc(role.company)}</span></div>
      <div class="roadmap-match-value"><strong>${role.compatibility}%</strong><small>skill fit</small></div>
      <div class="roadmap-match-track"><div style="width:${role.compatibility}%"></div></div>
      <small>${role.matched_required} of ${role.required_count} required skills matched exactly</small>
    </article>`).join("");

  const stages = ["Beginner", "Intermediate", "Advanced"];
  $("roadmapStages").innerHTML = stages.map((stage, index) => {
    const steps = roadmap.stages[stage] || [];
    return `<section class="roadmap-stage roadmap-stage-${stage.toLowerCase()}">
      <header class="roadmap-stage-heading">
        <span class="roadmap-stage-number">0${index + 1}</span>
        <div><span class="section-label">Learning stage</span><h2>${stage}</h2></div>
        <span class="roadmap-stage-count">${steps.length} skill${steps.length === 1 ? "" : "s"}</span>
      </header>
      <div class="roadmap-step-list">${steps.length
        ? steps.map((step) => roadmapStepCard(step)).join("")
        : `<div class="roadmap-stage-empty">No skills to add at this stage right now.</div>`}
      </div>
    </section>`;
  }).join("");
}

function roadmapStepCard(step) {
  const options = [
    ["planned", "Planned"],
    ["learning", "Learning"],
    ["completed", "Completed"],
  ].map(([value, label]) =>
    `<option value="${value}" ${step.status === value ? "selected" : ""}>${label}</option>`
  ).join("");
  const priorityLabel = step.required_for.length
    ? `Required · priority ${step.priority}%`
    : step.is_prerequisite
      ? `Suggested prerequisite · priority ${step.priority}%`
      : `Preferred · priority ${step.priority}%`;
  const resource = step.resource
    ? `<a class="roadmap-resource" href="${esc(step.resource.url)}" target="_blank" rel="noopener noreferrer">${esc(step.resource.title)} <span>↗</span><small>${esc(step.resource.provider)} · Free</small></a>`
    : `<span class="roadmap-no-resource">No curated free link for this skill yet.</span>`;
  return `<article class="roadmap-step-card ${step.status === "completed" ? "is-completed" : ""}">
    <div class="roadmap-step-top">
      <div class="roadmap-step-title">
        <span class="roadmap-priority">${priorityLabel}</span>
        <h3>${esc(step.skill)}</h3>
        <p>${esc(step.reason)}</p>
        ${step.prerequisite_for.length
          ? `<small class="roadmap-prereq">Supports: ${step.prerequisite_for.map(esc).join(", ")}</small>`
          : ""}
      </div>
      <label class="roadmap-status-control">Progress
        <select data-roadmap-progress data-roadmap-skill="${esc(step.skill)}">
          ${options}
        </select>
      </label>
    </div>
    <div class="roadmap-step-bottom">
      <span class="roadmap-effort">Estimated effort <strong>~${step.estimated_hours} focused hours</strong></span>
      ${resource}
    </div>
  </article>`;
}

async function loadCareerRoadmap() {
  const requestId = ++roadmapRequest;
  try {
    const roadmap = await readJson(await apiFetch("/api/roadmap"));
    if (requestId === roadmapRequest) renderCareerRoadmap(roadmap);
  } catch (error) {
    if (requestId === roadmapRequest) {
      $("roadmapContent").hidden = true;
      $("roadmapMessage").textContent = `Could not load roadmap: ${error.message}`;
    }
  }
}

async function buildCareerRoadmap() {
  const jobIds = [...document.querySelectorAll("[data-roadmap-job]:checked")]
    .map((checkbox) => Number(checkbox.dataset.roadmapJob));
  if (!jobIds.length) {
    $("roadmapMessage").textContent = "Select at least one target role to build a roadmap.";
    return;
  }

  const button = $("buildRoadmapButton");
  button.disabled = true;
  $("roadmapMessage").textContent = "Comparing your skills with the selected job requirements...";
  try {
    window.clearTimeout(profileSaveTimer);
    await persistProfile();
    const roadmap = await readJson(await apiFetch("/api/roadmap", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_ids: jobIds, skills: state.skills }),
    }));
    renderCareerRoadmap(roadmap);
    showToast("Career roadmap updated");
  } catch (error) {
    $("roadmapMessage").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function updateRoadmapProgress(select) {
  select.disabled = true;
  try {
    const data = await readJson(await apiFetch("/api/roadmap/progress", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        skill: select.dataset.roadmapSkill,
        status: select.value,
      }),
    }));
    if (select.value === "completed") {
      state.skills = data.profile_skills;
      renderProfile();
      scheduleRecommendations();
    }
    await loadCareerRoadmap();
    showToast(select.value === "completed" ? "Skill marked complete; compatibility refreshed" : "Learning progress saved");
  } catch (error) {
    showToast(error.message);
    await loadCareerRoadmap();
  } finally {
    select.disabled = false;
  }
}

async function renderJobDetails(jobId) {
  try {
    const job = await readJson(await apiFetch(`/api/jobs/${jobId}`));
    const matched = latestRecommendations.find((item) => item.id === job.id);
    let detail = matched;
    if (!detail) {
      const response = await readJson(await apiFetch("/api/recommend", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(recommendationPayload("Any")),
      }));
      detail = response.results.find((item) => item.id === job.id);
    }

    const score = detail ? detail.score : null;
    const required = job.required.map((skill) => {
      const missing = detail ? detail.missing_required.includes(skill) : null;
      return `<span class="skill-tag ${missing === null ? "" : missing ? "missing" : "hit"}">${esc(skill)}</span>`;
    }).join("");
    const nice = job.nice.map((skill) => {
      const hit = detail ? detail.matched_nice.includes(skill) : false;
      return `<span class="skill-tag ${hit ? "hit" : ""}">${esc(skill)}</span>`;
    }).join("");
    const missing = detail ? detail.missing_required : null;
    const related = detail
      ? [...(detail.related_required || []), ...(detail.related_nice || [])]
          .map((item) =>
            `<span class="skill-tag related-skill">${esc(item.candidate)} → ${esc(item.required)}</span>`
          ).join("")
      : "";
    const reasons = detail && detail.reasons && detail.reasons.length
      ? `<ul class="details-reasons">${detail.reasons.map((reason) => `<li>${esc(reason)}</li>`).join("")}</ul>`
      : `<div class="unavailable">Complete your profile to see the match explanation.</div>`;
    const parts = detail
      ? Object.entries(detail.parts).map(([key, value]) =>
          `<div class="analysis-row"><span>${key[0].toUpperCase()}${key.slice(1)}</span><div class="analysis-track"><div style="width:${Math.round(value * 100)}%"></div></div><strong>${Math.round(value * 100)}%</strong></div>`
        ).join("")
      : `<div class="unavailable">Complete your profile to calculate match analysis.</div>`;
    const saved = interactions[job.id] === "saved";

    $("jobDetails").innerHTML = `
      <div class="details-layout">
        <article class="details-main">
          <div class="details-header">
            <div><span class="section-label">${esc(job.company)}</span><h1>${esc(job.title)}</h1><p>${esc(job.city)} · ${esc(job.mode)} · ${job.exp_min}–${job.exp_max} years experience</p></div>
            ${score === null
              ? `<div class="details-score unavailable-score"><div><strong>—</strong><span>match</span></div></div>`
              : `<div class="details-score" style="--ring:${score * 3.6}deg"><div><strong>${score}%</strong><span>match</span></div></div>`}
          </div>
          <div class="details-body">
            <h2>Role overview</h2>
            <div class="unavailable">A job description is not included in the current example dataset.</div>
            <h2>Why this match</h2>${reasons}
            <h2>Required skills</h2><div class="details-skills">${required}</div>
            <h2>Related skills</h2>
            ${related ? `<div class="details-skills">${related}</div>` : `<div class="unavailable">No related-skill matches.</div>`}
            <h2>Preferred skills</h2><div class="details-skills">${nice}</div>
            <h2>Missing required skills</h2>
            ${missing === null
              ? `<div class="unavailable">Complete your profile to calculate missing skills.</div>`
              : missing.length
                ? `<div class="details-skills">${missing.map((skill) => `<span class="skill-tag missing">${esc(skill)}</span>`).join("")}</div>`
                : `<div class="unavailable">You meet every required skill in the current dataset.</div>`}
            <h2>Match analysis</h2><div class="match-analysis">${parts}</div>
          </div>
        </article>
        <aside class="details-sidebar">
          <div class="detail-facts">
            <div class="detail-fact"><span>Location</span><strong>${esc(job.city)}</strong></div>
            <div class="detail-fact"><span>Work style</span><strong>${esc(job.mode)}</strong></div>
            <div class="detail-fact"><span>Experience</span><strong>${job.exp_min}–${job.exp_max} yrs</strong></div>
            <div class="detail-fact"><span>Salary</span><strong>₹${job.salary_min}–${job.salary_max} LPA</strong></div>
          </div>
          <button type="button" class="primary-button" data-save-job="${job.id}">${saved ? "♥ Saved" : "♡ Save job"}</button>
          <button type="button" class="secondary-button" data-track-job="${job.id}" ${applications.some((application) => application.job_id === job.id) ? "disabled" : ""}>${applications.some((application) => application.job_id === job.id) ? "In application tracker" : "Track application"}</button>
          <div class="unavailable" style="margin-top:10px">No application URL is included in the current example dataset.</div>
        </aside>
      </div>`;
  } catch (error) {
    $("jobDetails").innerHTML = `<div class="empty-state"><strong>Could not load job details</strong>${esc(error.message)}</div>`;
  }
}

async function initializeApp(email) {
  $("accountEmail").textContent = email;
  $("accountAvatar").textContent = email.slice(0, 1).toUpperCase();
  $("landingPage").hidden = true;
  $("authPage").hidden = true;
  $("appContent").hidden = false;

  try {
    const csrfData = await readJson(await apiFetch("/api/csrf"));
    csrfToken = csrfData.token;
    const [skills, cities, saved, profileResponse, applicationData] = await Promise.all([
      readJson(await apiFetch("/api/skills")),
      readJson(await apiFetch("/api/cities")),
      readJson(await apiFetch("/api/saved-jobs")),
      readJson(await apiFetch("/api/profile")),
      readJson(await apiFetch("/api/applications")),
    ]);
    allSkills = skills;
    allCities = cities;
    interactions = Object.fromEntries(
      (saved.interactions || []).map((job) => [job.id, job.status])
    );
    applications = applicationData.applications || [];
    $("navApplicationCount").textContent = String(applications.length);
    if (profileResponse.profile) {
      state = { ...structuredClone(DEFAULT_PROFILE), ...profileResponse.profile };
    } else {
      try {
        state = JSON.parse(localStorage.getItem(profileKey) || "null") || state;
      } catch (_error) {}
    }
  } catch (error) {
    allSkills = [];
    allCities = [];
    showToast(error.message);
  }

  $("skillList").innerHTML = allSkills
    .map((skill) => `<option value="${esc(skill)}"></option>`)
    .join("");
  $("city").innerHTML = ["Anywhere", ...allCities]
    .map((city) => `<option>${esc(city)}</option>`)
    .join("");
  activeModeFilter = "Any";
  document.querySelectorAll("[data-filter-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.filterMode === activeModeFilter);
  });

  renderProfile();
  updateSavedCounts();
  await fetchResults();

  const hash = window.location.hash.replace("#", "");
  if (hash.startsWith("details/")) {
    navigate("details", Number(hash.split("/")[1]));
  } else if (["dashboard", "recommendations", "profile", "saved", "tracker", "roadmap"].includes(hash)) {
    navigate(hash);
  } else {
    navigate("dashboard");
  }
}

async function checkSession() {
  try {
    const csrfData = await readJson(await apiFetch("/api/csrf"));
    csrfToken = csrfData.token;
    const session = await readJson(await apiFetch("/api/session"));
    if (session.authenticated) {
      profileKey = `pathfinder-profile:${session.email}`;
      await initializeApp(session.email);
    } else {
      showLanding();
    }
  } catch (error) {
    showLanding();
    showToast(`Could not reach the backend: ${error.message}`);
  }
}

document.addEventListener("click", (event) => {
  const viewButton = event.target.closest("[data-view]");
  if (viewButton) navigate(viewButton.dataset.view);

  const authButton = event.target.closest("[data-auth-mode]");
  if (authButton) showAuth(authButton.dataset.authMode === "register");

  const routeButton = event.target.closest("[data-route]");
  if (routeButton) {
    if (routeButton.dataset.route === "landing") showLanding();
  }

  const jobButton = event.target.closest("[data-view-job]");
  if (jobButton) navigate("details", Number(jobButton.dataset.viewJob));

  const saveButton = event.target.closest("[data-save-job]");
  if (saveButton) toggleSavedJob(Number(saveButton.dataset.saveJob));

  const trackButton = event.target.closest("[data-track-job]");
  if (trackButton && !trackButton.disabled) createApplication(Number(trackButton.dataset.trackJob));

  const saveApplicationButton = event.target.closest("[data-save-application]");
  if (saveApplicationButton) saveApplication(Number(saveApplicationButton.dataset.saveApplication));

  const deleteApplicationButton = event.target.closest("[data-delete-application]");
  if (deleteApplicationButton) deleteApplication(Number(deleteApplicationButton.dataset.deleteApplication));

  const removeSkillButton = event.target.closest("[data-remove-skill]");
  if (removeSkillButton) removeSkill(removeSkillButton.dataset.removeSkill);

  const addSkillButton = event.target.closest("[data-add-skill]");
  if (addSkillButton) addSkill(addSkillButton.dataset.addSkill);

  const modeButton = event.target.closest("#mode button");
  if (modeButton) {
    updateProfile(() => {
      state.mode = modeButton.dataset.v;
    });
  }

  const filterButton = event.target.closest("[data-filter-mode]");
  if (filterButton) {
    document.querySelectorAll("[data-filter-mode]").forEach((button) =>
      button.classList.toggle("active", button === filterButton)
    );
    activeModeFilter = filterButton.dataset.filterMode;
    fetchResults();
  }
});

document.addEventListener("change", (event) => {
  const progressSelect = event.target.closest("[data-roadmap-progress]");
  if (progressSelect) updateRoadmapProgress(progressSelect);
});

$("authSwitch").addEventListener("click", () => setAuthMode(!creatingAccount));
$("authForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("authMessage").textContent = "";
  $("authSubmit").disabled = true;
  try {
    const endpoint = creatingAccount ? "/api/register" : "/api/login";
    const data = await readJson(await apiFetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: $("authEmail").value,
        password: $("authPassword").value,
      }),
    }));
    $("authForm").reset();
    profileKey = `pathfinder-profile:${data.email}`;
    await initializeApp(data.email);
  } catch (error) {
    $("authMessage").textContent = error.message;
  } finally {
    $("authSubmit").disabled = false;
  }
});

$("togglePassword").addEventListener("click", () => {
  const input = $("authPassword");
  const reveal = input.type === "password";
  input.type = reveal ? "text" : "password";
  $("togglePassword").textContent = reveal ? "Hide" : "Show";
});

$("logout").addEventListener("click", async () => {
  try {
    await readJson(await apiFetch("/api/logout", { method: "POST" }));
    profileKey = "";
    activeView = "dashboard";
    state = structuredClone(DEFAULT_PROFILE);
    showLanding();
  } catch (error) {
    showToast(error.message);
  }
});

$("skillIn").addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === ",") {
    event.preventDefault();
    addSkill(event.target.value);
  }
});
$("addSkillButton").addEventListener("click", () => addSkill($("skillIn").value));
$("exp").addEventListener("input", (event) =>
  updateProfile(() => { state.exp = Number(event.target.value); })
);
$("sal").addEventListener("input", (event) =>
  updateProfile(() => { state.sal = Number(event.target.value); })
);
$("city").addEventListener("change", (event) =>
  updateProfile(() => { state.city = event.target.value; })
);
$("sortResults").addEventListener("change", fetchResults);
$("reset").addEventListener("click", () =>
  updateProfile(() => { state = structuredClone(DEFAULT_PROFILE); })
);
$("saveProfileButton").addEventListener("click", saveProfileNow);
$("globalSearch").addEventListener("input", (event) => {
  activeSearch = event.target.value.trim();
  if (activeView !== "recommendations") navigate("recommendations");
  renderResults({
    skill_gaps: [],
    total_jobs: latestRecommendations.length,
    total_matches: latestRecommendations.length,
  });
});
$("detailsBack").addEventListener("click", () => navigate("recommendations"));
$("buildRoadmapButton").addEventListener("click", buildCareerRoadmap);
$("applicationStatusFilter").addEventListener("change", renderApplicationTracker);
$("applicationStartDate").addEventListener("change", renderApplicationTracker);
$("applicationEndDate").addEventListener("change", renderApplicationTracker);
$("clearApplicationFilters").addEventListener("click", () => {
  $("applicationStatusFilter").value = "";
  $("applicationStartDate").value = "";
  $("applicationEndDate").value = "";
  renderApplicationTracker();
});
$("topProfileButton").addEventListener("click", () => navigate("profile"));
$("mobileMenu").addEventListener("click", () => {
  const open = $("sidebar").classList.toggle("open");
  $("mobileMenu").setAttribute("aria-expanded", String(open));
  $("sidebarOverlay").hidden = !open;
});
$("sidebarOverlay").addEventListener("click", closeMobileNavigation);

window.addEventListener("hashchange", () => {
  if ($("appContent").hidden) return;
  const hash = window.location.hash.replace("#", "");
  if (hash.startsWith("details/")) navigate("details", Number(hash.split("/")[1]));
  else if (["dashboard", "recommendations", "profile", "saved", "tracker", "roadmap"].includes(hash)) navigate(hash);
});

checkSession();
