"""`leads <category> <city>` - reply with a summary of a Part 1 dataset and attach its CSV."""

from __future__ import annotations

import json
import re
from pathlib import Path

HELP_TEXT = "To get a list of businesses send: *leads <category> <city>*, e.g. *leads dental clinic Lahore*"


def default_reply(text: str) -> str:
    """Fixed reply for any text that isn't a command (the brief asks for a reply, not an AI one)."""
    return f"Got your message: \"{text.strip()[:300]}\"\n\n{HELP_TEXT}"


# "leeds": speech-to-text often hears the spoken command as the homophone (measured: "Leeds ...").
LEADS_RE = re.compile(r"^\s*/?(?:leads|leeds)\b[\s,.:;!-]*(.*?)[\s.!?]*$", re.I | re.S)


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower())) - {"in", "of", "the"}


def find_datasets(dirs: list[Path]) -> list[dict]:
    """Every Part 1 output folder (has report.json + businesses.csv) under the given dirs."""
    out = []
    for d in dirs:
        if not d.exists():
            continue
        for rep in d.rglob("report.json"):
            csv = rep.parent / "businesses.csv"
            if not csv.exists():
                continue
            try:
                meta = json.loads(rep.read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            out.append({"report": meta, "csv": csv, "json": rep.parent / "businesses.json",
                        "mtime": rep.stat().st_mtime})
    return out


def match_dataset(args: str, datasets: list[dict]) -> dict | None:
    want = _tokens(args)
    best = None
    for ds in datasets:
        r = ds["report"]
        cat = _tokens(r.get("category", ""))
        city_main = _tokens(r.get("city", "").split(",")[0])
        if cat and city_main and cat <= want and city_main <= want:
            if best is None or ds["mtime"] > best["mtime"]:
                best = ds
    return best


def summarize(ds: dict, top_n: int = 5) -> str:
    r = ds["report"]
    c = r["counts"]
    lines = [
        f"*{r['category'].title()} in {r['city']}*",
        f"{c['final_businesses']} businesses found on Google Maps",
        f"- with phone: {c['with_phone']}",
        f"- with website: {c['with_website']}",
        f"- with email: {c['with_email']} ({c['total_emails']} addresses)",
        f"- with Instagram: {c['with_instagram']} | Facebook: {c['with_facebook']} | WhatsApp: {c['with_whatsapp']}",
    ]
    try:
        rows = json.loads(ds["json"].read_text("utf-8"))
        top = sorted((x for x in rows if (x.get("review_count") or 0) >= 20 and x.get("rating")),
                     key=lambda x: (-x["rating"], -(x.get("review_count") or 0)))[:top_n]
        if top:
            lines.append("")
            lines.append("Top rated (20+ reviews):")
            lines += [f"{i}. {x['name']} - {x['rating']} ({x['review_count']})" for i, x in enumerate(top, 1)]
    except (OSError, json.JSONDecodeError, KeyError):
        pass
    lines.append("")
    lines.append(f"Data collected {r.get('finished_at', '?')[:10]}. Full list attached as CSV.")
    return "\n".join(lines)


def handle_leads(text: str, dirs: list[Path]) -> dict | None:
    """Return a reply payload, or None if `text` is not a leads command."""
    m = LEADS_RE.match(text or "")
    if not m:
        return None
    args = m.group(1).strip()
    datasets = find_datasets(dirs)
    if not args:
        return {"text": "Usage: leads <category> <city>, e.g. *leads dental clinic Lahore*"}
    ds = match_dataset(args, datasets)
    if not ds:
        avail = sorted({f"{d['report']['category']} {d['report']['city'].split(',')[0]}" for d in datasets})
        hint = ("Available: " + "; ".join(avail)) if avail else "No datasets have been collected yet."
        return {"text": f"I don't have data for \"{args}\" yet. {hint}"}
    name = re.sub(r"[^a-z0-9]+", "-", f"{ds['report']['category']} {ds['report']['city'].split(',')[0]}".lower())
    return {"text": summarize(ds), "document": str(ds["csv"].resolve()), "filename": f"{name.strip('-')}.csv"}
