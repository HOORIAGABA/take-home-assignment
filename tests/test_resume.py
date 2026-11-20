"""Crash/resume of the Maps crawl, with a fake client (no network)."""

import asyncio

import pytest

from leadgen.maps.crawl import crawl_query
from leadgen.maps.geo import Tile
from leadgen.maps.parse import Place
from leadgen.store import Store


class Crash(BaseException):
    """Like KeyboardInterrupt / the process being killed: nothing gets to clean up."""


class FakeClient:
    """Each tile holds `n` places at its centre; optionally dies after `crash_after` requests."""

    def __init__(self, n_per_tile: int, crash_after: int | None = None):
        self.n = n_per_tile
        self.crash_after = crash_after
        self.requests = 0
        self.calls: list[tuple] = []

    async def search_page(self, query, lat, lng, span, offset):
        if self.crash_after is not None and self.requests >= self.crash_after:
            raise Crash()
        self.requests += 1
        self.calls.append((round(lat, 4), round(lng, 4), offset))
        start = offset
        count = max(0, min(20, self.n - offset))
        return [Place(cid=f"{lat:.4f}-{lng:.4f}-{i}", place_id=None, ftid="0x0:0x1", name=f"p{i}", category="Dentist",
                      lat=lat, lng=lng) for i in range(start, start + count)]


def run(coro):
    return asyncio.run(coro)


def test_resume_after_crash_does_not_repeat_pages(tmp_path):
    store = Store(tmp_path / "s.sqlite3")
    store.add_tiles("q", [Tile(31.0, 74.0, 31.1, 74.1)])
    with pytest.raises(Crash):
        run(crawl_query(FakeClient(70, crash_after=2), store, "q", max_pages=10, max_depth=0))
    assert len(store.all_places()) == 40                       # 2 pages committed before the crash

    c2 = FakeClient(70)
    assert run(crawl_query(c2, store, "q", max_pages=10, max_depth=0)) == "complete"
    assert [off for *_, off in c2.calls] == [40, 60]           # resumed at offset 40, no repeats
    assert len(store.all_places()) == 70
    assert store.tile_stats()["q"] == {"done": 1}


def test_saturated_tile_is_split(tmp_path):
    store = Store(tmp_path / "s.sqlite3")
    store.add_tiles("q", [Tile(31.0, 74.0, 31.1, 74.1)])
    c = FakeClient(500)
    assert run(crawl_query(c, store, "q", max_pages=2, max_depth=1)) == "complete"
    stats = store.tile_stats()["q"]
    assert stats["split"] == 1 and stats["done"] == 4
    # children saturate too but are at max depth -> recorded as a coverage failure, not silently dropped
    assert any("saturated at max depth" in f["error"] for f in store.failures())


def test_duplicates_across_tiles_are_stored_once(tmp_path):
    store = Store(tmp_path / "s.sqlite3")
    p = Place(cid="42", place_id=None, ftid="0x0:0x2a", name="Same", category="Dentist", lat=31.05, lng=74.05)
    assert store.save_page("q", "t1", 0, [p], 1, 0) == 1
    assert store.save_page("q2", "t2", 0, [p], 1, 0) == 0
    assert len(store.all_places()) == 1
    assert store.hits_by_query() == {"q": {"42"}, "q2": {"42"}}


class FlakyClient(FakeClient):
    async def search_page(self, query, lat, lng, span, offset):
        if offset == 20 and not getattr(self, "failed", False):
            self.failed = True
            raise RuntimeError("transient")
        return await super().search_page(query, lat, lng, span, offset)


def test_failed_tile_is_retried_on_next_run_from_its_cursor(tmp_path):
    store = Store(tmp_path / "s.sqlite3")
    store.add_tiles("q", [Tile(31.0, 74.0, 31.1, 74.1)])
    assert run(crawl_query(FlakyClient(50), store, "q", max_pages=10, max_depth=0)) == "incomplete"
    c = FakeClient(50)
    assert run(crawl_query(c, store, "q", max_pages=10, max_depth=0)) == "complete"
    assert [off for *_, off in c.calls] == [20, 40]
    assert len(store.all_places()) == 50


class DriftClient(FakeClient):
    """Endless results, but only the first page is inside the tile (Google drifting to neighbours)."""

    async def search_page(self, query, lat, lng, span, offset):
        places = await super().search_page(query, lat, lng, span, offset)
        if offset:
            for p in places:
                p.lat += 1.0
        return places


def test_full_but_drifting_tile_is_not_split(tmp_path):
    store = Store(tmp_path / "s.sqlite3")
    store.add_tiles("q", [Tile(31.0, 74.0, 31.1, 74.1)])
    c = DriftClient(10_000)
    assert run(crawl_query(c, store, "q", max_pages=6, max_depth=3, stale_limit=10)) == "complete"
    assert store.tile_stats()["q"] == {"done": 1} and len(c.calls) == 6


def test_coverage_query_runs_on_sample_after_main(tmp_path):
    from leadgen.pipeline import Settings, _prepare_coverage_queries
    from leadgen.maps.geo import City
    s = Settings(category="q", city="X", extra_queries=["q2"], coverage_sample=3)
    city = City("X", 31.0, 31.4, 74.0, 74.4, [])
    store = Store(tmp_path / "s.sqlite3")
    tiles = [Tile(31.0 + i * 0.1, 74.0, 31.1 + i * 0.1, 74.1) for i in range(4)]
    store.add_tiles("q", tiles)
    store.add_tiles("q2", tiles)                                  # stale full-city grid from an old run
    assert _prepare_coverage_queries(s, store, city) is False     # main query not finished yet
    for t in tiles:
        store.finish_tile("q", t.id, "done")
    assert _prepare_coverage_queries(s, store, city) is True
    assert len(store.tiles_for("q2")) == 3                        # replaced by a 3-tile sample
    assert {t for t, _ in store.tiles_for("q2")} <= {t.id for t in tiles}
