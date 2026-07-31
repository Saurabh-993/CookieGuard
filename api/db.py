"""
CookieGuard — Database layer (Phase 2b)
========================================

WHAT THIS FILE DOES
-------------------
Everything to do with storing and retrieving scans. Nothing else in the project
talks to SQLite directly — if you want data, you call a function in here.

WHY A DATABASE INSTEAD OF JSON FILES?
-------------------------------------
Right now every scan is a separate JSON file. That works for one scan. It falls
apart the moment you ask a real question:

    "Show me every marketing cookie across all scans of bbc.com since June"
    "Is this site getting better or worse over time?"
    "Which vendor appears on the most sites we audit?"

With JSON files you would have to open every file, parse it, and filter in
Python — every single time. With a database you ask once, in SQL, and an index
makes it fast.

    JSON FILES                        DATABASE
    ----------                        --------
    Read whole file to find one row   Jump straight to it via an index
    Filtering = Python loops          Filtering = WHERE clause
    No relationships                  Foreign keys enforce them
    No safety if writing fails        Transactions: all-or-nothing
    Fine for 10 scans                 Fine for 10 million

WHY SQLite SPECIFICALLY
-----------------------
It is a complete SQL database that lives in a single file, and the `sqlite3`
module is part of the Python standard library — nothing to install, no server
to run, no passwords to configure. For a single-machine audit tool that is
exactly right.

Its main limitation is that only ONE process can write at a time. That is fine
for us today, and the day we need concurrent writers is the day we move to
PostgreSQL. Because we write plain standard SQL and keep it all in this one
file, that migration is small.

RUN IT LIKE THIS
----------------
    python api/db.py init                    # create the database + tables
    python api/db.py save data/bbc.json      # classify and store a scan
    python api/db.py list                    # every domain we've scanned
    python api/db.py history bbc.com         # scan history for one domain
    python api/db.py report bbc.com          # summary + trend
"""

import argparse
import json
import os
import sqlite3          # the SQLite driver, built into Python
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make scanner/ importable so we can classify a raw scan before saving it.
# `__file__` is this file; .parent is api/; .parent.parent is the project root.
sys.path.insert(0, str(Path(__file__).parent.parent / "scanner"))
from classifier import classify_scan, load_trackers  # noqa: E402


# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

# The database is one file on disk. We put it in data/ (which .gitignore
# excludes, because a database is generated output, not source code).
#
# The location can be overridden with the COOKIEGUARD_DB environment variable.
# Two reasons that matters:
#   * Docker (Phase 6) mounts the database on a volume at a different path
#   * tests point it at a throwaway file so they never touch real data
#
# Reading configuration from the environment rather than hardcoding it is one
# of the "twelve-factor app" principles, and it's what lets the same image run
# in dev, CI and production unchanged.
DEFAULT_DB_PATH = Path(
    os.environ.get("COOKIEGUARD_DB")
    or (Path(__file__).parent.parent / "data" / "cookieguard.db")
)


def _resolve_db(db_path=None) -> Path:
    """
    Work out which database file to use.

    ⚠ WHY THIS FUNCTION EXISTS — a real Python gotcha.

    The obvious way to write these functions would be:

        def list_domains(db_path=DEFAULT_DB_PATH):    # ← looks fine, isn't

    Python evaluates default arguments ONCE, when the `def` line is executed
    at import time. So `db_path` gets permanently bound to whatever
    DEFAULT_DB_PATH was at import. Changing DEFAULT_DB_PATH afterwards — which
    is exactly what a test does — has no effect at all, and the test silently
    reads and writes the real database.

    Using `db_path=None` and resolving inside the function means the lookup
    happens at CALL time, so overrides work.

    This is the same trap as the notorious `def f(items=[])`: the list is
    created once and shared by every call. Late-binding defaults is the fix
    for both.
    """
    if db_path is not None:
        return Path(db_path)
    return DEFAULT_DB_PATH


# ---------------------------------------------------------------------------
# THE SCHEMA
# ---------------------------------------------------------------------------
# A "schema" is the shape of the database: which tables exist, which columns
# each has, and how they relate. Below is the whole thing in one SQL string.
#
# HOW THE TABLES RELATE
# ---------------------
#
#   domains  (one row per website ever scanned)
#      │  1
#      │
#      │  many
#   scans  (one row per scan run — this is the history)
#      │  1                          │  1
#      │                             │
#      │  many                       │  many
#   cookies                    third_party_domains
#
# Read the arrows as: "one domain has many scans; one scan has many cookies".
# This is called a ONE-TO-MANY relationship, and it is the most common shape
# in relational databases.
#
# WHY SPLIT IT UP AT ALL? (this is "normalisation")
# -------------------------------------------------
# We could have shoved everything into one giant table with a row per cookie
# and the domain name repeated on every row. That's a bad idea:
#
#   * WASTE       "bbc.com" stored 38 times per scan instead of once
#   * INCONSISTENCY  fix a typo in one row, 37 rows still wrong
#   * NO INTEGRITY   nothing stops a cookie existing with no scan
#
# Storing each fact exactly once, and pointing at it with an ID, is
# normalisation. It is the default you should reach for.
#
# (We do break the rule deliberately in one place — see the note on the scans
# table below. Knowing when to break a rule matters as much as knowing it.)

SCHEMA_SQL = """
-- ===========================================================================
-- TABLE 1: domains — one row per website we have ever scanned
-- ===========================================================================
CREATE TABLE IF NOT EXISTS domains (
    -- PRIMARY KEY = the unique identifier for a row. Every table needs one.
    -- INTEGER PRIMARY KEY in SQLite auto-numbers itself: 1, 2, 3...
    -- We use a meaningless number rather than the domain name itself
    -- (a "surrogate key") so that if a name ever needs correcting, nothing
    -- that points at this row breaks.
    id            INTEGER PRIMARY KEY,

    -- NOT NULL  = this column can never be empty.
    -- UNIQUE    = no two rows may have the same domain. The database itself
    --             enforces this, so even a buggy INSERT cannot create a
    --             duplicate. Rules enforced by the database beat rules
    --             enforced by application code, because they hold no matter
    --             which code path is running.
    domain        TEXT NOT NULL UNIQUE,

    -- SQLite has no DATE type. We store ISO-8601 UTC strings such as
    -- "2026-07-31T09:14:22+00:00". They sort correctly as plain text, which
    -- is exactly why ISO-8601 orders fields largest-to-smallest.
    first_seen    TEXT NOT NULL,
    last_scanned  TEXT
);

-- ===========================================================================
-- TABLE 2: scans — one row per scan run. This IS the audit history.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS scans (
    id                  INTEGER PRIMARY KEY,

    -- FOREIGN KEY = this column holds the id of a row in another table.
    -- It is what turns separate tables into a connected database.
    --
    -- REFERENCES domains(id)  the database will REFUSE to insert a scan
    --                         whose domain_id doesn't exist. That guarantee
    --                         is called referential integrity.
    --
    -- ON DELETE CASCADE       if a domain is deleted, its scans are deleted
    --                         automatically. Without this you get "orphan"
    --                         rows pointing at nothing.
    domain_id           INTEGER NOT NULL
                        REFERENCES domains(id) ON DELETE CASCADE,

    url                 TEXT NOT NULL,
    final_url           TEXT,
    page_title          TEXT,
    http_status         INTEGER,
    scanned_at          TEXT NOT NULL,
    duration_seconds    REAL,      -- REAL = a decimal number
    error               TEXT,      -- NULL when the scan went fine

    -- ---- Raw counts -------------------------------------------------------
    cookie_count        INTEGER NOT NULL DEFAULT 0,
    first_party_cookies INTEGER DEFAULT 0,
    third_party_cookies INTEGER DEFAULT 0,
    session_cookies     INTEGER DEFAULT 0,
    persistent_cookies  INTEGER DEFAULT 0,
    total_requests      INTEGER DEFAULT 0,

    -- ---- Category counts and score ---------------------------------------
    -- ⚠ DELIBERATE DENORMALISATION.
    --
    -- Every number below could be recalculated by counting rows in the
    -- cookies table. Storing it again technically duplicates data, which
    -- normalisation says to avoid.
    --
    -- We do it anyway because the dashboard's main screen shows a list of
    -- scans with their scores. Without these columns, drawing that list means
    -- running a COUNT over the cookies table for every single scan on screen.
    -- With them it is one cheap SELECT.
    --
    -- The cost is that these columns could drift out of sync with the cookies
    -- table if something updated one and not the other. We contain that risk
    -- by writing both inside ONE transaction, and by never updating a scan
    -- after it is written — scans are immutable historical records.
    --
    -- That is the real trade-off: normalise for correctness, denormalise for
    -- read speed, and be able to say WHY you chose.
    necessary_count           INTEGER DEFAULT 0,
    functional_count          INTEGER DEFAULT 0,
    analytics_count           INTEGER DEFAULT 0,
    marketing_count           INTEGER DEFAULT 0,
    unknown_count             INTEGER DEFAULT 0,
    compliance_score          INTEGER,
    compliance_grade          TEXT,
    cookies_requiring_consent INTEGER DEFAULT 0
);

-- ===========================================================================
-- TABLE 3: cookies — one row per cookie found in a scan
-- ===========================================================================
CREATE TABLE IF NOT EXISTS cookies (
    id            INTEGER PRIMARY KEY,
    scan_id       INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,

    name          TEXT NOT NULL,
    domain        TEXT,
    path          TEXT,
    party         TEXT,          -- 'first' | 'third'
    cookie_type   TEXT,          -- 'session' | 'persistent'
                                 -- named cookie_type, not type: 'type' reads
                                 -- ambiguously next to SQL's own type names
    expires_at    TEXT,
    lifetime_days INTEGER,

    -- SQLite has no BOOLEAN type. The convention is INTEGER 0 = false,
    -- 1 = true. Python's True/False convert automatically on the way in.
    http_only     INTEGER DEFAULT 0,
    secure        INTEGER DEFAULT 0,
    same_site     TEXT,

    -- Length only. We never store the cookie's VALUE — it can contain
    -- personal data, and storing it would make this audit database a privacy
    -- liability in its own right. Privacy by design, applied to our own tool.
    value_length  INTEGER,

    -- ---- Classification, from classifier.py ------------------------------
    category      TEXT,          -- necessary|functional|analytics|marketing|unknown
    vendor        TEXT,
    purpose       TEXT,
    matched_by    TEXT,          -- how we decided — keeps the result auditable
    confidence    TEXT           -- high|medium|low|none
);

-- ===========================================================================
-- TABLE 4: third_party_domains — other companies the page contacted
-- ===========================================================================
-- These matter even when no cookie is set: a tracking pixel transmits your IP,
-- device and referring page purely by being requested.
--
-- NOTE what we do NOT store: the individual network requests. A single CNN
-- scan made thousands. Storing every URL would bloat the database enormously
-- for very little analytical value, since the useful question is "which
-- companies, how often" — which is exactly what this aggregated table answers.
-- Deciding what NOT to persist is a real design decision.
CREATE TABLE IF NOT EXISTS third_party_domains (
    id            INTEGER PRIMARY KEY,
    scan_id       INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    domain        TEXT NOT NULL,
    request_count INTEGER DEFAULT 0,
    category      TEXT,
    vendor        TEXT
);

-- ===========================================================================
-- INDEXES
-- ===========================================================================
-- An index is like the index at the back of a textbook. Without one, finding
-- every mention of "cookie" means reading all 900 pages. With one, you look up
-- the word and jump straight to the right pages.
--
-- Technically: without an index the database does a FULL TABLE SCAN, checking
-- every row. With one it uses a sorted structure (a B-tree) to jump directly
-- to matching rows.
--
-- Indexes are not free: they take disk space, and every INSERT has to update
-- them too. So you index the columns you FILTER or JOIN on, not every column.
--
-- Each index below exists because a specific query we actually run needs it.

-- "give me all scans for this domain" — used on every history page
CREATE INDEX IF NOT EXISTS idx_scans_domain_id ON scans(domain_id);

-- "give me the most recent scans" — used for sorting history newest-first
CREATE INDEX IF NOT EXISTS idx_scans_scanned_at ON scans(scanned_at);

-- "give me all cookies for this scan" — used on every scan detail page
CREATE INDEX IF NOT EXISTS idx_cookies_scan_id ON cookies(scan_id);

-- "give me all marketing cookies" — used by category filters and charts
CREATE INDEX IF NOT EXISTS idx_cookies_category ON cookies(category);

-- "which vendors appear most often" — used by the vendor summary
CREATE INDEX IF NOT EXISTS idx_cookies_vendor ON cookies(vendor);

CREATE INDEX IF NOT EXISTS idx_tpd_scan_id ON third_party_domains(scan_id);
"""


# ---------------------------------------------------------------------------
# CONNECTION HANDLING
# ---------------------------------------------------------------------------

def get_connection(db_path=None) -> sqlite3.Connection:
    """
    Open a connection to the database file, configured the way we want it.

    A "connection" is your open session with the database. You send SQL through
    it and read results back.

    TWO SETTINGS THAT MATTER A LOT
    ------------------------------

    1. `row_factory = sqlite3.Row`

       By default SQLite hands back plain tuples:

           row = ("bbc.com", 38, 0)
           row[1]                        # 38 — but what IS index 1?

       With sqlite3.Row you get access by column name:

           row["cookie_count"]           # 38 — obvious, and survives a schema
                                         #      change that reorders columns

       Positional access is a bug waiting to happen. Always set this.

    2. `PRAGMA foreign_keys = ON`

       ⚠ SQLite DISABLES foreign key enforcement BY DEFAULT, for backwards
       compatibility with very old versions. That means all those REFERENCES
       clauses in our schema are silently ignored unless you switch this on —
       you can insert a cookie pointing at a scan that doesn't exist, and
       ON DELETE CASCADE never fires.

       It must be set on EVERY connection; it is not stored in the file.

       This is a genuinely notorious gotcha and a fair interview question.
    """
    # Create the parent folder if it doesn't exist yet, so a fresh clone works.
    db_path = _resolve_db(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path=None) -> None:
    """
    Create the database file and all tables/indexes if they don't exist.

    `executescript` runs several SQL statements separated by semicolons.
    Every CREATE uses `IF NOT EXISTS`, so running this repeatedly is safe —
    an operation you can repeat without changing the result is called
    IDEMPOTENT, and it's a valuable property: startup code can just always
    run it without checking first.
    """
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        # `finally` runs whether or not an exception happened, so the
        # connection is always released. Leaked connections hold file locks,
        # and in SQLite a stale lock blocks every other writer.
        conn.close()


def _now() -> str:
    """Current UTC time as an ISO-8601 string — our standard timestamp format."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# WRITING
# ---------------------------------------------------------------------------

def get_or_create_domain(conn: sqlite3.Connection, domain: str) -> int:
    """
    Return the id of `domain`, inserting it if we've never seen it before.

    This is the "upsert" pattern, and it's what keeps the domains table free of
    duplicates while letting callers just say "give me the id for bbc.com".

    ⚠ NOTE THE `?` PLACEHOLDER — this is the most important line in the file.

        cur.execute("SELECT id FROM domains WHERE domain = ?", (domain,))

    We do NOT build the SQL by gluing strings together. Never write this:

        f"SELECT id FROM domains WHERE domain = '{domain}'"      # ☠️ NEVER

    Why it's dangerous: the value gets pasted directly into the SQL text, so a
    value containing SQL syntax becomes part of the command. If `domain` were

        x'; DROP TABLE domains; --

    the database would receive two statements and cheerfully delete the table.
    That's SQL INJECTION, and it remains one of the most common serious
    vulnerabilities in real software.

    With `?`, the SQL text and the data travel separately. The database parses
    the command FIRST, then slots the value in as pure data. It can never be
    read as SQL, no matter what characters it contains.

        WRONG:  "...WHERE domain = '" + user_input + "'"
        RIGHT:  "...WHERE domain = ?", (user_input,)

    The `(domain,)` trailing comma is required — it makes a one-element TUPLE.
    Without it, `(domain)` is just a string in brackets, and sqlite3 would try
    to treat each character as a separate parameter.
    """
    cur = conn.execute("SELECT id FROM domains WHERE domain = ?", (domain,))
    row = cur.fetchone()          # fetchone() -> first row, or None
    if row:
        return row["id"]

    now = _now()
    cur = conn.execute(
        "INSERT INTO domains (domain, first_seen, last_scanned) VALUES (?, ?, ?)",
        (domain, now, now),
    )
    # lastrowid gives the id the database just auto-generated.
    return cur.lastrowid


def save_scan(scan_result: dict, db_path=None) -> int:
    """
    Store one complete scan and return its new scan_id.

    Accepts either a raw `scan.py` result or an already-classified one. If the
    `compliance` key is missing we classify it first, so callers can't
    accidentally store unclassified data.

    TRANSACTIONS — why every write happens as one unit
    --------------------------------------------------
    Saving a scan writes to four tables. Imagine the program crashes halfway:

        domains        ✅ written
        scans          ✅ written
        cookies        ❌ crash here
        third_party    ❌ never written

    You'd be left with a scan claiming 38 cookies and zero cookie rows —
    corrupt data that every later query silently gets wrong.

    A TRANSACTION makes the whole thing ALL-OR-NOTHING:

        conn.commit()     → make every change permanent, together
        conn.rollback()   → undo every change, as if nothing happened

    Python's sqlite3 opens a transaction automatically before the first write,
    so we just have to decide how it ends. Committing once at the very end
    means a failure anywhere leaves the database exactly as it was.

    (The classic illustration is a bank transfer: debit one account, credit the
    other. Doing only half is catastrophic. Same principle, lower stakes.)
    """
    # Classify first if needed. `.get()` returns None instead of raising when
    # the key is absent.
    if not scan_result.get("compliance"):
        scan_result = classify_scan(scan_result, load_trackers())

    conn = get_connection(db_path)
    try:
        domain = scan_result.get("domain") or "unknown"
        domain_id = get_or_create_domain(conn, domain)

        cats = scan_result.get("categories", {})
        comp = scan_result.get("compliance", {})

        # ---- 1. the scan row ----
        cur = conn.execute(
            """
            INSERT INTO scans (
                domain_id, url, final_url, page_title, http_status,
                scanned_at, duration_seconds, error,
                cookie_count, first_party_cookies, third_party_cookies,
                session_cookies, persistent_cookies, total_requests,
                necessary_count, functional_count, analytics_count,
                marketing_count, unknown_count,
                compliance_score, compliance_grade, cookies_requiring_consent
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                domain_id,
                scan_result.get("url"),
                scan_result.get("final_url"),
                scan_result.get("page_title"),
                scan_result.get("http_status"),
                scan_result.get("scanned_at") or _now(),
                scan_result.get("duration_seconds"),
                scan_result.get("error"),
                scan_result.get("cookie_count", 0),
                scan_result.get("first_party_cookies", 0),
                scan_result.get("third_party_cookies", 0),
                scan_result.get("session_cookies", 0),
                scan_result.get("persistent_cookies", 0),
                scan_result.get("total_requests", 0),
                cats.get("necessary", 0),
                cats.get("functional", 0),
                cats.get("analytics", 0),
                cats.get("marketing", 0),
                cats.get("unknown", 0),
                comp.get("score"),
                comp.get("grade"),
                comp.get("cookies_requiring_consent", 0),
            ),
        )
        scan_id = cur.lastrowid

        # ---- 2. the cookies ----
        # Build a list of tuples, then insert them all at once with
        # `executemany`. One round-trip to the database instead of 177 is
        # dramatically faster — this is called BATCHING.
        cookie_rows = [
            (
                scan_id, c.get("name"), c.get("domain"), c.get("path"),
                c.get("party"), c.get("type"), c.get("expires_at"),
                c.get("lifetime_days"),
                # `int(bool(...))` converts Python True/False to SQLite 1/0,
                # and also copes with the value being None.
                int(bool(c.get("http_only"))),
                int(bool(c.get("secure"))),
                c.get("same_site"), c.get("value_length"),
                c.get("category"), c.get("vendor"), c.get("purpose"),
                c.get("matched_by"), c.get("confidence"),
            )
            for c in scan_result.get("cookies", [])
        ]
        if cookie_rows:
            conn.executemany(
                """
                INSERT INTO cookies (
                    scan_id, name, domain, path, party, cookie_type,
                    expires_at, lifetime_days, http_only, secure, same_site,
                    value_length, category, vendor, purpose, matched_by, confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                cookie_rows,
            )

        # ---- 3. the third-party domains ----
        tpd_rows = [
            (scan_id, d.get("domain"), d.get("request_count", 0),
             d.get("category"), d.get("vendor"))
            for d in scan_result.get("third_party_domains", [])
        ]
        if tpd_rows:
            conn.executemany(
                """
                INSERT INTO third_party_domains
                    (scan_id, domain, request_count, category, vendor)
                VALUES (?, ?, ?, ?, ?)
                """,
                tpd_rows,
            )

        # ---- 4. update the domain's last_scanned ----
        conn.execute(
            "UPDATE domains SET last_scanned = ? WHERE id = ?",
            (scan_result.get("scanned_at") or _now(), domain_id),
        )

        # Everything worked — make it all permanent, in one atomic step.
        conn.commit()
        return scan_id

    except Exception:
        # Anything went wrong — undo the entire operation.
        conn.rollback()
        # `raise` with no argument re-raises the original exception with its
        # original traceback, so the caller sees exactly what failed. Never
        # swallow an error here: silent data loss is far worse than a crash.
        raise
    finally:
        conn.close()


def delete_scan(scan_id: int, db_path=None) -> bool:
    """
    Delete a scan. Its cookies and third-party rows go too, automatically.

    We only issue ONE delete statement. The child rows disappear because of
    `ON DELETE CASCADE` in the schema — the database handles it. That's better
    than deleting them manually in Python, because it holds no matter which
    code (or which future developer) does the delete.

    Requires `PRAGMA foreign_keys = ON`, which get_connection() sets.
    """
    conn = get_connection(db_path)
    try:
        cur = conn.execute("DELETE FROM scans WHERE id = ?", (scan_id,))
        conn.commit()
        # rowcount = how many rows the statement affected. 0 means no such scan.
        return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# READING
# ---------------------------------------------------------------------------

def list_domains(db_path=None) -> list:
    """
    Every domain we've scanned, with summary stats.

    THIS QUERY DEMONSTRATES A JOIN — the core idea of relational databases.

    A JOIN combines rows from two tables by matching a column. Here we match
    `domains.id` against `scans.domain_id`, so each domain row is paired with
    its scans.

        LEFT JOIN  keep every row from the LEFT table (domains), even if it
                   has no matching scans. Those get NULLs.
        INNER JOIN would DROP domains that have no scans.

    We choose LEFT JOIN so a domain that was created but never successfully
    scanned still appears, rather than silently vanishing.

    AGGREGATE FUNCTIONS collapse many rows into one value:

        COUNT(s.id)              how many scans
        MAX(s.scanned_at)        the most recent one
        AVG(s.compliance_score)  the average score

    GROUP BY d.id says: "produce one output row per domain, and apply those
    aggregates within each group". Without GROUP BY you'd get a single row for
    the entire table.
    """
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            """
            SELECT
                d.id,
                d.domain,
                d.first_seen,
                d.last_scanned,
                COUNT(s.id)                      AS scan_count,
                MAX(s.scanned_at)                AS latest_scan,
                AVG(s.compliance_score)          AS avg_score,
                MAX(s.cookie_count)              AS max_cookies
            FROM domains d
            LEFT JOIN scans s ON s.domain_id = d.id
            GROUP BY d.id
            ORDER BY d.domain
            """
        )
        # sqlite3.Row behaves like a dict but isn't one, so we convert.
        # Callers (and Phase 3's API) want plain dicts they can turn into JSON.
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_scans_for_domain(domain: str, limit: int = 50,
                         db_path=None) -> list:
    """
    Scan history for one domain, newest first.

    Note `LIMIT ?` — even the limit is a parameter, not glued into the string.
    Being consistent about this matters: the one place you make an exception is
    the place the injection gets through.
    """
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            """
            SELECT s.*
            FROM scans s
            INNER JOIN domains d ON d.id = s.domain_id
            WHERE d.domain = ?
            ORDER BY s.scanned_at DESC
            LIMIT ?
            """,
            (domain, limit),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_scan(scan_id: int, db_path=None) -> dict:
    """One scan with its cookies and third-party domains. None if not found."""
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            """
            SELECT s.*, d.domain
            FROM scans s
            INNER JOIN domains d ON d.id = s.domain_id
            WHERE s.id = ?
            """,
            (scan_id,),
        )
        row = cur.fetchone()
        if not row:
            return None

        scan = dict(row)

        # Sort the riskiest categories to the top of the table.
        # CASE ... END is SQL's if/else. Here it maps each category to a sort
        # rank so ORDER BY can use it — doing this in SQL rather than Python
        # means the database returns rows already in display order.
        cur = conn.execute(
            """
            SELECT * FROM cookies
            WHERE scan_id = ?
            ORDER BY CASE category
                        WHEN 'marketing'  THEN 0
                        WHEN 'unknown'    THEN 1
                        WHEN 'analytics'  THEN 2
                        WHEN 'functional' THEN 3
                        ELSE 4
                     END,
                     name
            """,
            (scan_id,),
        )
        scan["cookies"] = [dict(r) for r in cur.fetchall()]

        cur = conn.execute(
            """
            SELECT * FROM third_party_domains
            WHERE scan_id = ?
            ORDER BY request_count DESC
            """,
            (scan_id,),
        )
        scan["third_party_domains"] = [dict(r) for r in cur.fetchall()]

        return scan
    finally:
        conn.close()


def get_latest_scan(domain: str, db_path=None) -> dict:
    """The most recent scan for a domain, fully populated."""
    scans = get_scans_for_domain(domain, limit=1, db_path=db_path)
    if not scans:
        return None
    return get_scan(scans[0]["id"], db_path=db_path)


def get_domain_report(domain: str, db_path=None) -> dict:
    """
    The compliance summary for a domain: latest state, history and trend.

    This is what Phase 4's audit report page will render, and it shows why the
    database earns its place — "is this site improving?" is a question you
    simply cannot answer from a folder of JSON files without writing a program.
    """
    conn = get_connection(db_path)
    try:
        cur = conn.execute("SELECT * FROM domains WHERE domain = ?", (domain,))
        domain_row = cur.fetchone()
        if not domain_row:
            return None

        domain_id = domain_row["id"]

        # ---- Overall stats across every scan of this domain ----
        cur = conn.execute(
            """
            SELECT
                COUNT(*)                  AS total_scans,
                MIN(scanned_at)           AS first_scan,
                MAX(scanned_at)           AS latest_scan,
                AVG(compliance_score)     AS avg_score,
                MIN(compliance_score)     AS worst_score,
                MAX(compliance_score)     AS best_score,
                AVG(cookie_count)         AS avg_cookies
            FROM scans
            WHERE domain_id = ?
            """,
            (domain_id,),
        )
        stats = dict(cur.fetchone())

        # ---- The most recent scan ----
        cur = conn.execute(
            """
            SELECT * FROM scans
            WHERE domain_id = ?
            ORDER BY scanned_at DESC
            LIMIT 1
            """,
            (domain_id,),
        )
        latest_row = cur.fetchone()
        latest = dict(latest_row) if latest_row else None

        # ---- Which vendors appear most often on this domain? ----
        # A three-table JOIN: cookies → scans → domains.
        # Reading it backwards: find this domain, find its scans, find their
        # cookies, then group those cookies by vendor and count them.
        cur = conn.execute(
            """
            SELECT
                c.vendor,
                c.category,
                COUNT(*)                    AS occurrences,
                COUNT(DISTINCT c.scan_id)   AS scans_seen_in
            FROM cookies c
            INNER JOIN scans s   ON s.id = c.scan_id
            INNER JOIN domains d ON d.id = s.domain_id
            WHERE d.domain = ?
              AND c.vendor IS NOT NULL
              AND c.vendor != 'Unknown'
            GROUP BY c.vendor, c.category
            ORDER BY occurrences DESC
            LIMIT 15
            """,
            (domain,),
        )
        top_vendors = [dict(r) for r in cur.fetchall()]

        # ---- Cookies still needing human review ----
        cur = conn.execute(
            """
            SELECT DISTINCT c.name, c.domain, c.party
            FROM cookies c
            INNER JOIN scans s   ON s.id = c.scan_id
            INNER JOIN domains d ON d.id = s.domain_id
            WHERE d.domain = ? AND c.category = 'unknown'
            ORDER BY c.name
            """,
            (domain,),
        )
        unknowns = [dict(r) for r in cur.fetchall()]

        # ---- Score history, oldest first, for a trend line ----
        cur = conn.execute(
            """
            SELECT scanned_at, compliance_score, compliance_grade, cookie_count
            FROM scans
            WHERE domain_id = ?
            ORDER BY scanned_at ASC
            """,
            (domain_id,),
        )
        history = [dict(r) for r in cur.fetchall()]

        # ---- Turn the history into a one-word trend ----
        # Comparing only first vs last is crude but honest and easy to explain.
        # A real trend line would fit a regression; we don't need that yet.
        trend = "insufficient data"
        if len(history) >= 2:
            first_score = history[0]["compliance_score"] or 0
            last_score = history[-1]["compliance_score"] or 0
            if last_score > first_score + 5:
                trend = "improving"
            elif last_score < first_score - 5:
                trend = "worsening"
            else:
                trend = "stable"

        return {
            "domain": domain,
            "first_seen": domain_row["first_seen"],
            "last_scanned": domain_row["last_scanned"],
            "stats": stats,
            "latest_scan": latest,
            "top_vendors": top_vendors,
            "unknown_cookies": unknowns,
            "history": history,
            "trend": trend,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# TERMINAL OUTPUT
# ---------------------------------------------------------------------------

def print_domains(domains: list) -> None:
    line = "=" * 78
    print(f"\n{line}\n  CookieGuard — Scanned Domains\n{line}")
    if not domains:
        print("  (none yet — run: python api/db.py save data/bbc.json)\n")
        return
    print(f"  {'DOMAIN':<30}{'SCANS':>7}{'AVG SCORE':>11}{'MAX COOKIES':>13}   LATEST")
    print(f"  {'-' * 74}")
    for d in domains:
        avg = f"{d['avg_score']:.0f}" if d["avg_score"] is not None else "-"
        latest = (d["latest_scan"] or "-")[:19].replace("T", " ")
        print(f"  {d['domain'][:29]:<30}{d['scan_count']:>7}{avg:>11}"
              f"{d['max_cookies'] or 0:>13}   {latest}")
    print(f"{line}\n")


def print_history(domain: str, scans: list) -> None:
    line = "=" * 78
    print(f"\n{line}\n  Scan history — {domain}\n{line}")
    if not scans:
        print(f"  (no scans recorded for {domain})\n")
        return
    print(f"  {'ID':>4}  {'WHEN':<20}{'COOKIES':>8}{'SCORE':>7}{'GRADE':>7}"
          f"{'NEC':>5}{'FUN':>5}{'ANA':>5}{'MKT':>5}{'UNK':>5}")
    print(f"  {'-' * 74}")
    for s in scans:
        when = (s["scanned_at"] or "")[:19].replace("T", " ")
        print(f"  {s['id']:>4}  {when:<20}{s['cookie_count']:>8}"
              f"{s['compliance_score'] if s['compliance_score'] is not None else '-':>7}"
              f"{s['compliance_grade'] or '-':>7}"
              f"{s['necessary_count']:>5}{s['functional_count']:>5}"
              f"{s['analytics_count']:>5}{s['marketing_count']:>5}"
              f"{s['unknown_count']:>5}")
    print(f"{line}\n")


def print_report(report: dict) -> None:
    line = "=" * 78
    sub = "-" * 74
    st = report["stats"]
    latest = report["latest_scan"]

    print(f"\n{line}\n  CookieGuard — Domain Audit Report\n{line}")
    print(f"  Domain       : {report['domain']}")
    print(f"  First seen   : {(report['first_seen'] or '-')[:19].replace('T', ' ')}")
    print(f"  Last scanned : {(report['last_scanned'] or '-')[:19].replace('T', ' ')}")
    print(f"  Total scans  : {st['total_scans']}")

    if latest:
        print(f"\n  LATEST RESULT")
        print(f"  {sub}")
        print(f"  Score  : {latest['compliance_score']}/100   Grade: {latest['compliance_grade']}")
        print(f"  Cookies: {latest['cookie_count']}  "
              f"(necessary {latest['necessary_count']}, "
              f"functional {latest['functional_count']}, "
              f"analytics {latest['analytics_count']}, "
              f"marketing {latest['marketing_count']}, "
              f"unknown {latest['unknown_count']})")
        print(f"  Cookies requiring consent, set before consent: "
              f"{latest['cookies_requiring_consent']}")

    if st["total_scans"] and st["avg_score"] is not None:
        print(f"\n  ACROSS ALL SCANS")
        print(f"  {sub}")
        print(f"  Average score : {st['avg_score']:.1f}")
        print(f"  Best / worst  : {st['best_score']} / {st['worst_score']}")
        print(f"  Trend         : {report['trend'].upper()}")

    if report["top_vendors"]:
        print(f"\n  MOST FREQUENT VENDORS")
        print(f"  {sub}")
        print(f"  {'VENDOR':<34}{'CATEGORY':<14}{'COOKIES':>9}{'IN SCANS':>10}")
        for v in report["top_vendors"][:10]:
            print(f"  {str(v['vendor'])[:33]:<34}{str(v['category']):<14}"
                  f"{v['occurrences']:>9}{v['scans_seen_in']:>10}")

    if report["unknown_cookies"]:
        print(f"\n  ⚠  {len(report['unknown_cookies'])} DISTINCT COOKIE(S) NEED REVIEW")
        print(f"  {sub}")
        for c in report["unknown_cookies"][:10]:
            print(f"    {c['name'][:44]:<46}{c['domain'][:24]:<26}{c['party']}")

    if len(report["history"]) > 1:
        print(f"\n  SCORE HISTORY")
        print(f"  {sub}")
        for h in report["history"]:
            score = h["compliance_score"] or 0
            bar = "#" * int(score / 2.5)          # 100 -> 40 chars wide
            when = (h["scanned_at"] or "")[:10]
            print(f"  {when}  {score:>3}  {bar}")

    print(f"\n  NOTE: automated classification is a technical aid, not legal advice.")
    print(f"{line}\n")


# ---------------------------------------------------------------------------
# COMMAND-LINE INTERFACE
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="CookieGuard — store and query scan results.",
    )
    # Subcommands: `db.py init`, `db.py save ...`, etc. Each gets its own args.
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Create the database and tables")

    p_save = sub.add_parser("save", help="Classify and store a scan JSON file")
    p_save.add_argument("scan_file", help="A JSON file produced by scan.py --output")

    sub.add_parser("list", help="List every scanned domain")

    p_hist = sub.add_parser("history", help="Scan history for one domain")
    p_hist.add_argument("domain")

    p_rep = sub.add_parser("report", help="Full audit report for one domain")
    p_rep.add_argument("domain")

    p_show = sub.add_parser("show", help="Show one scan in detail")
    p_show.add_argument("scan_id", type=int)

    parser.add_argument("--db", default=str(DEFAULT_DB_PATH),
                        help="Path to the database file")
    args = parser.parse_args()
    db_path = Path(args.db)

    # Always ensure the schema exists. It's idempotent, so this is free.
    init_db(db_path)

    if args.command == "init":
        print(f"[CookieGuard] Database ready at {db_path}")
        return 0

    if args.command == "save":
        path = Path(args.scan_file)
        if not path.exists():
            print(f"[CookieGuard] ERROR: file not found — {path}")
            return 1
        scan_result = json.loads(path.read_text(encoding="utf-8"))
        scan_id = save_scan(scan_result, db_path)
        saved = get_scan(scan_id, db_path)
        print(f"\n[CookieGuard] Saved scan #{scan_id} for {saved['domain']}")
        print(f"  {saved['cookie_count']} cookies, "
              f"{len(saved['third_party_domains'])} third-party domains")
        print(f"  Score {saved['compliance_score']}/100  "
              f"Grade {saved['compliance_grade']}\n")
        return 0

    if args.command == "list":
        print_domains(list_domains(db_path))
        return 0

    if args.command == "history":
        print_history(args.domain, get_scans_for_domain(args.domain, db_path=db_path))
        return 0

    if args.command == "report":
        report = get_domain_report(args.domain, db_path)
        if not report:
            print(f"[CookieGuard] No data for {args.domain}. "
                  f"Save a scan first with: python api/db.py save <file>")
            return 1
        print_report(report)
        return 0

    if args.command == "show":
        scan = get_scan(args.scan_id, db_path)
        if not scan:
            print(f"[CookieGuard] No scan with id {args.scan_id}")
            return 1
        print(f"\nScan #{scan['id']} — {scan['domain']} — {scan['scanned_at']}")
        print(f"Score {scan['compliance_score']}/100 ({scan['compliance_grade']})\n")
        print(f"  {'NAME':<28}{'CATEGORY':<12}{'VENDOR':<28}{'PARTY'}")
        print(f"  {'-' * 74}")
        for c in scan["cookies"]:
            print(f"  {str(c['name'])[:27]:<28}{str(c['category']):<12}"
                  f"{str(c['vendor'])[:27]:<28}{c['party']}")
        print()
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
