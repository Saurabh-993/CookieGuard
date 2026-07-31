"""
Tests for the globe's quaternion maths (frontend/app.js)
=========================================================

WHY THIS FILE IS UNUSUAL
------------------------
The rotation maths is JavaScript, and pytest can't run JavaScript. But this is
the highest-risk code in the frontend: it's pure maths with no visual feedback
when it's subtly wrong, and "the globe spins oddly near the poles" is close to
impossible to debug by eye.

So we extract the `versor` object from `app.js` and run it under **Node**,
which is already installed alongside Playwright. If Node isn't available the
test skips rather than fails — a missing optional tool shouldn't break the
suite.

WHAT WE'RE ACTUALLY CHECKING
----------------------------
The reason quaternions are used at all: the naive approach ("mouse moved 10px
right, add 10 to longitude") breaks near the poles, because longitude lines
converge there. Drag over the top of the globe and it spins wildly.

`test_pole_crossing_stays_bounded` is the test that matters. Everything else
is scaffolding proving the pieces work.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = Path(__file__).parent.parent / "frontend" / "app.js"

# `shutil.which` finds an executable on PATH — the Python equivalent of `which`.
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    NODE is None,
    reason="Node.js not installed — the globe maths tests need it",
)


def run_versor(script: str):
    """
    Extract the `versor` object from app.js, run `script` against it in Node,
    and return whatever the script prints as JSON.

    Slicing the source between two known markers is crude but effective: it
    means the tests exercise the REAL code rather than a copy that could drift
    out of sync. A copy would defeat the point entirely.
    """
    harness = f"""
    const fs = require('fs');
    const src = fs.readFileSync({str(APP_JS)!r}, 'utf8');
    const start = src.indexOf('const versor = {{');
    const end = src.indexOf('/* ---- Globe state');
    if (start < 0 || end < 0) {{
      console.log(JSON.stringify({{error: 'versor block not found in app.js'}}));
      process.exit(0);
    }}
    const body = src.slice(start + 'const versor = '.length, end).trim()
                    .replace(/;\\s*$/, '');
    const versor = eval('(' + body + ')');
    {script}
    """
    result = subprocess.run(
        [NODE, "-e", harness], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip())


# ---------------------------------------------------------------------------
# 1. THE BLOCK EXISTS
# ---------------------------------------------------------------------------

def test_versor_block_is_present():
    """Guards against a refactor quietly removing or renaming the block."""
    out = run_versor("console.log(JSON.stringify(Object.keys(versor)));")
    assert "error" not in out
    for fn in ("cartesian", "delta", "multiply", "toAngles", "fromAngles"):
        assert fn in out, fn


# ---------------------------------------------------------------------------
# 2. SPHERICAL -> CARTESIAN
# ---------------------------------------------------------------------------

def test_cartesian_produces_unit_vectors():
    """
    Every result must have length exactly 1 — it's a point on a UNIT sphere.
    If this drifts, every rotation built on it is wrong by the same factor.
    """
    out = run_versor("""
      const pts = [[0,0],[90,0],[0,90],[-45,30],[180,-60],[-179,-89]];
      console.log(JSON.stringify(
        pts.map(p => Math.hypot(...versor.cartesian(p)))));
    """)
    for length in out:
        assert abs(length - 1) < 1e-9


def test_cartesian_known_points():
    """Three anchor points, checked against the geometry by hand."""
    out = run_versor("""
      console.log(JSON.stringify({
        origin:     versor.cartesian([0, 0]),     // where x axis meets equator
        east:       versor.cartesian([90, 0]),    // quarter turn east
        north_pole: versor.cartesian([0, 90]),
      }));
    """)
    assert all(abs(a - b) < 1e-9 for a, b in zip(out["origin"], [1, 0, 0]))
    assert all(abs(a - b) < 1e-9 for a, b in zip(out["east"], [0, 1, 0]))
    assert all(abs(a - b) < 1e-9 for a, b in zip(out["north_pole"], [0, 0, 1]))


# ---------------------------------------------------------------------------
# 3. QUATERNION ALGEBRA
# ---------------------------------------------------------------------------

def test_delta_of_identical_vectors_is_identity():
    """
    Rotating a point onto itself must be "no rotation" — [1,0,0,0].

    This is the case that fires constantly in practice: every mousemove where
    the pointer hasn't actually moved. If it returned NaN the globe would
    vanish the moment you pressed the mouse button.
    """
    out = run_versor("""
      const v = versor.cartesian([30, 20]);
      console.log(JSON.stringify(versor.delta(v, v)));
    """)
    assert all(abs(a - b) < 1e-5 for a, b in zip(out, [1, 0, 0, 0]))


def test_multiply_by_identity_is_a_no_op():
    out = run_versor("""
      const q = versor.fromAngles([37, -14, 0]);
      console.log(JSON.stringify({
        q, product: versor.multiply([1, 0, 0, 0], q)
      }));
    """)
    assert all(abs(a - b) < 1e-9 for a, b in zip(out["q"], out["product"]))


def test_angles_survive_a_round_trip():
    """
    angles -> quaternion -> angles must return the original.

    This is the core correctness property. `fromAngles` and `toAngles` are
    inverses, and if they aren't, the drag handler slowly corrupts the
    rotation on every single mousemove — the globe drifts and tilts for no
    visible reason.
    """
    out = run_versor("""
      const cases = [[0,0,0],[45,20,0],[-120,-35,0],[179,60,0],[-90,-80,0]];
      console.log(JSON.stringify(
        cases.map(a => ({ input: a, output: versor.toAngles(versor.fromAngles(a)) }))));
    """)
    for case in out:
        for original, returned in zip(case["input"], case["output"]):
            assert abs(original - returned) < 1e-4, case


# ---------------------------------------------------------------------------
# 4. THE POLE CASE — the reason quaternions are here at all
# ---------------------------------------------------------------------------

def test_pole_crossing_stays_finite_and_bounded():
    """
    ⚠ THE TEST THAT JUSTIFIES THE WHOLE APPROACH.

    Dragging straight over the north pole is where naive Euler-angle rotation
    falls apart: longitude lines converge at the pole, so a tiny mouse
    movement implies an enormous longitude change, and the globe spins wildly
    or produces NaN.

    Quaternions have no such singularity. The result must be finite and within
    normal angle bounds.
    """
    out = run_versor("""
      const a0 = versor.cartesian([0, 85]);
      const a1 = versor.cartesian([180, 85]);        // straight over the top
      const q  = versor.multiply(versor.fromAngles([0,0,0]), versor.delta(a0, a1));
      console.log(JSON.stringify(versor.toAngles(q)));
    """)
    assert all(isinstance(a, (int, float)) for a in out)
    assert all(a == a for a in out), "produced NaN"      # NaN != NaN
    assert all(abs(a) <= 180.001 for a in out), out


def test_rotation_near_both_poles_is_stable():
    """The same check at several extreme latitudes, not just one."""
    out = run_versor("""
      const results = [];
      for (const lat of [89, 85, -85, -89]) {
        const a0 = versor.cartesian([0, lat]);
        const a1 = versor.cartesian([90, lat]);
        results.push(versor.toAngles(
          versor.multiply(versor.fromAngles([0,0,0]), versor.delta(a0, a1))));
      }
      console.log(JSON.stringify(results));
    """)
    for angles in out:
        assert all(a == a for a in angles), f"NaN at {angles}"
        assert all(abs(a) <= 180.001 for a in angles), angles


def test_small_drags_produce_small_rotations():
    """
    Continuity: a tiny movement must not cause a large jump. This is what
    makes dragging feel like grabbing the globe rather than flicking it.
    """
    out = run_versor("""
      const a0 = versor.cartesian([10, 10]);
      const a1 = versor.cartesian([10.5, 10]);        // half a degree
      console.log(JSON.stringify(versor.toAngles(
        versor.multiply(versor.fromAngles([0,0,0]), versor.delta(a0, a1)))));
    """)
    assert all(abs(a) < 5 for a in out), out
