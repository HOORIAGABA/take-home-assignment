"""Email extraction, de-obfuscation and validation.

Handles:
  * plain text and `mailto:` links (incl. URL-encoded and `?subject=` suffixes)
  * HTML entities (`info&#64;site.pk`, `&commat;`)
  * Cloudflare email protection (`data-cfemail="..."`, `/cdn-cgi/l/email-protection#...`)
  * written-out forms: `info [at] site [dot] pk`, `info(at)site(dot)pk`, `info at site dot pk`
  * JS-assembled addresses: `'info' + '@' + 'site.pk'`, `"mai" + "lto:"`, `\\u0040`, `\\x40`,
    `String.fromCharCode(...)`
Addresses built by arbitrary JavaScript are caught by the optional rendered-DOM pass
in website.py (we read the DOM after scripts ran).
"""

from __future__ import annotations

import html as htmlmod
import re
from urllib.parse import unquote

EMAIL_RE = re.compile(r"(?<![\w.+-])([a-z0-9][a-z0-9._%+-]{0,63}@(?:[a-z0-9-]{1,63}\.)+[a-z]{2,24})(?![\w-])", re.I)

_OPEN = r"\s*[\[\(\{<]\s*"
_CLOSE = r"\s*[\]\)\}>]\s*"
_AT_TOKEN = rf"(?:{_OPEN}(?:at|@){_CLOSE}|\s+at\s+)"
_DOT_TOKEN = rf"(?:{_OPEN}(?:dot|\.){_CLOSE}|\s+dot\s+)"
OBFUSCATED_RE = re.compile(
    rf"([a-z0-9][a-z0-9._%+-]{{0,63}}){_AT_TOKEN}([a-z0-9-]{{1,63}}(?:(?:{_DOT_TOKEN}|\.)[a-z0-9-]{{1,63}})+)",
    re.I,
)
CFEMAIL_ATTR_RE = re.compile(r'data-cfemail="([0-9a-fA-F]+)"')
CF_HREF_RE = re.compile(r"/cdn-cgi/l/email-protection#([0-9a-fA-F]+)")
MAILTO_RE = re.compile(r"mailto:([^\"'<>\s?]+)", re.I)
JS_CONCAT_RE = re.compile(r"""(["'])\s*\+\s*(["'])""")
FROMCHARCODE_RE = re.compile(r"String\.fromCharCode\(([\d,\s]+)\)")

BAD_TLDS = {"png", "jpg", "jpeg", "gif", "svg", "webp", "css", "js", "ico", "pdf", "mp4", "woff", "woff2", "ttf"}
PLACEHOLDER_DOMAINS = {
    "example.com", "example.org", "domain.com", "yourdomain.com", "email.com", "mail.com",
    "yoursite.com", "website.com", "company.com", "test.com", "sentry.io", "sentry-next.wixpress.com",
    "wixpress.com", "sentry.wixpress.com", "godaddy.com", "yourcompany.com", "mysite.com",
}
PLACEHOLDER_LOCALS = {"you", "yourname", "name", "user", "username", "email", "example", "test", "john.doe", "johndoe"}
ROLE_LOCALS = {"info", "contact", "admin", "office", "hello", "support", "sales", "appointments",
               "appointment", "reception", "enquiry", "enquiries", "inquiry", "help", "care"}


def decode_cfemail(hexstr: str) -> str | None:
    """Cloudflare: first byte is the XOR key for the remaining bytes."""
    try:
        data = bytes.fromhex(hexstr)
    except ValueError:
        return None
    if len(data) < 2:
        return None
    key = data[0]
    try:
        return bytes(b ^ key for b in data[1:]).decode("utf-8")
    except UnicodeDecodeError:
        return None


def _deobfuscate_written(text: str) -> list[str]:
    out = []
    for m in OBFUSCATED_RE.finditer(text):
        local, domain = m.group(1), m.group(2)
        domain = re.sub(_DOT_TOKEN, ".", domain, flags=re.I)
        out.append(f"{local}@{domain}")
    return out


def _unescape_js(text: str) -> str:
    text = re.sub(r"\\u0040|\\x40|\\100", "@", text)
    text = re.sub(r"\\u002[eE]|\\x2[eE]", ".", text)
    text = JS_CONCAT_RE.sub("", text)  # 'info' + '@' + 'x.pk' -> 'info@x.pk'

    def _chars(m: re.Match) -> str:
        try:
            return "".join(chr(int(c)) for c in m.group(1).split(",") if c.strip())
        except ValueError:
            return m.group(0)

    return FROMCHARCODE_RE.sub(_chars, text)


def extract_emails(raw_html: str) -> list[str]:
    """Return candidate addresses (unvalidated, lower-cased, de-duplicated) from a page."""
    found: list[str] = []
    for hx in CFEMAIL_ATTR_RE.findall(raw_html) + CF_HREF_RE.findall(raw_html):
        e = decode_cfemail(hx)
        if e:
            found.append(e)

    text = htmlmod.unescape(raw_html)
    js = _unescape_js(text)
    for src in (text, js):
        for m in MAILTO_RE.findall(src):
            found.extend(EMAIL_RE.findall(unquote(m)))
        found.extend(EMAIL_RE.findall(src))

    # written-out forms: run on visible-ish text (tags stripped) to avoid matching markup
    visible = re.sub(r"<[^>]+>", " ", text)
    found.extend(_deobfuscate_written(visible))

    out, seen = [], set()
    for e in found:
        e = e.strip().strip(".,;:").lower()
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


def is_valid_email(email: str) -> bool:
    m = EMAIL_RE.fullmatch(email)
    if not m:
        return False
    local, _, domain = email.rpartition("@")
    tld = domain.rsplit(".", 1)[-1]
    if tld in BAD_TLDS or not tld.isalpha():
        return False  # image@2x.png, bundle@1.2.js
    if domain in PLACEHOLDER_DOMAINS or domain.endswith(".wixpress.com") or domain.endswith("sentry.io"):
        return False
    if local in PLACEHOLDER_LOCALS:
        return False
    if ".." in email or local.startswith(".") or local.endswith("."):
        return False
    if re.fullmatch(r"[0-9a-f]{16,}", local):  # tracking hashes, e.g. sentry DSNs
        return False
    if len(local) > 64 or len(domain) > 253:
        return False
    return True


def is_role_address(email: str) -> bool:
    return email.split("@", 1)[0] in ROLE_LOCALS


def valid_emails(raw_html: str) -> list[str]:
    return [e for e in extract_emails(raw_html) if is_valid_email(e)]
