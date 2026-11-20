"""Instagram / Facebook / LinkedIn / WhatsApp extraction from links and page text."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlsplit

from ..util import normalize_phone, uniq

IG_SKIP = {"p", "reel", "reels", "explore", "accounts", "stories", "tv", "about", "legal", "developer", "share"}
FB_SKIP = {"sharer", "sharer.php", "share.php", "dialog", "plugins", "tr", "login", "login.php", "help",
           "policies", "privacy", "watch", "groups", "events", "hashtag", "photo.php", "story.php", "share"}


def _parts(url: str) -> tuple[str, list[str], str]:
    try:
        s = urlsplit(url if "//" in url else "https://" + url)
    except ValueError:
        return "", [], ""
    host = (s.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    host = host[2:] if host.startswith("m.") else host
    return host, [p for p in s.path.split("/") if p], s.query


def instagram_handle(url: str) -> str | None:
    host, parts, _ = _parts(url)
    if host not in ("instagram.com", "instagr.am") or not parts:
        return None
    h = parts[0].lower()
    if h in IG_SKIP or not re.fullmatch(r"[a-z0-9._]{1,30}", h):
        return None
    return f"https://www.instagram.com/{h}"


def facebook_page(url: str) -> str | None:
    host, parts, query = _parts(url)
    if host not in ("facebook.com", "fb.com", "fb.me", "business.facebook.com") or not parts:
        return None
    if parts[0].lower() in FB_SKIP:
        return None
    if parts[0] == "profile.php":
        pid = parse_qs(query).get("id")
        return f"https://www.facebook.com/profile.php?id={pid[0]}" if pid else None
    if parts[0] == "pages" and len(parts) >= 2:
        return "https://www.facebook.com/" + "/".join(parts[:3])
    return f"https://www.facebook.com/{parts[0]}"


def linkedin_page(url: str) -> str | None:
    host, parts, _ = _parts(url)
    if not host.endswith("linkedin.com") or len(parts) < 2:
        return None
    if parts[0] in ("company", "in", "school", "showcase"):
        return f"https://www.linkedin.com/{parts[0]}/{parts[1]}"
    return None


def whatsapp_number(url: str, default_cc: str = "92") -> str | None:
    host, parts, query = _parts(url)
    if host == "wa.me" and parts:
        return normalize_phone("+" + parts[0], default_cc) if parts[0].isdigit() else None
    if host in ("api.whatsapp.com", "web.whatsapp.com", "whatsapp.com", "chat.whatsapp.com") or url.startswith("whatsapp:"):
        phone = parse_qs(query or urlsplit(url).query).get("phone")
        if phone:
            p = unquote(phone[0]).strip()
            return normalize_phone(p if p.startswith(("+", "0")) else "+" + p, default_cc)
    return None


WA_TEXT_RE = re.compile(r"whats\s?app[^0-9+]{0,25}(\+?[\d][\d\s-]{8,16}\d)", re.I)


def extract_socials(links: list[str], text: str = "", default_cc: str = "92") -> dict[str, list[str]]:
    ig, fb, li, wa = [], [], [], []
    for link in links:
        link = link.strip()
        if not link:
            continue
        if (h := instagram_handle(link)):
            ig.append(h)
        elif (f := facebook_page(link)):
            fb.append(f)
        elif (l := linkedin_page(link)):
            li.append(l)
        elif (w := whatsapp_number(link, default_cc)):
            wa.append(w)
    for m in WA_TEXT_RE.finditer(text):
        n = normalize_phone(m.group(1), default_cc)
        if n:
            wa.append(n)
    return {"instagram": uniq(ig), "facebook": uniq(fb), "linkedin": uniq(li), "whatsapp": uniq(wa)}


SOCIAL_HOSTS = ("instagram.com", "facebook.com", "fb.com", "fb.me", "linkedin.com", "wa.me",
                "whatsapp.com", "linktr.ee", "tiktok.com", "youtube.com", "twitter.com", "x.com")


def is_social_url(url: str) -> bool:
    host, _, _ = _parts(url)
    return any(host == h or host.endswith("." + h) for h in SOCIAL_HOSTS)
