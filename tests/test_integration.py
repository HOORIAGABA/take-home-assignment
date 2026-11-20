"""Offline integration tests: pipeline glue + website enrichment against a local HTTP server."""

import asyncio
import csv
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from leadgen.enrich.website import WebsiteEnricher
from leadgen.maps.parse import parse_search_payload
from leadgen.pipeline import Settings, run
from leadgen.store import Store

FIX = Path(__file__).parent / "fixtures" / "maps_search_page.txt"


def test_pipeline_filter_dedupe_export(tmp_path):
    s = Settings(category="dental clinic", city="Lahore, Pakistan", out_dir=tmp_path / "out", data_dir=tmp_path,
                 bbox=(31.2, 74.0, 31.8, 74.7), skip_maps=True, skip_enrich=True, js_fallback=False)
    store = Store(tmp_path / "runs" / f"{s.slug}.sqlite3")
    places = parse_search_payload(FIX.read_text("utf-8"))
    store.save_page("dental clinic", "t", 0, places, len(places), 0)
    store.close()

    report = asyncio.run(run(s))
    out = tmp_path / "out" / s.slug
    rows = list(csv.DictReader((out / "businesses.csv").open(encoding="utf-8-sig")))
    assert report["counts"]["unique_listings_seen (by CID)"] == 20
    assert len(rows) == report["counts"]["final_businesses"] > 15
    assert json.loads((out / "report.json").read_text("utf-8"))["maps_status"] == "skipped"
    assert "Run report" in (out / "report.md").read_text("utf-8")
    assert all(r["cid"] and r["name"] for r in rows)


CF = "5a" + "".join(f"{ord(c) ^ 0x5A:02x}" for c in "dr.saad@smiledental.pk")
SITE = {
    "index.html": """<html><body><h1>Smile Dental</h1>
        <a href="/contact-us.html">Contact us</a>
        <a href="https://www.instagram.com/smiledental.pk/">IG</a>
        <a href="https://wa.me/923001234567">WhatsApp</a>
        <p>""" + "Lorem ipsum dolor sit amet. " * 30 + """</p></body></html>""",
    "contact-us.html": f"""<html><body>Write to info [at] smiledental [dot] pk
        <a href="/cdn-cgi/l/email-protection" data-cfemail="{CF}">x</a>
        <a href="https://facebook.com/SmileDentalPK">FB</a></body></html>""",
}


def test_website_enrichment_end_to_end(tmp_path):
    for name, body in SITE.items():
        (tmp_path / name).write_text(body, "utf-8")
    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *a, **k: None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        async def go():
            e = WebsiteEnricher(rate=50, concurrency=2, js_fallback=False)
            try:
                return await e.enrich(f"http://127.0.0.1:{srv.server_port}/index.html")
            finally:
                await e.close()
        r = asyncio.run(go())
    finally:
        srv.shutdown()
    assert "info@smiledental.pk" in r["emails"]
    assert "dr.saad@smiledental.pk" in r["emails"] and len(r["emails"]) == 2   # Cloudflare-protected
    assert r["instagram"] == ["https://www.instagram.com/smiledental.pk"]
    assert r["facebook"] == ["https://www.facebook.com/SmileDentalPK"]
    assert r["whatsapp"] == ["+923001234567"]
    assert len(r["pages"]) == 2


def test_robots_txt_is_respected(tmp_path):
    (tmp_path / "robots.txt").write_text("User-agent: *\nDisallow: /private/\n", "utf-8")
    (tmp_path / "private").mkdir()
    (tmp_path / "private" / "index.html").write_text("<p>secret@x.pk</p>", "utf-8")
    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *a, **k: None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        async def go():
            e = WebsiteEnricher(rate=50, concurrency=1, js_fallback=False)
            try:
                return await e.enrich(f"http://127.0.0.1:{srv.server_port}/private/index.html")
            finally:
                await e.close()
        try:
            asyncio.run(go())
            raise AssertionError("should have refused")
        except PermissionError as exc:
            assert "robots" in str(exc)
    finally:
        srv.shutdown()
