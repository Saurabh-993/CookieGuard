"""
CookieGuard — configuration (Phase 6)
======================================

WHY THIS FILE EXISTS
--------------------
Up to now, settings lived as literals inside the code:

    allow_origins=["*"]                       # in main.py
    Path(__file__).parent.parent / "data"     # in db.py

That is fine on your laptop and wrong the moment the same code runs somewhere
else. The container needs a different database path. Production needs a
different CORS origin. Staging needs a different port. Editing source code per
environment means the thing you tested is not the thing you deployed.

THE PRINCIPLE — "CONFIG IN THE ENVIRONMENT"
--------------------------------------------
This is factor III of the Twelve-Factor App, and it's the single most useful
deployment idea to be able to explain in an interview:

    CODE is what the program does.       Same everywhere. Lives in git.
    CONFIG is what varies per deploy.    Different everywhere. Never in git.

The test for whether something is config: **could this repository be made
public tomorrow without leaking anything or breaking anything?** If a value
would have to change between your laptop and a server, it's config.

Environment variables are the standard carrier because every operating system,
container runtime and CI system already knows how to set them — no file format
to agree on, no parser to write.

    docker run -e CORS_ORIGINS=https://cookieguard.example ...
    Actions:  env: { COOKIEGUARD_DB: /tmp/test.db }

WHY A MODULE RATHER THAN os.environ EVERYWHERE
-----------------------------------------------
Calling `os.environ.get("PORT")` at ten call sites means ten places that can
disagree about the default, the type, and the spelling. One typo'd key silently
falls back to a default and you debug the wrong thing for an hour.

Reading each variable EXACTLY ONCE, in one file, with the type conversion and
the default right there, means:

  * the full list of knobs is one file you can hand to whoever deploys it
  * defaults are declared once
  * `print_config()` can show what the app actually believes

WHAT THIS FILE DELIBERATELY DOESN'T DO
---------------------------------------
No pydantic-settings, no YAML layering, no profile inheritance. Nine variables
do not need a framework, and the version you can explain line by line is worth
more to you right now than the clever one.
"""

import os
from pathlib import Path

# The project root — the folder containing api/, scanner/, frontend/.
# Computed from THIS file's location so it's correct however the app is
# launched: `uvicorn api.main:app`, `python api/main.py`, or from a container
# with a different working directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# TYPED READERS
# ---------------------------------------------------------------------------
# Environment variables are ALWAYS strings. `os.environ["PORT"]` gives "8000",
# not 8000, and `"8000" + 1` is a TypeError at the worst possible moment.
# These three helpers convert once, at startup, where a bad value produces a
# clear error instead of a mysterious one later.

def env_str(key: str, default: str) -> str:
    """Read a string, treating empty/whitespace as 'not set'.

    `docker run -e CORS_ORIGINS=` sets the variable to an empty string, which
    `os.environ.get(key, default)` would happily return as "". Almost never
    what anyone means — an empty value means "I didn't configure this".
    """
    value = os.environ.get(key)
    return value.strip() if value and value.strip() else default


def env_bool(key: str, default: bool) -> bool:
    """
    Read a boolean.

    ⚠ THE CLASSIC BUG: `bool(os.environ.get("DEBUG", "False"))` is ALWAYS True,
    because bool() of any non-empty string is True — including "False", "0" and
    "no". This has shipped debug mode to production more than once.

    So we compare against an explicit set of truthy spellings instead.
    """
    value = os.environ.get(key)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(key: str, default: int) -> int:
    """Read an integer, falling back to the default if it isn't one.

    Failing loudly here would stop the container booting because of a typo in
    an optional tuning knob — worse than running with the documented default.
    A misconfigured PORT, by contrast, fails visibly the moment nothing can
    connect, so it doesn't need to crash on startup.
    """
    value = os.environ.get(key)
    if value is None or not value.strip():
        return default
    try:
        return int(value.strip())
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------------------

# --- Where the SQLite file lives -------------------------------------------
# In Docker this points at /data, which is a mounted VOLUME. That matters more
# than it looks: a container's own filesystem is destroyed when the container
# is replaced, so a database written inside the image disappears on every
# redeploy. See TEACHING.md §51.
#
# NOTE ON THE APPARENT DUPLICATION: db.py reads COOKIEGUARD_DB itself rather
# than importing this module. That is deliberate — db.py and the scanner must
# stay usable from the command line with no API and no FastAPI installed, and
# a config module that imports web dependencies would break that. They agree
# because they read the SAME variable name with the SAME default, which is the
# contract; the variable is the shared interface, not the Python object.
DB_PATH = Path(env_str("COOKIEGUARD_DB", str(PROJECT_ROOT / "data" / "cookieguard.db")))

# --- Network ---------------------------------------------------------------
# 127.0.0.1 means "only this machine can connect" — the right default for a
# laptop. Inside a container it must be 0.0.0.0, or the process is only
# reachable from inside the container itself and `docker run -p 8000:8000`
# appears to do nothing. That confusion is worth its own note; see TEACHING.md.
HOST = env_str("COOKIEGUARD_HOST", "127.0.0.1")
PORT = env_int("COOKIEGUARD_PORT", 8000)

# --- CORS ------------------------------------------------------------------
# ✅ THIS RESOLVES THE `TODO(Phase 7)` IN main.py.
#
# The old value was `["*"]` — any website on the internet may call this API.
# Acceptable for a local read-only demo, not for anything deployed.
#
# Comma-separated so it fits in a single environment variable:
#     CORS_ORIGINS=https://cookieguard.example,https://www.cookieguard.example
#
# The default is the local dashboard's own origins. Note that when the
# dashboard is served BY this API (which it is, at /dashboard), the browser
# sees one origin and CORS never comes into play at all — these entries exist
# for the case where the frontend is hosted separately, e.g. on S3.
CORS_ORIGINS = [
    origin.strip()
    for origin in env_str(
        "CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000"
    ).split(",")
    if origin.strip()
]

# Escape hatch, off by default. Someone will need it for a quick demo, and
# they will otherwise set `CORS_ORIGINS=*` — which silently does NOT work
# (it's matched as a literal origin string), and they'd waste an hour.
# An explicit, greppable, obviously-named flag is safer than a footgun.
CORS_ALLOW_ALL = env_bool("CORS_ALLOW_ALL", False)

# --- Browser sandbox -------------------------------------------------------
# Chromium normally isolates each renderer process in a kernel-level sandbox.
# Inside a container that sandbox needs privileges the container may not have,
# and Chromium then refuses to start at all.
#
# ⚠ Turning it off is a REAL reduction in security, not a formality. We are
# pointing a browser at arbitrary user-supplied URLs, which is precisely the
# situation the sandbox exists for. The honest position:
#
#     · the CONTAINER becomes the security boundary instead of the sandbox
#     · that boundary is weaker (a container shares the host kernel)
#     · the better fix is to keep the sandbox and give the container the
#       seccomp profile it needs — see docker-compose.yml, which does exactly
#       that, so this flag stays OFF in the compose setup
#
# Being able to explain that trade-off is worth more in an interview than
# having quietly added --no-sandbox because a Stack Overflow answer said to.
BROWSER_NO_SANDBOX = env_bool("BROWSER_NO_SANDBOX", False)

# --- Scan limits -----------------------------------------------------------
# A scan drives a real browser: it costs a lot of CPU and memory, and takes
# tens of seconds. Without a ceiling, one person holding down a button can
# exhaust a small EC2 instance. Phase 7 runs on t2.micro, so this matters.
MAX_SCAN_SECONDS = env_int("MAX_SCAN_SECONDS", 120)

# --- Environment name ------------------------------------------------------
# Purely informational: surfaced on /api/health so you can tell at a glance
# which deployment you're actually looking at. More useful than it sounds the
# first time you debug staging while staring at production.
ENVIRONMENT = env_str("ENVIRONMENT", "development")


def as_dict() -> dict:
    """
    The current configuration, for /api/health and for logging at startup.

    ⚠ RULE: this must only ever contain values that are safe to display.
    There are no secrets in CookieGuard today, but the moment someone adds
    `API_KEY` here, a health endpoint would publish it to the internet. That
    is a genuinely common way credentials leak — so the rule goes in the code,
    next to the risk, rather than in someone's memory.
    """
    return {
        "environment": ENVIRONMENT,
        "db_path": str(DB_PATH),
        "host": HOST,
        "port": PORT,
        "cors_origins": ["*"] if CORS_ALLOW_ALL else CORS_ORIGINS,
        "browser_sandbox": not BROWSER_NO_SANDBOX,
        "max_scan_seconds": MAX_SCAN_SECONDS,
    }


def print_config() -> None:
    """Print the effective configuration at startup.

    Ninety percent of "it works locally but not in the container" is a config
    value that isn't what someone assumed. Printing it turns a debugging
    session into reading four lines of log output.
    """
    print("[CookieGuard] configuration:")
    for key, value in as_dict().items():
        print(f"    {key:<18} {value}")


if __name__ == "__main__":
    # `python api/config.py` shows what the app would use right now — handy
    # for checking a .env or a docker-compose environment block took effect.
    print_config()
