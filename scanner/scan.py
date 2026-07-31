"""
CookieGuard — Scanner (Phase 1)
================================

WHAT THIS FILE DOES
-------------------
It opens a REAL web browser (Chromium), visits a URL you give it, waits for the
page's JavaScript to finish running, and then records two things:

  1. Every COOKIE that ended up stored in the browser.
  2. Every NETWORK REQUEST the page made (so we also catch tracking pixels,
     which spy on you without necessarily setting a cookie).

WHY A REAL BROWSER?
-------------------
A simple `requests.get(url)` downloads the raw HTML text and stops. But most
tracking cookies are NOT in the HTML — they are created later by JavaScript
(Google Analytics, Facebook Pixel, etc.). Only a real browser runs that
JavaScript. So to see the cookies a real visitor gets, we must BE a real
visitor. That is what Playwright gives us.

RUN IT LIKE THIS
----------------
    python scanner/scan.py https://example.com
    python scanner/scan.py https://example.com --headed
    python scanner/scan.py https://example.com --output data/example.json
"""

# ---------------------------------------------------------------------------
# IMPORTS — pulling in code other people already wrote
# ---------------------------------------------------------------------------

# `argparse` reads the words you type after the filename on the command line
# and turns them into Python variables. It also builds `--help` for us free.
import argparse

# `asyncio` is Python's built-in library for asynchronous code — code that can
# say "I'm waiting on something slow, go do other work meanwhile".
# Playwright's API is async, so we need this to actually run our async function.
import asyncio

# `json` converts between Python objects (dicts/lists) and JSON text, which is
# the universal format for sending data between programs.
import json

# `sys` gives access to interpreter things. We use `sys.exit()` to end the
# program with an exit code (0 = success, 1 = failure) — CI systems read this.
import sys

# `datetime` for timestamping the scan. `timezone.utc` makes the timestamp
# unambiguous worldwide, instead of depending on the machine's local clock.
from datetime import datetime, timezone

# `Path` is the modern, OS-safe way to handle file paths. It works with both
# Windows backslashes and Linux forward slashes without us thinking about it.
from pathlib import Path

# `urlparse` splits a URL string like "https://shop.example.com/cart?id=5"
# into named pieces: scheme=https, netloc=shop.example.com, path=/cart, etc.
from urllib.parse import urlparse

# Our own shared domain logic, backed by Mozilla's Public Suffix List.
# Both scan.py and classifier.py import from here so they can never disagree
# about what counts as the same organisation.
from domains import registrable_domain
from playwright.async_api import TimeoutError as PlaywrightTimeout

# The Playwright async API. `async_playwright` is the entry point that starts
# Playwright's background driver process.
# `TimeoutError` is renamed to PlaywrightTimeout so it does not collide with
# Python's own built-in TimeoutError.
from playwright.async_api import async_playwright

# ---------------------------------------------------------------------------
# CONFIGURATION CONSTANTS
# ---------------------------------------------------------------------------
# Putting "magic numbers" in named constants at the top means: one place to
# change them, and the name explains what the number means.

# How many milliseconds to wait for the page to load before giving up.
# 30000 ms = 30 seconds. Playwright measures time in milliseconds.
DEFAULT_NAV_TIMEOUT_MS = 30_000

# After the page loads, how many seconds to sit still and let lazy-loaded
# trackers fire. Many analytics scripts deliberately delay themselves so they
# don't slow the page down — if we leave immediately we would miss them.
DEFAULT_SETTLE_SECONDS = 5

# We pretend to be a normal Chrome browser on Windows. Some sites serve a
# stripped-down page (or block you outright) if the User-Agent string says
# "HeadlessChrome", which is the default. Setting a realistic one gives us the
# same experience a real visitor would get — which is exactly what we're auditing.
REALISTIC_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

# A standard laptop screen size. Some sites show different content (and load
# different trackers) on mobile vs desktop, so we fix this for consistency.
VIEWPORT = {"width": 1366, "height": 768}


# ---------------------------------------------------------------------------
# HELPER FUNCTIONS
# These are small, single-purpose functions. Keeping logic in small named
# functions makes each piece independently testable and easy to explain.
# ---------------------------------------------------------------------------


def browser_launch_args() -> list:
    """
    Extra command-line flags for Chromium. Empty unless configured otherwise.

    WHAT --no-sandbox ACTUALLY TURNS OFF
    ------------------------------------
    Chromium runs each web page in a separate RENDERER process that is stripped
    of almost every operating-system privilege. If a malicious page finds a bug
    in the rendering engine, the sandbox is what stops that becoming control of
    the machine. Two barriers, not one.

    `--no-sandbox` removes the second barrier. It is widely copy-pasted from
    Stack Overflow because it makes "Chromium failed to launch" go away inside
    containers — where the sandbox needs kernel privileges the container may
    not have been granted.

    WHY THIS MATTERS MORE FOR US THAN FOR MOST PROJECTS
    ---------------------------------------------------
    CookieGuard points a browser at URLs a stranger typed into a form. That is
    exactly the threat model the sandbox was built for. So the flag is:

        · OFF by default, everywhere
        · turned on only by explicitly setting BROWSER_NO_SANDBOX=1
        · NOT used by our docker-compose.yml, which instead grants the
          container the SYS_ADMIN capability Chromium needs and keeps the
          sandbox on — the better fix

    Keeping the escape hatch is still right: someone will run this on a
    platform where capabilities can't be granted, and they should reach for a
    documented flag rather than editing this file in a hurry.

    Read directly from the environment rather than importing api/config.py, so
    the scanner stays runnable with no web dependencies installed.
    """
    import os

    flag = (os.environ.get("BROWSER_NO_SANDBOX") or "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        # --disable-dev-shm-usage goes with it: containers default to a 64MB
        # /dev/shm, and Chromium crashes on pages larger than that. This makes
        # it use /tmp instead. Slower, but it doesn't fall over.
        return ["--no-sandbox", "--disable-dev-shm-usage"]
    return []

def get_registrable_domain(hostname: str) -> str:
    """
    Reduce a hostname to its 'core' domain so we can compare two hosts fairly.

        "www.google-analytics.com"  ->  "google-analytics.com"
        "shop.example.com"          ->  "example.com"
        "www.bbc.co.uk"             ->  "bbc.co.uk"

    WHY WE NEED THIS
    ----------------
    If the page is `www.example.com` and a cookie's domain is `.example.com`,
    those are the SAME organisation — it's a first-party cookie. A naive string
    comparison would wrongly call it third-party. This normalises both sides so
    the comparison is meaningful.

    The real work happens in `domains.py`, which uses Mozilla's Public Suffix
    List. This is a thin wrapper kept for readability at the call sites.
    See domains.py for why the obvious "last two labels" approach is wrong.
    """
    return registrable_domain(hostname)


def classify_party(cookie_domain: str, site_domain: str) -> str:
    """
    Decide whether a cookie is 'first' party or 'third' party.

    FIRST-PARTY  = set by the site you are visiting. Usually functional
                   (login sessions, language preference, shopping cart).
    THIRD-PARTY  = set by some OTHER company whose script the site embedded.
                   This is where advertising and cross-site tracking live, and
                   it is what privacy regulators care about most.

    ┌─────────────────────────────────────────────────────────┐
    │  You visit  news.com                                    │
    │                                                         │
    │   news.com sets  "session_id"     → FIRST party         │
    │   news.com embeds a Facebook script                     │
    │       facebook.com sets "_fbp"    → THIRD party         │
    │                                                         │
    │  Facebook can now recognise you on every OTHER site     │
    │  that embeds the same script. That is cross-site        │
    │  tracking, and it legally requires consent.             │
    └─────────────────────────────────────────────────────────┘
    """
    # Normalise both sides to their core domain, then compare.
    return "first" if get_registrable_domain(cookie_domain) == site_domain else "third"


def describe_expiry(expires: float) -> dict:
    """
    Turn Playwright's raw expiry value into something human-readable.

    Playwright gives `expires` as a Unix timestamp (seconds since 1 Jan 1970),
    or `-1` for a session cookie.

    SESSION COOKIE     — expires = -1. Lives only in memory. Deleted the moment
                         you close the browser. Lower privacy risk.
    PERSISTENT COOKIE  — expires = a real timestamp. Written to disk and
                         survives restarts. This is what enables long-term
                         tracking; some last 2 years or more.

    We return a dict so the caller gets the type, a readable date, and the
    lifetime in days all at once.
    """
    # -1 (or any negative number) means "session cookie".
    if expires is None or expires < 0:
        return {"type": "session", "expires_at": None, "lifetime_days": None}

    # Convert the Unix timestamp into a real datetime object in UTC.
    expiry_dt = datetime.fromtimestamp(expires, tz=timezone.utc)

    # Subtracting two datetimes gives a `timedelta`. `.days` gives whole days.
    lifetime_days = (expiry_dt - datetime.now(timezone.utc)).days

    return {
        "type": "persistent",
        # isoformat() produces the standard "2027-07-31T09:14:22+00:00" string.
        "expires_at": expiry_dt.isoformat(),
        # max(0, ...) so an already-expired cookie shows 0 rather than a negative.
        "lifetime_days": max(0, lifetime_days),
    }


# ---------------------------------------------------------------------------
# THE MAIN SCAN FUNCTION
# ---------------------------------------------------------------------------
# Note the `async` keyword. This makes it a "coroutine" — a function that can
# pause at every `await` to let other work happen, then resume. Playwright's
# API is async because talking to a browser is full of waiting, and waiting is
# exactly what async is designed to handle efficiently.

async def scan_website(
    url: str,
    headless: bool = True,
    settle_seconds: int = DEFAULT_SETTLE_SECONDS,
    accept_consent: bool = False,
) -> dict:
    """
    Visit `url` in a real browser and return everything we observed as a dict.

    Parameters
    ----------
    url            : the website to scan, e.g. "https://example.com"
    headless       : True  = run the browser invisibly (fast, needed on servers)
                     False = show the browser window (great for learning/demos)
    settle_seconds : how long to idle after load, letting delayed trackers fire

    Returns
    -------
    A plain Python dict describing the scan. Plain dicts (not custom classes)
    because they convert straight to JSON for the API in Phase 3.

    THE SEVEN STEPS
    ---------------
      1. Start Playwright
      2. Launch a browser
      3. Create a fresh, empty browsing context (a clean profile)
      4. Attach a listener that records every network request
      5. Navigate to the URL and wait
      6. Read the cookie jar
      7. Close everything and return the results
    """

    # Record when the scan started, so we can report how long it took.
    started_at = datetime.now(timezone.utc)

    # Pull the hostname out of the URL and reduce it to its core domain.
    # We need this to decide first-party vs third-party later.
    site_domain = get_registrable_domain(urlparse(url).netloc)

    # This list will be filled in by the event listener in step 4.
    # It lives out here (not inside the listener) so it survives after the
    # listener has stopped firing.
    network_requests = []

    # `async with` is a context manager: it guarantees Playwright is shut down
    # cleanly even if an exception is thrown inside the block. Without it, a
    # crash could leave a zombie browser process running on your machine.
    async with async_playwright() as p:

        # -------------------------------------------------------------------
        # STEP 2 — Launch the browser
        # -------------------------------------------------------------------
        # `p.chromium` is the Chromium browser engine (the open-source core of
        # Chrome and Edge). Playwright also offers `p.firefox` and `p.webkit`.
        # We use Chromium because it's what most real visitors use, so the
        # cookies we see match what real visitors get.
        #
        # HEADLESS means "no visible window". The browser is fully functional —
        # it renders, runs JavaScript, stores cookies — it just doesn't draw
        # pixels to a screen. That's essential on a server, which has no screen.
        #
        # PHASE 6 — the container flag.
        # `browser_launch_args()` returns [] normally and ["--no-sandbox"] only
        # when BROWSER_NO_SANDBOX is set. Read the docstring on that function
        # before you set it: it disables a real security boundary, and we are
        # pointing this browser at arbitrary user-supplied URLs.
        browser = await p.chromium.launch(
            headless=headless,
            args=browser_launch_args(),
        )

        # -------------------------------------------------------------------
        # STEP 3 — Create a fresh browsing context
        # -------------------------------------------------------------------
        # A CONTEXT is like a brand-new incognito profile: its own empty cookie
        # jar, own local storage, own cache. This matters enormously for us —
        # it guarantees every cookie we find was set by THIS scan of THIS site,
        # not left over from an earlier one. Our results are reproducible.
        #
        # Think of it as: browser = the application, context = one user profile,
        # page = one tab inside that profile.
        context = await browser.new_context(
            user_agent=REALISTIC_USER_AGENT,
            viewport=VIEWPORT,
            # Pretend to be in Europe. Under GDPR, many sites show a consent
            # banner and withhold trackers for EU visitors — this makes us see
            # the same page a European regulator would see.
            locale="en-GB",
            timezone_id="Europe/London",
        )

        # Open a tab inside that context.
        page = await context.new_page()

        # -------------------------------------------------------------------
        # STEP 4 — Listen for every network request
        # -------------------------------------------------------------------
        # `page.on("request", handler)` registers a CALLBACK: Playwright will
        # call our function automatically every single time the page requests
        # anything — an image, a script, a font, a tracking pixel.
        #
        # WHY WE CARE: a tracking pixel is a 1x1 invisible image. Loading it
        # tells the ad company "this person viewed this page". It may set no
        # cookie at all, so a cookie-only scanner would completely miss it.
        # Watching the network catches it.

        def handle_request(request):
            """Called automatically by Playwright on every outgoing request."""
            try:
                # Extract the hostname from the request URL.
                host = urlparse(request.url).netloc

                # Skip anything without a hostname (e.g. "data:" or "blob:" URLs,
                # which are inline data, not real network calls).
                if not host:
                    return

                network_requests.append({
                    # Truncate very long URLs (ad URLs can be thousands of
                    # characters) to keep our output readable and our future
                    # database rows a sane size.
                    "url": request.url[:500],
                    "domain": host,
                    # Is this request going to a different company?
                    "party": classify_party(host, site_domain),
                    # WHAT kind of resource: "script", "image", "xhr", "font"...
                    # An "image" request to an ad domain is very likely a pixel.
                    "resource_type": request.resource_type,
                    # GET, POST, etc.
                    "method": request.method,
                })
            except Exception:
                # A malformed URL in one request must never crash the whole
                # scan. We swallow the error and carry on. (In production you
                # would log this rather than pass silently.)
                pass

        # Register the callback. Note: NO parentheses after handle_request —
        # we are passing the function ITSELF, not calling it.
        page.on("request", handle_request)

        # -------------------------------------------------------------------
        # STEP 5 — Navigate to the page
        # -------------------------------------------------------------------
        # `wait_until` controls when `goto` considers the job done:
        #   "domcontentloaded" — HTML parsed (earliest, fastest)
        #   "load"             — HTML + images + stylesheets finished
        #   "networkidle"      — no network activity for 500ms (latest)
        #
        # We choose "domcontentloaded" and then wait manually, because
        # "networkidle" NEVER fires on sites with continuous polling (live
        # chat widgets, auto-refreshing ads) and would time out on them.
        # This is a deliberate reliability trade-off.
        navigation_error = None
        try:
            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=DEFAULT_NAV_TIMEOUT_MS,
            )
            # HTTP status code: 200 = OK, 404 = not found, 500 = server error.
            # `response` can be None for some navigations, hence the guard.
            status_code = response.status if response else None
        except PlaywrightTimeout:
            # The site was too slow. We do NOT give up — some cookies may have
            # already been set before the timeout, and partial data is still
            # useful. We record the problem and continue.
            navigation_error = f"Navigation timed out after {DEFAULT_NAV_TIMEOUT_MS}ms"
            status_code = None
        except Exception as e:
            # DNS failure, connection refused, bad SSL certificate, etc.
            navigation_error = f"{type(e).__name__}: {e}"
            status_code = None

        # Now sit still and let delayed trackers fire.
        # `asyncio.sleep` is the ASYNC version of `time.sleep`. The difference:
        # time.sleep freezes the entire program; asyncio.sleep only pauses THIS
        # coroutine, so Playwright can keep processing browser events (like our
        # request listener) in the meantime. Using time.sleep here would be a
        # genuine bug.
        await asyncio.sleep(settle_seconds)

        # -------------------------------------------------------------------
        # STEP 6 — Read the cookie jar
        # -------------------------------------------------------------------
        # THE KEY LINE OF THE WHOLE SCANNER.
        # `context.cookies()` returns EVERY cookie in this context's jar —
        # first-party AND third-party, set by HTTP headers AND by JavaScript.
        #
        # Note we ask the CONTEXT, not the page. The page's `document.cookie`
        # in JavaScript would only show first-party, non-HttpOnly cookies —
        # which would miss exactly the ones we care most about. Asking the
        # context bypasses that limit entirely, because we're the browser
        # operator, not a script running inside the page.
        raw_cookies = await context.cookies()

        # -------------------------------------------------------------------
        # STEP 6b — OPTIONAL SECOND PASS: click "Accept all", then look again
        # -------------------------------------------------------------------
        # THIS IS THE MOST VALUABLE THING THE SCANNER DOES.
        #
        # Everything above captured the PRE-CONSENT state. Now we click the
        # banner and watch what appears. The difference between the two answers
        # the question people actually have: what does accepting cost you?
        #
        # WHY THE SAME BROWSER SESSION, not a second scan?
        #   * It's what actually happens to a real visitor — they don't reload
        #   * The pre-consent cookies are still there, so the diff is exact
        #   * One browser launch instead of two: roughly half the time
        raw_post_cookies = None
        consent_click = None

        # Mark where the pre-consent requests end. Everything appended to
        # `network_requests` after this index was triggered BY the consent
        # click — which is exactly the "what did agreeing actually unlock?"
        # evidence the report is built on.
        pre_consent_request_count = len(network_requests)

        if accept_consent:
            # Local import: only this code path needs it.
            from consent_clicker import accept_consent as click_accept

            consent_click = await click_accept(page)

            if consent_click.get("clicked"):
                # Wait again. Newly-unblocked scripts have to download and run
                # before their cookies exist — clicking and immediately reading
                # would find almost nothing and report a false "consent adds
                # no tracking", which is the worst possible wrong answer.
                await asyncio.sleep(settle_seconds + 2)
                raw_post_cookies = await context.cookies()

        # Grab a couple of page facts for the report. Wrapped in try/except
        # because if navigation failed there may be no page to read.
        try:
            page_title = await page.title()
            final_url = page.url  # may differ from `url` if the site redirected
        except Exception:
            page_title = None
            final_url = url

        # -------------------------------------------------------------------
        # STEP 7 — Tidy up
        # -------------------------------------------------------------------
        # Always close the browser. Each Chromium instance uses hundreds of MB
        # of RAM; leaking them would eventually exhaust the machine's memory.
        await context.close()
        await browser.close()

    # -----------------------------------------------------------------------
    # PROCESS THE RAW DATA INTO OUR OWN CLEAN SHAPE
    # -----------------------------------------------------------------------
    # Playwright's cookie dicts contain fields we don't need and use names we
    # may not like. Reshaping them here means the rest of the project depends
    # on OUR format, not Playwright's — so if we ever swap out Playwright,
    # only this file changes.

    def reshape(raw_list):
        """
        Convert Playwright's cookie dicts into OUR format.

        Extracted into a local function because we now do this twice — once
        for the pre-consent cookies and once for post-consent. Two copies of
        this mapping would eventually drift apart, and then the diff would
        compare cookies described in two slightly different ways.
        """
        out = []
        for c in raw_list:
            cookie_domain = c.get("domain", "")
            expiry_info = describe_expiry(c.get("expires", -1))
            out.append({
                "name": c.get("name"),
                "domain": cookie_domain,
                # The URL path the cookie applies to. "/" = the whole site.
                "path": c.get("path", "/"),
                "party": classify_party(cookie_domain, site_domain),
                "type": expiry_info["type"],
                "expires_at": expiry_info["expires_at"],
                "lifetime_days": expiry_info["lifetime_days"],
                # SECURITY FLAGS — these matter for the compliance report:
                # httpOnly : JavaScript cannot read it. Protects session
                #            tokens against XSS. Good practice.
                "http_only": c.get("httpOnly", False),
                # secure   : only ever sent over HTTPS.
                "secure": c.get("secure", False),
                # sameSite : controls cross-site sending. "None" is the
                #            tracker's setting.
                "same_site": c.get("sameSite", "None"),
                # We deliberately do NOT store the cookie's VALUE — it may
                # contain personal data, and storing it would make our own
                # audit tool a privacy liability.
                "value_length": len(c.get("value", "")),
            })
        return out

    cookies = reshape(raw_cookies)

    # ----- Summarise the network requests by domain -----
    # Hundreds of raw requests are unreadable. What matters is: WHICH other
    # companies did this page contact, and how often?
    def summarise_third_parties(requests):
        """Count third-party requests per domain, busiest first.

        Extracted into a function in Phase 6 because we now call it TWICE —
        once for the requests seen before the consent click and once for all
        of them. Same reasoning as `reshape()` above: two copies of a counting
        rule drift apart, and then the before/after comparison is comparing
        two slightly different things.
        """
        counts = {}
        for req in requests:
            if req["party"] == "third":
                # `.get(key, 0) + 1` is the standard "count occurrences" idiom:
                # if we've seen this domain, add 1; if not, start at 0 and add 1.
                counts[req["domain"]] = counts.get(req["domain"], 0) + 1
        # Sort domains by request count, highest first.
        # `sorted(...)` returns a list of (key, value) tuples.
        # `key=lambda item: item[1]` says "sort by the second element".
        return sorted(counts.items(), key=lambda item: item[1], reverse=True)

    sorted_third_parties = summarise_third_parties(network_requests)

    # ⚠ BUG FIXED IN PHASE 6 — and found by a LINTER, which is the point.
    #
    # `post_requests_start` was assigned above and never read. Ruff flagged it
    # as F841 (unused variable), which sounds like tidiness and wasn't: it was
    # the marker recording where the pre-consent requests ended, and without it
    # classifier.py was passing the SAME domain list as both `pre_domains` and
    # `post_domains`. The diff therefore always reported "0 new domains
    # contacted after consent" — a plausible-looking number that was structurally
    # incapable of being anything else.
    #
    # Worth sitting with: the tests passed, the dashboard rendered, and the
    # figure was always wrong. An unused variable is often a half-finished
    # thought, and this is why "just style" warnings deserve a look.
    pre_consent_third_parties = summarise_third_parties(
        network_requests[:pre_consent_request_count]
    )

    finished_at = datetime.now(timezone.utc)

    # Build the final result dict. This exact shape is what Phase 2's
    # classifier will consume and Phase 3's API will return as JSON.
    return {
        "url": url,
        "final_url": final_url,
        "domain": site_domain,
        "page_title": page_title,
        "http_status": status_code,
        "scanned_at": started_at.isoformat(),
        "duration_seconds": round((finished_at - started_at).total_seconds(), 2),
        "error": navigation_error,
        "cookies": cookies,
        "cookie_count": len(cookies),
        # Two generator expressions inside sum(). `c["party"] == "first"`
        # evaluates to True/False, and in Python True == 1, so summing the
        # booleans counts how many are True. A concise, idiomatic count.
        "first_party_cookies": sum(1 for c in cookies if c["party"] == "first"),
        "third_party_cookies": sum(1 for c in cookies if c["party"] == "third"),
        "session_cookies": sum(1 for c in cookies if c["type"] == "session"),
        "persistent_cookies": sum(1 for c in cookies if c["type"] == "persistent"),
        "total_requests": len(network_requests),
        # Turn the sorted list of tuples into a list of dicts — JSON has no
        # tuple type, and dicts are self-documenting for whoever reads the JSON.
        "third_party_domains": [
            {"domain": d, "request_count": n} for d, n in sorted_third_parties
        ],
        # The same summary, but only for requests made BEFORE the consent
        # click. Identical to the list above when accept_consent is False,
        # which is correct: nothing was clicked, so nothing was unlocked.
        "pre_consent_third_party_domains": [
            {"domain": d, "request_count": n} for d, n in pre_consent_third_parties
        ],
        # Keep the full request list too — Phase 2's classifier will scan these
        # URLs for tracker signatures (e.g. spotting "facebook.com/tr" pixels).
        "requests": network_requests,

        # --- Post-consent pass (only present when accept_consent=True) ---
        # `consent_click` records WHAT was clicked, so the finding stays
        # auditable. `post_consent_cookies` is the raw second reading; the
        # actual diff is computed after classification, because comparing
        # categories requires both sides to be classified first.
        "consent_click": consent_click,
        "post_consent_cookies": reshape(raw_post_cookies)
                                if raw_post_cookies is not None else None,
    }


# ---------------------------------------------------------------------------
# PRETTY-PRINTING THE RESULT
# ---------------------------------------------------------------------------

def print_report(result: dict) -> None:
    """Print a human-readable summary of a scan result to the terminal."""
    line = "=" * 64
    sub = "-" * 64

    print(f"\n{line}")
    print("  CookieGuard Scan Report")
    print(line)
    print(f"  URL scanned : {result['url']}")
    print(f"  Final URL   : {result['final_url']}")
    print(f"  Page title  : {result['page_title']}")
    print(f"  HTTP status : {result['http_status']}")
    print(f"  Scanned at  : {result['scanned_at']}")
    print(f"  Duration    : {result['duration_seconds']}s")

    # Only show the error line if something actually went wrong.
    if result["error"]:
        print(f"  ⚠  WARNING  : {result['error']}")

    # ----- Cookie table -----
    print(f"\n  COOKIES FOUND: {result['cookie_count']}")
    print(f"  ({result['first_party_cookies']} first-party, "
          f"{result['third_party_cookies']} third-party | "
          f"{result['session_cookies']} session, "
          f"{result['persistent_cookies']} persistent)")
    print(f"  {sub}")

    if result["cookies"]:
        # f-string alignment: `{value:<24}` = left-align, pad to 24 characters.
        # This is how we get neat columns without a table library.
        print(f"  {'NAME':<26}{'DOMAIN':<26}{'PARTY':<8}{'TYPE':<12}{'LIFETIME'}")
        for c in result["cookies"]:
            lifetime = f"{c['lifetime_days']}d" if c["lifetime_days"] is not None else "-"
            # [:25] truncates long names so columns never break alignment.
            print(f"  {str(c['name'])[:25]:<26}"
                  f"{str(c['domain'])[:25]:<26}"
                  f"{c['party']:<8}"
                  f"{c['type']:<12}"
                  f"{lifetime}")
    else:
        print("  (no cookies found)")

    # ----- Third-party domain table -----
    print(f"\n  THIRD-PARTY DOMAINS CONTACTED: {len(result['third_party_domains'])}")
    print(f"  (out of {result['total_requests']} total network requests)")
    print(f"  {sub}")

    if result["third_party_domains"]:
        # Show only the top 15 so the terminal isn't flooded on big sites.
        for entry in result["third_party_domains"][:15]:
            print(f"  {entry['domain'][:44]:<46}{entry['request_count']} request(s)")
        if len(result["third_party_domains"]) > 15:
            remaining = len(result["third_party_domains"]) - 15
            print(f"  ... and {remaining} more")
    else:
        print("  (none — this site contacts no external domains)")

    # ----- Consent diff, if we did a second pass -----
    click = result.get("consent_click")
    if click:
        print("\n  CONSENT BANNER")
        print(f"  {sub}")
        if click.get("clicked"):
            print(f"  Clicked: \"{click.get('text')}\"")
            print(f"  Found via: {click.get('method')} ({click.get('detail')})")
            post = result.get("post_consent_cookies")
            if post is not None:
                before = result["cookie_count"]
                after = len(post)
                print(f"\n  BEFORE consent: {before} cookies")
                print(f"  AFTER consent:  {after} cookies"
                      f"   (+{after - before})")
                if before:
                    print(f"  Accepting multiplied tracking by "
                          f"{round(after / before, 1)}x")
        elif click.get("method") == "bot_challenge":
            # A completely different finding. The scan didn't see the site at
            # all, so EVERY number above is about the challenge page, not the
            # real one.
            print("  ⚠  BLOCKED BY A BOT CHALLENGE")
            print(f"  {sub}")
            print(f"  {click.get('detail')}")
            print("  The scanner never reached the real site, so every figure")
            print("  in this report describes the challenge page instead.")
            print("  Anti-bot protection is a genuine limitation of automated")
            print("  scanning — not a finding about the site's cookies.")
        else:
            print(f"  No accept button found ({click.get('method')}).")
            print("  NOTE: 'no banner found' is NOT the same as "
                  "'no tracking added'.")

            # DIAGNOSTICS. A bare "not found" tells you nothing about WHY.
            # Printing what we actually saw turns one run into an answer
            # instead of the start of a guessing game.
            seen = click.get("candidates_seen") or []
            frames = click.get("frame_urls") or []
            print(f"\n  DIAGNOSTICS — {click.get('frame_count', 0)} frame(s) on the page")
            if frames:
                print(f"  {sub}")
                print("  Non-main frames (a banner may be inside one):")
                for u in frames:
                    print(f"    {u}")
            print(f"  {sub}")
            if seen:
                print(f"  Visible clickable elements ({len(seen)} shown):")
                for t in seen:
                    print(f"    {t}")
                print("\n  If an 'Accept'-type button is listed above, its text")
                print("  needs adding to ACCEPT_PATTERNS in consent_clicker.py.")
                print("  If NOTHING here looks like a banner, the site probably")
                print("  did not show one to this IP — see the geo note below.")
            else:
                print("  No visible clickable elements found at all.")
                print("  That strongly suggests the banner is inside a CLOSED")
                print("  shadow root, or was never shown to this visitor.")

    print(f"{line}\n")


# ---------------------------------------------------------------------------
# COMMAND-LINE ENTRY POINT
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    """Define which command-line options this script accepts."""
    parser = argparse.ArgumentParser(
        description="CookieGuard — scan a website for cookies and trackers.",
        # Shows default values automatically in --help output.
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # A POSITIONAL argument: required, given without a flag.
    parser.add_argument("url", help="The website URL to scan, e.g. https://example.com")

    # OPTIONAL arguments: start with -- and have defaults.
    parser.add_argument(
        "--headed",
        # `store_true` means: if the flag is present the value is True,
        # otherwise False. No value needs to be typed after it.
        action="store_true",
        help="Show the browser window instead of running it invisibly",
    )
    parser.add_argument(
        "--wait",
        type=int,  # argparse converts the text "5" into the integer 5
        default=DEFAULT_SETTLE_SECONDS,
        help="Seconds to wait after page load, letting delayed trackers fire",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional path to save the full result as JSON, e.g. data/example.json",
    )
    parser.add_argument(
        "--accept-consent",
        action="store_true",
        help=("Also click 'Accept all' and scan again, then report the "
              "difference. This is the most useful thing the scanner does."),
    )
    return parser


def normalise_url(url: str) -> str:
    """
    Make sure the URL has a scheme (http:// or https://).

    Playwright rejects "example.com" — it needs "https://example.com".
    Rather than making the user remember, we add it for them.
    """
    if not url.startswith(("http://", "https://")):
        return "https://" + url
    return url


async def main_async() -> int:
    """
    The async entry point. Returns an exit code: 0 = success, 1 = failure.
    We return rather than call sys.exit() here so this stays a normal,
    testable function.
    """
    args = build_arg_parser().parse_args()
    url = normalise_url(args.url)

    print(f"\n[CookieGuard] Scanning {url}")
    print(f"[CookieGuard] Mode: {'headed (visible)' if args.headed else 'headless'}"
          f" | settle wait: {args.wait}s")
    print("[CookieGuard] Launching browser...")

    try:
        result = await scan_website(
            url=url,
            # `--headed` present means headless should be False. Hence `not`.
            headless=not args.headed,
            settle_seconds=args.wait,
            accept_consent=args.accept_consent,
        )
    except Exception as e:
        # Catch-all so the user gets a clear message instead of a raw traceback.
        print(f"\n[CookieGuard] ERROR: scan failed — {type(e).__name__}: {e}")
        print("[CookieGuard] Did you run `playwright install chromium`?")
        return 1

    print_report(result)

    # Save to a file if the user asked for it.
    if args.output:
        out_path = Path(args.output)
        # Create the parent folder if it doesn't exist.
        # parents=True  -> create intermediate folders too
        # exist_ok=True -> don't error if it already exists
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # `encoding="utf-8"` so non-English characters in page titles are saved
        # correctly. `indent=2` makes the JSON human-readable.
        # `ensure_ascii=False` keeps real characters instead of \u escapes.
        out_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"[CookieGuard] Full result saved to {out_path}")

    return 0


# This `if` is a Python convention meaning: "only run this when the file is
# executed directly (python scan.py), NOT when it is imported by another file".
# It matters because in Phase 3 the FastAPI app will `import scan_website` from
# here — and we do not want the CLI to fire off when it does.
if __name__ == "__main__":
    # `asyncio.run()` is the bridge from normal synchronous Python into the
    # async world. It starts an event loop, runs our coroutine to completion,
    # then shuts the loop down.
    exit_code = asyncio.run(main_async())
    sys.exit(exit_code)
