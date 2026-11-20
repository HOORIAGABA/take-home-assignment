"""
python -m wabot            supervisor: runs the bot, restarts it with exponential backoff
python -m wabot run        the bot process itself (what the supervisor runs)
python -m wabot relink     move the old session aside and pair again by QR
python -m wabot status     ledger counts, last messages, recent events
python -m wabot rearm      re-arm the WABOT_CRASH_AT test crash points
"""

from __future__ import annotations

import logging
import random
import subprocess
import sys
import time

from .config import Config

log = logging.getLogger("wabot.supervisor")


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")


def supervise() -> int:
    failures = 0
    while True:
        started = time.time()
        log.info("starting bot process")
        try:
            code = subprocess.call([sys.executable, "-m", "wabot", "run"])
        except KeyboardInterrupt:
            return 130
        if code in (0, 130, -2, 3221225786):  # clean exit / Ctrl+C (incl. Windows STATUS_CONTROL_C_EXIT)
            return 0
        if code == 2:
            log.critical("bot stopped and needs a human (logged out / banned / replaced) - not restarting")
            return 2
        if time.time() - started > 600:
            failures = 0                      # it was healthy for a while: reset backoff
        delay = min(300.0, 2 ** failures) * random.uniform(0.7, 1.3)
        failures += 1
        log.warning("bot exited with code %s; restarting in %.0fs (attempt %d)", code, delay, failures)
        try:
            time.sleep(delay)
        except KeyboardInterrupt:
            return 130


def main() -> int:
    setup_logging()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "supervise"
    if cmd == "run":
        from .bot import run_bot
        return run_bot()
    if cmd == "relink":
        cfg = Config.load()
        if cfg.session_db.exists():
            backup = cfg.session_db.with_name(cfg.session_db.name + f".old-{int(time.time())}")
            cfg.session_db.replace(backup)
            log.info("old session moved to %s", backup)
        return supervise()
    if cmd == "status":
        from .ledger import Ledger
        cfg = Config.load()
        led = Ledger(cfg.ledger_db)
        print("messages by status:", led.counts())
        print()
        print("last 10 messages:")
        for r in led.db.execute("SELECT datetime(ts,'unixepoch','localtime'), msg_id, kind, status, reason, "
                                "out_id, send_attempts, substr(text,1,40) FROM inbound ORDER BY ts DESC LIMIT 10"):
            print(" ", " | ".join("" if x is None else str(x) for x in r))
        print()
        print("recent events:")
        for r in led.db.execute("SELECT datetime(ts,'unixepoch','localtime'), kind, detail FROM events "
                                "ORDER BY id DESC LIMIT 15"):
            print(" ", *r)
        return 0
    if cmd == "rearm":
        from .ledger import Ledger
        led = Ledger(Config.load().ledger_db)
        led.db.execute("DELETE FROM meta WHERE key LIKE 'crashed_%'")
        print("crash test points re-armed")
        return 0
    if cmd == "supervise":
        return supervise()
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
