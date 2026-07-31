"""
CookieGuard — Domain utilities
===============================

WHY THIS FILE EXISTS
--------------------
Both `scan.py` and `classifier.py` need to answer one deceptively hard
question: **"what is the core domain of this hostname?"**

    www.bbc.co.uk            →  bbc.co.uk
    static.files.bbci.co.uk  →  bbci.co.uk
    shop.example.com         →  example.com

Getting this wrong breaks first-party vs third-party classification, which is
the foundation of the whole tool. So the logic lives in ONE place and both
files import it. Previously each file had its own copy — that's how the two
copies drift apart and start disagreeing.

THE PROBLEM WITH THE OBVIOUS APPROACH
-------------------------------------
The naive rule is "take the last two labels":

    www.example.com   →  ["www","example","com"]   →  "example.com"   ✅
    www.bbc.co.uk     →  ["www","bbc","co","uk"]   →  "co.uk"         ❌

`co.uk` is not a domain anybody owns. It's a **public suffix** — a slot under
which the public may register names, like `.com`.

Why that's dangerous for us: if we scan `bbc.co.uk`, our site domain becomes
`"co.uk"`. Then EVERY hostname ending in `.co.uk` — including trackers — looks
like a first-party domain of the site we're scanning. We would systematically
under-report third-party tracking on every UK site. This was logged as KI-1 and
confirmed on real BBC data.

THE REAL SOLUTION: THE PUBLIC SUFFIX LIST
-----------------------------------------
There is no rule that derives this. `co.uk` is a public suffix and `bbc.co.uk`
is not, purely because of how the UK registry chose to organise itself. Every
country did something different:

    example.com          public suffix = com          →  example.com
    example.co.uk        public suffix = co.uk        →  example.co.uk
    example.com.au       public suffix = com.au       →  example.com.au
    example.co.in        public suffix = co.in        →  example.co.in
    example.github.io    public suffix = github.io    →  example.github.io

So the only correct approach is a **lookup table**, and Mozilla maintains the
authoritative one: the Public Suffix List (publicsuffix.org). Browsers use it
to decide which domains may set cookies for which other domains — it is
literally part of how cookie security works.

We use the `tldextract` library, which bundles a snapshot of that list.

WHY WE FORCE THE OFFLINE SNAPSHOT
---------------------------------
By default `tldextract` tries to download a fresh copy of the list over the
network the first time it runs. We deliberately switch that off:

  * A scanner shouldn't make surprise network calls at import time.
  * It would fail or hang in Docker and in CI, where network access is
    restricted — exactly the environments Phases 6 and 7 target.
  * Reproducibility: everyone gets the same answers from the same snapshot.

The trade-off is that the bundled list ages. New public suffixes appear
occasionally, so the snapshot should be refreshed by upgrading the library
periodically. That's an acceptable, documented cost.
"""

import tldextract

# Build ONE extractor and reuse it for every call.
#
# `suffix_list_urls=()` — an empty tuple means "no URLs to fetch from", which
#   forces tldextract to use the snapshot bundled inside the package.
# `cache_dir=None` — don't write a cache directory to disk. Nothing to fetch,
#   so nothing to cache; this also avoids permission errors in a container.
#
# Creating this at module level (rather than inside the function) matters:
# constructing an extractor parses the suffix list, which is slow. Doing it
# once at import and reusing it means a scan with 200 cookies pays that cost
# once, not 200 times.
_extract = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)


def registrable_domain(hostname: str) -> str:
    """
    Reduce a hostname to the domain somebody actually registered.

        "www.bbc.co.uk"            ->  "bbc.co.uk"
        "static.files.bbci.co.uk"  ->  "bbci.co.uk"
        ".doubleclick.net"         ->  "doubleclick.net"
        "localhost"                ->  "localhost"

    The proper name for the result is the "registrable domain" or
    "eTLD+1" — the effective top-level domain plus one label.

    Parameters
    ----------
    hostname : a host such as "www.example.com". May carry a leading dot,
               which is how cookie domains are often written (".example.com"
               means "this domain and all its subdomains").

    Returns
    -------
    The registrable domain, lowercased. If the input has no recognised public
    suffix — "localhost", an IP address, an internal hostname — we return the
    cleaned input unchanged rather than an empty string, so callers always get
    something usable to compare.
    """
    if not hostname:
        return ""

    # Strip the leading dot from cookie-style domains, drop any port number,
    # and lowercase (domain names are case-insensitive).
    hostname = hostname.lstrip(".").lower().split(":")[0]

    # tldextract splits a hostname into three parts:
    #     "www.bbc.co.uk"  ->  subdomain="www", domain="bbc", suffix="co.uk"
    result = _extract(hostname)

    # `top_domain_under_public_suffix` joins domain + suffix -> "bbc.co.uk".
    # It returns an empty string when there is no recognised public suffix
    # (e.g. "localhost", "192.168.1.1", "my-internal-server").
    #
    # getattr() with a fallback because this property was renamed in a recent
    # tldextract release; older versions call it `registered_domain`. Handling
    # both means the code works across versions instead of breaking on upgrade.
    registered = getattr(result, "top_domain_under_public_suffix", None)
    if registered is None:
        registered = result.registered_domain

    # Fall back to the cleaned hostname so comparisons still work for
    # localhost, IPs and internal hostnames.
    return registered or hostname


def same_organisation(host_a: str, host_b: str) -> bool:
    """
    Do these two hostnames belong to the same registered domain?

        same_organisation("www.example.com", ".example.com")  ->  True
        same_organisation("www.example.com", "facebook.com")  ->  False

    This is the comparison behind first-party vs third-party classification.
    """
    return registrable_domain(host_a) == registrable_domain(host_b)
