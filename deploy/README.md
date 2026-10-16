# deploy/: DV02 on the always-on VM

Today the fund runs on the owner's Mac, which must be awake at 14:35 Central.
These files move it to the owner's Oracle Cloud VM (`docs/PLAN.md` §12,
Phase 2). Everything still binds to 127.0.0.1. The only way in from outside is
a Cloudflare tunnel with Cloudflare Access in front.

| File | What |
|---|---|
| `systemd/dv02-api.service` | The API, also serving the built dashboard (`DV02_WEB_DIST`), on 127.0.0.1:8000 |
| `systemd/dv02-runner.service`, `.timer` | The daily run, 15:35 America/New_York, Mon–Fri, whatever the VM's zone |
| `systemd/dv02-api-check.service`, `.timer` | Every 5 min: the API answers, then its heartbeat ping |
| `systemd/dv02-backup.service`, `.timer` | Daily 18:30 ET: the engine's state (and Postgres) to R2 |
| `systemd/dv02-alert@.service` | Any DV02 unit that fails pings its heartbeat's `/fail` |
| `bin/heartbeat`, `bin/backup`, `bin/migrate` | What those units run |
| `bin/install` | Installs the units; starts the API, its check and the backup, **not** the daily run |
| `bin/check` | Checks the files: script syntax, calendar specs, `systemd-analyze verify` |
| `dv02.env.example` | `/etc/dv02/dv02.env`: allowed host, heartbeat URLs, backup remote, DB user |
| `cloudflared/config.yml.example` | The tunnel: one hostname to 127.0.0.1:8000 |
| `postgres/docker-compose.yml` | Postgres 16 on 127.0.0.1:5432 (nothing writes to it yet) |

The units expect the checkout at `/opt/dv02`, owned by a `dv02` user.

## Before anything: read the VM

The owner signs in to Oracle Cloud in Chrome; Claude reads Compute → Instances
(`docs/HANDOFF.md`, "Using Chrome"). Record the following in `docs/HANDOFF.md`:
- the shape and CPU architecture
- OCPUs, memory, boot volume and OS image
- the public IP and the security list
- anything else already running there

The architecture decides the first check below.

## Set up (the owner, over SSH)

1. **Packages:**
   - Python 3.12 with venv
   - Node 22 and npm
   - Docker with the compose plugin
   - git, curl, rclone
   - cloudflared
   - the Postgres client (`psql`, `pg_dump`)
2. **Check the gateway image runs on this CPU.**
   `docker manifest inspect ghcr.io/gnzsnz/ib-gateway:stable | grep architecture`
   must list the VM's (`arm64` on an Ampere A1 shape, `amd64` on x86). If it
   doesn't, stop: the gateway needs another image, and that is a code change.
3. **The checkout:**
   ```bash
   sudo useradd --system --home-dir /opt/dv02 --shell /bin/bash dv02
   sudo usermod -aG docker dv02                         # the gateway and Postgres run in Docker
   sudo install -d -o dv02 -g dv02 /opt/dv02            # empty, so the clone can go in it
   sudo -u dv02 git clone https://github.com/samgeng14/Trading-Workspace.git /opt/dv02
   cd /opt/dv02 && sudo -u dv02 git checkout claude/serene-babbage-0204ht   # or main, once merged
   sudo -u dv02 bin/tradeit setup                       # .venv, the C++ engine, npm packages (written for the Mac:
                                                        # note anything that breaks on Linux in docs/HANDOFF.md)
   sudo -u dv02 bash -c 'cd apps/web && npm run build'  # the dashboard the API serves
   ```
4. **Secrets.** The owner types these; they never go in git or chat.
   ```bash
   sudo install -d -m 750 -o root -g dv02 /etc/dv02
   sudo install -m 640 -o root -g dv02 deploy/dv02.env.example /etc/dv02/dv02.env   # then edit it
   sudo -u dv02 bin/tradeit login   # the IBKR paper login, to ibgw/.env (mode 600)
   sudo -u dv02 bin/tradeit flex    # the Flex query and token, to .env
   ```
5. **Postgres** (optional until something writes to it):
   ```bash
   sudo sh -c 'umask 077; printf %s "<the DV02_DB_PASSWORD value>" > /etc/dv02/pg_password'
   sudo docker compose -f deploy/postgres/docker-compose.yml up -d
   sudo -u dv02 bash -c 'set -a; . /etc/dv02/dv02.env; set +a; deploy/bin/migrate'
   ```
6. **The state, copied from the Mac.** These folders are git-ignored and exist
   only there. Without them the drift gate cold-starts (it rebalances at once)
   and the equity curve loses its history before Flex. With the Mac's run
   finished for the day:
   ```bash
   # on the Mac, from ~/Code/deskd-live
   rsync -av trading-engine/artifacts trading-engine/data/broker trading-engine/data/market/cleaned \
     <vm>:/tmp/dv02-state/
   # on the VM
   sudo rsync -a /tmp/dv02-state/ /opt/dv02/trading-engine/ && sudo chown -R dv02:dv02 /opt/dv02/trading-engine
   ```
7. **Install:** `sudo deploy/bin/install`. Then
   `curl -fsS 127.0.0.1:8000/api/v1/status` answers, and
   `systemctl status dv02-api` is active.
8. **The tunnel.**
   - `cloudflared tunnel login`
   - `cloudflared tunnel create dv02`
   - `cloudflared tunnel route dns dv02 <hostname>`
   - Write `/etc/cloudflared/config.yml` from the example, then run
     `sudo cloudflared service install`.
   - Put the same hostname in `DV02_ALLOWED_HOSTS` and run
     `sudo systemctl restart dv02-api`.
   - In Cloudflare Zero Trust → Access → Applications, add a self-hosted
     application for the hostname. Its one policy allows only the owner's
     email. Check from the phone: the login page, then the dashboard.
9. **Heartbeats.** Create one healthchecks.io check per job, and put their
   ping URLs in `/etc/dv02/dv02.env`:
   - **runner:** cron `35 15 * * 1-5`, zone America/New_York, grace 1 h
   - **api:** every 5 min, grace 10 min
   - **backup:** daily, grace 2 h

   A missing ping is the alert. So is a unit failing, through `dv02-alert@`.
10. **Backups.** `rclone config` sets up an S3 remote, provider Cloudflare, with
    an R2 API token, named as in `DV02_BACKUP_REMOTE`. Then
    `sudo systemctl start dv02-backup` once, and check the bucket.

## Dry run on the VM

```bash
sudo -u dv02 bin/tradeit gateway start   # only once the Mac's gateway is stopped: one session per login
sudo -u dv02 bin/tradeit doctor
sudo -u dv02 bin/tradeit refresh         # the whole pipeline, no orders
```

Compare the VM's newest targets
(`trading-engine/data/market/cleaned/targets/etf_targets_monthly.csv`) with the
Mac's from the same day. They should match.

## Cut over (one trading day, before 15:35 ET)

1. On the Mac: `bin/tradeit schedule remove` and `bin/tradeit gateway stop`.
   IBKR allows one session per username. The Mac's container restarts by
   itself (`restart: unless-stopped`) unless it is stopped this way.
2. On the VM: `sudo -u dv02 bin/tradeit gateway start`, then
   `sudo systemctl enable --now dv02-runner.timer`, then
   `systemctl list-timers dv02-runner.timer`. The next run should show at
   15:35 New York time.
3. That evening, check:
   - the run log (`trading-engine/artifacts/runs/`)
   - the runner check on healthchecks.io
   - the dashboard through the tunnel

**Rollback:**
1. On the VM: `sudo systemctl disable --now dv02-runner.timer` and
   `sudo -u dv02 bin/tradeit gateway stop`.
2. On the Mac: `bin/tradeit gateway start` and `bin/tradeit schedule install`.

## Known gaps

- The dashboard's schedule panel shows the 15:35 ET time and checks the timer
  (`systemctl is-enabled dv02-runner.timer`). It doesn't read the timer's own
  next run, so a timer edited by hand to another time would show wrong.
- Nothing writes to Postgres yet. The run logs, NAV cache and expected book
  are still files, which the backup covers.
