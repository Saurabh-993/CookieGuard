"""
Tests for chartFrame() — the chart layout maths (frontend/app.js)
=================================================================

WHY THIS FILE EXISTS
--------------------
The browser console filled with 642 copies of:

    Error: <rect> attribute width: A negative value is not valid. ("-84.99")

Every chart computed its drawing area the same way:

    innerW = measuredWidth - margin.left - margin.right

which is correct arithmetic and a latent bug. Two situations break it:

  1. The chart lives in a HIDDEN view. `getBoundingClientRect()` returns 0,
     so innerW = 0 - 130 - 40 = -170.
  2. The chart is on a phone. The vendor chart reserves 130px on the left for
     labels; in a 160px container that margin alone exceeds the box.

A negative range makes d3.scaleLinear() return negative pixels, those land in
`.attr('width', ...)`, and SVG rejects them — once per rect, per transition
frame. Hence 642 errors from about eight bars.

WHAT THIS TEST PROTECTS
-----------------------
`chartFrame()` is now the single place that maths happens, so one test file
covers every chart. The rule it enforces is simple and worth remembering:

    Any layout calculation that subtracts CONSTANTS from a MEASURED size
    needs a floor. The measurement can always be smaller than you assumed.

Like test_globe_math.py, this runs the REAL function from app.js under Node
rather than a Python re-implementation. A re-implementation would pass
happily while the shipped code stayed broken, which is worse than no test.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = Path(__file__).parent.parent / "frontend" / "app.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    NODE is None,
    reason="Node.js not installed — chartFrame tests need it",
)


def call_chart_frame(box_width, box_height, margin):
    """
    Run the real chartFrame() from app.js against a FAKE element.

    chartFrame only ever touches `svgSel.node()` and `getBoundingClientRect()`,
    so a five-line stub is a complete stand-in. This is mocking at the boundary
    of what we own: we own the maths, we do not own the browser's layout
    engine, so the seam goes exactly there.
    """
    # Slice the function out of app.js between two stable markers, so the test
    # exercises the shipped source rather than a copy that could drift.
    harness = f"""
    const fs = require('fs');
    const src = fs.readFileSync({str(APP_JS)!r}, 'utf8');

    const start = src.indexOf('function chartFrame(');
    const end   = src.indexOf('const tooltip =');
    if (start < 0 || end < 0) {{
      console.log(JSON.stringify({{error: 'chartFrame block not found in app.js'}}));
      process.exit(0);
    }}
    eval(src.slice(start, end));

    // A fake d3 selection: just enough surface for chartFrame to work.
    const fakeSel = {{
      node: () => ({{
        getBoundingClientRect: () => ({{ width: {box_width}, height: {box_height} }}),
      }}),
    }};

    const margin = {json.dumps(margin)};
    const frozen = JSON.stringify(margin);   // to prove we don't mutate it
    const result = chartFrame(fakeSel, margin);

    console.log(JSON.stringify({{
      result,
      marginUnchanged: JSON.stringify(margin) === frozen,
    }}));
    """
    out = subprocess.run(
        [NODE, "-e", harness], capture_output=True, text=True, timeout=30
    )
    assert out.returncode == 0, f"Node failed:\n{out.stderr}"
    payload = json.loads(out.stdout)
    assert "error" not in payload, payload["error"]
    return payload


# The vendor chart's real margins — the ones that produced -84.99 in the wild.
VENDOR_MARGIN = {"top": 8, "right": 40, "bottom": 24, "left": 130}


def test_normal_desktop_width_is_untouched():
    """At a comfortable size, chartFrame must not "helpfully" change anything."""
    payload = call_chart_frame(700, 260, VENDOR_MARGIN)
    frame = payload["result"]

    assert frame["margin"]["left"] == 130      # 170 of margin fits in 700
    assert frame["margin"]["right"] == 40
    assert frame["innerW"] == 700 - 130 - 40
    assert frame["innerH"] == 260 - 8 - 24


def test_hidden_element_returns_null():
    """
    A chart in a `display: none` view measures 0x0. Drawing then produces
    garbage geometry AND burns a 600ms transition, so we draw nothing and let
    switchView() redraw once the tab is visible.
    """
    assert call_chart_frame(0, 0, VENDOR_MARGIN)["result"] is None


def test_narrow_phone_never_goes_negative():
    """
    THE REGRESSION TEST. 160px container, 170px of margin — the exact shape of
    the original bug. innerW must stay positive.
    """
    frame = call_chart_frame(160, 200, VENDOR_MARGIN)["result"]

    assert frame is not None
    assert frame["innerW"] > 0
    assert frame["innerH"] > 0


def test_margins_shrink_proportionally_rather_than_clipping():
    """
    The interesting design choice: when the margins don't fit we SCALE them,
    keeping at least 55% of the width for the data itself.

    The alternative — clamping innerW to 1 and leaving the margins alone —
    would technically stop the error while drawing a chart that is 99% empty
    gutter. Silencing an error is not the same as fixing a layout.
    """
    frame = call_chart_frame(300, 200, VENDOR_MARGIN)["result"]
    m = frame["margin"]

    assert m["left"] < 130, "left gutter should shrink on a narrow chart"
    assert m["left"] >= 28, "...but never below a readable minimum"
    assert frame["innerW"] >= 300 * 0.55 - 1, "data area keeps most of the width"


def test_caller_margin_object_is_not_mutated():
    """
    chartFrame copies the margin before adjusting it. Without the copy, the
    FIRST narrow render would permanently shrink the shared object and every
    later desktop render would inherit the phone-sized gutter — a bug that
    only appears after a resize, which is the worst kind to track down.
    """
    assert call_chart_frame(300, 200, VENDOR_MARGIN)["marginUnchanged"] is True


@pytest.mark.parametrize("width", [41, 60, 100, 200, 400, 1200])
def test_inner_width_positive_across_the_range(width):
    """Whatever the container, the drawing area is usable. No exceptions."""
    frame = call_chart_frame(width, 240, VENDOR_MARGIN)["result"]
    assert frame is not None
    assert frame["innerW"] >= 1
