"""WhatsApp assistant process: connection, event hooks and the exactly-once reply worker."""

from __future__ import annotations

import asyncio
import ctypes
import json
import logging
import os
import random
import time

import segno
from neonize.aioze.client import NewAClient
from neonize.events import (
    EVENT_TO_INT,
    ConnectedEv,
    ConnectFailureEv,
    DisconnectedEv,
    KeepAliveTimeoutEv,
    LoggedOutEv,
    MessageEv,
    OfflineSyncCompletedEv,
    PairStatusEv,
    ReceiptEv,
    StreamReplacedEv,
    TemporaryBanEv,
)
from neonize.utils.enum import ChatPresence, ChatPresenceMedia, ReceiptType
from neonize.utils.jid import build_jid

from .alerts import send_alert
from .allowlist import Allowlist
from .commands import HELP_TEXT, default_reply, handle_leads
from .config import Config
from .inbound import jid_str, normalize_ts, to_inbound
from .ledger import Ledger
from .transcribe import Transcriber

log = logging.getLogger("wabot")

EXIT_NEEDS_HUMAN = 2      # logged out / banned / session replaced: supervisor must NOT restart
CRASH_POINTS = ("before_reply", "before_send", "after_send")


def maybe_crash(point: str, ledger: Ledger) -> None:
    """Test hook: WABOT_CRASH_AT=<point> hard-kills the process (like kill -9) the first time
    execution reaches <point>. One-shot (remembered in the ledger) so the restart can recover."""
    if os.environ.get("WABOT_CRASH_AT") != point or ledger.get_meta(f"crashed_{point}"):
        return
    ledger.set_meta(f"crashed_{point}", True)
    log.critical("WABOT_CRASH_AT=%s: simulating a hard crash now", point)
    logging.shutdown()
    os._exit(9)
EXIT_RECONNECT = 75       # connection lost too long: supervisor restarts us with backoff
RETRY_RECEIPT = 3         # Receipt.RETRY - not proof of delivery


class Bot:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.ledger = Ledger(cfg.ledger_db)
        self.allowlist = Allowlist(cfg.allowlist_file)
        self.transcriber = Transcriber(cfg.stt_model, cfg.stt_model_dir, cfg.stt_device, cfg.stt_compute_type,
                                       cfg.stt_language)
        self.client: NewAClient | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.wake = asyncio.Event()
        self.connected = False
        self.connected_at = 0.0
        self.disconnected_at: float | None = None
        self.ever_connected = False
        self.synced = False
        self.exit_code: int | None = None

    # ================================================================== hooks
    def _install_sync_hooks(self, client: NewAClient) -> None:
        """Persist messages and receipts *in the Go callback thread*, before whatsmeow acks them.

        neonize's async client otherwise hands events to asyncio via run_coroutine_threadsafe and
        returns immediately - a crash in that window would lose an already-acknowledged message.
        """
        msg_code, rcpt_code = EVENT_TO_INT[MessageEv], EVENT_TO_INT[ReceiptEv]
        original = client.event.execute
        ledger, loop, wake = self.ledger, self.loop, self.wake

        def execute(uuid: int, binary: int, size: int, code: int):
            try:
                if code == msg_code:
                    data = ctypes.string_at(binary, size)
                    m = to_inbound(MessageEv.FromString(data), raw=data)
                    if ledger.record_inbound(m):
                        log.info("inbound %s from %s (%s) kind=%s", m.msg_id, m.sender_pn or m.sender, m.chat, m.kind)
                    else:
                        log.info("duplicate delivery of %s ignored", m.msg_id)
                    loop.call_soon_threadsafe(wake.set)
                elif code == rcpt_code:
                    ev = ReceiptEv.FromString(ctypes.string_at(binary, size))
                    if int(ev.Type) != RETRY_RECEIPT:
                        src = ev.MessageSource
                        ledger.record_receipt([jid_str(src.Chat), jid_str(src.Sender), jid_str(src.SenderAlt)],
                                              list(ev.MessageIDs), int(ev.Type), normalize_ts(ev.Timestamp))
            except Exception:  # never raise into Go
                log.exception("sync hook failed")
            return original(uuid, binary, size, code)

        client.event.execute = execute  # read by connect() when it builds the C callback

    def _register_events(self, client: NewAClient) -> None:
        @client.event(ConnectedEv)
        async def _connected(_c, _e):
            self.connected, self.ever_connected = True, True
            self.connected_at, self.disconnected_at = time.time(), None
            if self.ledger.get_meta("epoch") is None:
                self.ledger.set_meta("epoch", time.time())
            self.ledger.event("connected")
            log.info("Connected to WhatsApp")
            self.wake.set()

        @client.event(OfflineSyncCompletedEv)
        async def _synced(_c, e):
            self.synced = True
            log.info("Offline sync completed (%d queued events delivered)", e.Count)
            self.wake.set()

        @client.event(DisconnectedEv)
        async def _disconnected(_c, _e):
            self.connected = False
            self.disconnected_at = self.disconnected_at or time.time()
            self.ledger.event("disconnected")
            log.warning("Disconnected; whatsmeow will retry with backoff")

        @client.event(KeepAliveTimeoutEv)
        async def _ka(_c, _e):
            log.warning("Keep-alive timeout (network trouble?)")

        @client.event(ConnectFailureEv)
        async def _cf(_c, e):
            log.error("Connect failure: reason=%s %s", e.Reason, e.Message)

        @client.event(PairStatusEv)
        async def _paired(_c, e):
            log.info("Pairing status: %s", e)
            self.ledger.set_meta("epoch", self.ledger.get_meta("epoch") or time.time())

        @client.event(LoggedOutEv)
        async def _logged_out(_c, e):
            self._fatal(f"WhatsApp number was LOGGED OUT (reason={e.Reason}, on_connect={e.OnConnect}). "
                        "The bot is stopped. Re-link with: python -m wabot relink")

        @client.event(TemporaryBanEv)
        async def _ban(_c, e):
            self._fatal(f"WhatsApp TEMPORARY BAN on the bot number (code={e.Code}, expires={e.Expire}). Bot stopped.")

        @client.event(StreamReplacedEv)
        async def _replaced(_c, _e):
            self._fatal("Session opened elsewhere (another bot instance?). Stopping to avoid double replies.")

        @client.event(MessageEv)
        async def _msg(_c, _e):
            self.wake.set()  # already persisted by the sync hook

        @client.event(ReceiptEv)
        async def _rcpt(_c, _e):
            pass  # persisted by the sync hook; registration subscribes us to the event

        @client.event.qr
        async def _qr(_c, data: bytes):
            path = self.cfg.data_dir / "qr.png"
            segno.make_qr(data).save(str(path), scale=8)
            segno.make_qr(data).terminal(compact=True)
            log.info("Scan the QR above with WhatsApp > Linked devices (also saved to %s)", path)

    def _fatal(self, text: str) -> None:
        self.ledger.event("fatal", text)
        send_alert(text, self.cfg.data_dir / "ALERTS.log")
        self.exit_code = EXIT_NEEDS_HUMAN
        self.wake.set()

    # ================================================================== policy
    async def _resolve_phone(self, row) -> str | None:
        if row["sender_pn"]:
            return row["sender_pn"]
        if row["sender"] and row["sender"].endswith("@lid"):
            try:
                user, server = row["sender"].split("@")
                pn = await self.client.get_pn_from_lid(build_jid(user.split(":")[0], server))
                return pn.User or None
            except Exception as exc:  # noqa: BLE001
                log.warning("could not map LID %s to a phone number: %r", row["sender"], exc)
        return None

    async def _skip_reason(self, row) -> str | None:
        if row["from_me"]:
            return "from_me"
        if row["is_group"] or row["chat"].endswith("@g.us"):
            return "group"
        if "broadcast" in row["chat"] or row["chat"].endswith("@newsletter"):
            return "broadcast"
        if row["kind"] == "system":
            return "system_message"
        epoch = self.ledger.get_meta("epoch") or 0
        if row["ts"] < epoch - 60:
            return "before_bot_was_linked"
        phone = await self._resolve_phone(row)
        if not phone:
            return "unknown_sender"   # unsure who sent it -> skip, never guess
        if not self.allowlist.allowed(phone):
            log.warning("IGNORED message %s from non-allowlisted +%s", row["msg_id"], phone)
            return "not_allowlisted"
        return None

    # ================================================================== replies
    async def _build_reply(self, row) -> dict:
        if row["kind"] == "text":
            return handle_leads(row["text"], self.cfg.leads_dirs) or {"text": default_reply(row["text"])}
        if row["kind"] == "voice":
            ev = MessageEv.FromString(row["raw"])
            audio = await self.client.download_any(ev.Message)
            transcript = await self.transcriber.transcribe(audio)
            if not transcript:
                return {"text": "I couldn't make out that voice note - could you try again?", "transcript": ""}
            cmd = handle_leads(transcript, self.cfg.leads_dirs)
            body = cmd["text"] if cmd else HELP_TEXT
            out = {"text": f"_You said:_ \"{transcript}\"\n\n{body}", "transcript": transcript}
            if cmd and cmd.get("document"):
                out.update(document=cmd["document"], filename=cmd["filename"])
            return out
        return {"text": "I can read text messages and voice notes. Try sending one of those!"}

    async def _human_pause(self, row, reply: dict) -> None:
        if not self.cfg.pace:
            return
        user, server = row["chat"].split("@")
        jid = build_jid(user, server)
        incoming = len(row["text"] or "") or 40
        await asyncio.sleep(min(6.0, random.uniform(1.0, 2.5) + incoming / 40))      # "reading"
        try:
            sender_user, sender_server = (row["sender"] or row["chat"]).split("@")
            await self.client.mark_read(row["msg_id"], chat=jid, sender=build_jid(sender_user, sender_server),
                                        receipt=ReceiptType.READ)
            await self.client.send_chat_presence(jid, ChatPresence.CHAT_PRESENCE_COMPOSING,
                                                 ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT)
        except Exception as exc:  # noqa: BLE001 - cosmetic
            log.debug("presence failed: %r", exc)
        typing_s = len(reply.get("text", "")) / random.uniform(6, 9)                  # ~40-55 wpm
        await asyncio.sleep(min(8.0, max(1.0, typing_s)))

    async def _send(self, row, reply: dict):
        user, server = row["chat"].split("@")
        jid = build_jid(user, server)
        if reply.get("document"):
            return await self.client.send_document(jid, file=reply["document"], caption=reply["text"][:1000],
                                                   filename=reply.get("filename"), mimetype="text/csv")
        return await self.client.send_message(jid, reply["text"])

    async def _wait_for_hourly_slot(self) -> None:
        while self.ledger.sent_since(3600) >= self.cfg.max_replies_per_hour:
            log.warning("Hourly reply limit (%d) reached; waiting", self.cfg.max_replies_per_hour)
            await asyncio.sleep(60)

    async def process(self, row) -> None:
        key = row["key"]
        if row["status"] == "received":
            maybe_crash("before_reply", self.ledger)
            reason = await self._skip_reason(row)
            if reason:
                self.ledger.ignore(key, reason)
                log.info("skip %s: %s", row["msg_id"], reason)
                return
            try:
                reply = await self._build_reply(row)
            except Exception as exc:  # noqa: BLE001
                n = self.ledger.bump_attempts(key, repr(exc))
                log.error("reply generation failed for %s (attempt %d): %r", row["msg_id"], n, exc)
                if n >= 3:
                    self.ledger.fail(key, f"reply generation failed: {exc!r}")
                    send_alert(f"Gave up replying to message {row['msg_id']}: {exc!r}",
                               self.cfg.data_dir / "ALERTS.log")
                else:
                    await asyncio.sleep(min(60, 2 ** n + random.random()))
                return
            self.ledger.set_ready(key, reply)
            row = self.ledger.get(key)

        if row["status"] == "ready":
            reply = json.loads(row["reply"])
            phone = await self._resolve_phone(row)
            if not self.allowlist.allowed(phone):       # checked again at send time
                self.ledger.ignore(key, "not_allowlisted_at_send_time")
                log.warning("allowlist no longer contains +%s; reply to %s dropped", phone, row["msg_id"])
                return
            if row["send_attempts"] >= self.cfg.max_send_attempts:
                self.ledger._set(key, ("ready",), status="failed", reason="too many send attempts")
                return
            await self._wait_for_hourly_slot()
            await self._human_pause(row, reply)
            if not self.allowlist.allowed(phone):       # ...and once more right before the network call
                self.ledger.ignore(key, "not_allowlisted_at_send_time")
                return
            if not self.ledger.mark_sending(key):       # durable BEFORE the network call
                return
            maybe_crash("before_send", self.ledger)
            try:
                resp = await self._send(row, reply)
            except Exception as exc:  # noqa: BLE001 - outcome unknown: resolved later via receipts
                self.ledger.event("send_error", f"{key}: {exc!r}")
                log.error("send of reply to %s failed/uncertain: %r (will reconcile)", row["msg_id"], exc)
                return
            maybe_crash("after_send", self.ledger)      # the hard window: sent, but not recorded
            self.ledger.mark_sent(key, resp.ID, row["chat"])
            log.info("replied to %s with %s", row["msg_id"], resp.ID)

    async def reconcile_stuck(self) -> None:
        """Messages left in 'sending' by a crash/kill or an uncertain send error.

        After `receipt_grace_s` of being connected, any delivery/read receipt from that peer for an
        id we never recorded proves our send went out -> mark sent. No receipt -> resend.
        """
        if not self.connected or time.time() - self.connected_at < self.cfg.receipt_grace_s:
            return
        for row in self.ledger.stuck_sending(self.cfg.receipt_grace_s):
            peers = [row["chat"], row["sender"] or "", (row["sender_pn"] or "") + "@s.whatsapp.net"]
            rid = self.ledger.unexplained_receipt(peers, row["sending_at"])
            if rid:
                self.ledger.claim_receipt(row["key"], rid, row["chat"])
                log.info("reconciled %s: receipt %s proves the reply was delivered", row["msg_id"], rid)
            else:
                self.ledger.back_to_ready(row["key"], "no delivery receipt after grace period; resending")
                log.warning("no receipt for reply to %s; it will be re-sent", row["msg_id"])

    # ================================================================== loops
    async def worker(self) -> None:
        while self.exit_code is None:
            ready = self.connected and (self.synced or time.time() - self.connected_at > 15)
            if not ready:
                await self._sleep_or_wake(2)
                continue
            await self.reconcile_stuck()
            row = self.ledger.next_actionable()
            if row is None:
                await self._sleep_or_wake(5)
                continue
            try:
                await self.process(row)
            except Exception:  # noqa: BLE001 - keep the loop alive
                log.exception("worker error on %s", row["key"])
                await asyncio.sleep(5)

    async def _sleep_or_wake(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self.wake.wait(), seconds)
        except asyncio.TimeoutError:
            pass
        self.wake.clear()

    async def watchdog(self) -> None:
        while self.exit_code is None:
            await asyncio.sleep(5)
            if self.ever_connected and not self.connected and self.disconnected_at and \
                    time.time() - self.disconnected_at > self.cfg.disconnect_exit_s:
                log.error("Disconnected for > %.0fs; exiting so the supervisor reconnects with backoff",
                          self.cfg.disconnect_exit_s)
                self.exit_code = EXIT_RECONNECT

    async def run(self) -> int:
        self.loop = asyncio.get_running_loop()
        self.cfg.data_dir.mkdir(parents=True, exist_ok=True)
        log.info("Ledger: %s  |  allowlist: %d number(s)  |  pending: %s",
                 self.cfg.ledger_db, len(self.allowlist.numbers()), self.ledger.counts())
        if not self.allowlist.numbers():
            log.warning("Allowlist is EMPTY - the bot will not reply to anyone (fill %s)", self.cfg.allowlist_file)
        self.client = NewAClient(str(self.cfg.session_db))
        self._register_events(self.client)
        self._install_sync_hooks(self.client)
        await self.client.connect()
        tasks = [asyncio.create_task(self.worker()), asyncio.create_task(self.watchdog()),
                 asyncio.create_task(self.transcriber.warm_up())]
        while self.exit_code is None:
            await self._sleep_or_wake(1)
        for t in tasks:
            t.cancel()
        try:
            await asyncio.wait_for(self.client.disconnect(), 5)
        except Exception:  # noqa: BLE001
            pass
        return self.exit_code


def run_bot() -> int:
    cfg = Config.load()
    code = asyncio.run(Bot(cfg).run())
    logging.shutdown()
    os._exit(code)  # the Go runtime holds a non-daemon thread; all state is already on disk
