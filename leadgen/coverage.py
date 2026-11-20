"""Coverage checks - how do we know we found (nearly) everything?

1. Saturation curve: new unique businesses per request over the crawl. If the
   last N% of requests add almost nothing, more tiles at this granularity won't help.
2. Tile saturation: no leaf tile may end the crawl still "full" (that is what the
   adaptive split prevents); any that did are listed as failures.
3. Capture-recapture (Lincoln-Petersen / Chapman): run a second, independent query
   ("dentist") over a random sample of the finished tiles (cheap) or the whole city. If the main query found n1, the second found n2
   and m of the second's were already in the first, the estimated population is
   N ~= (n1+1)(n2+1)/(m+1) - 1. The ratio n1/N estimates the main query's coverage.
   (Queries aren't truly independent - both rank the same index - so this is an
   *upper bound* on coverage, and we report it as such.)
"""

from __future__ import annotations


def chapman(n1: int, n2: int, m: int) -> float | None:
    if n1 == 0 or n2 == 0:
        return None
    return (n1 + 1) * (n2 + 1) / (m + 1) - 1


def saturation(request_log: list[tuple], buckets: int = 10) -> list[dict]:
    if not request_log:
        return []
    total = len(request_log)
    size = max(1, total // buckets)
    out, cum = [], 0
    for start in range(0, total, size):
        chunk = request_log[start:start + size]
        new = sum(r[6] for r in chunk)
        cum += new
        out.append({"requests": f"{start + 1}-{start + len(chunk)}", "new_unique": new, "cumulative": cum})
    return out


def coverage_report(main_query: str, final_cids: set[str], hits: dict[str, set[str]],
                    request_log: list[tuple], coords: dict[str, tuple] | None = None,
                    sample_tiles: dict[str, list] | None = None) -> dict:
    """`sample_tiles[q]`: if an extra query was only run on a sample of tiles, compare both
    queries *inside those tiles only* (otherwise the main query looks artificially complete)."""
    rep: dict = {"saturation_curve": saturation(request_log), "cross_query": []}
    coords = coords or {}
    sample_tiles = sample_tiles or {}
    for q, cids in hits.items():
        if q == main_query:
            continue
        main = hits.get(main_query, set()) & final_cids
        other = cids & final_cids
        area = sample_tiles.get(q)
        if area:
            def inside(cid: str) -> bool:
                lat, lng = coords.get(cid, (None, None))
                return any(t.contains(lat, lng) for t in area)
            main = {c for c in main if inside(c)}
            other = {c for c in other if inside(c)}
        m = len(main & other)
        est = chapman(len(main), len(other), m)
        rep["cross_query"].append({
            "query": q, "scope": f"{len(area)} sampled tiles" if area else "whole city",
            "main_found": len(main), "other_found": len(other), "overlap": m,
            "only_in_other": len(other - main),
            "chapman_population_estimate": round(est) if est else None,
            "main_query_coverage_estimate": round(len(main) / est, 3) if est else None,
            "union_found": len(main | other),
        })
    curve = rep["saturation_curve"]
    if len(curve) >= 2 and curve[-1]["cumulative"]:
        rep["last_bucket_share_of_new"] = round(curve[-1]["new_unique"] / curve[-1]["cumulative"], 3)
    return rep
