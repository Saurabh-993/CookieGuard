"""
CookieGuard — Pre/post-consent diff
====================================

WHAT THIS FILE DOES
-------------------
Compares the cookies present BEFORE consent with those present AFTER clicking
"Accept all", and reports what changed.

WHY THE DIFF IS THE PRODUCT
---------------------------
Two numbers, two very different stories:

    "bbc.com sets 38 cookies"                    ← a footnote
    "bbc.com sets 4 before consent, 61 after"    ← a headline

The second one answers the question people actually have: **what does
accepting cost you?** It's the number a journalist quotes, a privacy officer
acts on, and a regulator asks about.

It also verifies a claim sites make constantly — *"we only load analytics after
you agree"*. Before this, we had to take that on trust. Now we can check it.

THE TWO FINDINGS THIS PRODUCES
------------------------------

    1. WHAT CONSENT UNLOCKS
       Cookies that appeared only after accepting. Expected and legitimate —
       this is the system working as designed.

    2. WHAT FIRED ANYWAY  ⚠
       Non-necessary cookies present BEFORE the click. These are the
       violation: set without permission. This is what CNIL fines people for.

Finding (2) is the compliance failure. Finding (1) is just information —
though a very interesting kind.
"""


from typing import Optional


def _cookie_key(cookie: dict) -> tuple:
    """
    A stable identity for a cookie, used to tell "the same cookie" from
    "a different one".

    Name alone isn't enough: `_ga` on `.bbc.com` and `_ga` on `.example.com`
    are genuinely different cookies belonging to different parties. Name plus
    domain plus path is how the browser itself distinguishes them, so we use
    the same rule.

    We deliberately ignore the VALUE — a session id changes on every load, and
    we don't store values anyway.
    """
    return (
        cookie.get("name"),
        (cookie.get("domain") or "").lstrip("."),   # ".x.com" and "x.com" are
                                                    # the same cookie to a user
        cookie.get("path") or "/",
    )


def diff_consent(pre_cookies: list, post_cookies: list,
                 pre_domains: Optional[list] = None,
                 post_domains: Optional[list] = None) -> dict:
    """
    Compare two classified cookie lists and summarise what consent changed.

    `pre_cookies`  — cookies found before clicking accept
    `post_cookies` — cookies found after (includes the pre ones, since the
                     browser doesn't discard them)

    Returns a dict describing what appeared, by category and vendor.
    """
    pre_keys = {_cookie_key(c) for c in pre_cookies}

    # A cookie is "new" if its identity wasn't in the pre-consent set.
    # Set difference is the right tool here — O(1) lookups, and it reads as
    # exactly what we mean.
    new_cookies = [c for c in post_cookies if _cookie_key(c) not in pre_keys]

    def count_by_category(cookies):
        counts = {"necessary": 0, "functional": 0, "analytics": 0,
                  "marketing": 0, "unknown": 0}
        for c in cookies:
            cat = c.get("category", "unknown")
            counts[cat] = counts.get(cat, 0) + 1
        return counts

    new_by_category = count_by_category(new_cookies)

    # Which vendors appeared only after consent? This is the list that reads
    # best in a report: "accepting added Google, Meta and 14 ad exchanges."
    new_vendors = {}
    for c in new_cookies:
        vendor = c.get("vendor") or "Unknown"
        if vendor == "Unknown":
            continue
        entry = new_vendors.setdefault(
            vendor, {"vendor": vendor, "category": c.get("category"), "count": 0}
        )
        entry["count"] += 1
    vendors_added = sorted(
        new_vendors.values(), key=lambda v: v["count"], reverse=True
    )

    # New third-party domains contacted after consent.
    domains_added = []
    if pre_domains is not None and post_domains is not None:
        pre_names = {d.get("domain") for d in pre_domains}
        domains_added = [
            d for d in post_domains if d.get("domain") not in pre_names
        ]
        domains_added.sort(key=lambda d: d.get("request_count", 0), reverse=True)

    # ---- THE COMPLIANCE FINDING ----
    # Non-necessary cookies present BEFORE the click. Consent hadn't been
    # given yet, so under ePrivacy these should not exist.
    pre_violations = [
        c for c in pre_cookies if c.get("category") != "necessary"
    ]

    # A simple, honest verdict. We deliberately don't invent a second score —
    # one heuristic number per report is enough, and the counts speak clearly.
    if not pre_violations:
        verdict = "compliant"
        summary = ("No non-necessary cookies were set before consent. "
                   "This is the behaviour the law requires.")
    elif len(pre_violations) <= 3:
        verdict = "minor"
        summary = (f"{len(pre_violations)} non-necessary cookies were set "
                   f"before any consent was given.")
    else:
        verdict = "major"
        summary = (f"{len(pre_violations)} non-necessary cookies were set "
                   f"before any consent was given — consent was not obtained "
                   f"before tracking began.")

    return {
        "pre_consent_count": len(pre_cookies),
        "post_consent_count": len(post_cookies),
        "added_count": len(new_cookies),

        # How much MORE tracking accepting unlocks. Guard against dividing by
        # zero on a site with no pre-consent cookies at all.
        "multiplier": round(len(post_cookies) / len(pre_cookies), 1)
                      if pre_cookies else None,

        "added_by_category": new_by_category,
        "added_cookies": [
            {
                "name": c.get("name"),
                "domain": c.get("domain"),
                "category": c.get("category"),
                "vendor": c.get("vendor"),
                "party": c.get("party"),
                "lifetime_days": c.get("lifetime_days"),
            }
            for c in new_cookies
        ],
        "vendors_added": vendors_added,
        "domains_added": [
            {"domain": d.get("domain"), "request_count": d.get("request_count"),
             "category": d.get("category"), "vendor": d.get("vendor")}
            for d in domains_added[:25]
        ],

        # The compliance half.
        "pre_consent_violations": len(pre_violations),
        "violation_categories": count_by_category(pre_violations),
        "verdict": verdict,
        "summary": summary,
    }


def mark_post_consent_cookies(pre_cookies: list, post_cookies: list) -> list:
    """
    Return the post-consent cookie list with each entry flagged as to whether
    it appeared before or after the click.

    We store ONE cookie list per scan with a boolean flag, rather than two
    separate lists. That keeps the database schema unchanged in shape — the
    cookies table just gains one column — and makes "show me everything that
    needed consent" a simple WHERE clause instead of a set operation in
    Python.
    """
    pre_keys = {_cookie_key(c) for c in pre_cookies}
    return [
        {**c, "set_after_consent": _cookie_key(c) not in pre_keys}
        for c in post_cookies
    ]
