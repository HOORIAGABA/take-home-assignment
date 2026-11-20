"""Small shared helpers: logging, geometry, text normalisation, rate limiting, retries."""

from __future__ import annotations

import asyncio
import logging
import math
import random
import re
import time
import unicodedata
from collections.abc import Awaitable, Callable, Iterable
from typing import TypeVar

T = TypeVar("T")
log = logging.getLogger("leadgen")


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # httpx logs every request at INFO; that is noise for a crawler.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "run"


# ---------------------------------------------------------------- geometry

EARTH_R_M = 6_371_000.0


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_M * math.asin(math.sqrt(a))


def point_in_ring(lat: float, lng: float, ring: list[list[float]]) -> bool:
    """Ray casting. `ring` is GeoJSON order: [[lng, lat], ...]."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            x_cross = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lng < x_cross:
                inside = not inside
        j = i
    return inside


def point_in_polygons(lat: float, lng: float, polygons: list[list[list[list[float]]]]) -> bool:
    """`polygons` is a list of GeoJSON polygons (outer ring first, then holes)."""
    for poly in polygons:
        if not poly:
            continue
        if point_in_ring(lat, lng, poly[0]) and not any(point_in_ring(lat, lng, h) for h in poly[1:]):
            return True
    return False


# ---------------------------------------------------------------- phones

def normalize_phone(raw: str | None, default_cc: str = "92") -> str | None:
    """Return E.164 (`+923001234567`) or None. Handles local `0300...`, `0092...`, `+92 ...`."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    if raw.strip().startswith("+"):
        pass
    elif digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = default_cc + digits[1:]
    elif len(digits) <= 10:
        digits = default_cc + digits
    if not 8 <= len(digits) <= 15:
        return None
    exact = NATIONAL_LENGTHS.get(next((cc for cc in NATIONAL_LENGTHS if digits.startswith(cc)), ""), None)
    if exact and len(digits) != exact:
        return None  # e.g. two Pakistani numbers glued together by page text
    return "+" + digits


# Full E.164 digit count (country code included) for countries where it is fixed.
NATIONAL_LENGTHS = {"92": 12}


def uniq(items: Iterable[T]) -> list[T]:
    seen: set = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ---------------------------------------------------------------- rate limiting / retry

class RateLimiter:
    """Spaces calls at `rate` per second (with jitter). Shared across tasks."""

    def __init__(self, rate_per_sec: float, jitter: float = 0.3):
        self.interval = 1.0 / rate_per_sec if rate_per_sec > 0 else 0.0
        self.jitter = jitter
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        if self.interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next - now)
            spacing = self.interval * random.uniform(1 - self.jitter, 1 + self.jitter)
            self._next = max(now, self._next) + spacing
        if delay:
            await asyncio.sleep(delay)

    def penalize(self, seconds: float) -> None:
        """Push the next slot out (used after 429s so *all* workers slow down)."""
        self._next = max(self._next, time.monotonic() + seconds)


class RetryableError(Exception):
    """Raise from inside a retried call to request another attempt."""

    def __init__(self, msg: str, retry_after: float | None = None):
        super().__init__(msg)
        self.retry_after = retry_after


def backoff_delay(attempt: int, base: float = 1.0, cap: float = 60.0) -> float:
    """Exponential backoff with full jitter. attempt starts at 0."""
    return random.uniform(0, min(cap, base * (2 ** attempt)))


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 4,
    base: float = 1.0,
    cap: float = 60.0,
    retry_on: tuple[type[BaseException], ...] = (RetryableError,),
    what: str = "call",
) -> T:
    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            return await fn()
        except retry_on as exc:  # type: ignore[misc]
            last = exc
            if attempt == attempts - 1:
                break
            delay = backoff_delay(attempt, base, cap)
            ra = getattr(exc, "retry_after", None)
            if ra:
                delay = max(delay, float(ra))
            log.debug("%s failed (%s); retry %d/%d in %.1fs", what, exc, attempt + 1, attempts - 1, delay)
            await asyncio.sleep(delay)
    assert last is not None
    raise last
