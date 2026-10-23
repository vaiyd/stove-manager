# Stove Pilot

## What it is

Local control and scheduling for a Micronova pellet stove fitted with a Navel Wi-Fi module (Superior Maira profile). It ships:

- an HTTP API (`stove-pilot-api`, port 8710): status, start/stop, setpoint, power, clock, history, suspension, native chrono, safety restart;
- a web UI (`stove-pilot-ui`, port 8711, French copy): dashboard, history, settings, chrono editor, `/widget`;
- a discovery CLI (`stove-pilot`) to read and diff the module registers.

The weekly chrono runs on the stove itself. A host outage never stops the heating. The safety restart runs in the API process: once it is enabled in Reglages, that process can start the stove when the room stays at or below a threshold, and only while it is polling.

## Requirements

- Python 3.12 or newer (3.13 is the verified version; both are supported).
- Network access from the host to the Navel module (default `http://192.168.68.54`).
- Optional: Docker with Compose v2 for the recommended deployment.

## Setup

```powershell
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.lock
pip install --no-deps .
Copy-Item .env.example .env
```

On Linux: `python3 -m venv .venv && . .venv/bin/activate`, then the same `pip` commands and `cp .env.example .env`. For development use `pip install -e .` instead.

Edit `.env` (module MAC address, AP password from the module label). Never commit `.env`: it is git-ignored and holds the only secrets. The default gateway is `simulator`; set `STOVE_PILOT_GATEWAY=navel` only when you deliberately want to talk to the real stove. All variables are listed in `.env.example`.

## Discovery CLI

```powershell
stove-pilot identify                       # module device type and revision
stove-pilot read ram:0x01 eeprom:0x7D      # read registers
stove-pilot dump --out discovery\dump.json # RAM and EEPROM 0x00-0xFF as JSON
stove-pilot diff a.json b.json             # offline comparison of two dumps
stove-pilot watch ram:0x01 --interval 10   # poll until Ctrl+C
stove-pilot clock                          # stove clock vs PC clock (drift in seconds)
stove-pilot write ram:0x00 1 --confirm-attended   # attended only
```

## Running locally

```powershell
stove-pilot-api     # API on 127.0.0.1:8710
stove-pilot-ui      # UI on 0.0.0.0:8711 (talks to the API at STOVE_PILOT_UI_API_BASE_URL)
```

Or both at once on Windows: `powershell -ExecutionPolicy Bypass -File deploy\windows\run.ps1`. It starts the API, waits for `/api/v1/health`, starts the UI, opens the browser (`-NoBrowser` to skip) and stops both on Ctrl+C. `-WorkingDirectory` changes where `.env` is looked up (default: the project root).

Dev reload: add `-Dev`, or set `STOVE_PILOT_DEV=true` before each command. Saving a Python file restarts both apps. Saving a template, stylesheet, script or `fr.json` restarts the UI only. Saving a profile `.toml` or `.env` restarts the API. Refresh the browser once after a restart. Leave this off for the scheduled task: each API restart reconnects to the stove.

The supervisor is `src/stove_pilot/service/dev_server.py`. Its module docstring lists every seam. Delete that file and those seams to drop the feature, for example after moving development to a machine where another reloader is available. Docker and the scheduled task do not set `STOVE_PILOT_DEV`, so they never load the module.

## CSS build

`src/stove_pilot/service/ui/static/css/app.css` is committed, so the runtime needs no tools. Rebuild it only after changing templates or `input.css`:

```powershell
powershell -ExecutionPolicy Bypass -File tools\build-css.ps1
```

The script downloads the pinned Tailwind and daisyUI tools next to itself once (git-ignored); `tools\build-css.sh` is the Linux equivalent. The build is offline afterwards.

## Feature flags

`STOVE_PILOT_FEATURES` lists the optional programming features (JSON list). Today the only feature is `chrono` (native stove chrono, its editor, and the suspension participant). It is registered in `service/features.py`; only that file, `service/composition.py` and the feature's own router modules know it.

- Disable it at run time: `STOVE_PILOT_FEATURES=[]` in `.env`, then restart both services. `/chrono` and `/programming` return 404, `/summary` lists no programming, the chrono menu and the suspend button disappear; the dashboard, history, and settings keep working. The stove clock belongs to the common base and stays.
- Remove it from the code (rarely needed):
  1. Delete `core/chrono/`, `service/api/routers/chrono.py`, `service/api/schemas/chrono.py`, `service/ui/routes/chrono.py`, `service/ui/templates/chrono.html`, `service/ui/templates/partials/chrono_*.html`, and `service/ui/static/js/chrono-editor.js`.
  2. Remove the `"chrono"` entry from `service/features.py` and `service/ui/nav.py`, plus `build_chrono_repository` in `composition.py`.
  3. Remove the `chrono` keys from `i18n/fr.json`, then rebuild the CSS.
  4. Edit the shared files that name the feature: the `features` Literal and default in `common/settings.py`, the `Container` fields in `composition.py`, and this README.
  5. Check that `rg -il chrono src/` returns nothing (or only documented leftovers) and that both apps start.

## Deployment modes

The chrono runs on the stove in both modes, so a host outage never stops the heating.

- **Recommended: always-on host** (NAS or Raspberry Pi) running Docker with `restart: unless-stopped`. It keeps the clock auto-sync (the stove clock otherwise drifts about 8 minutes per day), the history, the suspension auto-resume, and the safety restart running.
- **On demand:** `deploy\windows\run.ps1` on the PC when needed. Press "Synchroniser" in the UI at least once a week to correct the clock drift. A suspension with "Jusqu'au" only resumes while the API runs, so keep the PC and the API up until that time.

### Docker

```bash
cp .env.example .env            # then set STOVE_PILOT_GATEWAY=navel and the module credentials
docker compose up -d --build    # builds one image, starts api (internal) and ui (8711)
docker compose logs -f
docker compose ps               # api turns "healthy", then ui starts
docker compose down             # keeps the stove-data volume (SQLite history)
```

- One image (`python:3.13-slim`, non-root user, no build tools in the final stage, `/data` volume) serves both services.
- `api` is not published; only `ui` publishes 8711. The data lives in the named volume `stove-data` mounted at `/data` (`STOVE_PILOT_DB_PATH=/data/stove-pilot.sqlite`).
- `.env` is read at run time through `env_file`; it is never copied into the image (`.dockerignore`).
- Raspberry Pi (64-bit): build on a PC with `docker buildx build --platform linux/arm64 -t stove-pilot:latest --load .`, or simply run `docker compose up -d --build` on the Pi.
- Update: pull the new sources, then `docker compose up -d --build`.

### Deployment: systemd (Linux without Docker)

```bash
sudo useradd --system --home /var/lib/stove-pilot --shell /usr/sbin/nologin stove-pilot
sudo python3 -m venv /opt/stove-pilot/venv
sudo /opt/stove-pilot/venv/bin/pip install -r requirements.lock
sudo /opt/stove-pilot/venv/bin/pip install --no-deps .
sudo install -d -m 0750 -o root -g stove-pilot /etc/stove-pilot
sudo install -m 0640 -o root -g stove-pilot .env.example /etc/stove-pilot/stove-pilot.env   # then edit it
sudo cp deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stove-pilot-api stove-pilot-ui
journalctl -u stove-pilot-api -f
```

Placeholders in the unit files: `User`/`Group` (`stove-pilot`), `/opt/stove-pilot/venv`, `/etc/stove-pilot/stove-pilot.env`, `/var/lib/stove-pilot` (created by `StateDirectory`, holds the database). Adapt them together. systemd strips double quotes in env files: write `STOVE_PILOT_FEATURES='["chrono"]'` or omit the line (the default is `["chrono"]`). The units use `Restart=always`, `NoNewPrivileges`, `ProtectSystem=strict`, `ReadWritePaths` limited to the data directory and `PrivateTmp`.

## History retention and display

- `STOVE_PILOT_HISTORY_RETENTION_DAYS=0` (default) keeps the history forever; a positive value keeps that many days and purges older temperature samples and events. Display alerts (one row per message code, from `GET /api/v1/history/alerts`) follow the same window: a cleared alert is deleted when its last appearance is older than the cutoff, and an alert still shown on the stove is kept. The History page lists that catalog without applying the 24 h / 7 d / 30 d tabs. The daily backup copies the whole SQLite file, so alert rows are included with no extra step.
- `STOVE_PILOT_SHORT_CYCLE_MIN_INTERVAL_MIN=90` (1 to 1440) raises the `SHORT_CYCLE` warning when the stove starts again strictly sooner than that after the previous start. The instant is the first temperature sample in ignition or on after an off, standby, or cooling sample; a gap of exactly the minimum interval is compliant and clears the dashboard. The History alerts card keeps the row and its occurrence count. A first start, or a gap longer than 15 minutes, does not invent a start. The alert is advice only: it never starts, stops, or changes the stove or the chrono.
- `STOVE_PILOT_UI_HISTORY_EVENTS_PAGE_SIZE` (5..100, default 25) sets how many events the History page shows per page. A compact control (newer-page arrow, `N / M`, older-page arrow) pages through the range tab (24 h, 7 d, 30 d). The left arrow moves to the previous page (newer events) and the right arrow to the next page (older events); page 1 is the newest. The position is kept in the URL, so a page can be bookmarked. Only the first page refreshes by itself. The control is hidden when the range fits on one page and the URL has no cursor.
- `GET /api/v1/history/events` returns a bare JSON array unless `include_counts` is `1`, `true` or `yes` (any case); with that parameter the body is an object with the page counts and cursors.

## Backup

The SQLite file (history and suspension state) runs in WAL mode: never copy it with `cp` while the API runs, recent writes sit in the `-wal` file and the copy can be torn. `deploy/backup/stove-pilot-backup.sh` uses python's `sqlite3` `Connection.backup` (online, consistent), checks `PRAGMA integrity_check` on the copy, writes `stove-<date>.sqlite` and keeps the last 14 (`STOVE_PILOT_BACKUP_KEEP`).

```bash
deploy/backup/stove-pilot-backup.sh docker /srv/stove-pilot/backups   # runs inside the api container
STOVE_PILOT_DB_PATH=/var/lib/stove-pilot/stove-pilot.sqlite \
  deploy/backup/stove-pilot-backup.sh local /var/backups/stove-pilot  # venv or systemd install
```

The slim image has no `sqlite3` CLI, hence the python module. Equivalent one-liner for a non-Docker install:

```bash
python -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close()" /var/lib/stove-pilot/stove-pilot.sqlite /tmp/copy.sqlite
```

Daily cron example (03:15, Docker mode; the script resolves `compose.yaml` relative to itself):

```cron
15 3 * * * /srv/stove-pilot/deploy/backup/stove-pilot-backup.sh docker /srv/stove-pilot/backups >> /var/log/stove-pilot-backup.log 2>&1
```

systemd timer alternative (`/etc/systemd/system/stove-pilot-backup.service` and `.timer`):

```ini
[Service]
Type=oneshot
User=stove-pilot
Environment=STOVE_PILOT_DB_PATH=/var/lib/stove-pilot/stove-pilot.sqlite
ExecStart=/opt/stove-pilot/deploy/backup/stove-pilot-backup.sh local /var/backups/stove-pilot
```

```ini
[Timer]
OnCalendar=*-*-* 03:15:00
Persistent=true

[Install]
WantedBy=timers.target
```

### Copy the backups to another host

The backup directory sits on the same disk as the database: a dead SD card or disk loses both. Copy it to ANOTHER machine (for example a NAS) right after the backup, in the same cron line or systemd unit, so the copy only runs when the backup succeeded. Never put NAS credentials in the repository or `.env`: use an SSH key restricted to the backup account, or an already mounted SMB/NFS share.

```bash
# one-time: ssh-keygen -t ed25519 -f ~/.ssh/stove_backup -N "" ; ssh-copy-id -i ~/.ssh/stove_backup.pub backup@nas
# cron (03:15): backup, then push; --delete-after mirrors the 14-file rotation on the NAS
15 3 * * * /srv/stove-pilot/deploy/backup/stove-pilot-backup.sh docker /srv/stove-pilot/backups && rsync -a --delete-after -e "ssh -i ~/.ssh/stove_backup" /srv/stove-pilot/backups/ backup@nas:/volume1/backups/stove-pilot/ >> /var/log/stove-pilot-backup.log 2>&1
# alternative without rsync, or to a mounted share (/mnt/nas):
# scp -i ~/.ssh/stove_backup /srv/stove-pilot/backups/stove-*.sqlite backup@nas:/volume1/backups/stove-pilot/
# cp -p /srv/stove-pilot/backups/stove-*.sqlite /mnt/nas/stove-pilot/
```

With the systemd timer, add a second `ExecStart=` line running the same `rsync` after the backup script (a failing first line stops the unit).

On the on-demand Windows PC, run the backup, then mirror the folder to the NAS share (`/MIR` deletes files missing from the source; point it at a dedicated folder only):

```powershell
robocopy D:\stove-pilot\backups \\nas\backups\stove-pilot /MIR /R:2 /W:5 /NP
```

Verify the remote copy now and then (a copy can be torn or truncated), on the NAS or any host with Python:

```bash
python3 -c "import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute('PRAGMA integrity_check').fetchone()[0])" /volume1/backups/stove-pilot/stove-<date>.sqlite   # expect: ok
```

Restore: stop the API, replace the database file with a backup (delete any stale `-wal` and `-shm` next to it), start the API.

## Security and hardening

- There is no UI login: every page is open to whoever reaches port 8711. Keep 8711 on the LAN or VPN, never port-forward it, never expose it to the Internet.
- The API port 8710 is not published by Docker and binds to `127.0.0.1` outside Docker. Do not publish it.
- Optional shared key between UI and API: `STOVE_PILOT_API_KEY` (checked on every API route except `/health`). Optional `STOVE_PILOT_UI_SECRET_KEY` keeps CSRF tokens valid across restarts. Use long random values.
- Secrets live only in `.env` (or the systemd env file, mode 0640/0600); logs redact them. Never put them in compose files, units, or the image. In the compose stack both services read the same `.env`.
- Router: give the module a DHCP reservation (default `192.168.68.54`), optionally put it on an IoT VLAN and block its Internet egress (a firmware update could remove the local API). Do not forward any port.
- Remote access goes through your existing VPN, optionally with an internal HTTPS name (see the next section).
- `/widget` may be framed only by origins listed in `STOVE_PILOT_UI_FRAME_ANCESTORS`; it is read only.

## Safety notes

- An ignition is never retried automatically. The safety restart below is a new start from `OFF`, not a retry of an ignition in progress.
- Alarms are never reset remotely, and a safety restart cannot reset one either; clear them at the stove.
- A stop during `IGNITION` needs an explicit confirmation (it forces an on-site brazier emptying); automated sources defer such a stop. Restarts are throttled (`STOVE_PILOT_MIN_RESTART_INTERVAL_MIN`).
- EEPROM writes are read-before-write, diff-based, verified after writing, and never periodic. With the `navel` gateway only registers marked `verified = true` in the profile can be written.
- The module is fragile: keep polling gentle (`STOVE_PILOT_POLL_INTERVAL_S`, `STOVE_PILOT_NAVEL_MIN_INTERVAL_S`), run a single API instance (an instance lock enforces it) and do not point other tools at the module at the same time.
- Disable the safety in Reglages before any maintenance or cleaning.
- The safety check runs only after a successful status refresh that is not flagged stale. A failed refresh, or a degraded display read, does nothing on that cycle. The read taken just before a start counts the same way: when it flags the display, that poll is discarded and no start is written.
- A panel stop that falls on a native chrono stop minute does not pause the safety, when the previous poll is recent. A doubtful stop attribution does, and then there is no automatic restart (Known limits, below).

## Safety restart

When the feature is enabled, every successful status poll that is not flagged stale checks the ambient temperature. Readings outside 0 to 40 C are ignored. After `STOVE_PILOT_SAFETY_CONFIRM_POLLS` consecutive fresh readings at or below the threshold (default 2), and only while the stove is `OFF`, the API starts it with the normal start command (source `SYSTEM`). The preview uses the poll that was just read, and reads the stove again only when that preview says the start would be applied. The start call is also made when the preview says reject or no-op, only to obtain the guard's outcome, and never when it says defer. A deferred start is not created and is not counted; the next poll tries again. It never starts from `ALARM` or `UNKNOWN`, and it never sends a stop. A warmer plausible reading resets the confirmation count. The count does not advance while the stove is lit.

A start that stays `OFF`, with no start waiting, is retried only from `OFF`, never sooner than the retry interval, and that interval cannot be saved below `STOVE_PILOT_MIN_RESTART_INTERVAL_MIN`. A stored interval below that floor is read as the floor, with one warning per process. There are at most `max_attempts` tries in one episode. Success is `RUNNING` or `CLEANING`, and also `STANDBY` when the previous poll was `IGNITION` (the stove's own energy saving). `IGNITION` stays in progress. If the stove leaves a lit state and returns to `OFF` or `SHUTDOWN` while a restart is in progress, the episode fails with `ignition_failed` and is not retried. A warmer room does not rearm that failure. `no_op` (the stove was already on) returns to idle and clears the attempt counter, so the next episode keeps its full budget. `UNKNOWN` during a restart fails the episode the same way an alarm does. Other failures keep their reason (alarm, attempts exhausted, or a rejected start). Otherwise the episode waits for the next attempt.

A user stop from the app whose result is `applied`, `unknown` or `no_op` pauses it. A still-deferred stop does not, until that later result. A panel stop pauses it unless a non-user stop command in the last 5 minutes, and newer than the previous poll, or a matching chrono stop, explains the transition. A user stop in that window pauses it as an app stop. The manual latch clears only when a stopped poll (`OFF` or `SHUTDOWN`) is followed by `IGNITION` or `RUNNING`. A lit poll with no such previous poll keeps the latch. Vacation mode suspends it. A plain programming suspension does not. Rearm with the Rearmer button in Reglages, with `POST /api/v1/safety/rearm`, or by turning the feature off and on again. A waiting episode, or a failed episode other than `ignition_failed`, also rearms when the room rises by the rearm hysteresis above the threshold, or when the stove is seen igniting or running. `ignition_failed` rearms only when a stopped poll (`OFF` or `SHUTDOWN`) is followed by `IGNITION` or `RUNNING`, on rearm, or when the feature goes from off to on. Saving the other fields does not rearm and does not clear a manual stop. During a wait, saving recomputes the next attempt from the last one and does not reset the attempts already made.

The card is on Reglages and shows the last check time. While the feature is enabled, the dashboard glance header shows a badge with the threshold (at or below that temperature). `/widget` does not show that badge. A failure is still the shared alert line on the widget and on the dashboard when the derived status is `failed` and the stove itself is not in alarm, and the same failure is stored in the alert history (`SAFETY_RESTART_FAILED`). The history page lists `safety_restart`, `safety_restart_succeeded`, `safety_restart_failed`, `safety_paused` and `safety_rearmed`.

The four `*_DEFAULT*` variables only seed the card. After the first save in Reglages, the persisted values win: `enabled`, and the three numbers on the card (`threshold_c`, `max_attempts`, `retry_interval_min`). Confirm polls, the rearm hysteresis and the chrono stop window stay process settings.

- `STOVE_PILOT_SAFETY_ENABLED_DEFAULT=false` seeds the toggle until that first save.
- `STOVE_PILOT_SAFETY_THRESHOLD_DEFAULT_C=10` (integer, 5 to 20) seeds the threshold in degrees Celsius.
- `STOVE_PILOT_SAFETY_MAX_ATTEMPTS_DEFAULT=3` (1 to 5) seeds the attempts per episode.
- `STOVE_PILOT_SAFETY_RETRY_INTERVAL_DEFAULT_MIN=15` (from `STOVE_PILOT_MIN_RESTART_INTERVAL_MIN` to 240) seeds the minutes between attempts. A default below the minimum restart interval is raised to it, with one startup warning.
- `STOVE_PILOT_SAFETY_CONFIRM_POLLS=2` (1 to 10) is how many consecutive fresh polls at or below the threshold are required before a start.
- `STOVE_PILOT_SAFETY_REARM_HYSTERESIS_C=1.0` (0.5 to 5) is how many degrees above the threshold rearm a waiting or failed episode.
- `STOVE_PILOT_SAFETY_CHRONO_STOP_WINDOW_MIN=5` (1 to 15) is how many minutes after a chrono stop minute still count as that stop.

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/v1/safety` | Card, bounds, derived status, attempts, next attempt, failure and pause reasons, last check, warning. |
| PUT | `/api/v1/safety` | `{enabled, threshold_c, max_attempts, retry_interval_min}`, all required. Whole numbers only (`10.0` accepted, `10.5` rejected). `422` outside the range. Writable during vacation. |
| POST | `/api/v1/safety/rearm` | Returns to idle, clears the counters and the manual stop. |
| GET | `/api/v1/summary` | Additive `safety` object: `enabled`, `status`, `threshold_c`. `null` when the stored safety row cannot be read (the rest of the summary stays). `GET`/`PUT /safety` and rearm then answer `503 safety_unavailable`. |

### Known limits

- A panel stop that coincides with an enabled chrono stop minute is attributed to the chrono, so it does not pause the safety, when the two polls are at most `min(max(3 * poll interval, 90), 300)` seconds apart. The first poll that shows the stop must fall from 1 minute before that minute through `STOVE_PILOT_SAFETY_CHRONO_STOP_WINDOW_MIN` after it, and the previous poll must not already be more than 1 minute past that stop.
- A doubtful stop attribution is treated as manual, so there is no automatic restart. Doubt is a disabled chrono, an unreadable chrono configuration, a missing stove clock, a missing previous poll, a chrono read that fails, or a gap between the two polls longer than `min(max(3 * poll interval, 90), 300)` seconds. A stop command older than the previous poll does not explain the later transition. A poll-gap doubt while the episode is `restarting` latches as `paused_manual` and resets the attempts. It does not become `ignition_failed`.
- A stop that happens entirely inside a gap between polls, or while the process is down, with the stove `OFF` at both ends, is not observable. The safety then starts the stove.
- The start call is also made when the preview says reject or no-op, only to obtain the guard's outcome. It is never made when the preview says defer.
- The safety starts only from `OFF`, and only when the start would be applied immediately. `STANDBY`, `SHUTDOWN` and every other state are left alone. An attempt that is still `OFF`, with no start waiting, five minutes later counts as a failed attempt: the episode waits while attempts remain, and becomes `failed` (`attempts_exhausted`) at the maximum. `ALARM` or `UNKNOWN` during a restart fails the episode at once. A display alarm code is treated as an alarm. `IGNITION` is not success.
- A process crash between the start write and the saved attempt is not covered. One extra attempt is possible if the stove is still `OFF` when the process is back. If the stove has already left `OFF`, that attempt is not sent again and is not recorded as a success.
- The safety needs the API process running. It does nothing while the server is down, and a failed or stale status refresh skips the check for that cycle. A disable, a manual latch or vacation that arrives while the start write is already in flight cannot cancel that write.

## HTTPS prerequisite (PWA)

The UI ships a service worker (offline page and cached shell assets) so it can be installed on a phone. Browsers register service workers only on HTTPS or `localhost`.

- The UI is internal: expose it on the LAN or VPN only, never on the public internet.
- To install it on a phone, publish it behind a TLS-terminating reverse proxy that the phone trusts. The proxy must serve the UI at the root path (`/`), not under a sub-path.
- The UI needs no change behind the proxy: every URL it emits is relative and the service worker scope is `/`.
- Plain `http://<lan-ip>:8711` keeps working without the service worker (registration is skipped).
- The session cookie `Secure` flag stays off so that plain-HTTP LAN access keeps working.
