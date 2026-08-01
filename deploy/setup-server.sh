#!/usr/bin/env bash
# =============================================================================
# CookieGuard — one-time EC2 bootstrap (Phase 7)
# =============================================================================
#
# Run ONCE on a fresh Ubuntu 24.04 instance:
#
#     curl -fsSL https://raw.githubusercontent.com/Saurabh-993/CookieGuard/main/deploy/setup-server.sh -o setup.sh
#     less setup.sh          # ← read it before you run it. Always.
#     bash setup.sh
#
# WHY A SCRIPT AND NOT A LIST OF COMMANDS IN A README
# ---------------------------------------------------
# Because you will do this more than once. The first instance will be
# misconfigured in some way you only discover later, and you will rebuild it.
# A script means the second attempt is identical to the first — and it doubles
# as documentation that cannot go out of date, because it's the thing that ran.
#
# This is the same argument as the Dockerfile, one level up the stack. It's
# also the entry point to Infrastructure as Code: Terraform and Ansible are
# this idea taken seriously.
# =============================================================================

# ---- Shell safety ----------------------------------------------------------
# These three options should be on the first line of every bash script you
# write, and almost no tutorial includes them:
#
#   -e  exit immediately if any command fails. WITHOUT THIS, a failed install
#       is followed by the script cheerfully continuing and "succeeding".
#   -u  treat an unset variable as an error. Catches typos: `$DOKCER_VERSION`
#       would otherwise silently expand to "".
#   -o pipefail  a pipeline fails if ANY command in it fails, not just the
#       last. `curl bad-url | bash` normally reports success, because `bash`
#       succeeded at running nothing.
set -euo pipefail

# Colours, so the important lines stand out in a wall of apt output.
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
say()  { echo -e "\n${GREEN}==>${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
die()  { echo -e "${RED}[✗]${NC} $1" >&2; exit 1; }

[[ $EUID -eq 0 ]] && die "Do not run this as root. Run it as 'ubuntu'; it uses sudo where needed."


# =============================================================================
# 1. SYSTEM UPDATE
# =============================================================================
say "Updating system packages"
sudo apt-get update -qq
sudo apt-get upgrade -y -qq

# Unattended-upgrades installs SECURITY patches automatically. On a machine
# with a public IP that you will inevitably stop logging into, this is the
# single highest-value line in the script. Unpatched servers are compromised by
# automated scanning, not by anyone targeting you specifically.
say "Installing base tools"
# dnsutils provides `dig`, which deploy.sh uses to verify DNS before starting
# Caddy. Ubuntu's cloud images don't include it, and a deploy script that
# fails with "dig: command not found" is a poor first impression.
sudo apt-get install -y -qq git curl dnsutils ca-certificates gnupg

say "Enabling automatic security updates"
sudo apt-get install -y -qq unattended-upgrades
sudo dpkg-reconfigure -f noninteractive unattended-upgrades


# =============================================================================
# 2. SWAP  —  THE STEP THAT MAKES A 1 GB INSTANCE VIABLE
# =============================================================================
# ⚠ Skip this and scans will fail unpredictably.
#
# t3.micro has 1 GB of RAM. Chromium uses 400-700 MB during a scan, on top of
# Python, uvicorn, Caddy and the OS. When Linux runs out of memory it invokes
# the OOM KILLER, which picks a process and terminates it — and its scoring
# frequently picks sshd or Caddy rather than the process that caused the
# problem. You then cannot log in to find out what happened.
#
# Swap is disk used as overflow memory. It is far slower than RAM (an SSD is
# ~1000x slower), so this is NOT "more memory" — it is a shock absorber for
# short spikes, which is exactly the shape of a scan.
#
# AWS does not create swap on Ubuntu AMIs by default. Almost every "my t2.micro
# keeps dying" thread ends here.
if [[ -f /swapfile ]]; then
  say "Swap already configured — skipping"
else
  say "Creating a 2 GB swap file"
  # fallocate reserves the space instantly rather than writing 2 GB of zeros.
  sudo fallocate -l 2G /swapfile
  # 600 = readable/writable by root only. The kernel refuses to use a swap file
  # that other users can read — it would otherwise be a way to read memory
  # belonging to other processes.
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  # /etc/fstab makes it survive a reboot. Without this line the swap silently
  # disappears the first time the instance restarts, and the problem comes back
  # weeks later looking completely new.
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab > /dev/null

  # swappiness: how eagerly the kernel moves pages to swap. Default is 60,
  # tuned for desktops. On a server we want RAM used for RAM and swap kept as
  # an emergency reserve, so 10.
  echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swap.conf > /dev/null
  sudo sysctl -q vm.swappiness=10
fi


# =============================================================================
# 3. DOCKER
# =============================================================================
# From Docker's own repository, NOT Ubuntu's `docker.io` package — that one is
# usually several versions behind and ships without the `compose` plugin, so
# `docker compose` (v2, the current syntax) simply doesn't exist.
if command -v docker &> /dev/null; then
  say "Docker already installed — skipping"
else
  say "Installing Docker Engine + Compose plugin"

  sudo install -m 0755 -d /etc/apt/keyrings
  # The GPG key lets apt verify that packages genuinely came from Docker.
  # Adding a repository WITHOUT its key means trusting whatever a network
  # attacker hands you — this is the step people skip with `--allow-unauthenticated`.
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg

  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

  sudo apt-get update -qq
  sudo apt-get install -y -qq \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

  # Lets you run docker without sudo.
  #
  # ⚠ BE HONEST ABOUT THIS ONE: the docker group is equivalent to root. Anyone
  # in it can run `docker run -v /:/host` and read or modify the entire
  # filesystem. It is convenience, not a privilege reduction, and on a shared
  # machine you would not do it. On a single-user instance it's a reasonable
  # trade, but know what you're trading.
  sudo usermod -aG docker "$USER"
  warn "You must log out and back in for the docker group to take effect."
fi

# Docker's default log driver has NO size limit, so on a long-running host the
# JSON log files grow until the disk is full. This is a genuinely common cause
# of "the server died and nothing in the app changed". Our compose files set
# limits per service; this is the belt-and-braces default for anything else.
say "Capping Docker log sizes globally"
sudo mkdir -p /etc/docker
echo '{"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"3"}}' \
  | sudo tee /etc/docker/daemon.json > /dev/null
sudo systemctl restart docker
sudo systemctl enable docker    # start on boot


# =============================================================================
# 4. FIREWALL
# =============================================================================
# The EC2 SECURITY GROUP is the real firewall and lives in AWS, outside the
# instance — so it protects you even if the instance is compromised. ufw here
# is a second layer.
#
# Defence in depth: two independent controls that must both fail. Also useful
# if you later move this to a VPS with no security group at all.
say "Configuring the local firewall (ufw)"
sudo apt-get install -y -qq ufw
sudo ufw --force reset > /dev/null

sudo ufw default deny incoming      # ⚠ deny by default, allow by exception.
sudo ufw default allow outgoing     #    The opposite order is how ports leak.

# ⚠ ALLOW SSH FIRST. Enabling a deny-by-default firewall without this locks you
# out of your own server instantly, and the only recovery is detaching the disk
# from AWS. People do this. Frequently.
sudo ufw allow 22/tcp   comment 'SSH'
sudo ufw allow 80/tcp   comment 'HTTP - Lets Encrypt challenge + redirect'
sudo ufw allow 443/tcp  comment 'HTTPS'
sudo ufw allow 443/udp  comment 'HTTP/3 QUIC'

sudo ufw --force enable
sudo ufw status verbose


# =============================================================================
# 5. PROJECT DIRECTORY
# =============================================================================
say "Fetching deployment files"
if [[ -d ~/cookieguard/.git ]]; then
  cd ~/cookieguard && git pull --ff-only
else
  # A SHALLOW clone (--depth 1) fetches only the latest commit, not the whole
  # history. Faster, and the server has no use for history.
  git clone --depth 1 https://github.com/Saurabh-993/CookieGuard.git ~/cookieguard
fi

cd ~/cookieguard/deploy
[[ -f .env ]] || cp .env.example .env


# =============================================================================
# DONE
# =============================================================================
cat <<EOF

$(echo -e "${GREEN}")============================================================$(echo -e "${NC}")
 Server ready.
$(echo -e "${GREEN}")============================================================$(echo -e "${NC}")

  RAM     : $(free -h | awk '/^Mem:/{print $2}')
  Swap    : $(free -h | awk '/^Swap:/{print $2}')
  Disk    : $(df -h / | awk 'NR==2{print $4}') free
  Docker  : $(docker --version 2>/dev/null || echo 'log out and back in first')
  Public IP: $(curl -s --max-time 5 https://checkip.amazonaws.com || echo '?')

NEXT — in order, and the order matters:

  1. LOG OUT AND BACK IN.  (exit, then ssh in again)
     The docker group only applies to a new login session.

  2. Point your hostname at the IP above.
     DuckDNS: https://duckdns.org  — paste the IP, click update.

  3. Verify DNS has propagated BEFORE starting the stack:
         dig +short YOUR-NAME.duckdns.org
     It must print the IP above. If it prints nothing, wait and retry.

     ⚠ Starting Caddy before DNS resolves means the Let's Encrypt challenge
       fails. Let's Encrypt limits you to 5 failures per hour per hostname,
       so repeatedly retrying makes it worse rather than better.

  4. Edit deploy/.env with your hostname and image name:
         nano ~/cookieguard/deploy/.env

  5. Deploy:
         cd ~/cookieguard/deploy && ./deploy.sh

EOF
