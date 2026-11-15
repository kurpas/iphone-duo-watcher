"""iPhone pickup watcher + Telegram bot.

  python main.py --once [duo|18]   # single check, print result
  python main.py --duration 330    # watch loop for 330 min, answering Telegram commands
  python main.py --test-notify     # send a test message to configured channels

Telegram commands: "busca duo", "busca 18", "ayuda"
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import time
from html import escape
from collections import OrderedDict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Dict, List, Optional

from services import apple_checker, notifier

CONFIG = json.loads((Path(__file__).parent / "config.json").read_text())
PRODUCTS: Dict[str, dict] = CONFIG["products"]
NAME_RE = re.compile(r"^(?P<model>.*?) (?P<mem>\d+(?:GB|TB)) (?P<color>.+)$")
MAX_MSG_AGE = 15 * 60  # ignore Telegram messages older than this (e.g. left over from long ago)


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def split_name(name: str) -> str:
    m = NAME_RE.match(name)
    return f"{m['model']} · {m['color']} · {m['mem']}" if m else name


def area() -> str:
    return f"tiendas a ≤{CONFIG['max_miles']} mi de {CONFIG['origin']['name']}"


def watched_parts() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for p in PRODUCTS.values():
        if p.get("watch"):
            out.update(p["parts"])
    return out


def buy_url(part: str) -> str:
    """Direct link to the model with color/storage preselected (unlocked).
    On iPhone it opens straight in the Apple Store app."""
    for p in PRODUCTS.values():
        if part in p["parts"]:
            slug = p.get("buy_slugs", {}).get(part)
            return f"{p['url']}/{slug}" if slug else p["url"]
    return ""


def format_found(items: List[apple_checker.Availability], parts: Dict[str, str]) -> str:
    """Groups availability by model; nearest stores + direct buy link per model."""
    n = CONFIG["stores_per_model_in_message"]
    by_part: "OrderedDict[str, list]" = OrderedDict((p, []) for p in parts)
    for a in items:
        by_part.setdefault(a.part, []).append(a)

    lines = ["✅ DISPONIBLE para pickup:"]
    for part, stores in by_part.items():
        if not stores:
            continue
        stores.sort(key=lambda a: a.miles)
        shown = ", ".join(f"{a.store_name} ({a.miles:.0f} mi)" for a in stores[:n])
        more = f" y {len(stores) - n} más" if len(stores) > n else ""
        lines.append(f"\n• {split_name(parts.get(part, part))}: {shown}{more}")
        lines.append(f"  👉 {buy_url(part)}")
    missing = [split_name(parts[p]) for p, s in by_part.items() if not s and p in parts]
    if missing:
        lines.append("\n❌ Sin stock:")
        lines += [f"• {m}" for m in missing]
    return "\n".join(lines)


def format_headline(items: List[apple_checker.Availability], parts: Dict[str, str]) -> str:
    """Short first message: what shows on the phone's lock screen. Nearest store wins."""
    a = min(items, key=lambda x: x.miles)
    quote = f" · {a.quote}" if a.quote else ""
    return (
        f"🚨 HAY STOCK: {split_name(parts.get(a.part, a.part))}\n"
        f"📍 {a.store_name} ({a.miles:.0f} mi){quote}\n"
        f"👉 {buy_url(a.part)}\n"
        f"Add to Bag → Check Out → pickup en {a.store_name} → tarjeta guardada."
    )


def send_alert(items: List[apple_checker.Availability], parts: Dict[str, str], prefix: str = "") -> bool:
    """Two messages: actionable headline first, full detail after."""
    ok = notifier.notify(prefix + format_headline(items, parts))
    if ok:
        notifier.notify(format_found(items, parts))
    return ok


# ---------- Telegram commands ----------

def is_night() -> bool:
    n = CONFIG.get("night")
    if not n:
        return False
    h = datetime.now(ZoneInfo(n["timezone"])).hour
    return n["start_hour"] <= h < n["end_hour"]


def current_interval(day_interval: int) -> int:
    return CONFIG["night"]["interval_seconds"] if is_night() else day_interval


def help_text(html: bool = True) -> str:
    """Menu. With html=True the command names are bold (Telegram HTML)."""
    def cmd(name: str) -> str:
        return f"<b>{name.upper()}</b>" if html else name.upper()

    esc = escape if html else (lambda x: x)
    watched = ", ".join(p["label"] for p in PRODUCTS.values() if p.get("watch"))
    lines = [cmd("📋 Menú"), ""]
    for key, p in PRODUCTS.items():
        lines.append(f"• {cmd('busca ' + key)} → busca ahora {esc(p['label'])}")
    lines += [
        f"• {cmd('prueba compra')} → simula un aviso para practicar la compra (no compra nada)",
        f"• {cmd('estado')} → muestra si la búsqueda está andando y el último chequeo",
        f"• {cmd('pausa')} → frena la búsqueda automática (los comandos siguen andando)",
        f"• {cmd('reanuda')} → retoma la búsqueda automática",
        f"• {cmd('menu')} → muestra este menú",
        "(también podés tocarlos desde el botón ☰ Menú; mayúsculas o minúsculas da igual)",
        "",
        f"🤖 Automático: busco {esc(watched)} cada {CONFIG['interval_seconds']} s y te aviso solo si hay stock.",
        f"🌙 De {CONFIG['night']['start_hour']} a {CONFIG['night']['end_hour']} h (Argentina): cada {CONFIG['night']['interval_seconds'] // 60} min.",
        f"📍 Solo pickup en {esc(area())}.",
        f"☀️ Todos los días a las {CONFIG['heartbeat_hour']} h (Argentina) te mando un \"sigo vivo\".",
    ]
    return "\n".join(lines)


# ---------- status / pause / daily heartbeat ----------

PAUSE_MARK = "⏸"
STATE = {
    "run_started": time.time(),
    "checks": 0,
    "last_check": None,
    "last_stores": 0,
    "last_available": 0,
    "errors_in_row": 0,
    "paused": False,
    "heartbeat_date": None,
}


def ago(ts: Optional[float]) -> str:
    if not ts:
        return "nunca"
    s = int(time.time() - ts)
    if s < 90:
        return f"hace {s} s"
    m = s // 60
    return f"hace {m} min" if m < 90 else f"hace {m // 60} h {m % 60} min"


def status_text() -> str:
    watched = ", ".join(p["label"] for p in PRODUCTS.values() if p.get("watch"))
    if STATE["paused"]:
        head = f"{PAUSE_MARK} Búsqueda automática EN PAUSA (escribí reanuda para retomarla)"
    else:
        every = current_interval(CONFIG["interval_seconds"])
        night = " 🌙 modo noche" if is_night() else ""
        head = f"▶️ Buscando {watched} cada {every} s{night}"
    lines = ["📊 ESTADO", head]
    lines.append(f"⏱ Tanda actual: arrancó {ago(STATE['run_started'])} · {STATE['checks']} chequeos")
    if STATE["last_check"]:
        result = (
            f"✅ {STATE['last_available']} disponibles" if STATE["last_available"] else "sin stock"
        )
        lines.append(f"🕐 Último chequeo: {ago(STATE['last_check'])} · {STATE['last_stores']} tiendas · {result}")
    if STATE["errors_in_row"]:
        lines.append(f"⚠️ Apple no respondió en los últimos {STATE['errors_in_row']} chequeos")
    return "\n".join(lines)


def set_paused(paused: bool) -> None:
    if paused == STATE["paused"]:
        notifier.reply("Ya está en pausa." if paused else "Ya está buscando.")
        return
    STATE["paused"] = paused
    if paused:
        notifier.send_and_pin(
            f"{PAUSE_MARK} Búsqueda automática EN PAUSA. Escribí reanuda para retomarla.\n"
            "(busca duo / busca 18 siguen andando)"
        )
    else:
        notifier.unpin_all()
        notifier.reply(f"▶️ Reanudada. Vuelvo a buscar cada {CONFIG['interval_seconds']} s.")


def load_paused() -> None:
    """Pause survives restarts: it's stored as the pinned message in the chat."""
    if not notifier.telegram_enabled():
        return
    try:
        STATE["paused"] = notifier.pinned_text().startswith(PAUSE_MARK)
    except Exception as exc:
        print(f"{now()} [telegram] could not read pinned message: {type(exc).__name__}")
    print(f"{now()} paused={STATE['paused']}")


def maybe_heartbeat() -> None:
    """One 'still alive' message per day, in the first minutes of heartbeat_hour."""
    local = datetime.now(ZoneInfo(CONFIG["night"]["timezone"]))
    today = local.date().isoformat()
    if local.hour == CONFIG["heartbeat_hour"] and local.minute < 10 and STATE["heartbeat_date"] != today:
        STATE["heartbeat_date"] = today
        notifier.notify("☀️ Sigo vivo.\n\n" + status_text())


def find_product(arg: str) -> Optional[str]:
    arg = arg.strip().lower()
    for key, p in PRODUCTS.items():
        if arg == key or arg in p.get("aliases", []):
            return key
    return None


def on_demand_search(key: str) -> None:
    p = PRODUCTS[key]
    parts = p["parts"]
    notifier.reply(
        f"🔎 Buscando {p['label']} para pickup en {area()}:\n"
        + "\n".join(f"• {split_name(n)}" for n in parts.values())
    )
    items, checked, errors = apple_checker.check(CONFIG, parts)
    if checked == 0:
        notifier.reply("⚠️ No pude consultar a Apple ahora. Probá de nuevo en un rato.")
        return
    if items:
        notifier.reply(format_found(items, parts) + f"\n\n(revisé {checked} tiendas)")
    else:
        notifier.reply(f"❌ No hay stock de ninguno en las {checked} tiendas.")


def practice_alert() -> None:
    """Sends a simulated stock alert using a model that's usually in stock (18 Pro)."""
    parts = PRODUCTS["18"]["parts"]
    items, checked, _ = apple_checker.check(CONFIG, parts)
    if not items:
        notifier.reply("No hay ningún 18 Pro en stock ahora para simular. Probá más tarde.")
        return
    send_alert(items, parts, prefix="🧪 PRUEBA (no es el Duo, NO pagues)\n")
    notifier.reply(
        "🧪 Practicá: tocá el link del primer mensaje, elegí la tienda para pickup y avanzá "
        "hasta la pantalla final de pago (Place Order) SIN confirmar. Fijate cuánto tardaste."
    )


def handle_command(text: str) -> None:
    t = text.strip().lower().replace("_", " ").split("@")[0]
    if re.match(r"^/?prueba\s*compra", t):
        practice_alert()
        return
    if re.match(r"^/?estado\b", t):
        notifier.reply(status_text())
        return
    if re.match(r"^/?pausa(r)?\b", t):
        set_paused(True)
        return
    if re.match(r"^/?(reanuda(r)?|segui|seguí)\b", t):
        set_paused(False)
        return
    m = re.match(r"^/?busca(?:r)?\b\s*(.*)$", t)
    if m:
        key = find_product(m.group(1))
        if key:
            on_demand_search(key)
        else:
            notifier.reply("No conozco ese modelo 🤔 Escribí menu para ver qué puedo buscar.")
    elif re.match(r"^/?(menu|menú|info|ayuda|help|start|comandos)\b", t):
        notifier.reply(help_text(), html=True)
    else:
        notifier.reply("No entendí 🤔 Escribí menu para ver los comandos.")


class CommandListener:
    """Long-polls Telegram and runs commands from the configured chat only."""

    def __init__(self) -> None:
        self.offset = 0
        self.chat_id = str(os.environ.get("TELEGRAM_CHAT_ID", ""))

    def wait(self, seconds: float) -> None:
        """Waits `seconds`, answering any commands that arrive meanwhile."""
        end = time.time() + seconds
        if not notifier.telegram_enabled():
            time.sleep(max(0, seconds))
            return
        while (remaining := end - time.time()) > 1:
            try:
                updates = notifier.get_messages(self.offset, timeout=int(min(30, remaining)))
            except Exception as exc:
                print(f"{now()} [telegram] poll failed: {type(exc).__name__}")
                time.sleep(min(10, max(0, end - time.time())))
                continue
            for u in updates:
                self.offset = u["update_id"] + 1
                msg = u.get("message") or {}
                if str(msg.get("chat", {}).get("id")) != self.chat_id:
                    continue  # ignore strangers
                if time.time() - msg.get("date", 0) > MAX_MSG_AGE or not msg.get("text"):
                    continue
                print(f"{now()} [telegram] command: {msg['text'][:40]!r}")
                try:
                    handle_command(msg["text"])
                except Exception as exc:
                    print(f"{now()} [telegram] command failed: {type(exc).__name__}: {exc}")
                    notifier.reply("⚠️ Hubo un error procesando el pedido.")


# ---------- watch loop ----------

def run(duration_min: float, interval: int) -> None:
    deadline = time.time() + duration_min * 60
    parts = watched_parts()
    listener = CommandListener()
    notified = set()  # (store, part) currently known as available
    consecutive_errors = 0
    error_alert_sent = False
    STATE["run_started"] = time.time()
    load_paused()

    while True:
        maybe_heartbeat()
        if STATE["paused"]:
            if time.time() + current_interval(interval) > deadline:
                break
            listener.wait(current_interval(interval))
            continue

        items, checked, errors = apple_checker.check(CONFIG, parts)
        STATE["checks"] += 1
        STATE["last_check"] = time.time()
        STATE["last_stores"] = checked
        STATE["last_available"] = len(items)
        for e in errors:
            print(f"{now()} [error] {e}")
        if errors and checked == 0:
            consecutive_errors += 1
            if consecutive_errors >= CONFIG["alert_after_consecutive_errors"] and not error_alert_sent:
                notifier.notify(
                    "⚠️ Watcher: Apple no responde hace "
                    f"{consecutive_errors} chequeos seguidos. Último error: {errors[-1][:200]}"
                )
                error_alert_sent = True
        else:
            if error_alert_sent:
                notifier.notify("Watcher: volvió a funcionar.")
            consecutive_errors, error_alert_sent = 0, False
        STATE["errors_in_row"] = consecutive_errors

        current = {a.key for a in items}
        new_items = [a for a in items if a.key not in notified]
        print(f"{now()} checked {checked} stores, available: {len(items)}, new: {len(new_items)}")
        if new_items and send_alert(items, parts):
            notified |= {a.key for a in new_items}
        notified &= current  # forget sold-out items so a restock notifies again

        if time.time() + current_interval(interval) > deadline:
            break
        listener.wait(current_interval(interval) * random.uniform(0.85, 1.15))  # irregular, ±15%


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--once", nargs="?", const="watched", metavar="PRODUCT")
    p.add_argument("--duration", type=float, default=0, help="minutes to keep looping")
    p.add_argument("--interval", type=int, default=CONFIG["interval_seconds"])
    p.add_argument("--test-notify", action="store_true")
    args = p.parse_args()

    if args.test_notify:
        items, checked, errors = apple_checker.check(CONFIG, watched_parts())
        msg = f"Watcher: prueba OK. Revisé {checked} {area()}; disponibles ahora: {len(items)}."
        if errors:
            msg += f" Errores: {len(errors)}"
        msg += "\n\n" + help_text(html=False)
        raise SystemExit(0 if notifier.notify(msg) else 1)

    if args.once:
        key = find_product(args.once) if args.once != "watched" else None
        parts = PRODUCTS[key]["parts"] if key else watched_parts()
        items, checked, errors = apple_checker.check(CONFIG, parts)
        for e in errors:
            print(f"[error] {e}")
        print(f"Checked {checked} stores. Available: {len(items)}")
        if items:
            print(format_found(items, parts))
        return

    run(args.duration or 60, args.interval)


if __name__ == "__main__":
    main()
