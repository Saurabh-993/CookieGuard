"""
Tests for scanner/consent_diff.py
==================================

Pure functions — two cookie lists in, a comparison out. No browser, no network,
so this is fully testable and it's where the logic bugs would be.

The clicker itself (`consent_clicker.py`) is NOT tested here: it needs a real
page with a real banner. That's an integration test, noted in AI_CONTEXT.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scanner"))

from consent_diff import diff_consent, mark_post_consent_cookies, _cookie_key


def cookie(name, category="marketing", domain=".news.com",
           vendor="SomeVendor", party="first", path="/"):
    return {"name": name, "domain": domain, "path": path, "party": party,
            "category": category, "vendor": vendor, "lifetime_days": 365}


# ---------------------------------------------------------------------------
# 1. COOKIE IDENTITY
# ---------------------------------------------------------------------------

def test_identity_uses_name_domain_and_path():
    """
    Name alone isn't enough. `_ga` on bbc.com and `_ga` on example.com are
    genuinely different cookies belonging to different parties — which is
    exactly how the browser treats them.
    """
    a = cookie("_ga", domain=".bbc.com")
    b = cookie("_ga", domain=".example.com")
    assert _cookie_key(a) != _cookie_key(b)


def test_identity_ignores_a_leading_dot():
    """`.x.com` and `x.com` are the same cookie to a user."""
    assert _cookie_key(cookie("_ga", domain=".x.com")) == \
           _cookie_key(cookie("_ga", domain="x.com"))


# ---------------------------------------------------------------------------
# 2. THE DIFF
# ---------------------------------------------------------------------------

def test_detects_cookies_added_by_consent():
    pre = [cookie("session", "necessary")]
    post = pre + [cookie("_ga", "analytics"), cookie("_fbp", "marketing")]
    d = diff_consent(pre, post)
    assert d["pre_consent_count"] == 1
    assert d["post_consent_count"] == 3
    assert d["added_count"] == 2


def test_added_cookies_are_categorised():
    pre = [cookie("session", "necessary")]
    post = pre + [cookie("a", "analytics"), cookie("b", "marketing"),
                  cookie("c", "marketing")]
    d = diff_consent(pre, post)
    assert d["added_by_category"]["marketing"] == 2
    assert d["added_by_category"]["analytics"] == 1
    assert d["added_by_category"]["necessary"] == 0


def test_multiplier_quantifies_the_cost():
    """The headline number: how much MORE tracking accepting unlocks."""
    pre = [cookie("a", "necessary")]
    post = pre + [cookie(str(i)) for i in range(9)]
    assert diff_consent(pre, post)["multiplier"] == 10.0


def test_multiplier_is_none_when_nothing_preceded_it():
    """Guard against dividing by zero on a site with no pre-consent cookies."""
    assert diff_consent([], [cookie("a")])["multiplier"] is None


def test_vendors_added_are_ranked():
    pre = []
    post = [cookie("a", vendor="Google"), cookie("b", vendor="Google"),
            cookie("c", vendor="Meta")]
    vendors = diff_consent(pre, post)["vendors_added"]
    assert vendors[0]["vendor"] == "Google"
    assert vendors[0]["count"] == 2


def test_unknown_vendor_is_excluded_from_the_ranking():
    """'Unknown' isn't a vendor — it must not appear in a vendor list."""
    d = diff_consent([], [cookie("a", vendor="Unknown"), cookie("b", vendor="Meta")])
    assert all(v["vendor"] != "Unknown" for v in d["vendors_added"])


def test_a_cookie_present_both_times_is_not_counted_as_added():
    """The core correctness property of a diff."""
    same = cookie("_ga", "analytics")
    d = diff_consent([same], [same])
    assert d["added_count"] == 0


# ---------------------------------------------------------------------------
# 3. THE COMPLIANCE FINDING
# ---------------------------------------------------------------------------
# Non-necessary cookies present BEFORE the click are the actual violation.
# This is what CNIL fines people for.

def test_compliant_when_only_necessary_cookies_precede_consent():
    pre = [cookie("session", "necessary"), cookie("csrf", "necessary")]
    d = diff_consent(pre, pre + [cookie("_ga", "analytics")])
    assert d["pre_consent_violations"] == 0
    assert d["verdict"] == "compliant"


def test_minor_verdict_for_a_few_violations():
    pre = [cookie("session", "necessary"), cookie("_ga", "analytics")]
    d = diff_consent(pre, pre)
    assert d["pre_consent_violations"] == 1
    assert d["verdict"] == "minor"


def test_major_verdict_for_many_violations():
    pre = [cookie(str(i), "marketing") for i in range(12)]
    d = diff_consent(pre, pre)
    assert d["verdict"] == "major"
    assert d["violation_categories"]["marketing"] == 12


def test_necessary_cookies_are_never_a_violation():
    """They are consent-exempt, so their presence pre-consent is lawful."""
    pre = [cookie(str(i), "necessary") for i in range(20)]
    assert diff_consent(pre, pre)["pre_consent_violations"] == 0


# ---------------------------------------------------------------------------
# 4. FLAGGING
# ---------------------------------------------------------------------------

def test_marks_which_cookies_appeared_after_consent():
    """
    One list with a boolean flag, not two lists — so the database gains a
    single column and "show me everything that needed consent" is a WHERE
    clause rather than a set operation in Python.
    """
    pre = [cookie("session", "necessary")]
    post = pre + [cookie("_ga", "analytics")]
    marked = mark_post_consent_cookies(pre, post)

    assert len(marked) == 2
    by_name = {c["name"]: c for c in marked}
    assert by_name["session"]["set_after_consent"] is False
    assert by_name["_ga"]["set_after_consent"] is True


def test_marking_preserves_all_original_fields():
    marked = mark_post_consent_cookies([], [cookie("_ga", "analytics")])
    assert marked[0]["vendor"] == "SomeVendor"
    assert marked[0]["category"] == "analytics"


# ---------------------------------------------------------------------------
# 5. EDGE CASES
# ---------------------------------------------------------------------------

def test_empty_lists_do_not_crash():
    d = diff_consent([], [])
    assert d["added_count"] == 0
    assert d["verdict"] == "compliant"


def test_handles_a_banner_that_added_nothing():
    """
    Some sites genuinely load everything up front. Accepting changes nothing —
    which is itself a finding, and a damning one.
    """
    pre = [cookie("_ga", "analytics"), cookie("_fbp", "marketing")]
    d = diff_consent(pre, pre)
    assert d["added_count"] == 0
    assert d["pre_consent_violations"] == 2      # both were already there
    assert d["verdict"] == "minor"


def test_new_third_party_domains_are_reported():
    pre_domains = [{"domain": "cdn.com", "request_count": 3}]
    post_domains = pre_domains + [
        {"domain": "doubleclick.net", "request_count": 12,
         "category": "marketing", "vendor": "Google"},
    ]
    d = diff_consent([], [], pre_domains, post_domains)
    assert len(d["domains_added"]) == 1
    assert d["domains_added"][0]["domain"] == "doubleclick.net"
