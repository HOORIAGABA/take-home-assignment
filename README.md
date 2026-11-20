# Automation & Integrations take-home

| Part | What | Entry point |
|---|---|---|
| 1 | Every business of a category in a city from Google Maps + website contact enrichment | `python -m leadgen` |
| 2B | WhatsApp assistant on a linked number (text + voice notes, `leads` command) | `python -m wabot` |
| 3 | Write-up | [WRITEUP.md](WRITEUP.md) |
| Bonus | Part 1 ↔ Part 2: `leads dental clinic Lahore` returns the Part 1 CSV over WhatsApp | |

Python 3.11+. No official Google/WhatsApp APIs, no paid scraping services, no CAPTCHA solving.

## Setup (5 commands)

Windows (PowerShell or cmd):

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
copy .env.example .env
```

macOS/Linux: the same, with `source .venv/bin/activate` and `cp .env.example .env`.

Then edit `.env`: `ALLOWLIST=<your own test number>`.
(Alternatively list numbers in `allowlist.txt`; see `allowlist.example.txt`.)

## Part 1 - run the benchmark

```bash
python -m leadgen --category "dental clinic" --city "Lahore, Pakistan" --extra-queries "dentist"
```

Outputs go to `output/<category>-<city>/`: `businesses.csv`, `businesses.json`, `report.json`, `report.md`
(counts, coverage, failures, time taken). The benchmark copy lives in [`benchmark/`](benchmark/).

* **Crash-safe**: kill it at any point (Ctrl+C, close the window, `kill -9`) and run the *same command* again;
  it resumes from the last finished request. `--fresh` starts over.
* **Throttling**: `--maps-rate` (req/s, default 0.5), `--max-requests` (hard cap across resumed runs,
  default 600), `--web-rate`, `--concurrency`. Exponential backoff with jitter on errors; stops cleanly on CAPTCHA.
* **Coverage check**: `--extra-queries dentist` re-searches a seeded random sample of 12 finished tiles
  (`--coverage-sample`, 0 = whole city) after the main crawl, and the report gives a capture-recapture estimate.
* Other flags: `python -m leadgen --help` (`--smtp-probe` for mailbox checks, `--headful` to watch the browser,
  `--skip-maps` to only re-enrich/re-export, `--bbox` to skip the city lookup).

Exit codes: `0` complete, `2` incomplete (budget/blocked - re-run later to continue), `3` Google changed its
response format (loud failure instead of "0 results").

## Part 2B - WhatsApp assistant

```bash
python -m wabot           # first run prints a QR: WhatsApp > Settings > Linked devices > Link a device
```

* The session is stored in `data/wa/session.sqlite3` and survives restarts and reboots; no re-scan needed.
* `python -m wabot` is a small supervisor that restarts the bot with exponential backoff; it does **not**
  restart after a logout/ban (that needs a human) - it raises an alert instead (CRITICAL log line +
  `data/wa/ALERTS.log`).
* `python -m wabot status` shows the message ledger; `python -m wabot relink` pairs again.
* Crash testing: `WABOT_CRASH_AT=before_reply|before_send|after_send` hard-kills the bot once at that point;
  `python -m wabot rearm` re-arms it.
* Message it from the allowlisted test phone: any text, a voice note, or `leads dental clinic Lahore`.
* Voice notes are transcribed **locally** with faster-whisper (no API key). The first voice note downloads
  the model (`STT_MODEL=small`, ~460 MB) into `data/models/`; later ones take a few seconds on CPU.
* Start on boot: Windows `schtasks /create /tn wabot /sc onlogon /tr "<repo>\deploy\wabot.cmd"`,
  Linux `deploy/wabot.service` (systemd).

## Tests

```bash
pytest
```

Offline (no network): Maps payload parser against a captured real response, email de-obfuscation, socials,
dedupe, tile split, crash/resume of the crawl, website enrichment against a local HTTP server, robots.txt,
and the WhatsApp exactly-once ledger including the "killed between send and commit" case.

## Layout

```
leadgen/            Part 1
  maps/client.py    Playwright session; captures the page's own search request and replays it per tile
  maps/parse.py     positional-JSON parser (+ SchemaChanged / Blocked)
  maps/geo.py       city polygon (OpenStreetMap) and adaptive tiles
  maps/crawl.py     tile paging, saturation split, checkpointing
  enrich/           website fetcher, email de-obfuscation, socials, MX/SMTP verification
  store.py          SQLite checkpoint store
  dedupe.py         CID + phone/website/name-within-distance merge
  coverage.py       saturation curve + capture-recapture estimate
wabot/              Part 2B
  bot.py            neonize (whatsmeow) client, sync persistence hook, reply worker, reconnect/alerts
  ledger.py         exactly-once state machine (SQLite)
  commands.py       `leads <category> <city>`
  inbound.py        WhatsApp event -> plain record (sender phone/LID, kind, text)
  allowlist.py      allowlist, re-read on every check
  transcribe.py     local voice-note transcription (faster-whisper)
  alerts.py         logout/ban alerts (CRITICAL log line + data/wa/ALERTS.log)
deploy/             start-on-boot (Windows Task Scheduler, systemd)
benchmark/          Part 1 output for dental clinic / Lahore
tests/              pytest suite (offline)
```
