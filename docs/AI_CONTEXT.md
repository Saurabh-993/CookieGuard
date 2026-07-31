# AI_CONTEXT.md — CookieGuard Project Handoff

> **Purpose of this file.** If an AI assistant or a new developer opens this
> project tomorrow with zero prior conversation, this file alone should give
> them everything needed to continue. Read this first, before any code.
>
> **Last updated:** 2026-07-31 · End of **Phase 1**

---

## 1. Project Goal

Build **CookieGuard** — an automated cookie-compliance scanner and consent
manager. It scans websites, detects all cookies and tracking technologies,
classifies them into compliance categories, displays audit reports on a
dashboard, and provides a configurable cookie-consent banner.

Think: **a simplified open-source version of OneTrust.**

### Why this project exists

The developer (Saurabh) is building it for:

1. **Portfolio** — a substantial, end-to-end project.
2. **Two job applications at Caterpillar:**
   - a cookie-compliance / OneTrust role → hence the compliance domain
   - an AWS Cloud Engineer role → hence Docker + CI/CD + AWS phases

**Critical constraint:** he must be able to explain *every single line and
concept* in a technical interview. This drives every decision below.

---

## 2. Developer Profile & Working Constraints

**READ THIS BEFORE WRITING ANY CODE.**

| | |
|---|---|
| **Name** | Saurabh — B.Tech, Information Technology |
| **Comfortable with** | Python, JavaScript, SQL, Node/Express, MongoDB, Pandas, React (beginner) |
| **Complete beginner at** | Web scraping / browser automation, Docker, CI/CD, AWS deployment |
| **Environment** | Windows, PowerShell terminal |
| **Package manager** | **`uv`, NOT pip.** User explicitly requested this — pip was too slow. Use `uv venv`, `uv pip install`. |
| **Python version** | **Pinned to 3.12 via `uv venv --python 3.12`.** Do not use the system Python — see KI-6. |

### Non-negotiable working rules

1. **Teach as you build.** Explain WHY before showing code, never just what.
2. **Build phase by phase.** After each phase, **STOP** and let him run the
   code and ask questions before moving on. Do not run ahead.
3. **Comment every non-obvious line** in the code. The comments in `scan.py`
   are the reference standard for density and tone — match them.
4. **No clever tricks, no over-engineering.** If he can't defend it in an
   interview, it does not belong in the codebase. Prefer boring and explicit
   over concise and clever.
5. **Explanations go in two places:** in the chat conversation AND written
   into `docs/TEACHING.md`. He chose this explicitly.
6. **Update `AI_CONTEXT.md` and `TEACHING.md` at the end of every phase.**

---

## 3. Tech Stack — and Why Each Was Chosen Over Alternatives

| Layer | Chosen | Rejected | Reasoning |
|-------|--------|----------|-----------|
| **Scanner** | Python + **Playwright** | Selenium, BeautifulSoup/requests | Playwright drives a real browser so JS-set cookies are captured; `context.cookies()` returns the *whole* jar including HttpOnly and third-party; first-class network event hooks; bundles its own browser binaries. Selenium needs external chromedriver management and lacks native network interception. BeautifulSoup cannot execute JavaScript at all, so it would miss most real trackers. |
| **Backend** | Python + **FastAPI** | Flask, Django | Async-native (scans take seconds); auto-generated Swagger docs at `/docs` are a strong portfolio demo; Pydantic validation is built in; same language as the scanner so we can `import scan_website` directly with no subprocess or queue. Flask is sync-first with no validation or docs. Django brings an ORM, admin and templating we would not use. |
| **Database** | **SQLite** | PostgreSQL, MongoDB | Zero setup, single file, `sqlite3` is in the Python standard library. Data is strongly relational (domain → scan → cookies), so a relational model with foreign keys fits naturally — that argues against MongoDB. Postgres would add a server process for no MVP benefit. Standard SQL keeps the migration path to Postgres short. |
| **Frontend** | **Vanilla HTML/CSS/JS** | React | The target job description explicitly asks for HTML/CSS/JS. No build step means no npm, bundler or transpiler. It also demonstrates `fetch`, DOM manipulation and events without a framework hiding them. Separately, the consent banner *must* be framework-free to drop into any customer site. |
| **Charts** | **Chart.js via CDN** | D3, hand-rolled canvas | One `<script>` tag, no build step, readable declarative config. D3 is far more powerful but much harder to explain in an interview. |
| **Container** | **Docker** | bare-metal install | Playwright's Chromium needs ~20 Linux shared libraries that vary by distro — the classic deployment pain point. A container pins them. |
| **CI/CD** | **GitHub Actions** | Jenkins | Native to GitHub, free for public repos, one YAML file, no server to maintain. |
| **Deploy** | **AWS EC2 running Docker** | AWS Lambda | Lambda's 250 MB unzipped package limit and 15-min timeout make bundling Chromium painful. EC2 gives OS-level control, which a headless browser needs, and maps directly onto the AWS Cloud Engineer role. |

---

## 4. Architecture & Data Flow

```
                          ┌─────────────────────┐
                          │   USER'S BROWSER    │
                          │  frontend/index.html│
                          └──────────┬──────────┘
                                     │ 1. fetch("/api/scan", {url})
                                     ▼
                          ┌─────────────────────┐
                          │  FastAPI  api/main  │
                          │  validates via      │
                          │  schemas.py         │
                          └──────────┬──────────┘
                                     │ 2. await scan_website(url)
                                     ▼
                          ┌─────────────────────┐
                          │ scanner/scan.py     │
                          │  ┌───────────────┐  │
                          │  │ Chromium via  │  │  3. real browser visits site,
                          │  │  Playwright   │  │     JS runs, cookies get set
                          │  └───────┬───────┘  │
                          │          │ context.cookies()
                          │          │ + page.on("request")
                          └──────────┬──────────┘
                                     │ 4. raw result dict
                                     ▼
                          ┌─────────────────────┐
                          │ scanner/classifier  │  5. match each cookie/request
                          │  + trackers.json    │     against signatures
                          └──────────┬──────────┘
                                     │ 6. categorised result
                                     ▼
                          ┌─────────────────────┐
                          │   api/db.py         │  7. INSERT into
                          │   SQLite file       │     domains/scans/cookies
                          └──────────┬──────────┘
                                     │ 8. JSON response
                                     ▼
                          ┌─────────────────────┐
                          │  frontend/app.js    │  9. render tables + charts
                          └─────────────────────┘
```

**One-sentence version:** dashboard → API → scanner (real browser) →
classifier → SQLite → API → dashboard.

---

## 5. Live Status Checklist

### ✅ Phase 1 — Setup, docs, scanner — **COMPLETE**

- [x] Folder structure created
- [x] `.gitignore`
- [x] `requirements.txt` (pinned versions, commented)
- [x] `README.md` — written before any code, as required
- [x] `docs/AI_CONTEXT.md` — this file
- [x] `docs/TEACHING.md` — Phase 1 concepts
- [x] `scanner/scan.py` — Playwright scanner capturing cookies + network requests
- [x] CLI with `--headed`, `--wait`, `--output` flags
- [x] Pure helper functions unit-verified (13 assertions passing)
- [x] All Playwright API calls verified against v1.48 signatures
- [ ] **PENDING USER ACTION:** run a live scan on the developer's machine
      (the build sandbox could not download Chromium — CDN blocked)

### ⬜ Phase 2 — Classifier + Database — **NEXT**

- [ ] `scanner/trackers.json` — signature DB (~40-60 known trackers)
- [ ] `scanner/classifier.py` — assign category to each cookie
- [ ] Category rules: exact name match → prefix match → domain match → Unknown
- [ ] `api/db.py` — SQLite schema: `domains`, `scans`, `cookies`, `requests`
- [ ] Persist scan results
- [ ] Compliance score calculation

### ⬜ Phase 3 — FastAPI

- [ ] `api/schemas.py` — Pydantic request/response models
- [ ] `api/main.py` — the 7 endpoints documented in README
- [ ] CORS middleware (the dashboard is served from `file://` or a different port)
- [ ] Background task handling for long scans

### ⬜ Phase 4 — Dashboard

- [ ] `frontend/index.html`, `style.css`, `app.js`
- [ ] Domain list, cookie inventory table, category pie/bar charts, audit report
- [ ] Responsive layout

### ⬜ Phase 5 — Consent banner

- [ ] `frontend/consent-banner.js` — Accept All / Reject All / Customise
- [ ] Store preference; block non-necessary cookies until consent

### ⬜ Phase 6 — Docker + CI/CD

- [ ] `Dockerfile` (base off `mcr.microsoft.com/playwright/python`)
- [ ] `docker-compose.yml`
- [ ] `.github/workflows/ci.yml` — lint + pytest on push

### ⬜ Phase 7 — AWS

- [ ] EC2 instance, security group, Docker deploy
- [ ] Optional: ECR for the image, CloudWatch for logs

---

## 6. Key Design Decisions & Reasoning

| # | Decision | Reasoning | Trade-off accepted |
|---|----------|-----------|--------------------|
| 1 | **Read cookies from the `BrowserContext`, not `document.cookie`** | `context.cookies()` returns the entire jar — third-party and `HttpOnly` cookies included. `document.cookie` in JS sees neither, which would miss precisely the cookies regulators care about. | None. This is strictly better. |
| 2 | **Fresh `BrowserContext` per scan** | Guarantees every cookie found was set by *this* scan. Results are reproducible and never polluted by a previous run. | Slightly slower than reusing a context. Worth it. |
| 3 | **Do NOT store cookie *values*** | Values can contain personal data (user IDs, emails). Storing them would make our own audit tool a privacy liability. We store `value_length` only. | Cannot inspect value contents. We only need metadata to classify, so no real loss. This is a strong interview talking point. |
| 4 | **`wait_until="domcontentloaded"` + manual sleep, not `networkidle`** | `networkidle` never fires on sites with continuous polling (live chat, auto-refreshing ads) and would time out on them. Explicit settle time is predictable. | Might miss a tracker that fires after our wait window. Mitigated by the configurable `--wait` flag. |
| 5 | **Realistic desktop User-Agent** | Default headless UA identifies as `HeadlessChrome`; some sites serve reduced content or block it. We want the page a real visitor gets — that's what we're auditing. | Mild deception, but we make no attempt to bypass auth or paywalls. Documented in the README's ethics section. |
| 6 | **EU locale/timezone (`en-GB` / `Europe/London`)** | Under GDPR many sites suppress trackers for EU visitors until consent. Scanning as an EU visitor reveals the pre-consent state, which is the compliance-relevant view. | Won't see the US-visitor tracker set. Could be made configurable later. |
| 7 | **Also capture network requests, not just cookies** | Tracking pixels (1×1 invisible images) and fingerprinting scripts often set no cookie. Cookie-only scanning misses them entirely. Real compliance tools track both. | More data volume. We truncate URLs to 500 chars and aggregate by domain. |
| 8 | **Return a plain `dict`, not a custom class** | Converts directly to JSON for the API, and is trivially inspectable in a debugger or a saved file. | No type safety at this layer. Pydantic will add that at the API boundary in Phase 3. |
| 9 | **Reshape Playwright's cookie dicts into our own field names** | Isolates the rest of the project from Playwright's API. If we ever swap the automation library, only `scan.py` changes. | A small amount of mapping code. |
| 10 | **Catch navigation errors and continue rather than abort** | A slow or partly-broken site may still have set cookies before the timeout. Partial data beats no data for an audit. | Result may be incomplete — signalled via the `error` field, which callers must check. |

---

## 7. Known Issues, Blockers & TODOs

### Known issues

| ID | Issue | Impact | Planned fix |
|----|-------|--------|-------------|
| **KI-1** | `get_registrable_domain()` naively takes the last two labels. Wrong for multi-part public suffixes: `bbc.co.uk` → `"co.uk"`. | Some UK/AU/JP domains misclassified first- vs third-party. | Adopt Mozilla's Public Suffix List via the `tldextract` library. Deferred to avoid an extra dependency in the MVP. |
| **KI-2** | Scans the landing page only. Cookies set on `/checkout` or `/login` are not seen. | Under-reports total cookies on multi-page sites. | Post-MVP: multi-page crawl with a configurable depth. |
| **KI-3** | Consent banners are not interacted with. We capture the *pre-consent* state only. | We don't see cookies that appear only after "Accept All". | Post-MVP: add an optional "click accept" pass and diff the two states. That diff is actually the most valuable compliance signal there is. |
| **KI-4** | Fixed settle time, not adaptive. | Slow sites may not be fully captured. | `--wait` flag exists as a manual workaround. |
| **KI-5** | Sandbox could not download Chromium during Phase 1 build (CDN blocked). Logic was unit-tested and API signatures verified instead of a live run. | Live scan is unverified on real traffic. | **Saurabh must run a live scan locally and report the output.** |
| **KI-6** | **RESOLVED.** First install attempt failed: `Failed building wheel for pydantic-core` / `greenlet`. Root cause = user's system Python was newer than the exact-pinned package versions, so no pre-built wheels existed and pip fell back to compiling Rust/C from source with no toolchain present. | Install blocked entirely. | Fixed two ways: (1) `requirements.txt` switched from `==` exact pins to `>=` ranges; (2) interpreter pinned to Python 3.12 via `uv venv --python 3.12`. Explained in TEACHING.md §25. **Rule going forward: pin the interpreter, float the libraries.** |

### Immediate TODOs

1. **Saurabh:** run `python scanner/scan.py https://example.com --headed` and a
   tracker-heavy site such as `https://www.bbc.com`, then report the output.
2. **Phase 2:** build `trackers.json` and `classifier.py`.
3. Consider `tldextract` when starting Phase 2, since the classifier will do
   heavy domain matching and KI-1 will start to bite.

### Blockers

None. Phase 1 is complete pending the user's live-run confirmation.

---

## 8. File-by-File Summary

| File | Status | What it does |
|------|--------|--------------|
| `README.md` | ✅ Done | Public-facing docs: problem, features, stack rationale, setup, API contract, screenshot placeholders. |
| `.gitignore` | ✅ Done | Excludes `venv/`, `__pycache__/`, `*.db`, scan JSON output, editor and OS files. |
| `requirements.txt` | ✅ Done | Python deps with a comment explaining each. Playwright, FastAPI, Uvicorn, Pydantic, pytest. Uses `>=` ranges, not `==` pins — see KI-6. |
| `requirements.lock.txt` | ⬜ User-generated | Created by `uv pip freeze > requirements.lock.txt`. Exact versions for reproducible CI builds in Phase 6. |
| `scanner/scan.py` | ✅ Done | **The Phase 1 deliverable.** Launches Chromium via Playwright, opens a fresh context, registers a `request` listener, navigates, waits for delayed trackers, reads `context.cookies()`, reshapes everything into a clean dict, prints a formatted terminal report, optionally writes JSON. Also exposes pure helpers `get_registrable_domain`, `classify_party`, `describe_expiry`, `normalise_url`. Entry point `scan_website()` is `async` and will be imported directly by FastAPI in Phase 3. |
| `scanner/classifier.py` | ⬜ Phase 2 | Will take `scan_website()` output and add a `category` field to every cookie. |
| `scanner/trackers.json` | ⬜ Phase 2 | Signature DB: cookie-name patterns and tracker domains → category + vendor. |
| `api/db.py` | ⬜ Phase 3 | SQLite connection, schema creation, insert/query functions. |
| `api/schemas.py` | ⬜ Phase 3 | Pydantic models defining request/response JSON shapes. |
| `api/main.py` | ⬜ Phase 3 | FastAPI app and the seven REST endpoints. |
| `frontend/index.html` | ⬜ Phase 4 | Dashboard markup. |
| `frontend/style.css` | ⬜ Phase 4 | Responsive styling. |
| `frontend/app.js` | ⬜ Phase 4 | `fetch` the API, render tables and charts. |
| `frontend/consent-banner.js` | ⬜ Phase 5 | Standalone drop-in consent banner. |
| `Dockerfile` | ⬜ Phase 6 | Container image including Chromium's system dependencies. |
| `docker-compose.yml` | ⬜ Phase 6 | One-command local startup. |
| `.github/workflows/ci.yml` | ⬜ Phase 6 | Lint + test on every push. |
| `docs/TEACHING.md` | ✅ Phase 1 done | Ground-up concept explanations, diagrams, interview Q&A, glossary. Grows every phase. |
| `docs/AI_CONTEXT.md` | ✅ This file | Project handoff context. Update at the end of every phase. |

---

## 9. The `scan_website()` Output Contract

Everything downstream depends on this shape. **Do not change it without
updating the classifier, the DB schema and the dashboard.**

```jsonc
{
  "url": "https://example.com",          // what was requested
  "final_url": "https://example.com/",   // after any redirects
  "domain": "example.com",               // registrable domain
  "page_title": "Example Domain",
  "http_status": 200,
  "scanned_at": "2026-07-31T09:14:22+00:00",  // ISO 8601, UTC
  "duration_seconds": 3.42,
  "error": null,                         // string if navigation had a problem

  "cookies": [{
    "name": "_ga",
    "domain": ".example.com",
    "path": "/",
    "party": "first",                    // "first" | "third"
    "type": "persistent",                // "session" | "persistent"
    "expires_at": "2028-07-31T09:14:22+00:00",  // null if session
    "lifetime_days": 730,                // null if session
    "http_only": false,
    "secure": true,
    "same_site": "Lax",
    "value_length": 27                   // length only — never the value
  }],

  "cookie_count": 4,
  "first_party_cookies": 3,
  "third_party_cookies": 1,
  "session_cookies": 1,
  "persistent_cookies": 3,

  "total_requests": 41,
  "third_party_domains": [
    { "domain": "google-analytics.com", "request_count": 4 }
  ],
  "requests": [{
    "url": "https://...",                // truncated to 500 chars
    "domain": "www.google-analytics.com",
    "party": "third",
    "resource_type": "script",           // script | image | xhr | font | ...
    "method": "GET"
  }]
}
```

**Phase 2 will add** a `"category"` field to each cookie and a
`"categories": {...}` summary object at the top level.

---

## 10. How to Continue This Project

If you are an AI assistant picking this up:

1. Read this file, then `docs/TEACHING.md`, then `scanner/scan.py`.
2. Check §5 for the current phase and §7 for open issues.
3. **Match the comment density in `scan.py`.** It is the house style.
4. Explain concepts before code. Assume beginner knowledge of browser
   automation, Docker, CI/CD and AWS.
5. Build one phase, then **stop and wait** for the user to run it.
6. At the end of the phase, update §5, §7, §8 of this file and append a new
   phase section to `docs/TEACHING.md`.
