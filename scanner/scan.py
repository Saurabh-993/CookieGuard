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

# The Playwright async API. `async_playwright` is the entry point that starts
# Playwright's background driver process.
# `TimeoutError` is renamed to PlaywrightTimeout so it does not collide with
# Python's own built-in TimeoutError.
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# Our own shared domain logic, backed by Mozilla's Public Suffix List.
# Both scan.py and classifier.py import from here so they can never disagree
# about what counts as the same organisation.
from domains import registrable_domain


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
        browser = await p.chromium.launch(headless=headless)

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

    cookies = []
    for c in raw_cookies:
        # `c.get("key", default)` reads a dict key safely: if the key is
        # missing it returns the default instead of raising KeyError.
        cookie_domain = c.get("domain", "")
        expiry_info = describe_expiry(c.get("expires", -1))

        cookies.append({
            "name": c.get("name"),
            "domain": cookie_domain,
            # The URL path the cookie applies to. "/" means the whole site.
            "path": c.get("path", "/"),
            "party": classify_party(cookie_domain, site_domain),
            "type": expiry_info["type"],
            "expires_at": expiry_info["expires_at"],
            "lifetime_days": expiry_info["lifetime_days"],
            # SECURITY FLAGS — these matter for the compliance report:
            # httpOnly  : JavaScript cannot read this cookie. Protects session
            #             tokens against XSS attacks. Good practice.
            "http_only": c.get("httpOnly", False),
            # secure    : only ever sent over HTTPS, never plain HTTP.
            "secure": c.get("secure", False),
            # sameSite  : controls whether the cookie is sent on cross-site
            #             requests. "Strict"/"Lax" limit tracking; "None"
            #             explicitly allows it and is the tracker's setting.
            "same_site": c.get("sameSite", "None"),
            # We deliberately do NOT store the cookie's VALUE. It may contain
            # personal data, and storing it would make our own audit tool a
            # privacy liability. We only need the metadata to classify it.
            # This is a defensible design decision worth stating in interview.
            "value_length": len(c.get("value", "")),
        })

    # ----- Summarise the network requests by domain -----
    # Hundreds of raw requests are unreadable. What matters is: WHICH other
    # companies did this page contact, and how often?
    third_party_domains = {}  # dict: domain name -> how many requests
    for req in network_requests:
        if req["party"] == "third":
            # `.get(key, 0) + 1` is the standard "count occurrences" idiom:
            # if we've seen this domain, add 1; if not, start at 0 and add 1.
            third_party_domains[req["domain"]] = third_party_domains.get(req["domain"], 0) + 1

    # Sort domains by request count, highest first.
    # `sorted(...)` returns a list of (key, value) tuples.
    # `key=lambda item: item[1]` says "sort by the second element" (the count).
    # `reverse=True` makes it descending.
    sorted_third_parties = sorted(
        third_party_domains.items(), key=lambda item: item[1], reverse=True
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
        # Keep the full request list too — Phase 2's classifier will scan these
        # URLs for tracker signatures (e.g. spotting "facebook.com/tr" pixels).
        "requests": network_requests,
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
