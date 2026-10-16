"use strict";
// Job Agent dashboard. Plain JS, no build step.
// Everything that comes from job feeds is untrusted: it is only ever put on the
// page as text (textContent), never as HTML, and links must be http(s).

// ---------- helpers ----------
const $ = (sel, root = document) => root.querySelector(sel);

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "on") for (const [ev, fn] of Object.entries(v)) el.addEventListener(ev, fn);
    else if (k === "text") el.textContent = v;
    else if (k === "href") { const u = safeUrl(v); if (u) el.setAttribute("href", u); }
    else if (k in el && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

function safeUrl(u) {
  if (typeof u !== "string" || !u) return null;
  if (u.startsWith("#") || u.startsWith("/")) return u;
  try {
    const p = new URL(u);
    return p.protocol === "http:" || p.protocol === "https:" ? p.href : null;
  } catch { return null; }
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || "GET",
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data = {};
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

let toastTimer;
function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (isError ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = "toast"), isError ? 5000 : 3000);
}

const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
function ago(iso) {
  if (!iso) return "never";
  const secs = (new Date(iso) - Date.now()) / 1000;
  const units = [["year", 31536000], ["month", 2592000], ["week", 604800], ["day", 86400], ["hour", 3600], ["minute", 60]];
  for (const [unit, s] of units) if (Math.abs(secs) >= s) return rtf.format(Math.round(secs / s), unit);
  return secs > 0 ? "in a moment" : "just now";
}
const fmtDate = (iso) => iso ? new Date(iso).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) : "";
function duration(a, b) {
  if (!a || !b) return "";
  const s = Math.max(0, Math.round((new Date(b) - new Date(a)) / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

const VERDICTS = {
  fits: ["Fits your hours", "good"],
  no_cap: ["OK (course break)", "good"],
  partly: ["Fits at low end", "warn"],
  unknown_hours: ["Check hours", "info"],
  over_cap: ["Over hours cap", "bad"],
  blocked: ["Not open to student visa", "bad"],
};
const STATUS = { seen: ["New", "accent"], applied: ["Applied", "good"], skipped: ["Skipped", ""] };
const TRIGGERS = { manual: "Run now", schedule: "Scheduled", cli: "Command line" };
const RUN_STATUS = { done: ["Done", "good"], failed: ["Failed", "bad"], running: ["Running", "info running"] };
const TYPE_LABEL = { part_time: "Part-time", casual: "Casual", internship: "Internship", full_time: "Full-time", contract: "Contract" };
const MODE_LABEL = { remote: "Remote", hybrid: "Hybrid", onsite: "On-site" };
const PAY_LABEL = { paid: "Paid", unpaid: "Unpaid" };
const NOT_STATED = "Not stated";

function detailText(j) {
  return [MODE_LABEL[j.work_mode], TYPE_LABEL[j.employment_type], PAY_LABEL[j.pay], j.salary_text].filter(Boolean).join(" · ");
}

function downloadText(filename, text) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  const a = h("a", { download: filename.replace(/[\\/:*?"<>|]+/g, " ").trim() });
  a.href = url;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function copyText(text, what) {
  try { await navigator.clipboard.writeText(text); toast(`${what} copied`); }
  catch { toast("Couldn't copy. Select the text and copy it instead.", true); }
}

const pill = (text, cls = "") => h("span", { class: `pill ${cls}` }, text);
function scoreBadge(score, big = false) {
  const cls = score == null ? "" : score >= 70 ? "high" : score >= 45 ? "mid" : "low";
  return h("span", { class: `score ${cls}${big ? " big" : ""}`, title: score == null ? "Not scored" : `Match score ${score}/100` }, score == null ? "–" : score);
}
const verdictPill = (v) => v && VERDICTS[v] ? pill(VERDICTS[v][0], VERDICTS[v][1]) : h("span", { class: "muted small" }, "–");
const statusPill = (s) => pill(...(STATUS[s] || [s, ""]));

// ---------- state ----------
const state = {
  summary: null,
  wasRunning: false,
  jobFilters: { q: "", status: "seen", verdict: "", min_score: "", work_mode: "", pay: "", job_type: "", sort: "score", run_id: "" },
  openJobId: null,
};

// ---------- run status + run now ----------
async function refreshSummary() {
  try {
    const s = await api("/api/summary");
    const finished = state.wasRunning && !s.running;
    state.summary = s;
    state.wasRunning = s.running;
    renderRunStatus();
    if (finished) {
      const r = s.last_run;
      if (r && r.status === "done") toast(`Search finished: ${r.shown || 0} new job${r.shown === 1 ? "" : "s"} added`);
      else if (r) toast(`Search failed: ${r.error || "see Runs"}`, true);
      render();
    } else if (currentView() === "dashboard") {
      renderDashboard();
    }
  } catch (e) {
    $("#run-status").textContent = "Offline";
    $("#run-status").className = "pill bad";
  }
  setTimeout(refreshSummary, state.summary && state.summary.running ? 2500 : 20000);
}

function renderRunStatus() {
  const s = state.summary;
  const el = $("#run-status");
  const btn = $("#run-now");
  if (s.running) {
    el.textContent = "Searching…";
    el.className = "pill info running";
    btn.disabled = true;
    btn.textContent = "Running…";
  } else {
    el.textContent = s.next_run ? `Next run ${ago(s.next_run)}` : "Idle";
    el.className = "pill";
    btn.disabled = false;
    btn.textContent = "Run search now";
  }
}

async function runNow() {
  try {
    await api("/api/runs", { method: "POST", body: {} });
    toast("Search started. This can take a few minutes on a free AI key.");
    state.wasRunning = true;
    if (state.summary) { state.summary.running = true; renderRunStatus(); }
    setTimeout(refreshSummary, 1000);
  } catch (e) { toast(e.message, true); }
}

// ---------- router ----------
const VIEWS = { dashboard: renderDashboard, jobs: renderJobs, runs: renderRuns, settings: renderSettings };
const currentView = () => (location.hash.slice(1).split("?")[0] || "dashboard");

function render() {
  const view = VIEWS[currentView()] ? currentView() : "dashboard";
  document.querySelectorAll(".tabs a").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
  VIEWS[view]();
}

function mount(...nodes) {
  const main = $("#view");
  main.replaceChildren(...nodes.flat().filter((n) => n != null && n !== false));
}

// ---------- dashboard ----------
function setupAlerts(s) {
  const out = [];
  const st = s.setup;
  if (st.ai_problem) out.push(h("div", { class: "alert" }, h("strong", {}, "AI is off."), h("span", {}, ` ${st.ai_problem} Jobs will still be found, just not scored.`)));
  if (!s.resume_found) out.push(h("div", { class: "alert" }, h("strong", {}, "No resume found."), h("span", {}, " Create resume.txt in the job-agent folder (or change the path in Settings) to get match scores, cover letters and tailored resumes.")));
  if (!st.adzuna) out.push(h("div", { class: "alert" }, h("strong", {}, "Adzuna isn't set up."), h("span", {}, " Add free ADZUNA_APP_ID and ADZUNA_APP_KEY to .env for Australian job boards. Without it you only get company careers pages and RemoteOK.")));
  return out;
}

function kpi(label, value, hint) {
  return h("div", { class: "card kpi" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value ?? "–"), hint ? h("div", { class: "hint" }, hint) : null);
}

function runFacts(r) {
  return h("dl", { class: "facts" },
    h("dt", {}, "When"), h("dd", {}, `${fmtDate(r.started)} (${ago(r.started)})`),
    h("dt", {}, "Trigger"), h("dd", {}, TRIGGERS[r.trigger] || r.trigger || "–"),
    h("dt", {}, "Status"), h("dd", {}, pill(...(RUN_STATUS[r.status] || [r.status, ""]))),
    h("dt", {}, "Results"), h("dd", {}, `${r.fetched ?? 0} found → ${r.new ?? 0} new → ${r.shown ?? 0} saved`),
    r.error ? [h("dt", {}, "Error"), h("dd", {}, r.error)] : null,
  );
}

function renderDashboard() {
  const s = state.summary;
  if (!s) return mount(h("p", { class: "muted" }, "Loading…"));
  const c = s.counts;
  const st = s.setup;
  const free = Math.max(0, st.visa_cap - st.committed_hours);

  const lastCard = h("div", { class: "card" }, h("h3", {}, "Last search"),
    s.last_run ? runFacts(s.last_run) : h("p", { class: "muted" }, "No searches yet. Press “Run search now” to start."),
    s.last_run ? h("div", { class: "row mt-12" }, h("a", { class: "btn small", href: "#runs" }, "View run history")) : null);

  const nextCard = h("div", { class: "card" }, h("h3", {}, "Next scheduled search"),
    s.next_run
      ? h("div", { class: "stack" }, h("div", { class: "kpi" }, h("div", { class: "value value value-md" }, fmtDate(s.next_run))), h("p", { class: "muted small" }, `${ago(s.next_run)}. Runs while this dashboard is open. If your computer was off, it catches up when you next open it.`))
      : h("div", { class: "stack" }, h("p", { class: "muted" }, "Scheduling is off."), h("a", { class: "btn small", href: "#settings" }, "Set a daily search")));

  const top = h("div", { class: "card" }, h("div", { class: "spread" }, h("h3", {}, "Top matches to review"), h("a", { href: "#jobs", class: "small" }, "All jobs →")),
    s.top_jobs.length
      ? h("ul", { class: "job-list" }, s.top_jobs.map((j) => h("li", { on: { click: () => openJob(j.id) }, tabindex: "0" },
          scoreBadge(j.score),
          h("div", { class: "grow" }, h("div", { class: "role-title" }, j.title), h("div", { class: "role-sub" }, [j.company, j.location].filter(Boolean).join(" · "))),
          j.verdict ? h("span", { class: "hide-sm" }, verdictPill(j.verdict)) : null)))
      : h("p", { class: "muted" }, "Nothing to review. New jobs from your next search will appear here."));
  top.querySelectorAll(".job-list li").forEach((li, i) => li.addEventListener("keydown", (e) => { if (e.key === "Enter") openJob(s.top_jobs[i].id); }));

  const setup = h("div", { class: "card" }, h("h3", {}, "Setup"),
    h("dl", { class: "facts" },
      h("dt", {}, "AI"), h("dd", {}, st.ai_provider ? `${st.ai_provider === "gemini" ? "Gemini" : "Claude"} · ${st.ai_model}` : pill("Off", "warn")),
      h("dt", {}, "Sources"), h("dd", {}, st.default_sources.join(", ")),
      h("dt", {}, "Companies"), h("dd", {}, `${st.companies} careers page${st.companies === 1 ? "" : "s"}`),
      h("dt", {}, "Visa hours"), h("dd", {}, `${free} of ${st.visa_cap} h/fortnight free`)),
    h("p", { class: "muted small muted small mt-12 m-0" }, "Keys live in your .env file and are never shown here."));

  mount(
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Dashboard"), h("p", { class: "muted" }, "Your job search at a glance."))),
    ...setupAlerts(s),
    h("div", { class: "grid kpis" },
      kpi("New to review", c.by_status.seen, "Not applied or skipped yet"),
      kpi("Strong matches", c.strong, "Scored 70 or more"),
      kpi("Applied", c.by_status.applied, c.by_status.skipped ? `${c.by_status.skipped} skipped` : null),
      kpi("Cover letters ready", c.drafts, c.avg_score != null ? `Average match ${c.avg_score}` : null)),
    h("div", { class: "grid two" }, lastCard, nextCard),
    h("div", { class: "grid main-side" }, top, setup),
  );
}

// ---------- jobs ----------
async function renderJobs() {
  const f = state.jobFilters;
  const params = new URLSearchParams(Object.entries(f).filter(([, v]) => v !== ""));
  const set = (key) => (e) => { f[key] = e.target.value; renderJobs(); };
  let searchTimer;

  const filters = h("div", { class: "filters" },
    h("input", { type: "search", placeholder: "Search title, company or location", value: f.q, "aria-label": "Search jobs",
      on: { input: (e) => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { f.q = e.target.value; renderJobs().then(() => { const i = $(".filters input"); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }); }, 300); } } }),
    select("Status", f.status, [["", "All statuses"], ["seen", "New"], ["applied", "Applied"], ["skipped", "Skipped"]], set("status")),
    select("Hours check", f.verdict, [["", "Any hours"], ...Object.entries(VERDICTS).map(([k, [l]]) => [k, l])], set("verdict")),
    select("Minimum score", f.min_score, [["", "Any score"], ["50", "50+"], ["70", "70+"], ["85", "85+"]], set("min_score")),
    select("Work mode", f.work_mode, [["", "Any work mode"], ...Object.entries(MODE_LABEL), ["unknown", `Mode ${NOT_STATED.toLowerCase()}`]], set("work_mode")),
    select("Pay", f.pay, [["", "Paid or unpaid"], ...Object.entries(PAY_LABEL), ["unknown", `Pay ${NOT_STATED.toLowerCase()}`]], set("pay")),
    select("Job type", f.job_type, [["", "Any job type"], ...Object.entries(TYPE_LABEL), ["unknown", `Type ${NOT_STATED.toLowerCase()}`]], set("job_type")),
    select("Sort", f.sort, [["score", "Best match"], ["recent", "Newest"], ["updated", "Recently updated"]], set("sort")),
  );

  let data;
  try { data = await api(`/api/jobs?${params}`); }
  catch (e) { return mount(h("div", { class: "alert bad" }, h("strong", {}, "Couldn't load jobs. "), e.message)); }
  if (currentView() !== "jobs") return;

  const runChip = f.run_id ? h("div", { class: "row mb-12" }, pill(`From run #${f.run_id}`, "accent"),
    h("button", { class: "btn small", type: "button", on: { click: () => { f.run_id = ""; renderJobs(); } } }, "Clear")) : null;

  const body = data.jobs.length
    ? h("div", { class: "table-wrap" }, h("table", { class: "jobs-table" },
        h("thead", {}, h("tr", {}, h("th", {}, "Score"), h("th", {}, "Role"), h("th", { class: "hide-sm" }, "Location"), h("th", {}, "Hours check"), h("th", {}, "Status"), h("th", { class: "hide-sm" }, "Found"))),
        h("tbody", {}, data.jobs.map((j) => {
          const tr = h("tr", { class: "clickable" + (j.id === state.openJobId ? " selected" : ""), tabindex: "0", on: { click: () => openJob(j.id), keydown: (e) => { if (e.key === "Enter") openJob(j.id); } } },
            h("td", {}, scoreBadge(j.score)),
            h("td", {}, h("div", { class: "role-title" }, j.title || "Untitled"),
              h("div", { class: "role-sub" }, [j.company, detailText(j)].filter(Boolean).join(" · ")),
              j.has_draft || j.has_resume ? h("div", { class: "role-sub" }, h("span", { class: "doc-flag" }, [j.has_draft && "Cover letter", j.has_resume && "Tailored resume"].filter(Boolean).join(" + ") + " ready")) : null),
            h("td", { class: "hide-sm" }, j.location || "–"),
            h("td", {}, verdictPill(j.verdict)),
            h("td", {}, statusPill(j.status)),
            h("td", { class: "hide-sm muted small", title: fmtDate(j.first_seen) }, ago(j.first_seen)));
          return tr;
        }))))
    : h("div", { class: "card empty" }, h("h2", {}, "No jobs match these filters"), h("p", {}, f.status === "seen" ? "You're all caught up. Run a new search or change the filters." : "Try different filters."));

  mount(
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Jobs"), h("p", { class: "muted" }, `${data.jobs.length} job${data.jobs.length === 1 ? "" : "s"}. Click one for details, the cover letter and a tailored resume.`))),
    filters, runChip, body);
}

function select(label, value, options, onChange) {
  return h("select", { "aria-label": label, on: { change: onChange } },
    options.map(([v, l]) => h("option", { value: v, selected: v === value }, l)));
}

// ---------- job drawer ----------
async function openJob(id) {
  state.openJobId = id;
  let job;
  try { job = (await api(`/api/job?id=${encodeURIComponent(id)}`)).job; }
  catch (e) { return toast(e.message, true); }

  const statusBtn = (status, label, cls) => h("button", {
    class: `btn small ${cls}${job.status === status ? " on" : ""}`, type: "button",
    on: { click: async () => {
      try {
        await api("/api/job/status", { method: "POST", body: { id: job.id, status } });
        toast(status === "applied" ? "Marked as applied" : status === "skipped" ? "Skipped" : "Moved back to New");
        openJob(job.id);
        refreshQuiet();
      } catch (e) { toast(e.message, true); }
    } },
  }, label);

  const fileBase = `${job.company || "Company"} - ${job.title || "Job"}`.slice(0, 80);
  const writeBtn = (kind, label) => h("button", { class: "btn small", type: "button", on: { click: async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = "Writing…";
    try {
      await api("/api/job/write", { method: "POST", body: { id: job.id, kind } });
      toast(kind === "resume" ? "Tailored resume ready" : "Cover letter ready");
      if (state.openJobId === job.id) openJob(job.id);
      refreshQuiet();
    } catch (err) {
      toast(err.message, true);
      btn.disabled = false;
      btn.textContent = label;
    }
  } } }, label);
  const docTools = (text, what, file) => [
    h("button", { class: "btn small", type: "button", on: { click: () => copyText(text, what) } }, "Copy"),
    h("button", { class: "btn small", type: "button", on: { click: () => downloadText(file, text) } }, "Download .txt"),
  ];

  const meta = [
    job.work_mode ? pill(MODE_LABEL[job.work_mode] || job.work_mode, "info") : null,
    job.employment_type ? pill(TYPE_LABEL[job.employment_type] || job.employment_type) : null,
    job.pay ? pill(PAY_LABEL[job.pay] || job.pay, job.pay === "unpaid" ? "warn" : "good") : null,
    job.salary_text ? pill(job.salary_text, "good") : null,
    job.category ? pill(job.category) : null,
    job.source ? pill(job.source) : null,
    statusPill(job.status),
  ];

  $("#drawer-body").replaceChildren(
    h("div", { class: "row drawer-head" },
      scoreBadge(job.score, true),
      h("div", { class: "grow" },
        h("h2", { id: "drawer-title", class: "drawer-title" }, job.title || "Untitled"),
        h("div", { class: "muted" }, [job.company, job.location].filter(Boolean).join(" · ")))),
    h("div", { class: "chips mt-12" }, meta),
    h("div", { class: "row mt-16" },
      safeUrl(job.url) ? h("a", { class: "btn primary small", href: job.url, target: "_blank", rel: "noopener noreferrer" }, "Open job ad ↗") : null,
      statusBtn("applied", "Applied", "good"),
      statusBtn("skipped", "Skip", "bad"),
      job.status !== "seen" ? statusBtn("seen", "Move back to New", "") : null),

    job.reasons.length ? h("section", {}, h("h3", {}, "Why it fits"), h("ul", { class: "plain" }, job.reasons.map((r) => h("li", {}, r)))) : null,
    job.missing_skills.length ? h("section", {}, h("h3", {}, "Missing skills"), h("div", { class: "chips" }, job.missing_skills.map((m) => pill(m, "warn")))) : null,
    job.verdict ? h("section", {}, h("h3", {}, "Student visa check"),
      h("div", { class: "stack" }, h("div", {}, verdictPill(job.verdict)), job.verdict_note ? h("div", { class: "small" }, job.verdict_note) : null,
        job.positives.length ? h("div", { class: "small muted" }, "Student-friendly signs: ", job.positives.join(", ")) : null,
        h("div", { class: "small muted" }, "Read from the ad's text. Always check the ad and your visa conditions."))) : null,
    h("section", {},
      h("div", { class: "spread" }, h("h3", {}, "Cover letter"),
        h("div", { class: "row" }, job.draft ? docTools(job.draft, "Cover letter", `Cover letter - ${fileBase}.txt`) : null,
          writeBtn("cover_letter", job.draft ? "Rewrite" : "Write cover letter"))),
      job.draft ? h("div", { class: "cover-note" }, job.draft)
        : h("p", { class: "muted small" }, job.draft_error || "No cover letter yet. Each run writes them for the top matches, or write one now.")),
    h("section", {},
      h("div", { class: "spread" }, h("h3", {}, "Resume tailored to this job"),
        h("div", { class: "row" }, job.tailored_resume ? docTools(job.tailored_resume, "Resume", `Resume - ${fileBase}.txt`) : null,
          writeBtn("resume", job.tailored_resume ? "Rewrite" : "Tailor my resume"))),
      job.tailored_resume
        ? h("div", { class: "stack" },
            job.tailored_warnings.length
              ? h("div", { class: "alert" }, h("div", {}, h("strong", {}, "Check before sending. "), h("ul", { class: "plain" }, job.tailored_warnings.map((w) => h("li", {}, w)))))
              : null,
            h("div", { class: "cover-note" }, job.tailored_resume))
        : h("p", { class: "muted small" }, "Reorders and rewords your resume for this ad, using only what's already in it. Anything that looks new is flagged for you to check.")),
    job.draft || job.tailored_resume ? h("p", { class: "muted small" }, "Written to the humanizer rules (no dashes, no stock AI phrases). Always read it through before you send it.") : null,
    job.description ? h("section", {}, h("h3", {}, "From the ad"), h("div", { class: "description" }, job.description)) : null,
    h("section", {}, h("dl", { class: "facts small" },
      h("dt", {}, "First found"), h("dd", {}, fmtDate(job.first_seen)),
      h("dt", {}, "Last seen"), h("dd", {}, fmtDate(job.last_seen || job.updated)),
      h("dt", {}, "Job ID"), h("dd", { class: "mono" }, job.id))),
  );
  const drawer = $("#drawer");
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
  $(".drawer-close").focus();
}

function closeDrawer() {
  const drawer = $("#drawer");
  drawer.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
  state.openJobId = null;
  document.querySelectorAll("tr.selected").forEach((tr) => tr.classList.remove("selected"));
}

async function refreshQuiet() {
  try { state.summary = await api("/api/summary"); } catch { /* ignore */ }
  if (currentView() === "jobs") renderJobs();
  if (currentView() === "dashboard") renderDashboard();
}

// ---------- runs ----------
async function renderRuns() {
  let data;
  try { data = await api("/api/runs"); }
  catch (e) { return mount(h("div", { class: "alert bad" }, h("strong", {}, "Couldn't load runs. "), e.message)); }
  if (currentView() !== "runs") return;
  const detail = h("div", { id: "run-detail" });

  const table = data.runs.length
    ? h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "#"), h("th", {}, "Started"), h("th", {}, "Trigger"), h("th", {}, "Status"),
          h("th", { class: "num" }, "Found"), h("th", { class: "num hide-sm" }, "Matched"), h("th", { class: "num" }, "New"),
          h("th", { class: "num hide-sm" }, "Scored"), h("th", { class: "num hide-sm" }, "Notes"), h("th", { class: "hide-sm" }, "Took"))),
        h("tbody", {}, data.runs.map((r) => h("tr", { class: "clickable", tabindex: "0", on: { click: () => showRun(r.id, detail), keydown: (e) => { if (e.key === "Enter") showRun(r.id, detail); } } },
          h("td", { class: "muted" }, r.id),
          h("td", { title: fmtDate(r.started) }, fmtDate(r.started)),
          h("td", {}, TRIGGERS[r.trigger] || r.trigger || "–"),
          h("td", {}, pill(...(RUN_STATUS[r.status] || [r.status, ""]))),
          h("td", { class: "num" }, r.fetched ?? "–"),
          h("td", { class: "num hide-sm" }, r.matched ?? "–"),
          h("td", { class: "num" }, r.shown ?? "–"),
          h("td", { class: "num hide-sm" }, r.scored ?? "–"),
          h("td", { class: "num hide-sm" }, r.drafted ?? "–"),
          h("td", { class: "hide-sm muted" }, duration(r.started, r.finished)))))))
    : h("div", { class: "card empty" }, h("h2", {}, "No searches yet"), h("p", {}, "Every search you run, from here, the schedule or the command line, shows up here."));

  mount(
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Run history"), h("p", { class: "muted" }, "Every search, with what it found and its log. Click a run for details."))),
    table, detail);
  if (data.runs.length) showRun(data.runs[0].id, detail, false);
}

async function showRun(id, container, scroll = true) {
  let run;
  try { run = (await api(`/api/runs/${id}`)).run; } catch (e) { return toast(e.message, true); }
  const p = run.params || {};
  const settingsLine = [
    p.keywords && `Keywords: ${p.keywords}`, p.location && `Location: ${p.location}`,
    p.part_time && "Part-time mode", p.fits_only && "Fits hours only", p.term_break && "Course break",
    p.sources && `Sources: ${p.sources}`,
  ].filter(Boolean).join(" · ");
  container.replaceChildren(h("div", { class: "card mt-16" },
    h("div", { class: "spread" }, h("h2", {}, `Run #${run.id}`),
      run.shown ? h("button", { class: "btn small", type: "button", on: { click: () => { state.jobFilters = { ...state.jobFilters, run_id: String(run.id), status: "" }; location.hash = "#jobs"; } } }, `View its ${run.shown} job${run.shown === 1 ? "" : "s"}`) : null),
    h("div", { class: "mt-12" }, runFacts(run)),
    settingsLine ? h("p", { class: "muted small" }, settingsLine) : null,
    h("h3", { class: "log-head" }, "Log"),
    h("div", { class: "log" }, run.log || (run.status === "running" ? "Running…" : "No messages."))));
  if (scroll) container.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---------- settings ----------
async function renderSettings() {
  let s;
  try { s = (await api("/api/settings")).settings; }
  catch (e) { return mount(h("div", { class: "alert bad" }, h("strong", {}, "Couldn't load settings. "), e.message)); }
  if (currentView() !== "settings") return;
  const setup = state.summary ? state.summary.setup : null;

  const text = (key, label, help, attrs = {}) => h("div", { class: "field" + (attrs.full ? " full" : "") },
    h("label", { for: `f-${key}` }, label), h("input", { id: `f-${key}`, name: key, type: "text", value: s[key] ?? "", ...attrs, full: null }), help ? h("span", { class: "help" }, help) : null);
  const num = (key, label, help, min, max) => h("div", { class: "field" },
    h("label", { for: `f-${key}` }, label), h("input", { id: `f-${key}`, name: key, type: "number", min, max, value: s[key] }), help ? h("span", { class: "help" }, help) : null);
  const check = (key, label, help) => h("label", { class: "check" },
    h("input", { type: "checkbox", name: key, checked: !!s[key] }), h("span", {}, h("strong", {}, label), help ? h("span", { class: "help muted small" }, help) : null));

  const chosen = new Set((s.sources || "").split(",").map((x) => x.trim()).filter(Boolean));
  const sourceInfo = { companies: "Company careers pages", adzuna: "Adzuna (AU job boards)", jooble: "Jooble", remoteok: "RemoteOK (remote)" };
  const sourceBoxes = h("div", { class: "days" }, Object.entries(sourceInfo).map(([k, l]) =>
    h("label", {}, h("input", { type: "checkbox", name: "source", value: k, checked: chosen.has(k) }), l)));

  const group = (key, label, options) => h("div", { class: "field" }, h("span", { class: "label" }, label),
    h("div", { class: "days" }, Object.entries(options).map(([v, l]) =>
      h("label", {}, h("input", { type: "checkbox", name: key, value: v, checked: (s[key] || []).includes(v) }), l))));

  const dayNames = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const days = h("div", { class: "days" }, dayNames.map((d, i) =>
    h("label", {}, h("input", { type: "checkbox", name: "day", value: String(i), checked: (s.schedule_days || []).includes(i) }), d)));

  const form = h("form", { on: { submit: saveSettings }, novalidate: true },
    h("div", { class: "card settings-section" }, h("h3", {}, "What to search for"),
      h("div", { class: "form-grid" },
        text("keywords", "Keywords", "Comma separated. A job matches if it has any of them as a whole word.", { full: true }),
        text("location", "Location", "A city or suburb, or “remote”."),
        h("div", { class: "field" }, h("span", { class: "label" }, "Sources"), sourceBoxes, h("span", { class: "help" }, `Leave all unticked to use everything that's set up${setup ? ` (now: ${setup.default_sources.join(", ")})` : ""}.`)))),
    h("div", { class: "card settings-section" }, h("h3", {}, "Kind of job"),
      h("div", { class: "stack" },
        group("work_modes", "Work mode", { ...MODE_LABEL, unknown: NOT_STATED }),
        group("pays", "Pay", { ...PAY_LABEL, unknown: NOT_STATED }),
        group("job_types", "Job type", { ...TYPE_LABEL, unknown: NOT_STATED }),
        h("span", { class: "help muted small" }, "Leave a row all unticked to allow anything. “Not stated” keeps ads that don't say. Unpaid roles usually don't count toward your visa hours, but check the conditions."))),
    h("div", { class: "card settings-section" }, h("h3", {}, "Student visa mode"),
      h("div", { class: "stack" },
        check("part_time", "Part-time, casual and internships only", "Checks each job's hours against your free visa hours (set COMMITTED_FORTNIGHT_HOURS in .env)."),
        check("fits_only", "Only show jobs that clearly fit my free hours", null),
        check("term_break", "I'm on a scheduled course break", "Turns the hours cap off. Untick when term starts."),
        check("show_blocked", "Include jobs needing citizenship, PR, clearance or full work rights", null))),
    h("div", { class: "card settings-section" }, h("h3", {}, "AI scoring and documents"),
      h("div", { class: "form-grid" },
        text("resume", "Resume file", "A .txt file in the job-agent folder. It's sent to the AI, so keep your phone number and address out."),
        num("limit", "Jobs to save per run", "1 to 100.", 1, 100),
        num("max_score", "Jobs to score with AI", "Each one uses a free-tier request. 0 turns scoring off.", 0, 100),
        num("draft_top", "Cover letters per run", "Written for the best matches. 0 to 20.", 0, 20))),
    h("div", { class: "card settings-section" }, h("h3", {}, "Schedule"),
      h("div", { class: "stack" },
        check("schedule_enabled", "Run a search automatically", "Runs while this dashboard is open. If it's closed at that time, it runs when you next open it."),
        h("div", { class: "form-grid" },
          h("div", { class: "field" }, h("label", { for: "f-time" }, "Time"), h("input", { id: "f-time", name: "schedule_time", type: "time", value: s.schedule_time })),
          h("div", { class: "field" }, h("span", { class: "label" }, "Days"), days)))),
    h("div", { class: "sticky-save" }, h("button", { class: "btn primary", type: "submit" }, "Save settings"), h("span", { class: "muted small", id: "save-note" })),
  );

  mount(h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Settings"), h("p", { class: "muted" }, "Used by “Run search now” and the schedule. API keys stay in your .env file."))), form);
}

async function saveSettings(e) {
  e.preventDefault();
  const f = e.target;
  const val = (n) => f.elements[n].value;
  const intVal = (n) => Number.parseInt(val(n), 10);
  const body = {
    keywords: val("keywords"), location: val("location"), resume: val("resume"),
    sources: [...f.querySelectorAll("input[name=source]:checked")].map((i) => i.value).join(","),
    part_time: f.elements.part_time.checked, fits_only: f.elements.fits_only.checked,
    term_break: f.elements.term_break.checked, show_blocked: f.elements.show_blocked.checked,
    limit: intVal("limit"), max_score: intVal("max_score"), draft_top: intVal("draft_top"),
    schedule_enabled: f.elements.schedule_enabled.checked, schedule_time: val("schedule_time"),
    schedule_days: [...f.querySelectorAll("input[name=day]:checked")].map((i) => Number(i.value)),
  };
  for (const key of ["work_modes", "pays", "job_types"]) body[key] = [...f.querySelectorAll(`input[name=${key}]:checked`)].map((i) => i.value);
  try {
    await api("/api/settings", { method: "PUT", body });
    toast("Settings saved");
    $("#save-note").textContent = `Saved ${new Date().toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}`;
    refreshQuiet().then(() => state.summary && renderRunStatus());
  } catch (err) { toast(err.message, true); }
}

// ---------- start ----------
window.addEventListener("hashchange", () => { closeDrawer(); render(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
document.querySelectorAll("[data-close]").forEach((el) => el.addEventListener("click", closeDrawer));
$("#run-now").addEventListener("click", runNow);
refreshSummary().then(render);
