"""Fetch a business website (home page + a couple of contact/about pages) and pull contacts.

* plain HTTP first (httpx) - fast and cheap;
* if no email was found and the page looks script-rendered, optionally re-load it in
  Chromium and read the DOM after JavaScript ran (catches mailto links built in JS);
* global rate limit + concurrency cap + one request at a time per host;
* exponential backoff on 429/5xx/timeouts; 4xx other than 429 is not retried.
"""

from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from ..util import RateLimiter, RetryableError, log, retry_async, uniq
from .emails import valid_emails
from .socials import extract_socials, is_social_url

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
CONTACT_HINT = re.compile(r"contact|about|reach|location|find-us|get-in-touch|appointment|book", re.I)
MAX_BYTES = 3_000_000
ROBOTS_UA = "leadgen"   # we match robots.txt rules for '*' unless a site names us


def _host(url: str) -> str:
    h = (urlsplit(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def _same_site(a: str, b: str) -> bool:
    ha, hb = _host(a), _host(b)
    return ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha)


def contact_links(base_url: str, soup: BeautifulSoup, limit: int) -> list[str]:
    scored = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        full = urljoin(base_url, href).split("#")[0]
        if not full.startswith("http") or not _same_site(full, base_url) or full.rstrip("/") == base_url.rstrip("/"):
            continue
        label = f"{href} {a.get_text(' ', strip=True)}"
        if CONTACT_HINT.search(label):
            score = 0 if re.search(r"contact", label, re.I) else 1
            scored.append((score, full))
    return uniq(u for _, u in sorted(scored))[:limit]


def looks_js_rendered(html: str, soup: BeautifulSoup) -> bool:
    text_len = len(soup.get_text(" ", strip=True))
    return text_len < 400 or bool(re.search(r'id="(root|app|__next)"|wix|elementor-lazy', html, re.I))


class WebsiteEnricher:
    def __init__(self, *, rate: float, concurrency: int, extra_pages: int = 2, js_fallback: bool = True,
                 browser=None, default_cc: str = "92", timeout: float = 20.0, respect_robots: bool = True):
        self.limiter = RateLimiter(rate)
        self.sem = asyncio.Semaphore(concurrency)
        self.host_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.extra_pages = extra_pages
        self.js_fallback = js_fallback and browser is not None
        self.browser = browser
        self.render_sem = asyncio.Semaphore(2)
        self.default_cc = default_cc
        self.http = httpx.AsyncClient(
            follow_redirects=True, timeout=timeout, headers={"User-Agent": UA, "Accept-Language": "en"},
            limits=httpx.Limits(max_connections=concurrency * 2), verify=False,  # many small-business certs are broken
        )
        self.pages_fetched = 0
        self.respect_robots = respect_robots
        self._robots: dict[str, RobotFileParser | None] = {}

    async def allowed_by_robots(self, url: str) -> bool:
        """Honour robots.txt (cached per origin). Unreachable robots.txt = allowed, per RFC 9309."""
        if not self.respect_robots:
            return True
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            rp: RobotFileParser | None = None
            try:
                await self.limiter.wait()
                r = await self.http.get(origin + "/robots.txt")
                if r.status_code == 200 and "text" in r.headers.get("content-type", "text"):
                    rp = RobotFileParser()
                    rp.parse(r.text.splitlines())
            except httpx.HTTPError:
                rp = None
            self._robots[origin] = rp
        rp = self._robots[origin]
        return rp is None or rp.can_fetch(ROBOTS_UA, url)

    async def close(self) -> None:
        await self.http.aclose()

    async def fetch(self, url: str) -> tuple[str, str]:
        """Return (final_url, html). Raises on permanent failure."""
        if not await self.allowed_by_robots(url):
            raise PermissionError("disallowed by robots.txt")

        async def once() -> tuple[str, str]:
            await self.limiter.wait()
            self.pages_fetched += 1
            try:
                r = await self.http.get(url)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                raise RetryableError(f"{type(exc).__name__}") from exc
            if r.status_code == 429:
                ra = r.headers.get("retry-after", "")
                raise RetryableError("HTTP 429", float(ra) if ra.isdigit() else 10.0)
            if r.status_code >= 500:
                raise RetryableError(f"HTTP {r.status_code}")
            if r.status_code >= 400:
                raise PermissionError(f"HTTP {r.status_code}")
            ctype = r.headers.get("content-type", "")
            if "html" not in ctype and "text" not in ctype:
                raise ValueError(f"not HTML ({ctype})")
            return str(r.url), r.text[:MAX_BYTES]

        async with self.host_locks[_host(url)]:
            return await retry_async(once, attempts=3, base=2.0, cap=30.0, what=url)

    async def render(self, url: str) -> tuple[str, list[str]]:
        """Load in Chromium; return DOM html after scripts + all hrefs (incl. JS-built mailto)."""
        async with self.render_sem:
            await self.limiter.wait()
            self.pages_fetched += 1
            ctx = await self.browser.new_context(user_agent=UA)
            try:
                page = await ctx.new_page()
                await page.route("**/*.{png,jpg,jpeg,gif,webp,mp4,woff,woff2}", lambda r: r.abort())
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(2500)
                hrefs = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
                return await page.content(), hrefs
            finally:
                await ctx.close()

    async def enrich(self, website: str) -> dict:
        result: dict = {"emails": [], "instagram": [], "facebook": [], "linkedin": [], "whatsapp": [],
                        "pages": [], "rendered": False, "website_final": None}
        if is_social_url(website):
            # Social profile listed as "website": take it as-is, don't scrape the platform.
            soc = extract_socials([website], default_cc=self.default_cc)
            for k, v in soc.items():
                result[k] = v
            result["website_kind"] = "social"
            return result
        result["website_kind"] = "site"
        url = website if website.startswith("http") else "http://" + website

        async with self.sem:
            final, html = await self.fetch(url)
            result["website_final"] = final
            result["pages"].append(final)
            htmls, links = [html], []
            soup = BeautifulSoup(html, "lxml")
            links += [urljoin(final, a["href"]) for a in soup.find_all("a", href=True)]
            js_heavy = looks_js_rendered(html, soup)

            for extra in contact_links(final, soup, self.extra_pages):
                try:
                    _, h = await self.fetch(extra)
                    htmls.append(h)
                    result["pages"].append(extra)
                    links += [urljoin(extra, a["href"]) for a in BeautifulSoup(h, "lxml").find_all("a", href=True)]
                except Exception as exc:  # noqa: BLE001 - extra pages are best effort
                    log.debug("extra page %s failed: %r", extra, exc)

            emails = uniq(e for h in htmls for e in valid_emails(h))
            if not emails and self.js_fallback and js_heavy:
                try:
                    dom, hrefs = await self.render(final)
                    result["rendered"] = True
                    htmls.append(dom)
                    links += hrefs
                    emails = uniq(e for h in htmls for e in valid_emails(h + " " + " ".join(hrefs)))
                except Exception as exc:  # noqa: BLE001
                    log.debug("render %s failed: %r", final, exc)

        text = " ".join(BeautifulSoup(h, "lxml").get_text(" ", strip=True) for h in htmls)
        socials = extract_socials(links, text, self.default_cc)
        result.update(socials)
        # prefer addresses on the business's own domain first
        site = _host(final)
        result["emails"] = sorted(emails, key=lambda e: (not e.endswith("@" + site) and not e.endswith("." + site), e))
        return result
