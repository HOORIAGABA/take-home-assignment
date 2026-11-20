"""Stretch goal: which emails can receive mail - without sending any email.

Levels (cheapest first):
  1. syntax + junk filtering (emails.is_valid_email)
  2. DNS: the domain must publish MX records (or an A record, RFC 5321 implicit MX)
     and must not publish a "null MX" (RFC 7505) saying it accepts no mail.
  3. Optional SMTP probe (`--smtp-probe`, off by default): connect to the MX,
     `MAIL FROM:<>`, `RCPT TO:<addr>`, read the reply code, then `QUIT` - no DATA,
     so nothing is ever delivered. A second RCPT to a random address detects
     catch-all servers, where a 250 proves nothing. Most residential ISPs block
     outbound port 25, and big providers (Gmail/Outlook) answer 250 for everything,
     so this is best-effort and reported as such.
"""

from __future__ import annotations

import asyncio
import secrets
import smtplib
import socket

import dns.asyncresolver
import dns.exception
import dns.resolver

FREE_PROVIDERS = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com", "icloud.com",
                  "yahoo.co.uk", "ymail.com", "aol.com", "protonmail.com", "proton.me"}
DISPOSABLE = {"mailinator.com", "10minutemail.com", "guerrillamail.com", "tempmail.com", "yopmail.com"}


class MailVerifier:
    def __init__(self, smtp_probe: bool = False, helo_host: str = "localhost"):
        self.smtp_probe = smtp_probe
        self.helo_host = helo_host
        self.resolver = dns.asyncresolver.Resolver()
        self.resolver.lifetime = 8.0
        self._mx_cache: dict[str, tuple[str, list[str]]] = {}
        self._lock = asyncio.Lock()

    async def mx(self, domain: str) -> tuple[str, list[str]]:
        """Return (status, hosts). status: mx | implicit_mx | null_mx | no_domain | dns_error."""
        if domain in self._mx_cache:
            return self._mx_cache[domain]
        try:
            ans = await self.resolver.resolve(domain, "MX")
            recs = sorted(ans, key=lambda r: r.preference)
            hosts = [str(r.exchange).rstrip(".") for r in recs]
            res = ("null_mx", []) if hosts in ([""], ["."]) or not any(hosts) else ("mx", hosts)
        except dns.resolver.NXDOMAIN:
            res = ("no_domain", [])
        except dns.resolver.NoAnswer:
            try:
                await self.resolver.resolve(domain, "A")
                res = ("implicit_mx", [domain])
            except Exception:  # noqa: BLE001
                res = ("no_domain", [])
        except (dns.exception.Timeout, dns.resolver.NoNameservers) as exc:
            res = ("dns_error", [str(exc)[:80]])
        self._mx_cache[domain] = res
        return res

    def _probe(self, host: str, email: str) -> tuple[int, bool | None]:
        domain = email.rsplit("@", 1)[1]
        with smtplib.SMTP(host, 25, timeout=12, local_hostname=self.helo_host) as s:
            s.ehlo_or_helo_if_needed()
            s.mail("")
            code, _ = s.rcpt(email)
            catch_all = None
            if code == 250:
                c2, _ = s.rcpt(f"no-such-user-{secrets.token_hex(6)}@{domain}")
                catch_all = c2 == 250
            s.rset()
        return code, catch_all

    async def verify(self, email: str) -> dict:
        domain = email.rsplit("@", 1)[1]
        status, hosts = await self.mx(domain)
        out: dict = {"email": email, "mx": status, "free_provider": domain in FREE_PROVIDERS,
                     "disposable": domain in DISPOSABLE}
        if status in ("null_mx", "no_domain"):
            out["verdict"] = "undeliverable"
            return out
        if status == "dns_error":
            out["verdict"] = "unknown"
            return out
        out["verdict"] = "domain_accepts_mail"
        if self.smtp_probe and hosts and domain not in FREE_PROVIDERS:
            try:
                code, catch_all = await asyncio.to_thread(self._probe, hosts[0], email)
                out["smtp_code"] = code
                if code == 250:
                    out["verdict"] = "accept_all" if catch_all else "mailbox_exists"
                elif 500 <= code < 600:
                    out["verdict"] = "mailbox_rejected"
            except (OSError, smtplib.SMTPException, socket.timeout) as exc:
                out["smtp_error"] = type(exc).__name__  # usually: port 25 blocked by ISP
        return out
