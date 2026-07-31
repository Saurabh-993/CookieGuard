# 📘 TEACHING.md — Learn CookieGuard From Zero

> **This is your learning and interview-prep file.** Every concept that appears
> anywhere in this codebase is explained here from the ground up, with
> diagrams, analogies, and interview questions with answers.
>
> **Read it in order the first time.** After that, use the table of contents to
> jump to whatever you need to revise.
>
> **Last updated:** End of **Phase 1**

---

## 📑 Table of Contents

**PART A — The Domain: Cookies & Privacy**
1. [What is a cookie?](#1-what-is-a-cookie)
2. [How a cookie actually travels](#2-how-a-cookie-actually-travels)
3. [First-party vs third-party cookies](#3-first-party-vs-third-party-cookies)
4. [Session vs persistent cookies](#4-session-vs-persistent-cookies)
5. [Cookie attributes and security flags](#5-cookie-attributes-and-security-flags)
6. [Tracking pixels and other tracking tech](#6-tracking-pixels-and-other-tracking-tech)
7. [The four compliance categories](#7-the-four-compliance-categories)
8. [The laws: GDPR, ePrivacy, CCPA, DPDP](#8-the-laws-gdpr-eprivacy-ccpa-dpdp)

**PART B — The Technology: How We Capture It**
9. [Client, server, HTTP — the basics](#9-client-server-http--the-basics)
10. [The DOM](#10-the-dom)
11. [Web scraping vs browser automation](#11-web-scraping-vs-browser-automation)
12. [Headless browsers](#12-headless-browsers)
13. [Playwright: browser → context → page](#13-playwright-browser--context--page)
14. [Synchronous vs asynchronous, async/await](#14-synchronous-vs-asynchronous-asyncawait)
15. [Event listeners and callbacks](#15-event-listeners-and-callbacks)
16. [JSON](#16-json)

**PART C — Technology Choices Defended**
17. [Playwright vs Selenium vs BeautifulSoup](#17-playwright-vs-selenium-vs-beautifulsoup)
18. [FastAPI vs Flask vs Django](#18-fastapi-vs-flask-vs-django)
19. [SQLite vs PostgreSQL vs MongoDB](#19-sqlite-vs-postgresql-vs-mongodb)
20. [Vanilla JS vs React](#20-vanilla-js-vs-react)

**PART D — Python Concepts Used in Phase 1**
21. [Python features in scan.py](#21-python-features-in-scanpy)

**PART E — Walkthrough & Prep**
22. [scan.py line-by-line walkthrough](#22-scanpy-line-by-line-walkthrough)
23. [Common Interview Questions & Answers](#23-common-interview-questions--answers)
24. [Glossary](#24-glossary)

**PART F — Environments & Packaging**
25. [venv, pip, uv, wheels — and the error you hit](#25-venv-pip-uv-wheels--and-the-error-you-hit)

---
---

# PART A — The Domain: Cookies & Privacy

## 1. What is a cookie?

**A cookie is a small piece of text that a website asks your browser to store
and hand back on every future visit.**

That's it. Not a program. Not a virus. Just a name and a value, like
`user_id = 8a3f9c`, plus some settings about when to send it and when to
delete it.

### Why cookies exist: HTTP has amnesia

The web runs on HTTP, and HTTP is **stateless** — every request is completely
independent, with no memory of any previous one.

```
Without cookies:

  You → server:  "Show me the login page"
  server → you:  <login page>
  You → server:  "Here's my username and password"
  server → you:  "Correct! You're logged in."
  You → server:  "Show me my account"
  server → you:  "Who are you?"          ← total amnesia
```

The server genuinely has no idea the third request came from the same person
as the second. Cookies fix this:

```
With cookies:

  You → server:  "Here's my username and password"
  server → you:  "Correct!  Set-Cookie: session=abc123"
                                    ↑ server asks browser to remember this
  You → server:  "Show me my account.  Cookie: session=abc123"
                                            ↑ browser attaches it automatically
  server → you:  "Ah, session abc123 — that's Saurabh. Here's your account."
```

### The real-world analogy

You check your coat at a theatre and get a **numbered ticket**. The ticket
isn't your coat — it's just a number. But when you hand it back, the attendant
knows which coat is yours.

- The ticket = the cookie
- The number on it = the cookie value
- The cloakroom's records = the server's session store
- "Valid tonight only" = the expiry
- **You carry the ticket, not the theatre** = cookies live in *your* browser

### Where cookies are actually stored

On your own machine, in a file the browser manages. On Chrome/Windows it's a
SQLite database at roughly:

```
C:\Users\<you>\AppData\Local\Google\Chrome\User Data\Default\Network\Cookies
```

**This is the single most important fact for understanding privacy law.** A
cookie is data stored *on your device*. That's why the EU ePrivacy Directive
regulates it — the law is fundamentally about a company writing to equipment
that belongs to you.

### See one right now

Open any website → press `F12` → **Application** tab → **Cookies** in the left
sidebar. Everything CookieGuard does is automating that panel.

---

## 2. How a cookie actually travels

There are **two ways** a cookie gets created, and understanding both is
essential to understanding why our scanner is built the way it is.

### Route 1 — The HTTP `Set-Cookie` header

```
  BROWSER                                    SERVER
     │                                          │
     │  GET /index.html HTTP/1.1                │
     │─────────────────────────────────────────▶│
     │                                          │
     │  HTTP/1.1 200 OK                         │
     │  Set-Cookie: session=abc123; Path=/;     │
     │              Max-Age=3600; HttpOnly      │
     │  Content-Type: text/html                 │
     │◀─────────────────────────────────────────│
     │                                          │
  [browser stores it]                           │
     │                                          │
     │  GET /account HTTP/1.1                   │
     │  Cookie: session=abc123     ← automatic  │
     │─────────────────────────────────────────▶│
```

The key word is **automatic**. Once stored, the browser attaches that cookie to
every matching request without being asked. Nobody clicks anything.

### Route 2 — JavaScript sets it

```javascript
document.cookie = "theme=dark; max-age=31536000";
```

This is the route that matters for us. **Google Analytics, Facebook Pixel and
most tracking cookies are created this way** — by JavaScript running *after*
the page has downloaded.

### Why this determines our whole architecture

```
┌──────────────────────────────────────────────────────────────┐
│  If you only download the HTML  (requests / BeautifulSoup):  │
│                                                              │
│    You see:      Set-Cookie headers          ✅ Route 1      │
│    You MISS:     everything JavaScript sets  ❌ Route 2      │
│                                                              │
│  Since Route 2 is where nearly all tracking lives, an        │
│  HTML-only scanner would report almost nothing useful.       │
│                                                              │
│  ▶ Therefore we must run a REAL BROWSER that executes the    │
│    JavaScript. That is exactly what Playwright gives us.     │
└──────────────────────────────────────────────────────────────┘
```

**This is the single best answer to "why did you choose Playwright?" in an
interview.** It isn't preference — it's a hard requirement.

---

## 3. First-party vs third-party cookies

This is *the* central distinction in cookie compliance.

| | First-party | Third-party |
|---|---|---|
| **Set by** | The site in your address bar | Some other company's embedded script |
| **Domain matches the site?** | Yes | No |
| **Typical purpose** | Login, cart, language, theme | Advertising, cross-site tracking |
| **Can track you across sites?** | No | **Yes** |
| **Needs consent?** | Only if non-essential | **Almost always** |
| **Browsers blocking it?** | No | Yes — Safari and Firefox already do |

### The diagram that makes it click

```
   You visit  news.com
   ┌──────────────────────────────────────────────────────────┐
   │  Address bar:  https://news.com                          │
   │                                                          │
   │  news.com sets:      session_id=xyz    (domain: news.com)│
   │                      └─▶ FIRST party — domains match     │
   │                                                          │
   │  news.com's HTML embeds:                                 │
   │      <script src="https://connect.facebook.net/..."></script>
   │                                                          │
   │  facebook.com sets:  _fbp=fb.1.16...  (domain: .facebook.com)
   │                      └─▶ THIRD party — domains differ    │
   └──────────────────────────────────────────────────────────┘
```

### Why third-party cookies are the whole privacy problem

```
   Monday    you visit  news.com      → facebook.com sees _fbp = USER_7781
   Tuesday   you visit  shoes.com     → facebook.com sees _fbp = USER_7781
   Wednesday you visit  clinic.com    → facebook.com sees _fbp = USER_7781

   Facebook now knows USER_7781 reads news, shops for shoes, and
   visited a medical site — even though you never logged into Facebook
   on any of those sites, and none of them told Facebook about you
   deliberately.
```

That capability — building a profile across unrelated websites — is what
regulators mean by "tracking", and it is why it requires explicit consent.

### How CookieGuard decides

In `scan.py`:

```python
def classify_party(cookie_domain, site_domain):
    return "first" if get_registrable_domain(cookie_domain) == site_domain else "third"
```

We must first **normalise** both sides, because these are all the same company:

```
   .example.com      ┐
   www.example.com   ├──▶  example.com     ← "registrable domain"
   shop.example.com  ┘
```

A naive string comparison of `".example.com" == "example.com"` returns
`False` and would wrongly flag a first-party cookie as third-party. That's why
`get_registrable_domain()` exists.

> **Known limitation (honest answer for interviews):** our version takes the
> last two labels, so `bbc.co.uk` incorrectly reduces to `co.uk`. The correct
> fix is Mozilla's **Public Suffix List**, available via the `tldextract`
> library. Knowing the limitation *and* naming the proper fix scores better in
> an interview than pretending the code is perfect.

---

## 4. Session vs persistent cookies

```
  SESSION COOKIE                    PERSISTENT COOKIE
  ──────────────                    ─────────────────
  No expiry set                     Has Expires or Max-Age
  Lives in RAM                      Written to disk
  Dies when browser closes          Survives restarts, reboots
  Playwright reports expires = -1   Playwright reports a Unix timestamp
  Lower privacy risk                Higher — enables long-term tracking

  Example: your login session       Example: _ga lives 2 years
```

### Why lifetime matters for compliance

A cookie that lives **two years** can follow you far longer than one that dies
when you close the tab. Regulators have pushed back hard on excessive
lifetimes — France's CNIL recommends a **13-month maximum** for analytics
cookies. So "how long does this cookie live?" is a compliance question, not
just a technical one.

That's why `describe_expiry()` computes `lifetime_days` — in Phase 2 we'll use
it to flag cookies with unreasonably long lifetimes in the audit report.

### The two ways to express lifetime

```
Set-Cookie: id=1; Expires=Wed, 31 Jul 2027 09:14:22 GMT   ← absolute date
Set-Cookie: id=1; Max-Age=31536000                        ← seconds from now
```

`Max-Age` wins if both are present. Playwright normalises both into a single
Unix timestamp for us.

### Unix timestamps

A **Unix timestamp** is the number of seconds since midnight, 1 January 1970
UTC — a single number that means the same instant everywhere on Earth, with no
timezone or date-format ambiguity.

```python
datetime.fromtimestamp(1785000000, tz=timezone.utc)
# → datetime(2026, 7, 25, ...)
```

---

## 5. Cookie attributes and security flags

A cookie is more than a name and value. `scan.py` captures these because each
one carries compliance meaning.

| Attribute | What it does | Why it matters to us |
|-----------|--------------|----------------------|
| `Domain` | Which hosts receive the cookie. A leading dot (`.example.com`) means "and all subdomains". | This is what we compare to decide first- vs third-party. |
| `Path` | Which URL paths receive it. `/` = the whole site. | `/admin` scoping is a minor security signal. |
| `Expires` / `Max-Age` | When to delete it. | Session vs persistent; excessive lifetimes get flagged. |
| `HttpOnly` | JavaScript **cannot** read this cookie (`document.cookie` won't show it). | Protects session tokens against XSS. Its absence on a session cookie is a security finding. |
| `Secure` | Only ever sent over HTTPS. | Without it, the cookie can be sniffed on plain HTTP. |
| `SameSite` | Controls sending on cross-site requests. | **The tracking-relevant one** — see below. |

### `SameSite` explained

```
  SameSite=Strict   Never sent on any cross-site request.
                    Most private. Can break "click a link from email
                    into the site and still be logged in".

  SameSite=Lax      Sent on top-level navigations (clicking a link),
                    not on embedded requests (images, iframes, scripts).
                    The modern browser default. Sensible balance.

  SameSite=None     Always sent, including from third-party iframes
                    and scripts. Must be paired with Secure.
                    ▶ THIS IS THE TRACKER'S SETTING. A third-party
                      cookie with SameSite=None is, almost by
                      definition, built for cross-site tracking.
```

So in our audit report, `party=third` **and** `SameSite=None` is the strongest
single signal of a cross-site tracker.

### A deliberate design decision: we do NOT store cookie values

Look at this line in `scan.py`:

```python
"value_length": len(c.get("value", "")),
```

We record how *long* the value is, never the value itself.

**Why:** cookie values frequently contain personal data — user IDs, hashed
emails, session tokens. If our audit database stored them, our privacy tool
would itself become a privacy liability, and a breach of it would be a data
breach. We only need metadata to classify a cookie; the value adds nothing.

**This is a great interview answer.** It shows you thought about
*privacy by design* — an actual GDPR Article 25 requirement — not just about
making the code work.

---

## 6. Tracking pixels and other tracking tech

Cookies are the famous tracking method. They are not the only one. This is
exactly why we capture network requests too.

### Tracking pixel (a.k.a. web beacon, 1×1 GIF)

```html
<img src="https://ads.example.com/track?page=checkout&user=7781"
     width="1" height="1" style="display:none">
```

An invisible one-pixel image. You never see it, but **loading it is itself the
signal**. The request to `ads.example.com` tells that company:

- your IP address (→ approximate location)
- your User-Agent (→ device, OS, browser)
- the `Referer` header (→ **which page you were on**)
- whatever is stuffed into the query string
- any cookies you already have for `ads.example.com`

```
  ┌──────────────────────────────────────────────────────────┐
  │  A pixel can set NO COOKIE AT ALL and still track you.   │
  │  A cookie-only scanner would report "clean" on a page    │
  │  covered in tracking pixels.                             │
  │                                                          │
  │  ▶ This is why scan.py registers page.on("request")      │
  │    and records every outgoing request.                   │
  └──────────────────────────────────────────────────────────┘
```

### Other techniques worth knowing (interviewers ask)

| Technique | How it works | Detectable by us? |
|-----------|--------------|-------------------|
| **Tracking pixel** | Invisible image; the request is the data | ✅ network request, `resource_type: "image"` |
| **localStorage / sessionStorage** | Browser key-value store, not sent automatically but readable by JS | ⬜ possible via Playwright, post-MVP |
| **Canvas fingerprinting** | Draws hidden graphics; tiny GPU/driver differences produce a near-unique ID — no storage needed, so nothing to delete | ⚠️ partially, by spotting known fingerprinting scripts |
| **ETag tracking** | Abuses HTTP caching headers as a hidden identifier | ⬜ post-MVP |
| **CNAME cloaking** | Third-party tracker disguised behind a first-party subdomain (`analytics.yoursite.com` → CNAME → tracker) | ⚠️ needs DNS lookup — a great "how would you extend this?" answer |

Mentioning **fingerprinting** and **CNAME cloaking** in an interview signals
that you understand the field beyond the basics.

### `resource_type` — why we record it

Playwright labels every request: `document`, `script`, `stylesheet`, `image`,
`xhr`, `fetch`, `font`, `media`. This is a strong classification hint:

- `image` request to a known ad domain → very likely a **tracking pixel**
- `script` from `google-analytics.com` → **analytics**
- `xhr`/`fetch` to an ad domain → **data being sent out**

Phase 2's classifier will use exactly this.

---

## 7. The four compliance categories

Every consent platform (OneTrust included) sorts cookies into roughly these
four buckets. Phase 2 implements this.

```
┌────────────────┬──────────────────────────┬────────────┬──────────────────┐
│ CATEGORY       │ PURPOSE                  │ CONSENT?   │ EXAMPLES         │
├────────────────┼──────────────────────────┼────────────┼──────────────────┤
│ NECESSARY      │ Site cannot function     │ ❌ NOT     │ session_id       │
│ (Strictly      │ without it: login,       │ required   │ csrf_token       │
│  Necessary)    │ cart, security, load     │ (exempt)   │ cart_id          │
│                │ balancing                │            │ __Host-*         │
├────────────────┼──────────────────────────┼────────────┼──────────────────┤
│ FUNCTIONAL     │ Improves experience but  │ ✅ YES     │ language         │
│ (Preferences)  │ site works without it:   │            │ theme=dark       │
│                │ language, theme, region  │            │ video_quality    │
├────────────────┼──────────────────────────┼────────────┼──────────────────┤
│ ANALYTICS      │ Measures usage: visits,  │ ✅ YES     │ _ga, _gid        │
│ (Performance)  │ popular pages, errors    │            │ _hjid (Hotjar)   │
│                │                          │            │ mp_* (Mixpanel)  │
├────────────────┼──────────────────────────┼────────────┼──────────────────┤
│ MARKETING      │ Ads, retargeting,        │ ✅ YES     │ _fbp (Facebook)  │
│ (Advertising / │ cross-site profiling.    │ (strictest │ _gcl_au (Ads)    │
│  Targeting)    │ The highest-risk bucket. │  scrutiny) │ IDE (DoubleClick)│
└────────────────┴──────────────────────────┴────────────┴──────────────────┘
                                                    + UNKNOWN — no signature
                                                      match. Must be reviewed
                                                      by a human, never
                                                      silently assumed safe.
```

### The rule that makes this legally meaningful

> **Only "Strictly Necessary" cookies may be set before the user consents.**
> Everything else must wait for an affirmative action.

And these do **not** count as necessary, however much a business wants them to:

- ❌ Analytics — "we need to know our traffic" is a business need, not a
  technical necessity
- ❌ A/B testing
- ❌ Social media share buttons
- ❌ Anything advertising-related

### Why we keep an explicit "Unknown" bucket

If a cookie doesn't match any signature, we label it **Unknown** rather than
guessing "probably necessary". Guessing safe would make the tool
systematically under-report risk — the worst possible failure mode for a
compliance tool. Surfacing unknowns forces human review.

**Interview-ready phrasing:** *"I designed the classifier to fail loudly rather
than fail silently. An unknown cookie appears in the report as unknown, because
a compliance tool that quietly assumes 'safe' is worse than no tool at all."*

---

## 8. The laws: GDPR, ePrivacy, CCPA, DPDP

You are applying for a cookie-compliance role. Know these.

| Law | Where | The core cookie rule |
|-----|-------|----------------------|
| **ePrivacy Directive 2002/58/EC** (the "Cookie Law") | EU | You must get consent **before** storing anything non-essential on a user's device. This is the one actually about cookies. |
| **GDPR (2018)** | EU | Defines what valid consent *is*: freely given, specific, informed, unambiguous, and as easy to withdraw as to give. |
| **CCPA / CPRA** | California | Opt-**out** model, not opt-in: users get a "Do Not Sell or Share My Personal Information" link. |
| **DPDP Act 2023** | India | Notice + consent before processing personal data; consent must be free, specific, informed, unconditional, unambiguous. |

### What "valid consent" means in practice (GDPR)

| ✅ Valid | ❌ Invalid |
|---------|-----------|
| Reject is as easy as Accept | "Accept All" big and colourful, "Reject" hidden in a submenu |
| All boxes unticked by default | Pre-ticked boxes |
| Nothing non-essential fires before the click | Trackers already loaded when the banner appears |
| Withdrawal is as easy as giving | No way to change your mind |
| Granular per-category choice | One all-or-nothing button |

### Real enforcement

- **CNIL (France), Jan 2022** — **Google €150m**, **Facebook €60m**.
  Not for the tracking itself, but because *refusing* cookies took more clicks
  than accepting them. The mechanics of the banner were the violation.
- **Amazon, Dec 2020** — **€35m**, cookies dropped before any consent.

**Why this matters for your project:** the "cookies fire before consent"
violation is exactly the state CookieGuard's pre-consent scan captures. That is
a genuinely useful compliance artifact, and worth saying out loud in an
interview.

---
---

# PART B — The Technology: How We Capture It

## 9. Client, server, HTTP — the basics

```
  ┌──────────┐         request          ┌──────────┐
  │  CLIENT  │ ───────────────────────▶ │  SERVER  │
  │ (browser)│ ◀─────────────────────── │          │
  └──────────┘        response          └──────────┘
```

An **HTTP request** is text with three parts:

```
GET /index.html HTTP/1.1        ← method, path, version
Host: example.com               ← headers (metadata)
Cookie: session=abc123          ←   cookies ride in a header
User-Agent: Mozilla/5.0 ...     ←   what browser you claim to be

(optional body — used by POST, not GET)
```

The **response** mirrors it:

```
HTTP/1.1 200 OK                 ← status code
Content-Type: text/html         ← headers
Set-Cookie: session=abc123      ←   cookies come back in a header

<html>...</html>                ← body: the actual content
```

### Status codes worth knowing

| Code | Meaning |
|------|---------|
| `200 OK` | Success |
| `301` / `302` | Redirect — this is why we record `final_url` separately from `url` |
| `403 Forbidden` | Blocked — sometimes an anti-bot system rejecting our scanner |
| `404 Not Found` | Page doesn't exist |
| `500` | Server crashed |
| `504` | Gateway timeout |

### Methods

| Method | Meaning | We use it for |
|--------|---------|---------------|
| `GET` | Read something | Fetching pages; `GET /api/domains` in Phase 3 |
| `POST` | Create something | `POST /api/scan` to trigger a scan |
| `PUT` / `PATCH` | Update | Not used in the MVP |
| `DELETE` | Remove | Not used in the MVP |

### The User-Agent, and why we fake it

The `User-Agent` header is a string the browser sends describing itself.
Headless Chromium sends one containing `HeadlessChrome`, and many sites
recognise that and serve reduced content or block outright.

```python
REALISTIC_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)
```

**Justification:** we are auditing what a *real visitor* receives. If the site
shows us a different page than it shows real people, our audit is measuring
the wrong thing. We change one header; we do not bypass authentication,
paywalls or rate limits.

---

## 10. The DOM

**DOM = Document Object Model.** When a browser receives HTML text, it parses
it into a live tree of objects in memory. That tree is the DOM.

```
  HTML text the server sent          The DOM the browser built
  ─────────────────────────          ─────────────────────────
  <html>                                    html
    <body>                                   │
      <h1>Hello</h1>          ──▶           body
      <p>World</p>                          ├── h1 ── "Hello"
    </body>                                 └── p  ── "World"
  </html>
```

Two things follow, and both matter to us:

1. **The DOM is live.** JavaScript can add, remove and change nodes after
   load. The page you *see* often differs from the HTML that was *sent*.
2. **`document` is the JS entry point to the DOM** — including
   `document.cookie`.

### Where the DOM shows up in this project

- **Phase 1:** we let the DOM finish building (and its scripts run) before
  reading cookies — that's what the settle wait is for.
- **Phase 4:** `app.js` will *manipulate* the DOM —
  `document.getElementById(...)`, `createElement`, `appendChild` — to build
  the dashboard tables.

### `document.cookie` — and why we deliberately don't use it

You *could* read cookies with JavaScript:

```javascript
document.cookie   // "theme=dark; lang=en"
```

But `document.cookie` **cannot see**:

- ❌ `HttpOnly` cookies (deliberately hidden from JS)
- ❌ third-party cookies belonging to other domains

That's most of what we care about. So instead we ask the browser *itself*:

```python
raw_cookies = await context.cookies()   # ← the entire jar, no exceptions
```

We're operating the browser, not running inside the page, so those
restrictions don't apply to us.

**Interview gold:** *"I read cookies from the Playwright BrowserContext rather
than `document.cookie`, because `document.cookie` can't see HttpOnly or
third-party cookies — which are precisely the ones a compliance audit needs."*

---

## 11. Web scraping vs browser automation

People use these interchangeably. They are not the same, and the difference is
the reason this project works.

```
┌────────────────────────────────┬────────────────────────────────┐
│  WEB SCRAPING                  │  BROWSER AUTOMATION            │
│  (requests + BeautifulSoup)    │  (Playwright / Selenium)       │
├────────────────────────────────┼────────────────────────────────┤
│  Downloads HTML text           │  Runs an actual browser        │
│  Does NOT run JavaScript       │  Runs JavaScript fully         │
│  No DOM is built               │  Full live DOM                 │
│  No cookie jar                 │  Real cookie jar               │
│  Milliseconds, ~1 MB RAM       │  Seconds, ~200 MB RAM          │
│  Cannot click or type          │  Can click, type, scroll       │
├────────────────────────────────┼────────────────────────────────┤
│  Good for: static pages,       │  Good for: modern JS apps,     │
│  APIs, plain HTML tables       │  logins, and ANYTHING          │
│                                │  involving cookies             │
└────────────────────────────────┴────────────────────────────────┘
```

### Concretely, on a real site

```
  requests.get("https://news.com")
     → returns raw HTML
     → Google Analytics <script> tag is present in the text
     → but the script NEVER RUNS
     → so _ga is NEVER CREATED
     → our scanner would report:  0 cookies      ❌ WRONG

  Playwright visits https://news.com
     → browser downloads HTML
     → browser EXECUTES the GA script
     → the script sets _ga and _gid
     → context.cookies() returns them
     → our scanner reports:  2 analytics cookies  ✅ CORRECT
```

**The rule:** if the thing you want is created *after* the page loads, you need
a real browser. Cookies are almost always created after the page loads.

---

## 12. Headless browsers

A **headless browser** is a full browser with no visible window. It downloads,
parses, builds the DOM, runs JavaScript, stores cookies — everything — it just
doesn't draw pixels on a screen.

```
   HEADED (--headed)                  HEADLESS (default)
   ─────────────────                  ──────────────────
   ┌───────────────────┐
   │ ▢ ▢ ▢   Chromium  │              (no window at all)
   ├───────────────────┤
   │                   │              Same engine.
   │   page renders    │              Same JavaScript.
   │   you can watch   │              Same cookies.
   │                   │              Just no pixels drawn.
   └───────────────────┘
   Slower, needs a screen             Faster, ~30% less RAM
   Great for learning/demo            REQUIRED on a server
```

### Why headless is mandatory, not just convenient

An AWS EC2 instance is a Linux machine with **no monitor and no graphical
desktop**. A headed browser has nothing to draw into and simply crashes.
Phase 7 deploys to EC2, so headless is a deployment requirement.

### Why we still built `--headed`

```bash
python scanner/scan.py https://example.com --headed
```

Watching the browser open, load and get covered in consent banners is by far
the fastest way to *understand* what the scanner does — and it's a genuinely
impressive thing to demo in an interview.

```python
browser = await p.chromium.launch(headless=headless)
```

One boolean. Same code path, both modes.

---

## 13. Playwright: browser → context → page

Playwright's object model has exactly three levels, and the middle one is the
one people miss.

```
  ┌─────────────────────────────────────────────────────────┐
  │  BROWSER            = the Chromium application itself   │
  │  ┌───────────────────────────────────────────────────┐  │
  │  │  CONTEXT        = one isolated profile            │  │
  │  │                   • its own cookie jar            │  │
  │  │                   • its own localStorage          │  │
  │  │                   • its own cache                 │  │
  │  │  ┌─────────────────────────────────────────────┐  │  │
  │  │  │  PAGE       = one tab                       │  │  │
  │  │  │               • goto(), click(), title()    │  │  │
  │  │  └─────────────────────────────────────────────┘  │  │
  │  └───────────────────────────────────────────────────┘  │
  └─────────────────────────────────────────────────────────┘
```

**The analogy:** browser = the Chrome app; context = a separate incognito
window with its own logins; page = a tab in that window.

### Why a fresh context per scan is critical for us

```python
context = await browser.new_context(...)
```

A brand-new context has a **completely empty cookie jar**. Therefore:

> Every cookie we find was set by *this scan* of *this site* — nothing left
> over from a previous run.

Without this, scanning `site-a.com` then `site-b.com` in the same context would
show site-a's cookies in site-b's report. Our results are **reproducible**,
which for an audit tool is non-negotiable.

Contexts are also cheap — far cheaper than launching a whole new browser — so
you get isolation almost for free. (Later, this is how you'd run parallel scans.)

### The options we set, and why

```python
context = await browser.new_context(
    user_agent=REALISTIC_USER_AGENT,   # look like a real visitor
    viewport={"width":1366,"height":768},  # fixed screen size = consistent results
    locale="en-GB",                    # ┐ pretend to be in Europe, so GDPR
    timezone_id="Europe/London",       # ┘ behaviour kicks in
)
```

The EU locale is a **compliance-driven** choice: many sites suppress trackers
for EU visitors until consent is given. Scanning as an EU visitor shows us the
pre-consent state — the state regulators actually care about.

### The three lines that do the real work

```python
raw_cookies = await context.cookies()   # ← the entire cookie jar
page.on("request", handle_request)      # ← every outgoing network request
await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
```

### `wait_until` — a deliberate reliability trade-off

| Value | Fires when | Verdict |
|-------|-----------|---------|
| `"domcontentloaded"` | HTML parsed | ✅ **our choice** |
| `"load"` | HTML + images + CSS done | Slower, no real benefit here |
| `"networkidle"` | No network activity for 500 ms | ❌ **never fires** on sites with live chat, polling, or auto-refreshing ads — it would time out on exactly the tracker-heavy sites we most want to scan |

So we take the earliest reliable signal and then wait a fixed, configurable
period ourselves. Predictable beats clever.

---

## 14. Synchronous vs asynchronous, async/await

This is the concept beginners find hardest, and it's the one most likely to be
probed in an interview. Take your time here.

### Synchronous: one thing at a time

```python
result1 = slow_thing()   # 3 seconds — program frozen
result2 = slow_thing()   # 3 seconds — program frozen
# total: 6 seconds
```

### Asynchronous: pause while waiting, do something else

```python
result1 = await slow_thing()   # at "await", control is RELEASED
result2 = await slow_thing()
```

### The analogy that makes it stick

```
  SYNCHRONOUS CHEF                 ASYNCHRONOUS CHEF
  ────────────────                 ─────────────────
  Put pasta on.                    Put pasta on.
  STAND AND STARE at it            While it boils → chop onions
  for 10 minutes.                  While onions fry → set the table
  Then chop onions.                Pasta beeps → drain it
  Then set the table.
                                   Same hands. Same stove.
  Total: 25 minutes                Total: 12 minutes
```

**Crucially:** the async chef is still *one person*. Async is **not** multiple
threads. It's one worker who refuses to stand idle during waiting periods.

### Why Playwright is async

Almost everything a browser does is waiting: waiting for DNS, for the server,
for the download, for JavaScript. If that waiting blocked the whole program,
we could never handle a second request. In Phase 3 that becomes concrete —
FastAPI will serve other users while one scan is waiting on a slow website.

### The vocabulary

| Term | Meaning |
|------|---------|
| `async def` | Defines a **coroutine** — a function that can pause and resume |
| `await` | "Pause here; wake me when this finishes. Meanwhile, run other work." |
| **Coroutine** | What `async def` produces. Calling it does *nothing* until awaited. |
| **Event loop** | The scheduler that decides which paused coroutine resumes next |
| `asyncio.run()` | Starts an event loop, runs one coroutine to completion, shuts it down |

### The rules you must remember

```
  1. You can only use `await` INSIDE an `async def` function.
  2. Calling an async function without `await` gives you a coroutine
     object, NOT the result. Nothing runs. (Common beginner bug.)
  3. To enter the async world from normal Python: asyncio.run(...)
```

```python
# ❌ WRONG — nothing happens, `result` is a coroutine object
result = scan_website(url)

# ✅ RIGHT — inside another async function
result = await scan_website(url)

# ✅ RIGHT — from normal synchronous code
result = asyncio.run(scan_website(url))
```

### `asyncio.sleep` vs `time.sleep` — a real bug we avoided

```python
await asyncio.sleep(settle_seconds)   # ✅ what scan.py uses
time.sleep(settle_seconds)            # ❌ would be a genuine bug
```

`time.sleep()` freezes the **entire program**, including the event loop.
Playwright's browser communication would stall, and our `page.on("request")`
listener could miss events. `asyncio.sleep()` pauses only *this* coroutine and
lets the loop keep processing browser events — which is exactly what we need
during the settle window.

### How it looks in `scan.py`

```python
async def scan_website(url, ...):          # coroutine
    async with async_playwright() as p:    # async context manager
        browser = await p.chromium.launch(...)
        context = await browser.new_context(...)
        page    = await context.new_page()
        await page.goto(url, ...)
        await asyncio.sleep(settle_seconds)
        raw     = await context.cookies()
        await browser.close()
    return result

if __name__ == "__main__":
    asyncio.run(main_async())              # ← the bridge into async
```

### `async with` — why not just `with`?

`async with` is a context manager whose setup and teardown are themselves
async. Its job here is **guaranteed cleanup**: if any line inside the block
raises an exception, Playwright still shuts down properly. Without it, a crash
mid-scan could leave a zombie Chromium process eating hundreds of MB of RAM.
Run a few hundred scans without cleanup and you'd exhaust the machine.

---

## 15. Event listeners and callbacks

### A callback is a function you hand to someone else to call later

```python
def handle_request(request):
    network_requests.append({...})

page.on("request", handle_request)   # ← note: NO parentheses
```

That missing `()` is the whole idea:

```python
page.on("request", handle_request)     # ✅ pass the function itself
page.on("request", handle_request())   # ❌ CALLS it now, passes the result
```

You're saying *"here is a function — you call it, whenever the event happens."*
This is **inversion of control**: normally you call the library; here the
library calls you.

### The analogy

You give a shop your phone number and say "ring me when the item arrives". You
don't stand in the shop refreshing. The shop (Playwright) calls your number
(the callback) when the event occurs.

### The flow in our scanner

```
   page.on("request", handle_request)     ← register once, before navigating
                │
                ▼
   await page.goto(url)
                │
   ┌────────────┴─────────────────────────────────────┐
   │  Browser starts fetching resources...            │
   │    fetch style.css     → handle_request() fires  │
   │    fetch logo.png      → handle_request() fires  │
   │    fetch analytics.js  → handle_request() fires  │  ← tracker!
   │    fetch pixel.gif     → handle_request() fires  │  ← pixel!
   └────────────┬─────────────────────────────────────┘
                ▼
   network_requests now holds every one of them
```

**Registration order matters.** We register the listener *before* `goto()`. If
we registered after, the page's initial requests would already have happened
and we'd miss them entirely.

### Why the callback has a try/except that swallows errors

```python
try:
    ...
except Exception:
    pass
```

One malformed URL among 400 requests must never kill the whole scan. We
degrade gracefully — losing one request's data is vastly better than losing the
entire result.

**Be honest in an interview:** *"Silent `pass` is a smell. In production I'd
log the exception rather than discard it. I kept it minimal here because
logging isn't wired up yet — it's on my TODO list."* Naming the weakness
yourself is far stronger than being caught by it.

### The same pattern in JavaScript (Phase 5 preview)

```javascript
button.addEventListener("click", handleClick);   // identical idea
```

Callbacks are a cross-language concept. Understanding them here means you
already understand them for the consent banner.

---

## 16. JSON

**JSON = JavaScript Object Notation.** A text format for structured data that
every language can read and write. It's how our Python backend will talk to
our JavaScript frontend.

```json
{
  "name": "_ga",
  "party": "first",
  "lifetime_days": 730,
  "secure": true,
  "expires_at": null,
  "tags": ["analytics", "google"]
}
```

### The type mapping

| JSON | Python | JavaScript |
|------|--------|-----------|
| object `{}` | `dict` | `Object` |
| array `[]` | `list` | `Array` |
| string | `str` | `String` |
| number | `int` / `float` | `Number` |
| `true` / `false` | `True` / `False` | `true` / `false` |
| `null` | `None` | `null` |

Note the capitalisation differences — `True` vs `true`, `None` vs `null`. The
`json` module handles the conversion; you never hand-write it.

### The two functions you need

```python
json.dumps(python_object)   # dump-s = to STRING   (Python → JSON text)
json.loads(json_string)     # load-s = from STRING (JSON text → Python)
```

The trailing `s` means "string" — that's the mnemonic. (`json.dump`/`json.load`
without the `s` work with files.)

### How `scan.py` uses it

```python
out_path.write_text(
    json.dumps(result, indent=2, ensure_ascii=False),
    encoding="utf-8",
)
```

| Argument | Why |
|----------|-----|
| `indent=2` | Pretty-print with 2-space indentation so a human can read the file |
| `ensure_ascii=False` | Keep real characters (`café`) instead of escapes (`caf\u00e9`) |
| `encoding="utf-8"` | Write the file so non-English page titles survive intact |

**This is why `scan_website()` returns a plain `dict`** — a dict maps directly
to JSON with zero conversion work, so the same object serves the CLI, the file
output, and Phase 3's API response.

---
---

# PART C — Technology Choices Defended

> Interviewers rarely ask "what did you use?". They ask **"why did you use
> it?"** and **"what else did you consider?"**. These four sections are your
> prepared answers.

## 17. Playwright vs Selenium vs BeautifulSoup

| | **Playwright** ✅ | Selenium | BeautifulSoup + requests |
|---|---|---|---|
| Runs JavaScript | ✅ Yes | ✅ Yes | ❌ **No** |
| Captures JS-set cookies | ✅ Yes | ✅ Yes | ❌ **No** |
| Third-party + HttpOnly cookies | ✅ `context.cookies()` | ⚠️ awkward | ❌ No |
| Network interception | ✅ Built in | ❌ Needs a proxy or CDP hacks | ❌ N/A |
| Auto-waiting | ✅ Built in | ❌ Manual sleeps/waits | N/A |
| Browser install | ✅ `playwright install` | ❌ Manage chromedriver + version match | N/A |
| Isolated profiles | ✅ Contexts (cheap) | ❌ Whole new browser | N/A |
| Async support | ✅ Native | ⚠️ Bolted on | N/A |
| Speed | Seconds | Seconds (slower) | Milliseconds |
| Memory | ~200 MB | ~250 MB | ~1 MB |

### Your one-paragraph answer

> *"BeautifulSoup was ruled out immediately: it only downloads HTML and can't
> execute JavaScript, and most tracking cookies are created by JavaScript after
> load — it would report almost nothing. That left Selenium and Playwright,
> both real browsers. I chose Playwright for three concrete reasons. First,
> `context.cookies()` returns the entire cookie jar in one call, including
> HttpOnly and third-party cookies, which is exactly the audit surface I need.
> Second, it has first-class network interception via `page.on("request")`, so
> I can catch tracking pixels that set no cookie — Selenium needs a proxy or
> raw CDP for that. Third, browser contexts give me a clean isolated profile
> per scan cheaply, which makes results reproducible. Playwright also bundles
> its own browser binaries, which removes the chromedriver version-mismatch
> problem entirely."*

### Fair points against Playwright

Be prepared to concede these — it shows balance:

- Newer (2020) than Selenium (2004), so a smaller community and fewer
  StackOverflow answers.
- Selenium has the W3C WebDriver standard behind it and broader legacy-browser
  support.
- Both are far heavier than plain `requests` — if you only needed static HTML,
  a browser would be the wrong tool.

---

## 18. FastAPI vs Flask vs Django

| | **FastAPI** ✅ | Flask | Django |
|---|---|---|---|
| Async native | ✅ Yes | ⚠️ Bolted on (3.x) | ⚠️ Partial |
| Auto API docs | ✅ Swagger at `/docs`, free | ❌ Extension needed | ❌ DRF extension |
| Request validation | ✅ Pydantic, built in | ❌ Manual or extension | ✅ Forms/serializers |
| Type hints drive behaviour | ✅ Yes | ❌ No | ❌ No |
| Learning curve | Low | Lowest | High |
| Size / weight | Light | Lightest | Heavy |
| Best suited to | JSON APIs | Small apps | Full DB-backed websites |

### Your answer

> *"Django was over-scoped — it brings an ORM, admin, templating and auth, and
> I'm building a JSON API with a static frontend, so I'd be carrying ~90% of a
> framework I don't use. Flask was the closest call: it's lighter and I know
> it. I chose FastAPI for three reasons specific to this project. One, it's
> async-native, and my scanner is async — a scan takes several seconds, so
> `async def` lets the server keep serving other requests while one scan waits
> on a slow website. With Flask I'd need a worker queue for the same result.
> Two, Pydantic validation is built in, so a bad URL is rejected with a clear
> 422 before my code ever runs. Three, it auto-generates interactive Swagger
> docs at `/docs`, which is both a genuine dev aid and a strong demo for a
> portfolio project."*

---

## 19. SQLite vs PostgreSQL vs MongoDB

| | **SQLite** ✅ | PostgreSQL | MongoDB |
|---|---|---|---|
| Setup | ✅ None — a file | ❌ Server + users + config | ❌ Server |
| In Python stdlib | ✅ `sqlite3` | ❌ Needs a driver | ❌ Needs a driver |
| Relational (joins, FKs) | ✅ Yes | ✅ Yes | ⚠️ Not naturally |
| Concurrent writers | ❌ One at a time | ✅ Many | ✅ Many |
| Portable | ✅ Copy one file | ❌ Dump/restore | ❌ Dump/restore |
| Right for | Single-machine tools | Multi-user production | Unstructured documents |

### Why relational beats document here

Our data has a genuinely relational shape:

```
   domains  (1) ──────< (many)  scans  (1) ──────< (many)  cookies
                                             ──────< (many)  requests
```

Questions we'll actually ask — *"show me every third-party marketing cookie
across all scans of this domain in the last 90 days"* — are exactly what SQL
joins and indexes are built for. MongoDB would either duplicate data or force
manual joins in Python.

### Your answer

> *"MongoDB was the wrong shape: my data is strongly relational — a domain has
> many scans, a scan has many cookies — and my main queries are aggregations
> and joins across those, which is SQL's home ground. Between SQLite and
> Postgres, I picked SQLite because CookieGuard is a single-machine tool and
> SQLite needs zero setup: it's one file and it's in the Python standard
> library, so `git clone` plus `pip install` is the entire onboarding. I wrote
> standard SQL and kept all database code isolated in `db.py`, so moving to
> Postgres later is mostly a connection change. The trade-off I'm accepting is
> that SQLite serialises writes — one writer at a time. That's fine for one
> user; the moment I need concurrent scans from multiple users, that's the
> trigger to migrate."*

Knowing **the specific condition that would change your mind** is what
separates a considered choice from a default.

---

## 20. Vanilla JS vs React

### Your answer

> *"Three reasons. First, the role I'm targeting explicitly asks for
> HTML/CSS/JavaScript, so demonstrating those directly is the point. Second,
> the dashboard is a few tables and charts driven by `fetch` calls — React's
> value is managing complex component state, and I don't have complex state, so
> I'd be adding npm, a bundler and JSX transpilation for no functional gain.
> Third — and this is the technical constraint, not a preference — the consent
> banner has to be a single script tag droppable into any customer's website.
> It cannot assume React is present. So that file is framework-free by
> requirement, and it would be odd to run two different paradigms in one small
> frontend."*
>
> *"If the dashboard grew to have user accounts, live filtering across many
> views and shared state between components, React would start to pay for
> itself. It doesn't yet."*

---
---

# PART D — Python Concepts Used in Phase 1

## 21. Python features in scan.py

### f-strings

```python
print(f"URL: {result['url']}")
```

The `f` prefix lets you embed expressions directly in `{}`.

**Alignment for tables** — this is how we get neat columns with no library:

```python
f"{'NAME':<26}"   # left-align,   pad to 26 characters
f"{'NAME':>26}"   # right-align
f"{'NAME':^26}"   # centre
```

### Slicing

```python
parts[-2:]          # last two elements
request.url[:500]   # first 500 characters (truncation)
str(name)[:25]      # keeps table columns from breaking
```

### `dict.get()` with a default

```python
c.get("domain", "")     # returns "" if the key is missing
c["domain"]             # raises KeyError if missing — crashes
```

Playwright doesn't guarantee every optional field is present, so `.get()` makes
our parsing resilient.

### Counting booleans with `sum()`

```python
sum(1 for c in cookies if c["party"] == "first")
```

A **generator expression** — it produces values one at a time without building
an intermediate list, so it's memory-efficient. Read it as: "for each cookie
where party is first, yield 1; add them all up."

### `sorted()` with a `key` and `lambda`

```python
sorted(domains.items(), key=lambda item: item[1], reverse=True)
```

- `.items()` → a list of `(key, value)` tuples
- `lambda item: item[1]` → a tiny anonymous function saying "sort by the second
  element", i.e. the count
- `reverse=True` → descending

### List comprehension

```python
[{"domain": d, "request_count": n} for d, n in sorted_third_parties]
```

Build a new list by transforming each element of another. `for d, n in ...` is
**tuple unpacking** — splitting each `(key, value)` pair into two variables in
one step.

### The counting idiom

```python
counts[domain] = counts.get(domain, 0) + 1
```

"If we've seen this key, add one; if not, start from 0 and add one." Memorise
this — it comes up constantly.

### Type hints

```python
def get_registrable_domain(hostname: str) -> str:
```

`: str` = this parameter should be a string. `-> str` = returns a string.

**Python does not enforce these at runtime** — they're documentation that your
editor and linters can check. But in Phase 3, FastAPI *does* act on them:
Pydantic uses type hints to actually validate incoming requests. Same syntax,
real behaviour.

### `pathlib.Path`

```python
out_path = Path(args.output)
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(text, encoding="utf-8")
```

Handles Windows `\` and Linux `/` transparently. `parents=True` creates
intermediate folders; `exist_ok=True` means "don't error if it already exists".

### `if __name__ == "__main__":`

```python
if __name__ == "__main__":
    asyncio.run(main_async())
```

`__name__` is `"__main__"` when the file is **run directly**, and the module's
name when it's **imported**.

**Why this matters concretely:** in Phase 3, `api/main.py` will do
`from scanner.scan import scan_website`. Without this guard, importing that
function would also fire off the entire command-line program. The guard keeps
the file usable as both a script and a library.

### `try` / `except` and exception types

```python
except PlaywrightTimeout:                  # specific — handle deliberately
    ...
except Exception as e:                     # broad — the safety net
    print(f"{type(e).__name__}: {e}")      # e.g. "ValueError: bad URL"
```

Always catch the **specific** exception first. `type(e).__name__` gives the
exception's class name, which makes error messages far more useful than the
message alone.

### `argparse`

```python
parser.add_argument("url")                        # positional, required
parser.add_argument("--headed", action="store_true")  # flag, no value needed
parser.add_argument("--wait", type=int, default=5)    # converts "5" → 5
```

You get `--help` generated for free.

### Exit codes

```python
sys.exit(exit_code)   # 0 = success, non-zero = failure
```

Invisible when you run it by hand, essential in Phase 6: GitHub Actions decides
whether a build passed or failed by reading this number.

---
---

# PART E — Walkthrough & Prep

## 22. `scan.py` line-by-line walkthrough

### The seven steps, as a diagram

```
  ┌────────────────────────────────────────────────────────────────┐
  │ 1. async with async_playwright() as p:                         │
  │    Starts Playwright's driver. `async with` guarantees cleanup  │
  │    even if something later throws.                              │
  ├────────────────────────────────────────────────────────────────┤
  │ 2. browser = await p.chromium.launch(headless=...)              │
  │    Launches Chromium. Invisible unless --headed.                │
  ├────────────────────────────────────────────────────────────────┤
  │ 3. context = await browser.new_context(...)                     │
  │    Fresh EMPTY cookie jar → results are reproducible.           │
  │    EU locale so GDPR-conditional behaviour kicks in.            │
  ├────────────────────────────────────────────────────────────────┤
  │ 4. page.on("request", handle_request)                           │
  │    Registered BEFORE goto(), or we'd miss the first requests.   │
  ├────────────────────────────────────────────────────────────────┤
  │ 5. await page.goto(url, wait_until="domcontentloaded")          │
  │    await asyncio.sleep(settle_seconds)                          │
  │    Navigate, then idle so delayed trackers fire.                │
  ├────────────────────────────────────────────────────────────────┤
  │ 6. raw_cookies = await context.cookies()                        │
  │    ★ THE KEY LINE. Whole jar: first + third party, HttpOnly     │
  │      included. Something document.cookie could never do.        │
  ├────────────────────────────────────────────────────────────────┤
  │ 7. await context.close(); await browser.close()                 │
  │    Chromium uses hundreds of MB — leaking it exhausts the box.  │
  └────────────────────────────────────────────────────────────────┘
                              │
                              ▼
        Reshape into OUR dict format  →  print report  →  optional JSON
```

### Why we reshape Playwright's data

Playwright hands back `httpOnly`, `sameSite`, `expires`. We convert to
`http_only`, `same_site`, `expires_at` + `lifetime_days` + `type`.

**The reason is decoupling.** The classifier, the database and the dashboard
all depend on *our* format. If we ever replaced Playwright, only `scan.py`
would change. Everything downstream is insulated.

That's a genuine software-design point you can make in an interview: *"I put an
adapter layer at the boundary so the vendor's data shape doesn't leak through
my whole application."*

### Why navigation errors don't abort the scan

```python
except PlaywrightTimeout:
    navigation_error = f"Navigation timed out after ..."
    status_code = None
    # ...and we carry on
```

A site that times out may still have set cookies before it stalled. For an
audit, **partial data beats no data** — so we record the problem in the
`error` field and keep going. Callers can check that field and decide whether
to trust the result.

---

## 23. Common Interview Questions & Answers

### On cookies and privacy

**Q: What is a cookie?**
> A small piece of text a website asks the browser to store and send back on
> future requests. It exists because HTTP is stateless — without it a server
> can't tell that two requests came from the same person. Crucially, it's
> stored on the *user's device*, which is why storing one is a regulated act
> under the ePrivacy Directive.

**Q: First-party vs third-party — and why does it matter?**
> First-party cookies are set by the domain in the address bar; third-party
> ones by some other company whose script the site embedded. The difference
> matters because a third party embedded on many sites sees the same
> identifier on all of them, which lets it build a cross-site behavioural
> profile. That capability is what triggers consent requirements, and it's why
> Safari and Firefox now block third-party cookies by default.

**Q: Session vs persistent?**
> A session cookie has no expiry, lives in memory and dies when the browser
> closes. A persistent cookie has an expiry, is written to disk and survives
> restarts. Persistent cookies enable long-term tracking — some last two years
> — which is why CNIL recommends capping analytics cookies at 13 months. My
> scanner records `lifetime_days` specifically so excessive lifetimes can be
> flagged.

**Q: Which cookies don't need consent?**
> Only strictly necessary ones — the site literally can't work without them:
> session/login, CSRF tokens, shopping cart, load balancing. Analytics is not
> exempt, however much businesses argue it is; wanting traffic numbers is a
> business need, not a technical necessity.

**Q: How would you detect tracking that doesn't use cookies?**
> That's why I capture network requests as well as cookies. A tracking pixel
> is a 1×1 invisible image — the *request itself* transmits your IP,
> User-Agent and referring page, with no cookie needed. Beyond that there's
> canvas fingerprinting, which derives a near-unique ID from tiny GPU and font
> rendering differences and stores nothing at all, and CNAME cloaking, where a
> third-party tracker hides behind a first-party subdomain. My scanner catches
> pixels today; fingerprinting detection and CNAME resolution are on the
> roadmap.

**Q: What makes a consent banner non-compliant?**
> Under GDPR, consent must be freely given, specific, informed and
> unambiguous. So: pre-ticked boxes are invalid; "Accept All" prominent with
> "Reject" buried is invalid — that's exactly what CNIL fined Google €150m and
> Facebook €60m for in 2022; and firing trackers before the user clicks
> anything is invalid regardless of how good the banner looks. That last case
> is precisely what my pre-consent scan captures.

### On the technology

**Q: Why Playwright rather than Selenium or BeautifulSoup?**
> *(Use the full paragraph in §17.)*

**Q: Why not just use `requests`?**
> `requests` downloads HTML and stops — it doesn't execute JavaScript. Most
> tracking cookies are created *by* JavaScript after the page loads, so a
> `requests`-based scanner would return near-zero cookies on sites that are
> covered in trackers. The tool has to be a real browser to be correct.

**Q: What is a headless browser and why use one?**
> A full browser with no visible window — same engine, same JavaScript, same
> cookie jar, it just doesn't draw pixels. It's faster and uses less memory,
> but the real reason is deployment: an EC2 instance has no display, so a
> headed browser would have nothing to render into. I kept a `--headed` flag
> for local debugging and demos.

**Q: Explain async/await.**
> Async lets a single-threaded program stop idling during waits. `await` means
> "pause this function and let the event loop run other work until this
> finishes". It's not parallelism — it's one worker who doesn't stand still.
> It suits browser automation because nearly all of a scan is waiting on the
> network. In Phase 3 that becomes concrete: FastAPI can serve other users
> while one scan waits on a slow site. A related detail I was careful about:
> inside async code you must use `asyncio.sleep`, not `time.sleep` — the
> latter blocks the whole event loop and would stall Playwright's event
> processing.

**Q: Why a fresh browser context per scan?**
> A context is an isolated profile with its own empty cookie jar. Starting
> fresh guarantees every cookie I find was set by *this* scan of *this* site,
> not left over from a previous one. For an audit tool, reproducibility isn't
> optional — a report you can't reproduce is worthless as evidence. Contexts
> are also much cheaper than launching a new browser, so isolation is nearly
> free.

**Q: How do you handle a site that never finishes loading?**
> I deliberately avoid `wait_until="networkidle"` because it never fires on
> sites with live chat or auto-refreshing ads — the very sites most worth
> scanning. Instead I wait for `domcontentloaded`, then idle for a
> configurable settle period. And if navigation times out entirely, I catch it,
> record it in an `error` field, and still return whatever cookies were set
> before the stall — partial data is more useful than none for an audit.

**Q: What are the limitations of your scanner?**
> Four I'd call out. It scans only the landing page, so cookies set on
> `/checkout` or after login are missed. It captures the pre-consent state
> only — it doesn't click "Accept" and rescan, and that *diff* is actually the
> most valuable compliance signal, so it's my top roadmap item. My
> registrable-domain function naively takes the last two labels, so
> `bbc.co.uk` misreduces to `co.uk`; the correct fix is Mozilla's Public
> Suffix List via `tldextract`. And the settle time is fixed rather than
> adaptive.

**Q: Why don't you store cookie values?**
> Because values often contain personal data — user IDs, hashed emails,
> session tokens. Storing them would make my privacy audit tool a privacy
> liability, and a breach of my database would itself be a data breach. I
> store `value_length` instead, which is enough for the report. It's privacy
> by design — GDPR Article 25 — applied to my own tool.

**Q: How would you scale this to scan 10,000 sites?**
> Playwright contexts are cheap, so first I'd run several contexts
> concurrently inside one browser using `asyncio.gather`, with a semaphore to
> cap concurrency and avoid exhausting memory. Beyond one machine, I'd move to
> a queue — SQS or Celery — with a pool of containerised workers pulling jobs,
> and switch SQLite for Postgres, since SQLite serialises writes and that
> becomes the bottleneck the moment workers run in parallel.

### On design decisions

**Q: Why FastAPI over Flask?** → *(§18)*
**Q: Why SQLite over MongoDB?** → *(§19)*
**Q: Why vanilla JS over React?** → *(§20)*

**Q: What would you do differently if you started again?**
> Two things. I'd use `tldextract` from day one instead of my own
> domain-reduction function — I knew it was a simplification when I wrote it
> and it'll cause misclassifications on `.co.uk` domains. And I'd add
> structured logging early; right now one error handler swallows exceptions
> silently, which is fine for a demo but wrong for anything real.

---

## 24. Glossary

| Term | Meaning |
|------|---------|
| **API** | Application Programming Interface — a defined way for one program to call another. |
| **argparse** | Python library that turns command-line arguments into variables. |
| **async / await** | Syntax letting a function pause during waits so other work can run. |
| **Browser context** | Playwright's isolated profile: its own cookie jar, storage and cache. |
| **Callback** | A function passed to a library so the library can call it when an event happens. |
| **Chromium** | The open-source browser engine behind Chrome and Edge. |
| **CI/CD** | Continuous Integration / Continuous Deployment — automatically test and ship on every push. |
| **CNIL** | France's data-protection regulator; the most active cookie enforcer. |
| **Consent banner** | The UI asking permission before non-essential cookies are set. |
| **Cookie** | A small text value a site asks the browser to store and return on future requests. |
| **Cookie jar** | The browser's store of all cookies. |
| **Coroutine** | What `async def` creates — a pausable function. Does nothing until awaited. |
| **CORS** | Cross-Origin Resource Sharing — browser rules for calling a different origin. Needed in Phase 3. |
| **CSRF token** | A value proving a request came from your own site. A necessary cookie. |
| **Docker** | Packages an app with all its dependencies into a portable container. |
| **DOM** | Document Object Model — the browser's live in-memory tree of the page. |
| **Event loop** | The scheduler that decides which paused coroutine resumes next. |
| **Fingerprinting** | Identifying a user from device characteristics, storing nothing. |
| **First-party cookie** | Set by the domain in the address bar. |
| **GDPR** | EU General Data Protection Regulation — defines valid consent. |
| **Generator expression** | `(x for x in y)` — produces values lazily, one at a time. |
| **Headless** | A browser running with no visible window. |
| **HTTP** | The request/response protocol of the web. Stateless by design. |
| **HttpOnly** | Cookie flag hiding it from JavaScript. Protects session tokens from XSS. |
| **JSON** | Text format for structured data; the lingua franca between backend and frontend. |
| **Lambda** | An anonymous inline function: `lambda x: x[1]`. |
| **Max-Age** | Cookie lifetime in seconds from now. |
| **ORM** | Object-Relational Mapper — maps database rows to objects. We use raw SQL instead. |
| **Persistent cookie** | Has an expiry; written to disk; survives browser restart. |
| **Playwright** | Microsoft's browser-automation library. Our scanner's engine. |
| **Public Suffix List** | Mozilla's authoritative list of domain suffixes (`.co.uk` etc.). Fixes our KI-1. |
| **Pydantic** | Python validation library using type hints. Powers FastAPI validation. |
| **REST** | An API style using HTTP methods and URLs to represent resources. |
| **SameSite** | Cookie flag controlling cross-site sending. `None` is the tracker's setting. |
| **Secure** | Cookie flag: send only over HTTPS. |
| **Session cookie** | No expiry; lives in memory; dies with the browser. |
| **Set-Cookie** | The HTTP response header that creates a cookie. |
| **SQLite** | A full SQL database that lives in a single file. Our storage. |
| **Third-party cookie** | Set by a domain other than the one you're visiting. The tracking one. |
| **Tracking pixel** | A 1×1 invisible image whose *loading* transmits data. |
| **Type hint** | `x: str` — documentation your editor checks; FastAPI acts on it at runtime. |
| **Unix timestamp** | Seconds since 1 Jan 1970 UTC. How Playwright reports expiry. |
| **User-Agent** | Header describing the browser. We set a realistic one deliberately. |
| **UTC** | Coordinated Universal Time — timezone-free reference. All our timestamps use it. |
| **uv** | Fast Rust-based replacement for pip + venv. Can also install Python itself. |
| **Uvicorn** | The ASGI server that will run our FastAPI app. |
| **venv** | Virtual environment — an isolated per-project Python + packages folder. |
| **Wheel (.whl)** | A pre-compiled package for a specific Python version and OS. Installs instantly. |
| **Source distribution (sdist)** | Raw package source that must be compiled locally. Needs a C/Rust toolchain. |
| **Lockfile** | Machine-generated file pinning exact installed versions, for reproducible builds. |
| **Viewport** | The visible page area in pixels. We fix it for consistent results. |
| **XSS** | Cross-Site Scripting — injecting malicious JS. `HttpOnly` defends cookies against it. |
| **YAML** | Indentation-based config format. Used by GitHub Actions and docker-compose. |

---
---

# PART F — Environments & Packaging

## 25. venv, pip, uv, wheels — and the error you hit

This section exists because Phase 1's install failed on your machine. The
failure is a genuinely useful thing to understand — dependency management is a
standard interview topic, and "tell me about a build problem you debugged" is a
standard question.

### What a virtual environment actually is

```
   WITHOUT a venv                    WITH a venv
   ─────────────                     ───────────
   One system-wide Python            Each project gets its own folder
   ┌──────────────────┐              ┌─────────────┐  ┌─────────────┐
   │ ProjectA needs   │              │ ProjectA    │  │ ProjectB    │
   │   pydantic 1.x   │              │ .venv/      │  │ .venv/      │
   │ ProjectB needs   │  ⚠ CONFLICT  │  pydantic 1 │  │  pydantic 2 │
   │   pydantic 2.x   │              └─────────────┘  └─────────────┘
   └──────────────────┘                    ✅ no conflict
```

A venv is just a folder containing its own copy of the Python interpreter and
its own `site-packages` directory. "Activating" it simply puts that folder
first on your `PATH`, so `python` and `pip` resolve to the project's copies
instead of the system ones. There's no magic — it's a path trick.

### Wheels vs source distributions — the actual cause of your error

When you `pip install pydantic`, the package can arrive in one of two forms:

```
  ┌────────────────────────────────┬────────────────────────────────┐
  │  WHEEL  (.whl)                 │  SOURCE DIST  (.tar.gz)        │
  ├────────────────────────────────┼────────────────────────────────┤
  │  Pre-compiled, ready to use    │  Raw source code               │
  │  Just unzip it into place      │  Must be COMPILED on your      │
  │                                │  machine first                 │
  │  Seconds                       │  Minutes — and needs a         │
  │                                │  C or Rust compiler installed  │
  │  ✅ what you want              │  ⚠️  where installs go wrong   │
  └────────────────────────────────┴────────────────────────────────┘
```

A wheel is built for a **specific Python version and OS**. Its filename says so:

```
   pydantic_core-2.23.4-cp312-cp312-win_amd64.whl
                        └─┬─┘        └───┬───┘
                    Python 3.12      Windows 64-bit
```

### So what actually went wrong

```
  Your Python version  ─────▶  is NEWER than the pinned package versions

     requirements.txt said:  pydantic==2.9.2   (released Sept 2024)
     Your interpreter is:    a Python release from AFTER that

     ▶ No  pydantic_core-...-cp3XX-win_amd64.whl  exists for your Python
     ▶ pip falls back to the source distribution
     ▶ pydantic-core is written in RUST, greenlet in C
     ▶ pip tries to run `cargo` (the Rust compiler)
     ▶ You have no Rust toolchain
     ▶ ERROR: Failed building wheel for pydantic-core
```

Read your error message again with that in mind — `maturin`, `cargo`, `rustc`,
`Cargo.toml` are all **Rust build tools**. Python was trying to compile Rust
source code on your laptop. That's the tell.

**Key insight:** nothing was wrong with the packages or with your code. The
problem was a *version mismatch between your interpreter and the pinned
dependencies.*

### The two fixes (we apply both)

| Fix | What it does |
|-----|--------------|
| **1. Loosen exact pins to ranges** | `pydantic==2.9.2` → `pydantic>=2.9`. Now the resolver may pick a newer release that *does* ship a wheel for your Python. |
| **2. Pin the Python version instead** | Tell `uv` to use Python 3.12, which every package in our stack has wheels for. `uv` will download that interpreter for you automatically. |

Fix 2 is the more important habit. **Pin your interpreter, keep your libraries
flexible** — not the other way round. Bleeding-edge Python versions always
outrun the ecosystem's pre-built wheels by several months.

### Why we're switching to `uv`

`uv` is a modern replacement for `pip` + `venv` + `virtualenv`, written in Rust
by Astral (the makers of the `ruff` linter).

| | pip | **uv** |
|---|---|---|
| Install speed | Baseline | **10–100× faster** |
| Downloads Python itself | ❌ No | ✅ Yes — `uv venv --python 3.12` |
| Creates venvs | Needs `python -m venv` | ✅ Built in |
| Dependency resolution | Sequential | Parallel, with a global cache |
| Command style | `pip install X` | `uv pip install X` — same syntax |

The `uv pip` interface is deliberately identical to pip's, so everything you
already know transfers directly.

**How it's so much faster:** it downloads packages in parallel, caches them
globally across all your projects (so the second project installing Playwright
gets it instantly), and uses hardlinks instead of copying files into the venv.

### Reproducibility: ranges *plus* a lockfile

Loosening `==` to `>=` fixes the install but loses exact reproducibility —
today's install might resolve differently from next month's. The standard
solution is two files with two different jobs:

```
  requirements.txt          "what this project needs"     → ranges,  human-written
  requirements.lock.txt     "exactly what I installed"    → pins,    machine-generated
```

Generate the lock file once your install succeeds:

```bash
uv pip freeze > requirements.lock.txt
```

Phase 6's CI pipeline will install from the **lock** file, so the build is
byte-identical every time.

**Interview-ready phrasing:** *"I keep loose ranges in requirements.txt so the
project installs cleanly across Python versions, and a generated lockfile for
reproducible CI builds. Pinning the interpreter and floating the libraries is
more robust than the reverse, because new Python releases outpace pre-built
wheels by months."*

### Interview Q&A on this section

**Q: What's a virtual environment and why use one?**
> An isolated folder with its own Python interpreter and packages. It stops
> two projects that need different versions of the same library from
> conflicting, and it means my `requirements.txt` describes exactly what the
> project needs rather than everything installed on my machine.

**Q: What's the difference between a wheel and a source distribution?**
> A wheel is pre-compiled for a specific Python version and OS — install is
> just unzipping. A source distribution is raw code that must be compiled
> locally, which needs a C or Rust toolchain. Most install failures are a wheel
> not existing for your platform, so pip silently falls back to compiling and
> then fails.

**Q: Tell me about a build problem you debugged.**
> My install failed with "Failed building wheel for pydantic-core". The
> giveaway was that the traceback mentioned `cargo` and `rustc` — Rust build
> tools — which meant pip had fallen back to compiling from source. The root
> cause was that I'd pinned exact library versions while running a Python
> release newer than those libraries, so no matching wheel existed. I fixed it
> by pinning the *interpreter* to 3.12 and loosening the library pins to
> minimum-version ranges, then generated a lockfile for reproducible builds. I
> also moved from pip to uv, which cut install time substantially.

---

## ✅ Phase 1 Self-Check

Can you answer these without looking? If not, re-read the linked section.

- [ ] Why can't `requests` + BeautifulSoup do this job? → §11
- [ ] What are the two routes by which a cookie gets created? → §2
- [ ] Why is `context.cookies()` better than `document.cookie`? → §10
- [ ] What's the difference between a browser, a context and a page? → §13
- [ ] Why a *fresh* context for every scan? → §13
- [ ] What does `await` actually do? → §14
- [ ] Why `asyncio.sleep` and not `time.sleep`? → §14
- [ ] Why register the request listener *before* `goto()`? → §15
- [ ] Why do we avoid `wait_until="networkidle"`? → §13
- [ ] Why don't we store cookie values? → §5
- [ ] Which cookie categories legally require consent? → §7
- [ ] What did CNIL fine Google €150m for, exactly? → §8
- [ ] Name three limitations of the scanner and the fix for each. → §23

---

> **Next up — Phase 2:** the classifier and `trackers.json`. New concepts will
> include pattern matching, signature databases, SQL schema design, foreign
> keys, and database normalisation. They'll be appended here.
