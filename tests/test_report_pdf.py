"""
Tests for api/report_pdf.py
============================

WHAT'S TESTED HERE, AND WHAT ISN'T
----------------------------------
`build_report_html()` is a **pure function**: report dict in, HTML string out.
No browser, no network, no filesystem. That makes it fully testable, and it's
where the bugs would actually be — the arc maths, the escaping, the handling
of missing data.

`render_pdf_sync()` is NOT tested here. It needs a real Chromium, which would
make the suite slow and environment-dependent. Splitting the two apart is
precisely what makes the interesting half testable — a lesson worth
generalising: push logic out of the I/O boundary and the logic becomes easy
to test.
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "api"))
sys.path.insert(0, str(Path(__file__).parent.parent / "scanner"))

from report_pdf import (  # noqa: E402
    build_report_html, svg_bar_chart, svg_donut, esc, fmt_date,
)


@pytest.fixture
def report():
    """A realistic report dict, matching what get_domain_report returns."""
    return {
        "domain": "example.com",
        "first_seen": "2026-07-01T10:00:00+00:00",
        "last_scanned": "2026-07-31T10:00:00+00:00",
        "trend": "improving",
        "stats": {"total_scans": 3, "avg_score": 42.0,
                  "best_score": 60, "worst_score": 20},
        "latest_scan": {
            "id": 1, "compliance_score": 35, "compliance_grade": "F",
            "cookie_count": 20, "third_party_cookies": 12,
            "cookies_requiring_consent": 17,
            "necessary_count": 3, "functional_count": 2,
            "analytics_count": 6, "marketing_count": 8, "unknown_count": 1,
        },
        "top_vendors": [
            {"vendor": "Google Analytics", "category": "analytics",
             "occurrences": 6, "scans_seen_in": 3},
            {"vendor": "Meta", "category": "marketing",
             "occurrences": 4, "scans_seen_in": 3},
        ],
        "unknown_cookies": [
            {"name": "mystery_cookie", "domain": ".example.com", "party": "first"},
        ],
        "history": [
            {"scanned_at": "2026-07-01T10:00:00+00:00", "compliance_score": 20,
             "compliance_grade": "F", "cookie_count": 24},
            {"scanned_at": "2026-07-31T10:00:00+00:00", "compliance_score": 35,
             "compliance_grade": "F", "cookie_count": 20},
        ],
        "data_flows": {
            "countries": [
                {"code": "US", "country": "United States", "region": "US (DPF)",
                 "cookie_count": 14, "vendors": ["Google", "Meta"],
                 "vendor_count": 2, "iso_numeric": "840"},
                {"code": "FR", "country": "France", "region": "EEA",
                 "cookie_count": 3, "vendors": ["Criteo"],
                 "vendor_count": 1, "iso_numeric": "250"},
            ],
            "regions": [{"region": "US (DPF)", "cookie_count": 14}],
            "total_cookies": 17, "outside_eea": 14, "outside_eea_pct": 82.4,
        },
        "lifetime_buckets": [
            {"label": "Session", "count": 3, "excessive": False},
            {"label": "> 13 months", "count": 5, "excessive": True},
        ],
        "security_posture": {
            "total": 20, "secure_count": 15, "secure_pct": 75.0,
            "http_only_count": 4, "http_only_pct": 20.0,
            "samesite_none_count": 11, "third_party_count": 12,
            "cross_site_tracker_count": 10,
        },
        "third_party_domains": [
            {"domain": "google-analytics.com", "request_count": 5,
             "category": "analytics", "vendor": "Google Analytics"},
        ],
    }


# ---------------------------------------------------------------------------
# 1. THE DOCUMENT
# ---------------------------------------------------------------------------

def test_builds_a_complete_html_document(report):
    html = build_report_html(report)
    assert html.startswith("<!DOCTYPE html>")
    assert html.rstrip().endswith("</html>")


def test_includes_the_domain_and_score(report):
    html = build_report_html(report)
    assert "example.com" in html
    assert "35" in html
    assert "GRADE F" in html


def test_includes_page_rule_for_pdf_sizing(report):
    """
    `@page` is what sets the paper size and margins in the generated PDF.
    Without it the content runs to the very edge of the sheet.
    """
    html = build_report_html(report)
    assert "@page" in html
    assert "A4" in html


def test_is_self_contained(report):
    """
    No external requests. The PDF must generate identically offline — an
    export that breaks without internet is a bad export.
    """
    html = build_report_html(report)
    assert "<script" not in html
    assert "http://" not in html.replace("http://www.w3.org/2000/svg", "")
    assert "https://" not in html
    assert "cdn." not in html


def test_includes_the_legal_disclaimer(report):
    """This document could end up in front of a regulator. It must say what
    it is and what it isn't."""
    html = build_report_html(report)
    assert "not legal advice" in html
    assert "GDPR Chapter V" in html


def test_states_the_headquarters_limitation(report):
    """
    We infer country from company HQ, which is a proxy rather than a legal
    determination. Saying so in the document is the honest thing to do.
    """
    html = build_report_html(report)
    assert "indicator for review" in html


# ---------------------------------------------------------------------------
# 2. ESCAPING
# ---------------------------------------------------------------------------

def test_escapes_untrusted_values():
    """
    Cookie names come from scanned third-party sites. The same XSS reasoning
    as the dashboard (§63) applies to a generated document.
    """
    assert esc("<script>") == "&lt;script&gt;"
    assert esc('"quoted"') == "&quot;quoted&quot;"
    assert esc(None) == ""


def test_malicious_cookie_name_is_neutralised(report):
    report["unknown_cookies"] = [
        {"name": "<img src=x onerror=alert(1)>",
         "domain": ".evil.com", "party": "third"},
    ]
    html = build_report_html(report)
    assert "<img src=x" not in html
    assert "&lt;img src=x" in html


def test_malicious_vendor_name_is_neutralised(report):
    report["top_vendors"] = [
        {"vendor": "<script>bad()</script>", "category": "marketing",
         "occurrences": 1, "scans_seen_in": 1},
    ]
    html = build_report_html(report)
    assert "<script>bad()" not in html


# ---------------------------------------------------------------------------
# 3. SVG CHARTS
# ---------------------------------------------------------------------------

def test_bar_chart_produces_one_rect_per_row():
    svg = svg_bar_chart([("A", 10, "#000"), ("B", 5, "#111"), ("C", 1, "#222")])
    assert svg.count("<rect") == 3


def test_bar_chart_widths_are_proportional():
    """The one piece of real maths: value scaled into pixels."""
    svg = svg_bar_chart([("Big", 100, "#000"), ("Small", 10, "#111")])
    widths = [int(w) for w in re.findall(r'<rect[^>]*width="(\d+)"', svg)]
    assert len(widths) == 2
    # The 100 bar should be roughly ten times the 10 bar.
    assert widths[0] > widths[1] * 8


def test_bar_chart_handles_empty_input():
    assert "No data" in svg_bar_chart([])


def test_bar_chart_never_produces_a_zero_width_bar():
    """A zero-value row must still be visible, or it looks like a render bug."""
    svg = svg_bar_chart([("Big", 1000, "#000"), ("Zero", 0, "#111")])
    widths = [int(w) for w in re.findall(r'<rect[^>]*width="(\d+)"', svg)]
    assert all(w >= 1 for w in widths)


def test_donut_produces_one_path_per_slice():
    svg = svg_donut([("necessary", 3), ("marketing", 7)])
    assert svg.count("<path") == 2


def test_donut_skips_zero_slices():
    """A zero-value category must not produce an invisible degenerate path."""
    svg = svg_donut([("necessary", 5), ("marketing", 0), ("analytics", 3)])
    assert svg.count("<path") == 2


def test_donut_shows_the_total_in_the_middle():
    svg = svg_donut([("necessary", 4), ("marketing", 6)])
    assert ">10<" in svg


def test_donut_uses_large_arc_flag_for_big_slices():
    """
    SVG's arc command needs `large-arc=1` when a slice exceeds half the
    circle. Get it wrong and a 90% slice renders as a 10% one — the classic
    hand-rolled-pie-chart bug.
    """
    svg = svg_donut([("marketing", 9), ("necessary", 1)])
    # The 90% slice must have the large-arc flag set.
    assert re.search(r"A [\d.]+ [\d.]+ 0 1 1", svg)


def test_donut_handles_a_single_slice():
    svg = svg_donut([("necessary", 5)])
    assert svg.count("<path") == 1


# ---------------------------------------------------------------------------
# 4. MISSING DATA
# ---------------------------------------------------------------------------
# A report can legitimately be sparse — one scan, no unknowns, no third
# parties. None of that should raise.

def test_handles_report_with_no_latest_scan():
    html = build_report_html({"domain": "empty.com", "stats": {}, "trend": "n/a"})
    assert "empty.com" in html


def test_handles_no_unknown_cookies(report):
    report["unknown_cookies"] = []
    html = build_report_html(report)
    assert "Every cookie was identified" in html


def test_handles_no_history(report):
    report["history"] = []
    html = build_report_html(report)
    assert "No history yet" in html


def test_handles_completely_empty_report():
    """The defensive case: every optional key missing."""
    html = build_report_html({"domain": "bare.com"})
    assert html.startswith("<!DOCTYPE html>")
    assert "bare.com" in html


def test_fmt_date_handles_none():
    assert fmt_date(None) == "—"
    assert fmt_date("2026-07-31T10:00:00+00:00") == "2026-07-31 10:00"
