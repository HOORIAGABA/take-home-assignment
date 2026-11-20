"""Exactly-once ledger + reply worker, with a fake WhatsApp client (no network)."""

import asyncio
import json
import time
from types import SimpleNamespace

from wabot.allowlist import Allowlist
from wabot.bot import Bot
from wabot.commands import handle_leads
from wabot.config import Config
from wabot.ledger import Inbound, Ledger

TESTER = "923001112222"


def msg(i: int, text="hello", from_me=False, pn=TESTER, chat=None, kind="text", ts=None):
    chat = chat or f"{pn}@s.whatsapp.net"
    return Inbound(key=f"{chat}|M{i}", msg_id=f"M{i}", chat=chat, sender=chat, sender_pn=pn, from_me=from_me,
                   is_group=False, kind=kind, text=text, ts=ts or time.time())


# ------------------------------------------------------------------ ledger
def test_redelivery_is_ignored(tmp_path):
    led = Ledger(tmp_path / "l.db")
    assert led.record_inbound(msg(1)) is True
    assert led.record_inbound(msg(1)) is False
    assert led.counts() == {"received": 1}


def test_state_machine_compare_and_set(tmp_path):
    led = Ledger(tmp_path / "l.db")
    led.record_inbound(msg(1))
    k = msg(1).key
    assert led.set_ready(k, {"text": "hi"})
    assert not led.set_ready(k, {"text": "again"})         # can't regenerate once ready
    assert led.mark_sending(k)
    assert not led.mark_sending(k)                          # second sender loses the race
    assert led.mark_sent(k, "OUT1", msg(1).chat)
    assert not led.ignore(k, "late")                        # terminal


def test_crash_after_send_is_reconciled_by_receipt(tmp_path):
    led = Ledger(tmp_path / "l.db")
    m = msg(1)
    led.record_inbound(m)
    led.set_ready(m.key, {"text": "hi"})
    led.mark_sending(m.key)
    # process killed here: the message went out but mark_sent never ran.
    sending_at = led.get(m.key)["sending_at"]
    led.record_receipt([m.chat], ["OUT-UNKNOWN"], 1, sending_at + 1)   # delivered receipt arrives on reconnect
    assert led.unexplained_receipt([m.chat], sending_at) == "OUT-UNKNOWN"
    assert led.claim_receipt(m.key, "OUT-UNKNOWN", m.chat)
    assert led.get(m.key)["status"] == "sent"
    assert led.unexplained_receipt([m.chat], sending_at) is None        # can't be claimed twice


def test_receipts_for_known_or_old_messages_are_not_proof(tmp_path):
    led = Ledger(tmp_path / "l.db")
    m = msg(1)
    led.record_inbound(m)
    led.set_ready(m.key, {"text": "hi"})
    led.mark_sending(m.key)
    t = led.get(m.key)["sending_at"]
    led.record_receipt([m.chat], ["OLD"], 1, t - 100)                   # before we started sending
    led.record_inbound(msg(2))
    led.record_receipt([m.chat], ["M2"], 4, t + 1)                      # id of an inbound message
    assert led.unexplained_receipt([m.chat], t) is None


# ------------------------------------------------------------------ worker
class FakeWA:
    def __init__(self, fail_send=False):
        self.sent: list[tuple] = []
        self.fail_send = fail_send

    async def send_message(self, jid, text):
        if self.fail_send:
            raise RuntimeError("network down")
        self.sent.append(("text", jid.User, text))
        return SimpleNamespace(ID=f"OUT{len(self.sent)}")

    async def send_document(self, jid, file, caption=None, filename=None, mimetype=None):
        self.sent.append(("doc", jid.User, caption, filename))
        return SimpleNamespace(ID=f"OUT{len(self.sent)}")

    async def get_pn_from_lid(self, jid):
        return SimpleNamespace(User="")


def make_bot(tmp_path, monkeypatch, allow=(TESTER,)):
    monkeypatch.delenv("ALLOWLIST", raising=False)
    (tmp_path / "allow.txt").write_text("\n".join(allow) + "\n# comment\n", "utf-8")
    cfg = Config(
        data_dir=tmp_path, session_db=tmp_path / "s.db", ledger_db=tmp_path / "l.db",
        allowlist_file=tmp_path / "allow.txt", leads_dirs=[tmp_path / "leads"],
        stt_model="tiny", stt_model_dir=tmp_path / "models", stt_device="cpu",
        stt_compute_type="int8", stt_language=None,
        max_replies_per_hour=100, receipt_grace_s=0, disconnect_exit_s=60, pace=False, max_send_attempts=3,
    )
    bot = Bot(cfg)
    bot.client = FakeWA()
    bot.connected, bot.connected_at = True, time.time() - 100
    bot.ledger.set_meta("epoch", time.time() - 3600)
    return bot


async def drain(bot):
    while (row := bot.ledger.next_actionable()) is not None:
        await bot.process(row)


def test_replies_once_and_skips_own_and_strangers(tmp_path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)
    bot.ledger.record_inbound(msg(1, "hi"))
    bot.ledger.record_inbound(msg(1, "hi"))                    # re-delivered
    bot.ledger.record_inbound(msg(2, "note to self", from_me=True))
    bot.ledger.record_inbound(msg(3, "spam", pn="923339999999"))
    asyncio.run(drain(bot))
    assert len(bot.client.sent) == 1 and bot.client.sent[0][1] == TESTER
    reasons = {r["msg_id"]: r["reason"] for r in bot.ledger.db.execute("SELECT msg_id, reason FROM inbound")}
    assert reasons["M2"] == "from_me" and reasons["M3"] == "not_allowlisted"
    asyncio.run(drain(bot))                                     # "restart": nothing left to do
    assert len(bot.client.sent) == 1


def test_allowlist_rechecked_at_send_time(tmp_path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)
    bot.ledger.record_inbound(msg(1))
    row = bot.ledger.next_actionable()
    bot.ledger.set_ready(row["key"], {"text": "prepared earlier"})
    (tmp_path / "allow.txt").write_text("", "utf-8")            # number removed before send
    asyncio.run(drain(bot))
    assert bot.client.sent == []
    assert bot.ledger.get(row["key"])["reason"] == "not_allowlisted_at_send_time"


def test_uncertain_send_is_resent_only_without_receipt(tmp_path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)
    bot.client = FakeWA(fail_send=True)
    bot.ledger.record_inbound(msg(1))
    asyncio.run(drain(bot))
    key = msg(1).key
    assert bot.ledger.get(key)["status"] == "sending"           # outcome unknown
    bot.client = FakeWA()
    asyncio.run(bot.reconcile_stuck())                          # no receipt -> back to ready
    assert bot.ledger.get(key)["status"] == "ready"
    asyncio.run(drain(bot))
    assert len(bot.client.sent) == 1 and bot.ledger.get(key)["status"] == "sent"


def test_old_history_and_groups_ignored(tmp_path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)
    bot.ledger.record_inbound(msg(1, ts=time.time() - 10 * 86400))
    g = msg(2, chat="1203630@g.us")
    g.is_group = True
    bot.ledger.record_inbound(g)
    asyncio.run(drain(bot))
    assert bot.client.sent == []


# ------------------------------------------------------------------ leads command
def write_dataset(root):
    d = root / "leads" / "dental-clinic-lahore-pakistan"
    d.mkdir(parents=True)
    counts = {k: 1 for k in ("final_businesses", "with_phone", "with_website", "with_email", "total_emails",
                             "with_instagram", "with_facebook", "with_whatsapp")}
    (d / "report.json").write_text(json.dumps({"category": "dental clinic", "city": "Lahore, Pakistan",
                                               "finished_at": "2026-11-18T10:00:00", "counts": counts}), "utf-8")
    (d / "businesses.csv").write_text("cid,name\n1,Smile\n", "utf-8")
    (d / "businesses.json").write_text(json.dumps([{"name": "Smile", "rating": 4.9, "review_count": 40}]), "utf-8")


def test_leads_command(tmp_path, monkeypatch):
    write_dataset(tmp_path)
    r = handle_leads("leads dental clinic Lahore", [tmp_path / "leads"])
    assert r["document"].endswith("businesses.csv") and r["filename"] == "dental-clinic-lahore.csv"
    assert "Smile - 4.9 (40)" in r["text"]
    assert handle_leads("hello", [tmp_path]) is None
    # how Whisper actually transcribes the spoken command (measured): "Leeds Dental Clinic Lahore"
    assert handle_leads("Leeds Dental Clinic Lahore.", [tmp_path / "leads"])["filename"] == "dental-clinic-lahore.csv"
    assert "don't have data" in handle_leads("leads plumber Karachi", [tmp_path / "leads"])["text"]

    bot = make_bot(tmp_path, monkeypatch)
    bot.ledger.record_inbound(msg(1, "Leads dental clinic Lahore"))
    asyncio.run(drain(bot))
    assert bot.client.sent[0][0] == "doc"


def test_allowlist_normalises(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLOWLIST", "+92 300 111 2222")
    a = Allowlist(tmp_path / "missing.txt")
    assert a.allowed("923001112222") and not a.allowed(None) and not a.allowed("923001112223")


def test_crash_hook_is_one_shot(tmp_path, monkeypatch):
    import wabot.bot as botmod
    led = Ledger(tmp_path / "l.db")
    exits = []
    monkeypatch.setattr(botmod.os, "_exit", lambda code: exits.append(code))
    monkeypatch.setenv("WABOT_CRASH_AT", "after_send")
    botmod.maybe_crash("before_send", led)
    botmod.maybe_crash("after_send", led)
    botmod.maybe_crash("after_send", led)
    assert exits == [9]


def test_plain_text_gets_fixed_reply(tmp_path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)
    bot.ledger.record_inbound(msg(1, "hello there"))
    asyncio.run(drain(bot))
    kind, user, text = bot.client.sent[0]
    assert kind == "text" and 'Got your message: "hello there"' in text and "leads <category> <city>" in text
