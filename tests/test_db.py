"""
Tests for api/db.py
====================

THE KEY TECHNIQUE: A THROWAWAY DATABASE PER TEST
------------------------------------------------
Tests must never touch the real `data/cookieguard.db`. If they did, running the
suite would pollute your actual audit history, and tests would pass or fail
depending on what happened to be in there — which makes them useless.

pytest gives us `tmp_path`: a fresh empty directory, unique to each test,
deleted afterwards. We put a brand-new database in it. Every test therefore
starts from a guaranteed-empty, known state.

That property has a name: tests should be **isolated** and **repeatable**.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "api"))
sys.path.insert(0, str(Path(__file__).parent.parent / "scanner"))

from db import (
    delete_scan,
    get_connection,
    get_domain_report,
    get_latest_scan,
    get_or_create_domain,
    get_scan,
    get_scans_for_domain,
    init_db,
    list_domains,
    save_scan,
)

# ---------------------------------------------------------------------------
# FIXTURES
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    """
    A fresh, empty, initialised database for one test.

    No `scope=` argument means the default, "function" scope: this runs again
    for every single test. That's what we want here — unlike the read-only
    trackers.json fixture, each test WRITES, so they must not share state.
    """
    path = tmp_path / "test.db"
    init_db(path)
    return path


def make_scan(domain="example.com", scanned_at="2026-07-01T10:00:00+00:00",
              cookies=None, score=75, grade="B"):
    """Build a classified scan result for testing."""
    cookies = cookies if cookies is not None else [
        {
            "name": "PHPSESSID", "domain": "example.com", "path": "/",
            "party": "first", "type": "session", "expires_at": None,
            "lifetime_days": None, "http_only": True, "secure": True,
            "same_site": "Lax", "value_length": 26,
            "category": "necessary", "vendor": "PHP",
            "purpose": "Session identifier.",
            "matched_by": "exact name 'PHPSESSID'", "confidence": "high",
        },
        {
            "name": "_ga", "domain": ".example.com", "path": "/",
            "party": "first", "type": "persistent",
            "expires_at": "2028-07-01T10:00:00+00:00", "lifetime_days": 730,
            "http_only": False, "secure": True, "same_site": "Lax",
            "value_length": 27,
            "category": "analytics", "vendor": "Google Analytics",
            "purpose": "Visitor ID.",
            "matched_by": "exact name '_ga'", "confidence": "high",
        },
    ]
    counts = {"necessary": 0, "functional": 0, "analytics": 0,
              "marketing": 0, "unknown": 0}
    for c in cookies:
        counts[c["category"]] = counts.get(c["category"], 0) + 1

    return {
        "url": f"https://{domain}", "final_url": f"https://{domain}/",
        "domain": domain, "page_title": "Test Page", "http_status": 200,
        "scanned_at": scanned_at, "duration_seconds": 3.1, "error": None,
        "cookies": cookies, "cookie_count": len(cookies),
        "first_party_cookies": sum(1 for c in cookies if c["party"] == "first"),
        "third_party_cookies": sum(1 for c in cookies if c["party"] == "third"),
        "session_cookies": sum(1 for c in cookies if c["type"] == "session"),
        "persistent_cookies": sum(1 for c in cookies if c["type"] == "persistent"),
        "total_requests": 20,
        "third_party_domains": [
            {"domain": "google-analytics.com", "request_count": 3,
             "category": "analytics", "vendor": "Google Analytics"},
        ],
        "categories": counts,
        "compliance": {
            "score": score, "grade": grade, "verdict": "Test",
            "deductions": [],
            "cookies_requiring_consent": sum(
                1 for c in cookies if c["category"] != "necessary"),
        },
    }


# ---------------------------------------------------------------------------
# 1. SCHEMA
# ---------------------------------------------------------------------------

def test_init_creates_all_tables(db):
    conn = get_connection(db)
    # sqlite_master is SQLite's internal catalogue of everything in the file.
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    names = {r["name"] for r in rows}
    conn.close()
    assert {"domains", "scans", "cookies", "third_party_domains"} <= names


def test_init_creates_indexes(db):
    conn = get_connection(db)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()
    names = {r["name"] for r in rows}
    conn.close()
    assert "idx_scans_domain_id" in names
    assert "idx_cookies_scan_id" in names
    assert "idx_cookies_category" in names


def test_init_is_idempotent(db):
    """Running init twice must not error or duplicate anything."""
    init_db(db)
    init_db(db)
    conn = get_connection(db)
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table'"
    ).fetchone()["n"]
    conn.close()
    assert n >= 4


def test_row_factory_gives_named_access(db):
    """Rows must be accessible by column name, not just position."""
    save_scan(make_scan(), db)
    conn = get_connection(db)
    row = conn.execute("SELECT domain FROM domains LIMIT 1").fetchone()
    conn.close()
    assert row["domain"] == "example.com"   # by name, not row[0]


# ---------------------------------------------------------------------------
# 2. WRITING
# ---------------------------------------------------------------------------

def test_save_scan_returns_id(db):
    scan_id = save_scan(make_scan(), db)
    assert isinstance(scan_id, int) and scan_id > 0


def test_save_scan_writes_all_tables(db):
    scan_id = save_scan(make_scan(), db)
    saved = get_scan(scan_id, db)
    assert saved["domain"] == "example.com"
    assert saved["cookie_count"] == 2
    assert len(saved["cookies"]) == 2
    assert len(saved["third_party_domains"]) == 1


def test_category_counts_are_stored(db):
    """The denormalised count columns must match the actual cookie rows."""
    scan_id = save_scan(make_scan(), db)
    saved = get_scan(scan_id, db)
    assert saved["necessary_count"] == 1
    assert saved["analytics_count"] == 1
    assert saved["marketing_count"] == 0
    # ...and they agree with counting the real rows.
    actual = {}
    for c in saved["cookies"]:
        actual[c["category"]] = actual.get(c["category"], 0) + 1
    assert actual["necessary"] == saved["necessary_count"]
    assert actual["analytics"] == saved["analytics_count"]


def test_domain_is_reused_not_duplicated(db):
    """
    Two scans of the same site must produce ONE domain row and TWO scan rows.
    This is the whole point of splitting the tables — the domain name is
    stored once, not repeated on every scan.
    """
    save_scan(make_scan(domain="example.com",
                        scanned_at="2026-07-01T10:00:00+00:00"), db)
    save_scan(make_scan(domain="example.com",
                        scanned_at="2026-07-02T10:00:00+00:00"), db)

    conn = get_connection(db)
    n_domains = conn.execute("SELECT COUNT(*) AS n FROM domains").fetchone()["n"]
    n_scans = conn.execute("SELECT COUNT(*) AS n FROM scans").fetchone()["n"]
    conn.close()

    assert n_domains == 1
    assert n_scans == 2


def test_unique_constraint_blocks_duplicate_domains(db):
    """The UNIQUE constraint must be enforced by the database itself."""
    conn = get_connection(db)
    conn.execute(
        "INSERT INTO domains (domain, first_seen) VALUES (?, ?)",
        ("dup.com", "2026-07-01T00:00:00+00:00"),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO domains (domain, first_seen) VALUES (?, ?)",
            ("dup.com", "2026-07-02T00:00:00+00:00"),
        )
        conn.commit()
    conn.close()


def test_get_or_create_domain_is_stable(db):
    """Calling it twice with the same name returns the same id."""
    conn = get_connection(db)
    a = get_or_create_domain(conn, "stable.com")
    b = get_or_create_domain(conn, "stable.com")
    conn.commit()
    conn.close()
    assert a == b


def test_last_scanned_is_updated(db):
    save_scan(make_scan(scanned_at="2026-07-01T10:00:00+00:00"), db)
    save_scan(make_scan(scanned_at="2026-07-05T10:00:00+00:00"), db)
    conn = get_connection(db)
    row = conn.execute("SELECT last_scanned FROM domains").fetchone()
    conn.close()
    assert row["last_scanned"].startswith("2026-07-05")


def test_unclassified_scan_is_classified_on_save(db):
    """
    A raw scan.py result (no 'compliance' key) must be classified before
    storage, so unclassified data can never enter the database.
    """
    raw = {
        "url": "https://raw.com", "domain": "raw.com",
        "scanned_at": "2026-07-01T10:00:00+00:00", "cookie_count": 1,
        "cookies": [{
            "name": "_fbp", "domain": ".raw.com", "path": "/", "party": "first",
            "type": "persistent", "expires_at": None, "lifetime_days": 90,
            "http_only": False, "secure": True, "same_site": "Lax",
            "value_length": 22,
        }],
        "third_party_domains": [],
    }
    scan_id = save_scan(raw, db)
    saved = get_scan(scan_id, db)
    assert saved["cookies"][0]["category"] == "marketing"
    assert saved["cookies"][0]["vendor"].startswith("Meta")
    assert saved["compliance_score"] is not None


def test_booleans_stored_as_integers(db):
    """SQLite has no BOOLEAN; True/False must land as 1/0."""
    scan_id = save_scan(make_scan(), db)
    conn = get_connection(db)
    row = conn.execute(
        "SELECT http_only, secure FROM cookies WHERE name = 'PHPSESSID' "
        "AND scan_id = ?", (scan_id,)
    ).fetchone()
    conn.close()
    assert row["http_only"] == 1
    assert row["secure"] == 1


# ---------------------------------------------------------------------------
# 3. FOREIGN KEYS AND CASCADE
# ---------------------------------------------------------------------------

def test_foreign_keys_are_enabled(db):
    """
    SQLite disables foreign key enforcement BY DEFAULT. get_connection() must
    turn it on via PRAGMA, or every REFERENCES clause is silently ignored.
    This test exists because that failure is completely invisible otherwise.
    """
    conn = get_connection(db)
    on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.close()
    assert on == 1


def test_cannot_insert_scan_for_missing_domain(db):
    """Referential integrity: a scan must point at a real domain."""
    conn = get_connection(db)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO scans (domain_id, url, scanned_at, cookie_count) "
            "VALUES (?, ?, ?, ?)",
            (99999, "https://ghost.com", "2026-07-01T00:00:00+00:00", 0),
        )
        conn.commit()
    conn.close()


def test_deleting_scan_cascades_to_cookies(db):
    """
    ON DELETE CASCADE: removing a scan must remove its cookies automatically.
    We issue ONE delete; the database handles the children.
    """
    scan_id = save_scan(make_scan(), db)
    conn = get_connection(db)
    before = conn.execute(
        "SELECT COUNT(*) AS n FROM cookies WHERE scan_id = ?", (scan_id,)
    ).fetchone()["n"]
    conn.close()
    assert before == 2

    assert delete_scan(scan_id, db) is True

    conn = get_connection(db)
    after = conn.execute(
        "SELECT COUNT(*) AS n FROM cookies WHERE scan_id = ?", (scan_id,)
    ).fetchone()["n"]
    tpd = conn.execute(
        "SELECT COUNT(*) AS n FROM third_party_domains WHERE scan_id = ?",
        (scan_id,)
    ).fetchone()["n"]
    conn.close()
    assert after == 0
    assert tpd == 0


def test_delete_missing_scan_returns_false(db):
    assert delete_scan(4242, db) is False


# ---------------------------------------------------------------------------
# 4. SQL INJECTION
# ---------------------------------------------------------------------------

def test_sql_injection_in_domain_name_is_harmless(db):
    """
    THE SECURITY TEST.

    We feed a classic SQL injection payload in as a domain name. Because every
    query uses `?` placeholders, the database treats it as pure DATA, never as
    a command. The tables must survive.

    Had we built SQL with f-strings, this input would have ended the SELECT
    statement and run DROP TABLE as a second command.
    """
    evil = "evil.com'; DROP TABLE domains; --"
    save_scan(make_scan(domain=evil), db)

    conn = get_connection(db)
    # The table still exists...
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    # ...and the payload was stored verbatim, as a harmless string.
    row = conn.execute(
        "SELECT domain FROM domains WHERE domain = ?", (evil,)).fetchone()
    conn.close()

    assert "domains" in tables
    assert row is not None
    assert row["domain"] == evil


def test_sql_injection_in_cookie_name_is_harmless(db):
    evil_cookie = "x'; DELETE FROM cookies; --"
    scan = make_scan(cookies=[{
        "name": evil_cookie, "domain": "example.com", "path": "/",
        "party": "first", "type": "session", "expires_at": None,
        "lifetime_days": None, "http_only": False, "secure": False,
        "same_site": "Lax", "value_length": 5,
        "category": "unknown", "vendor": "Unknown", "purpose": "-",
        "matched_by": "no match", "confidence": "none",
    }])
    scan_id = save_scan(scan, db)
    saved = get_scan(scan_id, db)
    assert len(saved["cookies"]) == 1
    assert saved["cookies"][0]["name"] == evil_cookie


# ---------------------------------------------------------------------------
# 5. READING
# ---------------------------------------------------------------------------

def test_history_is_newest_first(db):
    for day in ("01", "03", "02"):
        save_scan(make_scan(scanned_at=f"2026-07-{day}T10:00:00+00:00"), db)
    scans = get_scans_for_domain("example.com", db_path=db)
    dates = [s["scanned_at"][:10] for s in scans]
    assert dates == ["2026-07-03", "2026-07-02", "2026-07-01"]


def test_history_respects_limit(db):
    for day in range(1, 6):
        save_scan(make_scan(scanned_at=f"2026-07-{day:02d}T10:00:00+00:00"), db)
    assert len(get_scans_for_domain("example.com", limit=2, db_path=db)) == 2


def test_get_latest_scan(db):
    save_scan(make_scan(scanned_at="2026-07-01T10:00:00+00:00", score=40), db)
    save_scan(make_scan(scanned_at="2026-07-09T10:00:00+00:00", score=90), db)
    latest = get_latest_scan("example.com", db)
    assert latest["compliance_score"] == 90


def test_list_domains_aggregates(db):
    save_scan(make_scan(domain="a.com", score=80), db)
    save_scan(make_scan(domain="a.com", score=60,
                        scanned_at="2026-07-02T10:00:00+00:00"), db)
    save_scan(make_scan(domain="b.com", score=100), db)

    rows = {d["domain"]: d for d in list_domains(db)}
    assert rows["a.com"]["scan_count"] == 2
    assert rows["a.com"]["avg_score"] == 70      # (80 + 60) / 2
    assert rows["b.com"]["scan_count"] == 1


def test_list_domains_on_empty_db(db):
    """An empty database must return an empty list, not crash."""
    assert list_domains(db) == []


def test_get_scan_returns_none_when_missing(db):
    assert get_scan(9999, db) is None


def test_cookies_sorted_riskiest_first(db):
    """The ORDER BY CASE clause must put marketing at the top."""
    scan = make_scan(cookies=[
        {"name": "sess", "domain": "e.com", "path": "/", "party": "first",
         "type": "session", "expires_at": None, "lifetime_days": None,
         "http_only": True, "secure": True, "same_site": "Lax",
         "value_length": 8, "category": "necessary", "vendor": "Generic",
         "purpose": "-", "matched_by": "-", "confidence": "low"},
        {"name": "_fbp", "domain": "e.com", "path": "/", "party": "first",
         "type": "persistent", "expires_at": None, "lifetime_days": 90,
         "http_only": False, "secure": True, "same_site": "Lax",
         "value_length": 8, "category": "marketing", "vendor": "Meta",
         "purpose": "-", "matched_by": "-", "confidence": "high"},
    ])
    scan_id = save_scan(scan, db)
    cookies = get_scan(scan_id, db)["cookies"]
    assert cookies[0]["category"] == "marketing"


# ---------------------------------------------------------------------------
# 6. DOMAIN REPORT
# ---------------------------------------------------------------------------

def test_report_returns_none_for_unknown_domain(db):
    assert get_domain_report("never-scanned.com", db) is None


def test_report_detects_improving_trend(db):
    save_scan(make_scan(scanned_at="2026-07-01T10:00:00+00:00", score=30), db)
    save_scan(make_scan(scanned_at="2026-07-15T10:00:00+00:00", score=85), db)
    assert get_domain_report("example.com", db)["trend"] == "improving"


def test_report_detects_worsening_trend(db):
    save_scan(make_scan(scanned_at="2026-07-01T10:00:00+00:00", score=90), db)
    save_scan(make_scan(scanned_at="2026-07-15T10:00:00+00:00", score=20), db)
    assert get_domain_report("example.com", db)["trend"] == "worsening"


def test_report_detects_stable_trend(db):
    save_scan(make_scan(scanned_at="2026-07-01T10:00:00+00:00", score=70), db)
    save_scan(make_scan(scanned_at="2026-07-15T10:00:00+00:00", score=72), db)
    assert get_domain_report("example.com", db)["trend"] == "stable"


def test_report_single_scan_has_insufficient_data(db):
    save_scan(make_scan(), db)
    assert get_domain_report("example.com", db)["trend"] == "insufficient data"


def test_report_aggregates_stats(db):
    save_scan(make_scan(scanned_at="2026-07-01T10:00:00+00:00", score=60), db)
    save_scan(make_scan(scanned_at="2026-07-02T10:00:00+00:00", score=80), db)
    st = get_domain_report("example.com", db)["stats"]
    assert st["total_scans"] == 2
    assert st["avg_score"] == 70
    assert st["best_score"] == 80
    assert st["worst_score"] == 60


def test_report_lists_top_vendors(db):
    """The three-table JOIN must find vendors across all scans of a domain."""
    save_scan(make_scan(), db)
    save_scan(make_scan(scanned_at="2026-07-02T10:00:00+00:00"), db)
    vendors = {v["vendor"]: v for v in get_domain_report("example.com", db)["top_vendors"]}
    assert "Google Analytics" in vendors
    assert vendors["Google Analytics"]["occurrences"] == 2
    assert vendors["Google Analytics"]["scans_seen_in"] == 2


def test_report_excludes_unknown_vendor_from_top_list(db):
    """'Unknown' isn't a vendor — it must not appear in the vendor ranking."""
    scan = make_scan(cookies=[{
        "name": "mystery", "domain": "e.com", "path": "/", "party": "first",
        "type": "session", "expires_at": None, "lifetime_days": None,
        "http_only": False, "secure": False, "same_site": "Lax",
        "value_length": 4, "category": "unknown", "vendor": "Unknown",
        "purpose": "-", "matched_by": "no match", "confidence": "none",
    }])
    save_scan(scan, db)
    report = get_domain_report("example.com", db)
    assert all(v["vendor"] != "Unknown" for v in report["top_vendors"])
    # ...but it IS surfaced in the review list.
    assert any(c["name"] == "mystery" for c in report["unknown_cookies"])


def test_report_history_is_oldest_first(db):
    """History powers a trend chart, so it must run forwards in time."""
    for day in ("05", "01", "03"):
        save_scan(make_scan(scanned_at=f"2026-07-{day}T10:00:00+00:00"), db)
    history = get_domain_report("example.com", db)["history"]
    dates = [h["scanned_at"][:10] for h in history]
    assert dates == sorted(dates)


# ---------------------------------------------------------------------------
# 7. ISOLATION
# ---------------------------------------------------------------------------

def test_two_databases_do_not_share_data(tmp_path):
    """
    Proof that the db_path argument really isolates data. If this failed, every
    test above would be suspect, because they'd all be sharing one database.
    """
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"
    init_db(db_a)
    init_db(db_b)
    save_scan(make_scan(domain="only-in-a.com"), db_a)
    assert len(list_domains(db_a)) == 1
    assert len(list_domains(db_b)) == 0
