"""
CookieGuard — REST API (Phase 3)
=================================

WHAT THIS FILE DOES
-------------------
Puts the scanner, classifier and database behind an HTTP interface, so a web
page (Phase 4) — or any other program — can use them over the network.

    Before Phase 3:   you had to be sitting at the terminal
    After Phase 3:    any browser, script or service can drive it

WHAT IS A REST API?
-------------------
REST is a set of conventions for exposing things over HTTP. The core idea:
your data is a set of RESOURCES, each with a URL, and you act on them using
standard HTTP verbs.

    GET    /api/domains        "give me the list of domains"
    GET    /api/scans/12       "give me scan 12"
    POST   /api/scan           "create a new scan"
    DELETE /api/scans/12       "delete scan 12"

Two conventions worth internalising:

  * The URL names a NOUN (a thing), the verb says what to DO with it.
    `/api/scans/12` + `DELETE`, never `/api/deleteScan?id=12`.

  * GET must never change anything. Browsers, proxies and crawlers assume they
    can repeat a GET safely — that property is called being SAFE. If GET
    deleted things, a search engine crawling your site would wipe your data.
    (This genuinely happened to people in the early 2000s.)

THE ARCHITECTURAL RULE FOR THIS FILE
------------------------------------
**No SQL in here.** Not one query.

Every database operation goes through a function in `db.py`. This file's job is
HTTP: read the request, call the right function, shape the response, pick a
status code.

Why that matters:
  * Migrating SQLite → PostgreSQL touches exactly one file
  * SQL-injection review has exactly one place to look
  * The scanner and classifier stay usable without the API

That separation has a name — the **layered architecture**: HTTP layer → data
layer → domain logic. Each layer only talks to the one below it.

RUN IT
------
    uvicorn api.main:app --reload

Then open http://127.0.0.1:8000/docs — FastAPI generates a full interactive
API console from the type hints in this file and in schemas.py.
"""

import asyncio
import ipaddress
import socket
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import List
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi import Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Make our own modules importable however the app is launched.
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "scanner"))

import config
import db
from schemas import (
    CookieOut,
    DomainReport,
    DomainSummary,
    ErrorResponse,
    HealthResponse,
    ScanCreatedResponse,
    ScanDetail,
    ScanRequest,
    ScanSummary,
)

API_VERSION = "0.3.0"


# ---------------------------------------------------------------------------
# THE APPLICATION
# ---------------------------------------------------------------------------
# Everything passed here shows up on the /docs page. Good descriptions cost
# nothing and make the difference between a demo that impresses and one that
# doesn't.

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup and shutdown, in one function.

    Everything BEFORE `yield` runs once when the server boots.
    Everything AFTER runs once when it shuts down.

    Creating the schema at startup means a fresh clone works immediately — no
    "remember to run init first" step. `init_db` is idempotent, so calling it
    on every boot costs nothing.

    (FastAPI used to use `@app.on_event("startup")` for this. That's deprecated
    now: the context-manager form makes the pairing between setup and teardown
    explicit, and guarantees cleanup runs even if startup raised. It's the same
    reasoning as `async with` in scan.py — see TEACHING.md §14.)
    """
    # Phase 6: print the effective configuration on boot. Nearly every
    # "works locally, broken in the container" bug is a config value that
    # isn't what someone assumed — four lines of log output beats an hour
    # of guessing.
    config.print_config()
    db.init_db()
    yield
    # Nothing to tear down yet. When we add a connection pool or a background
    # worker in a later phase, closing it goes here.


app = FastAPI(
    title="CookieGuard API",
    version=API_VERSION,
    lifespan=lifespan,
    description=(
        "Scan websites for cookies and trackers, classify them into compliance "
        "categories, and retrieve audit history.\n\n"
        "**Note:** scans capture the *pre-consent* state — the scanner clicks "
        "nothing. Every non-necessary cookie found is therefore a cookie set "
        "before the user agreed to anything."
    ),
    # Tags group endpoints into sections on the docs page.
    openapi_tags=[
        {"name": "scanning", "description": "Trigger new scans"},
        {"name": "domains", "description": "Browse scanned domains and history"},
        {"name": "scans", "description": "Retrieve individual scan results"},
        {"name": "reports", "description": "Compliance reports and trends"},
        {"name": "system", "description": "Health and diagnostics"},
    ],
)


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
# CORS = Cross-Origin Resource Sharing.
#
# THE PROBLEM IT SOLVES
# ---------------------
# Browsers enforce the SAME-ORIGIN POLICY: JavaScript on page A may not read
# responses from server B. An "origin" is scheme + host + port, so all of these
# are different origins:
#
#     http://localhost:8000     https://localhost:8000    (different scheme)
#     http://localhost:8000     http://localhost:3000     (different port)
#     file:///C:/.../index.html                           (no origin at all)
#
# This rule exists for a real reason: without it, any website you visited could
# quietly make requests to your bank in your browser, WITH your cookies
# attached, and read the replies.
#
# WHY WE NEED TO RELAX IT
# -----------------------
# Our Phase 4 dashboard is a plain HTML file. Opened from disk it has origin
# `null`; served by a dev server it's on a different port from the API. Either
# way, the browser blocks its fetch() calls unless the API explicitly says
# "requests from that origin are allowed".
#
# CORS is the API's way of saying that. The server sends an
# `Access-Control-Allow-Origin` header, and the browser then permits the read.
#
# ✅ RESOLVED IN PHASE 6 (this used to be `allow_origins=["*"]` with a TODO).
#
# The allow-list now comes from configuration, so the SAME code runs on your
# laptop and in production with different origins:
#
#     laptop      CORS_ORIGINS unset  →  http://localhost:8000, http://127.0.0.1:8000
#     production  CORS_ORIGINS=https://cookieguard.example
#
# WHY "*" WAS ACCEPTABLE BEFORE AND ISN'T NOW
# -------------------------------------------
# "*" means any website on the internet may make requests to this API from a
# visitor's browser. That was harmless while the API only ever listened on
# localhost. Once it has a public address it is not, because POST /api/scan
# spends real CPU driving a real browser — any page a user visits could make
# their browser tell our server to go and scan things.
#
# `allow_credentials` stays False. Combined with "*" it is a serious hole, and
# browsers refuse the combination outright — but we don't use cookies for auth
# at all, so False is simply correct rather than a workaround.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if config.CORS_ALLOW_ALL else config.CORS_ORIGINS,
    allow_credentials=False,   # deliberately False — see the note above
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# SECURITY: blocking SSRF
# ---------------------------------------------------------------------------

# Hostnames that must never be scanned, whatever else the checks say.
BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata.google.internal",      # Google Cloud metadata service
    "metadata.goog",
}

# AWS/Azure/GCP all expose instance metadata — including temporary IAM
# credentials — on this link-local address. It is THE classic SSRF target.
CLOUD_METADATA_IPS = {"169.254.169.254", "fd00:ec2::254"}


def validate_scan_url(url: str) -> str:
    """
    Check that a URL is safe for the server to fetch. Raises HTTPException(400).

    ⚠ THIS IS THE MOST SECURITY-SENSITIVE FUNCTION IN THE PROJECT.

    WHAT IS SSRF?
    -------------
    Server-Side Request Forgery. Our API takes a URL from a user and makes the
    SERVER fetch it. That's the whole feature — and it's also a serious
    vulnerability if unguarded, because the server sits somewhere the user
    doesn't:

        User's browser  ──✗──▶  internal-admin.company.local   (blocked)
        Our server      ──✓──▶  internal-admin.company.local   (allowed!)

    So an attacker can use our server as a proxy into places they can't reach:

        http://localhost:8000/admin        our own internal endpoints
        http://192.168.1.1/                the private network we're hosted in
        http://169.254.169.254/latest/...  ← AWS instance metadata

    That last one is the serious case. On EC2 that address returns temporary
    IAM credentials for the instance's role. An unguarded URL-fetching feature
    hands those to an attacker, who then has our AWS permissions.

    This is not theoretical: it is essentially how the 2019 Capital One breach
    worked — an SSRF flaw was used to reach the metadata service and retrieve
    credentials, exposing ~100 million records.

    Since Phase 7 deploys this to EC2, this function is not optional.

    WHAT WE CHECK
    -------------
      1. Scheme must be http or https  (blocks file://, gopher://, ftp://)
      2. A hostname must be present
      3. Hostname isn't a known-blocked name
      4. If it's an IP literal, it must be a public address
      5. If it's a name, resolve it and check the resulting IP too

    Step 5 matters because `evil.com` can be a DNS record pointing at
    127.0.0.1. Checking only the text of the hostname is not enough.

    HONEST LIMITATION
    -----------------
    There is still a DNS-rebinding window: the name could resolve to a public
    IP when we check and a private one microseconds later when Playwright
    connects. Closing that properly means resolving once and connecting to the
    pinned IP. Logged as a TODO; the current checks stop every straightforward
    attack.
    """
    parsed = urlparse(url)

    # ---- 1. scheme ----
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported URL scheme '{parsed.scheme}'. Use http or https.",
        )

    # ---- 2. hostname present ----
    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(status_code=400, detail="URL has no hostname.")

    hostname = hostname.lower()

    # ---- 3. blocked names ----
    if hostname in BLOCKED_HOSTNAMES or hostname.endswith(".local"):
        raise HTTPException(
            status_code=400,
            detail=f"Refusing to scan internal hostname '{hostname}'.",
        )

    # ---- 4 & 5. IP address checks ----
    def reject_if_private(ip_text: str):
        """Raise if this IP is anything other than a normal public address."""
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            return  # not an IP; nothing to check here

        if ip_text in CLOUD_METADATA_IPS:
            raise HTTPException(
                status_code=400,
                detail="Refusing to scan the cloud instance metadata endpoint.",
            )
        # `is_global` is True only for addresses routable on the public
        # internet. It excludes private ranges (10.x, 192.168.x, 172.16-31.x),
        # loopback (127.x, ::1), link-local (169.254.x), multicast and
        # reserved blocks — one property instead of a hand-written list we'd
        # inevitably get wrong.
        if not ip.is_global:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Refusing to scan non-public address '{ip_text}'. "
                    "Private, loopback and link-local addresses are blocked."
                ),
            )

    # Is the hostname itself a literal IP?
    reject_if_private(hostname)

    # Otherwise resolve the name and check where it actually points.
    try:
        # getaddrinfo returns every address the name resolves to. We check all
        # of them — a name with one public and one private address must still
        # be rejected.
        for info in socket.getaddrinfo(hostname, None):
            reject_if_private(info[4][0])
    except HTTPException:
        raise                    # our own rejection — let it through
    except Exception:
        # DNS failed (offline, no such host). We don't reject on this: the
        # scan will fail anyway with a clearer message, and being offline
        # shouldn't look like a security error.
        pass

    return url


# ---------------------------------------------------------------------------
# SERVING THE DASHBOARD
# ---------------------------------------------------------------------------
# The Phase 4 dashboard is three static files. We can serve them from the same
# server that serves the API.
#
# WHY BOTHER, WHEN CORS ALREADY ALLOWS file://?
#
#   Opening index.html from disk works, but the browser treats it as origin
#   `null`, so every API call is cross-origin and depends on our permissive
#   CORS settings. Serving the dashboard from FastAPI puts the page and the API
#   on the SAME ORIGIN — so there is no cross-origin request at all, and the
#   dashboard would keep working even after we tighten CORS in Phase 7.
#
#   It also means one command starts everything, and it is how the app will be
#   packaged in Docker.
#
# `html=True` makes StaticFiles serve index.html for the directory root, so
# /dashboard/ works rather than needing /dashboard/index.html.
#
# The `if exists` guard matters: mounting a missing directory raises at import
# time and would take the whole API down. A missing dashboard should not stop
# the API serving JSON.

_FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
if _FRONTEND_DIR.is_dir():
    app.mount(
        "/dashboard",
        StaticFiles(directory=str(_FRONTEND_DIR), html=True),
        name="dashboard",
    )


# ---------------------------------------------------------------------------
# RUNNING THE SCANNER FROM INSIDE THE WEB SERVER
# ---------------------------------------------------------------------------

def _run_scan_in_worker_thread(url: str, wait_seconds: int,
                               accept_consent: bool = False) -> dict:
    """
    Run a Playwright scan in its own thread, with its own event loop.

    ⚠ WHY THIS EXISTS — a genuine Windows/asyncio incompatibility.

    THE SYMPTOM
    -----------
    Calling `await scan_website(...)` directly from the endpoint works fine on
    Linux and macOS. On Windows it crashes with a bare, unhelpful:

        NotImplementedError
          File ".../asyncio/base_events.py", line 528,
               in _make_subprocess_transport
            raise NotImplementedError

    THE CAUSE
    ---------
    Windows has TWO different asyncio event loop implementations, and they
    don't support the same things:

        SelectorEventLoop   ❌ CANNOT spawn subprocesses
        ProactorEventLoop   ✅ CAN spawn subprocesses

    Uvicorn selects SelectorEventLoop on Windows (it's better for network
    sockets, which is uvicorn's whole job). But Playwright works by launching
    its Node.js driver **as a subprocess** — so on that loop it cannot start
    at all.

    Neither tool is wrong. They just need different things from the loop, and
    a process only gets one.

    WHY WE CAN'T JUST CHANGE THE POLICY
    -----------------------------------
    `asyncio.set_event_loop_policy(...)` at import time is too late — uvicorn
    has already created its loop by the time our module is imported. And
    forcing uvicorn onto ProactorEventLoop would degrade the server's socket
    handling for everyone, to suit one endpoint.

    THE FIX
    -------
    Give the scan its own thread with its own private ProactorEventLoop. The
    server keeps its Selector loop for sockets; Playwright gets a Proactor loop
    for subprocesses. `asyncio.set_event_loop()` is per-thread, so the two
    never interfere.

    A BONUS, NOT JUST A WORKAROUND
    ------------------------------
    This is genuinely better architecture regardless of platform. The browser
    is heavy, CPU-bursty work; isolating it in a worker thread keeps it well
    away from the loop that serves every other request.

    This is exactly the class of bug you only find by running on the target
    platform. My tests mock the browser, so they never exercised this path —
    a fair reminder that mocking hides integration problems.
    """
    from scan import scan_website

    # ProactorEventLoop exists only on Windows. Everywhere else the default
    # loop already supports subprocesses, so a normal new loop is fine.
    if sys.platform == "win32" and hasattr(asyncio, "ProactorEventLoop"):
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()

    # Bind the loop to THIS thread only. The server's loop is untouched.
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(
            scan_website(url=url, headless=True, settle_seconds=wait_seconds,
                         accept_consent=accept_consent)
        )
    finally:
        # Always clean up: an unclosed loop leaks file descriptors, and a
        # long-running server would eventually exhaust them.
        asyncio.set_event_loop(None)
        loop.close()


# ---------------------------------------------------------------------------
# SYSTEM
# ---------------------------------------------------------------------------

@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["system"],
    summary="Liveness and database check",
)
def health():
    """
    Report whether the service and its database are working.

    Used by Docker's HEALTHCHECK (Phase 6) and by CI. A health endpoint should
    do a little real work — here, an actual query — rather than just returning
    "ok", or it will happily report healthy while the database is unreachable.
    """
    try:
        domains = db.list_domains()
        conn = db.get_connection()
        scans = conn.execute("SELECT COUNT(*) AS n FROM scans").fetchone()["n"]
        conn.close()
        return HealthResponse(
            status="ok", version=API_VERSION, database="connected",
            domains_tracked=len(domains), scans_stored=scans,
            environment=config.ENVIRONMENT,
        )
    except Exception as e:
        # Report the failure honestly rather than pretending to be healthy.
        return HealthResponse(
            status="degraded", version=API_VERSION,
            database=f"error: {type(e).__name__}",
            environment=config.ENVIRONMENT,
        )


@app.get("/", tags=["system"], summary="API root")
def root():
    """A friendly landing response pointing at the docs."""
    return {
        "name": "CookieGuard API",
        "version": API_VERSION,
        "docs": "/docs",
        "health": "/health",
        "dashboard": "/dashboard/",
    }


# ---------------------------------------------------------------------------
# SCANNING
# ---------------------------------------------------------------------------

@app.post(
    "/api/scan",
    response_model=ScanCreatedResponse,
    status_code=201,
    tags=["scanning"],
    summary="Scan a website",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid or unsafe URL"},
        504: {"model": ErrorResponse, "description": "The scan timed out"},
    },
)
async def create_scan(request: ScanRequest):
    """
    Run a fresh scan and (by default) store the result.

    **This takes 5–30 seconds** — we launch a real browser, wait for the page's
    JavaScript to run, and idle so delayed trackers fire. Show a spinner.

    WHY `async def` HERE, BUT PLAIN `def` FOR THE READ ENDPOINTS
    ------------------------------------------------------------
    This is the FastAPI concept most people get wrong, and it's worth knowing.

        async def  → runs ON the event loop.
                     Correct when you `await` something. While awaiting, the
                     loop serves other requests.
                     ⚠ But any BLOCKING call inside freezes the whole server.

        def        → FastAPI runs it in a THREAD POOL, off the event loop.
                     Correct for blocking work (like SQLite queries), because
                     blocking a worker thread doesn't block anyone else.

    So: `async def` here because `scan_website()` is a coroutine we await.
    Plain `def` on the read endpoints because SQLite calls are blocking.

    Getting this backwards — a blocking database call inside `async def` — is
    the classic FastAPI performance bug. The code works fine under one user and
    falls over under load.

    STATUS CODE 201, NOT 200
    ------------------------
    201 Created is the correct response when a request creates a new resource.
    200 means "here's your answer"; 201 means "I made something new". Using the
    right code is what lets any HTTP client understand your API without
    reading your docs.
    """
    url = validate_scan_url(request.url)

    # Imported here, not at the top of the file, ON PURPOSE.
    # `scan.py` imports Playwright, which is heavy and needs browser binaries.
    # A local import means the API still starts, and every read endpoint still
    # works, on a machine where Playwright isn't installed. Only this one
    # endpoint fails, and with a clear message.
    try:
        import scan  # imported purely to prove Playwright is installed
    except ImportError as e:
        # `from e` CHAINS the exceptions. Without it Python prints "During
        # handling of the above exception, another exception occurred" and the
        # original cause is technically still there but reads as unrelated
        # noise. With it, the traceback says "this was DIRECTLY caused by" and
        # names the real problem. One keyword, much better 3am debugging.
        raise HTTPException(
            status_code=503,
            detail=f"Scanner unavailable: {e}. Run: playwright install chromium",
        ) from e

    try:
        # `asyncio.to_thread` runs a blocking function in a worker thread and
        # awaits the result. So the endpoint still yields control — the server
        # keeps serving other requests for the whole 5–30 seconds — while the
        # browser runs somewhere it can't interfere with the event loop.
        #
        # See `_run_scan_in_worker_thread` for why the thread is required on
        # Windows and merely good practice elsewhere.
        result = await asyncio.to_thread(
            _run_scan_in_worker_thread, url, request.wait_seconds,
            request.accept_consent,
        )
    except Exception as e:
        raise HTTPException(
            status_code=504,
            detail=f"Scan failed: {type(e).__name__}: {e}",
        ) from e

    # Classify. `save_scan` would do this too, but we need the classified data
    # even when save=False.
    from classifier import classify_scan, load_trackers
    result = classify_scan(result, load_trackers())

    scan_id = None
    if request.save:
        scan_id = db.save_scan(result)

    return ScanCreatedResponse(
        scan_id=scan_id,
        domain=result.get("domain", "unknown"),
        url=result.get("url", url),
        scanned_at=result.get("scanned_at"),
        duration_seconds=result.get("duration_seconds"),
        cookie_count=result.get("cookie_count", 0),
        categories=result.get("categories", {}),
        compliance=result.get("compliance", {}),
        error=result.get("error"),
        saved=request.save,
        consent_click=result.get("consent_click"),
        consent_diff=result.get("consent_diff"),
    )


# ---------------------------------------------------------------------------
# DOMAINS
# ---------------------------------------------------------------------------

@app.get(
    "/api/domains",
    response_model=List[DomainSummary],
    tags=["domains"],
    summary="List every scanned domain",
)
def list_domains():
    """
    All domains we hold data for, with scan counts and average scores.

    Plain `def`, not `async def` — the database call blocks, so FastAPI should
    run this in a thread. See the note on `create_scan`.
    """
    return db.list_domains()


@app.get(
    "/api/domains/{domain}/scans",
    response_model=List[ScanSummary],
    tags=["domains"],
    summary="Scan history for one domain",
    responses={404: {"model": ErrorResponse, "description": "Domain not found"}},
)
def domain_scans(
    # A PATH PARAMETER — the {domain} in the URL becomes this argument.
    # FastAPI matches by name, converts to the annotated type, and documents it.
    domain: str = PathParam(..., description="e.g. bbc.com"),

    # A QUERY PARAMETER — ?limit=10 in the URL.
    # ge/le bounds are enforced automatically: ?limit=99999 gets a 422 before
    # our code runs. Without a cap, one request could ask for a million rows.
    limit: int = Query(50, ge=1, le=500, description="Maximum scans to return"),
):
    """Scans for `domain`, newest first."""
    scans = db.get_scans_for_domain(domain, limit=limit)
    if not scans:
        # 404 = the resource doesn't exist.
        #
        # A subtle but important distinction: an empty LIST is not a 404. If
        # someone asks for all domains and there are none, that's `[]` with a
        # 200 — the collection exists and happens to be empty. But asking for
        # the history of a domain we've never seen is a request for something
        # that doesn't exist, so 404 is right.
        raise HTTPException(
            status_code=404,
            detail=f"No scans found for domain '{domain}'.",
        )
    return scans


@app.get(
    "/api/domains/{domain}/latest",
    response_model=ScanDetail,
    tags=["domains"],
    summary="Most recent scan for a domain",
    responses={404: {"model": ErrorResponse, "description": "Domain not found"}},
)
def domain_latest(domain: str = PathParam(..., description="e.g. bbc.com")):
    """The newest scan for `domain`, with full cookie detail."""
    scan = db.get_latest_scan(domain)
    if not scan:
        raise HTTPException(
            status_code=404, detail=f"No scans found for domain '{domain}'."
        )
    return scan


# ---------------------------------------------------------------------------
# SCANS
# ---------------------------------------------------------------------------

@app.get(
    "/api/scans/{scan_id}",
    response_model=ScanDetail,
    tags=["scans"],
    summary="One scan in full",
    responses={404: {"model": ErrorResponse, "description": "Scan not found"}},
)
def get_scan(scan_id: int = PathParam(..., ge=1, description="Scan id")):
    """
    A complete scan: metadata, every cookie, every third-party domain.

    `scan_id: int` means FastAPI converts and validates for us. A request to
    `/api/scans/abc` returns 422 automatically — our function is never called
    with a non-integer, so we never have to check.
    """
    scan = db.get_scan(scan_id)
    if not scan:
        raise HTTPException(status_code=404, detail=f"No scan with id {scan_id}.")
    return scan


@app.get(
    "/api/scans/{scan_id}/cookies",
    response_model=List[CookieOut],
    tags=["scans"],
    summary="Cookie inventory for one scan",
    responses={404: {"model": ErrorResponse, "description": "Scan not found"}},
)
def get_scan_cookies(
    scan_id: int = PathParam(..., ge=1),
    category: str = Query(
        None,
        description="Optional filter: necessary|functional|analytics|marketing|unknown",
    ),
):
    """
    Just the cookies, riskiest category first. Optionally filtered.

    A separate endpoint from `/api/scans/{id}` because the dashboard's cookie
    table may want to refilter without re-fetching all the scan metadata.
    """
    scan = db.get_scan(scan_id)
    if not scan:
        raise HTTPException(status_code=404, detail=f"No scan with id {scan_id}.")

    cookies = scan["cookies"]
    if category:
        valid = {"necessary", "functional", "analytics", "marketing", "unknown"}
        if category not in valid:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid category '{category}'. Valid: {', '.join(sorted(valid))}",
            )
        cookies = [c for c in cookies if c.get("category") == category]
    return cookies


@app.delete(
    "/api/scans/{scan_id}",
    status_code=204,
    tags=["scans"],
    summary="Delete a scan",
    responses={404: {"model": ErrorResponse, "description": "Scan not found"}},
)
def delete_scan(scan_id: int = PathParam(..., ge=1)):
    """
    Delete a scan. Its cookies and third-party rows go too, via ON DELETE
    CASCADE in the schema — we issue one statement and the database handles
    the children.

    **204 No Content** is the conventional response to a successful DELETE:
    it worked, and there is deliberately no body to return. Returning
    `{"deleted": true}` with a 200 would be redundant — the status code
    already said that.
    """
    if not db.delete_scan(scan_id):
        raise HTTPException(status_code=404, detail=f"No scan with id {scan_id}.")
    # Returning None produces an empty body, which is what 204 requires.
    return None


# ---------------------------------------------------------------------------
# REPORTS
# ---------------------------------------------------------------------------

@app.get(
    "/api/scans",
    tags=["scans"],
    summary="List scans across all domains",
)
def list_all_scans(
    domain: str = Query(None, description="Filter to one domain"),
    grade: str = Query(None, description="Filter by compliance grade A-F"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0, description="Rows to skip, for paging"),
):
    """
    Scan history across the whole portfolio, newest first.

    Powers the Scan History panel. Returns `{items, total}` rather than a bare
    list, because the UI needs to know how many rows exist beyond the current
    page in order to render "showing 20 of 137".

    `offset` + `limit` is the simplest form of pagination. It's fine at this
    scale; on very large tables it gets slow because the database still has to
    walk past the skipped rows. The alternative, keyset pagination ("give me
    rows after this timestamp"), stays fast but can't jump to an arbitrary
    page. Worth knowing the trade-off exists.
    """
    if grade:
        grade = grade.upper()
        if grade not in {"A", "B", "C", "D", "F"}:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid grade '{grade}'. Valid: A, B, C, D, F",
            )
    return db.list_scans(domain=domain, grade=grade, limit=limit, offset=offset)


@app.get(
    "/api/report/{domain}/pdf",
    tags=["reports"],
    summary="Download the audit report as a PDF",
    responses={
        200: {"content": {"application/pdf": {}},
              "description": "The audit report as a PDF file"},
        404: {"model": ErrorResponse, "description": "Domain not found"},
    },
)
async def domain_report_pdf(domain: str = PathParam(..., description="e.g. bbc.com")):
    """
    Generate a downloadable PDF of the audit report.

    HOW IT WORKS
    ------------
    We build a self-contained HTML document in Python — all data inlined, no
    JavaScript, no external requests — then let Chromium's own print engine
    turn it into a PDF. Playwright was already a dependency for scanning, so
    the capability came free.

    That's worth noticing: a tool bought for one job solved a different one,
    because browser automation is a general capability rather than a
    task-specific library.

    Why not point Playwright at the live dashboard? It fetches its data with
    JavaScript, so the server would be calling itself over HTTP, and the D3
    charts need a CDN. An export that breaks when you're offline is a bad
    export.
    """
    report = db.get_domain_report(domain)
    if not report:
        raise HTTPException(status_code=404, detail=f"No data for domain '{domain}'.")

    from report_pdf import build_report_html, render_pdf_sync

    html_text = build_report_html(report)

    try:
        # Worker thread for the same reason as the scanner — see
        # `_run_scan_in_worker_thread` and TEACHING.md §54.
        pdf_bytes = await asyncio.to_thread(render_pdf_sync, html_text)
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=(f"PDF generation failed: {type(e).__name__}: {e}. "
                    "Is Chromium installed? Run: playwright install chromium"),
        ) from e

    # A safe filename: strip anything that isn't alphanumeric, dot, dash or
    # underscore. A domain can't normally contain path separators, but building
    # a filename from user input without sanitising is a habit worth not having.
    safe = "".join(c for c in domain if c.isalnum() or c in "._-") or "report"
    filename = f"cookieguard-{safe}-{datetime.now(timezone.utc):%Y%m%d}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            # `attachment` makes the browser DOWNLOAD the file rather than
            # display it inline. `inline` would open it in the PDF viewer.
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@app.get(
    "/api/report/{domain}",
    response_model=DomainReport,
    tags=["reports"],
    summary="Compliance report for a domain",
    responses={404: {"model": ErrorResponse, "description": "Domain not found"}},
)
def domain_report(domain: str = PathParam(..., description="e.g. bbc.com")):
    """
    The full audit report: latest result, aggregate stats, most frequent
    vendors, cookies needing review, score history and the trend.

    This is what Phase 4's report page renders, and it's the endpoint that
    justifies having a database at all — none of it is answerable from a
    single scan.
    """
    report = db.get_domain_report(domain)
    if not report:
        raise HTTPException(
            status_code=404, detail=f"No data for domain '{domain}'."
        )
    return report


# ---------------------------------------------------------------------------
# RUNNING DIRECTLY
# ---------------------------------------------------------------------------
# The normal way to start this is:
#
#     uvicorn api.main:app --reload
#
# `api.main:app` means "in the module api.main, use the object called app".
# `--reload` restarts on file changes — for development only; it watches the
# filesystem and costs performance.
#
# This block lets `python api/main.py` work too, which is convenient for
# beginners and for a quick demo.

if __name__ == "__main__":
    import uvicorn
    print("\n[CookieGuard] Starting API on http://127.0.0.1:8000")
    print("[CookieGuard] Interactive docs: http://127.0.0.1:8000/docs\n")
    uvicorn.run(app, host="127.0.0.1", port=8000)
