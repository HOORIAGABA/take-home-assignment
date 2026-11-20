"""Adaptive tile crawl.

For each tile we page through results (20 per request) until one of:
  * the page has < 20 results              -> end of list for this viewport
  * `stale_limit` pages in a row added no result located inside the tile
                                           -> Google has moved on to far-away places
  * we hit `max_pages` and the last two pages were still mostly inside the tile
                                           -> the tile is *saturated*: split it into
                                              4 children and crawl those (until max_depth)
    (if the last pages were mostly outside the tile, Google is already drifting to
     neighbours that their own tiles cover, so splitting would only re-fetch them)

A tile cursor (`next_offset`) is checkpointed after every page.
"""

from __future__ import annotations

from ..store import Store
from ..util import log
from .client import BudgetExhausted, MapsClient
from .geo import Tile
from .parse import Blocked, SchemaChanged

PAGE = 20


async def crawl_query(client: MapsClient, store: Store, query: str, *, max_pages: int,
                      max_depth: int, stale_limit: int = 1, split_min_in_tile: int = 10) -> str:
    """Crawl all pending tiles for `query`. Returns 'complete' | 'incomplete' | 'budget' | 'blocked'.

    A tile that errors stays `pending` (cursor intact) and is skipped for the rest of this
    run, so the next run retries it from where it stopped.
    """
    skipped: set[str] = set()
    while True:
        pending = [r for r in store.pending_tiles(query) if r["tile_id"] not in skipped]
        if not pending:
            return "incomplete" if skipped else "complete"
        for row in pending:
            tile = Tile.from_id(row["tile_id"], row["depth"])
            offset, stale = row["next_offset"], row["stale_pages"]
            lat, lng = tile.center
            span = tile.span_param()
            saturated = False
            try:
                while True:
                    if offset >= max_pages * PAGE:
                        saturated = True
                        break
                    places = await client.search_page(query, lat, lng, span, offset)
                    in_tile = sum(tile.contains(p.lat, p.lng) for p in places)
                    stale = 0 if in_tile else stale + 1
                    new = store.save_page(query, tile.id, offset, places, in_tile, stale)
                    log.info("[%s] tile d%d %s off=%-3d got=%2d in_tile=%2d new=%2d (req %d)",
                             query, tile.depth, tile.id, offset, len(places), in_tile, new, client.requests)
                    offset += PAGE
                    if len(places) < PAGE or stale >= stale_limit:
                        break
            except BudgetExhausted:
                log.warning("Request budget reached; progress saved. Re-run to continue.")
                return "budget"
            except Blocked as exc:
                store.log_failure("maps", tile.id, str(exc))
                log.error("Google is blocking us (%s). Stopping Maps phase; re-run later to resume.", exc)
                return "blocked"
            except SchemaChanged:
                raise  # loud failure: never report "0 results" for a format change
            except Exception as exc:  # noqa: BLE001 - one bad tile must not kill the run
                store.log_failure("maps", tile.id, repr(exc))
                skipped.add(tile.id)
                log.warning("Tile %s failed (%r); will retry on the next run", tile.id, exc)
                continue

            dense = sum(store.recent_in_tile(query, tile.id, 2)) >= split_min_in_tile
            if saturated and dense and tile.depth < max_depth:
                kids = tile.split()
                store.finish_tile(query, tile.id, "split", children=kids)
                log.info("Tile %s saturated after %d pages -> split into %d", tile.id, max_pages, len(kids))
            else:
                if saturated and dense:
                    store.log_failure("maps", tile.id, "saturated at max depth; may be incomplete")
                store.finish_tile(query, tile.id, "done")
