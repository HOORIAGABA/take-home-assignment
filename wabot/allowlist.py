"""Allowlist of phone numbers the bot may talk to.

Re-read from disk on *every* check (it is a tiny file), so removing a number takes
effect immediately - including for a reply that was already generated and is
waiting to be sent. Sources: ALLOWLIST env var (comma separated) + ALLOWLIST_FILE.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


def norm(number: str) -> str:
    return re.sub(r"\D", "", number).lstrip("0")


class Allowlist:
    def __init__(self, path: Path | None, env_var: str = "ALLOWLIST"):
        self.path = path
        self.env_var = env_var

    def numbers(self) -> set[str]:
        nums: set[str] = set()
        env = os.environ.get(self.env_var, "")
        nums.update(norm(x) for x in env.split(",") if norm(x))
        if self.path and self.path.exists():
            for line in self.path.read_text("utf-8").splitlines():
                line = line.split("#", 1)[0].strip()
                if line and norm(line):
                    nums.add(norm(line))
        return nums

    def allowed(self, phone: str | None) -> bool:
        if not phone:
            return False   # unknown sender (LID we couldn't resolve) -> never reply
        return norm(phone) in self.numbers()
