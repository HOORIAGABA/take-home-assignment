"""Browser-backed client for Google Maps search results.

How it works:
1. Open a real Chromium page on google.com/maps and run one normal search. The page
   itself issues `/search?tbm=map&pb=...`; we capture that request as a *template*.
   Capturing it (instead of hard-coding `pb`) keeps us in step with whatever
   parameter set the current Maps front-end sends.
2. For every tile/page we rewrite three things in the template - query, viewport
   (centre + span) and result offset `!8i` - and issue it with `fetch()` from inside
   the page, so cookies, headers and TLS fingerprint are the browser's own.

No Places API, no third-party scraping service.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from ..util import RateLimiter, RetryableError, log, retry_async
from .parse import Blocked, Place, SchemaChanged, parse_search_payload

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


class BudgetExhausted(Exception):
    pass


class MapsClient:
    def __init__(self, *, rate: float, max_requests: int, headless: bool = True, lang: str = "en"):
        self.limiter = RateLimiter(rate)
        self.max_requests = max_requests
        self.headless = headless
        self.lang = lang
        self.requests = 0
        self.template: str | None = None
        self._pw = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    # ------------------------------------------------------------ lifecycle
    async def __aenter__(self) -> "MapsClient":
        self._pw = await async_playwright().start()
        self.browser = await self._pw.chromium.launch(headless=self.headless)
        self.context = await self.browser.new_context(locale="en-US", user_agent=UA,
                                                      viewport={"width": 1280, "height": 900})
        self.page = await self.context.new_page()
        return self

    async def __aexit__(self, *exc) -> None:
        for closer in (self.context, self.browser):
            try:
                if closer:
                    await closer.close()
            except Exception:  # noqa: BLE001 - best effort shutdown
                pass
        if self._pw:
            await self._pw.stop()

    async def _handle_consent(self) -> None:
        page = self.page
        assert page
        if "consent." not in page.url:
            return
        log.info("Consent page shown; choosing 'Reject all'")
        for label in ("Reject all", "Accept all"):
            btn = page.get_by_role("button", name=label)
            if await btn.count():
                await btn.first.click()
                await page.wait_for_load_state("domcontentloaded")
                return
        raise SchemaChanged("consent page detected but no known button found")

    async def bootstrap(self, query: str, lat: float, lng: float) -> None:
        """Load Maps once like a user would, and capture the search request template."""
        page = self.page
        assert page
        url = f"https://www.google.com/maps/search/{quote(query)}/@{lat},{lng},14z?hl={self.lang}"
        self.requests += 1
        async with page.expect_request(lambda r: "tbm=map" in r.url and "/search?" in r.url,
                                       timeout=45_000) as req_info:
            await page.goto(url, wait_until="domcontentloaded")
            await self._handle_consent()
        if "/sorry/" in page.url:
            raise Blocked("Google redirected the first page load to /sorry/ (CAPTCHA)")
        req = await req_info.value
        self.template = req.url
        if "!7i20" not in self._pb(self.template):
            raise SchemaChanged("captured search request has no '!7i20' page-size field")
        log.info("Captured Maps search template (%d chars)", len(self.template))
        await page.wait_for_timeout(1500)

    # ------------------------------------------------------------ requests
    @staticmethod
    def _pb(url: str) -> str:
        return parse_qs(urlsplit(url).query)["pb"][0]

    def build_url(self, query: str, lat: float, lng: float, span: float, offset: int) -> str:
        assert self.template, "call bootstrap() first"
        pb = self._pb(self.template)
        q_pb = query.replace("!", "*21")
        pb, n = re.subn(r"^!1s[^!]*", f"!1s{q_pb}", pb, count=1)
        if not n:
            pb = f"!1s{q_pb}" + pb
        pb, n = re.subn(r"!1d[\d.]+!2d-?[\d.]+!3d-?[\d.]+", f"!1d{span}!2d{lng}!3d{lat}", pb, count=1)
        if not n:
            raise SchemaChanged("template has no viewport (!1d!2d!3d) block")
        pb = re.sub(r"!8i\d+", "", pb)
        if offset:
            pb = pb.replace("!7i20", f"!7i20!8i{offset}", 1)
        params = {"tbm": "map", "authuser": "0", "hl": self.lang, "q": query, "pb": pb}
        return "https://www.google.com/search?" + urlencode(params)

    async def search_page(self, query: str, lat: float, lng: float, span: float, offset: int) -> list[Place]:
        if self.requests >= self.max_requests:
            raise BudgetExhausted(f"request budget of {self.max_requests} reached")
        url = self.build_url(query, lat, lng, span, offset)

        async def once() -> list[Place]:
            await self.limiter.wait()
            self.requests += 1
            assert self.page
            try:
                res = await self.page.evaluate(
                    """async (u) => {
                        const r = await fetch(u, {credentials: 'include'});
                        return {status: r.status, url: r.url, text: await r.text()};
                    }""",
                    url,
                )
            except Exception as exc:  # page crashed / navigation / network
                raise RetryableError(f"fetch failed: {exc}") from exc
            status = res["status"]
            if status == 429 or "/sorry/" in res["url"]:
                self.limiter.penalize(60)
                raise Blocked(f"HTTP {status} / CAPTCHA from Google Maps")
            if status >= 500:
                raise RetryableError(f"HTTP {status}")
            if status != 200:
                raise SchemaChanged(f"unexpected HTTP {status}")
            return parse_search_payload(res["text"])

        return await retry_async(once, attempts=4, base=2.0, cap=60.0, what=f"maps page @{offset}")

    async def pause(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
