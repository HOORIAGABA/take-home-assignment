"""Alerts (logout, ban, give-ups). When the number is logged out we can't alert over WhatsApp,
so an alert is a CRITICAL log line plus a line appended to data/wa/ALERTS.log."""

from __future__ import annotations

import logging
import time
from pathlib import Path

log = logging.getLogger("wabot.alert")


def send_alert(text: str, alert_file: Path) -> None:
    log.critical("ALERT: %s", text)
    alert_file.parent.mkdir(parents=True, exist_ok=True)
    with alert_file.open("a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
