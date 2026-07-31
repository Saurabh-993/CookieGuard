"""
CookieGuard — PDF report generation
====================================

WHAT THIS FILE DOES
-------------------
Turns a domain's audit report into a downloadable PDF.

HOW: WE ALREADY OWN A BROWSER
-----------------------------
There are three usual ways to make a PDF from a web app:

  1. Client-side `window.print()` → "Save as PDF"
     Zero dependencies, but it's the user's browser doing it, the output
     depends on their print settings, and it isn't a file the server can
     email or archive.

  2. A JavaScript PDF library (jsPDF + html2canvas)
     Screenshots the page into a bitmap and wraps it in a PDF. The text
     stops being text — it can't be selected, searched or read by a screen
     reader — and it looks blurry when printed.

  3. Render real HTML in a real browser and use its PDF engine.   ← we do this
     Proper vector text, real fonts, selectable and searchable.

We can do (3) almost for free, because **Playwright is already a dependency**.
`page.pdf()` uses Chromium's own print engine — the same one behind Ctrl+P.

That's a nice thing to be able to say in an interview: the browser automation
bought for scanning turned out to solve a completely different problem, because
the capability was general rather than task-specific.

WHY WE BUILD THE HTML IN PYTHON
-------------------------------
We could point Playwright at the live dashboard. We deliberately don't:

  * The dashboard fetches its data with JavaScript, so we'd be waiting on the
    server to call itself over HTTP — fragile and slow.
  * D3 charts need the CDN. A PDF export that fails when you're offline is a
    bad export.
  * Print layout and screen layout genuinely want different things.

So we generate a **self-contained** HTML document: all data inlined, no
JavaScript, no external requests, charts as hand-built inline SVG. It renders
identically every time, offline, in about a second.
"""

import html
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# SMALL HELPERS
# ---------------------------------------------------------------------------

def esc(value) -> str:
    """
    Escape text for HTML.

    Same reasoning as `esc()` in app.js (§63): cookie names and vendor strings
    come from scanned third-party sites and are untrusted. Python's stdlib has
    this built in, so we use it rather than hand-rolling the replacements.
    """
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def fmt_date(iso) -> str:
    if not iso:
        return "—"
    return str(iso)[:16].replace("T", " ")


GRADE_COLORS = {
    "A": "#059669", "B": "#65a30d", "C": "#d97706",
    "D": "#ea580c", "F": "#dc2626",
}
CATEGORY_COLORS = {
    "necessary": "#059669", "functional": "#0284c7", "analytics": "#d97706",
    "marketing": "#dc2626", "unknown": "#6b7280",
}
REGION_COLORS = {
    "EEA": "#059669", "Adequate": "#0284c7",
    "US (DPF)": "#d97706", "Restricted": "#dc2626",
}


# ---------------------------------------------------------------------------
# INLINE SVG CHARTS
# ---------------------------------------------------------------------------
# Built with plain string formatting — no D3, no JavaScript, no network.
#
# This is worth understanding: D3 does two things, MATHS and DOM MANIPULATION.
# In a static document there is no DOM to manipulate, so all we need is the
# maths — and for a bar chart the maths is one multiplication.
#
# It is a useful reminder that a charting library is a convenience, not a
# requirement. An SVG bar chart is `<rect>` elements with computed widths.

def svg_bar_chart(rows, width=520, bar_height=22, gap=7, color_key=None):
    """
    A horizontal bar chart as an SVG string.

    `rows` is a list of (label, value, colour) tuples.
    """
    if not rows:
        return '<p class="muted">No data.</p>'

    max_value = max(r[1] for r in rows) or 1
    label_w = 150
    chart_w = width - label_w - 50
    height = len(rows) * (bar_height + gap)

    parts = [f'<svg width="{width}" height="{height}" '
             f'xmlns="http://www.w3.org/2000/svg" font-family="Helvetica, Arial">']

    for i, (label, value, colour) in enumerate(rows):
        y = i * (bar_height + gap)
        # The one piece of "maths": scale the value into pixels.
        bar_w = max(2, int(chart_w * value / max_value))
        parts.append(
            f'<text x="{label_w - 8}" y="{y + bar_height * 0.7}" '
            f'text-anchor="end" font-size="11" fill="#475569">{esc(label)[:26]}</text>'
            f'<rect x="{label_w}" y="{y}" width="{bar_w}" height="{bar_height}" '
            f'rx="3" fill="{colour}"/>'
            f'<text x="{label_w + bar_w + 7}" y="{y + bar_height * 0.7}" '
            f'font-size="11" fill="#475569">{value}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def svg_donut(counts, size=170):
    """
    A donut chart as SVG.

    THE MATHS, since we're not using d3.arc():

      1. Convert each value into a fraction of the whole
      2. Convert that fraction into an angle
      3. Convert the angle into a point on the circle, with sin/cos
      4. Draw an arc between consecutive points

    SVG's arc command is `A rx ry rotation large-arc sweep x y`. The
    `large-arc` flag is the fiddly bit: it tells the renderer which of the two
    possible arcs between two points to draw, and must be 1 when the slice is
    more than half the circle. Getting it wrong turns a 70% slice into a 30%
    one.
    """
    import math

    total = sum(v for _, v in counts) or 1
    cx = cy = size / 2
    r_outer = size / 2 - 6
    r_inner = r_outer * 0.58

    parts = [f'<svg width="{size}" height="{size}" '
             f'xmlns="http://www.w3.org/2000/svg" font-family="Helvetica, Arial">']
    angle = -math.pi / 2      # start at 12 o'clock, not 3 o'clock

    for label, value in counts:
        if value <= 0:
            continue
        sweep = 2 * math.pi * value / total
        end = angle + sweep

        x1, y1 = cx + r_outer * math.cos(angle), cy + r_outer * math.sin(angle)
        x2, y2 = cx + r_outer * math.cos(end), cy + r_outer * math.sin(end)
        x3, y3 = cx + r_inner * math.cos(end), cy + r_inner * math.sin(end)
        x4, y4 = cx + r_inner * math.cos(angle), cy + r_inner * math.sin(angle)

        large = 1 if sweep > math.pi else 0
        colour = CATEGORY_COLORS.get(label, "#6b7280")

        parts.append(
            f'<path d="M {x1:.2f} {y1:.2f} '
            f'A {r_outer:.2f} {r_outer:.2f} 0 {large} 1 {x2:.2f} {y2:.2f} '
            f'L {x3:.2f} {y3:.2f} '
            f'A {r_inner:.2f} {r_inner:.2f} 0 {large} 0 {x4:.2f} {y4:.2f} Z" '
            f'fill="{colour}"/>'
        )
        angle = end

    parts.append(
        f'<text x="{cx}" y="{cy + 2}" text-anchor="middle" '
        f'font-size="26" font-weight="bold" fill="#0f172a">{total}</text>'
        f'<text x="{cx}" y="{cy + 18}" text-anchor="middle" '
        f'font-size="9" fill="#64748b">COOKIES</text></svg>'
    )
    return "".join(parts)


# ---------------------------------------------------------------------------
# THE DOCUMENT
# ---------------------------------------------------------------------------

def build_report_html(report: dict) -> str:
    """Render a complete, self-contained HTML audit report."""
    latest = report.get("latest_scan") or {}
    stats = report.get("stats") or {}
    flows = report.get("data_flows") or {}
    security = report.get("security_posture") or {}
    grade = latest.get("compliance_grade") or "—"
    grade_colour = GRADE_COLORS.get(grade, "#6b7280")

    # ---- charts ----
    category_counts = [
        (c, latest.get(f"{c}_count", 0))
        for c in ("necessary", "functional", "analytics", "marketing", "unknown")
    ]
    donut = svg_donut([(c, n) for c, n in category_counts if n > 0])

    vendor_rows = [
        (v.get("vendor") or "Unknown", v.get("occurrences", 0),
         CATEGORY_COLORS.get(v.get("category"), "#6b7280"))
        for v in (report.get("top_vendors") or [])[:8]
    ]
    vendor_chart = svg_bar_chart(vendor_rows)

    country_rows = [
        (c.get("country"), c.get("cookie_count", 0),
         REGION_COLORS.get(c.get("region"), "#6b7280"))
        for c in (flows.get("countries") or [])[:10]
    ]
    country_chart = svg_bar_chart(country_rows)

    lifetime_rows = [
        (b.get("label"), b.get("count", 0),
         "#dc2626" if b.get("excessive") else "#0284c7")
        for b in (report.get("lifetime_buckets") or [])
    ]
    lifetime_chart = svg_bar_chart(lifetime_rows)

    # ---- category legend ----
    legend = "".join(
        f'<span class="chip"><i style="background:{CATEGORY_COLORS[c]}"></i>'
        f'{c.capitalize()} {n}</span>'
        for c, n in category_counts
    )

    # ---- unknown cookies table ----
    unknowns = report.get("unknown_cookies") or []
    unknown_rows = "".join(
        f'<tr><td class="mono">{esc(c.get("name"))}</td>'
        f'<td class="mono">{esc(c.get("domain"))}</td>'
        f'<td>{esc(c.get("party"))}</td></tr>'
        for c in unknowns[:25]
    ) or '<tr><td colspan="3" class="muted">Every cookie was identified.</td></tr>'

    # ---- history ----
    history = report.get("history") or []
    history_rows = "".join(
        f'<tr><td>{fmt_date(h.get("scanned_at"))}</td>'
        f'<td><b style="color:{GRADE_COLORS.get(h.get("compliance_grade"), "#6b7280")}">'
        f'{h.get("compliance_score")}/100 ({h.get("compliance_grade")})</b></td>'
        f'<td>{h.get("cookie_count")}</td></tr>'
        for h in history[-12:]
    ) or '<tr><td colspan="3" class="muted">No history yet.</td></tr>'

    generated = datetime.now(timezone.utc).strftime("%d %B %Y at %H:%M UTC")

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>CookieGuard audit — {esc(report.get('domain'))}</title>
<style>
  /* @page controls the PDF page itself — size and margins. This is a real CSS
     feature that only applies to paged media, and it's what stops the content
     running to the very edge of the paper. */
  @page {{ size: A4; margin: 16mm 14mm; }}

  * {{ box-sizing: border-box; }}
  body {{
    font-family: Helvetica, Arial, sans-serif; font-size: 10.5pt;
    line-height: 1.5; color: #0f172a; margin: 0;
  }}
  h1 {{ font-size: 19pt; margin: 0 0 2px; }}
  h2 {{ font-size: 12pt; margin: 0 0 8px; padding-bottom: 5px;
        border-bottom: 2px solid #e2e8f0; }}
  h3 {{ font-size: 10pt; margin: 0 0 6px; }}
  .muted {{ color: #64748b; }}
  .small {{ font-size: 8.5pt; }}
  .mono {{ font-family: "Courier New", monospace; font-size: 8.5pt; }}

  header {{ border-bottom: 3px solid #4f46e5; padding-bottom: 10px;
            margin-bottom: 16px; }}
  .sub {{ color: #64748b; font-size: 9pt; }}

  /* break-inside: avoid stops a card being split across two pages —
     a small detail that makes printed output look considered. */
  section {{ margin-bottom: 18px; break-inside: avoid; }}

  .hero {{ display: flex; gap: 18px; align-items: center; margin-bottom: 14px; }}
  .score {{
    width: 92px; height: 92px; border-radius: 50%; color: #fff;
    display: flex; flex-direction: column; align-items: center;
    justify-content: center; flex-shrink: 0;
    background: {grade_colour};
  }}
  .score b {{ font-size: 24pt; line-height: 1; }}
  .score span {{ font-size: 7.5pt; }}

  .grid {{ display: flex; gap: 10px; flex-wrap: wrap; margin: 10px 0; }}
  .stat {{ border: 1px solid #e2e8f0; border-radius: 7px; padding: 8px 12px;
           min-width: 108px; }}
  .stat b {{ display: block; font-size: 15pt; }}
  .stat span {{ font-size: 7.5pt; text-transform: uppercase;
                letter-spacing: .04em; color: #64748b; }}

  table {{ width: 100%; border-collapse: collapse; font-size: 9pt; }}
  th {{ text-align: left; font-size: 7.5pt; text-transform: uppercase;
        letter-spacing: .04em; color: #64748b; padding: 5px 7px;
        border-bottom: 1.5px solid #e2e8f0; }}
  td {{ padding: 5px 7px; border-bottom: 1px solid #f1f5f9; }}

  .chip {{ display: inline-flex; align-items: center; gap: 5px;
           font-size: 8.5pt; margin-right: 12px; }}
  .chip i {{ width: 9px; height: 9px; border-radius: 2px; display: inline-block; }}

  .callout {{ background: #f8fafc; border: 1px solid #e2e8f0;
              border-radius: 7px; padding: 11px 14px; display: flex;
              gap: 14px; align-items: center; }}
  .callout b {{ font-size: 21pt; color: #dc2626; }}

  .cols {{ display: flex; gap: 22px; }}
  .cols > div {{ flex: 1; }}

  footer {{ margin-top: 22px; padding-top: 9px; border-top: 1px solid #e2e8f0;
            font-size: 8pt; color: #64748b; }}
</style></head>
<body>

<header>
  <h1>🍪 Cookie Compliance Audit</h1>
  <div class="sub">
    <b>{esc(report.get('domain'))}</b> · generated {generated} ·
    CookieGuard automated scan
  </div>
</header>

<section>
  <div class="hero">
    <div class="score"><b>{latest.get('compliance_score', '—')}</b><span>GRADE {esc(grade)}</span></div>
    <div>
      <h3>Pre-consent compliance</h3>
      <p style="margin:0">
        <b>{latest.get('cookies_requiring_consent', 0)}</b> cookies that legally
        require consent were set <b>before any consent was given</b>.
      </p>
      <p class="muted small" style="margin:5px 0 0">
        Scanned {fmt_date(report.get('last_scanned'))} ·
        {stats.get('total_scans', 0)} scan(s) on record ·
        trend: <b>{esc(report.get('trend'))}</b>
      </p>
    </div>
  </div>

  <div class="grid">
    <div class="stat"><b>{latest.get('cookie_count', 0)}</b><span>Total cookies</span></div>
    <div class="stat"><b>{latest.get('third_party_cookies', 0)}</b><span>Third-party</span></div>
    <div class="stat"><b>{security.get('cross_site_tracker_count', 0)}</b><span>Cross-site trackers</span></div>
    <div class="stat"><b>{len(report.get('third_party_domains') or [])}</b><span>Domains contacted</span></div>
    <div class="stat"><b>{flows.get('outside_eea_pct', 0)}%</b><span>Outside EEA</span></div>
  </div>
</section>

<section>
  <h2>Cookie categories</h2>
  <div class="cols">
    <div style="flex:0 0 180px">{donut}</div>
    <div>
      <div style="margin-bottom:9px">{legend}</div>
      <p class="small muted">
        Only <b>strictly necessary</b> cookies may be set before the visitor
        consents. Every other category found here was placed on the device
        without permission.
      </p>
      <table style="margin-top:9px">
        <tr><th>Security attribute</th><th>Count</th><th>%</th></tr>
        <tr><td>Secure flag set</td><td>{security.get('secure_count', 0)} / {security.get('total', 0)}</td><td>{security.get('secure_pct', 0)}%</td></tr>
        <tr><td>HttpOnly flag set</td><td>{security.get('http_only_count', 0)} / {security.get('total', 0)}</td><td>{security.get('http_only_pct', 0)}%</td></tr>
        <tr><td>SameSite=None</td><td>{security.get('samesite_none_count', 0)} / {security.get('total', 0)}</td><td>—</td></tr>
      </table>
    </div>
  </div>
</section>

<section>
  <h2>Where does the data go?</h2>
  <div class="callout">
    <b>{flows.get('outside_eea_pct', 0)}%</b>
    <div class="small">
      <b>{flows.get('outside_eea', 0)} of {flows.get('total_cookies', 0)}</b>
      cookies come from vendors headquartered outside the EEA. Each is an
      international data transfer under <b>GDPR Chapter V</b> and requires a
      documented legal basis.
      <br><span class="muted">Country is inferred from the vendor's
      headquarters — an indicator for review, not a legal determination.</span>
    </div>
  </div>
  <div style="margin-top:11px">{country_chart}</div>
</section>

<section>
  <h2>Most frequent vendors</h2>
  {vendor_chart}
</section>

<section>
  <h2>Cookie lifetimes</h2>
  <p class="small muted" style="margin-top:0">
    France's CNIL recommends a maximum of <b>13 months</b> for analytics
    cookies. Anything longer is shown in red.
  </p>
  {lifetime_chart}
</section>

<section>
  <h2>Cookies requiring manual review</h2>
  <p class="small muted" style="margin-top:0">
    No signature matched these. They are reported as unknown rather than
    assumed safe — a compliance tool that guesses "necessary" would
    under-report risk.
  </p>
  <table>
    <tr><th>Cookie</th><th>Domain</th><th>Party</th></tr>
    {unknown_rows}
  </table>
</section>

<section>
  <h2>Scan history</h2>
  <table>
    <tr><th>Scanned</th><th>Score</th><th>Cookies</th></tr>
    {history_rows}
  </table>
</section>

<footer>
  Generated by <b>CookieGuard</b> — automated cookie compliance scanner.
  Scans capture the state <b>before</b> any consent is given.
  <br>
  <b>Automated classification is a technical aid, not legal advice.</b>
  Compliance decisions should be reviewed by a qualified privacy professional.
</footer>

</body></html>"""


# ---------------------------------------------------------------------------
# RENDERING TO PDF
# ---------------------------------------------------------------------------

def render_pdf_sync(html_text: str) -> bytes:
    """
    Turn an HTML string into PDF bytes using Chromium's own print engine.

    ⚠ SYNCHRONOUS ON PURPOSE, and it must run in a worker thread.

    Same Windows event-loop problem as the scanner (§54): Playwright spawns a
    subprocess, and uvicorn's Windows loop can't. So this uses the SYNC
    Playwright API and the caller wraps it in `asyncio.to_thread` — the sync
    API manages its own loop internally, which sidesteps the conflict entirely.

    `set_content` loads HTML directly from a string, so there is no file to
    write and no server to call. `wait_until="load"` is enough because the
    document has no scripts and no external requests.
    """
    from playwright.sync_api import sync_playwright

    # Phase 6: same container flags as the scanner. Reuse the scanner's helper
    # rather than duplicating the logic — one definition of "how do we launch
    # Chromium here" means the container fix can't be applied to one call site
    # and forgotten at the other.
    from scan import browser_launch_args

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=browser_launch_args())
        try:
            page = browser.new_page()
            page.set_content(html_text, wait_until="load")
            return page.pdf(
                format="A4",
                print_background=True,   # without this, every background
                                         # colour is dropped — the score circle
                                         # would print as white on white
                margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
                # Margins are set in CSS via @page instead, so the same values
                # apply whether the document is printed from here or by hand.
            )
        finally:
            browser.close()
