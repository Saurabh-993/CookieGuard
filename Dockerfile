# =============================================================================
# CookieGuard — container image (Phase 6)
# =============================================================================
#
# WHAT A CONTAINER ACTUALLY IS
# ----------------------------
# Not a virtual machine. A VM boots a whole second operating system with its
# own kernel — gigabytes, and tens of seconds to start. A container is just
# ORDINARY PROCESSES ON THE HOST KERNEL that have been lied to about what they
# can see: their own filesystem, their own process list, their own network.
# Two Linux features do it — namespaces (what you can see) and cgroups (how
# much you can use).
#
# That's why containers start in milliseconds, and also why the isolation is
# weaker than a VM's: everything shares one kernel. Worth knowing both halves.
#
# THE PROBLEM IT SOLVES FOR US, CONCRETELY
# ----------------------------------------
# CookieGuard needs Python 3.12, Playwright, AND a working Chromium with about
# thirty Linux shared libraries (fonts, audio, X11 bits). "Install these on the
# server" is a page of instructions that goes stale and works differently on
# Ubuntu than on Amazon Linux. This file replaces that page with something a
# machine can execute identically every time.
#
#     Before:  "works on my machine"     (and a README nobody can follow)
#     After:   `docker run cookieguard`  (and it is bit-identical to CI)
#
# HOW IMAGES ARE BUILT — LAYERS AND THE CACHE
# -------------------------------------------
# Each instruction below produces a LAYER: a read-only diff of the filesystem.
# Docker caches layers and reuses them if the instruction AND its inputs are
# unchanged. Change something early and every later layer is rebuilt.
#
# That single fact dictates the order of this file. Dependencies change rarely,
# our source changes constantly, so dependencies are installed FIRST:
#
#     COPY requirements.txt  →  install       ← cached across most builds
#     COPY . .                                ← invalidated on every edit
#
# Reversed, every one-character source edit would reinstall Playwright and
# re-download a browser. Minutes instead of seconds, on every commit.
# =============================================================================


# -----------------------------------------------------------------------------
# BASE IMAGE
# -----------------------------------------------------------------------------
# Microsoft publishes an official Playwright image with Chromium AND all its
# Linux dependencies preinstalled and version-matched.
#
# WHY NOT `FROM python:3.12-slim` AND INSTALL CHROMIUM OURSELVES?
# You can, and it's about 40 lines of `apt-get install` naming libnss3,
# libatk1.0-0, libcups2, libgbm1 and friends. Get one wrong and Chromium fails
# to start with an error that names a missing symbol, not a missing package.
# Using the maintained image is not laziness — it's declining to re-solve a
# problem the vendor already solved and keeps solving.
#
# ⚠⚠ THE VERSION-DRIFT BUG — this comment used to say "remember to keep these
# in sync", and a comment is not a mechanism. It drifted on the first build.
#
# WHAT HAPPENED
# -------------
# requirements.txt says `playwright>=1.48` (a RANGE, deliberately — see KI-6:
# exact pins broke installs when no wheel existed for the user's Python). uv
# resolved that to the newest release, 1.62.0, and installed it OVER the
# base image's 1.61.0.
#
# The browsers, though, are baked into the image at 1.61's paths. So the
# Python package went looking for:
#     /ms-playwright/chromium_headless_shell-1234/chrome-headless-shell
# which 1.61's image doesn't contain. Every scan failed with:
#     "Executable doesn't exist … Please update docker image as well."
#
# The app started. Health was green. CI was green. Only an actual scan failed.
#
# THE FIX — one variable, used in BOTH places, so they cannot drift
# -----------------------------------------------------------------
# `ARG` before `FROM` is in Docker's *global scope*: it can be used in the
# FROM line, but it is NOT visible inside the build stage. That's why it is
# declared a second time after FROM — a genuine Docker quirk that catches
# everyone once.
#
# To upgrade Playwright, change this ONE line and rebuild.
ARG PLAYWRIGHT_VERSION=1.61.0

FROM mcr.microsoft.com/playwright/python:v${PLAYWRIGHT_VERSION}-noble

# Re-declare, so the value is visible to RUN instructions below.
ARG PLAYWRIGHT_VERSION


# -----------------------------------------------------------------------------
# ENVIRONMENT
# -----------------------------------------------------------------------------
# PYTHONDONTWRITEBYTECODE — don't create .pyc files. In a container they are
#   pure waste: written on first import, never reused, and they bloat the layer.
#
# PYTHONUNBUFFERED — THE IMPORTANT ONE. Python buffers stdout when it isn't a
#   terminal, so `print()` output can sit in memory for ages. In a container
#   that means `docker logs` shows NOTHING while the app runs, and everything
#   at once when it crashes. This one line is the difference between logs that
#   help and logs that mislead.
#
# PIP_NO_CACHE_DIR — pip's download cache is useless in an image (nothing will
#   ever install again) and costs ~100MB.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Application defaults. Every one is overridable at `docker run` time — see
# .env.example. Two of these are load-bearing:
#
#   COOKIEGUARD_HOST=0.0.0.0
#       Inside a container, 127.0.0.1 means "this container only". Bind there
#       and `-p 8000:8000` looks broken while the app is running perfectly.
#       This is the single most common first-time Docker bug.
#
#   COOKIEGUARD_DB=/data/...
#       /data is a VOLUME. A container's own filesystem is destroyed when the
#       container is replaced, which happens on every deploy — a database
#       written anywhere else would silently vanish.
ENV COOKIEGUARD_HOST=0.0.0.0 \
    COOKIEGUARD_PORT=8000 \
    COOKIEGUARD_DB=/data/cookieguard.db \
    ENVIRONMENT=docker

WORKDIR /app


# -----------------------------------------------------------------------------
# DEPENDENCIES  (cached layer — see the note on ordering above)
# -----------------------------------------------------------------------------
# Only the requirements files are copied here, NOT the source. So editing
# app.js does not invalidate this layer and does not reinstall anything.
COPY requirements.txt requirements.lock.txt ./

# `uv` is a Rust reimplementation of pip: same job, roughly 10-100x faster,
# because it resolves in parallel and copies from a global cache instead of
# unpacking archives. Saurabh switched to it in Phase 1 for exactly this
# reason, so CI and the container use it too — one toolchain, one set of
# surprises.
#
# --system installs into the image's Python rather than a virtualenv. A venv
# inside a container is a layer of indirection protecting you from a conflict
# that cannot happen: nothing else lives in this image.
# ⚠ NOTE THE TRAILING `playwright==${PLAYWRIGHT_VERSION}`.
#
# That extra argument is what stops the drift. It gives the resolver a hard
# constraint, so `playwright>=1.48` from requirements.txt is satisfied by
# EXACTLY the version whose browsers this image contains — instead of by
# whatever was released this morning.
#
# This does not weaken the "pin the interpreter, float the libraries" rule from
# KI-6. That rule exists so a missing wheel can't break an install. Playwright
# is the one dependency that is genuinely coupled to something outside Python
# — the browser binaries on disk — so it is the one that must be pinned, and
# it's pinned to a value derived from the base image rather than typed twice.
RUN pip install --no-cache-dir uv==0.9.7 \
 && uv pip install --system --no-cache -r requirements.txt \
      "playwright==${PLAYWRIGHT_VERSION}"

# ---- Prove it, at BUILD time -------------------------------------------
# Two assertions, because the failure this catches was invisible until a user
# clicked Scan:
#
#   1. the installed package version is the one we pinned
#   2. Chromium ACTUALLY LAUNCHES
#
# The second is the one that matters. Version numbers agreeing is a proxy;
# launching the browser is the thing we actually care about. It costs about
# two seconds and turns a runtime failure into a failed build — which is
# always the better place for a failure to happen.
#
# `--no-sandbox` here ONLY: the build runs as root inside BuildKit, where
# Chromium refuses to start sandboxed. At runtime the sandbox stays on.
RUN set -eux; \
    INSTALLED="$(python -c 'import importlib.metadata as m; print(m.version("playwright"))')"; \
    test "$INSTALLED" = "$PLAYWRIGHT_VERSION" \
      || { echo "MISMATCH: package $INSTALLED vs image $PLAYWRIGHT_VERSION"; exit 1; }; \
    python -c "\
from playwright.sync_api import sync_playwright; \
p = sync_playwright().start(); \
b = p.chromium.launch(args=['--no-sandbox']); \
print('chromium', b.version, 'launches OK'); \
b.close(); p.stop()"

# NOTE: no `playwright install` step. The base image already ships the browser
# binaries. Running it would re-download ~150MB for nothing.


# -----------------------------------------------------------------------------
# APPLICATION SOURCE  (rebuilt on every code change — deliberately last)
# -----------------------------------------------------------------------------
# .dockerignore decides what "." actually means here. Without it this would
# copy .git, .venv, the local database and every scan JSON into the image —
# hundreds of megabytes, plus your real data baked into a shareable artifact.
COPY api/     ./api/
COPY scanner/ ./scanner/
COPY frontend/ ./frontend/

# Copying named directories rather than `COPY . .` is a small discipline with
# a real payoff: a new top-level file cannot end up in the image by accident.
# If you add a folder that belongs in the image, you have to say so here —
# which is exactly the moment to ask whether it should be.


# -----------------------------------------------------------------------------
# RUNTIME USER AND WRITABLE PATHS
# -----------------------------------------------------------------------------
# The Playwright base image already defines a non-root user, `pwuser`.
#
# WHY NOT RUN AS ROOT: root inside a container is, for several kinds of
# escape, root on the host. Running as an unprivileged user means a Chromium
# exploit gets an account that owns nothing. This costs nothing and is the
# first thing a security reviewer checks.
#
# /data must exist and be writable BEFORE we drop privileges — pwuser cannot
# create a directory at the filesystem root.
RUN mkdir -p /data && chown -R pwuser:pwuser /data /app

USER pwuser

# VOLUME marks /data as living outside the image's layers. Its real purpose
# here is documentation: it tells anyone reading this file (and `docker
# inspect`) that this path holds state which must survive the container.
VOLUME ["/data"]

# EXPOSE publishes nothing by itself — it is metadata saying "this image
# serves on 8000". The actual publishing is `-p 8000:8000` at run time.
# Worth knowing precisely, because "I added EXPOSE, why can't I connect?" is
# a common interview-adjacent confusion.
EXPOSE 8000


# -----------------------------------------------------------------------------
# HEALTHCHECK
# -----------------------------------------------------------------------------
# Docker's default liveness test is "is PID 1 still running?", which is a much
# weaker claim than "is this thing working". A Python process whose database
# has gone unreadable is still running, and Docker would call it healthy.
#
# /health does a real query (see main.py), so this distinguishes "up" from
# "actually working". Orchestrators use the result to decide whether to route
# traffic here and whether to restart it.
#
# ⚠ NOTE THE PATH: /health, not /api/health. It is the one system endpoint that
# sits outside the /api prefix. This was written as /api/health first, and the
# only reason it isn't still wrong is that the container was actually STARTED
# and asked — which is precisely the argument for the smoke-test job in CI.
# A healthcheck pointed at a 404 fails silently in the worst way: the container
# reports unhealthy forever and the app is completely fine.
#
#   --start-period=20s  grace period; failures during it don't count. Playwright
#                       imports are slow, and without this the container gets
#                       killed and restarted forever while it's simply booting.
#   --retries=3         one blip on a busy box shouldn't trigger a restart.
#
# python -c rather than curl: the base image may not ship curl, and depending
# on a tool you didn't install is how healthchecks fail in production only.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"


# -----------------------------------------------------------------------------
# START COMMAND
# -----------------------------------------------------------------------------
# EXEC FORM (a JSON array), not shell form. With the shell form Docker runs
# `/bin/sh -c "..."`, so PID 1 is the shell and it does NOT forward signals:
# `docker stop` sends SIGTERM, the shell ignores it, Docker waits 10 seconds
# and SIGKILLs. Every stop takes ten seconds and the app never shuts down
# cleanly. The exec form makes uvicorn itself PID 1 and the signal arrives.
#
# No --reload: that watches the filesystem for changes, which in production
# costs CPU to detect edits that will never happen.
#
# One worker on purpose. SQLite tolerates concurrent readers but serialises
# writers, and a scan pins a CPU for ~40 seconds anyway — more workers on a
# t2.micro would fight each other for the same core. Scaling this properly
# means moving scans to a queue, which is a real next step, not a tweak.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
