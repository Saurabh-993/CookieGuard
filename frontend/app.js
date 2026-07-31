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

const CATEGORY_COLORS = {
  necessary:  cssVar('--necessary'),
  functional: cssVar('--functional'),
  analytics:  cssVar('--analytics'),
  marketing:  cssVar('--marketing'),
  unknown:    cssVar('--unknown'),
};

const CATEGORY_ORDER = ['necessary', 'functional', 'analytics', 'marketing', 'unknown'];


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
  const dot  = $('#api-status .dot');
  const text = $('#api-status-text');
  try {
    const health = await api('/health');
    dot.className = 'dot dot-ok';
    text.textContent = `Connected · ${health.scans_stored} scans`;
    show($('#global-error'), false);
    return true;
  } catch (err) {
    dot.className = 'dot dot-error';
    text.textContent = 'Offline';
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
  const tbody = $('#domains-tbody');
  show($('#domains-empty'), state.domains.length === 0);

  /*
    BUILDING HTML: why `.map().join('')` and one innerHTML assignment.

    Writing to innerHTML forces the browser to re-parse and re-render. Doing it
    once per row means N reflows; building the whole string first means one.
    With 200 cookies that difference is visible.

    `.map()` transforms each item into a string; `.join('')` glues them
    together with nothing in between.
  */
  tbody.innerHTML = state.domains.map((d) => {
    const score = d.avg_score === null || d.avg_score === undefined
      ? '—'
      : Math.round(d.avg_score);
    const grade = scoreToGrade(score);

    return `
      <tr class="row-clickable" data-domain="${esc(d.domain)}">
        <td><strong>${esc(d.domain)}</strong></td>
        <td class="num">${d.scan_count}</td>
        <td class="num">${d.max_cookies ?? 0}</td>
        <td>
          ${score === '—' ? '—'
            : `<span class="badge" style="background:${gradeColor(grade)}">
                 ${score}/100 &nbsp;${grade}
               </span>`}
        </td>
        <td class="muted">${fmtDate(d.latest_scan)}</td>
        <td><button class="btn-link" data-report="${esc(d.domain)}">Report →</button></td>
      </tr>`;
  }).join('');

  /*
    EVENT DELEGATION.

    Instead of adding a listener to every row (which would need re-adding every
    time we redraw), we add ONE listener to the table body and ask which
    element was actually clicked.

    This works because of EVENT BUBBLING: a click on a <td> also fires on its
    <tr>, then <tbody>, then up to <body>. `event.target` is the deepest
    element; `.closest(sel)` walks back up to find the row we care about.

        one listener, survives redraws     ✅
        N listeners, re-added every render ❌

    Same idea as the Playwright request listener in §15 — register once, react
    to many events.
  */
  tbody.onclick = (event) => {
    const reportBtn = event.target.closest('[data-report]');
    if (reportBtn) {
      openReport(reportBtn.dataset.report);
      return;
    }
    const row = event.target.closest('[data-domain]');
    if (row) openInventory(row.dataset.domain);
  };
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
  status.textContent = `Opening a browser and loading ${url}… this takes ${wait + 10}s or so.`;
  show(status, true);

  try {
    const result = await api('/api/scan', {
      method: 'POST',
      body: JSON.stringify({ url, wait_seconds: wait, save: true }),
    });

    const c = result.compliance || {};
    status.className = 'scan-status alert-success';
    status.innerHTML = `
      <strong>${esc(result.domain)}</strong> scanned —
      ${result.cookie_count} cookies found,
      score <strong>${c.score}/100 (${c.grade})</strong>.
      ${c.cookies_requiring_consent} required consent but were set before it.
      <button class="btn-link" id="goto-report">View report →</button>`;

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
  renderUnknowns();

  // Charts last: they need the SVG elements to have their final size, which
  // only happens once the surrounding layout has been rendered.
  drawDonutChart();
  drawVendorChart();
  drawHistoryChart();
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

  const box = svg.node().getBoundingClientRect();
  const width = box.width;
  const height = box.height;

  // Left margin is generous because vendor names are long.
  const margin = { top: 8, right: 40, bottom: 24, left: 130 };
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;

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
      .attr('width', (d) => x(d.occurrences));

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

  const box = svg.node().getBoundingClientRect();
  const width = box.width;
  const height = box.height;
  const margin = { top: 16, right: 20, bottom: 34, left: 42 };
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;

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
   10. TABS AND WIRING
   ========================================================================== */

function switchView(name) {
  $$('.view').forEach((v) => v.classList.remove('view-active'));
  $(`#view-${name}`).classList.add('view-active');

  $$('.tab').forEach((t) => {
    const active = t.dataset.view === name;
    t.classList.toggle('tab-active', active);
    t.setAttribute('aria-selected', String(active));
  });

  // Charts measure the SVG's rendered width. A hidden element has width 0, so
  // charts drawn while the tab was hidden come out wrong. Redraw on show.
  if (name === 'report' && state.currentReport) {
    drawDonutChart();
    drawVendorChart();
    drawHistoryChart();
  }
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
  $$('.tab').forEach((tab) => {
    tab.onclick = () => switchView(tab.dataset.view);
  });

  $('#scan-form').onsubmit = handleScanSubmit;
  $('#refresh-domains').onclick = loadDomains;

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
      drawDonutChart();
      drawVendorChart();
      drawHistoryChart();
    }
  }, 200);
}


/* ==========================================================================
   11. STARTUP
   ========================================================================== */

async function init() {
  wireEvents();

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
