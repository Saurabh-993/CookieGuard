/* ==========================================================================
   CookieGuard — Dashboard behaviour (Phase 4)
   ==========================================================================

   WHAT THIS FILE DOES
   -------------------
   Everything that happens after the page loads:

       1. Ask the API for data          (fetch)
       2. Turn that JSON into HTML      (DOM manipulation)
       3. Draw charts from it           (D3)
       4. React to clicks and typing    (event listeners)

   index.html contains empty tables on purpose. This file fills them.

   'use strict' opts into stricter JavaScript rules — for example, assigning to
   an undeclared variable becomes an error instead of silently creating a
   global. It catches a whole class of typo bugs.
   ========================================================================== */

'use strict';


/* ==========================================================================
   1. CONFIGURATION AND STATE
   ========================================================================== */

/*
  WHERE IS THE API?

  If this page is served BY FastAPI (http://127.0.0.1:8000/dashboard/), then
  the API is on the same origin and a relative path works — no CORS involved
  at all.

  If you opened index.html straight from disk (file://...), there is no server
  to be relative to, so we must point at the API explicitly. That IS a
  cross-origin request, which is exactly why the API enables CORS.

  `window.location.protocol` tells us which situation we're in.
*/
const API_BASE = window.location.protocol === 'file:'
  ? 'http://127.0.0.1:8000'
  : window.location.origin;

/*
  STATE — everything the UI needs to remember.

  Kept in ONE object rather than scattered across separate variables. When
  something looks wrong you inspect one thing, and it's obvious what the UI
  depends on. (This is the same problem React's useState solves; with a UI
  this small, a plain object is enough.)
*/
const state = {
  domains: [],            // from GET /api/domains
  scans: [],              // scans for the selected domain
  currentScan: null,      // the full scan being shown in the inventory
  currentReport: null,    // the report being shown
  selectedDomain: null,
  cookieFilter: 'all',    // which category chip is active
  cookieSearch: '',       // text in the search box
  health: null,           // last /health response, shown on Settings
};

/*
  Read the category colours OUT of the CSS.

  `getComputedStyle(document.documentElement)` gives us the resolved styles of
  the <html> element, where our :root variables live.

  WHY BOTHER: the colours are defined once in style.css. The tables and chips
  use them via CSS; the D3 charts read the same values here. Change a colour in
  one place and everything updates together. Hardcoding hex codes here would
  guarantee the charts drift out of sync with the tables eventually.
*/
const cssVar = (name) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/*
  ⚠ `let`, not `const`, and refreshed on every theme change.

  The dark theme redefines --marketing, --analytics and so on. If we computed
  this object once at load and never again, the charts would keep light-theme
  colours after switching to dark — bright bars on a near-black background.

  `refreshColors()` is called from applyTheme() before anything redraws.
*/
let CATEGORY_COLORS = {};

function refreshColors() {
  CATEGORY_COLORS = {
    necessary:  cssVar('--necessary'),
    functional: cssVar('--functional'),
    analytics:  cssVar('--analytics'),
    marketing:  cssVar('--marketing'),
    unknown:    cssVar('--unknown'),
  };
}
refreshColors();

const CATEGORY_ORDER = ['necessary', 'functional', 'analytics', 'marketing', 'unknown'];

// GDPR transfer-risk regions, ordered least to most concerning.
const REGION_COLORS = () => ({
  'EEA':        cssVar('--region-eea'),
  'Adequate':   cssVar('--region-adequate'),
  'US (DPF)':   cssVar('--region-dpf'),
  'Restricted': cssVar('--region-restricted'),
});


/* ==========================================================================
   1b. THEME (dark / light)
   ==========================================================================

   HOW IT WORKS
   ------------
   We set one attribute on <html>:

       document.documentElement.setAttribute('data-theme', 'dark');

   CSS does the rest, because `[data-theme="dark"]` redefines the colour
   variables and every rule already reads them via var(). No class juggling on
   individual elements, no second stylesheet.

   THE THREE-WAY PREFERENCE
   ------------------------
       1. an explicit saved choice        (localStorage)   ← always wins
       2. otherwise, the OS setting       (prefers-color-scheme)
       3. otherwise, light

   Respecting the OS default matters: someone who runs their machine in dark
   mode shouldn't be flashbanged on first visit. But once they click the
   toggle, that choice must persist and override the system — which is why we
   only set the attribute when there IS an explicit choice, letting the CSS
   media query handle case 2.

   WHY localStorage
   ----------------
   A key-value store in the browser that survives closing the tab. Note the
   irony, and be ready for it in an interview: localStorage is itself a
   tracking-capable technology. Ours holds one string ('dark'), is
   first-party, and stores nothing about the person — so it's a functional
   preference. Under our own classifier it would be `functional`, and on a
   real site it would require consent.
*/

const THEME_KEY = 'cookieguard-theme';

function applyTheme(theme) {
  if (theme) {
    document.documentElement.setAttribute('data-theme', theme);
  } else {
    // Removing the attribute hands control back to the OS media query.
    document.documentElement.removeAttribute('data-theme');
  }
  refreshColors();          // pick up the new theme's variable values
  const btn = $('#theme-toggle');
  const icon = $('#theme-icon');
  if (btn && icon) {
    const isDark = resolvedTheme() === 'dark';
    // Swap the SVG's path data rather than the whole element, so the button
    // keeps its focus state and no layout shift occurs.
    icon.innerHTML = isDark
      ? `<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41
         M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41
         M19.07 4.93l-1.41 1.41"/>`
      : `<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>`;
    btn.title = isDark ? 'Switch to light mode' : 'Switch to dark mode';
  }
}

/** What theme are we actually showing right now, explicit or inherited? */
function resolvedTheme() {
  const explicit = document.documentElement.getAttribute('data-theme');
  if (explicit) return explicit;
  // matchMedia lets JS ask the same question a CSS media query asks.
  return window.matchMedia('(prefers-color-scheme: dark)').matches
    ? 'dark' : 'light';
}

function initTheme() {
  let saved = null;
  try {
    saved = localStorage.getItem(THEME_KEY);
  } catch (e) {
    /* localStorage can throw in private mode or if storage is disabled.
       A missing theme preference must never break the dashboard. */
  }
  applyTheme(saved);

  $('#theme-toggle').onclick = () => {
    const next = resolvedTheme() === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    try { localStorage.setItem(THEME_KEY, next); } catch (e) { /* ignore */ }

    /*
      ⚠ CHARTS MUST BE REDRAWN.
      D3 reads colours from CSS at DRAW time and bakes them into SVG `fill`
      attributes. Those attributes don't re-evaluate when the variables change,
      so existing charts would keep their old colours — dark background,
      light-theme bars. Redrawing is the fix.

      This is the one real cost of reading theme colours in JavaScript rather
      than styling SVG purely with CSS classes.
    */
    if (state.currentReport) redrawCharts();
  };
}


/* ==========================================================================
   2. SMALL HELPERS
   ========================================================================== */

// `$` and `$$` are just short names for the two DOM lookup functions.
// querySelector takes a CSS selector — '#id', '.class', 'tag' — and returns
// the first match (or all matches, for querySelectorAll).
const $  = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

/*
  ⚠ SECURITY: escaping HTML.

  We build table rows as HTML strings. If a value contains '<script>', writing
  it straight into innerHTML would EXECUTE it. That's CROSS-SITE SCRIPTING
  (XSS) — the frontend equivalent of the SQL injection problem from §40.

  Cookie names and vendor strings come from scanned websites, which we do not
  control. An attacker could name a cookie `<img src=x onerror=alert(1)>` and
  our dashboard would run it.

  Converting < > & " into their harmless entity forms means the browser
  DISPLAYS the characters instead of interpreting them as markup.

  Same principle as parameterised SQL: keep data as data, never let it become
  code.
*/
function esc(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')     // must be first, or it double-escapes the others
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** "2026-07-31T08:43:07+00:00" -> "2026-07-31 08:43" */
function fmtDate(iso) {
  if (!iso) return '—';
  return String(iso).slice(0, 16).replace('T', ' ');
}

function fmtLifetime(days) {
  if (days === null || days === undefined) return 'session';
  if (days > 365) return `${(days / 365).toFixed(1)}y`;
  return `${days}d`;
}

function show(el, visible) {
  if (el) el.hidden = !visible;
}


/* ==========================================================================
   3. TALKING TO THE API
   ========================================================================== */

/*
  `fetch` makes an HTTP request and returns a PROMISE.

  A PROMISE represents a value that isn't ready yet — the network takes time.
  `await` pauses this function until it arrives, without freezing the browser.
  It is exactly the same idea as Python's async/await from §14, and it exists
  for the same reason: waiting on the network shouldn't block everything else.

      Without await:  fetch(...) → a Promise object, not your data
      With await:     fetch(...) → the actual response

  TWO STEPS, TWO AWAITS
  ---------------------
      const res  = await fetch(url);    // headers have arrived
      const data = await res.json();    // body downloaded AND parsed

  A common beginner bug is forgetting the second one and wondering why `data`
  is a Promise.

  ⚠ THE FETCH GOTCHA WORTH KNOWING:
  fetch does NOT throw on 404 or 500. It only rejects if the request couldn't
  be made at all (network down, DNS failure, CORS blocked). A 404 is a
  perfectly successful HTTP exchange as far as fetch is concerned — the server
  answered. So you MUST check `res.ok` yourself. Almost everyone gets caught by
  this once.
*/
async function api(path, options = {}) {
  const res = await fetch(API_BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });

  if (!res.ok) {
    // Try to read the API's error detail; fall back to the status text.
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch (e) {
      /* body wasn't JSON — keep the status text */
    }
    // Attaching the status lets callers distinguish 404 from 500.
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }

  // 204 No Content has no body — calling .json() on it would throw.
  if (res.status === 204) return null;

  return res.json();
}


/* ==========================================================================
   4. HEALTH CHECK
   ========================================================================== */

async function checkHealth() {
  const pill = $('#api-status');
  const text = $('#api-status-text');
  try {
    state.health = await api('/health');
    pill.className = 'status-pill status-live';
    text.textContent = 'Live';
    show($('#demo-banner'), false);
    show($('#global-error'), false);
    return true;
  } catch (err) {
    // Design B's demo-mode idea: never present sample data as if it were live.
    // Here there IS no sample data, so we say plainly that nothing is live.
    pill.className = 'status-pill status-offline';
    text.textContent = 'Offline';
    show($('#demo-banner'), true);
    $('#global-error-detail').textContent = err.message;
    show($('#global-error'), true);
    return false;
  }
}


/* ==========================================================================
   5. VIEW 1 — DOMAINS
   ========================================================================== */

async function loadDomains() {
  try {
    state.domains = await api('/api/domains');
  } catch (err) {
    state.domains = [];
    return;
  }
  renderDomains();
  populateDomainSelects();
}

function renderDomains() {
  renderDomainStats();

  const tbody = $('#domains-tbody');
  show($('#domains-empty'), state.domains.length === 0);

  const ICON_REPORT = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18"
      y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6"
      y1="20" x2="6" y2="14"/></svg>`;
  const ICON_LIST = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="8"
      y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8"
      y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3"
      y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>`;

  tbody.innerHTML = state.domains.map((d) => {
    const score = d.avg_score === null || d.avg_score === undefined
      ? null : Math.round(d.avg_score);
    const grade = score === null ? '—' : scoreToGrade(score);

    return `
      <tr data-domain="${esc(d.domain)}">
        <td>
          <div class="domain-cell">
            <span class="domain-name">${esc(d.domain)}</span>
          </div>
        </td>
        <td class="num">${d.scan_count}</td>
        <td class="num">${d.max_cookies ?? 0}</td>
        <td>
          ${score === null ? '<span class="muted">—</span>' : `
            <div class="score-cell">
              <div class="score-bar">
                <div class="score-bar-fill"
                     style="width:${score}%;background:${gradeColor(grade)}"></div>
              </div>
              <span class="score-value">${score}</span>
            </div>`}
        </td>
        <td>
          ${grade === '—' ? '<span class="muted">—</span>'
            : `<span class="grade-chip grade-chip-${esc(grade)}">${esc(grade)}</span>`}
        </td>
        <td class="muted" style="white-space:nowrap;font-size:.82rem">
          ${fmtDate(d.latest_scan)}
        </td>
        <td>
          <div class="row-actions">
            <button class="btn-mini" data-report="${esc(d.domain)}">
              ${ICON_REPORT} Report
            </button>
            <button class="btn-mini" data-inventory="${esc(d.domain)}">
              ${ICON_LIST} Inventory
            </button>
          </div>
        </td>
      </tr>`;
  }).join('');

  // Event delegation — one listener, survives every redraw (§59).
  tbody.onclick = (event) => {
    const report = event.target.closest('[data-report]');
    if (report) { openReport(report.dataset.report); return; }
    const inventory = event.target.closest('[data-inventory]');
    if (inventory) { openInventory(inventory.dataset.inventory); }
  };
}


/* ---- Portfolio-level stat tiles ----
   Design A's choice of metrics. Each answers a question an auditor actually
   asks, rather than just counting rows:

     "how much am I monitoring?"   → domains
     "how big is the problem?"     → cookies
     "how are we doing overall?"   → average score
     "what needs attention now?"   → domains below 60          ← the actionable one
*/
function renderDomainStats() {
  const domains = state.domains;
  const scored = domains.filter(
    (d) => d.avg_score !== null && d.avg_score !== undefined);

  const totalCookies = domains.reduce((sum, d) => sum + (d.max_cookies || 0), 0);
  const avg = scored.length
    ? Math.round(scored.reduce((sum, d) => sum + d.avg_score, 0) / scored.length)
    : null;
  const failing = scored.filter((d) => d.avg_score < 60).length;

  const avgColour = avg === null ? 'var(--text)'
    : avg >= 75 ? 'var(--necessary)'
    : avg >= 60 ? 'var(--analytics)'
    : 'var(--marketing)';

  const tiles = [
    { label: 'Domains monitored', value: domains.length },
    { label: 'Cookies in latest scans', value: totalCookies },
    { label: 'Average score',
      value: avg === null ? '—' : `${avg}/100`, colour: avgColour },
    { label: 'Domains below 60', value: failing,
      colour: failing ? 'var(--marketing)' : 'var(--necessary)',
      hint: failing
        ? 'Scores under 60 usually mean trackers fire before consent.'
        : 'No domain is currently below the threshold.' },
  ];

  $('#domain-stats').innerHTML = tiles.map((t) => `
    <div class="stat">
      <div class="stat-label">${esc(t.label)}</div>
      <div class="stat-value" style="color:${t.colour || 'var(--text)'}">${esc(t.value)}</div>
      ${t.hint ? `<div class="stat-hint">${esc(t.hint)}</div>` : ''}
    </div>`).join('');
}


function scoreToGrade(score) {
  if (score === '—' || score === null) return '—';
  if (score >= 90) return 'A';
  if (score >= 75) return 'B';
  if (score >= 60) return 'C';
  if (score >= 40) return 'D';
  return 'F';
}

function gradeColor(grade) {
  return {
    A: cssVar('--grade-a'), B: cssVar('--grade-b'), C: cssVar('--grade-c'),
    D: cssVar('--grade-d'), F: cssVar('--grade-f'),
  }[grade] || cssVar('--unknown');
}


/* ==========================================================================
   6. RUNNING A SCAN
   ========================================================================== */

async function handleScanSubmit(event) {
  // Stop the browser's default behaviour for a form submit, which is to
  // navigate away and reload the page. We want to stay put and use fetch.
  event.preventDefault();

  const url  = $('#scan-url').value.trim();
  const wait = parseInt($('#scan-wait').value, 10);
  const acceptConsent = $('#scan-accept-consent').checked;
  if (!url) return;

  const btn     = $('#scan-btn');
  const btnText = $('#scan-btn-text');
  const spinner = $('#scan-spinner');
  const status  = $('#scan-status');

  /*
    THE LOADING STATE.

    A scan takes 5–30 seconds. Without feedback the user assumes it's broken
    and clicks again — which would start a second browser. Disabling the button
    prevents that, and the spinner shows the click registered.

    Communicating "I'm working" is not decoration; it prevents a real bug.
  */
  btn.disabled = true;
  show(spinner, true);
  btnText.textContent = 'Scanning…';
  status.className = 'scan-status alert-info';
  status.textContent = acceptConsent
    ? `Scanning ${url}, then clicking "Accept all" and scanning again… `
      + `this takes about ${wait * 2 + 20}s.`
    : `Opening a browser and loading ${url}… this takes ${wait + 10}s or so.`;
  show(status, true);

  try {
    const result = await api('/api/scan', {
      method: 'POST',
      body: JSON.stringify({
        url, wait_seconds: wait, save: true, accept_consent: acceptConsent,
      }),
    });

    const c = result.compliance || {};
    const d = result.consent_diff;
    status.className = 'scan-status alert-success';

    let message = `<strong>${esc(result.domain)}</strong> scanned — `
      + `score <strong>${c.score}/100 (${c.grade})</strong>. `
      + `${c.cookies_requiring_consent} cookies required consent but were `
      + `set before it.`;

    if (d) {
      message += `<br><br><strong>Accepting added ${d.added_count} more `
        + `cookies</strong> — ${d.pre_consent_count} before, `
        + `${d.post_consent_count} after`
        + (d.multiplier ? ` (${d.multiplier}× more tracking)` : '') + '.';
    }
    message += ` <button class="btn-link" id="goto-report">View report →</button>`;
    status.innerHTML = message;

    $('#goto-report').onclick = () => openReport(result.domain);
    $('#scan-url').value = '';

    await loadDomains();

  } catch (err) {
    status.className = 'scan-status alert-error';
    // err.status lets us give a much more useful message than "it failed".
    status.textContent = err.status === 400
      ? `Rejected: ${err.message}`
      : `Scan failed: ${err.message}`;
  } finally {
    // `finally` always runs — success or failure. Without it, a thrown error
    // would leave the button disabled forever and the UI stuck.
    btn.disabled = false;
    show(spinner, false);
    btnText.textContent = 'Scan';
  }
}


/* ==========================================================================
   7. VIEW 2 — COOKIE INVENTORY
   ========================================================================== */

async function openInventory(domain) {
  switchView('inventory');
  state.selectedDomain = domain;
  try {
    state.scans = await api(`/api/domains/${encodeURIComponent(domain)}/scans`);
  } catch (err) {
    state.scans = [];
    return;
  }
  populateScanSelect();
  if (state.scans.length) await loadScan(state.scans[0].id);
}

function populateScanSelect() {
  const sel = $('#inventory-scan-select');
  sel.innerHTML = state.scans.map((s) =>
    `<option value="${s.id}">
       ${esc(state.selectedDomain)} — ${fmtDate(s.scanned_at)} (${s.cookie_count} cookies)
     </option>`
  ).join('');
  sel.onchange = () => loadScan(parseInt(sel.value, 10));
}

async function loadScan(scanId) {
  try {
    state.currentScan = await api(`/api/scans/${scanId}`);
  } catch (err) {
    return;
  }
  renderInventoryStats();
  renderCookies();
}

function renderInventoryStats() {
  const s = state.currentScan;
  if (!s) return;

  const tiles = [
    { label: 'Total cookies',   value: s.cookie_count },
    { label: 'Third-party',     value: s.third_party_cookies },
    { label: 'Need consent',    value: s.cookies_requiring_consent },
    { label: 'Score',           value: `${s.compliance_score ?? '—'}` },
    { label: 'Grade',           value: s.compliance_grade ?? '—' },
    { label: 'Domains contacted', value: (s.third_party_domains || []).length },
  ];

  $('#inventory-stats').innerHTML = tiles.map((t) => `
    <div class="stat">
      <div class="stat-value">${esc(t.value)}</div>
      <div class="stat-label">${esc(t.label)}</div>
    </div>`).join('');
}

function renderCookies() {
  const scan = state.currentScan;
  if (!scan) return;

  /*
    FILTERING.

    We filter a COPY in memory rather than re-requesting from the API. The data
    is already here; a round-trip would be slower and pointless. Typing in the
    search box feels instant because nothing touches the network.
  */
  const term = state.cookieSearch.toLowerCase();
  const rows = (scan.cookies || []).filter((c) => {
    const categoryOk = state.cookieFilter === 'all' || c.category === state.cookieFilter;
    if (!categoryOk) return false;
    if (!term) return true;
    return [c.name, c.vendor, c.domain]
      .filter(Boolean)
      .some((f) => String(f).toLowerCase().includes(term));
  });

  show($('#cookies-empty'), rows.length === 0);

  $('#cookies-tbody').innerHTML = rows.map((c) => {
    const flags = [];
    if (c.http_only) flags.push('<span class="pill">HttpOnly</span>');
    if (c.secure)    flags.push('<span class="pill">Secure</span>');
    // Third-party + SameSite=None is the clearest technical fingerprint of a
    // cross-site tracker, so we highlight that combination specifically.
    if (c.same_site === 'None' && c.party === 'third') {
      flags.push('<span class="pill pill-warn">SameSite=None</span>');
    }
    if ((c.lifetime_days || 0) > 400) {
      flags.push('<span class="pill pill-warn">&gt;13 months</span>');
    }

    return `
      <tr title="${esc(c.purpose || '')}">
        <td class="mono">${esc(c.name)}</td>
        <td><span class="badge badge-${esc(c.category || 'unknown')}">${esc(c.category)}</span></td>
        <td>${esc(c.vendor || '—')}</td>
        <td class="mono muted">${esc(c.domain)}</td>
        <td>${esc(c.party)}</td>
        <td>${esc(c.cookie_type || '—')}</td>
        <td class="num">${fmtLifetime(c.lifetime_days)}</td>
        <td>${flags.join(' ') || '—'}</td>
      </tr>`;
  }).join('');
}


/* ==========================================================================
   8. VIEW 3 — AUDIT REPORT
   ========================================================================== */

function populateDomainSelects() {
  const sel = $('#report-domain-select');
  sel.innerHTML = state.domains
    .map((d) => `<option value="${esc(d.domain)}">${esc(d.domain)}</option>`)
    .join('');
  sel.onchange = () => openReport(sel.value, false);
}

async function openReport(domain, switchTab = true) {
  if (switchTab) switchView('report');
  state.selectedDomain = domain;
  $('#report-domain-select').value = domain;

  try {
    state.currentReport = await api(`/api/report/${encodeURIComponent(domain)}`);
  } catch (err) {
    $('#report-summary').innerHTML =
      `<p class="empty-state">Could not load report: ${esc(err.message)}</p>`;
    return;
  }

  renderReportSummary();
  renderConsentDiff();
  renderUnknowns();
  renderFlowHeadline();
  renderSecurityGauges();

  // Charts last: they need the SVG elements to have their final size, which
  // only happens once the surrounding layout has been rendered.
  redrawCharts();
}

/** Redraw every chart. Called on load, tab switch, resize and theme change. */
function redrawCharts() {
  drawDonutChart();
  drawVendorChart();
  drawGlobe();             // async, but we don't await — it renders when ready
  drawJurisdictionChart();
  drawLifetimeChart();
  drawTreemap();
  drawHistoryChart();
}


/* ==========================================================================
   8b. PDF DOWNLOAD
   ========================================================================== */

/*
  DOWNLOADING A FILE FROM AN API CALL.

  The naive approach is `window.location = url`, which works but has a real
  problem: if the server returns an ERROR, the browser navigates away from
  your app to show it. The user loses their place, and you can't display a
  friendly message.

  So we fetch the file as a BLOB (binary data), then trigger the download
  ourselves. That way we can catch failures and keep the user where they are.

  The download itself uses a small trick: create an <a> element with the
  `download` attribute pointing at a temporary object URL, click it
  programmatically, then throw it away. There is no other API for "save this
  data as a file" — this is genuinely the standard approach.
*/
async function downloadReportPdf() {
  const domain = state.selectedDomain;
  if (!domain) return;

  const btn = $('#download-pdf');
  const text = $('#pdf-btn-text');
  const spinner = $('#pdf-spinner');

  btn.disabled = true;
  show(spinner, true);
  text.textContent = 'Generating…';

  try {
    const res = await fetch(
      `${API_BASE}/api/report/${encodeURIComponent(domain)}/pdf`
    );
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (e) { /* */ }
      throw new Error(detail);
    }

    // The raw bytes.
    const blob = await res.blob();

    // A temporary in-memory URL pointing at those bytes.
    const url = URL.createObjectURL(blob);

    const a = document.createElement('a');
    a.href = url;
    a.download = `cookieguard-${domain}.pdf`;   // the suggested filename
    document.body.appendChild(a);
    a.click();
    a.remove();

    // Release the memory. Object URLs are NOT garbage-collected automatically
    // — the browser holds the blob alive until you revoke the URL. Forgetting
    // this leaks memory on every download.
    URL.revokeObjectURL(url);

    text.textContent = '✓ Downloaded';
    setTimeout(() => { text.textContent = '⬇ Download PDF'; }, 2200);

  } catch (err) {
    text.textContent = '⬇ Download PDF';
    alert(`Could not generate the PDF.\n\n${err.message}`);
  } finally {
    btn.disabled = false;
    show(spinner, false);
  }
}


/* ==========================================================================
   8c. SCAN HISTORY PANEL
   ========================================================================== */

const historyState = {
  page: 0,
  pageSize: 20,
  total: 0,
  domain: '',
  grade: '',
  pendingDelete: null,     // the scan awaiting confirmation
};

async function loadHistory() {
  const params = new URLSearchParams({
    limit: historyState.pageSize,
    offset: historyState.page * historyState.pageSize,
  });
  // URLSearchParams handles escaping for us. Building a query string by
  // concatenation breaks the moment a value contains & or a space.
  if (historyState.domain) params.set('domain', historyState.domain);
  if (historyState.grade) params.set('grade', historyState.grade);

  let data;
  try {
    data = await api(`/api/scans?${params}`);
  } catch (err) {
    return;
  }

  historyState.total = data.total;
  renderHistoryTable(data.items);
}

function renderHistoryTable(scans) {
  const tbody = $('#history-tbody');
  show($('#history-empty-state'), scans.length === 0);

  $('#history-count').textContent =
    `${historyState.total} scan${historyState.total === 1 ? '' : 's'} recorded`;

  tbody.innerHTML = scans.map((s) => {
    const grade = s.compliance_grade || '—';
    // A compact category breakdown as coloured blocks — readable at a glance
    // without needing a legend on every row.
    const breakdown = CATEGORY_ORDER.map((cat) => {
      const n = s[`${cat}_count`] || 0;
      if (!n) return '';
      return `<span class="pill" style="background:${CATEGORY_COLORS[cat]};color:#fff"
                    title="${cat}">${n}</span>`;
    }).join('');

    return `
      <tr>
        <td class="muted">${fmtDate(s.scanned_at)}</td>
        <td><strong>${esc(s.domain)}</strong></td>
        <td class="num">${s.cookie_count}</td>
        <td>
          <span class="badge" style="background:${gradeColor(grade)}">
            ${s.compliance_score ?? '—'}/100 ${esc(grade)}
          </span>
        </td>
        <td class="num">${s.cookies_requiring_consent}</td>
        <td>${breakdown || '<span class="muted">—</span>'}</td>
        <td>
          <button class="btn-link" data-view-scan="${s.id}">View</button>
          <button class="btn-link" style="color:var(--marketing)"
                  data-delete-scan="${s.id}"
                  data-domain="${esc(s.domain)}"
                  data-when="${esc(fmtDate(s.scanned_at))}"
                  data-cookies="${s.cookie_count}">Delete</button>
        </td>
      </tr>`;
  }).join('');

  // Event delegation again — one listener, survives every redraw.
  tbody.onclick = (event) => {
    const viewBtn = event.target.closest('[data-view-scan]');
    if (viewBtn) {
      switchView('inventory');
      loadScanDirect(parseInt(viewBtn.dataset.viewScan, 10));
      return;
    }
    const delBtn = event.target.closest('[data-delete-scan]');
    if (delBtn) openDeleteModal(delBtn.dataset);
  };

  // ---- pager ----
  const pages = Math.ceil(historyState.total / historyState.pageSize);
  show($('#history-pager'), pages > 1);
  $('#history-page-label').textContent =
    `Page ${historyState.page + 1} of ${pages || 1}`;
  $('#history-prev').disabled = historyState.page === 0;
  $('#history-next').disabled = historyState.page >= pages - 1;
}

/** Load one scan into the inventory view, without needing its domain first. */
async function loadScanDirect(scanId) {
  try {
    state.currentScan = await api(`/api/scans/${scanId}`);
  } catch (err) {
    return;
  }
  state.selectedDomain = state.currentScan.domain;
  state.scans = await api(
    `/api/domains/${encodeURIComponent(state.currentScan.domain)}/scans`
  ).catch(() => []);
  populateScanSelect();
  $('#inventory-scan-select').value = String(scanId);
  renderInventoryStats();
  renderCookies();
}


/* ---- The delete confirmation flow ----
   Deletion is IRREVERSIBLE and CASCADES to every cookie row. A single
   mis-click must not be able to destroy audit history.

   The dialog names the SPECIFIC scan — domain, date, cookie count — rather
   than asking a generic "are you sure?". A confirmation you can dismiss
   without reading isn't a confirmation, it's a speed bump. */

function openDeleteModal(data) {
  historyState.pendingDelete = parseInt(data.deleteScan, 10);
  $('#delete-detail').innerHTML =
    `<strong>${esc(data.domain)}</strong> — scanned ${esc(data.when)},
     containing <strong>${esc(data.cookies)}</strong> cookie records.`;
  show($('#delete-modal'), true);
  // Focus Cancel, not Delete. The SAFE option should be the one that a stray
  // Enter keypress selects.
  $('#delete-cancel').focus();
}

function closeDeleteModal() {
  historyState.pendingDelete = null;
  show($('#delete-modal'), false);
}

async function confirmDelete() {
  const id = historyState.pendingDelete;
  if (!id) return;

  const btn = $('#delete-confirm');
  btn.disabled = true;
  btn.textContent = 'Deleting…';

  try {
    await api(`/api/scans/${id}`, { method: 'DELETE' });
    closeDeleteModal();
    await loadHistory();
    await loadDomains();     // counts and averages have changed
  } catch (err) {
    alert(`Could not delete the scan.\n\n${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Delete scan';
  }
}

function populateHistoryFilters() {
  const sel = $('#history-domain-filter');
  const current = sel.value;
  sel.innerHTML = '<option value="">All domains</option>' +
    state.domains.map((d) =>
      `<option value="${esc(d.domain)}">${esc(d.domain)}</option>`).join('');
  sel.value = current;      // preserve the selection across a refresh
}

function renderReportSummary() {
  const r = state.currentReport;
  const latest = r.latest_scan;
  if (!latest) {
    $('#report-summary').innerHTML = '<p class="empty-state">No scans yet.</p>';
    return;
  }

  const grade = latest.compliance_grade || 'F';
  const trendClass = {
    improving: 'trend-improving',
    worsening: 'trend-worsening',
    stable: 'trend-stable',
  }[r.trend] || 'trend-insufficient';

  $('#report-summary').innerHTML = `
    <div class="score-box grade-${esc(grade)}">
      <div class="score-number">${latest.compliance_score ?? '—'}</div>
      <div class="score-grade">GRADE ${esc(grade)}</div>
    </div>
    <div class="report-meta">
      <h3>${esc(r.domain)}</h3>
      <p class="verdict">
        <strong>${latest.cookies_requiring_consent}</strong> cookies that legally
        require consent were set <strong>before any consent was given</strong>.
      </p>
      <p class="muted small">
        ${r.stats.total_scans} scan(s) · average score
        ${r.stats.avg_score !== null ? r.stats.avg_score.toFixed(1) : '—'} ·
        last scanned ${fmtDate(r.last_scanned)}
      </p>
      <p style="margin-top:8px">
        <span class="trend ${trendClass}">Trend: ${esc(r.trend)}</span>
      </p>
    </div>`;
}

/* ---- The consent diff panel ----
   The headline finding: what accepting actually costs you.

   Shown only when the LATEST scan did a second pass. Older scans predate the
   feature and simply have nothing to show — so we hide the card entirely
   rather than render an empty one. */

function renderConsentDiff() {
  const card = $('#consent-diff-card');
  const latest = state.currentReport?.latest_scan;

  if (!latest || !latest.consent_clicked) {
    show(card, false);
    return;
  }
  show(card, true);

  const before = latest.pre_consent_count ?? 0;
  const after  = latest.post_consent_count ?? 0;
  const added  = latest.cookies_added_by_consent ?? 0;
  const mult   = latest.consent_multiplier;
  const verdict = latest.consent_verdict || 'unknown';

  const verdictText = {
    compliant: 'No non-necessary cookies were set before consent. '
             + 'This is the behaviour the law requires.',
    minor: 'A small number of non-necessary cookies were set before any '
         + 'consent was given.',
    major: 'Non-necessary cookies were set before any consent was given — '
         + 'tracking began before permission was obtained.',
  }[verdict] || 'Consent state could not be determined.';

  $('#consent-diff-body').innerHTML = `
    <div class="diff-compare">
      <div class="diff-side">
        <div class="diff-number" style="color:var(--necessary)">${before}</div>
        <div class="diff-label">Before consent</div>
        <div class="diff-sub">What loads if you touch nothing</div>
      </div>
      <div class="diff-arrow">
        <div class="diff-arrow-symbol">→</div>
        ${mult ? `<div class="diff-multiplier">${mult}× more</div>` : ''}
      </div>
      <div class="diff-side after">
        <div class="diff-number" style="color:var(--marketing)">${after}</div>
        <div class="diff-label">After "Accept all"</div>
        <div class="diff-sub">+${added} cookies unlocked</div>
      </div>
    </div>

    <div class="diff-verdict verdict-${esc(verdict)}">
      <strong>${esc(verdict.charAt(0).toUpperCase() + verdict.slice(1))}.</strong>
      ${verdictText}
    </div>

    <div style="display:flex;gap:10px;flex-wrap:wrap">
      <span class="consent-badge">
        Banner found via <strong>${esc(latest.consent_method || '—')}</strong>
      </span>
      ${latest.consent_detail
        ? `<span class="consent-badge">${esc(latest.consent_detail)}</span>` : ''}
    </div>

    <p class="muted small" style="margin-top:12px">
      The compliance score above is calculated on the <strong>pre-consent</strong>
      cookies only. Cookies that appear after an explicit "Accept all" are
      consented to, and scoring them would penalise a site for honouring
      consent correctly.
    </p>`;
}


function renderUnknowns() {
  const unknowns = state.currentReport.unknown_cookies || [];
  show($('#unknowns-empty'), unknowns.length === 0);
  $('#unknowns-tbody').innerHTML = unknowns.map((c) => `
    <tr>
      <td class="mono">${esc(c.name)}</td>
      <td class="mono muted">${esc(c.domain)}</td>
      <td>${esc(c.party)}</td>
    </tr>`).join('');
}


/* ==========================================================================
   9. D3 CHARTS
   ==========================================================================

   WHAT D3 IS
   ----------
   D3 = Data-Driven Documents. It is NOT a chart library — there is no
   `d3.pieChart()`. D3 is a toolkit for BINDING DATA TO DOM ELEMENTS and doing
   the maths that turns numbers into pixels. You draw the shapes yourself.

   That's why it's harder than Chart.js, and also why it can do anything.

   THE THREE IDEAS YOU NEED
   ------------------------

   1. SELECTIONS — like querySelectorAll, but chainable
          d3.select('#chart').append('g').attr('fill', 'red')

   2. THE DATA JOIN — the core idea, and the one that takes a moment
          svg.selectAll('rect')
             .data(myArray)
             .join('rect')          ← create one <rect> per array item
             .attr('width', d => d.value)

      You don't loop. You declare "there should be one rect per data item"
      and D3 creates, updates or removes elements to make that true.

   3. SCALES — functions that map DATA space to PIXEL space
          const y = d3.scaleLinear()
                      .domain([0, 100])    // possible data values
                      .range([200, 0]);    // pixel positions
          y(50)  →  100

      Note range is [200, 0] not [0, 200]. SVG's y-axis points DOWN — y=0 is
      the top. Flipping the range is what makes big values appear high up.
      Forgetting this produces upside-down charts, which is everyone's first
      D3 bug.

   THE MARGIN CONVENTION
   ---------------------
   Axis labels need room. The standard pattern is a margin object plus an inner
   <g> shifted by it, so all drawing code can use coordinates from (0,0)
   without thinking about the labels.
   ========================================================================== */

/*
  IS D3 ACTUALLY HERE?

  D3 loads from a CDN, so it needs internet access on first load. If you're
  offline, behind a strict corporate proxy, or the CDN is blocked, `d3` will be
  undefined — and every chart function would throw `d3 is not defined`, which
  in turn would abort whatever called it.

  DEGRADING GRACEFULLY: the tables, scores and scan form don't need D3 at all,
  so a missing chart library should cost you the charts and nothing else. We
  check once and show an honest message instead of letting an exception take
  down the rest of the page.

  This is the difference between "the dashboard is broken" and "the charts
  didn't load" — and it's a five-line check.
*/
function d3Available() {
  return typeof d3 !== 'undefined';
}

function chartUnavailable(svgId, message) {
  const svg = document.getElementById(svgId);
  if (!svg) return;
  svg.innerHTML = '';
  const note = document.createElement('p');
  note.className = 'empty-state';
  note.textContent = message;
  // insertAdjacentElement puts the message right after the (now empty) chart.
  svg.insertAdjacentElement('afterend', note);
}

/*
  chartFrame() — THE FIX FOR THE `<rect> width: negative value` CONSOLE ERRORS
  ---------------------------------------------------------------------------
  Every chart below did the same thing:

      const innerW = box.width - margin.left - margin.right;

  That is correct arithmetic and a latent bug. Two situations break it:

    1. The chart is inside a HIDDEN view (`display: none`), so
       getBoundingClientRect() returns width 0 → innerW = -170.
    2. The chart is on a narrow phone. The vendor chart reserves 130px on the
       left for labels and 40px on the right for values. In a 160px-wide
       container that is 170px of margin in 160px of space → innerW = -10.

  A negative range makes d3.scaleLinear() return negative pixels, which end up
  in `.attr('width', …)`. SVG rejects negative widths, so the browser logs an
  error PER RECT PER TRANSITION FRAME — which is why you saw 642 of them.

  This helper does three things, and the middle one is the interesting one:

    · returns null when the element is not laid out yet (draw nothing)
    · SCALES THE MARGINS DOWN when they don't fit, rather than clipping.
      A 130px label gutter is right at 700px wide and absurd at 300px. We
      keep the same PROPORTION of the box for labels instead of a fixed px.
    · guarantees innerW/innerH are at least 1

  General principle: any layout maths that subtracts constants from a measured
  size needs a floor. The measurement can always be smaller than you assumed.
*/
function chartFrame(svgSel, margin) {
  const node = svgSel.node();
  if (!node) return null;

  const box = node.getBoundingClientRect();
  const width  = box.width;
  const height = box.height;

  // Not rendered yet (hidden tab, or detached). Drawing now would produce
  // garbage geometry AND waste a transition, so bail out cleanly. The chart
  // is redrawn when the view becomes visible.
  if (width < 40 || height < 40) return null;

  const m = { ...margin };                 // copy: never mutate the caller's object
  const horizontal = m.left + m.right;

  // Leave at least 55% of the width for the actual data.
  const maxHorizontal = width * 0.45;
  if (horizontal > maxHorizontal) {
    const shrink = maxHorizontal / horizontal;
    m.left  = Math.max(28, Math.floor(m.left  * shrink));
    m.right = Math.max(12, Math.floor(m.right * shrink));
  }

  return {
    width, height, margin: m,
    innerW: Math.max(1, width  - m.left - m.right),
    innerH: Math.max(1, height - m.top  - m.bottom),
  };
}

/* Belt and braces: any value handed to an SVG width/height goes through this,
   so a future chart can never reintroduce the same console spam. */
const px = (n) => (Number.isFinite(n) && n > 0 ? n : 0);

const tooltip = () => $('#tooltip');

function showTooltip(event, html) {
  const el = tooltip();
  el.innerHTML = html;
  el.hidden = false;
  // +14 offsets it from the cursor so it doesn't sit under the pointer.
  el.style.left = (event.clientX + 14) + 'px';
  el.style.top  = (event.clientY + 14) + 'px';
}

function hideTooltip() {
  tooltip().hidden = true;
}


/* ---- CHART 1: donut, category breakdown -------------------------------- */

function drawDonutChart() {
  if (!d3Available()) {
    chartUnavailable('chart-donut', 'Charts need D3, which loads from a CDN. Check your internet connection.');
    return;
  }
  const svg = d3.select('#chart-donut');
  svg.selectAll('*').remove();          // clear any previous render

  const latest = state.currentReport.latest_scan;
  if (!latest) return;

  const data = CATEGORY_ORDER
    .map((cat) => ({ category: cat, value: latest[`${cat}_count`] || 0 }))
    .filter((d) => d.value > 0);        // don't draw empty slices

  if (!data.length) return;

  // getBoundingClientRect gives the element's REAL rendered size, which is
  // what we need because the CSS sets the width as a percentage.
  const box = svg.node().getBoundingClientRect();
  const width = box.width;
  const height = box.height;
  // A hidden tab measures 0×0, and `0/2 - 8` is a NEGATIVE radius — d3.arc()
  // then emits an invalid path. Bail out; the chart redraws when visible.
  if (width < 40 || height < 40) return;
  const radius = Math.min(width, height) / 2 - 8;

  // Move the origin to the centre — arcs are drawn around (0,0).
  const g = svg.append('g')
    .attr('transform', `translate(${width / 2}, ${height / 2})`);

  /*
    d3.pie() does the ARITHMETIC: it converts values into start/end angles that
    sum to a full circle. It draws nothing.
    d3.arc() then converts one of those angle pairs into an SVG path string.

    Separating "calculate" from "draw" is very D3.
  */
  const pie = d3.pie().value((d) => d.value).sort(null);
  const arc = d3.arc()
    .innerRadius(radius * 0.58)    // > 0 makes it a DONUT rather than a pie
    .outerRadius(radius);

  const arcHover = d3.arc()
    .innerRadius(radius * 0.58)
    .outerRadius(radius + 6);      // slightly bigger on hover

  const total = d3.sum(data, (d) => d.value);

  // THE DATA JOIN: one <path> per category.
  g.selectAll('path')
    .data(pie(data))
    .join('path')
      .attr('d', arc)
      .attr('fill', (d) => CATEGORY_COLORS[d.data.category])
      .attr('stroke', '#fff')
      .attr('stroke-width', 2)
      .style('cursor', 'pointer')
      // Arrow functions here take (event, d): d is the bound datum.
      .on('mousemove', function (event, d) {
        d3.select(this).transition().duration(120).attr('d', arcHover);
        const pct = ((d.data.value / total) * 100).toFixed(1);
        showTooltip(event, `
          <strong>${d.data.category}</strong>
          ${d.data.value} cookies (${pct}%)<br>
          ${d.data.category === 'necessary'
            ? 'Exempt from consent'
            : 'Consent required'}`);
      })
      .on('mouseleave', function () {
        d3.select(this).transition().duration(120).attr('d', arc);
        hideTooltip();
      });

  // Centre label — the number that actually matters.
  g.append('text')
    .attr('text-anchor', 'middle')
    .attr('dy', '-0.1em')
    .style('font-size', '1.9rem')
    .style('font-weight', '700')
    .style('fill', cssVar('--text'))
    .text(total);

  g.append('text')
    .attr('text-anchor', 'middle')
    .attr('dy', '1.4em')
    .style('font-size', '.72rem')
    .style('fill', cssVar('--text-muted'))
    .text('COOKIES');

  // Legend, built with plain DOM (no need for D3 here).
  $('#donut-legend').innerHTML = data.map((d) => `
    <div class="legend-item">
      <span class="legend-swatch" style="background:${CATEGORY_COLORS[d.category]}"></span>
      ${esc(d.category)} (${d.value})
    </div>`).join('');
}


/* ---- CHART 2: horizontal bars, top vendors ----------------------------- */

function drawVendorChart() {
  if (!d3Available()) return;
  const svg = d3.select('#chart-vendors');
  svg.selectAll('*').remove();

  const vendors = (state.currentReport.top_vendors || []).slice(0, 8);
  if (!vendors.length) return;

  // Left margin is generous because vendor names are long — but chartFrame()
  // shrinks it on narrow screens instead of letting it eat the whole chart.
  const frame = chartFrame(svg, { top: 8, right: 40, bottom: 24, left: 130 });
  if (!frame) return;
  const { margin, innerW, innerH } = frame;

  const g = svg.append('g')
    .attr('transform', `translate(${margin.left}, ${margin.top})`);

  /*
    TWO KINDS OF SCALE:

      scaleLinear   continuous numbers → continuous pixels   (counts → width)
      scaleBand     discrete categories → evenly spaced slots (names → rows)

    scaleBand also handles the gaps between bars via `padding`.
  */
  const x = d3.scaleLinear()
    .domain([0, d3.max(vendors, (d) => d.occurrences)])
    .range([0, innerW]);

  const y = d3.scaleBand()
    .domain(vendors.map((d) => d.vendor))
    .range([0, innerH])
    .padding(0.25);

  // Bars.
  g.selectAll('rect')
    .data(vendors)
    .join('rect')
      .attr('x', 0)
      .attr('y', (d) => y(d.vendor))
      .attr('height', y.bandwidth())     // bandwidth = the computed bar height
      .attr('fill', (d) => CATEGORY_COLORS[d.category] || cssVar('--unknown'))
      .attr('rx', 3)
      .style('cursor', 'pointer')
      // Start at width 0 and TRANSITION to full width. The animation isn't
      // decoration — a bar growing draws the eye to the comparison.
      .attr('width', 0)
      .on('mousemove', (event, d) => showTooltip(event, `
          <strong>${esc(d.vendor)}</strong>
          ${d.occurrences} cookies across ${d.scans_seen_in} scan(s)<br>
          Category: ${esc(d.category)}`))
      .on('mouseleave', hideTooltip)
    .transition()
      .duration(600)
      .delay((d, i) => i * 45)           // stagger, so bars appear in sequence
      .attr('width', (d) => px(x(d.occurrences)));

  // Value labels at the end of each bar.
  g.selectAll('.bar-label')
    .data(vendors)
    .join('text')
      .attr('class', 'bar-label')
      .attr('x', (d) => x(d.occurrences) + 6)
      .attr('y', (d) => y(d.vendor) + y.bandwidth() / 2)
      .attr('dy', '0.35em')              // nudge down to optically centre text
      .style('font-size', '11px')
      .style('fill', cssVar('--text-muted'))
      .text((d) => d.occurrences);

  // Y axis — the vendor names. d3.axisLeft builds the ticks and labels.
  g.append('g')
    .attr('class', 'axis')
    .call(d3.axisLeft(y).tickSize(0))
    .select('.domain').remove();         // drop the axis spine; it adds noise
}


/* ---- The "data leaves the EEA" headline -------------------------------- */

function renderFlowHeadline() {
  const df = state.currentReport.data_flows || {};
  const pct = df.outside_eea_pct ?? 0;

  // Colour the number by severity — the figure should read at a glance.
  const colour = pct >= 75 ? cssVar('--marketing')
               : pct >= 40 ? cssVar('--analytics')
               : cssVar('--necessary');

  $('#flow-headline').innerHTML = `
    <div class="flow-big" style="color:${colour}">${pct}%</div>
    <div class="flow-text">
      <strong>${df.outside_eea ?? 0} of ${df.total_cookies ?? 0}</strong>
      cookies come from vendors headquartered outside the EEA.
      ${pct > 0
        ? `Each is an international data transfer under GDPR Chapter V and
           needs a documented legal basis.`
        : `All vendors are within the EEA — no Chapter V transfer analysis needed.`}
    </div>`;
}


/* ==========================================================================
   THE GLOBE
   ==========================================================================

   A hybrid of three classic D3 examples:

       world-tour          auto-rotation, sequential country highlighting
       versor-dragging     mouse-interactive orthographic globe
       zoom-to-bounding-box  click a country to fly to it

   FIVE CONCEPTS
   -------------

   1. ORTHOGRAPHIC PROJECTION
      Renders the Earth as it looks from space — a real sphere, with a far
      side you cannot see. Every other projection flattens the globe into a
      rectangle and has to distort something. Orthographic distorts nothing
      at the centre and simply hides the back.

      That's exactly right here: we're not comparing land areas, we're
      showing WHERE data goes. A sphere is the honest shape.

   2. ROTATION IS THREE ANGLES
          projection.rotate([lambda, phi, gamma])
            lambda  spin around the poles (longitude)  ← what auto-rotation changes
            phi     tilt north/south (latitude)
            gamma   roll

      Rotating the PROJECTION rather than the SVG is the key idea. We are not
      spinning a picture — we recompute which parts of the sphere face us, so
      the far side genuinely disappears.

   3. d3.timer FOR ANIMATION
      A requestAnimationFrame loop that runs every frame (~60/second). Better
      than setInterval: it pauses when the tab is hidden, and syncs to the
      display refresh so motion is smooth.

   4. VERSOR DRAGGING (quaternions)
      The naive approach — "mouse moved 10px right, add 10 to lambda" —
      breaks badly near the poles. Drag over the top of the globe and it
      spins wildly, because longitude lines converge there.

      A QUATERNION represents a rotation as a single object rather than three
      separate angles, which removes that problem entirely. We compute the
      rotation that moves the point you grabbed to the point you dragged to,
      and apply it. The globe then follows your cursor exactly.

      That's the whole idea; the maths is ~40 lines below.

   5. FLY-TO ON CLICK
      d3.geoInterpolate walks along the great circle between two points, so
      the globe takes the shortest real path — as an aeroplane would — rather
      than sliding in a straight line across a flat map.
   ========================================================================== */

/* ---- Minimal quaternion helpers (the "versor" technique) --------------- */
/*
   Inlined rather than pulling versor.js from a CDN — it is 40 lines, and one
   fewer network dependency is one fewer thing that can fail offline.

   You do NOT need to derive quaternion algebra to use this. What matters:

     cartesian()  spherical coordinates (lon, lat) → a 3D point on a unit sphere
     delta()      the rotation that turns one 3D point into another
     multiply()   combine two rotations into one
     toAngles()   convert a rotation back into the [lambda, phi, gamma] D3 wants
*/
const versor = {
  // (longitude, latitude) in degrees → an [x, y, z] point on the unit sphere.
  cartesian([lon, lat]) {
    const l = lon * Math.PI / 180, p = lat * Math.PI / 180, cp = Math.cos(p);
    return [cp * Math.cos(l), cp * Math.sin(l), Math.sin(p)];
  },

  // The quaternion rotating unit vector v0 onto v1.
  delta(v0, v1, alpha = 1) {
    const w = [
      v0[1] * v1[2] - v0[2] * v1[1],     // cross product gives the AXIS
      v0[2] * v1[0] - v0[0] * v1[2],     // of rotation
      v0[0] * v1[1] - v0[1] * v1[0],
    ];
    const wLen = Math.hypot(...w);
    if (!wLen) return [1, 0, 0, 0];      // identical vectors → no rotation
    // dot product gives the ANGLE between them
    const t = alpha * Math.acos(Math.max(-1, Math.min(1, v0[0] * v1[0] + v0[1] * v1[1] + v0[2] * v1[2]))) / 2;
    const s = Math.sin(t);
    return [Math.cos(t), (w[2] / wLen) * s, (-w[1] / wLen) * s, (w[0] / wLen) * s];
  },

  // Combine two rotations. Order matters — rotations don't commute.
  multiply([a0, a1, a2, a3], [b0, b1, b2, b3]) {
    return [
      a0 * b0 - a1 * b1 - a2 * b2 - a3 * b3,
      a0 * b1 + a1 * b0 + a2 * b3 - a3 * b2,
      a0 * b2 - a1 * b3 + a2 * b0 + a3 * b1,
      a0 * b3 + a1 * b2 - a2 * b1 + a3 * b0,
    ];
  },

  // Quaternion → the three Euler angles D3's projection.rotate() expects.
  toAngles([l, a, b, c]) {
    return [
      Math.atan2(2 * (l * a + b * c), 1 - 2 * (a * a + b * b)) * 180 / Math.PI,
      Math.asin(Math.max(-1, Math.min(1, 2 * (l * b - c * a)))) * 180 / Math.PI,
      Math.atan2(2 * (l * c + a * b), 1 - 2 * (b * b + c * c)) * 180 / Math.PI,
    ];
  },

  // The quaternion equivalent of a given [lambda, phi, gamma] rotation.
  fromAngles([l, p, g]) {
    l = l * Math.PI / 360; p = p * Math.PI / 360; g = (g || 0) * Math.PI / 360;
    const sl = Math.sin(l), cl = Math.cos(l);
    const sp = Math.sin(p), cp = Math.cos(p);
    const sg = Math.sin(g), cg = Math.cos(g);
    return [
      cl * cp * cg + sl * sp * sg,
      sl * cp * cg - cl * sp * sg,
      cl * sp * cg + sl * cp * sg,
      cl * cp * sg - sl * sp * cg,
    ];
  },
};


/* ---- Globe state ------------------------------------------------------- */

let worldAtlas = null;
let worldAtlasFailed = false;

const globe = {
  timer: null,          // the d3.timer driving auto-rotation
  projection: null,
  path: null,
  svg: null,
  countriesById: new Map(),
  selected: null,       // ISO numeric id of the focused country
  spinning: true,
  idleTimeout: null,
  baseScale: 1,
};

const SPIN_SPEED = 4;         // degrees of longitude per second — slow enough
                              // to read, fast enough to feel alive
const IDLE_BEFORE_RESPIN = 4000;   // ms of no interaction before spinning resumes


async function loadWorldAtlas() {
  if (worldAtlas || worldAtlasFailed) return worldAtlas;
  try {
    const topo = await d3.json(
      'https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json'
    );
    worldAtlas = topojson.feature(topo, topo.objects.countries);
    return worldAtlas;
  } catch (err) {
    worldAtlasFailed = true;
    return null;
  }
}


/** Stop auto-rotation, and schedule it to resume once the user is idle. */
function pauseSpin() {
  globe.spinning = false;
  const hint = $('#globe-hint');
  if (hint) hint.classList.add('faded');
  clearTimeout(globe.idleTimeout);
  // Don't resume while a country is selected — the user is reading it.
  globe.idleTimeout = setTimeout(() => {
    if (!globe.selected) globe.spinning = true;
  }, IDLE_BEFORE_RESPIN);
}


async function drawGlobe() {
  if (!d3Available() || typeof topojson === 'undefined') {
    show($('#globe-fallback'), true);
    return;
  }

  const svg = d3.select('#globe');
  svg.selectAll('*').remove();
  if (globe.timer) globe.timer.stop();

  const countries = state.currentReport?.data_flows?.countries || [];
  renderCountryList(countries);
  renderVendorFlows();
  if (!countries.length) return;

  const atlas = await loadWorldAtlas();
  if (!atlas) { show($('#globe-fallback'), true); return; }
  show($('#globe-fallback'), false);

  const node = svg.node();
  const box = node.getBoundingClientRect();
  const width = box.width || 440;
  const height = 440;
  // Leave a margin so the halo isn't clipped at the edges.
  const radius = Math.min(width, height) / 2 - 14;

  // Lookup: ISO numeric id → our data. Numeric ids, never names (§73).
  globe.countriesById = new Map();
  countries.forEach((c) => {
    if (c.iso_numeric) globe.countriesById.set(String(Number(c.iso_numeric)), c);
  });

  const projection = d3.geoOrthographic()
    .scale(radius)
    .translate([width / 2, height / 2])
    .rotate([0, -12, 0])       // a slight northward tilt looks more natural
                               // than staring at the equator dead-on
    .clipAngle(90);            // ← THE ORTHOGRAPHIC ESSENTIAL. Hides everything
                               // more than 90° away, i.e. the far side of the
                               // planet. Without it the back shows through.

  const path = d3.geoPath(projection);
  const regionColor = REGION_COLORS();

  globe.projection = projection;
  globe.path = path;
  globe.svg = svg;
  globe.baseScale = radius;
  globe.selected = null;

  // ---- gradient + glow filter, defined once in <defs> ----
  const defs = svg.append('defs');

  // An SVG filter that paints a coloured blur behind whatever it's applied to.
  // Used to make the selected country glow. `flood-color` is set per-use in
  // flyTo(), so one filter serves every region colour.
  const glow = defs.append('filter')
    .attr('id', 'country-glow')
    .attr('x', '-60%').attr('y', '-60%')      // room for the blur to spread;
    .attr('width', '220%').attr('height', '220%');  // the default box clips it
  glow.append('feDropShadow')
    .attr('dx', 0).attr('dy', 0)
    .attr('stdDeviation', 3.5)
    .attr('flood-color', cssVar('--brand'))
    .attr('flood-opacity', 0.95);
  const grad = defs.append('radialGradient')
    .attr('id', 'ocean-gradient')
    .attr('cx', '35%').attr('cy', '30%');    // offset centre = a light source
  grad.append('stop').attr('offset', '0%')
      .attr('stop-color', cssVar('--surface'));
  grad.append('stop').attr('offset', '100%')
      .attr('stop-color', cssVar('--bg'));

  // ---- the sphere ----
  svg.append('path')
    .datum({ type: 'Sphere' })
    .attr('class', 'globe-ocean')
    .attr('fill', 'url(#ocean-gradient)')
    .attr('d', path);

  // A halo ring just outside the sphere. Cheap, and it makes the globe read
  // as an object floating in space rather than a circle stuck on the page.
  svg.append('circle')
    .attr('class', 'globe-halo')
    .attr('cx', width / 2).attr('cy', height / 2).attr('r', radius + 5)
    .attr('stroke', cssVar('--brand'))
    .attr('stroke-width', 2.5);

  svg.append('path')
    .datum(d3.geoGraticule10())
    .attr('class', 'globe-graticule')
    .attr('d', path);

  // ---- countries ----
  const land = svg.append('g').selectAll('path')
    .data(atlas.features)
    .join('path')
      .attr('class', (d) => globe.countriesById.has(String(d.id))
        ? 'globe-land globe-land-data' : 'globe-land')
      .attr('fill', (d) => {
        const info = globe.countriesById.get(String(d.id));
        return info ? (regionColor[info.region] || cssVar('--unknown'))
                    : cssVar('--border');
      })
      .attr('d', path)
      .on('mousemove', function (event, d) {
        const info = globe.countriesById.get(String(d.id));
        if (!info) return;
        showTooltip(event, `
          <strong>${esc(info.country)}</strong>
          ${info.cookie_count} cookies · ${info.vendor_count} vendor(s)<br>
          <span style="opacity:.8">${esc(info.region)}</span><br>
          <span style="opacity:.6;font-size:.9em">click to zoom</span>`);
      })
      .on('mouseleave', hideTooltip)
      .on('click', (event, d) => {
        const info = globe.countriesById.get(String(d.id));
        if (info) flyTo(info, d);
      });

  /** Recompute every path. Called on each animation frame and each drag move. */
  function render() {
    svg.selectAll('path').attr('d', path);
  }
  globe.render = render;

  // ---- auto-rotation ----
  // d3.timer runs a callback every animation frame. `elapsed` is milliseconds
  // since it started. We track the previous value so speed is time-based, not
  // frame-based — otherwise a fast machine spins faster than a slow one.
  let lastElapsed = 0;
  globe.timer = d3.timer((elapsed) => {
    const delta = elapsed - lastElapsed;
    lastElapsed = elapsed;
    if (!globe.spinning) return;
    const r = projection.rotate();
    projection.rotate([r[0] + (SPIN_SPEED * delta) / 1000, r[1], r[2]]);
    render();
  });

  // ---- drag ----
  // v0/q0 capture the state when the drag STARTS; each move computes the
  // rotation from the original grab point to the current pointer, which is
  // what makes the globe follow the cursor precisely.
  let v0, q0, r0;

  svg.call(d3.drag()
    .on('start', (event) => {
      pauseSpin();
      svg.classed('dragging', true);
      r0 = projection.rotate();
      q0 = versor.fromAngles(r0);
      // invert() converts a screen pixel back into a lon/lat on the globe.
      const inv = projection.invert([event.x, event.y]);
      v0 = inv ? versor.cartesian(inv) : null;
    })
    .on('drag', (event) => {
      if (!v0) return;
      // Where is the pointer now, in globe coordinates? We invert using the
      // ORIGINAL rotation, so we're asking "which point did the user grab".
      projection.rotate(r0);
      const inv = projection.invert([event.x, event.y]);
      if (!inv) return;
      const v1 = versor.cartesian(inv);
      // Combine the starting rotation with the one that moves v0 → v1.
      const q1 = versor.multiply(q0, versor.delta(v0, v1));
      projection.rotate(versor.toAngles(q1));
      render();
    })
    .on('end', () => {
      svg.classed('dragging', false);
      pauseSpin();
    })
  );

  // Clicking empty ocean deselects.
  svg.on('click', (event) => {
    if (event.target === node || event.target.classList.contains('globe-ocean')) {
      clearGlobeSelection();
    }
  });

  render();
}


/* ---- Fly to a country --------------------------------------------------
   Two things animate together: the ROTATION (so the country faces us) and
   the SCALE (so we zoom in). Doing both in one transition is what makes it
   feel like a camera move rather than two separate effects. */

function flyTo(info, feature) {
  if (!globe.projection) return;

  globe.spinning = false;
  globe.selected = String(feature.id);
  clearTimeout(globe.idleTimeout);

  // Highlight IMMEDIATELY, before the camera starts moving.
  // Waiting until the flight ends makes the click feel unacknowledged for a
  // whole second — the response should be instant even if the motion isn't.
  const regionColour = REGION_COLORS()[info.region] || cssVar('--unknown');
  globe.svg.select('#country-glow feDropShadow')
    .attr('flood-color', regionColour);

  globe.svg.classed('globe-dimmed', true);
  globe.svg.selectAll('.globe-land')
    .classed('globe-land-selected', (d) => String(d.id) === globe.selected)
    .attr('filter', (d) => String(d.id) === globe.selected
      ? 'url(#country-glow)' : null)
    // Brighten the selected country's own fill so its region colour reads
    // clearly against the dimmed background.
    .attr('fill', (d) => {
      const entry = globe.countriesById.get(String(d.id));
      if (String(d.id) === globe.selected) return regionColour;
      return entry ? (REGION_COLORS()[entry.region] || cssVar('--unknown'))
                   : cssVar('--border');
    });

  // Raise it above its neighbours so the glow and outline aren't clipped by
  // countries drawn later. `raise()` re-appends the node, and in SVG paint
  // order is document order — there is no z-index.
  globe.svg.selectAll('.globe-land')
    .filter((d) => String(d.id) === globe.selected)
    .raise();

  const projection = globe.projection;
  const svg = globe.svg;

  // geoCentroid finds the middle of a shape ON A SPHERE — not the average of
  // its screen coordinates, which would be wrong for anything near a pole or
  // crossing the date line.
  const centroid = d3.geoCentroid(feature);

  // The rotation that brings that point to face us is the NEGATIVE of its
  // coordinates: to look at longitude 100, rotate the globe by -100.
  const targetRotation = [-centroid[0], -centroid[1], 0];
  const startRotation = projection.rotate();
  const startScale = projection.scale();

  // Bigger countries need less zoom. geoBounds gives the shape's lon/lat
  // extent, so we can scale inversely to its size — Russia and Malta both end
  // up filling a sensible portion of the view.
  const bounds = d3.geoBounds(feature);
  const spanLon = Math.abs(bounds[1][0] - bounds[0][0]);
  const spanLat = Math.abs(bounds[1][1] - bounds[0][1]);
  const span = Math.max(spanLon, spanLat, 4);
  const targetScale = globe.baseScale * Math.min(2.6, Math.max(1.25, 46 / span));

  // geoInterpolate walks the GREAT CIRCLE between two points — the shortest
  // path across a sphere, the route an aeroplane takes. Interpolating the
  // numbers directly would slide across the map in a straight line, which
  // looks wrong on a globe.
  const rotInterp = d3.geoInterpolate(
    [-startRotation[0], -startRotation[1]],
    [centroid[0], centroid[1]]
  );

  d3.transition()
    .duration(1100)
    .ease(d3.easeCubicInOut)
    .tween('fly', () => (t) => {
      const point = rotInterp(t);
      projection.rotate([-point[0], -point[1], 0]);
      projection.scale(startScale + (targetScale - startScale) * t);
      globe.render();
    })
    ;

  showGlobeDetail(info);
  highlightCountryRow(info.code);
}


function clearGlobeSelection() {
  if (!globe.projection) return;
  globe.selected = null;
  show($('#globe-detail'), false);
  globe.svg.classed('globe-dimmed', false);
  globe.svg.selectAll('.globe-land')
    .classed('globe-land-selected', false)
    .attr('filter', null);
  highlightCountryRow(null);

  const projection = globe.projection;
  const startScale = projection.scale();
  d3.transition().duration(700).ease(d3.easeCubicInOut)
    .tween('zoom-out', () => (t) => {
      projection.scale(startScale + (globe.baseScale - startScale) * t);
      globe.render();
    })
    .on('end', () => { globe.spinning = true; });
}


function showGlobeDetail(info) {
  const colour = REGION_COLORS()[info.region] || cssVar('--unknown');
  const el = $('#globe-detail');
  el.style.borderLeftColor = colour;
  el.innerHTML = `
    <button class="close" id="globe-detail-close" aria-label="Close">×</button>
    <h5>${esc(info.country)}</h5>
    <span class="region-tag" style="background:${colour}">${esc(info.region)}</span>
    <div><strong>${info.cookie_count}</strong> cookies from
         <strong>${info.vendor_count}</strong> vendor(s)</div>
    <div class="muted small" style="margin-top:5px">
      ${esc(info.vendors.slice(0, 5).join(', '))}${
        info.vendor_count > 5 ? '…' : ''}
    </div>`;
  show(el, true);
  $('#globe-detail-close').onclick = clearGlobeSelection;
}


/* ---- The country list beside the globe --------------------------------- */

function renderCountryList(countries) {
  const list = $('#country-list');
  if (!list) return;
  const regionColor = REGION_COLORS();

  list.innerHTML = countries.map((c) => `
    <button class="country-row" data-code="${esc(c.code)}"
            data-iso="${esc(c.iso_numeric || '')}">
      <span class="country-swatch"
            style="background:${regionColor[c.region] || cssVar('--unknown')}"></span>
      <span class="country-name">${esc(c.country)}</span>
      <span class="country-count">${c.cookie_count}</span>
    </button>`).join('');

  list.onclick = (event) => {
    const row = event.target.closest('[data-code]');
    if (!row) return;
    const info = countries.find((c) => c.code === row.dataset.code);
    if (!info || !info.iso_numeric || !worldAtlas) return;
    const feature = worldAtlas.features.find(
      (f) => String(f.id) === String(Number(info.iso_numeric))
    );
    if (feature) flyTo(info, feature);
  };
}

function highlightCountryRow(code) {
  $$('#country-list .country-row').forEach((row) => {
    row.classList.toggle('active', row.dataset.code === code);
  });
}


/* ---- Vendor-level transfer table --------------------------------------- */

function renderVendorFlows() {
  const tbody = $('#vendor-flows-tbody');
  if (!tbody) return;
  const vendors = state.currentReport?.data_flows?.vendors || [];
  const regionColor = REGION_COLORS();

  tbody.innerHTML = vendors.slice(0, 20).map((v) => `
    <tr>
      <td><strong>${esc(v.vendor)}</strong></td>
      <td class="muted">${esc(v.country)}</td>
      <td>
        <span class="badge" style="background:${regionColor[v.region] || cssVar('--unknown')}">
          ${esc(v.region)}
        </span>
      </td>
      <td>${v.categories.map((c) =>
        `<span class="pill" style="background:${CATEGORY_COLORS[c]};color:#fff">${esc(c)}</span>`
      ).join(' ')}</td>
      <td class="num"><strong>${v.cookie_count}</strong></td>
    </tr>`).join('') ||
    '<tr><td colspan="5" class="muted">No vendor data.</td></tr>';
}


/* ---- CHART 4: jurisdiction, horizontal bars ---------------------------- */

function drawJurisdictionChart() {
  // The flat bar chart was replaced by the globe + country list. The element
  // no longer exists, so this returns early. Kept (rather than deleted) so the
  // redrawCharts() call list stays stable.
  if (!d3Available() || !document.getElementById('chart-jurisdiction')) return;
  const svg = d3.select('#chart-jurisdiction');
  svg.selectAll('*').remove();

  const countries = (state.currentReport.data_flows?.countries || []).slice(0, 10);
  if (!countries.length) return;

  // Height is data-driven here (one row per country), so it's set BEFORE
  // measuring — chartFrame() then reads the height we just applied.
  svg.attr('height', Math.max(160, countries.length * 30 + 40));

  const frame = chartFrame(svg, { top: 6, right: 60, bottom: 24, left: 140 });
  if (!frame) return;
  const { margin, innerW, innerH } = frame;

  const g = svg.append('g')
    .attr('transform', `translate(${margin.left}, ${margin.top})`);

  const x = d3.scaleLinear()
    .domain([0, d3.max(countries, (d) => d.cookie_count)])
    .range([0, innerW]);

  const y = d3.scaleBand()
    .domain(countries.map((d) => d.country))
    .range([0, innerH])
    .padding(0.22);

  const regionColor = REGION_COLORS();

  g.selectAll('rect')
    .data(countries)
    .join('rect')
      .attr('x', 0)
      .attr('y', (d) => y(d.country))
      .attr('height', y.bandwidth())
      .attr('rx', 3)
      .attr('fill', (d) => regionColor[d.region] || cssVar('--unknown'))
      .style('cursor', 'pointer')
      .attr('width', 0)
      .on('mousemove', (event, d) => showTooltip(event, `
          <strong>${esc(d.country)} — ${esc(d.region)}</strong>
          ${d.cookie_count} cookies from ${d.vendor_count} vendor(s)<br>
          <span style="opacity:.75">${esc(d.vendors.join(', '))}${
            d.vendor_count > d.vendors.length ? '…' : ''}</span>`))
      .on('mouseleave', hideTooltip)
    .transition().duration(650).delay((d, i) => i * 40)
      .attr('width', (d) => Math.max(2, px(x(d.cookie_count))));

  g.selectAll('.jur-label')
    .data(countries)
    .join('text')
      .attr('class', 'jur-label')
      .attr('x', (d) => Math.max(2, x(d.cookie_count)) + 7)
      .attr('y', (d) => y(d.country) + y.bandwidth() / 2)
      .attr('dy', '0.35em')
      .style('font-size', '11px')
      .style('fill', cssVar('--text-muted'))
      .text((d) => d.cookie_count);

  g.append('g')
    .attr('class', 'axis')
    .call(d3.axisLeft(y).tickSize(0))
    .select('.domain').remove();
}


/* ---- CHART 5: lifetime histogram --------------------------------------- */

function drawLifetimeChart() {
  if (!d3Available()) return;
  const svg = d3.select('#chart-lifetime');
  svg.selectAll('*').remove();

  const buckets = state.currentReport.lifetime_buckets || [];
  if (!buckets.length || buckets.every((b) => b.count === 0)) return;

  const frame = chartFrame(svg, { top: 10, right: 8, bottom: 52, left: 34 });
  if (!frame) return;
  const { margin, innerW, innerH } = frame;

  const g = svg.append('g')
    .attr('transform', `translate(${margin.left}, ${margin.top})`);

  const x = d3.scaleBand()
    .domain(buckets.map((d) => d.label))
    .range([0, innerW])
    .padding(0.22);

  const y = d3.scaleLinear()
    .domain([0, d3.max(buckets, (d) => d.count) || 1])
    .nice()                       // round the top up to a tidy number
    .range([innerH, 0]);          // inverted — SVG's y-axis points down

  g.selectAll('.grid-line')
    .data(y.ticks(4))
    .join('line')
      .attr('class', 'grid-line')
      .attr('x1', 0).attr('x2', innerW)
      .attr('y1', (d) => y(d)).attr('y2', (d) => y(d));

  g.selectAll('rect')
    .data(buckets)
    .join('rect')
      .attr('x', (d) => x(d.label))
      .attr('width', x.bandwidth())
      .attr('rx', 3)
      // The over-13-months bucket gets the alarm colour. That one bar is the
      // whole point of the chart.
      .attr('fill', (d) => d.excessive ? cssVar('--marketing') : cssVar('--functional'))
      .style('cursor', 'pointer')
      .attr('y', innerH)          // start flat...
      .attr('height', 0)
      .on('mousemove', (event, d) => showTooltip(event, `
          <strong>${esc(d.label)}</strong>
          ${d.count} cookie(s)
          ${d.excessive ? '<br>Exceeds CNIL\'s recommended maximum' : ''}`))
      .on('mouseleave', hideTooltip)
    .transition().duration(600).delay((d, i) => i * 50)
      .attr('y', (d) => y(d.count))          // ...then grow upward
      .attr('height', (d) => innerH - y(d.count));

  g.append('g')
    .attr('class', 'axis')
    .attr('transform', `translate(0, ${innerH})`)
    .call(d3.axisBottom(x))
    .selectAll('text')
      // Rotate the labels — "≤ 13 months" won't fit horizontally in a
      // narrow band. `text-anchor: end` keeps the rotated text tidy.
      .attr('transform', 'rotate(-38)')
      .style('text-anchor', 'end')
      .attr('dx', '-0.4em')
      .attr('dy', '0.4em');

  g.append('g')
    .attr('class', 'axis')
    .call(d3.axisLeft(y).ticks(4));
}


/* ---- CHART 6: security gauges ------------------------------------------ */

function renderSecurityGauges() {
  const sp = state.currentReport?.security_posture || {};
  const el = $('#security-metrics');
  if (!el) return;

  const total = sp.total || 0;

  /*
    Design B's format, and it is strictly better than the radial gauges it
    replaces. A gauge shows a PERCENTAGE. A metric row shows the percentage
    AND the counts AND what the flag means:

        Secure          19/38   50%
        ──────────────────
        Sent only over HTTPS

    A compliance officer has to put numbers in a report. "50%" isn't
    quotable; "19 of 38" is.
  */
  const rows = [
    {
      name: 'Secure',
      count: sp.secure_count ?? 0,
      pct: sp.secure_pct ?? 0,
      good: true,
      note: 'Sent only over HTTPS, never plain HTTP.',
    },
    {
      name: 'HttpOnly',
      count: sp.http_only_count ?? 0,
      pct: sp.http_only_pct ?? 0,
      good: true,
      note: 'Hidden from JavaScript, which protects session tokens against XSS.',
    },
    {
      name: 'Cross-site (SameSite=None)',
      count: sp.cross_site_tracker_count ?? 0,
      pct: total ? Math.round(100 * (sp.cross_site_tracker_count ?? 0) / total) : 0,
      good: false,          // for this one, LOWER is better
      note: 'Third-party AND SameSite=None — the strongest technical signal of '
          + 'cross-site tracking behaviour.',
    },
  ];

  el.innerHTML = rows.map((r) => {
    // For "good" metrics a high value is green; for cross-site it's inverted.
    const quality = r.good ? r.pct : 100 - r.pct;
    const colour = quality >= 70 ? 'var(--necessary)'
                 : quality >= 40 ? 'var(--analytics)'
                 : 'var(--marketing)';
    return `
      <div class="metric-row">
        <div class="metric-head">
          <span class="metric-name">${esc(r.name)}</span>
          <span class="metric-figures">
            <span class="metric-count">${r.count}</span><span class="metric-total">/${total}</span>
            <span class="metric-pct">${r.pct}%</span>
          </span>
        </div>
        <div class="metric-track">
          <div class="metric-fill" style="width:${Math.min(100, r.pct)}%;background:${colour}"></div>
        </div>
        <div class="metric-note">${esc(r.note)}</div>
      </div>`;
  }).join('');
}


/* ---- CHART 7: treemap of third-party domains --------------------------- */

function drawTreemap() {
  if (!d3Available()) return;
  const svg = d3.select('#chart-treemap');
  svg.selectAll('*').remove();

  const domains = (state.currentReport.third_party_domains || []).slice(0, 30);
  show($('#treemap-empty'), domains.length === 0);
  if (!domains.length) return;

  const box = svg.node().getBoundingClientRect();
  const width = box.width;
  const height = 320;
  if (width < 40) return;          // not laid out yet — see chartFrame()

  /*
    A TREEMAP fills a rectangle with sub-rectangles whose AREA is proportional
    to a value. It's the right chart when you have many categories of wildly
    different sizes — a bar chart of 30 domains would be unreadable, and a pie
    chart with 30 slices is worse.

    D3 needs a HIERARCHY, even for flat data, so we wrap the list in a fake
    root node with `children`.
  */
  const root = d3.hierarchy({ children: domains })
    .sum((d) => d.request_count || 1)     // sum() decides each box's area
    .sort((a, b) => b.value - a.value);   // biggest first

  d3.treemap().size([width, height]).paddingInner(2).round(true)(root);

  // One <g> per leaf, positioned at its computed corner.
  const leaf = svg.selectAll('g')
    .data(root.leaves())
    .join('g')
      .attr('transform', (d) => `translate(${d.x0},${d.y0})`);

  leaf.append('rect')
    .attr('width', (d) => d.x1 - d.x0)
    .attr('height', (d) => d.y1 - d.y0)
    .attr('rx', 3)
    // Design B uses a violet family here rather than category colours. The
    // treemap's job is showing REQUEST VOLUME, and a single-hue ramp reads as
    // "more/less" far better than five unrelated hues, which read as
    // "different kinds".
    .attr('fill', (d, i) => cssVar(`--chart-${(i % 5) + 1}`))
    .attr('fill-opacity', 0.95)
    .style('cursor', 'pointer')
    .on('mousemove', (event, d) => showTooltip(event, `
        <strong>${esc(d.data.domain)}</strong>
        ${d.data.request_count} request(s)<br>
        ${esc(d.data.vendor || 'Unknown vendor')} · ${esc(d.data.category)}`))
    .on('mouseleave', hideTooltip)
    .style('opacity', 0)
    .transition().duration(500).delay((d, i) => i * 18)
    .style('opacity', 1);

  /*
    Only label boxes big enough to hold text. Cramming a label into a 12px box
    produces overlapping mush — leaving it out and relying on the tooltip is
    the better design.
  */
  // Domain name...
  leaf.append('text')
    .attr('x', 10).attr('y', 20)
    .style('font-size', '12px')
    .style('font-weight', '700')
    .style('font-family', 'var(--font-mono)')
    .style('fill', '#fff')
    .text((d) => {
      const w = d.x1 - d.x0, h = d.y1 - d.y0;
      if (w < 74 || h < 34) return '';
      const name = d.data.domain.replace(/^www\./, '');
      const maxChars = Math.floor(w / 7);
      return name.length > maxChars ? name.slice(0, maxChars - 1) + '…' : name;
    });

  // ...and the request count beneath it, Design B style.
  leaf.append('text')
    .attr('x', 10).attr('y', 36)
    .style('font-size', '11px')
    .style('fill', '#fff')
    .style('fill-opacity', .8)
    .text((d) => {
      const w = d.x1 - d.x0, h = d.y1 - d.y0;
      if (w < 74 || h < 46) return '';
      return `${d.data.request_count} req`;
    });
}


/* ---- CHART 3: line, compliance score over time ------------------------- */

function drawHistoryChart() {
  if (!d3Available()) return;
  const svg = d3.select('#chart-history');
  svg.selectAll('*').remove();

  const history = (state.currentReport.history || [])
    .filter((h) => h.compliance_score !== null);

  // One point is not a trend. Say so instead of drawing a misleading dot.
  show($('#history-empty'), history.length < 2);
  if (history.length < 2) return;

  const frame = chartFrame(svg, { top: 16, right: 20, bottom: 34, left: 42 });
  if (!frame) return;
  const { margin, innerW, innerH } = frame;

  const g = svg.append('g')
    .attr('transform', `translate(${margin.left}, ${margin.top})`);

  // `new Date(...)` parses our ISO-8601 strings — another payoff for using a
  // standard date format back in the database.
  const data = history.map((h) => ({
    date: new Date(h.scanned_at),
    score: h.compliance_score,
    cookies: h.cookie_count,
    grade: h.compliance_grade,
  }));

  // scaleTime understands dates: it spaces points by REAL elapsed time, so a
  // six-month gap looks like a six-month gap.
  const x = d3.scaleTime()
    .domain(d3.extent(data, (d) => d.date))   // extent = [min, max]
    .range([0, innerW]);

  // Fixed 0–100 domain, not the data's own range. A score of 60 must always
  // sit at the same height, or the chart would exaggerate tiny changes.
  const y = d3.scaleLinear().domain([0, 100]).range([innerH, 0]);

  // Horizontal grid lines.
  g.selectAll('.grid-line')
    .data(y.ticks(5))
    .join('line')
      .attr('class', 'grid-line')
      .attr('x1', 0).attr('x2', innerW)
      .attr('y1', (d) => y(d)).attr('y2', (d) => y(d));

  // d3.line() turns an array of points into one SVG path string.
  const line = d3.line()
    .x((d) => x(d.date))
    .y((d) => y(d.score))
    .curve(d3.curveMonotoneX);   // smooth, but never overshoots the real values
                                 // — important: a prettier curve that invents
                                 // peaks would be lying about the data

  const path = g.append('path')
    .datum(data)                 // datum() binds ONE object, not an array of them
    .attr('fill', 'none')
    .attr('stroke', cssVar('--brand'))
    .attr('stroke-width', 2.5)
    .attr('d', line);

  /*
    THE LINE-DRAWING ANIMATION.
    getTotalLength() measures the path. We set the dash pattern to one dash of
    exactly that length, offset fully out of view, then animate the offset to
    zero — so the line appears to draw itself. A classic D3 trick.
  */
  const len = path.node().getTotalLength();
  path
    .attr('stroke-dasharray', `${len} ${len}`)
    .attr('stroke-dashoffset', len)
    .transition().duration(900).ease(d3.easeQuadOut)
    .attr('stroke-dashoffset', 0);

  // Points.
  g.selectAll('circle')
    .data(data)
    .join('circle')
      .attr('cx', (d) => x(d.date))
      .attr('cy', (d) => y(d.score))
      .attr('r', 5)
      .attr('fill', (d) => gradeColor(d.grade))
      .attr('stroke', '#fff')
      .attr('stroke-width', 2)
      .style('cursor', 'pointer')
      .on('mousemove', (event, d) => showTooltip(event, `
          <strong>${d.date.toISOString().slice(0, 10)}</strong>
          Score ${d.score}/100 (${d.grade})<br>
          ${d.cookies} cookies`))
      .on('mouseleave', hideTooltip)
      .style('opacity', 0)
      .transition().delay(700).duration(300)
      .style('opacity', 1);

  // Axes.
  g.append('g')
    .attr('class', 'axis')
    .attr('transform', `translate(0, ${innerH})`)
    .call(d3.axisBottom(x).ticks(Math.min(data.length, 6))
            .tickFormat(d3.timeFormat('%d %b')));

  g.append('g')
    .attr('class', 'axis')
    .call(d3.axisLeft(y).ticks(5));
}


/* ==========================================================================
   9b. CONSENT BANNER CONFIGURATOR
   ==========================================================================
   The single best product idea in either mockup. It turns the banner from
   "a file in a repo" into something a site owner could actually adopt:
   configure it, see it, copy the snippet.

   Everything lives in one `bannerConfig` object and one `renderPreview()`.
   Any control writes to the object then calls the renderer — so there is
   exactly ONE place that knows how the preview looks, and adding a control
   never means touching the drawing code.
   ========================================================================== */

const bannerConfig = {
  color: '#4f46e5',
  position: 'bottom',
  overlay: true,
  details: false,
  title: 'We use cookies',
  body: 'Necessary cookies keep this site working. Everything else stays '
      + 'blocked until you say yes.',
  policy: 'https://example.com/privacy',
  expiryDays: 180,
  previewState: 'first',
};

const SWATCHES = ['#4f46e5', '#7c3aed', '#0f766e', '#1e293b', '#dc2626', '#ea580c'];

function initConsentConfigurator() {
  // Colour swatches
  $('#cfg-swatches').innerHTML = SWATCHES.map((c, i) => `
    <button class="swatch ${i === 0 ? 'swatch-active' : ''}"
            style="background:${c}" data-color="${c}"
            aria-label="Brand colour ${c}"></button>`).join('');

  $('#cfg-swatches').onclick = (e) => {
    const sw = e.target.closest('[data-color]');
    if (!sw) return;
    bannerConfig.color = sw.dataset.color;
    $('#cfg-color').value = sw.dataset.color;
    $$('#cfg-swatches .swatch').forEach((s) =>
      s.classList.toggle('swatch-active', s === sw));
    renderBannerPreview();
  };

  $('#cfg-color').oninput = (e) => {
    // Only accept a complete hex value — otherwise the preview flickers to
    // black while you're still typing the second character.
    if (/^#[0-9a-fA-F]{6}$/.test(e.target.value)) {
      bannerConfig.color = e.target.value;
      renderBannerPreview();
    }
  };

  // Segmented controls. One handler for both, keyed by which dataset field
  // the buttons carry.
  const wireSegmented = (id, key, attr, cast = (v) => v) => {
    $(id).onclick = (e) => {
      const btn = e.target.closest(`[data-${attr}]`);
      if (!btn) return;
      bannerConfig[key] = cast(btn.dataset[attr]);
      $$(`${id} .segment`).forEach((b) =>
        b.classList.toggle('segment-active', b === btn));
      renderBannerPreview();
    };
  };
  wireSegmented('#cfg-position', 'position', 'pos');
  wireSegmented('#cfg-expiry', 'expiryDays', 'days', Number);

  // Text inputs and toggles
  const bind = (id, key, prop = 'value') => {
    $(id)[prop === 'checked' ? 'onchange' : 'oninput'] = (e) => {
      bannerConfig[key] = e.target[prop];
      renderBannerPreview();
    };
  };
  bind('#cfg-title', 'title');
  bind('#cfg-body', 'body');
  bind('#cfg-policy', 'policy');
  bind('#cfg-overlay', 'overlay', 'checked');
  bind('#cfg-details', 'details', 'checked');

  $('#preview-tabs').onclick = (e) => {
    const tab = e.target.closest('[data-state]');
    if (!tab) return;
    bannerConfig.previewState = tab.dataset.state;
    $$('#preview-tabs .preview-tab').forEach((t) =>
      t.classList.toggle('preview-tab-active', t === tab));
    renderBannerPreview();
  };

  $('#copy-snippet').onclick = async () => {
    try {
      // The Clipboard API is async and requires a secure context (https or
      // localhost). It can also be denied, so this must be guarded.
      await navigator.clipboard.writeText($('#cfg-snippet').textContent);
      const btn = $('#copy-snippet');
      btn.textContent = '✓ Copied';
      setTimeout(() => { btn.textContent = 'Copy snippet'; }, 1800);
    } catch (err) {
      alert('Could not copy automatically — select the snippet and copy it manually.');
    }
  };

  renderBannerPreview();
}

function renderBannerPreview() {
  const c = bannerConfig;
  const stage = $('#preview-stage');
  if (!stage) return;

  // Skeleton page content, so the banner is judged in context rather than
  // floating in a void.
  const skeleton = [92, 74, 97, 85, 95, 80, 88].map((w) =>
    `<div class="skeleton-line" style="width:${w}%"></div>`).join('');

  let banner = '';

  if (c.previewState === 'withdrawn') {
    banner = `
      <div class="preview-banner" style="border-top-color:${esc(c.color)}">
        <h4>🍪 Preferences saved</h4>
        <p>
          The visitor has made a choice. A permanently visible button lets
          them change it — <strong>withdrawing consent must be as easy as
          giving it</strong>.
        </p>
        <div class="preview-actions">
          <span class="btn btn-ghost" style="pointer-events:none">🍪 Cookie preferences</span>
        </div>
      </div>`;
  } else if (c.previewState === 'details' || c.details) {
    const cats = [
      ['Strictly necessary', 'Login, security, shopping cart.', true],
      ['Functional', 'Language, region, theme.', false],
      ['Analytics', 'Visits, popular pages, errors.', false],
      ['Marketing', 'Advertising and cross-site profiling.', false],
    ];
    banner = `
      <div class="preview-banner pos-${esc(c.position)}"
           style="border-top-color:${esc(c.color)}">
        <h4>🍪 ${esc(c.title)}</h4>
        ${cats.map(([name, desc, locked]) => `
          <div class="toggle-row" style="margin-bottom:7px">
            <span>
              <div class="toggle-text">${esc(name)}</div>
              <div class="toggle-hint">${esc(desc)}</div>
            </span>
            ${locked
              ? `<span style="font-size:.72rem;font-weight:700;color:var(--necessary)">ALWAYS ON</span>`
              : `<span class="switch"><input type="checkbox" disabled>
                   <span class="switch-track"></span></span>`}
          </div>`).join('')}
        <div class="preview-actions" style="margin-top:14px">
          <span class="btn" style="background:${esc(c.color)};color:#fff;pointer-events:none">Reject all</span>
          <span class="btn" style="background:${esc(c.color)};color:#fff;pointer-events:none">Accept all</span>
          <span class="btn btn-ghost" style="pointer-events:none">Save my choices</span>
        </div>
      </div>`;
  } else {
    banner = `
      <div class="preview-banner pos-${esc(c.position)}"
           style="border-top-color:${esc(c.color)}">
        <h4>🍪 ${esc(c.title)}</h4>
        <p>
          ${esc(c.body)}
          ${c.policy ? ` <a href="#" style="color:${esc(c.color)}">Privacy policy</a>.` : ''}
        </p>
        <div class="preview-actions">
          <!-- ⚠ Reject and Accept are rendered IDENTICALLY. This is the rule
               CNIL fined Google €150m and Facebook €60m over — refusing must
               be exactly as easy as accepting. -->
          <span class="btn" style="background:${esc(c.color)};color:#fff;pointer-events:none">Reject all</span>
          <span class="btn" style="background:${esc(c.color)};color:#fff;pointer-events:none">Accept all</span>
          <span class="btn btn-ghost" style="pointer-events:none">Customise</span>
        </div>
      </div>`;
  }

  stage.innerHTML = skeleton + banner;
  stage.style.background = c.overlay && c.previewState !== 'withdrawn'
    ? 'color-mix(in oklch, var(--bg) 78%, black)'
    : 'var(--bg)';

  $('#cfg-snippet').textContent =
`<script src="consent-banner.js"
        data-accent-color="${c.color}"
        data-position="${c.position}"
        data-expiry-days="${c.expiryDays}"
        data-policy-url="${c.policy}"></script>`;
}


/* ==========================================================================
   9c. SETTINGS
   ========================================================================== */

const ENDPOINTS = [
  ['GET', '/health', 'Backend liveness and database probe'],
  ['POST', '/api/scan', 'Run a real-browser scan (optionally with consent diff)'],
  ['GET', '/api/domains', 'List every scanned domain'],
  ['GET', '/api/domains/{domain}/scans', 'Scan history for one domain'],
  ['GET', '/api/domains/{domain}/latest', 'Most recent scan, full detail'],
  ['GET', '/api/scans', 'Scan history across all domains, filterable'],
  ['GET', '/api/scans/{id}', 'Single scan with cookies and third parties'],
  ['GET', '/api/scans/{id}/cookies', 'Cookie inventory for one scan'],
  ['DELETE', '/api/scans/{id}', 'Delete a scan (cascades to its cookies)'],
  ['GET', '/api/report/{domain}', 'Compliance report, vendors, trend, data flows'],
  ['GET', '/api/report/{domain}/pdf', 'Download the audit report as a PDF'],
];

function renderSettings(healthy, health) {
  $('#endpoints-tbody').innerHTML = ENDPOINTS.map(([method, path, purpose]) => `
    <tr>
      <td><span class="method-badge method-${method}">${method}</span></td>
      <td class="mono">${esc(path)}</td>
      <td class="muted">${esc(purpose)}</td>
    </tr>`).join('');

  $('#settings-base-url').textContent = API_BASE;

  $('#settings-status').innerHTML = healthy
    ? `<div class="status-card status-card-ok">
         <h4 style="color:var(--necessary)">● Live API connected</h4>
         <p>
           The FastAPI backend is reachable. Scans use the real
           Playwright-powered browser engine.
           ${health ? `<br><strong>${health.domains_tracked}</strong> domains ·
             <strong>${health.scans_stored}</strong> scans stored.` : ''}
         </p>
       </div>`
    : `<div class="status-card status-card-fail">
         <h4 style="color:var(--analytics)">● Backend unreachable</h4>
         <p>
           Nothing on this dashboard is live. Start the API with
           <code>uvicorn api.main:app --reload</code>.
         </p>
       </div>`;
}


/* ==========================================================================
   10. TABS AND WIRING
   ========================================================================== */

function switchView(name) {
  $$('.view').forEach((v) => v.classList.remove('view-active'));
  $(`#view-${name}`).classList.add('view-active');

  $$('.nav-item').forEach((t) => {
    const active = t.dataset.view === name;
    t.classList.toggle('nav-item-active', active);
    t.setAttribute('aria-selected', String(active));
  });

  // Close the mobile drawer after choosing a destination.
  $('#main-nav').classList.remove('open');
  window.scrollTo({ top: 0, behavior: 'smooth' });

  // Charts measure the SVG's rendered width. A hidden element has width 0, so
  // charts drawn while the tab was hidden come out wrong. Redraw on show.
  if (name === 'report' && state.currentReport) redrawCharts();
  if (name === 'history') { populateHistoryFilters(); loadHistory(); }
  if (name === 'settings') { checkHealth().then((ok) => renderSettings(ok, state.health)); }
  if (name === 'consent') renderBannerPreview();
}

/*
  DEBOUNCE.

  Typing "facebook" fires 8 keystroke events. Re-rendering a 177-row table 8
  times in half a second is wasteful and makes typing feel laggy.

  Debouncing waits until the user PAUSES, then runs once. Each keystroke
  cancels the previous pending timer.

      keystrokes:  f-a-c-e-b-o-o-k......
      renders:                        ^  just one, after the pause
*/
function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

function wireEvents() {
  $$('.nav-item, .brand').forEach((el) => {
    el.onclick = () => switchView(el.dataset.view);
  });
  $('#nav-toggle').onclick = () => $('#main-nav').classList.toggle('open');

  $('#scan-form').onsubmit = handleScanSubmit;
  $('#refresh-domains').onclick = loadDomains;
  $('#download-pdf').onclick = downloadReportPdf;

  // ---- scan history ----
  $('#history-refresh').onclick = loadHistory;
  $('#recheck-api').onclick = async () => {
    const ok = await checkHealth();
    renderSettings(ok, state.health);
  };
  $('#history-domain-filter').onchange = (e) => {
    historyState.domain = e.target.value;
    historyState.page = 0;          // a new filter means a new result set
    loadHistory();
  };
  $('#history-grade-filter').onchange = (e) => {
    historyState.grade = e.target.value;
    historyState.page = 0;
    loadHistory();
  };
  $('#history-prev').onclick = () => {
    if (historyState.page > 0) { historyState.page--; loadHistory(); }
  };
  $('#history-next').onclick = () => { historyState.page++; loadHistory(); };

  // ---- delete confirmation ----
  $('#delete-cancel').onclick = closeDeleteModal;
  $('#delete-confirm').onclick = confirmDelete;
  // Click the backdrop to dismiss — but only the backdrop itself, not a click
  // that bubbled up from inside the dialog.
  $('#delete-modal').onclick = (e) => {
    if (e.target.id === 'delete-modal') closeDeleteModal();
  };
  // Escape closes it. Expected behaviour for any modal, and it's two lines.
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !$('#delete-modal').hidden) closeDeleteModal();
  });

  $('#cookie-search').oninput = debounce((event) => {
    state.cookieSearch = event.target.value;
    renderCookies();
  }, 180);

  $('#category-filters').onclick = (event) => {
    const chip = event.target.closest('[data-category]');
    if (!chip) return;
    state.cookieFilter = chip.dataset.category;
    $$('#category-filters .chip').forEach((c) =>
      c.classList.toggle('chip-active', c === chip));
    renderCookies();
  };

  /*
    Redraw charts when the window is resized — the SVG width changes, so the
    scales must be recomputed. Debounced, because a single drag fires dozens
    of resize events and redrawing on each would stutter badly.
  */
  window.onresize = debounce(() => {
    if (state.currentReport && $('#view-report').classList.contains('view-active')) {
      redrawCharts();
    }
  }, 200);
}


/* ==========================================================================
   11. STARTUP
   ========================================================================== */

async function init() {
  initTheme();      // BEFORE anything draws, so charts pick up the right colours
  wireEvents();
  initConsentConfigurator();

  const healthy = await checkHealth();
  if (!healthy) return;      // the error banner is already showing

  await loadDomains();

  // Preload the first domain's report so the tab isn't empty when clicked.
  if (state.domains.length) {
    await openReport(state.domains[0].domain, false);
  }
}

/*
  DOMContentLoaded fires when the HTML is parsed and the DOM is ready.

  We need it because a script could otherwise run before the elements it wants
  to find exist, and every `$('#thing')` would return null.

  (Our <script> tags use `defer`, which already guarantees this — the listener
  is belt-and-braces, and makes the dependency explicit to anyone reading.)
*/
document.addEventListener('DOMContentLoaded', init);
