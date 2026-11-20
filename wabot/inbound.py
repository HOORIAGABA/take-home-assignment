"""Turn a neonize `MessageEv` into a plain `Inbound` record (pure, unit-testable)."""

from __future__ import annotations

from .ledger import Inbound

PN_SERVER = "s.whatsapp.net"


def jid_str(jid) -> str:
    if not jid or not jid.User:
        return ""
    return f"{jid.User}@{jid.Server}"


def sender_phone(src) -> str | None:
    """Phone number of the sender, or None if WhatsApp only gave us a LID (privacy id)."""
    if src.Sender.User and src.Sender.Server == PN_SERVER:
        return src.Sender.User.split(":")[0]
    if src.HasField("SenderAlt") and src.SenderAlt.User and src.SenderAlt.Server == PN_SERVER:
        return src.SenderAlt.User.split(":")[0]
    return None


def classify(msg) -> tuple[str, str | None]:
    """Return (kind, text) for a waE2E.Message."""
    if msg.conversation:
        return "text", msg.conversation
    if msg.HasField("extendedTextMessage") and msg.extendedTextMessage.text:
        return "text", msg.extendedTextMessage.text
    if msg.HasField("audioMessage"):
        return "voice", None
    for f in ("protocolMessage", "reactionMessage", "senderKeyDistributionMessage", "pollUpdateMessage",
              "keepInChatMessage", "editedMessage"):
        if msg.HasField(f):
            return "system", None
    for f, label in (("imageMessage", "image"), ("videoMessage", "video"), ("documentMessage", "document"),
                     ("stickerMessage", "sticker"), ("locationMessage", "location"), ("contactMessage", "contact")):
        if msg.HasField(f):
            cap = getattr(getattr(msg, f), "caption", None) if f in ("imageMessage", "videoMessage", "documentMessage") else None
            return "other", cap or f"[{label}]"
    return "system", None


def normalize_ts(ts: int | float) -> float:
    ts = float(ts or 0)
    if ts > 1e14:   # microseconds
        return ts / 1e6
    if ts > 1e11:   # milliseconds
        return ts / 1e3
    return ts


def to_inbound(ev, raw: bytes | None = None) -> Inbound:
    info = ev.Info
    src = info.MessageSource
    chat = jid_str(src.Chat)
    kind, text = classify(ev.Message)
    return Inbound(
        key=f"{chat}|{info.ID}",
        msg_id=info.ID,
        chat=chat,
        sender=jid_str(src.Sender),
        sender_pn=sender_phone(src),
        from_me=bool(src.IsFromMe),
        is_group=bool(src.IsGroup) or chat.endswith("@g.us"),
        kind=kind,
        text=text,
        ts=normalize_ts(info.Timestamp),
        push_name=info.Pushname or None,
        raw=raw,
    )
