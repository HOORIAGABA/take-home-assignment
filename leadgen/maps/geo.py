"""City boundary lookup and the adaptive tile grid used to beat the ~120-results-per-search cap.

The city boundary comes from OpenStreetMap Nominatim (one request per city, cached
on disk). It is *not* a Google API; it is only used to know where the city is and
to drop results that fall outside it.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..util import log, point_in_polygons

M_PER_DEG_LAT = 111_320.0
# Ratio between the `!1d` "distance" value in the Maps request and the visible map
# height in metres, measured from a zoom-14 request at Lahore (27209 / 6256 m).
SPAN_PER_VISIBLE_HEIGHT = 4.349
VIEWPORT_ASPECT = 1024 / 768  # width / height used by the template request


@dataclass
class City:
    name: str
    south: float
    north: float
    west: float
    east: float
    polygons: list  # GeoJSON polygons, may be empty (then the bbox is used)

    def contains(self, lat: float | None, lng: float | None) -> bool:
        if lat is None or lng is None:
            return False
        if not (self.south <= lat <= self.north and self.west <= lng <= self.east):
            return False
        return point_in_polygons(lat, lng, self.polygons) if self.polygons else True

    @property
    def center(self) -> tuple[float, float]:
        return (self.south + self.north) / 2, (self.west + self.east) / 2


def resolve_city(query: str, cache_dir: Path, bbox: tuple[float, float, float, float] | None = None) -> City:
    """`bbox` = (south, west, north, east) overrides the lookup."""
    if bbox:
        s, w, n, e = bbox
        return City(query, s, n, w, e, [])
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / "geo_cache.json"
    store = json.loads(cache.read_text("utf-8")) if cache.exists() else {}
    key = query.strip().lower()
    if key not in store:
        log.info("Resolving city boundary for %r via OpenStreetMap Nominatim", query)
        resp = httpx.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "jsonv2", "limit": 1, "polygon_geojson": 1},
            headers={"User-Agent": "leadgen-takehome/0.1 (research prototype)"},
            timeout=30,
        )
        resp.raise_for_status()
        hits = resp.json()
        if not hits:
            raise SystemExit(f"Could not find city {query!r}; pass --bbox south,west,north,east")
        store[key] = hits[0]
        cache.write_text(json.dumps(store), "utf-8")
    hit = store[key]
    s, n, w, e = (float(x) for x in hit["boundingbox"])
    geom = hit.get("geojson") or {}
    if geom.get("type") == "Polygon":
        polys = [geom["coordinates"]]
    elif geom.get("type") == "MultiPolygon":
        polys = geom["coordinates"]
    else:
        polys = []
    return City(hit.get("display_name", query), s, n, w, e, polys)


@dataclass(frozen=True)
class Tile:
    south: float
    west: float
    north: float
    east: float
    depth: int = 0

    @property
    def id(self) -> str:
        return f"{self.south:.5f},{self.west:.5f},{self.north:.5f},{self.east:.5f}"

    @property
    def center(self) -> tuple[float, float]:
        return (self.south + self.north) / 2, (self.west + self.east) / 2

    @property
    def height_m(self) -> float:
        return (self.north - self.south) * M_PER_DEG_LAT

    @property
    def width_m(self) -> float:
        lat = self.center[0]
        return (self.east - self.west) * M_PER_DEG_LAT * math.cos(math.radians(lat))

    def span_param(self) -> float:
        """Value for the `!1d` field so the viewport just covers this tile."""
        visible_h = max(self.height_m, self.width_m / VIEWPORT_ASPECT)
        return round(visible_h * SPAN_PER_VISIBLE_HEIGHT, 3)

    def contains(self, lat: float | None, lng: float | None) -> bool:
        if lat is None or lng is None:
            return False
        return self.south <= lat < self.north and self.west <= lng < self.east

    def split(self) -> list["Tile"]:
        mlat, mlng = self.center
        d = self.depth + 1
        return [
            Tile(self.south, self.west, mlat, mlng, d),
            Tile(self.south, mlng, mlat, self.east, d),
            Tile(mlat, self.west, self.north, mlng, d),
            Tile(mlat, mlng, self.north, self.east, d),
        ]

    @classmethod
    def from_id(cls, tile_id: str, depth: int) -> "Tile":
        s, w, n, e = (float(x) for x in tile_id.split(","))
        return cls(s, w, n, e, depth)


def _tile_touches_city(t: Tile, city: City) -> bool:
    if not city.polygons:
        return True
    samples = [(t.south + (t.north - t.south) * i / 4, t.west + (t.east - t.west) * j / 4)
               for i in range(5) for j in range(5)]
    if any(city.contains(lat, lng) for lat, lng in samples):
        return True
    # City polygon vertex inside the tile (thin slivers of the boundary).
    for poly in city.polygons:
        for lng, lat in poly[0]:
            if t.contains(lat, lng):
                return True
    return False


def initial_tiles(city: City, tile_km: float) -> list[Tile]:
    lat_step = tile_km * 1000 / M_PER_DEG_LAT
    lng_step = tile_km * 1000 / (M_PER_DEG_LAT * math.cos(math.radians(city.center[0])))
    tiles = []
    lat = city.south
    while lat < city.north:
        lng = city.west
        while lng < city.east:
            t = Tile(lat, lng, min(lat + lat_step, city.north), min(lng + lng_step, city.east), 0)
            if _tile_touches_city(t, city):
                tiles.append(t)
            lng += lng_step
        lat += lat_step
    return tiles
