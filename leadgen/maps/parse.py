"""Parse the payload of Google Maps' internal `/search?tbm=map` request.

The payload is positional JSON (arrays, not objects) behind an XSSI guard `)]}'`.
Index positions were mapped by hand against live responses (Nov 2026); see
WRITEUP.md. Because positions can move without notice, the parser:

* looks for the result list by *shape* rather than trusting one index, and
* raises `SchemaChanged` when the payload no longer looks like what we mapped,
  so a silent "0 results" can never be mistaken for "no businesses here".
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

XSSI = ")]}'"


class SchemaChanged(Exception):
    """The response no longer matches the structure this parser was written for."""


class Blocked(Exception):
    """Google answered with a CAPTCHA / 'unusual traffic' page instead of data."""


@dataclass
class Place:
    cid: str                      # decimal customer id - stable, used as the primary key
    place_id: str | None          # ChIJ... id
    ftid: str                     # 0x...:0x... feature id (cid is its 2nd half)
    name: str
    category: str | None
    categories: list[str] = field(default_factory=list)
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    phone: str | None = None      # international format as shown by Maps
    phone_local: str | None = None
    website: str | None = None
    rating: float | None = None
    review_count: int | None = None
    maps_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def g(obj: Any, *path: int | str, default: Any = None) -> Any:
    """Safe nested get for positional JSON."""
    cur = obj
    for p in path:
        try:
            cur = cur[p]
        except (IndexError, KeyError, TypeError):
            return default
        if cur is None:
            return default
    return cur


def ftid_to_cid(ftid: str) -> str | None:
    try:
        return str(int(ftid.split(":")[1], 16))
    except (IndexError, ValueError, AttributeError):
        return None


def _clean_url(url: str | None) -> str | None:
    if not url:
        return None
    if url.startswith("/url?"):
        q = parse_qs(urlsplit(url).query).get("q")
        url = q[0] if q else None
    return url


def _looks_like_place(info: Any) -> bool:
    return isinstance(info, list) and len(info) > 80 and isinstance(g(info, 11), str) and isinstance(g(info, 10), str)


def _find_result_list(data: list) -> list | None:
    # Known location first, then any top-level list whose items carry place arrays.
    candidates = [g(data, 64)] + [x for x in data if isinstance(x, list)]
    for cand in candidates:
        if not isinstance(cand, list):
            continue
        if any(isinstance(it, list) and _looks_like_place(g(it, 1)) for it in cand):
            return cand
    return None


def load_payload(text: str) -> list:
    text = text.lstrip()
    if not text.startswith(XSSI):
        low = text[:5000].lower()
        if "unusual traffic" in low or "/sorry/" in low or "captcha" in low:
            raise Blocked("Google returned a CAPTCHA / unusual-traffic page")
        raise SchemaChanged(f"response is not an XSSI-guarded JSON payload: {text[:80]!r}")
    body = text[len(XSSI):]
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise SchemaChanged(f"payload is not valid JSON: {exc}") from exc
    if not isinstance(data, list) or len(data) < 10:
        raise SchemaChanged("payload top level is not the expected array")
    return data


def parse_place(info: list) -> Place | None:
    ftid = g(info, 10)
    name = g(info, 11)
    cid = ftid_to_cid(ftid) if ftid else None
    if not (cid and name):
        return None
    categories = [c for c in (g(info, 13) or []) if isinstance(c, str)]
    review_count = g(info, 37, 1)
    place_id = g(info, 78)
    return Place(
        cid=cid,
        place_id=place_id if isinstance(place_id, str) else None,
        ftid=ftid,
        name=name,
        category=categories[0] if categories else None,
        categories=categories,
        address=g(info, 39) or g(info, 18),
        lat=g(info, 9, 2),
        lng=g(info, 9, 3),
        phone=g(info, 178, 0, 0),
        phone_local=g(info, 178, 0, 3),
        website=_clean_url(g(info, 7, 0)),
        rating=g(info, 4, 7),
        review_count=review_count if isinstance(review_count, int) else None,
        maps_url=(f"https://www.google.com/maps/place/?q=place_id:{place_id}" if place_id
                  else f"https://www.google.com/maps?cid={cid}"),
    )


def parse_search_payload(text: str) -> list[Place]:
    """Return the places in one page of results. Empty list == genuine end of results."""
    data = load_payload(text)
    results = _find_result_list(data)
    if results is None:
        # A real "no results" page still carries the query echo at [0][0] and has no
        # place arrays anywhere. Anything else is a structure we don't understand.
        if isinstance(g(data, 0), list) and isinstance(g(data, 0, 0), str):
            return []
        raise SchemaChanged("could not find the result list in the payload")

    places: list[Place] = []
    malformed = 0
    for item in results:
        info = g(item, 1)
        if not isinstance(info, list):
            continue  # ads / headers / separators
        p = parse_place(info) if _looks_like_place(info) else None
        if p is None:
            malformed += 1
            continue
        places.append(p)
    if malformed and malformed >= max(3, len(places)):
        raise SchemaChanged(f"{malformed} result entries could not be parsed (vs {len(places)} ok)")
    return places
