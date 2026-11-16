"""Sends notifications via Telegram and/or WhatsApp (CallMeBot), and reads Telegram commands.

Each channel is enabled only if its environment variables are set:
  Telegram: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  WhatsApp: CALLMEBOT_PHONE (e.g. +15615551234), CALLMEBOT_APIKEY
Errors never print URLs, so secrets don't leak into logs.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import List


def _whatsapp(text: str) -> None:
    phone, key = os.environ["CALLMEBOT_PHONE"], os.environ["CALLMEBOT_APIKEY"]
    qs = urllib.parse.urlencode({"phone": phone, "text": text, "apikey": key})
    with urllib.request.urlopen(f"https://api.callmebot.com/whatsapp.php?{qs}", timeout=30) as r:
        body = r.read().decode("utf-8", "replace").lower()
    if "error" in body and "queued" not in body and "sent" not in body:
        raise RuntimeError("CallMeBot rejected the message (check phone/apikey)")


def _telegram_api(method: str, params: dict, timeout: int = 30) -> dict:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(
        f"https://api.telegram.org/bot{token}/{method}", data=data, timeout=timeout
    ) as r:
        return json.load(r)


def _telegram(text: str, html: bool = False) -> None:
    params = {"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text, "disable_web_page_preview": "true"}
    if html:
        params["parse_mode"] = "HTML"
    _telegram_api("sendMessage", params)


def send_and_pin(text: str) -> None:
    """Sends a message and pins it (used to persist 'paused' across runs)."""
    chat = os.environ["TELEGRAM_CHAT_ID"]
    res = _telegram_api("sendMessage", {"chat_id": chat, "text": text})
    _telegram_api(
        "pinChatMessage",
        {"chat_id": chat, "message_id": res["result"]["message_id"], "disable_notification": "true"},
    )


def unpin_all() -> None:
    _telegram_api("unpinAllChatMessages", {"chat_id": os.environ["TELEGRAM_CHAT_ID"]})


def pinned_text() -> str:
    res = _telegram_api("getChat", {"chat_id": os.environ["TELEGRAM_CHAT_ID"]})
    return (res.get("result", {}).get("pinned_message") or {}).get("text", "")


def set_commands(commands: List[tuple]) -> None:
    """Sets the bot's ☰ Menú shortcuts: [(command, description), ...]."""
    payload = [{"command": c, "description": d} for c, d in commands]
    _telegram_api("setMyCommands", {"commands": json.dumps(payload)})


def telegram_enabled() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def reply(text: str, html: bool = False) -> None:
    """Reply on Telegram only (answers to commands). html=True enables <b> etc."""
    try:
        _telegram(text, html)
    except Exception as exc:
        print(f"[reply] telegram failed: {type(exc).__name__}")


def get_messages(offset: int, timeout: int) -> List[dict]:
    """Long-polls Telegram for new messages. Returns raw update objects."""
    res = _telegram_api(
        "getUpdates",
        {"offset": offset, "timeout": timeout, "allowed_updates": '["message"]'},
        timeout=timeout + 15,
    )
    return res.get("result", [])


def channels() -> List[str]:
    out = []
    if telegram_enabled():
        out.append("telegram")
    if os.environ.get("CALLMEBOT_PHONE") and os.environ.get("CALLMEBOT_APIKEY"):
        out.append("whatsapp")
    return out


def notify(text: str) -> bool:
    """Sends to every configured channel. Returns True if at least one succeeded."""
    senders = {"whatsapp": _whatsapp, "telegram": _telegram}
    ok = False
    active = channels()
    if not active:
        print("[notify] no channels configured; message would be:\n" + text)
        return False
    for name in active:
        try:
            senders[name](text)
            print(f"[notify] sent via {name}")
            ok = True
        except Exception as exc:
            print(f"[notify] {name} failed: {type(exc).__name__}")
    return ok
