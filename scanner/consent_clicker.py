"""
CookieGuard — Consent banner clicker
=====================================

WHAT THIS FILE DOES
-------------------
Finds the "Accept all" button on a website's cookie banner, and clicks it.

WHY THIS IS THE MOST VALUABLE FEATURE IN THE PROJECT
----------------------------------------------------
Until now CookieGuard only saw the **pre-consent** state. That answers
"is this site compliant?" — useful, but one-sided.

Clicking accept and scanning again answers a much better question:

        "What does accepting actually cost you?"

That diff is what a privacy officer, a journalist or a regulator genuinely
wants. "This site sets 4 cookies before consent and 61 after" is a headline.
"This site sets 4 cookies" is a footnote.

It also lets us check a claim sites make constantly: *"we only load analytics
after you agree."* Now we can verify it instead of believing it.

WHY THIS IS HARD
----------------
**There is no standard.** No `<button rel="accept-cookies">`, no agreed API,
no convention. Every consent platform invented its own markup, and thousands
of sites hand-rolled their own.

So we use a LAYERED STRATEGY, most reliable first:

    1. Known CMP selectors     exact ids used by OneTrust, Cookiebot, etc.
    2. Text matching           any button whose text says "accept all"
    3. Frames                  some CMPs render inside an <iframe>
    4. Give up honestly        report that no banner was found

That layering is the interesting engineering here. Each layer is more general
and less reliable than the one above it, and we report WHICH layer succeeded
so the result stays auditable — the same principle as the classifier's
`matched_by` field.

⚠ AN HONEST LIMITATION
----------------------
This will not work on every site. Some banners are inside closed shadow DOM,
some require scrolling, some appear only after a delay, some are in languages
we don't pattern-match. When we can't find one, we say so rather than
pretending the site has no banner — reporting "not found" is very different
from reporting "no tracking added".
"""

import re


# ---------------------------------------------------------------------------
# LAYER 1: KNOWN CONSENT MANAGEMENT PLATFORMS
# ---------------------------------------------------------------------------
# A handful of vendors cover a large share of the web. Their markup is stable
# and documented, so an exact selector is by far the most reliable signal.
#
# Ordered roughly by market share. First match wins.

CMP_SELECTORS = [
    # --- OneTrust (the market leader, and the product the target job uses) ---
    ("OneTrust", "#onetrust-accept-btn-handler"),
    ("OneTrust", "button.onetrust-close-btn-handler.banner-close-button"),
    ("OneTrust", "#accept-recommended-btn-handler"),

    # --- Cookiebot ---
    ("Cookiebot", "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll"),
    ("Cookiebot", "#CybotCookiebotDialogBodyButtonAccept"),

    # --- Didomi ---
    ("Didomi", "#didomi-notice-agree-button"),
    ("Didomi", "button.didomi-continue-without-agreeing"),

    # --- Quantcast / IAB TCF ---
    ("Quantcast", "button.qc-cmp2-summary-buttons > button[mode='primary']"),
    ("Quantcast", ".qc-cmp2-summary-buttons button:nth-child(2)"),

    # --- Usercentrics ---
    ("Usercentrics", "button[data-testid='uc-accept-all-button']"),
    ("Usercentrics", "#uc-btn-accept-banner"),

    # --- Sourcepoint (used by BBC and many publishers) ---
    # Sourcepoint renders inside an <iframe>, so these are mostly reached by
    # Layer 3. `sp_choice_type_11` is its "accept all" choice id.
    ("Sourcepoint", "button[title='Accept all']"),
    ("Sourcepoint", "button[title='Yes, I agree']"),
    ("Sourcepoint", "button[title=\"Yes, I'm happy\"]"),
    ("Sourcepoint", ".sp_choice_type_11"),
    ("Sourcepoint", "button.sp_choice_type_ACCEPT_ALL"),
    ("Sourcepoint", "button[aria-label='Accept all']"),

    # --- TrustArc ---
    ("TrustArc", "#truste-consent-button"),

    # --- Osano ---
    ("Osano", "button.osano-cm-accept-all"),

    # --- Klaro / Cookie Script / Termly ---
    ("Klaro", ".cm-btn-success"),
    ("CookieScript", "#cookiescript_accept"),
    ("Termly", "#termly-code-snippet-support button[data-tid='banner-accept']"),

    # --- Google Funding Choices ---
    ("Google FC", ".fc-cta-consent"),

    # --- BBC's own implementation ---
    ("BBC", "button[data-testid='banner-accept']"),

    # --- Generic but common conventions ---
    ("Generic", "button#accept-all-cookies"),
    ("Generic", "button[aria-label='Accept all cookies']"),
    ("Generic", "[data-cookiebanner='accept_button']"),
]


# ---------------------------------------------------------------------------
# LAYER 2: TEXT PATTERNS
# ---------------------------------------------------------------------------
# When no known selector matches, look at what buttons SAY.
#
# ⚠ ORDER MATTERS ENORMOUSLY HERE, and getting it wrong is dangerous.
#
# "Accept all" and "Accept necessary only" both contain the word "accept".
# If we matched loosely we could click REJECT while believing we clicked
# accept — and then report that consent adds no cookies, which would be
# exactly backwards.
#
# So: most specific phrases first, and an explicit blocklist of phrases that
# mean the opposite.

ACCEPT_PATTERNS = [
    r"^accept all( cookies)?$",
    r"^allow all( cookies)?$",
    r"^accept all and continue$",
    r"^i accept all$",
    r"^agree to all$",
    r"^accept cookies$",
    r"^allow cookies$",
    r"^accept$",
    r"^i agree$",
    r"^agree$",
    r"^got it$",
    r"^ok, got it$",
    r"^understood$",
    r"^continue$",
    r"^yes, i agree$",
    r"^allow$",
]

# Regexes for `get_by_role(name=...)`, which matches the ACCESSIBLE NAME.
# Compiled with re.I because banner text casing is arbitrary.
# Ordered most specific first — "Accept all" before bare "Accept".
ACCESSIBLE_NAME_PATTERNS = [
    re.compile(r"^\s*accept all( cookies)?\s*$", re.I),
    re.compile(r"^\s*allow all( cookies)?\s*$", re.I),
    re.compile(r"^\s*agree to all\s*$", re.I),
    re.compile(r"^\s*yes,? i('m)? (agree|happy|accept)", re.I),
    re.compile(r"^\s*i accept( all)?\s*$", re.I),
    re.compile(r"^\s*accept( cookies)?\s*$", re.I),
    re.compile(r"^\s*allow( cookies)?\s*$", re.I),
    re.compile(r"^\s*i agree\s*$", re.I),
    re.compile(r"^\s*agree\s*$", re.I),
    re.compile(r"^\s*got it\s*$", re.I),
    re.compile(r"^\s*ok\s*$", re.I),
]

# If a button's text contains any of these it is NOT an accept-all button,
# whatever else it says. This blocklist is the safety net for Layer 2.
REJECT_INDICATORS = [
    "reject", "decline", "refuse", "deny", "necessary only",
    "essential only", "only necessary", "only essential", "manage",
    "customi", "settings", "preferences", "options", "more info",
    "learn more", "without agreeing", "do not", "don't",
]


# Containers whose appearance means "a banner is on screen". We wait for one
# of these before searching, because a banner that hasn't rendered yet is
# indistinguishable from no banner at all.
BANNER_CONTAINERS = [
    "#onetrust-banner-sdk", "#onetrust-consent-sdk",
    "#CybotCookiebotDialog", "#didomi-notice", ".qc-cmp2-container",
    "#usercentrics-root", "#truste-consent-track", ".osano-cm-window",
    "[id^='sp_message_container']", "[id^='sp_message_iframe']",
    "#cookiescript_injected", ".fc-consent-root", "#cmpbox",
    "[class*='cookie-banner']", "[class*='consent-banner']",
    "[id*='cookie-banner']", "[id*='consent']",
]


async def _wait_for_banner(page, timeout_ms: int) -> bool:
    """
    Wait until a consent banner is actually on screen.

    ⚠ WHY THIS EXISTS — it's why the first version failed on BBC and CNN.

    Banners are injected by JavaScript, often after an extra network round
    trip to the consent platform, and frequently with a fade-in animation.
    Searching immediately finds nothing and we report `not_found` — which is
    the worst possible wrong answer, because it looks like "this site has no
    banner" when really it means "we looked too early".

    Playwright's `wait_for_selector` with a comma-separated list resolves as
    soon as ANY of them appears, so this costs nothing when the banner is
    already there.
    """
    try:
        await page.wait_for_selector(
            ", ".join(BANNER_CONTAINERS), state="visible", timeout=timeout_ms
        )
        return True
    except Exception:
        # No known container. The banner may still exist with markup we don't
        # recognise, so we carry on and let the text search try.
        return False


async def _try_selectors(scope, selectors, result, method) -> bool:
    """
    Try a list of (vendor, selector) pairs against one page or frame.

    Factored out because Layer 1 and Layer 3 do exactly the same thing to
    different scopes. Two copies would drift.
    """
    for vendor, selector in selectors:
        try:
            element = scope.locator(selector).first
            if await element.is_visible(timeout=350):
                text = (await element.inner_text(timeout=500) or "").strip()
                await element.click(timeout=2500)
                result.update(clicked=True, method=method, detail=vendor,
                              selector=selector, text=text[:120])
                return True
        except Exception:
            continue
    return False


async def accept_consent(page, timeout_ms: int = 6000) -> dict:
    """
    Try to find and click an "accept all" button on `page`.

    Returns a dict describing what happened — never raises. A scan must not
    fail just because a banner couldn't be dismissed.

        {
          "clicked": True,
          "method": "cmp_selector",
          "detail": "OneTrust",
          "selector": "#onetrust-accept-btn-handler",
          "text": "Accept All Cookies",
        }

    Reporting HOW we clicked (not just whether) keeps the result auditable —
    the same reasoning as `matched_by` on a classified cookie. If a scan says
    consent added 40 cookies, you can check that we clicked the right thing.
    """
    result = {"clicked": False, "method": None, "detail": None,
              "selector": None, "text": None, "error": None,
              "candidates_seen": [], "frame_count": 0, "frame_urls": []}

    # ---- LAYER -1: are we even looking at the real site? --------------------
    # Cloudflare, Akamai and similar serve a bot-challenge page instead of the
    # site. It has no banner because it has no content — reporting "no banner
    # found" would be true but deeply misleading.
    #
    # StackOverflow does exactly this: HTTP 403, title "Just a moment...", and
    # the only domain contacted is challenges.cloudflare.com. That is a
    # completely different finding from "this site has no consent banner", and
    # conflating the two would make the whole scan worthless without anyone
    # noticing.
    try:
        title = (await page.title() or "").lower()
        CHALLENGE_TITLES = ["just a moment", "attention required",
                            "checking your browser", "access denied",
                            "verifying you are human", "one moment, please"]
        if any(t in title for t in CHALLENGE_TITLES):
            result.update(method="bot_challenge",
                          detail=f"Blocked by a bot challenge (page title: '{title[:60]}')")
            return result
    except Exception:
        pass

    # ---- LAYER 0: wait for the banner to actually appear --------------------
    # Without this we search before the banner has rendered and report
    # "not_found" on sites that plainly do have one. This was the bug that
    # made the first version fail on both BBC and CNN.
    found_container = await _wait_for_banner(page, timeout_ms)
    if found_container:
        # A container appeared, but it may still be animating in. Half a second
        # is enough for a CSS transition and costs nothing when there isn't one.
        await page.wait_for_timeout(600)

    # ---- LAYER 1: known CMP selectors on the main page ---------------------
    if await _try_selectors(page, CMP_SELECTORS, result, "cmp_selector"):
        return result

    # ---- LAYER 2: role + accessible name -----------------------------------
    # `get_by_role` is far better than querying for <button> elements:
    #
    #   * it PIERCES OPEN SHADOW DOM, which several CMPs use and which plain
    #     CSS selectors simply cannot see into
    #   * it matches the ACCESSIBLE NAME — the text a screen reader would
    #     announce — so it works for <div role="button"> and for buttons whose
    #     label comes from aria-label rather than visible text
    #
    # This is the layer that generalises to banners nobody has ever catalogued.
    for pattern in ACCESSIBLE_NAME_PATTERNS:
        try:
            element = page.get_by_role("button", name=pattern).first
            if await element.is_visible(timeout=400):
                text = " ".join((await element.inner_text(timeout=400) or "").split())
                # THE SAFETY CHECK — never click something that means the
                # opposite. "Accept all" and "Accept necessary only" both
                # contain "accept".
                if not any(bad in text.lower() for bad in REJECT_INDICATORS):
                    await element.click(timeout=2500)
                    result.update(clicked=True, method="accessible_name",
                                  detail=f"matched '{text}'", text=text[:120])
                    return result
        except Exception:
            continue

    # ---- LAYER 3: iframes ---------------------------------------------------
    # Sourcepoint (BBC), TrustArc and others render the banner inside an
    # <iframe>. Selectors on the main page cannot see into one, so every frame
    # has to be searched separately.
    #
    # The first version only tried the first 12 selectors here, which excluded
    # Sourcepoint entirely — the reason BBC failed.
    try:
        for frame in page.frames:
            if frame == page.main_frame:
                continue

            if await _try_selectors(frame, CMP_SELECTORS, result, "iframe"):
                return result

            # Text matching inside the frame too.
            for pattern in ACCESSIBLE_NAME_PATTERNS:
                try:
                    element = frame.get_by_role("button", name=pattern).first
                    if await element.is_visible(timeout=300):
                        text = " ".join(
                            (await element.inner_text(timeout=300) or "").split())
                        if any(bad in text.lower() for bad in REJECT_INDICATORS):
                            continue
                        await element.click(timeout=2500)
                        result.update(clicked=True, method="iframe_text",
                                      detail=f"matched '{text}'", text=text[:120])
                        return result
                except Exception:
                    continue
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"

    # ---- LAYER 4: plain element scan (last resort) --------------------------
    # Catches hand-rolled banners using <div onclick> with no role attribute,
    # which `get_by_role` won't see.
    try:
        candidates = page.locator(
            "button, a[role='button'], [role='button'], div[onclick], "
            "input[type='button'], input[type='submit']"
        )
        count = min(await candidates.count(), 150)
        for i in range(count):
            element = candidates.nth(i)
            try:
                if not await element.is_visible(timeout=150):
                    continue
                raw = (await element.inner_text(timeout=150)
                       or await element.get_attribute("value") or "")
            except Exception:
                continue

            text = " ".join(raw.split()).strip()
            if not text or len(text) > 60:
                continue
            lowered = text.lower()
            if any(bad in lowered for bad in REJECT_INDICATORS):
                continue
            if any(re.match(p, lowered) for p in ACCEPT_PATTERNS):
                try:
                    await element.click(timeout=2500)
                    result.update(clicked=True, method="text_match",
                                  detail=f"matched '{text}'", text=text)
                    return result
                except Exception:
                    continue
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"

    # ---- LAYER 5: DIAGNOSTICS -----------------------------------------------
    # We failed. Before giving up, record WHAT WE ACTUALLY SAW.
    #
    # ⚠ WHY THIS MATTERS MORE THAN ANOTHER GUESS AT A SELECTOR
    #
    # A detector that reports "not found" gives you no information about WHY.
    # Is the banner absent? Present but in a shadow root? Present with text we
    # don't recognise? Geo-gated away? Each has a different fix, and you cannot
    # tell them apart from the outside.
    #
    # So when we fail, we capture every visible clickable element's text, plus
    # the frame list. One run then answers the question that would otherwise
    # take several rounds of guessing.
    #
    # **Make your failure modes self-diagnosing.** It is almost always cheaper
    # than adding another speculative pattern.
    try:
        seen = []
        for scope, label in [(page, "main")] + [
            (f, f"frame:{(f.url or '')[:60]}") for f in page.frames
            if f != page.main_frame
        ]:
            try:
                els = scope.locator("button, a[role='button'], [role='button'], "
                                    "input[type='button'], input[type='submit'], "
                                    "a.btn, div[onclick]")
                n = min(await els.count(), 40)
                for i in range(n):
                    try:
                        el = els.nth(i)
                        if not await el.is_visible(timeout=120):
                            continue
                        txt = " ".join((
                            await el.inner_text(timeout=120)
                            or await el.get_attribute("aria-label") or "").split())
                        if txt and len(txt) < 70:
                            seen.append(f"[{label}] {txt}")
                    except Exception:
                        continue
            except Exception:
                continue

        result["candidates_seen"] = seen[:40]
        result["frame_count"] = len(page.frames)
        result["frame_urls"] = [
            (f.url or "")[:90] for f in page.frames if f != page.main_frame
        ][:10]
    except Exception:
        pass

    # ---- Nothing found ------------------------------------------------------
    # Say so plainly. "No banner found" and "no tracking added" are completely
    # different findings, and conflating them would be a serious error.
    result["method"] = "not_found"
    return result
