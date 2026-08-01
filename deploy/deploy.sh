#!/usr/bin/env bash
# =============================================================================
# CookieGuard — deploy / update (Phase 7)
# =============================================================================
#
#     cd ~/cookieguard/deploy && ./deploy.sh
#
# WHAT A DEPLOY IS HERE
# ---------------------
#   1. pull the image CI already built and smoke-tested
#   2. recreate the containers
#   3. WAIT, and CHECK IT ACTUALLY WORKS
#   4. if it doesn't, say so loudly and show the logs
#
# Step 3 is the one people leave out, and it's the one that matters. A deploy
# script that ends at `docker compose up -d` reports success the moment Docker
# accepts the request — before the app has started, and regardless of whether
# it ever does. You then find out from a user.
#
# Same principle as the CI smoke test: starting is not working.
# =============================================================================

set -euo pipefail
cd "$(dirname "$0")"          # run from this script's directory, whatever the cwd

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
say()  { echo -e "\n${GREEN}==>${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
die()  { echo -e "${RED}[✗]${NC} $1" >&2; exit 1; }

COMPOSE="docker compose -f docker-compose.prod.yml"

# ---- Preflight -------------------------------------------------------------
# Check assumptions BEFORE changing anything. A deploy that fails at step one
# leaves the old version running; a deploy that fails halfway leaves you with
# neither.
[[ -f .env ]] || die "No .env file. Run: cp .env.example .env && nano .env"

# shellcheck disable=SC1091
set -a; source .env; set +a     # `set -a` exports everything sourced

[[ -n "${SITE_ADDRESS:-}" ]] || die "SITE_ADDRESS is not set in .env"
[[ -n "${GHCR_IMAGE:-}"   ]] || die "GHCR_IMAGE is not set in .env"

# ---- DNS check -------------------------------------------------------------
# ⚠ The single most common Phase 7 failure, and it is worth failing fast on.
# Caddy proves domain ownership by having Let's Encrypt fetch a file over HTTP.
# If DNS doesn't point here, that fails — and Let's Encrypt rate-limits
# failures, so retrying repeatedly makes recovery slower, not faster.
say "Checking DNS for ${SITE_ADDRESS}"
RESOLVED=$(dig +short "$SITE_ADDRESS" | tail -1 || true)
PUBLIC_IP=$(curl -s --max-time 5 https://checkip.amazonaws.com || echo "?")

if [[ -z "$RESOLVED" ]]; then
  die "${SITE_ADDRESS} does not resolve. Point it at ${PUBLIC_IP} first."
elif [[ "$RESOLVED" != "$PUBLIC_IP" ]]; then
  warn "DNS says ${RESOLVED}, this machine is ${PUBLIC_IP}."
  warn "If you just changed it, wait a few minutes for propagation."
  read -rp "Continue anyway? [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]] || exit 1
else
  echo "    ${SITE_ADDRESS} -> ${RESOLVED} ✓"
fi

# ---- Pull ------------------------------------------------------------------
# Pull BEFORE stopping anything. If the registry is unreachable or the tag is
# wrong, we find out while the old version is still serving traffic.
say "Pulling ${GHCR_IMAGE}"
$COMPOSE pull

# ---- Restart ---------------------------------------------------------------
# `up -d` recreates only containers whose image or config changed, so Caddy
# usually keeps running and its certificates are untouched.
say "Starting containers"
$COMPOSE up -d --remove-orphans

# ---- Verify ----------------------------------------------------------------
# Poll rather than sleep — faster when it's ready, more patient when it isn't.
say "Waiting for the app to become healthy"
for i in $(seq 1 45); do
  if docker exec cookieguard python -c \
      "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3).status==200 else 1)" \
      2>/dev/null; then
    echo "    healthy after ${i}s ✓"
    break
  fi
  [[ $i -eq 45 ]] && {
    echo -e "${RED}[✗]${NC} App never became healthy. Logs:"
    $COMPOSE logs --tail 60 cookieguard
    exit 1
  }
  sleep 1
done

# ---- Verify from the OUTSIDE ----------------------------------------------
# The check above ran INSIDE the container, so it proves the app works and
# says nothing about whether the internet can reach it. Those are different
# claims, and conflating them cost us an hour on the laptop — the container was
# reporting "healthy" while its port was never published.
say "Checking HTTPS from outside"
for i in $(seq 1 60); do
  if curl -fsS --max-time 5 "https://${SITE_ADDRESS}/health" > /tmp/health.json 2>/dev/null; then
    echo "    https://${SITE_ADDRESS}/health responded ✓"
    cat /tmp/health.json; echo
    break
  fi
  # First deploy takes longest: Caddy is negotiating with Let's Encrypt.
  [[ $i -eq 1 ]] && echo "    (first run obtains a TLS certificate — up to a minute)"
  [[ $i -eq 60 ]] && {
    warn "No HTTPS response yet. Usually one of:"
    warn "  · DNS not propagated       -> dig +short ${SITE_ADDRESS}"
    warn "  · port 80/443 not open     -> check the EC2 security group"
    warn "  · Let's Encrypt rate limit -> see Caddy's logs below"
    $COMPOSE logs --tail 40 caddy
    exit 1
  }
  sleep 2
done

# ---- Clean up --------------------------------------------------------------
# Old images pile up — ours is ~2 GB each and the free tier gives 30 GB. A few
# deploys and the disk is full, which manifests as everything failing at once.
say "Removing unused images"
docker image prune -f > /dev/null

cat <<EOF

$(echo -e "${GREEN}")============================================================$(echo -e "${NC}")
 Deployed.   https://${SITE_ADDRESS}/dashboard/
$(echo -e "${GREEN}")============================================================$(echo -e "${NC}")

  running : ${GHCR_IMAGE}
  disk    : $(df -h / | awk 'NR==2{print $4}') free
  memory  : $(free -h | awk '/^Mem:/{print $7}') available

  logs      : $COMPOSE logs -f
  restart   : $COMPOSE restart
  stop      : $COMPOSE down          (data survives)

EOF
