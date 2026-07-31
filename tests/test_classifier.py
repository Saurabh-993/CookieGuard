"""
Tests for scanner/classifier.py
================================

WHY WRITE TESTS AT ALL
----------------------
The classifier is pure logic with no browser and no network, which makes it
perfectly testable: same input always gives the same output. That is exactly
the kind of code that repays automated testing.

Concretely, tests buy us three things:

  1. CONFIDENCE WHEN CHANGING trackers.json. Adding a signature could
     accidentally shadow an existing one. A test catches that instantly.
  2. EXECUTABLE DOCUMENTATION. `test_third_party_never_necessary` states a rule
     of the system more precisely than a paragraph of prose could.
  3. SOMETHING FOR CI TO RUN. Phase 6's GitHub Actions pipeline needs a real
     check to run on every push, or the pipeline is theatre.

HOW TO RUN
----------
    pytest -v

pytest discovers tests by convention: files named test_*.py, functions named
test_*. No registration or configuration needed.

HOW A TEST WORKS
----------------
`assert <something true>`. If the expression is False, pytest reports a
failure and shows you the actual values. That is the entire mechanism.
"""

import sys
from pathlib import Path

import pytest

# Make `scanner/` importable. __file__ is this file; .parent is tests/;
# .parent.parent is the project root; then we point at scanner/.
# Needed because tests/ and scanner/ are sibling folders, so Python would not
# otherwise find the module.
sys.path.insert(0, str(Path(__file__).parent.parent / "scanner"))

from classifier import (  # noqa: E402  (import after path setup, deliberately)
    classify_cookie,
    classify_scan,
    calculate_compliance_score,
    load_trackers,
    sorted_domain_signatures,
    _domain_matches,
    _registrable_domain,
)


# ---------------------------------------------------------------------------
# FIXTURES
# ---------------------------------------------------------------------------
# A pytest "fixture" is reusable setup. Any test that takes an argument named
# `trackers` automatically receives the return value of this function.
# scope="module" means it runs ONCE for the whole file rather than once per
# test — trackers.json never changes during a run, so re-reading it would be
# wasted disk I/O.

@pytest.fixture(scope="module")
def trackers():
    return load_trackers()


def make_cookie(name, domain="example.com", party="first",
                same_site="Lax", lifetime_days=30, cookie_type="persistent"):
    """
    Build a cookie dict for testing.

    A helper like this is worth writing because tests should show only what is
    RELEVANT to the case at hand. Without it, every test would repeat ten
    fields, and the one field that actually matters would be buried.
    """
    return {
        "name": name,
        "domain": domain,
        "path": "/",
        "party": party,
        "type": cookie_type,
        "expires_at": None,
        "lifetime_days": lifetime_days,
        "http_only": False,
        "secure": True,
        "same_site": same_site,
        "value_length": 20,
    }


# ---------------------------------------------------------------------------
# 1. THE SIGNATURE FILE ITSELF
# ---------------------------------------------------------------------------

def test_trackers_file_loads(trackers):
    """trackers.json must be valid JSON with the three expected sections."""
    assert "cookie_signatures" in trackers
    assert "domain_signatures" in trackers
    assert "generic_patterns" in trackers


def test_every_signature_has_required_fields(trackers):
    """
    Guard against a typo when someone adds a tracker by hand.

    Without this, a missing "category" key would only blow up at runtime, on
    whichever unlucky site happened to set that cookie.
    """
    valid = {"necessary", "functional", "analytics", "marketing"}
    for section in ("cookie_signatures", "domain_signatures", "generic_patterns"):
        for sig in trackers[section]:
            assert "category" in sig, f"missing category in {sig}"
            assert "vendor" in sig, f"missing vendor in {sig}"
            assert "purpose" in sig, f"missing purpose in {sig}"
            assert sig["category"] in valid, \
                f"invalid category '{sig['category']}' in {sig}"


def test_generic_patterns_are_valid_regex(trackers):
    """A malformed regex would crash the classifier on every single cookie."""
    import re
    for sig in trackers["generic_patterns"]:
        re.compile(sig["pattern"])  # raises re.error if invalid


# ---------------------------------------------------------------------------
# 2. DOMAIN HELPERS
# ---------------------------------------------------------------------------

def test_registrable_domain():
    assert _registrable_domain("www.google-analytics.com") == "google-analytics.com"
    assert _registrable_domain(".example.com") == "example.com"
    assert _registrable_domain("example.com") == "example.com"
    assert _registrable_domain("localhost") == "localhost"


def test_domain_matches_subdomains():
    """Subdomains of a tracker must still match its signature."""
    assert _domain_matches("stats.g.doubleclick.net", "doubleclick.net")
    assert _domain_matches(".doubleclick.net", "doubleclick.net")
    assert _domain_matches("doubleclick.net", "doubleclick.net")


def test_domain_matches_rejects_substring_lookalikes():
    """
    THE SECURITY-RELEVANT CASE.

    A naive `signature in domain` check would return True for
    "notdoubleclick.net.evil.com", letting an attacker's domain inherit a
    trusted classification. We require a real subdomain boundary (a dot), so
    these must NOT match.
    """
    assert not _domain_matches("notdoubleclick.net", "doubleclick.net")
    assert not _domain_matches("doubleclick.net.evil.com", "doubleclick.net")
    assert not _domain_matches("mydoubleclick.net", "doubleclick.net")


# ---------------------------------------------------------------------------
# 3. CLASSIFICATION — the core behaviour
# ---------------------------------------------------------------------------

def test_exact_match_google_analytics(trackers):
    result = classify_cookie(make_cookie("_ga"), trackers)
    assert result["category"] == "analytics"
    assert result["vendor"] == "Google Analytics"
    assert result["confidence"] == "high"


def test_exact_match_facebook_pixel(trackers):
    result = classify_cookie(make_cookie("_fbp"), trackers)
    assert result["category"] == "marketing"
    assert "Meta" in result["vendor"]


def test_prefix_match_ga4(trackers):
    """GA4 cookies have a variable suffix: _ga_ABC123XYZ."""
    result = classify_cookie(make_cookie("_ga_9BXYZ12345"), trackers)
    assert result["category"] == "analytics"
    assert "prefix" in result["matched_by"]


def test_longest_prefix_wins(trackers):
    """
    Precedence check. Both "_ga_" and "_gat" are prefix signatures.
    "_gat_UA-12345" must match "_gat" (the longer, more specific pattern for
    that name), not something shorter. This test exists because the bug it
    guards against is order-dependent and would be very hard to spot by eye.
    """
    result = classify_cookie(make_cookie("_gat_UA_12345"), trackers)
    assert result["category"] == "analytics"
    assert "_gat" in result["matched_by"]


def test_name_beats_domain(trackers):
    """
    PRECEDENCE: an exact name match must win over a domain match.

    _fbp on example.com is still the Facebook Pixel. Checking domain first
    would misclassify it as a first-party cookie of unknown purpose.
    """
    result = classify_cookie(
        make_cookie("_fbp", domain="example.com"), trackers
    )
    assert result["category"] == "marketing"
    assert "exact name" in result["matched_by"]


def test_domain_match_for_unknown_name(trackers):
    """
    We've never seen this cookie name, but it sits on doubleclick.net.
    Domain is enough to call it marketing.
    """
    result = classify_cookie(
        make_cookie("xyz123", domain=".doubleclick.net", party="third"),
        trackers,
    )
    assert result["category"] == "marketing"
    assert "domain" in result["matched_by"]
    assert result["confidence"] == "medium"


def test_domain_signatures_sorted_most_specific_first(trackers):
    """Longest domain must come first, so specific entries beat general ones."""
    lengths = [len(s["domain"]) for s in sorted_domain_signatures(trackers)]
    assert lengths == sorted(lengths, reverse=True)


def test_more_specific_domain_wins(trackers):
    """
    REGRESSION TEST — found on live bbc.com data.

    `mybbc-analytics.files.bbci.co.uk` was being classified `necessary` because
    the shorter `bbci.co.uk` entry matched first. A domain with "analytics" in
    its name was landing in the one consent-exempt category.

    Same bug class as test_longest_prefix_wins: whichever entry sits earlier in
    the data file wins, so overlapping entries need an explicit specificity
    rule.
    """
    specific = classify_cookie(
        make_cookie("x", domain="mybbc-analytics.files.bbci.co.uk", party="third"),
        trackers,
    )
    general = classify_cookie(
        make_cookie("x", domain="static.files.bbci.co.uk", party="third"),
        trackers,
    )
    assert specific["category"] == "analytics"   # specific entry wins
    assert general["category"] == "necessary"    # general entry still works


def test_scan_domain_enrichment_uses_specificity(trackers):
    """The same ordering rule must apply to third-party request domains."""
    result = classify_scan(
        {
            "domain": "bbc.com",
            "cookies": [],
            "third_party_domains": [
                {"domain": "mybbc-analytics.files.bbci.co.uk", "request_count": 3},
                {"domain": "static.files.bbci.co.uk", "request_count": 70},
            ],
        },
        trackers,
    )
    by_domain = {d["domain"]: d["category"] for d in result["third_party_domains"]}
    assert by_domain["mybbc-analytics.files.bbci.co.uk"] == "analytics"
    assert by_domain["static.files.bbci.co.uk"] == "necessary"


def test_generic_session_pattern(trackers):
    """A first-party cookie named like a session ID is necessary."""
    result = classify_cookie(make_cookie("session_id"), trackers)
    assert result["category"] == "necessary"


def test_generic_csrf_pattern(trackers):
    result = classify_cookie(make_cookie("csrf_token"), trackers)
    assert result["category"] == "necessary"


def test_generic_language_pattern(trackers):
    result = classify_cookie(make_cookie("language"), trackers)
    assert result["category"] == "functional"


def test_case_insensitive_generic_match(trackers):
    """Cookie naming in the wild is inconsistent: SESSIONID, sessionid, SessionId."""
    for name in ("SESSION_ID", "session_id", "Session_Id"):
        assert classify_cookie(make_cookie(name), trackers)["category"] == "necessary"


def test_unknown_cookie_is_not_guessed_safe(trackers):
    """
    THE MOST IMPORTANT TEST IN THIS FILE.

    An unrecognised cookie must come back "unknown", never "necessary".
    Guessing "necessary" would tell the user no consent is required when it
    may well be — a compliance tool that under-reports risk is worse than no
    tool at all.
    """
    result = classify_cookie(make_cookie("zqx_internal_42"), trackers)
    assert result["category"] == "unknown"
    assert result["confidence"] == "none"


def test_third_party_never_necessary(trackers):
    """
    THE SAFETY GUARD.

    A third-party cookie whose NAME looks like a session ID must be downgraded
    to "unknown". Another company's cookie cannot be necessary for OUR site to
    function, and "necessary" is the one consent-exempt category — so a false
    positive here is the most damaging mistake the classifier could make.
    """
    result = classify_cookie(
        make_cookie("session_id", domain="sketchy-tracker.com", party="third"),
        trackers,
    )
    assert result["category"] == "unknown"
    assert "downgraded" in result["matched_by"]


def test_first_party_session_still_necessary(trackers):
    """The guard above must not break the legitimate first-party case."""
    result = classify_cookie(
        make_cookie("session_id", domain="example.com", party="first"), trackers
    )
    assert result["category"] == "necessary"


# ---------------------------------------------------------------------------
# 4. COMPLIANCE SCORING
# ---------------------------------------------------------------------------

def test_perfect_score_for_necessary_only():
    """A site setting only necessary cookies pre-consent is fully compliant."""
    cookies = [
        {**make_cookie("PHPSESSID"), "category": "necessary"},
        {**make_cookie("csrf_token"), "category": "necessary"},
    ]
    result = calculate_compliance_score(cookies)
    assert result["score"] == 100
    assert result["grade"] == "A"
    assert result["cookies_requiring_consent"] == 0


def test_no_cookies_scores_100():
    """Edge case: an empty list must not crash or produce a weird score."""
    result = calculate_compliance_score([])
    assert result["score"] == 100


def test_marketing_cookies_reduce_score():
    cookies = [{**make_cookie("_fbp"), "category": "marketing"}]
    result = calculate_compliance_score(cookies)
    assert result["score"] < 100
    assert result["cookies_requiring_consent"] == 1


def test_marketing_penalised_more_than_analytics():
    """Marketing involves cross-site profiling, so it must cost more."""
    mkt = calculate_compliance_score(
        [{**make_cookie("_fbp"), "category": "marketing"}]
    )
    ana = calculate_compliance_score(
        [{**make_cookie("_ga"), "category": "analytics"}]
    )
    assert mkt["score"] < ana["score"]


def test_diminishing_returns():
    """
    The 1st marketing cookie must cost more than the 21st.

    Rationale: going from zero to one is the legally meaningful event — that's
    when a violation starts existing. A flat penalty would floor every
    ad-funded site at zero and make the score useless for comparison.
    """
    one = calculate_compliance_score(
        [{**make_cookie("_fbp"), "category": "marketing"}]
    )
    twenty = calculate_compliance_score(
        [{**make_cookie(f"ad{i}"), "category": "marketing"} for i in range(20)]
    )
    drop_first = 100 - one["score"]
    drop_twenty = 100 - twenty["score"]
    assert drop_twenty > drop_first        # more cookies is still worse
    assert drop_twenty < drop_first * 20   # but not linearly worse


def test_score_never_negative():
    """Floor the score at 0 — a negative grade would be nonsense."""
    cookies = [
        {**make_cookie(f"ad{i}", party="third", same_site="None",
                       lifetime_days=800), "category": "marketing"}
        for i in range(60)
    ]
    result = calculate_compliance_score(cookies)
    assert result["score"] >= 0
    assert result["grade"] == "F"


def test_excessive_lifetime_is_penalised():
    """CNIL recommends a 13-month cap; longer-lived cookies lose points."""
    short = calculate_compliance_score(
        [{**make_cookie("_ga", lifetime_days=30), "category": "analytics"}]
    )
    long = calculate_compliance_score(
        [{**make_cookie("_ga", lifetime_days=730), "category": "analytics"}]
    )
    assert long["score"] < short["score"]


def test_grades_map_to_scores():
    """Every score must land in exactly one grade band."""
    for cookies, expected in [
        ([], "A"),
        ([{**make_cookie(f"ad{i}"), "category": "marketing"} for i in range(30)], "F"),
    ]:
        assert calculate_compliance_score(cookies)["grade"] == expected


# ---------------------------------------------------------------------------
# 5. WHOLE-SCAN INTEGRATION
# ---------------------------------------------------------------------------

def test_classify_scan_end_to_end(trackers):
    """Feed in a realistic scan result and check the whole pipeline."""
    scan = {
        "url": "https://example.com",
        "domain": "example.com",
        "cookie_count": 4,
        "cookies": [
            make_cookie("PHPSESSID"),
            make_cookie("_ga", lifetime_days=730),
            make_cookie("_fbp", domain=".facebook.com", party="third",
                        same_site="None", lifetime_days=90),
            make_cookie("weird_internal_thing"),
        ],
        "third_party_domains": [
            {"domain": "google-analytics.com", "request_count": 3},
            {"domain": "totally-unknown-xyz.com", "request_count": 1},
        ],
    }

    result = classify_scan(scan, trackers)

    # Every cookie gained the classification fields.
    for c in result["cookies"]:
        assert "category" in c and "vendor" in c and "matched_by" in c

    # Category counts add up.
    assert result["categories"]["necessary"] == 1
    assert result["categories"]["analytics"] == 1
    assert result["categories"]["marketing"] == 1
    assert result["categories"]["unknown"] == 1

    # Third-party domains were categorised too.
    doms = {d["domain"]: d for d in result["third_party_domains"]}
    assert doms["google-analytics.com"]["category"] == "analytics"
    assert doms["totally-unknown-xyz.com"]["category"] == "unknown"

    # A compliance block exists and is sane.
    assert 0 <= result["compliance"]["score"] <= 100
    assert result["compliance"]["cookies_requiring_consent"] == 3


def test_classify_scan_does_not_mutate_input(trackers):
    """
    classify_scan must return a NEW object, not modify the caller's.

    Functions that silently mutate their arguments are a classic source of
    confusing bugs — the caller's data changes without them asking.
    """
    original = {
        "domain": "example.com",
        "cookies": [make_cookie("_ga")],
        "third_party_domains": [],
    }
    classify_scan(original, trackers)
    assert "category" not in original["cookies"][0]
    assert "compliance" not in original


def test_classify_scan_handles_empty_scan(trackers):
    """A site with no cookies must not crash the classifier."""
    result = classify_scan(
        {"domain": "example.com", "cookies": [], "third_party_domains": []},
        trackers,
    )
    assert result["compliance"]["score"] == 100
    assert all(v == 0 for v in result["categories"].values())
