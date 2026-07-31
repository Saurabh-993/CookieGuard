"""
Tests for api/main.py
======================

HOW YOU TEST AN API WITHOUT STARTING A SERVER
---------------------------------------------
FastAPI ships a `TestClient`. It sends real HTTP requests through the app
IN-PROCESS — no server to start, no port to bind, no network involved.

    client.get("/health")           # goes straight into the app
    response.status_code            # a real HTTP status code
    response.json()                 # the real parsed response body

That makes API tests as fast as unit tests while still exercising the genuine
request path: routing, validation, serialisation, status codes and all.

THE ISOLATION PROBLEM, AND WHY IT MATTERS HERE
----------------------------------------------
`api/main.py` calls `db.list_domains()` with no arguments, so it uses
`db.DEFAULT_DB_PATH` — the developer's REAL database. Without intervention,
running this suite would read and write actual audit data.

We point `db.DEFAULT_DB_PATH` at a throwaway file for the duration of each
test. That only works because `db.py` resolves the path at CALL time via
`_resolve_db()` rather than binding it as a default argument at import time —
see the comment on that function. It's a good illustration of how a subtle
Python detail decides whether code is testable at all.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "api"))
sys.path.insert(0, str(Path(__file__).parent.parent / "scanner"))

from fastapi.testclient import TestClient  # noqa: E402

import db  # noqa: E402
from main import app, validate_scan_url  # noqa: E402
from fastapi import HTTPException  # noqa: E402


# ---------------------------------------------------------------------------
# FIXTURES
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    """
    A TestClient wired to a fresh, empty database.

    `monkeypatch` is pytest's built-in tool for temporarily changing something
    and AUTOMATICALLY putting it back afterwards. If we assigned
    `db.DEFAULT_DB_PATH = ...` by hand, the change would leak into every later
    test in the session — and probably into the developer's real database.
    """
    test_db = tmp_path / "api_test.db"
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", test_db)
    db.init_db(test_db)
    return TestClient(app)


def make_scan(domain="example.com", scanned_at="2026-07-01T10:00:00+00:00",
              score=75, grade="B"):
    """A minimal classified scan we can store directly."""
    return {
        "url": f"https://{domain}", "final_url": f"https://{domain}/",
        "domain": domain, "page_title": "Test", "http_status": 200,
        "scanned_at": scanned_at, "duration_seconds": 2.5, "error": None,
        "cookie_count": 2, "first_party_cookies": 2, "third_party_cookies": 0,
        "session_cookies": 1, "persistent_cookies": 1, "total_requests": 10,
        "cookies": [
            {"name": "PHPSESSID", "domain": domain, "path": "/",
             "party": "first", "type": "session", "expires_at": None,
             "lifetime_days": None, "http_only": True, "secure": True,
             "same_site": "Lax", "value_length": 26, "category": "necessary",
             "vendor": "PHP", "purpose": "Session.",
             "matched_by": "exact", "confidence": "high"},
            {"name": "_fbp", "domain": domain, "path": "/",
             "party": "first", "type": "persistent", "expires_at": None,
             "lifetime_days": 90, "http_only": False, "secure": True,
             "same_site": "Lax", "value_length": 22, "category": "marketing",
             "vendor": "Meta", "purpose": "Ad targeting.",
             "matched_by": "exact", "confidence": "high"},
        ],
        "third_party_domains": [
            {"domain": "facebook.net", "request_count": 2,
             "category": "marketing", "vendor": "Meta"},
        ],
        "categories": {"necessary": 1, "functional": 0, "analytics": 0,
                       "marketing": 1, "unknown": 0},
        "compliance": {"score": score, "grade": grade, "verdict": "Test",
                       "deductions": [], "cookies_requiring_consent": 1},
    }


# ---------------------------------------------------------------------------
# 1. SYSTEM ENDPOINTS
# ---------------------------------------------------------------------------

def test_root_returns_api_info(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["docs"] == "/docs"


def test_health_reports_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"] == "connected"


def test_health_counts_reflect_data(client):
    """
    A health check should do real work, not just return "ok". These counts
    come from an actual query, so the endpoint fails honestly if the database
    is unreachable.
    """
    assert client.get("/health").json()["domains_tracked"] == 0
    db.save_scan(make_scan())
    assert client.get("/health").json()["domains_tracked"] == 1


def test_openapi_schema_is_generated(client):
    """
    FastAPI builds the whole OpenAPI spec from our type hints. If this breaks,
    the /docs page breaks — and the docs are a big part of the demo.
    """
    r = client.get("/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    assert spec["info"]["title"] == "CookieGuard API"
    assert "/api/scan" in spec["paths"]
    assert "/api/domains" in spec["paths"]


def test_docs_page_loads(client):
    assert client.get("/docs").status_code == 200


# ---------------------------------------------------------------------------
# 2. SSRF PROTECTION — the security tests
# ---------------------------------------------------------------------------
# Each of these is an attack the API must refuse. They matter most in Phase 7,
# when this runs on EC2 with an IAM role attached.

def test_blocks_cloud_metadata_endpoint(client):
    """
    THE MOST IMPORTANT SECURITY TEST IN THE PROJECT.

    169.254.169.254 is the cloud instance metadata service. On EC2 it returns
    temporary IAM credentials for the instance's role. An unguarded
    URL-fetching feature hands an attacker our AWS permissions — essentially
    the 2019 Capital One breach.
    """
    r = client.post("/api/scan", json={"url": "http://169.254.169.254/latest/meta-data/"})
    assert r.status_code == 400
    assert "metadata" in r.json()["detail"].lower()


def test_blocks_localhost(client):
    r = client.post("/api/scan", json={"url": "http://localhost:8000/admin"})
    assert r.status_code == 400


def test_blocks_loopback_ip(client):
    r = client.post("/api/scan", json={"url": "http://127.0.0.1/"})
    assert r.status_code == 400


def test_blocks_private_network_ranges(client):
    """All three RFC1918 private ranges must be refused."""
    for ip in ("http://192.168.1.1/", "http://10.0.0.1/", "http://172.16.0.1/"):
        r = client.post("/api/scan", json={"url": ip})
        assert r.status_code == 400, f"{ip} was not blocked"


def test_blocks_dot_local_hostnames(client):
    r = client.post("/api/scan", json={"url": "http://internal-admin.local/"})
    assert r.status_code == 400


def test_blocks_non_http_schemes(client):
    """file:// would let someone read files off the server's disk."""
    for url in ("file:///etc/passwd", "ftp://example.com/", "gopher://example.com/"):
        with pytest.raises(HTTPException) as exc:
            validate_scan_url(url)
        assert exc.value.status_code == 400


def test_allows_normal_public_url():
    """The guard must not block legitimate targets."""
    assert validate_scan_url("https://example.com") == "https://example.com"


# ---------------------------------------------------------------------------
# 3. REQUEST VALIDATION (Pydantic)
# ---------------------------------------------------------------------------
# 422 Unprocessable Entity is FastAPI's response when a request doesn't match
# the schema. We never write this handling — declaring the model is enough.

def test_missing_url_is_rejected(client):
    r = client.post("/api/scan", json={})
    assert r.status_code == 422


def test_wait_seconds_upper_bound_enforced(client):
    """Without a cap, someone could tie up a browser for 999999 seconds."""
    r = client.post("/api/scan", json={"url": "https://example.com",
                                       "wait_seconds": 999999})
    assert r.status_code == 422


def test_wait_seconds_lower_bound_enforced(client):
    r = client.post("/api/scan", json={"url": "https://example.com",
                                       "wait_seconds": 0})
    assert r.status_code == 422


def test_wait_seconds_type_is_validated(client):
    r = client.post("/api/scan", json={"url": "https://example.com",
                                       "wait_seconds": "not-a-number"})
    assert r.status_code == 422


def test_validation_error_names_the_bad_field(client):
    """A good error tells the client exactly what to fix."""
    r = client.post("/api/scan", json={"url": "https://example.com",
                                       "wait_seconds": 999999})
    assert "wait_seconds" in str(r.json())


def test_bare_domain_gets_https_prefix():
    """The `mode="before"` validator should be forgiving about input."""
    from schemas import ScanRequest
    assert ScanRequest(url="example.com").url == "https://example.com"
    assert ScanRequest(url="  example.com  ").url == "https://example.com"
    # An explicit scheme must be preserved, not overwritten.
    assert ScanRequest(url="http://example.com").url == "http://example.com"


def test_scan_id_must_be_an_integer(client):
    """`scan_id: int` in the path means FastAPI rejects non-integers for us."""
    assert client.get("/api/scans/not-a-number").status_code == 422


def test_scan_id_must_be_positive(client):
    """ge=1 in the path parameter rules out 0 and negatives."""
    assert client.get("/api/scans/0").status_code == 422


def test_limit_is_capped(client):
    db.save_scan(make_scan())
    assert client.get("/api/domains/example.com/scans?limit=99999").status_code == 422


# ---------------------------------------------------------------------------
# 4. READ ENDPOINTS
# ---------------------------------------------------------------------------

def test_domains_empty_list_is_200_not_404(client):
    """
    An empty COLLECTION is not an error. The collection exists; it happens to
    have nothing in it. 200 with `[]` is correct — 404 would wrongly suggest
    the endpoint doesn't exist.
    """
    r = client.get("/api/domains")
    assert r.status_code == 200
    assert r.json() == []


def test_domains_lists_saved_scans(client):
    db.save_scan(make_scan(domain="a.com"))
    db.save_scan(make_scan(domain="b.com"))
    body = client.get("/api/domains").json()
    assert {d["domain"] for d in body} == {"a.com", "b.com"}


def test_domain_scans_returns_history(client):
    db.save_scan(make_scan(scanned_at="2026-07-01T10:00:00+00:00"))
    db.save_scan(make_scan(scanned_at="2026-07-05T10:00:00+00:00"))
    body = client.get("/api/domains/example.com/scans").json()
    assert len(body) == 2
    # Newest first.
    assert body[0]["scanned_at"] > body[1]["scanned_at"]


def test_unknown_domain_history_is_404(client):
    """
    A MISSING RESOURCE is a 404 — unlike an empty collection. Asking for the
    history of a domain we've never seen is asking for something that doesn't
    exist.
    """
    r = client.get("/api/domains/never-seen.com/scans")
    assert r.status_code == 404
    assert "never-seen.com" in r.json()["detail"]


def test_get_scan_returns_full_detail(client):
    scan_id = db.save_scan(make_scan())
    body = client.get(f"/api/scans/{scan_id}").json()
    assert body["id"] == scan_id
    assert body["domain"] == "example.com"
    assert len(body["cookies"]) == 2
    assert len(body["third_party_domains"]) == 1


def test_missing_scan_is_404(client):
    assert client.get("/api/scans/9999").status_code == 404


def test_scan_cookies_endpoint(client):
    scan_id = db.save_scan(make_scan())
    body = client.get(f"/api/scans/{scan_id}/cookies").json()
    assert len(body) == 2
    # Riskiest category first, per the ORDER BY CASE in db.py.
    assert body[0]["category"] == "marketing"


def test_scan_cookies_category_filter(client):
    scan_id = db.save_scan(make_scan())
    body = client.get(f"/api/scans/{scan_id}/cookies?category=necessary").json()
    assert len(body) == 1
    assert body[0]["name"] == "PHPSESSID"


def test_invalid_category_filter_is_400(client):
    scan_id = db.save_scan(make_scan())
    r = client.get(f"/api/scans/{scan_id}/cookies?category=nonsense")
    assert r.status_code == 400


def test_latest_scan_endpoint(client):
    db.save_scan(make_scan(scanned_at="2026-07-01T10:00:00+00:00", score=30))
    db.save_scan(make_scan(scanned_at="2026-07-09T10:00:00+00:00", score=90))
    body = client.get("/api/domains/example.com/latest").json()
    assert body["compliance_score"] == 90


# ---------------------------------------------------------------------------
# 5. REPORTS
# ---------------------------------------------------------------------------

def test_report_returns_full_structure(client):
    db.save_scan(make_scan(scanned_at="2026-07-01T10:00:00+00:00", score=30))
    db.save_scan(make_scan(scanned_at="2026-07-15T10:00:00+00:00", score=85))
    body = client.get("/api/report/example.com").json()
    assert body["domain"] == "example.com"
    assert body["stats"]["total_scans"] == 2
    assert body["trend"] == "improving"
    assert len(body["history"]) == 2
    assert any(v["vendor"] == "Meta" for v in body["top_vendors"])


def test_report_for_unknown_domain_is_404(client):
    assert client.get("/api/report/nope.com").status_code == 404


# ---------------------------------------------------------------------------
# 6. DELETE
# ---------------------------------------------------------------------------

def test_delete_scan_returns_204(client):
    """
    204 No Content is the conventional success response to DELETE: it worked,
    and there is deliberately nothing to return.
    """
    scan_id = db.save_scan(make_scan())
    r = client.delete(f"/api/scans/{scan_id}")
    assert r.status_code == 204
    assert r.content == b""            # genuinely empty body


def test_delete_actually_removes_the_scan(client):
    scan_id = db.save_scan(make_scan())
    client.delete(f"/api/scans/{scan_id}")
    assert client.get(f"/api/scans/{scan_id}").status_code == 404


def test_delete_missing_scan_is_404(client):
    assert client.delete("/api/scans/9999").status_code == 404


# ---------------------------------------------------------------------------
# 7. THE SCAN ENDPOINT (with the browser mocked out)
# ---------------------------------------------------------------------------
# We do NOT launch a real browser in tests. It would be slow, need network
# access, and give different results every run — three things a test must not
# be. We replace `scan_website` with a fake that returns fixed data, which lets
# us test OUR logic: validation, classification, storage and response shape.

@pytest.fixture
def mock_scanner(monkeypatch):
    """Replace scan.scan_website with an instant fake."""
    async def fake_scan_website(url, headless=True, settle_seconds=5):
        return {
            "url": url, "final_url": url, "domain": "example.com",
            "page_title": "Fake", "http_status": 200,
            "scanned_at": "2026-07-31T12:00:00+00:00",
            "duration_seconds": 0.01, "error": None,
            "cookies": [{
                "name": "_ga", "domain": ".example.com", "path": "/",
                "party": "first", "type": "persistent", "expires_at": None,
                "lifetime_days": 730, "http_only": False, "secure": True,
                "same_site": "Lax", "value_length": 27,
            }],
            "cookie_count": 1, "first_party_cookies": 1,
            "third_party_cookies": 0, "session_cookies": 0,
            "persistent_cookies": 1, "total_requests": 5,
            "third_party_domains": [], "requests": [],
        }

    import scan
    monkeypatch.setattr(scan, "scan_website", fake_scan_website)
    return fake_scan_website


def test_scan_returns_201_created(client, mock_scanner):
    """
    201 Created, not 200 OK — the request created a new resource. Using the
    right status code is what lets any HTTP client understand the API without
    reading prose documentation.
    """
    r = client.post("/api/scan", json={"url": "https://example.com"})
    assert r.status_code == 201


def test_scan_classifies_before_returning(client, mock_scanner):
    """The scanner returns a raw cookie; the API must classify it."""
    body = client.post("/api/scan", json={"url": "https://example.com"}).json()
    assert body["categories"]["analytics"] == 1
    assert body["compliance"]["score"] is not None


def test_scan_persists_by_default(client, mock_scanner):
    body = client.post("/api/scan", json={"url": "https://example.com"}).json()
    assert body["saved"] is True
    assert body["scan_id"] is not None
    assert client.get(f"/api/scans/{body['scan_id']}").status_code == 200


def test_scan_dry_run_does_not_persist(client, mock_scanner):
    """save=false must return results without writing to the database."""
    body = client.post("/api/scan",
                       json={"url": "https://example.com", "save": False}).json()
    assert body["saved"] is False
    assert body["scan_id"] is None
    assert client.get("/api/domains").json() == []


def test_scan_failure_returns_504(client, monkeypatch):
    """A crash inside the scanner must become a clean HTTP error, not a 500."""
    async def boom(url, headless=True, settle_seconds=5):
        raise RuntimeError("browser exploded")

    import scan
    monkeypatch.setattr(scan, "scan_website", boom)
    r = client.post("/api/scan", json={"url": "https://example.com"})
    assert r.status_code == 504
    assert "browser exploded" in r.json()["detail"]


def test_response_model_strips_undeclared_fields(client, mock_scanner):
    """
    A `response_model` acts as a whitelist: anything not declared in the schema
    is removed from the response. That's a safety net against accidentally
    leaking internal fields — here, the full raw `requests` list.
    """
    body = client.post("/api/scan", json={"url": "https://example.com"}).json()
    assert "requests" not in body
    assert set(body.keys()) <= {
        "scan_id", "domain", "url", "scanned_at", "duration_seconds",
        "cookie_count", "categories", "compliance", "error", "saved",
    }


# ---------------------------------------------------------------------------
# 8. CORS
# ---------------------------------------------------------------------------

def test_cors_header_is_present(client):
    """
    Without this header the browser blocks the dashboard's fetch() calls,
    because a file:// page or a different port is a different ORIGIN.
    """
    r = client.get("/api/domains", headers={"Origin": "http://localhost:3000"})
    assert r.headers.get("access-control-allow-origin") == "*"


def test_cors_preflight_is_answered(client):
    """
    Before certain cross-origin requests the browser sends an OPTIONS
    "preflight" asking permission. If the API doesn't answer it, the real
    request is never sent at all.
    """
    r = client.options(
        "/api/scan",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert r.status_code in (200, 204)
    assert "access-control-allow-origin" in r.headers


# ---------------------------------------------------------------------------
# 9. ISOLATION
# ---------------------------------------------------------------------------

def test_client_fixture_uses_a_throwaway_database(client):
    """
    Proof the isolation works. If monkeypatching DEFAULT_DB_PATH failed, this
    would see the developer's real domains — and every test above would be
    meaningless.
    """
    assert client.get("/api/domains").json() == []
