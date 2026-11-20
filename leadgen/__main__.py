"""CLI:  python -m leadgen --category "dental clinic" --city "Lahore, Pakistan" """

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .maps.parse import SchemaChanged
from .pipeline import Settings, run
from .util import log, setup_logging


def parse_args(argv: list[str] | None = None) -> Settings:
    load_dotenv()
    env = os.environ.get
    p = argparse.ArgumentParser(prog="leadgen", description="Every business of a category in a city, with contacts.")
    p.add_argument("--category", required=True)
    p.add_argument("--city", required=True)
    p.add_argument("--extra-queries", default="", help="comma-separated extra queries for coverage checks, e.g. 'dentist'")
    p.add_argument("--bbox", help="south,west,north,east (skip the OSM city lookup)")
    p.add_argument("--out", default=env("LEADGEN_OUT", "output"))
    p.add_argument("--data", default=env("LEADGEN_DATA", "data"))
    p.add_argument("--tile-km", type=float, default=5.0)
    p.add_argument("--max-depth", type=int, default=3)
    p.add_argument("--max-pages", type=int, default=6, help="pages of 20 per tile before splitting it")
    p.add_argument("--coverage-sample", type=int, default=12,
                   help="run --extra-queries on this many random finished tiles (0 = whole city)")
    p.add_argument("--maps-rate", type=float, default=float(env("MAPS_RATE", 0.5)), help="Maps requests/second")
    p.add_argument("--max-requests", type=int, default=int(env("MAPS_MAX_REQUESTS", 600)))
    p.add_argument("--web-rate", type=float, default=float(env("WEB_RATE", 4)), help="website requests/second")
    p.add_argument("--concurrency", type=int, default=int(env("WEB_CONCURRENCY", 6)))
    p.add_argument("--extra-pages", type=int, default=2, help="contact/about pages per site")
    p.add_argument("--no-js-fallback", action="store_true")
    p.add_argument("--smtp-probe", action="store_true", help="RCPT-TO mailbox probe (sends no email)")
    p.add_argument("--headful", action="store_true")
    p.add_argument("--skip-maps", action="store_true", help="only re-enrich / re-export what is stored")
    p.add_argument("--skip-enrich", action="store_true")
    p.add_argument("--category-regex", help="override which Maps categories count as a match")
    p.add_argument("--country-code", default="92", help="default calling code for local numbers")
    p.add_argument("--fresh", action="store_true", help="discard the checkpoint and start over")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)
    setup_logging(a.verbose)
    s = Settings(
        category=a.category, city=a.city,
        extra_queries=[q.strip() for q in a.extra_queries.split(",") if q.strip()],
        out_dir=Path(a.out), data_dir=Path(a.data),
        bbox=tuple(float(x) for x in a.bbox.split(",")) if a.bbox else None,  # type: ignore[arg-type]
        tile_km=a.tile_km, max_depth=a.max_depth, max_pages=a.max_pages, maps_rate=a.maps_rate,
        max_requests=a.max_requests, web_rate=a.web_rate, concurrency=a.concurrency,
        extra_pages=a.extra_pages, coverage_sample=a.coverage_sample, js_fallback=not a.no_js_fallback, smtp_probe=a.smtp_probe,
        headless=not a.headful, skip_maps=a.skip_maps, skip_enrich=a.skip_enrich,
        category_regex=a.category_regex, default_cc=a.country_code,
    )
    if a.fresh:
        db = s.data_dir / "runs" / f"{s.slug}.sqlite3"
        for suffix in ("", "-wal", "-shm"):
            f = Path(str(db) + suffix)
            if f.exists():
                f.replace(f.with_name(f.name + ".bak"))
        log.info("--fresh: previous checkpoint moved to *.bak")
    return s


def main(argv: list[str] | None = None) -> int:
    s = parse_args(argv)
    try:
        report = asyncio.run(run(s))
    except SchemaChanged as exc:
        log.critical("Google Maps response format changed: %s. Progress is saved; fix the parser and re-run.", exc)
        return 3
    except KeyboardInterrupt:
        log.warning("Interrupted. All finished work is checkpointed; run the same command to resume.")
        return 130
    return 0 if report["maps_status"] in ("complete", "skipped") else 2


if __name__ == "__main__":
    sys.exit(main())
