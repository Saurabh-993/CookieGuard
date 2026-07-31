"""
CookieGuard — Jurisdiction mapping
===================================

WHAT THIS FILE DOES
-------------------
Maps a tracker vendor to the country its company is headquartered in, and
groups those countries into regions that matter under GDPR.

WHY THIS IS A REAL COMPLIANCE FEATURE, NOT DECORATION
-----------------------------------------------------
GDPR Chapter V restricts sending personal data OUTSIDE the European Economic
Area. Every third-party tracker on a European website is, by definition, a data
transfer — the visitor's IP address and browsing behaviour leave for that
company's servers.

That is not a technicality. Three things happened:

  * 2020 — "Schrems II". The EU Court of Justice struck down Privacy Shield,
    the framework that had legitimised EU→US data transfers. Overnight,
    thousands of sites using US analytics were on shaky legal ground.

  * 2022 — Austrian, French and Italian regulators each ruled that using
    Google Analytics breached GDPR, specifically because data went to the US.

  * 2023 — the EU–US Data Privacy Framework replaced Privacy Shield. It is
    already being challenged in court.

So "which countries does our data end up in?" is a question a privacy officer
genuinely has to answer — and answering it by hand means researching every
vendor individually.

HOW WE DETERMINE COUNTRY
------------------------
By company headquarters, using a curated lookup table. That is a deliberate
simplification, and it is important to be honest about why.

The legally precise question is "where are the SERVERS", not "where is the head
office". A US company may process EU data entirely in Frankfurt. Determining
that properly needs each vendor's published sub-processor list and data
residency commitments — documents, not code.

Headquarters is a reasonable PROXY because it usually determines which
government can compel disclosure of the data (for US companies, the CLOUD Act
applies regardless of where servers sit — which was central to the Schrems II
reasoning).

We label the output as an indicator requiring review, never as a legal finding.
Being explicit about the limits of your own tool is what separates a useful
instrument from a misleading one.
"""

# ---------------------------------------------------------------------------
# REGIONS
# ---------------------------------------------------------------------------
# The distinction that matters legally is "inside the EEA" vs "outside it".
# We split "outside" further because the risk profile differs a lot:
#
#   EEA        no transfer restrictions apply — data stays in scope
#   ADEQUATE   country has an EU adequacy decision (UK, Switzerland, Japan,
#              Canada...). Transfers are permitted without extra safeguards.
#   DPF        US companies certified under the EU-US Data Privacy Framework.
#              Permitted, but the framework is under active legal challenge.
#   RESTRICTED no adequacy decision. Transfers need Standard Contractual
#              Clauses plus a documented transfer impact assessment.

REGION_EEA = "EEA"
REGION_ADEQUATE = "Adequate"
REGION_DPF = "US (DPF)"
REGION_RESTRICTED = "Restricted"

# Country code -> (display name, region, ISO 3166-1 NUMERIC code)
#
# WHY THE NUMERIC CODE IS HERE
# ----------------------------
# The D3 world map draws countries from a TopoJSON file (Natural Earth via the
# `world-atlas` package). Every country in that file is identified by its ISO
# 3166-1 **numeric** id — 840 for the USA, 250 for France — not by name and not
# by the two-letter code.
#
# Matching by NAME would be fragile: the atlas calls the US "United States of
# America" while we call it "United States", and there are dozens of similar
# mismatches ("Russia" vs "Russian Federation", "Czechia" vs "Czech Republic").
# Numeric ids are unambiguous and stable, so we carry them alongside.

COUNTRIES = {
    # --- European Economic Area ---
    "FR": ("France", REGION_EEA, "250"),
    "DE": ("Germany", REGION_EEA, "276"),
    "NL": ("Netherlands", REGION_EEA, "528"),
    "SE": ("Sweden", REGION_EEA, "752"),
    "DK": ("Denmark", REGION_EEA, "208"),
    "NO": ("Norway", REGION_EEA, "578"),          # EEA, though not EU
    "IE": ("Ireland", REGION_EEA, "372"),
    "PL": ("Poland", REGION_EEA, "616"),
    "LT": ("Lithuania", REGION_EEA, "440"),
    "MT": ("Malta", REGION_EEA, "470"),
    "LU": ("Luxembourg", REGION_EEA, "442"),
    "SI": ("Slovenia", REGION_EEA, "705"),
    "ES": ("Spain", REGION_EEA, "724"),
    "IT": ("Italy", REGION_EEA, "380"),
    "FI": ("Finland", REGION_EEA, "246"),
    "BE": ("Belgium", REGION_EEA, "056"),
    "AT": ("Austria", REGION_EEA, "040"),

    # --- Adequacy decisions ---
    "UK": ("United Kingdom", REGION_ADEQUATE, "826"),
    "CH": ("Switzerland", REGION_ADEQUATE, "756"),
    "CA": ("Canada", REGION_ADEQUATE, "124"),
    "JP": ("Japan", REGION_ADEQUATE, "392"),
    "NZ": ("New Zealand", REGION_ADEQUATE, "554"),
    "IL": ("Israel", REGION_ADEQUATE, "376"),
    "KR": ("South Korea", REGION_ADEQUATE, "410"),

    # --- United States ---
    "US": ("United States", REGION_DPF, "840"),

    # --- No adequacy decision ---
    "CN": ("China", REGION_RESTRICTED, "156"),
    "SG": ("Singapore", REGION_RESTRICTED, "702"),
    "TW": ("Taiwan", REGION_RESTRICTED, "158"),
    "RS": ("Serbia", REGION_RESTRICTED, "688"),
    "AU": ("Australia", REGION_RESTRICTED, "036"),
    "IN": ("India", REGION_RESTRICTED, "356"),
    "RU": ("Russia", REGION_RESTRICTED, "643"),
    "BR": ("Brazil", REGION_RESTRICTED, "076"),

    # No numeric id — deliberately unmappable, so it never colours a country.
    "XX": ("Unknown", REGION_RESTRICTED, None),
}


# ---------------------------------------------------------------------------
# VENDOR -> COUNTRY
# ---------------------------------------------------------------------------
# Keys are lowercase substrings matched against the vendor name stored on each
# cookie. Substring matching (rather than exact) copes with the variants that
# appear in trackers.json: "Google Analytics", "Google Analytics 4",
# "Google Ad Manager" and "Google DoubleClick" should all resolve to Google.
#
# ORDER MATTERS. We check longest key first so "piano / cxense" wins over
# "piano". Same specificity rule as the cookie prefixes in §28 and the domain
# signatures in §33 — the third time this exact pattern has come up, which is
# a decent hint that "sort by specificity before matching" is a general rule
# worth internalising.

VENDOR_COUNTRY = {
    # --- United States ---
    "google": "US",
    "meta": "US",
    "facebook": "US",
    "microsoft": "US",
    "amazon": "US",
    "linkedin": "US",
    "x (twitter)": "US",
    "yahoo": "US",
    "adobe": "US",
    "optimizely": "US",
    "chartbeat": "US",
    "comscore": "US",
    "nielsen": "US",
    "lotame": "US",
    "liveramp": "US",
    "mediamath": "US",
    "amobee": "US",
    "gumgum": "US",
    "pinterest": "US",
    "snap": "US",
    "reddit": "US",
    "hubspot": "US",
    "segment": "US",
    "mixpanel": "US",
    "amplitude": "US",
    "new relic": "US",
    "sentry": "US",
    "quantcast": "US",
    "pubmatic": "US",
    "magnite": "US",
    "openx": "US",
    "the trade desk": "US",
    "doubleverify": "US",
    "integral ad science": "US",
    "bombora": "US",
    "conversant": "US",
    "dotomi": "US",
    "adkernel": "US",
    "cloudflare": "US",
    "imperva": "US",
    "intercom": "US",
    "zendesk": "US",
    "zopim": "US",
    "sourcepoint": "US",
    "onetrust": "US",
    "triplelift": "US",
    "rhythmone": "US",
    "beeswax": "US",
    "bidr": "US",
    "bidswitch": "US",
    "tapad": "US",
    "simpli.fi": "US",
    "wunderkind": "US",
    "bounce exchange": "US",
    "rezync": "US",
    "prebid": "US",
    "ladsp": "US",
    "exponential": "US",
    "tribal fusion": "US",
    "vimeo": "US",
    "wordpress": "US",
    "php": "US",
    "cnn": "US",
    "warner bros": "US",
    "wbd": "US",
    "wikimedia": "US",
    "mux": "US",
    "unpkg": "US",
    "jsdelivr": "US",
    "next.js": "US",
    "vercel": "US",
    "snowplow": "US",
    "web content assessor": "US",
    "blockthrough": "CA",
    "stackadapt": "CA",
    "index exchange": "CA",
    "casale": "CA",

    # --- European Economic Area ---
    "criteo": "FR",
    "equativ": "FR",
    "smart adserver": "FR",
    "stickyads": "FR",
    "didomi": "FR",
    "id5": "FR",
    "teads": "FR",
    "adform": "DK",
    "cookiebot": "DK",
    "usercentrics": "DE",
    "semasio": "DE",
    "matomo": "DE",
    "piwik": "DE",
    "rtb house": "PL",
    "eskimi": "LT",
    "hotjar": "MT",
    "cxense": "NO",
    "opera": "NO",
    "dotmetrics": "RS",

    # --- United Kingdom ---
    "bbc": "UK",
    "permutive": "UK",
    "unruly": "UK",
    "ozone": "UK",
    "edigitalresearch": "UK",
    "mediamelon": "UK",

    # --- Elsewhere ---
    "tiktok": "CN",
    "bytedance": "CN",
    "appier": "TW",
    "speedcurve": "NZ",
    "taboola": "IL",
    "outbrain": "IL",
    "temu": "CN",

    # --- Mixed / notable ---
    # Piano acquired Cxense (Norway) but is headquartered in the US.
    "piano / cxense": "US",
    "piano (tinypass)": "US",
    "piano analytics": "US",
    "piano": "US",
}

# Sort once at import: longest key first, so the most specific vendor match
# wins. Doing it here rather than per lookup means we pay the cost once.
_SORTED_VENDORS = sorted(VENDOR_COUNTRY.items(), key=lambda kv: len(kv[0]), reverse=True)


def country_for_vendor(vendor: str) -> str:
    """
    Return a two-letter country code for a vendor name, or 'XX' if unknown.

        country_for_vendor("Google Analytics 4")  ->  "US"
        country_for_vendor("Criteo")              ->  "FR"
        country_for_vendor("Some New Adtech Co")  ->  "XX"
    """
    if not vendor:
        return "XX"
    name = vendor.lower()
    for key, code in _SORTED_VENDORS:
        if key in name:
            return code
    return "XX"


def describe_country(code: str) -> dict:
    """
    Turn a country code into something the dashboard can render.

    `iso_numeric` is what the D3 world map joins on — see the note on
    COUNTRIES above for why we don't match by name.
    """
    name, region, numeric = COUNTRIES.get(code, COUNTRIES["XX"])
    return {"code": code, "country": name, "region": region, "iso_numeric": numeric}


def summarise_data_flows(cookies: list) -> dict:
    """
    Given classified cookies, work out where the data goes.

    Returns per-country counts, per-region counts, and the headline number a
    compliance officer actually wants: how many cookies send data outside the
    EEA.

    We count only NON-NECESSARY cookies for the headline. A strictly necessary
    cookie is consent-exempt, and its transfer is generally covered by the
    contractual necessity of providing the service — so including it would
    inflate the number and make the metric less useful.
    """
    by_country = {}
    by_region = {}

    for cookie in cookies:
        vendor = cookie.get("vendor") or "Unknown"
        code = country_for_vendor(vendor)
        info = describe_country(code)

        entry = by_country.setdefault(code, {
            **info, "cookie_count": 0, "vendors": set(),
        })
        entry["cookie_count"] += 1
        if vendor and vendor != "Unknown":
            entry["vendors"].add(vendor)

        by_region[info["region"]] = by_region.get(info["region"], 0) + 1

    # Sets don't serialise to JSON, so convert to sorted lists.
    countries = []
    for entry in by_country.values():
        countries.append({
            **{k: v for k, v in entry.items() if k != "vendors"},
            "vendors": sorted(entry["vendors"])[:6],
            "vendor_count": len(entry["vendors"]),
        })
    countries.sort(key=lambda c: c["cookie_count"], reverse=True)

    outside_eea = sum(
        c["cookie_count"] for c in countries if c["region"] != REGION_EEA
    )
    total = sum(c["cookie_count"] for c in countries)

    # ---- Per-vendor breakdown ----
    # The globe answers "which countries", but a compliance officer also needs
    # "which VENDOR is sending data where" — that's the row you'd put in a
    # Record of Processing Activities. Same data, different question.
    by_vendor = {}
    for cookie in cookies:
        vendor = cookie.get("vendor") or "Unknown"
        code = country_for_vendor(vendor)
        info = describe_country(code)
        entry = by_vendor.setdefault(vendor, {
            "vendor": vendor,
            "country": info["country"],
            "code": code,
            "region": info["region"],
            "cookie_count": 0,
            "categories": set(),
        })
        entry["cookie_count"] += 1
        if cookie.get("category"):
            entry["categories"].add(cookie["category"])

    vendors = [
        {**{k: v for k, v in e.items() if k != "categories"},
         "categories": sorted(e["categories"])}
        for e in by_vendor.values()
    ]
    vendors.sort(key=lambda v: v["cookie_count"], reverse=True)

    return {
        "countries": countries,
        "vendors": vendors,
        "regions": [
            {"region": r, "cookie_count": n}
            for r, n in sorted(by_region.items(), key=lambda kv: -kv[1])
        ],
        "total_cookies": total,
        "outside_eea": outside_eea,
        "outside_eea_pct": round(100 * outside_eea / total, 1) if total else 0.0,
    }
