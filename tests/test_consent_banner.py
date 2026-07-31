"""
Tests for frontend/consent-banner.js
=====================================

⚠ READ THIS FIRST — WHAT THESE TESTS CAN AND CANNOT DO
------------------------------------------------------
These are **structural** tests, not behavioural ones. They read the JavaScript
as text and assert that specific patterns are present.

That is genuinely weaker than running the code. A structural test proves the
right code EXISTS; only a browser proves it WORKS.

WHY WE HAVE THEM ANYWAY
-----------------------
Two honest reasons:

  1. The banner encodes **legal requirements**, and legal requirements are
     exactly the sort of thing that gets quietly refactored away six months
     later by someone who doesn't know why the code was shaped that way.
     `test_reject_button_has_same_styling_as_accept` will fail loudly if
     somebody makes "Reject" a small grey link — which is precisely the change
     CNIL fined Google €150m for.

  2. Python's test suite cannot execute JavaScript. Running these properly
     needs Playwright driving a real page — which we already have the
     machinery for, but which would make the suite slow and network-dependent.

WHAT PROPER TESTING WOULD LOOK LIKE
-----------------------------------
Phase 6 could add a Playwright test that:
    loads demo.html → asserts no _ga cookie exists
    clicks "Accept all" → asserts _ga now exists
    clicks "Reject all" → asserts _ga is gone

That's the real test. It's noted as a TODO in AI_CONTEXT.md.

Being clear about the limits of your own test suite is worth more in an
interview than pretending the coverage is better than it is.
"""

import re
import sys
from pathlib import Path

import pytest

FRONTEND = Path(__file__).parent.parent / "frontend"
BANNER = FRONTEND / "consent-banner.js"
DEMO = FRONTEND / "demo.html"


@pytest.fixture(scope="module")
def src():
    """The banner source, read once for the whole file."""
    return BANNER.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def code(src):
    """
    The source with COMMENTS STRIPPED.

    Needed because this file is heavily commented, and a naive
    `assert "HttpOnly" not in src` finds the word in a comment explaining why
    we deliberately don't use HttpOnly — so the test fails on its own
    documentation.

    A small but instructive lesson: a structural test has to be precise about
    what it inspects, or it ends up testing your prose instead of your code.
    """
    without_blocks = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    return "\n".join(
        re.sub(r"//.*$", "", line) for line in without_blocks.splitlines()
    )


# ---------------------------------------------------------------------------
# 1. THE FILE EXISTS AND IS SELF-CONTAINED
# ---------------------------------------------------------------------------

def test_banner_file_exists():
    assert BANNER.is_file()


def test_banner_is_wrapped_in_an_iife(src):
    """
    The whole script must live inside an IIFE.

    It runs on somebody else's website, alongside code we've never seen.
    Without a private scope, every variable we declare becomes a global and a
    name collision could break the host site — or the host could break us.
    """
    assert src.strip().startswith("/*") or src.strip().startswith("(function")
    assert "(function () {" in src
    assert "})();" in src.strip()[-20:]


def test_banner_has_no_imports_or_dependencies(src):
    """
    It must be droppable into any site with one script tag. No ES modules, no
    require(), no CDN link, no framework.
    """
    assert "import " not in src
    assert "require(" not in src
    assert "from '" not in src
    # jQuery is the classic accidental dependency in drop-in widgets.
    assert not re.search(r"\$\(['\"]", src), "looks like a jQuery dependency"


def test_banner_injects_its_own_styles(src):
    """We cannot ask a host site to add a stylesheet, so styles ship inline."""
    assert "injectStyles" in src
    assert "createElement('style')" in src


def test_all_css_classes_are_prefixed(src):
    """
    Every class must start `cg-`. On someone else's site our `.banner` would
    collide with their `.banner`.
    """
    classes = re.findall(r'className = [\'"]([a-z][a-z0-9 -]*)[\'"]', src)
    for cls in classes:
        for name in cls.split():
            assert name.startswith("cg-"), f"unprefixed class: {name}"


def test_exposes_exactly_one_global(src):
    """
    One namespace object on `window`, not several — minimising our footprint
    on a namespace we don't own.

    Note the `(?!=)` in the regex. Without it, `typeof window.gtag === 'function'`
    matches too, because `===` starts with `=`. We want ASSIGNMENT, not
    comparison — a lookahead excludes the second `=`.
    """
    globals_set = re.findall(r"window\.([A-Za-z_]\w*)\s*=(?!=)", src)
    assert globals_set == ["CookieGuardConsent"], globals_set


# ---------------------------------------------------------------------------
# 2. THE FIVE GDPR VALIDITY RULES
# ---------------------------------------------------------------------------
# These are the tests that actually earn their place. Each one guards a legal
# requirement that a well-meaning refactor could silently break.

def test_rule1_scripts_are_blocked_until_consent(src):
    """
    RULE 1: nothing non-essential fires before the click.

    Blocking works by the site marking trackers `type="text/plain"`, which the
    browser refuses to execute. The banner then swaps allowed tags for real
    ones.
    """
    assert "data-cookieguard" in src
    assert "unblockScripts" in src
    # Must CREATE a new element — mutating an existing script tag's type does
    # nothing, because the browser decides execution at insertion time.
    assert "createElement('script')" in src
    assert "insertBefore" in src


def test_rule1_consent_is_reapplied_on_every_page_load(src):
    """
    The returning-visitor half of Rule 1.

    Without re-applying the stored decision, an allowed script would stay
    blocked forever — the banner would record a preference it never acted on.
    """
    assert "applyConsent(existing.preferences" in src


def test_rule2_reject_button_exists(src):
    """RULE 2: a Reject option must exist at the top level, not buried."""
    assert "cg-reject" in src
    assert "Reject all" in src


def test_rule2_reject_has_same_styling_as_accept(src):
    """
    RULE 2, the part that actually got fined.

    CNIL fined Google €150m and Facebook €60m in 2022 — not for tracking, but
    because refusing took more clicks than accepting. Both buttons must share
    the base `cg-btn` class and sit in the same action row.

    If someone later makes Reject a small grey text link, this test fails.
    """
    assert 'class="cg-btn cg-btn-reject"' in src
    assert 'class="cg-btn cg-btn-accept"' in src
    # Same flex sizing → same visual weight.
    assert "flex: 1 1 150px" in src


def test_rule2_reject_is_one_click_from_the_first_screen(src):
    """Reject must appear on the initial banner, not only inside 'Customise'."""
    # The button markup is added unconditionally, outside the `if (showDetails)`
    # branch that governs the category list.
    actions_block = src[src.index('<div class="cg-actions">'):]
    assert "cg-reject" in actions_block[:600]


def test_rule3_no_pre_ticked_boxes(src):
    """
    RULE 3: a new visitor must see everything except 'necessary' switched OFF.

    Pre-ticked boxes make consent invalid — GDPR requires an affirmative
    action, and a box that is already ticked is not one.
    """
    # The ternary must fall back to `false` for a first-time visitor.
    assert "existing ? !!saved[cat.id] : false" in src


def test_rule3_necessary_category_is_locked_on(src):
    """Strictly necessary cookies are consent-exempt and cannot be refused."""
    assert "required: true" in src
    assert "Always active" in src


def test_rule4_granular_categories_exist(src):
    """RULE 4: per-category choice, not all-or-nothing."""
    for category in ("necessary", "functional", "analytics", "marketing"):
        assert f"id: '{category}'" in src, category


def test_rule4_categories_match_the_classifier():
    """
    The banner's categories must be the SAME taxonomy the scanner uses.

    That consistency is the point of the whole project: the tool that finds
    the problem and the tool that fixes it speak one language.
    """
    sys.path.insert(0, str(Path(__file__).parent.parent / "scanner"))
    from classifier import CATEGORIES as CLASSIFIER_CATEGORIES

    banner_src = BANNER.read_text(encoding="utf-8")
    banner_categories = set(re.findall(r"id: '(\w+)',", banner_src))

    # The classifier also has 'unknown', which is an audit outcome rather than
    # something a visitor can consent to — so it correctly has no toggle.
    consentable = set(CLASSIFIER_CATEGORIES) - {"unknown"}
    assert consentable == banner_categories


def test_rule5_withdrawal_mechanism_is_always_available(src):
    """
    RULE 5: withdrawing consent must be as easy as giving it.

    A permanently visible button, one click, on every page.
    """
    assert "cg-reopen" in src
    assert "showReopenButton" in src
    assert "Change your cookie preferences" in src


# ---------------------------------------------------------------------------
# 3. THE CONSENT RECORD
# ---------------------------------------------------------------------------

def test_record_includes_a_timestamp(src):
    """A regulator asks WHEN consent was given, not just whether."""
    assert "timestamp: new Date().toISOString()" in src


def test_record_is_versioned(src):
    """
    Versioning means a future format change can be detected and re-asked,
    rather than silently misread.
    """
    assert "version: 1" in src
    assert "record.version !== 1" in src


def test_consent_cookie_has_sensible_attributes(code):
    """
    SameSite=Lax, and Secure when served over HTTPS.

    Deliberately NOT HttpOnly — unlike a session token, this script has to read
    the record back, so hiding it from JavaScript would break the feature. That
    is a considered choice, not an oversight: the consent record contains no
    secret, only the user's own stated preferences.
    """
    assert "SameSite=Lax" in code
    assert "'; Secure'" in code
    assert "HttpOnly" not in code


def test_consent_expires(src):
    """
    Consent must not be assumed forever. CNIL suggests re-asking within about
    six months.
    """
    assert "expiryDays" in src
    assert "'180'" in src


def test_corrupted_cookie_is_treated_as_no_decision(src):
    """A malformed record must re-ask, never crash or assume consent."""
    assert "catch (e)" in src
    assert "return null" in src


# ---------------------------------------------------------------------------
# 4. ACTUALLY ENFORCING THE DECISION
# ---------------------------------------------------------------------------

def test_refused_cookies_are_deleted(src):
    """
    Withdrawing consent must remove cookies already on the device, or the
    withdrawal is meaningless and tracking simply continues.
    """
    assert "deleteRefusedCookies" in src
    # The only way to delete a cookie is to re-set it with a past expiry.
    assert "expires=Thu, 01 Jan 1970 00:00:00 GMT" in src


def test_cookie_deletion_tries_multiple_domains(src):
    """
    A cookie on '.example.com' is a DIFFERENT cookie from one on
    'example.com'. Deletion must match the original attributes.
    """
    assert "domains.forEach" in src
    assert "'.' + host" in src


def test_google_consent_mode_signal_is_sent(src):
    """The de-facto standard signal, so Google tags know what they may do."""
    for key in ("analytics_storage", "ad_storage",
                "ad_user_data", "ad_personalization"):
        assert key in src, key


def test_custom_event_lets_the_host_page_react(src):
    """
    A CustomEvent is how a third-party script talks to its host without
    requiring the host to call our functions.
    """
    assert "cookieguard:consent" in src
    assert "dispatchEvent" in src


# ---------------------------------------------------------------------------
# 5. ACCESSIBILITY
# ---------------------------------------------------------------------------

def test_dialog_has_aria_attributes(src):
    """Without these a screen-reader user may not realise anything appeared."""
    assert "role', 'dialog'" in src
    assert "aria-modal" in src
    assert "aria-labelledby" in src


def test_toggles_are_real_checkboxes(src):
    """
    The switch is a styled `<input type="checkbox">`, so keyboard operation
    and screen-reader support work with no extra ARIA.
    """
    assert 'type="checkbox"' in src
    assert "aria-label=" in src


def test_focus_is_moved_into_the_dialog(src):
    """Keyboard users must land inside the modal, not behind it."""
    assert ".focus()" in src


def test_focus_outline_is_replaced_not_removed(src):
    """Removing focus styling without a replacement is an accessibility failure."""
    assert "focus-visible" in src


# ---------------------------------------------------------------------------
# 6. THE DEMO PAGE
# ---------------------------------------------------------------------------

def test_demo_page_exists():
    assert DEMO.is_file()


def test_demo_blocks_scripts_the_documented_way():
    """The demo must actually demonstrate blocking, not just describe it."""
    html = DEMO.read_text(encoding="utf-8")
    assert html.count('type="text/plain"') >= 3
    assert 'data-cookieguard="analytics"' in html
    assert 'data-cookieguard="marketing"' in html
    assert 'data-cookieguard="functional"' in html


def test_demo_loads_the_banner():
    html = DEMO.read_text(encoding="utf-8")
    assert 'src="consent-banner.js"' in html
