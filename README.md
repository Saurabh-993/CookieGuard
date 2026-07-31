# 🍪 CookieGuard — Cookie Compliance Scanner & Consent Manager

An automated platform that scans any website, captures every cookie and tracking
technology it uses, classifies them into privacy-compliance categories, stores
audit history, and provides a configurable cookie-consent banner.

In short: **a simplified, open-source version of what OneTrust does.**

---

## 📖 Table of Contents

- [The Problem](#-the-problem)
- [What CookieGuard Does](#-what-cookieguard-does)
- [Features](#-features)
- [Architecture](#-architecture)
- [Tech Stack & Why](#-tech-stack--why-each-choice)
- [Folder Structure](#-folder-structure)
- [Setup & Installation](#-setup--installation)
- [How to Run](#-how-to-run)
- [API Documentation](#-api-documentation)
- [Screenshots](#-screenshots)
- [Build Phases](#-build-phases)
- [Documentation Files](#-documentation-files)
- [Legal & Ethical Note](#-legal--ethical-note)

---

## 🔍 The Problem

Under the **GDPR** (Europe), **ePrivacy Directive** ("the cookie law"), **CCPA/CPRA**
(California) and India's **DPDP Act 2023**, a website may not place non-essential
cookies on a visitor's device *before* obtaining informed consent.

To comply, an organisation must be able to answer three questions at any time:

| Question | Why it's hard |
|----------|---------------|
| **What cookies does our site actually set?** | Marketing teams add tags without telling IT. Third-party scripts silently set their own cookies. |
| **What is each cookie for?** | A cookie named `_ga` means nothing unless you know it's Google Analytics. |
| **Can we prove it?** | Regulators ask for dated evidence, not a screenshot from last year. |

Doing this by hand means opening DevTools, reading the Application tab, googling
every cookie name, and pasting it into a spreadsheet — for every page, every
month. It is slow, error-prone, and impossible to prove afterwards.

**Fines are real:** Google was fined €150m and Facebook €60m by France's CNIL in
2022 specifically over cookie-consent mechanics.

**CookieGuard automates the whole loop:** scan → classify → report → consent.

---

## 🎯 What CookieGuard Does

```
   ┌─────────────┐
   │  You type   │   "scan https://example.com"
   │  a domain   │
   └──────┬──────┘
          │
          ▼
   ┌──────────────────────────────────────┐
   │  SCANNER (Playwright)                │
   │  Opens a real Chromium browser,      │
   │  visits the site, waits for JS to    │
   │  run, then reads:                    │
   │    • every cookie in the jar         │
   │    • every network request made      │
   └──────┬───────────────────────────────┘
          │  raw data
          ▼
   ┌──────────────────────────────────────┐
   │  CLASSIFIER                          │
   │  Matches each cookie/request against │
   │  trackers.json signature database    │
   │  → Necessary / Analytics /           │
   │    Marketing / Functional / Unknown  │
   └──────┬───────────────────────────────┘
          │  classified data
          ▼
   ┌──────────────────────────────────────┐
   │  DATABASE (SQLite)                   │
   │  Stores scan history per domain      │
   └──────┬───────────────────────────────┘
          │
          ▼
   ┌──────────────────────────────────────┐
   │  REST API (FastAPI)                  │
   │  POST /scan   GET /domains           │
   │  GET /scans/{id}  GET /report/{id}   │
   └──────┬───────────────────────────────┘
          │  JSON
          ▼
   ┌──────────────────────────────────────┐
   │  DASHBOARD (HTML/CSS/JS)             │
   │  Tables + charts + audit reports     │
   │  + drop-in consent banner            │
   └──────────────────────────────────────┘
```

---

## ✨ Features

### MVP

1. **Scanner** — loads a website in a real headless browser and captures all
   cookies (first-party *and* third-party) plus every network request, so
   tracking pixels that set no cookie are still detected.
2. **Classifier** — categorises each cookie as **Necessary / Analytics /
   Marketing / Functional**, matched against a tracker-signature database
   (`trackers.json`).
3. **Multi-domain support** — scan and store results for many websites, each
   with full scan history.
4. **REST API** — endpoints to trigger scans, fetch results, and generate reports.
5. **Dashboard** — responsive vanilla HTML/CSS/JS UI showing the domain list, a
   cookie inventory table (name, domain, category, expiry, first/third-party)
   and category-wise charts.
6. **Consent banner** — Accept All / Reject All / Customise, storing the user's
   preference and blocking non-necessary cookies until consent is given.
7. **Audit report** — per-domain compliance summary with scan history and a
   compliance score.

### Planned (post-MVP)

- Multi-page crawling (currently scans the landing page only)
- PDF export of audit reports
- Scheduled recurring scans
- PostgreSQL backend for multi-user deployments

---

## 🏗 Architecture

```
┌──────────────┐   HTTP    ┌──────────────┐  function  ┌──────────────┐
│   Browser    │──────────▶│   FastAPI    │───call────▶│   Scanner    │
│  (Dashboard) │◀──JSON────│   (api/)     │            │  (Playwright)│
└──────────────┘           └──────┬───────┘            └──────┬───────┘
                                  │                            │
                                  │ SQL                        │ raw cookies
                                  ▼                            ▼
                           ┌──────────────┐            ┌──────────────┐
                           │   SQLite     │◀───────────│  Classifier  │
                           │ cookieguard  │  classified│ trackers.json│
                           │    .db       │    data    └──────────────┘
                           └──────────────┘
```

**Data flow in one sentence:** the dashboard calls the API → the API runs the
scanner → the scanner returns raw cookies → the classifier labels them → the
result is saved to SQLite → the API returns JSON → the dashboard renders it.

---

## 🛠 Tech Stack & Why Each Choice

| Layer | Choice | Why this | Why not the alternative |
|-------|--------|----------|-------------------------|
| **Scanner** | **Python + Playwright** | Drives a *real* browser, so JavaScript runs and JS-set cookies are captured. Has a built-in `context.cookies()` API and network event hooks. Auto-waits for elements, so fewer flaky sleeps. Bundles its own browser binaries — one command to install. | **Selenium**: older API, needs a separately managed chromedriver, no first-class network interception. **BeautifulSoup/requests**: only downloads raw HTML — it cannot execute JavaScript, so it would miss the majority of real-world tracking cookies. |
| **Backend** | **Python + FastAPI** | Async by default (a scan takes seconds — async keeps the server responsive). Auto-generates interactive Swagger docs at `/docs`, which is excellent for a portfolio demo. Uses Pydantic for automatic request validation. Same language as the scanner, so no cross-process glue. | **Flask**: synchronous by default, no built-in validation or auto docs. **Django**: heavyweight — ORM, admin, templates, auth — 90% of which we don't need for a JSON API. |
| **Database** | **SQLite** | Zero setup — it's a single file. Ships with Python (`sqlite3` in the standard library). Perfect for a single-machine audit tool. Standard SQL, so migrating to PostgreSQL later is mostly a connection-string change. | **PostgreSQL**: needs a server process, users, and config — unnecessary friction for an MVP. **MongoDB**: our data is highly relational (domain → scan → cookies); joins and foreign keys are exactly what we want, so a relational DB is the better fit. |
| **Frontend** | **Vanilla HTML5 + CSS3 + JS** | No build step, no `node_modules`, no bundler — open `index.html` and it works. Demonstrates that I understand `fetch`, DOM manipulation and the event loop without a framework hiding it. The consent banner **must** be framework-free anyway, since it has to drop into any customer's site. | **React**: would add a toolchain (npm, Vite, JSX transpilation) for a UI that is fundamentally a few tables and charts. Overkill, and it obscures the fundamentals. |
| **Containerisation** | **Docker** | Guarantees the browser binaries and system libraries Playwright needs are present. "Works on my machine" becomes "works everywhere". | Bare-metal install: Playwright needs ~20 Linux shared libraries that differ per distro — a classic source of deployment pain. |
| **CI/CD** | **GitHub Actions** | Native to GitHub, free for public repos, configured with a single YAML file. Runs lint + tests on every push. | Jenkins: needs its own server to be maintained. |
| **Deployment** | **AWS EC2 (Docker)** | Full control over the OS, which a headless browser needs. Directly relevant to the AWS Cloud Engineer role. | **Lambda**: 15-minute timeout and a 250 MB unzipped package limit make bundling Chromium painful. |

---

## 📁 Folder Structure

```
CookieGuard/
├── scanner/
│   ├── scan.py              # Playwright — opens a browser, captures cookies + network requests
│   ├── classifier.py        # Categorises each cookie + computes a compliance score
│   ├── domains.py           # Public Suffix List — "what is the core domain of this host?"
│   └── trackers.json        # Signature database: 276 known trackers (_ga → Analytics, etc.)
│
├── tests/
│   ├── test_classifier.py   # 33 tests for the classification logic
│   ├── test_db.py           # 36 tests for the database layer
│   └── test_api.py          # 45 tests for the REST API
│
├── api/
│   ├── db.py                # SQLite: schema, transactional writes, all queries
│   ├── main.py              # FastAPI app: 10 REST endpoints + SSRF protection
│   └── schemas.py           # Pydantic models describing request/response JSON shapes
│
├── frontend/
│   ├── index.html           # Phase 4 — dashboard page structure
│   ├── style.css            # Phase 4 — all styling; responsive layout
│   ├── app.js               # Phase 4 — fetches the API, renders tables and charts
│   └── consent-banner.js    # Phase 5 — standalone drop-in consent banner
│
├── docs/
│   ├── AI_CONTEXT.md        # Handoff file — full project context for any AI/developer
│   └── TEACHING.md          # Learning file — every concept explained from scratch
│
├── data/                    # Scan output JSON files (git-ignored)
├── .github/workflows/ci.yml # Phase 6 — GitHub Actions CI pipeline
├── Dockerfile               # Phase 6 — container image definition
├── docker-compose.yml       # Phase 6 — one-command local startup
├── requirements.txt         # Python dependencies
├── .gitignore               # Files git should ignore
└── README.md                # This file
```

---

## ⚙️ Setup & Installation

We use **[uv](https://docs.astral.sh/uv/)** rather than pip — it's 10–100×
faster and can install the correct Python version for you, which avoids a whole
class of "no matching wheel" build errors.

### Prerequisites

- **~400 MB free disk** — Playwright downloads its own Chromium browser
- No system Python needed — `uv` will fetch the right interpreter itself

### Step 1 — Install uv (once per machine)

```powershell
# Windows PowerShell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Close and reopen your terminal, then verify:

```bash
uv --version
```

### Step 2 — Create the environment

```bash
cd C:\Code_projects\CookieGuard
uv venv --python 3.12
```

> **Why pin Python 3.12?** Very new Python releases often have no pre-built
> wheels yet for compiled packages like `pydantic-core` (Rust) and `greenlet`
> (C), so the installer falls back to compiling from source and fails. 3.12 has
> wheels for everything in this stack. `uv` downloads it automatically — you do
> not need it installed already. See `docs/TEACHING.md` §25.

Activate it:

```powershell
# Windows PowerShell
.venv\Scripts\Activate.ps1
```

```bash
# macOS / Linux
source .venv/bin/activate
```

### Step 3 — Install dependencies

```bash
uv pip install -r requirements.txt
```

### Step 4 — Install the browser Playwright will drive

A **separate step** from installing packages: step 3 installed the Python
library, this downloads the actual Chromium binary.

```bash
playwright install chromium
```

> **Linux/Docker only:** also run `playwright install-deps chromium` to pull in
> the system shared libraries Chromium needs.

### Step 5 — Verify

```bash
python -c "import playwright; print('Playwright OK')"
python --version          # should print 3.12.x
```

### Step 6 — Lock exact versions (optional, recommended)

```bash
uv pip freeze > requirements.lock.txt
```

`requirements.txt` uses version *ranges* so the project installs cleanly
anywhere; the lockfile records exactly what you installed, so CI builds are
reproducible.

---

## ▶️ How to Run

### Run a scan (Phase 1 — available now)

```bash
python scanner/scan.py https://example.com
```

Useful flags:

```bash
# Show the browser window instead of running invisibly (great for learning)
python scanner/scan.py https://example.com --headed

# Save the result to a JSON file
python scanner/scan.py https://example.com --output data/example.json

# Wait longer for slow, tracker-heavy sites
python scanner/scan.py https://example.com --wait 8
```

Sample output:

```
============================================================
  CookieGuard Scan Report
============================================================
  URL scanned : https://example.com
  Final URL   : https://example.com/
  Page title  : Example Domain
  Duration    : 3.42s

  COOKIES FOUND: 4
  ---------------------------------------------------------
  NAME            DOMAIN            PARTY   TYPE
  _ga             .example.com      first   persistent
  _gid            .example.com      first   persistent
  _fbp            .facebook.com     third   persistent
  session_id      example.com       first   session

  THIRD-PARTY DOMAINS CONTACTED: 3
  ---------------------------------------------------------
  google-analytics.com          2 request(s)
  connect.facebook.net          1 request(s)
  fonts.googleapis.com          1 request(s)
============================================================
```

### Classify a scan (Phase 2a — available now)

Scan first, saving the result, then classify it:

```bash
python scanner/scan.py https://www.bbc.com --output data/bbc.json
python scanner/classifier.py data/bbc.json
```

Save the classified output too:

```bash
python scanner/classifier.py data/bbc.json --output data/bbc_classified.json
```

Sample output:

```
  COMPLIANCE SCORE: 13/100   Grade: F
  Failing — extensive tracking before any consent

  Cookies set BEFORE consent that legally require it: 14

  Points deducted for:
     -39  Marketing cookies set before consent (x7)
     -21  Analytics cookies set before consent (x4)
     -10  Unclassified cookies requiring manual review (x2)

  CATEGORY BREAKDOWN
  [OK] Necessary     3  ###
  [  ] Functional    1  #
  [! ] Analytics     4  ####
  [!!] Marketing     7  #######
  [??] Unknown       2  ##

  NAME             CATEGORY    VENDOR                 PARTY  MATCHED BY
  _fbp             marketing   Meta (Facebook Pixel)  first  exact name '_fbp'
  IDE              marketing   Google DoubleClick     third  exact name 'IDE'
  _ga              analytics   Google Analytics       first  exact name '_ga'
  PHPSESSID        necessary   PHP                    first  exact name 'PHPSESSID'
```

### Store and query scans (Phase 2b — available now)

```bash
python api/db.py init                    # create the database
python api/db.py save data/bbc.json      # classify and store a scan
python api/db.py list                    # every domain scanned
python api/db.py history bbc.com         # scan history
python api/db.py report bbc.com          # audit report + trend
python api/db.py show 2                  # one scan in detail
```

Sample report:

```
  LATEST RESULT
  Score  : 33/100   Grade: F
  Cookies: 24  (necessary 4, analytics 16, marketing 3, unknown 1)

  ACROSS ALL SCANS
  Average score : 16.5
  Best / worst  : 33 / 0
  Trend         : IMPROVING

  SCORE HISTORY
  2026-07-31    0
  2026-08-14   33  #############
```

### Run the tests

```bash
pytest -v
```

### Run the API (Phase 3 — available now)

```bash
uvicorn api.main:app --reload
```

Then open **http://127.0.0.1:8000/docs** — an interactive API console,
generated automatically from the code. You can run every endpoint from the
browser.

**Windows PowerShell** has two traps here:

1. Bare `curl` is an alias for `Invoke-WebRequest`, which takes completely
   different arguments. Type `curl.exe` to get the real curl (it ships with
   Windows 10+).
2. `Invoke-RestMethod` **throws a terminating error on any non-2xx status**.
   So a deliberate `400` — like the SSRF guard rejecting a URL — appears as a
   red PowerShell exception even though the request worked perfectly.

```powershell
curl.exe http://127.0.0.1:8000/health
curl.exe http://127.0.0.1:8000/api/domains

curl.exe -X POST http://127.0.0.1:8000/api/scan `
  -H "Content-Type: application/json" `
  -d "{\"url\": \"https://example.com\", \"wait_seconds\": 5}"
```

If you prefer the PowerShell cmdlet, catch the error to read the body:

```powershell
try {
  Invoke-RestMethod -Method POST http://127.0.0.1:8000/api/scan `
    -ContentType "application/json" `
    -Body '{"url": "https://example.com"}'
} catch {
  $_.ErrorDetails.Message      # the response body lives here on an error status
}
```

**Simplest of all:** use the `/docs` page. Click an endpoint → "Try it out" →
"Execute". Error responses render in a readable panel, with no shell quoting.

**macOS / Linux:**

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/domains

curl -X POST http://127.0.0.1:8000/api/scan \
     -H "Content-Type: application/json" \
     -d '{"url": "https://example.com", "wait_seconds": 5}'
```

Point the API at a different database with an environment variable:

```bash
COOKIEGUARD_DB=/path/to/other.db uvicorn api.main:app
```

### Open the dashboard (Phase 4 — coming soon)

```bash
# With the API running, open frontend/index.html in a browser
```

---

## 🔌 API Documentation

Base URL: `http://127.0.0.1:8000`

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/` | API info and links |
| `GET` | `/health` | Liveness + database check (used by Docker & CI) |
| `POST` | `/api/scan` | Trigger a new scan. `201 Created`. |
| `GET` | `/api/domains` | List every domain ever scanned |
| `GET` | `/api/domains/{domain}/scans` | Scan history, newest first. `?limit=` |
| `GET` | `/api/domains/{domain}/latest` | Most recent scan, full detail |
| `GET` | `/api/scans/{scan_id}` | Full result of one scan |
| `GET` | `/api/scans/{scan_id}/cookies` | Cookie inventory. `?category=` filter |
| `DELETE` | `/api/scans/{scan_id}` | Delete a scan. `204 No Content`. |
| `GET` | `/api/report/{domain}` | Compliance summary, vendors, history, trend |

### Security note

`POST /api/scan` makes the **server** fetch a user-supplied URL, which is an
SSRF risk. Requests to `localhost`, private network ranges, `.local`
hostnames, non-HTTP schemes, and the cloud instance metadata endpoint
(`169.254.169.254`) are rejected with `400`. Hostnames are resolved before
checking, since an attacker controls their own DNS. See `docs/TEACHING.md` §50.

### `POST /api/scan`

Request:

```json
{
  "url": "https://example.com",
  "wait_seconds": 5
}
```

Response `201 Created`:

```json
{
  "scan_id": 12,
  "domain": "example.com",
  "scanned_at": "2026-07-31T09:14:22Z",
  "cookie_count": 4,
  "categories": {
    "necessary": 1,
    "analytics": 2,
    "marketing": 1,
    "functional": 0,
    "unknown": 0
  }
}
```

Errors: `400` invalid URL · `422` validation failed · `504` site took too long.

Once the API exists, FastAPI will auto-generate live, testable documentation at
`/docs` — no manual maintenance needed.

---

## 📸 Screenshots

> Placeholders — to be filled in as each phase completes.

| View | Screenshot |
|------|-----------|
| Scanner CLI output | `docs/images/scanner-cli.png` *(Phase 1)* |
| Dashboard — domain list | `docs/images/dashboard-domains.png` *(Phase 4)* |
| Dashboard — cookie inventory | `docs/images/dashboard-cookies.png` *(Phase 4)* |
| Dashboard — category chart | `docs/images/dashboard-chart.png` *(Phase 4)* |
| Consent banner | `docs/images/consent-banner.png` *(Phase 5)* |
| Audit report | `docs/images/audit-report.png` *(Phase 4)* |
| CI pipeline passing | `docs/images/github-actions.png` *(Phase 6)* |

---

## 🚧 Build Phases

| Phase | Scope | Status |
|-------|-------|--------|
| **1** | Project setup, documentation, Playwright scanner | ✅ **Done** |
| **2a** | Classifier + `trackers.json` + tests | ✅ **Done** |
| **2b** | SQLite database + Public Suffix List fix | ✅ **Done** |
| **3** | FastAPI REST endpoints | ✅ **Done** |
| **4** | Dashboard (tables + charts + audit report) | ⬜ Next |
| **5** | Consent banner | ⬜ Pending |
| **6** | Docker + GitHub Actions CI/CD | ⬜ Pending |
| **7** | AWS deployment | ⬜ Pending |

---

## 📚 Documentation Files

| File | Read it when |
|------|--------------|
| `README.md` | You want to know what this project is and how to run it. |
| `docs/AI_CONTEXT.md` | You (or an AI assistant) are picking the project up cold and need full context. |
| `docs/TEACHING.md` | You want every concept in this codebase explained from zero, with interview Q&A. |

---

## ⚖️ Legal & Ethical Note

CookieGuard only visits publicly accessible pages and reads what any visitor's
browser would already receive — it does not log in, bypass authentication, or
attempt to access private data. Still, scan responsibly:

- Scan sites you own, or that you have permission to audit.
- Respect `robots.txt` and rate limits; don't hammer a server with rapid scans.
- The classification output is a technical aid, **not legal advice**. Compliance
  decisions should be reviewed by a qualified privacy professional.

---

## 👤 Author

**Saurabh Sharma** — B.Tech (Information Technology)
Built as a portfolio project demonstrating browser automation, REST API design,
compliance-domain knowledge, containerisation and CI/CD.

---

## 📄 Licence

MIT
