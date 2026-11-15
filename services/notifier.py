"""Sends notifications via WhatsApp (CallMeBot) and/or Telegram.

Each channel is enabled only if its environment variables are set:
  WhatsApp: CALLMEBOT_PHONE (e.g. +15615551234), CALLMEBOT_APIKEY
  Telegram: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
Errors never print URLs, so secrets don't leak into logs.
"""
from __future__ import annotations

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


def _telegram(text: str) -> None:
    token, chat = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
    data = urllib.parse.urlencode(
        {"chat_id": chat, "text": text, "disable_web_page_preview": "true"}
    ).encode()
    urllib.request.urlopen(
        f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=30
    ).read()


def channels() -> List[str]:
    out = []
    if os.environ.get("CALLMEBOT_PHONE") and os.environ.get("CALLMEBOT_APIKEY"):
        out.append("whatsapp")
    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        out.append("telegram")
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
