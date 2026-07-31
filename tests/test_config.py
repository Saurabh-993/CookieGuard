"""
Tests for api/config.py — the configuration layer (Phase 6)
============================================================

WHY CONFIGURATION DESERVES TESTS
--------------------------------
It looks like the least testable code in the project: nine variables and a few
`os.environ.get` calls. But configuration failures are among the nastiest bugs
you can ship, because they are INVISIBLE — the app starts, the tests pass, and
it is quietly doing the wrong thing somewhere you can't see.

The specific bug this file exists to prevent:

    DEBUG = bool(os.environ.get("DEBUG", "False"))

That is always True. `bool()` of any non-empty string is True, including the
string "False". Debug mode has reached production this way more than once, at
real companies, with real consequences. It is two lines to test and it can
never happen again.

A HELPFUL WAY TO THINK ABOUT WHAT TO TEST HERE
-----------------------------------------------
Not "does it read the variable" — that's `os.environ.get`, and it works. Test
the CONVERSIONS and the DEFAULTS, because those are the parts we wrote and
therefore the parts we can get wrong.
"""

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "api"))

import config


@pytest.fixture
def fresh_config(monkeypatch):
    """
    Reload config.py with a controlled environment.

    WHY A RELOAD IS NECESSARY, and it's a genuinely useful thing to understand:
    config.py reads the environment at IMPORT time, on purpose — one read, at
    startup, so the values can't change under a running request. The cost of
    that design is that setting an environment variable in a test has no effect
    on the already-imported module.

    `importlib.reload()` re-executes the module body with the new environment.

    `monkeypatch` is pytest's built-in tool for temporary changes: it records
    what it altered and puts everything back after the test, even if the test
    fails. Setting os.environ by hand would leak state into whichever test ran
    next — and THAT bug appears as "the suite passes alone but fails together",
    which is horrible to track down.
    """
    def _reload(**env):
        for key, value in env.items():
            if value is None:
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, value)
        return importlib.reload(config)

    yield _reload

    # Restore the module to the ambient environment for any later test that
    # imports it. monkeypatch has already undone the variables by now.
    importlib.reload(config)


# ---------------------------------------------------------------------------
# env_bool — the one that has actually caused outages
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["1", "true", "TRUE", "True", "yes", "on", " true "])
def test_env_bool_truthy_spellings(monkeypatch, value):
    monkeypatch.setenv("SOME_FLAG", value)
    assert config.env_bool("SOME_FLAG", False) is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off", "anything"])
def test_env_bool_falsy_spellings(monkeypatch, value):
    """
    THE IMPORTANT ONE: "false" must be False.

    `bool("false")` is True in Python. Any implementation that reaches for
    bool() directly fails this test, which is exactly why it's here.
    """
    monkeypatch.setenv("SOME_FLAG", value)
    assert config.env_bool("SOME_FLAG", True) is False


def test_env_bool_unset_uses_default(monkeypatch):
    monkeypatch.delenv("SOME_FLAG", raising=False)
    assert config.env_bool("SOME_FLAG", True) is True
    assert config.env_bool("SOME_FLAG", False) is False


def test_env_bool_empty_string_is_treated_as_unset(monkeypatch):
    """
    `docker run -e FLAG=` sets FLAG to "". Nobody means "false" by that; they
    mean "I didn't configure this". Treating it as unset is what makes a
    half-filled .env behave sensibly instead of silently flipping a default.
    """
    monkeypatch.setenv("SOME_FLAG", "")
    assert config.env_bool("SOME_FLAG", True) is True


# ---------------------------------------------------------------------------
# env_int
# ---------------------------------------------------------------------------

def test_env_int_parses_a_number(monkeypatch):
    monkeypatch.setenv("SOME_PORT", "9000")
    assert config.env_int("SOME_PORT", 8000) == 9000


def test_env_int_falls_back_on_garbage(monkeypatch):
    """
    A typo in an optional tuning knob should not stop the container booting.
    Falling back to the documented default is the kinder failure — and the
    startup log prints the effective value, so it's visible rather than silent.
    """
    monkeypatch.setenv("SOME_PORT", "eight thousand")
    assert config.env_int("SOME_PORT", 8000) == 8000


def test_env_int_returns_an_int_not_a_string(monkeypatch):
    """Guards against the other classic: "8000" + 1 raising TypeError later."""
    monkeypatch.setenv("SOME_PORT", "9000")
    assert isinstance(config.env_int("SOME_PORT", 8000), int)


# ---------------------------------------------------------------------------
# CORS — the setting this phase exists to fix
# ---------------------------------------------------------------------------

def test_cors_origins_default_is_local_only(fresh_config):
    """The default must NOT be a wildcard. That was the Phase 3 TODO."""
    cfg = fresh_config(CORS_ORIGINS=None, CORS_ALLOW_ALL=None)
    assert "*" not in cfg.CORS_ORIGINS
    assert "http://localhost:8000" in cfg.CORS_ORIGINS


def test_cors_origins_splits_on_commas(fresh_config):
    cfg = fresh_config(CORS_ORIGINS="https://a.example,https://b.example")
    assert cfg.CORS_ORIGINS == ["https://a.example", "https://b.example"]


def test_cors_origins_tolerates_spaces_around_commas(fresh_config):
    """
    People write `A, B` with a space. An origin with a leading space matches
    nothing, and CORS failures give no useful error — the browser just says the
    request was blocked. Stripping here saves someone a genuinely miserable
    afternoon.
    """
    cfg = fresh_config(CORS_ORIGINS=" https://a.example ,  https://b.example ")
    assert cfg.CORS_ORIGINS == ["https://a.example", "https://b.example"]


def test_cors_allow_all_is_off_by_default(fresh_config):
    cfg = fresh_config(CORS_ALLOW_ALL=None)
    assert cfg.CORS_ALLOW_ALL is False


def test_cors_allow_all_can_be_switched_on_explicitly(fresh_config):
    """The escape hatch works — but only when someone explicitly asks for it."""
    cfg = fresh_config(CORS_ALLOW_ALL="true")
    assert cfg.CORS_ALLOW_ALL is True


# ---------------------------------------------------------------------------
# Safe defaults
# ---------------------------------------------------------------------------

def test_browser_sandbox_is_on_by_default(fresh_config):
    """
    We point a browser at URLs typed by strangers. The sandbox stays on unless
    a human explicitly turns it off — the default must never be the insecure
    one, because defaults are what actually ship.
    """
    cfg = fresh_config(BROWSER_NO_SANDBOX=None)
    assert cfg.BROWSER_NO_SANDBOX is False


def test_host_default_is_loopback(fresh_config):
    """
    127.0.0.1, not 0.0.0.0. Running `python -m uvicorn` on a laptop in a café
    should not publish the API to that café's network. The container overrides
    this deliberately, in the Dockerfile, where it's visible.
    """
    cfg = fresh_config(COOKIEGUARD_HOST=None)
    assert cfg.HOST == "127.0.0.1"


# ---------------------------------------------------------------------------
# as_dict — this one is a security test wearing ordinary clothes
# ---------------------------------------------------------------------------

def test_as_dict_exposes_no_secret_looking_keys(fresh_config):
    """
    as_dict() is published by /api/health, which is unauthenticated.

    This test does not check today's values — it checks the SHAPE, so that the
    day someone adds API_KEY or DATABASE_PASSWORD to config.py, this fails and
    asks them whether it belongs on a public endpoint. That is a test defending
    a rule rather than a behaviour, and it's the right tool when the risk is
    "a future change nobody thought about".
    """
    cfg = fresh_config()
    forbidden = ("secret", "password", "token", "key", "credential")
    for name in cfg.as_dict():
        assert not any(word in name.lower() for word in forbidden), (
            f"{name!r} looks like a secret and /api/health is public"
        )


def test_as_dict_reports_the_effective_cors_setting(fresh_config):
    """When the wildcard is on, as_dict must say so — a config report that
    disagrees with the running config is worse than no report at all."""
    cfg = fresh_config(CORS_ALLOW_ALL="true")
    assert cfg.as_dict()["cors_origins"] == ["*"]
