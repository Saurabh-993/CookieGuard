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
- [Run with Docker](#-run-with-docker)
- [Continuous Integration](#-continuous-integration)
- [Deploy to AWS](#-deploy-to-aws)
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
2. **Pre/post-consent diff** — optionally clicks "Accept all" and scans again,
   reporting exactly what accepting unlocks. This is the finding that matters:
   *"4 cookies before consent, 61 after"* says far more than *"38 cookies"*.
3. **Classifier** — categorises each cookie as **Necessary / Analytics /
   Marketing / Functional**, matched against a tracker-signature database
   (`trackers.json`).
4. **Multi-domain support** — scan and store results for many websites, each
   with full scan history.
5. **REST API** — endpoints to trigger scans, fetch results, and generate reports.
6. **Dashboard** — responsive vanilla HTML/CSS/JS UI showing the domain list, a
   cookie inventory table (name, domain, category, expiry, first/third-party)
   and category-wise charts.
7. **Consent banner** — Accept All / Reject All / Customise, storing the user's
   preference and blocking non-necessary cookies until consent is given.
8. **Audit report** — per-domain compliance summary with scan history and a
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
| **Charts** | **D3.js (CDN)** | Real per-element interactivity — hover tooltips, staggered animations, a self-drawing trend line. Full control over every shape. | **Chart.js**: far easier, but canvas-based, so you can't style or click individual elements, and customisation hits a wall quickly. |
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
│   ├── jurisdictions.py     # vendor → country → GDPR transfer region
│   └── trackers.json        # Signature database: 276 known trackers (_ga → Analytics, etc.)
│
├── tests/
│   ├── test_classifier.py   # 33 tests for the classification logic
│   ├── test_db.py           # 36 tests for the database layer
│   ├── test_api.py          # 47 tests for the REST API
│   └── test_consent_banner.py # 34 tests for the consent banner
│
├── api/
│   ├── db.py                # SQLite: schema, transactional writes, all queries
│   ├── main.py              # FastAPI app: 10 REST endpoints + SSRF protection
│   └── schemas.py           # Pydantic models describing request/response JSON shapes
│
├── frontend/
│   ├── index.html           # dashboard structure (filled at runtime by app.js)
│   ├── style.css            # all styling; responsive + print
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

# THE BEST ONE: scan, click "Accept all", scan again, report the difference
python scanner/scan.py https://www.bbc.com --accept-consent
```

The `--accept-consent` pass answers the question people actually have:

```
  CONSENT BANNER
  ----------------------------------------------------------
  Clicked: "Accept All Cookies"
  Found via: cmp_selector (OneTrust)

  BEFORE consent: 4 cookies
  AFTER consent:  61 cookies   (+57)
  Accepting multiplied tracking by 15.3x
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

## 🐳 Run with Docker

Phase 6 — **available now.** One command, and you don't need Python,
Playwright or Chromium installed at all.

```bash
docker compose up --build
```

Then open **http://127.0.0.1:8000/dashboard/**.

```bash
docker compose logs -f      # follow the logs
docker compose down         # stop (your data survives)
docker compose down -v      # stop AND DELETE the database volume
```

### What the setup does, and why

| Choice | Reason |
|---|---|
| `FROM mcr.microsoft.com/playwright/python` | Chromium plus its ~30 Linux libraries, preinstalled and version-matched. Installing them by hand is 40 lines of `apt-get` and fails with errors naming missing *symbols*. |
| Dependencies copied **before** source | Docker caches layers. Reversed, every one-character code edit would reinstall Playwright and re-download a browser. |
| Runs as non-root `pwuser` | Root in a container is close to root on the host. We drive a browser at untrusted URLs. |
| Database in a `/data` **volume** | A container's filesystem is destroyed when the container is replaced — which happens on every deploy. A database inside the image vanishes silently. |
| `cap_add: SYS_ADMIN`, **not** `--no-sandbox` | Grants the one capability Chromium's sandbox needs, rather than switching the sandbox off. We point this browser at URLs typed by strangers. |
| `shm_size: 1gb` | Docker's default `/dev/shm` is 64MB; Chromium crashes above that. The symptom is `Target closed`, which reads like a Playwright bug. |
| Port bound to `127.0.0.1` | `"8000:8000"` would publish to your whole network — on a cloud VM, the internet. Docker edits iptables directly and bypasses your firewall to do it. |

### Configuration

Every setting is an environment variable. Copy the template and edit:

```bash
cp .env.example .env
```

| Variable | Default | Notes |
|---|---|---|
| `COOKIEGUARD_DB` | `./data/cookieguard.db` | Must be `/data/...` in Docker |
| `COOKIEGUARD_HOST` | `127.0.0.1` | Must be `0.0.0.0` in a container, or the port mapping silently receives nothing |
| `CORS_ORIGINS` | `http://localhost:8000,http://127.0.0.1:8000` | Comma-separated. No trailing slashes |
| `CORS_ALLOW_ALL` | `false` | Escape hatch. `CORS_ORIGINS=*` does **not** work |
| `BROWSER_NO_SANDBOX` | `false` | Real security reduction — read the note in `scanner/scan.py` first |
| `ENVIRONMENT` | `development` | Shown on `/health` |

`python api/config.py` prints the effective configuration, and the app prints
it on every startup.

---

## 🔄 Continuous Integration

`.github/workflows/ci.yml` runs on every push and pull request to `main`.

```
   lint  ─┐
          ├─►  docker: build + SMOKE TEST  ─►  publish to GHCR (main only)
   test  ─┘
   ~15s      ~2 min                             ~1 min
   ~90s
```

| Job | What it does |
|---|---|
| **lint** | `ruff check .` — separate job so a syntax error tells you in 15 seconds, not 2 minutes |
| **test** | Full pytest suite on Python **3.11 and 3.12** in parallel, `fail-fast: false` so you see both results at once |
| **docker** | Builds the image, **starts the container**, and polls `/health` until it responds |
| **publish** | Pushes to GitHub Container Registry, tagged `:latest` **and** `:<git-sha>` |

**Why the smoke test matters.** A successful `docker build` proves the image
was *assembled*. It proves nothing about whether the app *starts* — and that
gap is where every container-shaped bug lives. It caught a real one here: the
HEALTHCHECK pointed at `/api/health` when the route is `/health`, which would
have made the container report unhealthy forever while working perfectly.

**Deploy by SHA, not `:latest`.** `:latest` tells you nothing about what's
running. The SHA tag identifies the exact commit, and rollback is `docker run`
with an older one.

---

## ☁️ Deploy to AWS

Phase 7 — **available now.** Full step-by-step playbook: **[`deploy/README.md`](deploy/README.md)**

```
        internet
           │ HTTPS
           ▼
   ┌──────────────────────────────────────────┐
   │  EC2 t3.micro · Ubuntu 24.04 · 1 GB RAM  │
   │   ┌───────┐        ┌────────────────┐    │
   │   │ Caddy │───────►│  cookieguard   │    │
   │   │ :443  │  :8000 │  (no public    │    │
   │   └───────┘        │   port at all) │    │
   │    Let's Encrypt   └───────┬────────┘    │
   │    auto-renewed        /data volume      │
   └──────────────────────────────────────────┘
                  ▲
                  │ docker pull  (never builds)
            ghcr.io/<you>/cookieguard:<sha>
```

**Cost: $0** on the 12-month free tier, with a free DuckDNS hostname and a free
Let's Encrypt certificate. (~$2/year if you'd rather own a real domain.)

Once the server is bootstrapped, deploying is:

```bash
cd ~/cookieguard/deploy && ./deploy.sh
```

Three decisions worth knowing:

- **The server pulls, it never builds.** Production runs the bit-identical
  artifact CI smoke-tested — not "the same source, rebuilt, hopefully the same
  way". A t3.micro would also take ~20 minutes and might run out of memory.
- **The app container publishes no port.** Only Caddy is reachable. Publishing
  8000 would bypass TLS and every security header, and automated scanners sweep
  the public IPv4 space continuously — obscurity buys about twenty minutes.
- **A 2 GB swap file is mandatory.** 1 GB of RAM plus a headless Chromium means
  the OOM killer fires during scans, and it often picks `sshd` rather than the
  process at fault — so you can't log in to diagnose it.

---

### Run the tests

```bash
pytest -q          # 260 tests
ruff check .       # linting
```

Two test files run JavaScript from `frontend/` under **Node** — the globe's
quaternion maths and the chart layout maths. They skip cleanly if Node isn't
installed, but CI installs it so they actually run.

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

### Try the consent banner (Phase 5 — available now)

**http://127.0.0.1:8000/dashboard/demo.html**

A pretend website with three blocked trackers. Watch them stay inert until you
consent, then activate — and watch the cookies get deleted when you withdraw.

**This is the 30-second demo.** It shows the whole compliance argument: nothing
fires before consent, rejecting takes one click, and withdrawal is always
available.

To use the banner on a real site, one script tag plus a marker on each tracker:

```html
<script src="consent-banner.js" data-policy-url="/privacy"></script>

<!-- BLOCKED until consent. type="text/plain" makes the browser
     ignore the tag entirely — it isn't even downloaded. -->
<script type="text/plain" data-cookieguard="analytics"
        src="https://www.googletagmanager.com/gtag/js?id=G-XXXX"></script>
```

No framework, no build step, no dependencies. The banner injects its own styles.

### Open the dashboard (Phase 4 — available now)

With the API running, open:

**http://127.0.0.1:8000/dashboard/**

That's it — FastAPI serves the dashboard itself, so the page and the API are on
the same origin and there's no CORS involved.

The dashboard has three tabs:

| Tab | What it shows |
|-----|---------------|
| **Domains** | Every scanned site, with average score. Includes a form to run a new scan. |
| **Cookie Inventory** | Full cookie table for a chosen scan, filterable by category and searchable. |
| **Audit Report** | Score, trend, seven interactive D3 charts, and a **Download PDF** button. |
| **Scan History** | Every scan across all domains, filterable by domain and grade, with paging and a delete confirmation flow. |

There's a 🌙 / ☀️ toggle in the header for dark mode. It remembers your choice
and defaults to your operating system's setting.

#### Audit report metrics

| Metric | Why it matters |
|--------|----------------|
| **Category donut** | The consent split at a glance |
| **Top vendors** | Which companies appear most across all scans |
| **World map** | D3 choropleth — every country coloured by GDPR transfer region. Hover for vendors. |
| **Where does the data go?** | GDPR Chapter V — how many cookies come from vendors outside the EEA. On CNN this reads **89.8%**. |
| **Cookie lifetimes** | Histogram bucketed around CNIL's recommended 13-month maximum |
| **Cookie security** | Secure / HttpOnly / cross-site tracker percentages |
| **Third-party treemap** | Every external domain contacted, sized by request count |
| **Score over time** | Trend line — is this site improving? |

You can also open `frontend/index.html` directly from disk — it detects
`file://` and points at `http://127.0.0.1:8000` — but the served version is
preferred.

> Charts need **D3.js**, which loads from a CDN, so the first load needs
> internet access. Without it the tables still work and the charts show a
> message.

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
| `GET` | `/api/report/{domain}/pdf` | **Download the audit report as a PDF** |
| `GET` | `/api/scans` | Scan history across all domains. `?domain=` `?grade=` `?limit=` `?offset=` |

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
| **4** | Dashboard (tables + charts + audit report) | ✅ **Done** |
| **5** | Consent banner | ✅ **Done** |
| **6** | Docker + GitHub Actions CI/CD | ✅ **Done** |
| **7** | AWS deployment (EC2 + Caddy + HTTPS) | ✅ **Done** |

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
