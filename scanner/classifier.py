"""
CookieGuard — Classifier (Phase 2a)
====================================

WHAT THIS FILE DOES
-------------------
`scan.py` tells us WHAT cookies exist. This file works out WHAT THEY ARE FOR.

It takes the output of `scan_website()` and adds, to every cookie:

    "category"   : necessary | functional | analytics | marketing | unknown
    "vendor"     : who set it, e.g. "Google Analytics"
    "purpose"    : a plain-English explanation
    "matched_by" : HOW we decided — so a human can audit our reasoning

It then computes a per-domain compliance summary and a score.

WHY THIS MATTERS
----------------
A cookie called `_ga` means nothing on its own. Categorisation is the step that
turns a raw technical list into something a compliance officer can act on,
because the law treats each category differently: only "necessary" cookies may
be set before the user consents.

THE CORE IDEA: A SIGNATURE DATABASE
-----------------------------------
We do not guess. We compare each cookie against a list of known signatures in
`trackers.json` — much like an antivirus scanner matching file hashes against a
list of known malware.

    cookie name "_ga"  ──▶  look up in trackers.json  ──▶  "analytics, Google"

Keeping that list in a JSON *data* file rather than in Python *code* is
deliberate: tracker lists change constantly, application logic does not. Anyone
can add a tracker without touching a line of Python.

RUN IT LIKE THIS
----------------
    python scanner/classifier.py data/bbc.json
    python scanner/classifier.py data/bbc.json --output data/bbc_classified.json
"""

import argparse
import json
import re  # regular expressions — pattern matching for text
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------

# The five categories. Order matters for display: we show them from least to
# most privacy-invasive, which is also roughly the order of legal concern.
CATEGORIES = ["necessary", "functional", "analytics", "marketing", "unknown"]

# `__file__` is the path to THIS file. `.parent` is the folder containing it.
# So this resolves to scanner/trackers.json no matter what directory the user
# runs the script from. Using a path relative to the script (not to the current
# working directory) is what makes `python scanner/classifier.py` work from
# anywhere — a small detail that prevents a very common class of bug.
TRACKERS_PATH = Path(__file__).parent / "trackers.json"

# A cookie living longer than this is flagged. France's CNIL recommends a
# 13-month maximum for analytics cookies; we round up to ~13 months in days.
EXCESSIVE_LIFETIME_DAYS = 400


# ---------------------------------------------------------------------------
# LOADING THE SIGNATURE DATABASE
# ---------------------------------------------------------------------------

def load_trackers(path: Path = TRACKERS_PATH) -> dict:
    """
    Read trackers.json from disk and return it as a Python dict.

    We load it ONCE and pass it around, rather than re-reading the file for
    every cookie. Reading a file is slow (it hits the disk); a site might have
    80 cookies, and 80 disk reads to answer the same question would be wasteful.
    """
    # `encoding="utf-8"` so non-English characters in vendor names survive.
    with open(path, "r", encoding="utf-8") as f:
        # json.load (no "s") reads from a FILE.
        # json.loads (with "s") reads from a STRING.
        return json.load(f)


# ---------------------------------------------------------------------------
# DOMAIN HELPERS
# ---------------------------------------------------------------------------
# We duplicate this small function from scan.py rather than importing it, so
# the classifier can be used standalone (and unit-tested) without pulling in
# Playwright. It is four lines; the coupling would cost more than the copy.

def _registrable_domain(hostname: str) -> str:
    """Reduce 'www.google-analytics.com' to 'google-analytics.com'."""
    hostname = hostname.lstrip(".").lower()
    parts = hostname.split(".")
    if len(parts) <= 2:
        return hostname
    return ".".join(parts[-2:])


def sorted_domain_signatures(trackers: dict) -> list:
    """
    Return domain signatures sorted MOST SPECIFIC FIRST (longest domain first).

    WHY THIS EXISTS — a real bug found on live data
    -----------------------------------------------
    Scanning bbc.com produced this:

        static.files.bbci.co.uk            → necessary (BBC assets)   ✅
        mybbc-analytics.files.bbci.co.uk   → necessary (BBC assets)   ❌ WRONG

    The second one is an ANALYTICS endpoint — the word is right there in the
    hostname — but it was labelled `necessary`, the one consent-exempt
    category. It happened because `bbci.co.uk` appeared in the signature list
    and matched first, so the more specific analytics entry was never reached.

    Sorting longest-first makes the most specific signature win:

        mybbc-analytics.files.bbci.co.uk   (31 chars)  ← checked first
        bbci.co.uk                         (10 chars)  ← fallback

    This is the SAME bug class as the prefix-ordering issue in
    `classify_cookie` — whichever entry happens to sit earlier in a data file
    wins. Any "first match in a list" lookup where entries can overlap needs an
    explicit specificity rule, or behaviour depends on file ordering.
    """
    return sorted(
        trackers.get("domain_signatures", []),
        key=lambda s: len(s["domain"]),
        reverse=True,
    )


def _domain_matches(cookie_domain: str, signature_domain: str) -> bool:
    """
    Does this cookie's domain belong to this tracker?

    We must match SUBDOMAINS too. A cookie on `stats.g.doubleclick.net` should
    match the signature `doubleclick.net`.

        cookie: "stats.g.doubleclick.net"
        sig:    "doubleclick.net"          →  True

    But we must NOT match by simple substring, because that produces false
    positives:

        cookie: "notdoubleclick.net.evil.com"
        sig:    "doubleclick.net"          →  must be False

    So we require either an exact match, or that the cookie domain ENDS WITH
    "." + the signature. The leading dot is what makes it a real subdomain
    boundary rather than an accidental substring.
    """
    cookie_domain = cookie_domain.lstrip(".").lower()
    signature_domain = signature_domain.lower()
    return (
        cookie_domain == signature_domain
        or cookie_domain.endswith("." + signature_domain)
    )


# ---------------------------------------------------------------------------
# THE CLASSIFICATION ENGINE
# ---------------------------------------------------------------------------

def classify_cookie(cookie: dict, trackers: dict) -> dict:
    """
    Work out what a single cookie is for.

    Returns a dict with: category, vendor, purpose, matched_by, confidence.

    MATCHING PRECEDENCE — most specific first
    -----------------------------------------
    Order matters enormously. We check the most *specific* evidence first and
    fall back to weaker evidence only if nothing better matched.

        1. EXACT name match     "_ga"          → highest confidence
        2. PREFIX name match    "_ga_ABC123"   → high
        3. DOMAIN match         ".doubleclick.net" → medium
        4. GENERIC regex        "session_id"   → low (heuristic guess)
        5. UNKNOWN                             → none; needs human review

    WHY THIS ORDER: a cookie named `_fbp` sitting on `example.com` is still the
    Facebook Pixel — the NAME is stronger evidence than the domain. Checking
    domain first would mislabel it. Conversely a cookie we've never heard of,
    sitting on `doubleclick.net`, is almost certainly advertising even though
    its name tells us nothing — so domain is a useful fallback.
    """
    name = cookie.get("name") or ""
    domain = cookie.get("domain") or ""
    party = cookie.get("party", "first")

    sigs = trackers.get("cookie_signatures", [])

    # --- LEVEL 1: exact cookie-name match -----------------------------------
    # Strongest signal. The name IS the identifier.
    for sig in sigs:
        if sig["match"] == "exact" and name == sig["pattern"]:
            return {
                "category": sig["category"],
                "vendor": sig["vendor"],
                "purpose": sig["purpose"],
                "matched_by": f"exact name '{sig['pattern']}'",
                "confidence": "high",
            }

    # --- LEVEL 2: prefix cookie-name match ----------------------------------
    # For families of cookies with a variable suffix, e.g. GA4's
    # `_ga_ABC123XYZ` where the suffix is the property's measurement ID.
    #
    # We sort by pattern length DESCENDING so the most specific prefix wins.
    # Without this, if both "_ga" and "_gat" were prefixes, "_gat_xyz" might
    # match "_ga" first purely by list order — which would be a subtle,
    # order-dependent bug. Sorting makes the result deterministic.
    prefix_sigs = sorted(
        [s for s in sigs if s["match"] == "prefix"],
        key=lambda s: len(s["pattern"]),
        reverse=True,
    )
    for sig in prefix_sigs:
        if name.startswith(sig["pattern"]):
            return {
                "category": sig["category"],
                "vendor": sig["vendor"],
                "purpose": sig["purpose"],
                "matched_by": f"name prefix '{sig['pattern']}'",
                "confidence": "high",
            }

    # --- LEVEL 3: domain match ----------------------------------------------
    # We don't recognise the name, but we recognise WHO set it.
    # Sorted most-specific-first — see sorted_domain_signatures() for why.
    for sig in sorted_domain_signatures(trackers):
        if _domain_matches(domain, sig["domain"]):
            return {
                "category": sig["category"],
                "vendor": sig["vendor"],
                "purpose": sig["purpose"],
                "matched_by": f"domain '{sig['domain']}'",
                "confidence": "medium",
            }

    # --- LEVEL 4: generic regex heuristics ----------------------------------
    # Last resort before giving up. These match common NAMING CONVENTIONS
    # rather than specific products — e.g. anything that looks like a session
    # ID or a CSRF token.
    for sig in trackers.get("generic_patterns", []):
        # re.match tests the pattern against the start of the string;
        # our patterns are anchored with ^...$ so they must match the whole name.
        # re.IGNORECASE because cookie naming is wildly inconsistent in the wild
        # (SESSIONID, sessionid, SessionId all appear).
        if re.match(sig["pattern"], name, re.IGNORECASE):

            # ⚠ IMPORTANT GUARD — a deliberate safety rule.
            #
            # A THIRD-PARTY cookie must never be labelled "necessary" by a
            # generic guess. "Necessary" is the one category exempt from
            # consent, so wrongly assigning it is the most damaging mistake
            # this classifier can make — it would tell a user "no consent
            # needed" when consent IS legally needed.
            #
            # Another company's cookie is by definition not necessary for OUR
            # site to function. So we downgrade the guess to "unknown" and
            # force a human to look at it.
            if sig["category"] == "necessary" and party == "third":
                return {
                    "category": "unknown",
                    "vendor": "Unknown",
                    "purpose": (
                        "Name resembles a necessary cookie, but it is set by a "
                        "third-party domain. Third-party cookies are not "
                        "necessary for this site to function. Requires review."
                    ),
                    "matched_by": "generic pattern, downgraded (third-party)",
                    "confidence": "low",
                }

            return {
                "category": sig["category"],
                "vendor": sig["vendor"],
                "purpose": sig["purpose"],
                "matched_by": f"generic pattern '{sig['pattern']}'",
                "confidence": "low",
            }

    # --- LEVEL 5: no match --------------------------------------------------
    # We deliberately return "unknown", NOT a guess of "necessary".
    #
    # This is the single most important design decision in the file. A
    # compliance tool that quietly assumes "probably fine" systematically
    # UNDER-reports risk, which is the worst possible failure mode: the user
    # believes they are compliant when they are not. Failing loudly is safer
    # than failing silently.
    return {
        "category": "unknown",
        "vendor": "Unknown",
        "purpose": "No signature matched. Requires manual review.",
        "matched_by": "no match",
        "confidence": "none",
    }


# ---------------------------------------------------------------------------
# COMPLIANCE SCORING
# ---------------------------------------------------------------------------

def calculate_compliance_score(cookies: list) -> dict:
    """
    Produce a 0–100 compliance score for a PRE-CONSENT scan.

    THE CRITICAL CONTEXT
    --------------------
    Our scanner visits the site and clicks NOTHING. So every cookie we found
    was set BEFORE the user consented to anything. Under the ePrivacy Directive
    and GDPR, only strictly necessary cookies are allowed at that moment.

        ▶ Therefore every NON-NECESSARY cookie we find is a potential violation.

    That is what this score measures. It is not a measure of how "good" the
    site is generally.

    THE FORMULA
    -----------
    Start at 100 and deduct, with DIMINISHING RETURNS per category:

        marketing   -15 first, then -4 each     (cap -65)
        analytics   -12 first, then -3 each     (cap -30)
        unknown      -5 each                    (cap -20)
        functional   -4 first, then -1 each     (cap -10)
        lifetime > 400 days        -2 each      (cap -10)
        third-party SameSite=None  -1 each      (cap -10)

    WHY DIMINISHING RETURNS: the jump from 0 marketing cookies to 1 is the
    legally meaningful event — that's the moment a violation exists. Going from
    10 to 11 barely changes the legal position. A flat per-cookie penalty would
    give every ad-funded news site a score of 0, making the number useless for
    comparison. Weighting the first occurrence heavily preserves that signal.

    WHY THESE WEIGHTS: marketing outranks analytics because it involves
    cross-site profiling and draws the strictest regulatory scrutiny. Its cap
    is deliberately high enough (-65) that marketing cookies ALONE can push a
    site into an F. That is intentional: dozens of advertising cookies dropped
    before any consent is a failing result, and a cap that prevented an F would
    have made the score dishonest.

    CALIBRATION NOTE: the first version of this formula capped marketing at
    -40, which meant a site with 30 pre-consent marketing cookies still scored
    60 ("Fair"). A unit test caught it. The caps were raised so that the
    egregious case actually fails. Tests catching a *calibration* bug — not a
    crash — is a good illustration of why they're worth writing.

    HONEST CAVEAT — say this in an interview before anyone else does:
    these weights are a defensible heuristic, not a legal standard. No
    regulator publishes a scoring formula. The score's value is in making sites
    *comparable* and trends *visible*. The hard legal fact is the separate
    `cookies_requiring_consent` count — that number is either zero or it isn't.
    """
    # Count how many cookies fall into each category.
    counts = {c: 0 for c in CATEGORIES}
    for c in cookies:
        cat = c.get("category", "unknown")
        counts[cat] = counts.get(cat, 0) + 1

    deductions = []          # human-readable list of why points were lost
    total_deduction = 0

    def apply(n, first, each, cap, label):
        """
        Deduct points for `n` occurrences, with the first weighted heavier.
        A closure — it reads `deductions` and `total_deduction` from the
        enclosing function's scope.
        """
        nonlocal total_deduction
        if n == 0:
            return
        # First occurrence costs `first`; every extra costs `each`.
        raw = first + (n - 1) * each
        # min() enforces the cap so one bad category can't sink the whole score.
        amount = min(raw, cap)
        total_deduction += amount
        deductions.append({"reason": label, "count": n, "points": -amount})

    apply(counts["marketing"], 15, 4, 65,
          "Marketing cookies set before consent")
    apply(counts["analytics"], 12, 3, 30,
          "Analytics cookies set before consent")
    apply(counts["unknown"], 5, 5, 20,
          "Unclassified cookies requiring manual review")
    apply(counts["functional"], 4, 1, 10,
          "Functional cookies set before consent")

    # Excessive lifetimes — a separate concern from category.
    long_lived = [
        c for c in cookies
        if (c.get("lifetime_days") or 0) > EXCESSIVE_LIFETIME_DAYS
    ]
    apply(len(long_lived), 2, 2, 10,
          f"Cookies lasting over {EXCESSIVE_LIFETIME_DAYS} days")

    # Third-party + SameSite=None is the clearest technical fingerprint of a
    # cross-site tracker: another company's cookie, sent on every embedded
    # request anywhere on the web.
    cross_site = [
        c for c in cookies
        if c.get("party") == "third" and c.get("same_site") in ("None", None)
    ]
    apply(len(cross_site), 1, 1, 10,
          "Third-party cookies with SameSite=None (cross-site tracking)")

    # max(0, ...) floors the score at zero — negative scores would be silly.
    score = max(0, 100 - total_deduction)

    # Convert the number into a grade. Easier to read at a glance than "63".
    if score >= 90:
        grade, verdict = "A", "Excellent — little or no pre-consent tracking"
    elif score >= 75:
        grade, verdict = "B", "Good — minor issues to address"
    elif score >= 60:
        grade, verdict = "C", "Fair — several cookies set before consent"
    elif score >= 40:
        grade, verdict = "D", "Poor — significant pre-consent tracking"
    else:
        grade, verdict = "F", "Failing — extensive tracking before any consent"

    return {
        "score": score,
        "grade": grade,
        "verdict": verdict,
        "deductions": deductions,
        # Under ePrivacy, only necessary cookies may be set pre-consent.
        # So this count is the headline compliance number.
        "cookies_requiring_consent": (
            counts["functional"] + counts["analytics"]
            + counts["marketing"] + counts["unknown"]
        ),
    }


# ---------------------------------------------------------------------------
# CLASSIFYING A WHOLE SCAN
# ---------------------------------------------------------------------------

def classify_scan(scan_result: dict, trackers: dict = None) -> dict:
    """
    Take a full scan result from scan.py and return an enriched copy.

    Adds to the top level:
        "categories"          — counts per category
        "compliance"          — score, grade, deductions
        "third_party_domains" — each entry gains category + vendor

    And to every cookie:
        "category", "vendor", "purpose", "matched_by", "confidence"

    NOTE: we build a NEW dict rather than mutating the input. Functions that
    quietly modify their arguments are a classic source of confusing bugs —
    the caller's data changes without them asking. Returning a fresh object
    makes the data flow obvious.
    """
    if trackers is None:
        trackers = load_trackers()

    # Shallow copy of the top level.
    result = dict(scan_result)

    # Classify every cookie, building a brand-new list.
    classified_cookies = []
    for cookie in scan_result.get("cookies", []):
        info = classify_cookie(cookie, trackers)
        # `{**a, **b}` merges two dicts. Keys in `b` win on collision.
        # So we keep every original field and add the classification fields.
        classified_cookies.append({**cookie, **info})
    result["cookies"] = classified_cookies

    # Count per category.
    counts = {c: 0 for c in CATEGORIES}
    for c in classified_cookies:
        counts[c["category"]] = counts.get(c["category"], 0) + 1
    result["categories"] = counts

    # Also categorise the third-party domains the page contacted. Remember,
    # these can be trackers even when they set no cookie at all — that is the
    # tracking-pixel case, and it is exactly why we captured requests in
    # Phase 1.
    # Sort once, outside the loop — no point re-sorting for every domain.
    domain_sigs = sorted_domain_signatures(trackers)
    enriched_domains = []
    for entry in scan_result.get("third_party_domains", []):
        match = None
        for sig in domain_sigs:
            if _domain_matches(entry["domain"], sig["domain"]):
                match = sig
                break
        enriched_domains.append({
            **entry,
            "category": match["category"] if match else "unknown",
            "vendor": match["vendor"] if match else "Unknown",
        })
    result["third_party_domains"] = enriched_domains

    # Score it.
    result["compliance"] = calculate_compliance_score(classified_cookies)

    return result


# ---------------------------------------------------------------------------
# TERMINAL REPORT
# ---------------------------------------------------------------------------

def print_classified_report(result: dict) -> None:
    """Print a readable compliance report for a classified scan."""
    line = "=" * 78
    sub = "-" * 78

    # Small visual markers so categories are scannable at a glance.
    marks = {
        "necessary": "[OK]", "functional": "[  ]",
        "analytics": "[! ]", "marketing": "[!!]", "unknown": "[??]",
    }

    comp = result["compliance"]

    print(f"\n{line}")
    print("  CookieGuard — Compliance Report")
    print(line)
    print(f"  Domain      : {result.get('domain')}")
    print(f"  Scanned at  : {result.get('scanned_at')}")
    print(f"  Total cookies: {result.get('cookie_count', len(result.get('cookies', [])))}")

    # --- Score block ---
    print(f"\n  COMPLIANCE SCORE: {comp['score']}/100   Grade: {comp['grade']}")
    print(f"  {comp['verdict']}")
    print(f"\n  Cookies set BEFORE consent that legally require it: "
          f"{comp['cookies_requiring_consent']}")

    if comp["deductions"]:
        print(f"\n  Points deducted for:")
        for d in comp["deductions"]:
            print(f"    {d['points']:>4}  {d['reason']} (x{d['count']})")

    # --- Category breakdown ---
    print(f"\n  CATEGORY BREAKDOWN")
    print(f"  {sub}")
    for cat in CATEGORIES:
        n = result["categories"].get(cat, 0)
        # A simple text bar chart — one block per cookie.
        bar = "#" * min(n, 40)
        consent = "consent required" if cat != "necessary" else "exempt"
        if cat == "unknown":
            consent = "REVIEW NEEDED"
        print(f"  {marks[cat]} {cat.capitalize():<12}{n:>3}  {bar}")
        if n:
            print(f"       {' ' * 12}     ({consent})")

    # --- Cookie table ---
    print(f"\n  COOKIE INVENTORY")
    print(f"  {sub}")
    if result["cookies"]:
        print(f"  {'NAME':<24}{'CATEGORY':<12}{'VENDOR':<24}{'PARTY':<7}{'MATCHED BY'}")
        # Sort so the riskiest cookies appear at the top of the table.
        risk_order = {"marketing": 0, "unknown": 1, "analytics": 2,
                      "functional": 3, "necessary": 4}
        for c in sorted(result["cookies"],
                        key=lambda x: risk_order.get(x["category"], 9)):
            print(f"  {str(c['name'])[:23]:<24}"
                  f"{c['category']:<12}"
                  f"{str(c['vendor'])[:23]:<24}"
                  f"{c['party']:<7}"
                  f"{c['matched_by'][:28]}")
    else:
        print("  (no cookies found)")

    # --- Third-party domains ---
    tpd = result.get("third_party_domains", [])
    if tpd:
        print(f"\n  THIRD-PARTY DOMAINS CONTACTED: {len(tpd)}")
        print(f"  {sub}")
        print(f"  {'DOMAIN':<38}{'CATEGORY':<12}{'VENDOR':<20}{'REQS'}")
        for e in tpd[:20]:
            print(f"  {e['domain'][:37]:<38}"
                  f"{e['category']:<12}"
                  f"{str(e['vendor'])[:19]:<20}"
                  f"{e['request_count']}")
        if len(tpd) > 20:
            print(f"  ... and {len(tpd) - 20} more")

    # --- Unknowns need a human ---
    unknowns = [c for c in result["cookies"] if c["category"] == "unknown"]
    if unknowns:
        print(f"\n  ⚠  {len(unknowns)} COOKIE(S) NEED MANUAL REVIEW")
        print(f"  {sub}")
        for c in unknowns[:10]:
            print(f"    {c['name']}  (domain: {c['domain']}, {c['party']}-party)")
        print("\n  These matched no signature. Look them up and, if you identify")
        print("  them, add an entry to scanner/trackers.json.")

    print(f"\n  NOTE: automated classification is a technical aid, not legal advice.")
    print(f"{line}\n")


# ---------------------------------------------------------------------------
# COMMAND-LINE ENTRY POINT
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="CookieGuard — classify the cookies in a saved scan result.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "scan_file",
        help="Path to a JSON file produced by scan.py --output",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional path to save the classified result as JSON",
    )
    args = parser.parse_args()

    scan_path = Path(args.scan_file)
    if not scan_path.exists():
        print(f"[CookieGuard] ERROR: file not found — {scan_path}")
        print("[CookieGuard] Produce one first with:")
        print("    python scanner/scan.py https://example.com --output data/example.json")
        return 1

    try:
        scan_result = json.loads(scan_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"[CookieGuard] ERROR: {scan_path} is not valid JSON — {e}")
        return 1

    trackers = load_trackers()
    n_sigs = (len(trackers.get("cookie_signatures", []))
              + len(trackers.get("domain_signatures", []))
              + len(trackers.get("generic_patterns", [])))
    print(f"\n[CookieGuard] Loaded {n_sigs} signatures from trackers.json")

    classified = classify_scan(scan_result, trackers)
    print_classified_report(classified)

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(classified, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"[CookieGuard] Classified result saved to {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
