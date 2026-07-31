"""
CookieGuard — API schemas (Phase 3)
====================================

WHAT THIS FILE DOES
-------------------
It describes the SHAPE of every piece of JSON our API accepts or returns.

Not the logic — just the shape. "A scan request has a `url` which must be a
valid http/https URL, and an optional `wait_seconds` between 1 and 30."

WHAT IS PYDANTIC?
-----------------
A library that turns ordinary Python type hints into **runtime validation**.

Normally type hints are documentation only — Python doesn't check them:

    def greet(name: str): ...
    greet(42)              # Python runs this happily. No error.

Pydantic makes them real. Define a model, and any data you feed it is checked,
converted and rejected if wrong:

    class ScanRequest(BaseModel):
        url: str
        wait_seconds: int

    ScanRequest(url="https://x.com", wait_seconds="5")   # "5" → 5, converted
    ScanRequest(url="https://x.com", wait_seconds="abc") # ValidationError

WHY THIS MATTERS FOR AN API
---------------------------
Data arriving from the internet is untrusted. Someone will send
`wait_seconds: 999999` or `url: null` or no url at all — sometimes by mistake,
sometimes deliberately.

Without validation, that garbage reaches your business logic and fails
somewhere deep and confusing, or worse, doesn't fail at all.

    ┌────────────────────────────────────────────────────────────┐
    │  WITHOUT Pydantic          WITH Pydantic                   │
    │  ────────────────          ─────────────                   │
    │  bad JSON arrives          bad JSON arrives                │
    │       ↓                         ↓                          │
    │  reaches your code         REJECTED AT THE DOOR            │
    │       ↓                         ↓                          │
    │  crashes deep inside,      422 response naming the exact   │
    │  or corrupts data          field and what was wrong        │
    └────────────────────────────────────────────────────────────┘

FastAPI does this automatically. You never write "if url is missing, return an
error" — you declare the model and FastAPI enforces it.

THREE THINGS THESE MODELS GIVE US FOR FREE
------------------------------------------
1. **Validation** — bad input rejected before our code runs
2. **Documentation** — FastAPI reads these models to generate the interactive
   Swagger page at /docs. The docs can never drift from the code, because they
   ARE the code.
3. **Filtering** — a `response_model` acts as a whitelist. Any field not
   declared is stripped from the response, so we can't leak data by accident.
"""

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# REQUEST MODELS — what clients send us
# ---------------------------------------------------------------------------

class ScanRequest(BaseModel):
    """
    The body of `POST /api/scan`.

    `Field(...)` attaches rules and documentation to a single field:
        ...            (Ellipsis) means REQUIRED — no default
        min_length     reject empty strings
        ge / le        greater-or-equal / less-or-equal, for numbers
        description    shown in the Swagger docs
    """

    url: str = Field(
        ...,
        min_length=4,
        max_length=2048,     # browsers cap URLs around here; stops absurd input
        description="The website to scan, e.g. https://example.com",
        examples=["https://example.com"],
    )

    wait_seconds: int = Field(
        default=5,
        ge=1,                # at least 1 second, or delayed trackers are missed
        le=30,               # at most 30 — someone WILL try 999999 otherwise,
                             # tying up a browser and exhausting the server
        description="Seconds to idle after page load, letting delayed trackers fire",
    )

    save: bool = Field(
        default=True,
        description="Store the result in the database (set false for a dry run)",
    )

    # A `field_validator` runs custom logic Pydantic can't express declaratively.
    # `mode="before"` means it runs BEFORE type conversion, so we can clean the
    # raw input first.
    @field_validator("url", mode="before")
    @classmethod
    def add_scheme_if_missing(cls, v):
        """
        Let people type "example.com" and treat it as "https://example.com".

        Being forgiving about INPUT while strict about what we ACCEPT is good
        API design. We're not weakening validation — the URL still has to be
        valid, and main.py still runs security checks on it. We're just not
        making the user remember a prefix.
        """
        if isinstance(v, str):
            v = v.strip()
            if v and not v.startswith(("http://", "https://")):
                v = "https://" + v
        return v


# ---------------------------------------------------------------------------
# RESPONSE MODELS — what we send back
# ---------------------------------------------------------------------------
# `Optional[str]` means "a string OR None". In JSON that's `null`.
# Without Optional, a None value would fail validation on the way OUT — and a
# response model that rejects your own data is a confusing bug to chase.

class CategoryCounts(BaseModel):
    """How many cookies fell into each compliance category."""
    necessary: int = 0
    functional: int = 0
    analytics: int = 0
    marketing: int = 0
    unknown: int = 0


class Deduction(BaseModel):
    """One line of the compliance score's itemised breakdown."""
    reason: str
    count: int
    points: int


class ComplianceSummary(BaseModel):
    """The score block."""
    score: Optional[int] = Field(None, description="0-100, higher is better")
    grade: Optional[str] = Field(None, description="A to F")
    verdict: Optional[str] = None
    deductions: List[Deduction] = []
    cookies_requiring_consent: int = Field(
        0,
        description=(
            "Non-necessary cookies found before any consent was given. "
            "This is the objective legal number, as opposed to the heuristic score."
        ),
    )


class CookieOut(BaseModel):
    """One cookie as the API reports it."""
    name: str
    domain: Optional[str] = None
    path: Optional[str] = None
    party: Optional[str] = Field(None, description="'first' or 'third'")
    cookie_type: Optional[str] = Field(None, description="'session' or 'persistent'")
    expires_at: Optional[str] = None
    lifetime_days: Optional[int] = None
    http_only: Optional[bool] = None
    secure: Optional[bool] = None
    same_site: Optional[str] = None
    value_length: Optional[int] = Field(
        None,
        description="Length only. Cookie VALUES are never stored or returned.",
    )
    category: Optional[str] = None
    vendor: Optional[str] = None
    purpose: Optional[str] = None
    matched_by: Optional[str] = Field(
        None, description="How the classifier decided — keeps results auditable"
    )
    confidence: Optional[str] = None


class ThirdPartyDomainOut(BaseModel):
    """Another company the page contacted."""
    domain: str
    request_count: int = 0
    category: Optional[str] = None
    vendor: Optional[str] = None


class ScanSummary(BaseModel):
    """
    One scan WITHOUT its cookies — for lists and history tables.

    Why a separate model from ScanDetail: a CNN scan has 177 cookies and 186
    third-party domains. Sending all of that for every row of a history table
    would be wasteful and slow. Summary for lists, detail on request.

    That's the standard REST pattern: collections return summaries, individual
    resources return full detail.
    """
    id: int
    domain: Optional[str] = None
    url: Optional[str] = None
    final_url: Optional[str] = None
    page_title: Optional[str] = None
    http_status: Optional[int] = None
    scanned_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    error: Optional[str] = None

    cookie_count: int = 0
    first_party_cookies: int = 0
    third_party_cookies: int = 0
    session_cookies: int = 0
    persistent_cookies: int = 0
    total_requests: int = 0

    necessary_count: int = 0
    functional_count: int = 0
    analytics_count: int = 0
    marketing_count: int = 0
    unknown_count: int = 0

    compliance_score: Optional[int] = None
    compliance_grade: Optional[str] = None
    cookies_requiring_consent: int = 0


class ScanDetail(ScanSummary):
    """
    A full scan WITH its cookies and third-party domains.

    Note it INHERITS from ScanSummary — every field above, plus two more.
    Inheritance here isn't just tidiness: it guarantees the two models can
    never disagree about the shared fields, because there's only one
    definition of them.
    """
    cookies: List[CookieOut] = []
    third_party_domains: List[ThirdPartyDomainOut] = []


class ScanCreatedResponse(BaseModel):
    """Returned by `POST /api/scan` — a compact confirmation."""
    scan_id: Optional[int] = Field(
        None, description="Database id, or null when save=false"
    )
    domain: str
    url: str
    scanned_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    cookie_count: int = 0
    categories: CategoryCounts
    compliance: ComplianceSummary
    error: Optional[str] = Field(
        None,
        description="Set if navigation had a problem. Partial results may still be useful.",
    )
    saved: bool = True


class DomainSummary(BaseModel):
    """One row of `GET /api/domains`."""
    id: int
    domain: str
    first_seen: Optional[str] = None
    last_scanned: Optional[str] = None
    scan_count: int = 0
    latest_scan: Optional[str] = None
    avg_score: Optional[float] = None
    max_cookies: Optional[int] = None


class DomainStats(BaseModel):
    """Aggregates across every scan of one domain."""
    total_scans: int = 0
    first_scan: Optional[str] = None
    latest_scan: Optional[str] = None
    avg_score: Optional[float] = None
    worst_score: Optional[int] = None
    best_score: Optional[int] = None
    avg_cookies: Optional[float] = None


class VendorSummary(BaseModel):
    """How often one vendor's cookies appear on a domain."""
    vendor: Optional[str] = None
    category: Optional[str] = None
    occurrences: int = 0
    scans_seen_in: int = 0


class UnknownCookie(BaseModel):
    """A cookie no signature matched — needs a human."""
    name: str
    domain: Optional[str] = None
    party: Optional[str] = None


class HistoryPoint(BaseModel):
    """One point on the score trend line."""
    scanned_at: Optional[str] = None
    compliance_score: Optional[int] = None
    compliance_grade: Optional[str] = None
    cookie_count: int = 0


class DomainReport(BaseModel):
    """The full audit report — `GET /api/report/{domain}`."""
    domain: str
    first_seen: Optional[str] = None
    last_scanned: Optional[str] = None
    stats: DomainStats
    latest_scan: Optional[ScanSummary] = None
    top_vendors: List[VendorSummary] = []
    unknown_cookies: List[UnknownCookie] = []
    history: List[HistoryPoint] = []
    trend: str = Field(
        "insufficient data",
        description="improving | worsening | stable | insufficient data",
    )


class HealthResponse(BaseModel):
    """`GET /health` — used by Docker healthchecks and CI."""
    status: str = "ok"
    version: str
    database: str = Field(description="'connected' or an error description")
    domains_tracked: int = 0
    scans_stored: int = 0


class ErrorResponse(BaseModel):
    """
    A consistent error shape.

    Every error the API returns looks the same, so a frontend can handle them
    all with one code path instead of guessing at the format each time.
    """
    detail: str = Field(description="Human-readable explanation of what failed")
