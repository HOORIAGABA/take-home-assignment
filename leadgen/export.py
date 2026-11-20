"""CSV / JSON / run report writers."""

from __future__ import annotations

import csv
import json
from pathlib import Path

CSV_FIELDS = [
    "cid", "place_id", "name", "category", "categories", "address", "lat", "lng", "phone", "website",
    "rating", "review_count", "emails", "email_verdicts", "instagram", "facebook", "linkedin", "whatsapp",
    "enrich_status", "maps_url", "merged_cids",
]


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return "; ".join(str(x) for x in v)
    if isinstance(v, dict):
        return "; ".join(f"{k}={val}" for k, val in v.items())
    return str(v)


def write_outputs(out_dir: Path, rows: list[dict], report: dict) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"csv": out_dir / "businesses.csv", "json": out_dir / "businesses.json",
             "report_json": out_dir / "report.json", "report_md": out_dir / "report.md"}
    with paths["csv"].open("w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel shows Urdu correctly
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: _cell(r.get(k)) for k in CSV_FIELDS})
    paths["json"].write_text(json.dumps(rows, ensure_ascii=False, indent=1), "utf-8")
    paths["report_json"].write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    paths["report_md"].write_text(render_report_md(report), "utf-8")
    return paths


def render_report_md(r: dict) -> str:
    c = r["counts"]
    lines = [
        f"# Run report: {r['category']} in {r['city']}",
        "",
        f"- Started: {r['started_at']}  |  Finished: {r['finished_at']}  |  "
        f"Wall time this invocation: {r['duration_s']}s  |  All runs: {r.get('duration_total_s', '?')}s  |  "
        f"Resumed runs: {r['invocations'] - 1}",
        f"- Maps status: **{r['maps_status']}**  |  Maps requests (all runs): {r['maps_requests_total']}  |  "
        f"Website pages fetched (this run): {r['web_pages_fetched']}",
        "",
        "## Counts",
        "",
        "| Stage | Count |",
        "|---|---|",
    ]
    lines += [f"| {k.replace('_', ' ')} | {v} |" for k, v in c.items()]
    lines += ["", "## Tiles", "", "```", json.dumps(r["tiles"], indent=1), "```", "", "## Coverage", ""]
    cov = r.get("coverage", {})
    for x in cov.get("cross_query", []):
        lines.append(
            f"- vs `{x['query']}` ({x.get('scope', 'whole city')}): main found {x['main_found']}, other found {x['other_found']}, overlap "
            f"{x['overlap']}, only in other {x['only_in_other']}. Chapman estimate N~{x['chapman_population_estimate']} "
            f"-> main-query coverage ~{x['main_query_coverage_estimate']} (upper bound; queries are correlated)."
        )
    lines += ["", "Saturation curve (new unique places per bucket of requests):", "", "| requests | new | cumulative |",
              "|---|---|---|"]
    lines += [f"| {b['requests']} | {b['new_unique']} | {b['cumulative']} |" for b in cov.get("saturation_curve", [])]
    lines += ["", f"## Failures ({len(r['failures'])})", ""]
    lines += [f"- [{f['stage']}] {f['key']}: {f['error']}" for f in r["failures"][:200]]
    if len(r["failures"]) > 200:
        lines.append(f"- ... {len(r['failures']) - 200} more in report.json")
    return "\n".join(lines) + "\n"
