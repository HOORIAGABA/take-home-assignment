"""End-to-end run: Maps crawl -> filter -> dedupe -> website enrichment -> verify -> export."""

from __future__ import annotations

import asyncio
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

from .coverage import coverage_report
from .dedupe import merge_duplicates
from .enrich.verify import MailVerifier
from .enrich.website import WebsiteEnricher
from .export import write_outputs
from .maps.client import MapsClient
from .maps.crawl import crawl_query
from .maps.geo import City, Tile, initial_tiles, resolve_city
from .store import Store
from .util import log, normalize_phone, slugify, uniq

GENERIC = {"clinic", "clinics", "shop", "shops", "store", "stores", "center", "centre", "service", "services",
           "company", "agency", "office", "near", "me", "best", "the", "and", "in"}


@dataclass
class Settings:
    category: str
    city: str
    extra_queries: list[str] = field(default_factory=list)
    out_dir: Path = Path("output")
    data_dir: Path = Path("data")
    bbox: tuple[float, float, float, float] | None = None
    tile_km: float = 5.0
    max_depth: int = 3
    max_pages: int = 6           # 6 x 20 = 120 results per tile before we split it
    stale_pages: int = 1         # stop a tile after this many pages with nothing inside it
    split_min_in_tile: int = 10  # split a full tile only if its last 2 pages had >= this many in-tile hits
    coverage_sample: int = 12    # extra queries are run on this many random finished tiles (0 = whole city)
    maps_rate: float = 0.5       # Maps requests per second
    max_requests: int = 600      # hard cap on Maps requests across all resumed runs
    web_rate: float = 4.0        # website requests per second (global)
    concurrency: int = 6         # simultaneous websites
    extra_pages: int = 2
    js_fallback: bool = True
    smtp_probe: bool = False
    headless: bool = True
    skip_maps: bool = False
    skip_enrich: bool = False
    default_cc: str = "92"
    category_regex: str | None = None

    @property
    def slug(self) -> str:
        return slugify(f"{self.category}-{self.city}")


def category_matcher(category: str, override: str | None = None) -> re.Pattern:
    if override:
        return re.compile(override, re.I)
    # "dental clinic" -> r"\bdent" : matches Dental clinic, Dentist, Denture care; not "accident".
    stems = [w[:4] if len(w) > 4 else w for w in re.findall(r"[a-z]+", category.lower()) if w not in GENERIC]
    if not stems:
        return re.compile(re.escape(category), re.I)
    return re.compile("|".join(r"\b" + re.escape(s) for s in stems), re.I)


async def _maps_phase(s: Settings, store: Store, city: City) -> tuple[str, int]:
    queries = [s.category] + s.extra_queries
    used = store.get_meta("maps_requests", 0)
    remaining = s.max_requests - used
    if remaining <= 0:
        return "budget", 0
    if not store.has_tiles(s.category):
        tiles = initial_tiles(city, s.tile_km)
        store.add_tiles(s.category, tiles)
        log.info("Query %r: %d initial tiles of %.1f km", s.category, len(tiles), s.tile_km)
    if not store.pending_tiles(s.category):
        _prepare_coverage_queries(s, store, city)
        if not any(store.pending_tiles(q) for q in s.extra_queries):
            return "complete", 0
    status, statuses = "complete", []
    async with MapsClient(rate=s.maps_rate, max_requests=remaining, headless=s.headless) as client:
        try:
            await client.bootstrap(s.category, *city.center)
            for q in queries:
                if q != s.category:
                    if status != "complete":
                        break  # coverage queries need the main crawl to be finished first
                    _prepare_coverage_queries(s, store, city)
                sample = q != s.category and s.coverage_sample > 0
                status = await crawl_query(client, store, q, max_pages=s.max_pages,
                                           max_depth=0 if sample else s.max_depth, stale_limit=s.stale_pages,
                                           split_min_in_tile=s.split_min_in_tile)
                statuses.append(status)
                if status in ("budget", "blocked"):
                    break
        finally:
            store.set_meta("maps_requests", used + client.requests)
    if status == "complete" and "incomplete" in statuses:
        status = "incomplete"
    return status, client.requests


def _prepare_coverage_queries(s: Settings, store: Store, city: City) -> bool:
    """Set up tiles for the extra (coverage-check) queries. Returns True if any work was added.

    With `coverage_sample` > 0 an extra query is run only on a seeded random sample of the main
    query's finished leaf tiles; capture-recapture is then computed inside those tiles. That costs
    ~N x 3 requests instead of re-crawling the whole city.
    """
    if store.pending_tiles(s.category):
        return False
    added = False
    for q in s.extra_queries:
        if store.query_started(q):
            continue
        store.reset_unstarted_tiles(q)
        if s.coverage_sample > 0:
            leaves = store.tiles_for(s.category, "done")
            pick = random.Random(42).sample(leaves, min(s.coverage_sample, len(leaves)))
            tiles = [Tile.from_id(t, d) for t, d in pick]
        else:
            tiles = initial_tiles(city, s.tile_km)
        store.add_tiles(q, tiles)
        log.info("Coverage query %r: %d tiles", q, len(tiles))
        added = True
    return added


async def _enrich_phase(s: Settings, store: Store, places: list[dict]) -> int:
    done = store.enrich_done()
    todo = [p for p in places if p["cid"] not in done and store.enrich_attempts(p["cid"]) < 3]
    if not todo:
        return 0
    log.info("Enriching %d businesses (%d already done)", len(todo), len(done))
    verifier = MailVerifier(smtp_probe=s.smtp_probe)
    pw = browser = None
    if s.js_fallback:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
    enricher = WebsiteEnricher(rate=s.web_rate, concurrency=s.concurrency, extra_pages=s.extra_pages,
                               js_fallback=s.js_fallback, browser=browser, default_cc=s.default_cc)
    counter = {"n": 0}

    async def one(p: dict) -> None:
        cid, site = p["cid"], p.get("website")
        try:
            if not site:
                store.save_enrichment(cid, "skipped", {"reason": "no website"})
                return
            data = await enricher.enrich(site)
            data["email_checks"] = [await verifier.verify(e) for e in data["emails"]]
            store.save_enrichment(cid, "done", data)
        except Exception as exc:  # noqa: BLE001 - recorded, retried on the next run (max 3 attempts)
            store.save_enrichment(cid, "failed", None, f"{type(exc).__name__}: {exc}"[:300])
            store.log_failure("website", f"{p.get('name')} <{site}>", f"{type(exc).__name__}: {exc}")
        finally:
            counter["n"] += 1
            if counter["n"] % 25 == 0:
                log.info("  enriched %d/%d", counter["n"], len(todo))

    try:
        await asyncio.gather(*(one(p) for p in todo))
    finally:
        await enricher.close()
        if browser:
            await browser.close()
        if pw:
            await pw.stop()
    return enricher.pages_fetched


def _build_rows(places: list[dict], enrich: dict[str, dict]) -> list[dict]:
    rows = []
    for p in places:
        e = enrich.get(p["cid"], {})
        checks = {c["email"]: c["verdict"] for c in e.get("email_checks", [])}
        rows.append({
            **p,
            "emails": e.get("emails", []),
            "email_verdicts": checks,
            "instagram": e.get("instagram", []),
            "facebook": e.get("facebook", []),
            "linkedin": e.get("linkedin", []),
            "whatsapp": uniq(n for w in e.get("whatsapp", []) if (n := normalize_phone(w))),
            "enrich_status": e.get("_status", "pending") if p.get("website") else "no website",
            "enrich_error": e.get("_error"),
        })
    return rows


async def run(s: Settings) -> dict:
    t0 = time.time()
    store = Store(s.data_dir / "runs" / f"{s.slug}.sqlite3")
    store.set_meta("invocations", store.get_meta("invocations", 0) + 1)
    if not store.get_meta("started_at"):
        store.set_meta("started_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    city = resolve_city(s.city, s.data_dir, s.bbox)
    log.info("City: %s  bbox S%.4f N%.4f W%.4f E%.4f", city.name, city.south, city.north, city.west, city.east)

    maps_status, maps_reqs = ("skipped", 0) if s.skip_maps else await _maps_phase(s, store, city)
    log.info("Maps phase: %s (%d requests this run)", maps_status, maps_reqs)

    raw = store.all_places()
    in_city = [p for p in raw if city.contains(p.get("lat"), p.get("lng"))]
    match = category_matcher(s.category, s.category_regex)
    relevant = [p for p in in_city if match.search(" ".join(p.get("categories") or []) + " " + p.get("name", ""))]
    final, merges = merge_duplicates([dict(p) for p in relevant])

    pages = 0 if s.skip_enrich else await _enrich_phase(s, store, final)
    enrich = store.enrichment()
    rows = _build_rows(final, enrich)

    verdicts: dict[str, int] = {}
    for r in rows:
        for v in r["email_verdicts"].values():
            verdicts[v] = verdicts.get(v, 0) + 1
    with_site = [r for r in rows if r.get("website")]
    report = {
        "category": s.category, "city": s.city, "city_resolved": city.name,
        "started_at": store.get_meta("started_at"),
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration_s": round(time.time() - t0, 1),
        "duration_total_s": round(store.get_meta("elapsed_before", 0) + time.time() - t0, 1),
        "invocations": store.get_meta("invocations"),
        "maps_status": maps_status,
        "maps_requests_total": store.get_meta("maps_requests", 0),
        "web_pages_fetched": pages,
        "settings": {k: (str(v) if isinstance(v, Path) else v) for k, v in s.__dict__.items()},
        "counts": {
            "unique_listings_seen (by CID)": len(raw),
            "inside_city_boundary": len(in_city),
            "matching_category": len(relevant),
            "fuzzy_duplicates_merged": len(relevant) - len(final),
            "final_businesses": len(rows),
            "with_phone": sum(1 for r in rows if r.get("phone")),
            "with_website": len(with_site),
            "website_is_social_profile": sum(1 for r in rows if enrich.get(r["cid"], {}).get("website_kind") == "social"),
            "enrich_done": sum(1 for r in with_site if r["enrich_status"] == "done"),
            "enrich_failed": sum(1 for r in with_site if r["enrich_status"] == "failed"),
            "with_email": sum(1 for r in rows if r["emails"]),
            "total_emails": sum(len(r["emails"]) for r in rows),
            "with_instagram": sum(1 for r in rows if r["instagram"]),
            "with_facebook": sum(1 for r in rows if r["facebook"]),
            "with_linkedin": sum(1 for r in rows if r["linkedin"]),
            "with_whatsapp": sum(1 for r in rows if r["whatsapp"]),
            **{f"email_verdict_{k}": v for k, v in sorted(verdicts.items())},
        },
        "tiles": store.tile_stats(),
        "coverage": coverage_report(
            s.category, {r["cid"] for r in rows} | {c for r in rows for c in r.get("merged_cids", [])},
            store.hits_by_query(), store.request_log(), {p["cid"]: (p.get("lat"), p.get("lng")) for p in raw},
            {q: [Tile.from_id(t, d) for t, d in store.tiles_for(q)] for q in s.extra_queries} if s.coverage_sample else {}),
        "duplicates_merged": merges,
        "failures": store.failures(),
    }
    store.set_meta("elapsed_before", report["duration_total_s"])
    paths = write_outputs(s.out_dir / s.slug, rows, report)
    store.close()
    log.info("Done: %d businesses -> %s", len(rows), paths["csv"])
    return report
