"""De-duplication.

Level 1 (exact): Google's CID - the numeric id inside the feature id `0x...:0x<cid>`.
It is the primary key in the store, so the same listing seen from 10 overlapping
tiles or 3 different queries is stored once.

Level 2 (fuzzy): *different* listings that are the same real business (duplicate
pins created by owners/agencies). Two listings are merged when they are close
together AND share a strong identifier:
  * same normalised phone number and <= 250 m apart, or
  * same website host (not a social network) and <= 150 m apart, or
  * same normalised name and <= 60 m apart.
Branches of a chain share a phone/website but are far apart, so they survive.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from .enrich.socials import is_social_url
from .util import haversine_m, normalize_phone

STOP = {"the", "dr", "dental", "clinic", "and", "&", "centre", "center", "pvt", "ltd"}


def _norm_name(name: str) -> str:
    toks = re.findall(r"[a-z0-9]+", name.lower())
    return " ".join(t for t in toks if t not in STOP) or name.lower()


def _site(url: str | None) -> str | None:
    if not url or is_social_url(url):
        return None
    h = (urlsplit(url if "//" in url else "http://" + url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h or None


class _UF:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        self.p[self.find(a)] = self.find(b)


def _close(a: dict, b: dict, metres: float) -> bool:
    if None in (a.get("lat"), a.get("lng"), b.get("lat"), b.get("lng")):
        return False
    return haversine_m(a["lat"], a["lng"], b["lat"], b["lng"]) <= metres


def find_duplicate_groups(places: list[dict]) -> list[list[int]]:
    uf = _UF(len(places))
    keys: dict[tuple[str, str], list[int]] = {}
    for i, p in enumerate(places):
        if (ph := normalize_phone(p.get("phone"))):
            keys.setdefault(("phone", ph), []).append(i)
        if (s := _site(p.get("website"))):
            keys.setdefault(("site", s), []).append(i)
        keys.setdefault(("name", _norm_name(p.get("name", ""))), []).append(i)
    limits = {"phone": 250, "site": 150, "name": 60}
    for (kind, _), idx in keys.items():
        if len(idx) < 2 or len(idx) > 50:  # >50 = generic value (call-centre number); ignore
            continue
        for x in range(len(idx)):
            for y in range(x + 1, len(idx)):
                if _close(places[idx[x]], places[idx[y]], limits[kind]):
                    uf.union(idx[x], idx[y])
    groups: dict[int, list[int]] = {}
    for i in range(len(places)):
        groups.setdefault(uf.find(i), []).append(i)
    return [g for g in groups.values() if len(g) > 1]


def merge_duplicates(places: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (kept places, merge log). The listing with most reviews wins; fields are back-filled."""
    groups = find_duplicate_groups(places)
    drop: set[int] = set()
    log = []
    for g in groups:
        g = sorted(g, key=lambda i: (-(places[i].get("review_count") or 0), places[i]["cid"]))
        keep = dict(places[g[0]])
        for i in g[1:]:
            for k, v in places[i].items():
                if keep.get(k) in (None, "", []) and v not in (None, "", []):
                    keep[k] = v
            drop.add(i)
        keep["merged_cids"] = [places[i]["cid"] for i in g[1:]]
        places[g[0]] = keep
        log.append({"kept": keep["cid"], "name": keep.get("name"), "merged": keep["merged_cids"]})
    return [p for i, p in enumerate(places) if i not in drop], log
