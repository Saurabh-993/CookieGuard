# Deploying CookieGuard to AWS EC2

A step-by-step playbook. Roughly **45 minutes**, and **₹0 / $0** if you stay
inside the free tier and use a DuckDNS hostname.

Every step says *why*, not just *what* — the goal is that you can explain this
in an interview, not just repeat it.

---

## What you end up with

```
        internet
           │
           │  HTTPS (443)
           ▼
   ┌───────────────────────────────────────────────┐
   │  EC2 t3.micro · Ubuntu 24.04 · 1 GB RAM       │
   │                                               │
   │   ┌─────────┐        ┌──────────────────┐     │
   │   │  Caddy  │───────►│   cookieguard    │     │
   │   │  :443   │  :8000 │   (no public     │     │
   │   │  :80    │        │    port at all)  │     │
   │   └─────────┘        └────────┬─────────┘     │
   │    TLS certs                  │               │
   │    (Let's Encrypt,        /data volume        │
   │     auto-renewed)         (SQLite, survives   │
   │                            redeploys)         │
   └───────────────────────────────────────────────┘
                    ▲
                    │  docker pull
           ghcr.io/<you>/cookieguard:latest
                    ▲
                    │  built + smoke-tested
              GitHub Actions
```

**The server never builds anything.** It pulls the exact image CI already
tested. No source code, no compilers, no build tools on the internet-facing
box — and what runs in production is bit-identical to what was tested, rather
than "the same source, rebuilt, hopefully identically".

---

## Cost — read this before clicking anything

| Item | Free tier | After 12 months |
|---|---|---|
| t3.micro, 750 hrs/month | **$0** | ~$7.50/mo |
| 30 GB EBS storage | **$0** | ~$2.40/mo |
| Data transfer out, first 100 GB | **$0** | $0.09/GB |
| Elastic IP **attached to a running instance** | **$0** | $0 |
| Elastic IP **not attached** | ⚠ **charged** | charged |
| DuckDNS hostname | **$0** | $0 |
| Let's Encrypt certificate | **$0** | $0 |

**750 hours/month is one instance running 24/7** (a month is ~730 hours). Two
instances will exceed it.

> ⚠ **The classic free-tier bill** is an Elastic IP you allocated and then
> stopped the instance. AWS charges for *idle* addresses specifically to stop
> people hoarding them. If you stop the instance, release the IP too.

**Set a billing alarm in Step 2. Do not skip it.**

---

## Step 1 — Create the AWS account

<https://aws.amazon.com> → *Create an AWS Account*

You need a credit/debit card. AWS charges about ₹2 / $1 to verify it and
refunds it. Indian cards sometimes fail here — a different card usually works.

When it asks for a support plan, choose **Basic — free**.

### Then immediately stop using the root account

The email address you signed up with is the **root user**. It can close the
account, change billing, and cannot be restricted. It is not for daily work.

1. **Enable MFA on root.** Search "IAM" → *Security recommendations* → *Add
   MFA*. Use Google Authenticator or Authy. Do this now — a compromised root
   account can run up thousands of dollars before you notice.
2. **Create an admin user for yourself.** IAM → *Users* → *Create user* →
   tick *Provide user access to the Console* → attach the
   `AdministratorAccess` policy.
3. Sign out. Sign back in as that user. Use it from here on.

> **Why this matters in an interview:** "I don't use the root account" is a
> one-line answer that signals you've read the security basics. The principle
> is *least privilege* — use the weakest credential that can do the job.

---

## Step 2 — Billing alarm (do not skip)

The free tier is not a spending cap. Exceed it and you are billed, silently.

1. Top-right menu → **Billing and Cost Management**
2. **Billing preferences** → tick *Receive AWS Free Tier alerts* → your email
3. **Budgets** → *Create budget* → *Zero spend budget* → your email

You now get an email the moment anything costs money at all.

---

## Step 3 — Create an SSH key pair

**EC2 → Network & Security → Key Pairs → Create key pair**

- Name: `cookieguard-key`
- Type: **ED25519** (shorter and stronger than RSA; every modern system
  supports it)
- Format: **.pem**

The `.pem` downloads once. **AWS keeps only the public half — if you lose the
file, you cannot get it back**, and the only recovery is detaching the disk and
attaching it to another instance.

Move it somewhere sensible:

```powershell
mkdir $HOME\.ssh -Force
move $HOME\Downloads\cookieguard-key.pem $HOME\.ssh\
```

### Fix the permissions — Windows gets this wrong

SSH refuses to use a key that other users can read, and Windows files are
readable by several accounts by default. The error is
`UNPROTECTED PRIVATE KEY FILE` and it stops you dead.

```powershell
icacls "$HOME\.ssh\cookieguard-key.pem" /inheritance:r
icacls "$HOME\.ssh\cookieguard-key.pem" /grant:r "$($env:USERNAME):(R)"
```

`/inheritance:r` strips the permissions the folder passed down; the second line
grants read to you alone.

> **What a key pair actually is:** you keep the private half, AWS puts the
> public half on the server. To log in, the server sends a challenge that only
> the private key can answer. The private key never travels. This is why key
> auth beats passwords — there is nothing to guess, intercept, or reuse
> elsewhere.

---

## Step 4 — Launch the instance

**EC2 → Instances → Launch instances**

| Field | Value | Why |
|---|---|---|
| Name | `cookieguard` | |
| AMI | **Ubuntu Server 24.04 LTS** | LTS = 5 years of security updates. The setup script targets it. |
| Architecture | **64-bit (x86)** | ⚠ **Not Arm.** Our image is built for amd64 by GitHub's runners. An Arm instance cannot run it. |
| Instance type | **t3.micro** (or t2.micro) | Whichever your region marks *Free tier eligible*. |
| Key pair | `cookieguard-key` | |
| Storage | **16 GiB gp3** | Default 8 GiB is too tight — our image alone is ~2 GB. 30 GiB is free; 16 is comfortable. |

### Security group — this is your real firewall

Create a new one named `cookieguard-sg`:

| Type | Port | Source | Why |
|---|---|---|---|
| SSH | 22 | **My IP** | ⚠ **Not** `0.0.0.0/0`. Port 22 open to the world receives thousands of automated login attempts per day. |
| HTTP | 80 | `0.0.0.0/0` | Let's Encrypt's challenge, and the redirect to HTTPS. Closing it breaks certificate renewal. |
| HTTPS | 443 | `0.0.0.0/0` | The actual site. |

> **"My IP" and home internet.** Your ISP may change your address. If SSH
> suddenly hangs, come back here and update the rule — that's the trade-off
> for not being scanned constantly, and it's the right trade.

A **security group is a stateful firewall that lives in AWS, not on the
instance** — so it still protects you if the instance is compromised. Stateful
means a reply to an allowed inbound request is automatically allowed out; you
don't write outbound rules for it.

Click **Launch instance**.

---

## Step 5 — Connect

Copy the **Public IPv4 address** from the instance page.

```powershell
ssh -i $HOME\.ssh\cookieguard-key.pem ubuntu@YOUR_IP
```

`ubuntu` is the default user on Ubuntu AMIs (Amazon Linux uses `ec2-user`).

First connection asks about the host's fingerprint — that's SSH telling you it
has never seen this server before and cannot verify it isn't an impostor. Type
`yes`.

---

## Step 6 — Bootstrap the server

```bash
curl -fsSL https://raw.githubusercontent.com/Saurabh-993/CookieGuard/main/deploy/setup-server.sh -o setup.sh
less setup.sh        # read it first. always, for any script from the internet.
bash setup.sh
```

This installs Docker, configures a firewall, clones the repo — and creates a
**2 GB swap file**, which is the step that makes a 1 GB instance actually
usable. Chromium needs 400–700 MB per scan; without swap the kernel's OOM
killer terminates something, often `sshd`, and you can't log in to investigate.

Then **log out and back in** — Docker group membership only applies to a new
session.

```bash
exit
ssh -i $HOME\.ssh\cookieguard-key.pem ubuntu@YOUR_IP
docker ps          # should work without sudo
```

---

## Step 7 — Point a hostname at it

HTTPS certificates are issued to *names*, not IP addresses. You need a
hostname.

### Free: DuckDNS

1. <https://duckdns.org> → sign in with GitHub
2. Type a name, e.g. `cookieguard-saurabh` → **add domain**
3. Paste your EC2 public IP into the `current ip` box → **update ip**

You now have `cookieguard-saurabh.duckdns.org`, free, forever.

### Or ~$2/year for a real domain

A `.xyz` from Porkbun or Namecheap is a couple of dollars and looks better on a
CV. Add an **A record** pointing `@` at your IP.

### ⚠ Verify before continuing

```bash
dig +short cookieguard-saurabh.duckdns.org
```

It must print your EC2 IP. If it prints nothing, wait a few minutes.

**Do not start the stack before this resolves.** Caddy asks Let's Encrypt for a
certificate immediately, Let's Encrypt proves ownership by fetching a file from
that hostname, and **failures are rate-limited to 5 per hour per hostname**.
Retrying makes recovery slower, not faster.

---

## Step 8 — Make the image public (once)

GitHub packages are private by default, so `docker pull` on the server would
need a login.

1. Your GitHub profile → **Packages** → `cookieguard`
2. *Package settings* → **Change visibility** → **Public**

> **Is public safe?** For this project, yes — the image contains only code
> that's already in a public repo, with configuration supplied at runtime. That
> is exactly *why* Phase 6 moved config into environment variables: there is
> nothing secret baked into the image to leak. If there were, the answer would
> be to keep it private and authenticate the pull.

---

## Step 9 — Deploy

```bash
cd ~/cookieguard/deploy
cp .env.example .env
nano .env
```

Set three lines — **note the lowercase in the image path**, registries reject
uppercase and GitHub usernames are usually capitalised:

```ini
SITE_ADDRESS=cookieguard-saurabh.duckdns.org
SITE_URL=https://cookieguard-saurabh.duckdns.org
GHCR_IMAGE=ghcr.io/saurabh-993/cookieguard:latest
```

Save (`Ctrl+O`, `Enter`, `Ctrl+X`), then:

```bash
chmod +x deploy.sh
./deploy.sh
```

The script checks DNS, pulls the image, starts both containers, waits for the
app to be healthy *inside* the container, and then checks HTTPS *from outside*.

Those last two are different claims. The laptop taught us that the hard way:
the container reported healthy for an hour while its port was never published.

**The first run takes ~60 seconds** while Caddy negotiates a certificate.

---

## Step 10 — Verify

```
https://YOUR-NAME.duckdns.org/dashboard/
```

A padlock, no warning. Then:

- [ ] `https://YOUR-NAME.duckdns.org/health` shows `environment: production`
- [ ] `http://` (no s) redirects to `https://` automatically
- [ ] Run a scan — this is the only check that proves Chromium works here
- [ ] `docker compose -f docker-compose.prod.yml restart` — the scan survives
- [ ] Direct IP on port 8000 is **refused**: `curl http://YOUR_IP:8000` should
      fail. If it succeeds, the app is exposed outside TLS.

Grade the TLS setup at <https://www.ssllabs.com/ssltest/> — you should get an
**A**. Worth a screenshot for the portfolio.

---

## Everyday commands

```bash
cd ~/cookieguard/deploy
C="docker compose -f docker-compose.prod.yml"

$C ps                    # what's running
$C logs -f cookieguard   # app logs
$C logs -f caddy         # TLS / proxy logs
$C restart               # restart everything
$C down                  # stop  (volumes survive)

git pull && ./deploy.sh  # deploy a new version
free -h                  # memory + swap. check after a scan.
df -h /                  # disk. our image is 2 GB.
```

---

## When it goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| SSH `UNPROTECTED PRIVATE KEY FILE` | Windows permissions | The `icacls` commands in Step 3 |
| SSH times out | Your IP changed | Update the security group's SSH rule |
| Browser: "can't reach this site" | DNS, or ports 80/443 closed | `dig +short`; check the security group |
| Padlock warning / no certificate | DNS wasn't ready when Caddy started | `$C logs caddy`. Fix DNS, then `$C restart caddy` |
| `too many certificates already issued` | Let's Encrypt rate limit | Wait for the window. Use the staging endpoint while debugging — see below |
| Scan starts then dies | Out of memory | `free -h`. Swap missing? Re-run the swap section of setup-server.sh |
| `no space left on device` | Old images | `docker image prune -a -f` |
| Everything 502 | App container down | `$C logs cookieguard` |

### Testing certificates without burning the rate limit

Let's Encrypt allows **5 duplicate certificates per week**. That is easy to
exhaust while debugging. Their staging endpoint has far higher limits and
issues untrusted certificates — the browser will warn, which is fine for
testing the *plumbing*.

Add to the top of `Caddyfile`, above the site block:

```
{
    acme_ca https://acme-staging-v02.api.letsencrypt.org/directory
}
```

Get it working, **remove those lines**, then `$C restart caddy` for a real
certificate.

---

## Shutting it down

**Stopping** an instance keeps the disk (still billed after free tier) and
releases the public IP — so your DNS breaks and the IP changes on restart.

**Terminating** deletes everything permanently.

```
EC2 → Instances → select → Instance state → Terminate
```

Then check for leftovers, which is where surprise bills come from:

- **Elastic IPs** — release any that aren't attached
- **EBS volumes** — should delete with the instance; confirm
- **Snapshots** — you have none unless you made them

---

## What to say about this in an interview

> "It runs as a container on EC2 behind Caddy, which terminates TLS and
> auto-renews Let's Encrypt certificates. The server never builds — it pulls
> the image GitHub Actions already built and smoke-tested, so what's in
> production is bit-identical to what was tested. The app container publishes
> no port; only the proxy is reachable. Config comes from environment
> variables, so the same image runs locally and in production with different
> CORS origins and database paths."

Then the details that show you actually did it:

- **1 GB of RAM and a headless Chromium** — needed a swap file, or the OOM
  killer takes out `sshd` and you can't even log in to diagnose it
- **Security group scoped to my IP for SSH**, world for 80/443 only
- **A billing alarm before launching anything**
- **Deploy-by-SHA** rather than `:latest`, because rollback should be editing
  one line rather than guesswork
