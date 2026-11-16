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


def part_mem(name: str) -> str:
    m = NAME_RE.match(name)
    return m["mem"] if m else ""


def mems_of(key: str) -> List[str]:
    """Storage options a product has, in config order (e.g. ['256GB', '512GB'])."""
    out: List[str] = []
    for name in PRODUCTS[key]["parts"].values():
        mem = part_mem(name)
        if mem and mem not in out:
            out.append(mem)
    return out


def watched_keys() -> List[str]:
    return [k for k, p in PRODUCTS.items() if p.get("watch")]


# Storage the automatic watch looks for, per product. Changed from Telegram
# ("activar busqueda duo 512") and persisted in the pinned chat message.
STORAGE: Dict[str, List[str]] = {
    k: [m for m in PRODUCTS[k].get("watch_storage", mems_of(k)) if m in mems_of(k)] or mems_of(k)
    for k in watched_keys()
}


def watched_parts() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k in watched_keys():
        out.update({part: n for part, n in PRODUCTS[k]["parts"].items() if part_mem(n) in STORAGE[k]})
    return out


MIN_MILES, MAX_MILES = 5, 120  # Apple only returns stores up to ~120 mi from the search ZIP


def mem_num(mem: str) -> str:
    return mem.replace("GB", "").replace("TB", "")


def watch_desc() -> str:
    """e.g. 'iPhone Duo 256GB' or 'iPhone Duo 256GB y 512GB'."""
    return ", ".join(f"{PRODUCTS[k]['label']} {' y '.join(STORAGE[k])}" for k in watched_keys())


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
    lines = [
        f"🚨 HAY STOCK: {split_name(parts.get(a.part, a.part))}",
        f"📍 {a.store_name} ({a.miles:.0f} mi){quote}",
        f"👉 {buy_url(a.part)}",
        f"Add to Bag → Check Out → pickup en {a.store_name} → tarjeta guardada.",
    ]

    # Plan B: same model in other stores (go back ONE step, don't start over).
    same = sorted((x for x in items if x.part == a.part and x is not a), key=lambda x: x.miles)
    plan_b = "\n🔁 Si da error al pagar: NO empieces de nuevo. Volvé un paso atrás y probá otro horario"
    if same:
        plan_b += " u otra tienda: " + ", ".join(f"{x.store_name} ({x.miles:.0f} mi)" for x in same[:3])
    lines.append(plan_b + ".")

    # Other variants (color / storage) available right now, nearest store each.
    nearest: Dict[str, apple_checker.Availability] = {}
    for x in sorted(items, key=lambda x: x.miles):
        if x.part != a.part:
            nearest.setdefault(x.part, x)
    if nearest:
        lines.append("\n➕ También hay:")
        for x in nearest.values():
            lines.append(f"• {split_name(parts.get(x.part, x.part))} en {x.store_name} ({x.miles:.0f} mi)")
            lines.append(f"  👉 {buy_url(x.part)}")
    return "\n".join(lines)


def fmt_duration(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s} s"
    m, s = divmod(s, 60)
    return f"{m} min {s} s" if m < 60 else f"{m // 60} h {m % 60} min"


def format_sold_out(gone: List[tuple], parts: Dict[str, str]) -> str:
    """gone = [(Availability, first_seen_ts)]. Duration is ± the check interval."""
    local = datetime.now(ZoneInfo(CONFIG["night"]["timezone"])).strftime("%H:%M")
    lines = [f"⌛ Se agotó ({local} AR):"]
    for a, since in sorted(gone, key=lambda g: g[0].miles):
        lines.append(
            f"• {split_name(parts.get(a.part, a.part))} en {a.store_name}: duró ~{fmt_duration(time.time() - since)}"
        )
    return "\n".join(lines)


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
    watched = watch_desc()
    lines = [cmd("📋 Menú"), ""]
    for key, p in PRODUCTS.items():
        lines.append(f"• {cmd('busca ' + key)} → busca ahora {esc(p['label'])}")
    for key in watched_keys():
        label = esc(PRODUCTS[key]["label"])
        for mem in mems_of(key):
            lines.append(f"• {cmd('set memoria ' + mem_num(mem))} → la automática busca solo {label} {mem}")
        lines.append(f"• {cmd('set memoria todos')} → la automática busca {label} de todas las memorias")
    lines.append(
        f"• {cmd('set millas 100')} → descarta tiendas a más de 100 mi (o el número que pongas, "
        f"{MIN_MILES}–{MAX_MILES}); vale para todas las búsquedas"
    )
    lines.append(f"• {cmd('set')} → muestra la memoria y las millas configuradas")
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
    "blocked_since": None,
    "next_try": None,
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
    watched = watch_desc()
    if STATE["paused"]:
        head = f"{PAUSE_MARK} Búsqueda automática EN PAUSA (escribí reanuda para retomarla)"
    else:
        every = current_interval(CONFIG["interval_seconds"])
        night = " 🌙 modo noche" if is_night() else ""
        head = f"▶️ Buscando {watched} cada {every} s{night}"
    lines = ["📊 ESTADO", head, f"📍 {area()} (en línea recta)"]
    lines.append(f"⏱ Tanda actual: arrancó {ago(STATE['run_started'])} · {STATE['checks']} chequeos")
    if STATE["last_check"]:
        if not STATE["last_stores"]:
            detail = "sin respuesta de Apple"
        elif STATE["last_available"]:
            detail = f"{STATE['last_stores']} tiendas · ✅ {STATE['last_available']} disponibles"
        else:
            detail = f"{STATE['last_stores']} tiendas · sin stock"
        lines.append(f"🕐 Último chequeo: {ago(STATE['last_check'])} · {detail}")
    if STATE["blocked_since"]:
        nxt = max(0, int((STATE["next_try"] or time.time()) - time.time()) // 60)
        lines.append(
            f"🚫 Apple nos bloquea desde {ago(STATE['blocked_since'])}; "
            f"no insisto, próximo intento en ~{nxt} min"
        )
    return "\n".join(lines)


PAUSED_LINE = "Automática: EN PAUSA"
MEM_PREFIX = "📱 Memoria "
MILES_PREFIX = "📍 Distancia máx:"


def save_settings(header: str) -> None:
    """Settings survive restarts: they're stored in the chat's pinned message.
    The message doubles as the confirmation the user sees."""
    lines = [header, ""]
    if STATE["paused"]:
        lines.append(f"{PAUSE_MARK} {PAUSED_LINE} (escribí reanuda para retomarla)")
    else:
        lines.append(f"▶️ Automática: activa, cada {CONFIG['interval_seconds']} s")
    for k in watched_keys():
        lines.append(f"{MEM_PREFIX}{PRODUCTS[k]['label']}: {' y '.join(STORAGE[k])}")
    lines.append(f"{MILES_PREFIX} {CONFIG['max_miles']} mi de {CONFIG['origin']['name']} (en línea recta)")
    lines.append("📌 (este mensaje fijado guarda la configuración, no lo desfijes)")
    try:
        notifier.unpin_all()
        notifier.send_and_pin("\n".join(lines))
    except Exception as exc:
        print(f"{now()} [telegram] could not save settings: {type(exc).__name__}")
        notifier.reply("\n".join(lines[:-1]) + "\n⚠️ No pude fijar el mensaje: si se reinicia, vuelve a lo de antes.")


def set_paused(paused: bool) -> None:
    if paused == STATE["paused"]:
        notifier.reply("Ya está en pausa." if paused else "Ya está buscando.")
        return
    STATE["paused"] = paused
    if paused:
        save_settings(f"{PAUSE_MARK} Pausé la búsqueda automática. (busca duo / busca 18 siguen andando)")
    else:
        save_settings(f"▶️ Reanudada. Vuelvo a buscar {watch_desc()}.")


def set_storage(arg: str) -> None:
    """'duo 256' / 'duo 512' / 'duo todos' -> which storage the automatic watch looks for."""
    words = arg.split()
    if len(words) < 2:
        notifier.reply("Decime la memoria, por ejemplo: set memoria 256 (o 512, o todos).")
        return
    key = find_product(" ".join(words[:-1]))
    if not key:
        notifier.reply("No conozco ese modelo 🤔 Escribí menu para ver los comandos.")
        return
    if key not in watched_keys():
        notifier.reply(f"La búsqueda automática es solo para {', '.join(PRODUCTS[k]['label'] for k in watched_keys())}.")
        return
    opts = mems_of(key)
    choice = words[-1].upper()
    if choice in ("TODOS", "TODAS", "AMBOS", "AMBAS", "ALL"):
        mems = opts
    else:
        mems = [m for m in opts if m in (choice, choice + "GB", choice + "TB")]
    if not mems:
        nums = " / ".join(m.replace("GB", "").replace("TB", "") for m in opts)
        notifier.reply(f"Esa memoria no existe para {PRODUCTS[key]['label']}. Opciones: {nums} / todos.")
        return
    if mems == STORAGE[key]:
        notifier.reply(f"Ya estaba así: la automática busca {watch_desc()}.")
        return
    STORAGE[key] = mems
    save_settings(f"✅ Listo. Ahora busco {watch_desc()} en {area()} (en línea recta).")


def set_miles(arg: str) -> None:
    m = re.fullmatch(r"(\d+)\s*(mi|millas)?", arg.strip())
    if not m:
        notifier.reply(f"Poné un número de millas, por ejemplo: set millas 100 ({MIN_MILES}–{MAX_MILES}).")
        return
    miles = int(m.group(1))
    if miles > MAX_MILES:
        notifier.reply(
            f"Máximo {MAX_MILES} mi: Apple no devuelve tiendas más lejos desde la zona de "
            f"{CONFIG['origin']['name']}, así que no sumaría ninguna. Probá con set millas {MAX_MILES}."
        )
        return
    if miles < MIN_MILES:
        notifier.reply(f"Mínimo {MIN_MILES} mi. Probá con set millas {MIN_MILES} o más.")
        return
    if miles == CONFIG["max_miles"]:
        notifier.reply(f"Ya estaba así: {area()}.")
        return
    CONFIG["max_miles"] = miles
    header = f"✅ Listo. Ahora busco {watch_desc()} en {area()} (en línea recta)."
    stores = apple_checker.LAST_STORES
    if stores:
        inside = [f"{n} {d:.0f} mi" for n, d in stores if d <= miles]
        outside = [f"{n} {d:.0f} mi" for n, d in stores if d > miles]
        header += f"\n\n✅ Adentro ({len(inside)}): " + (", ".join(inside) or "ninguna")
        if outside:
            header += f"\n🚫 Afuera ({len(outside)}): " + ", ".join(outside)
    save_settings(header)


def settings_help() -> str:
    return (
        "⚙️ Configuración actual:\n"
        f"📱 {watch_desc()}\n"
        f"📍 {area()} (en línea recta)\n\n"
        "Para cambiarla:\n"
        "• set memoria 256 / set memoria 512 / set memoria todos\n"
        f"• set millas 100 (de {MIN_MILES} a {MAX_MILES})"
    )


def load_settings() -> None:
    """Reads pause + storage from the pinned message (older pins with only ⏸ still work)."""
    if not notifier.telegram_enabled():
        return
    try:
        text = notifier.pinned_text()
    except Exception as exc:
        print(f"{now()} [telegram] could not read pinned message: {type(exc).__name__}")
        return
    STATE["paused"] = text.startswith(PAUSE_MARK) or PAUSED_LINE in text
    for line in text.splitlines():
        if line.startswith(MILES_PREFIX):
            m = re.search(r"\d+", line)
            if m and MIN_MILES <= int(m.group()) <= MAX_MILES:
                CONFIG["max_miles"] = int(m.group())
        for k in watched_keys():
            prefix = f"{MEM_PREFIX}{PRODUCTS[k]['label']}:"
            if line.startswith(prefix):
                found = re.findall(r"\d+(?:GB|TB)", line[len(prefix):])
                mems = [m for m in mems_of(k) if m in found]
                if mems:
                    STORAGE[k] = mems
    print(f"{now()} paused={STATE['paused']} storage={STORAGE} max_miles={CONFIG['max_miles']}")


def bot_commands() -> List[tuple]:
    """Shortcuts for Telegram's ☰ Menú button (underscores become spaces when handled)."""
    cmds = [("menu", "Lista de comandos")]
    cmds += [(f"busca_{k}", f"Buscar ahora {p['label']}") for k, p in PRODUCTS.items()]
    for k in watched_keys():
        for mem in mems_of(k):
            cmds.append((f"set_memoria_{mem_num(mem)}", f"Automática: solo {PRODUCTS[k]['label']} {mem}"))
        cmds.append(("set_memoria_todos", f"Automática: {PRODUCTS[k]['label']} todas las memorias"))
    cmds.append(("set", "Ver configuración (memoria y millas)"))
    cmds += [
        ("estado", "Estado de la búsqueda"),
        ("pausa", "Pausar la búsqueda automática"),
        ("reanuda", "Retomar la búsqueda automática"),
        ("prueba_compra", "Simular un aviso para practicar"),
    ]
    return cmds


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
    m = re.match(r"^/?set\b\s*(\S*)\s*(.*)$", t)
    if m:
        what, value = m.group(1), m.group(2).strip()
        if what in ("memoria", "mem", "memory") and value:
            set_storage(f"{watched_keys()[0]} {value}")
        elif what in ("millas", "milla", "mi", "distancia") and value:
            set_miles(value)
        else:
            notifier.reply(settings_help())
        return
    m = re.match(r"^/?activa(?:r)?\s+(?:la\s+)?(?:b[uú]squeda\s+)?(.*)$", t)  # old alias
    if m:
        set_storage(m.group(1))
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
    first_seen: Dict[tuple, tuple] = {}  # (store, part) -> (Availability, timestamp first seen)
    consecutive_errors = 0
    error_alert_sent = False
    backoff = CONFIG["block_backoff_minutes"]  # e.g. [5, 10, 20]: wait times while blocked
    STATE["run_started"] = time.time()
    load_settings()
    if notifier.telegram_enabled():
        try:
            notifier.set_commands(bot_commands())
        except Exception as exc:
            print(f"{now()} [telegram] could not set menu commands: {type(exc).__name__}")

    while True:
        maybe_heartbeat()
        # Storage may have changed from Telegram: watch the new set, forget the rest
        # silently (so dropping 512 doesn't produce a fake "sold out" message).
        parts = watched_parts()
        notified = {k for k in notified if k[1] in parts}
        for k in [k for k in first_seen if k[1] not in parts]:
            first_seen.pop(k)
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
            # Blocked (HTTP 541) or Apple down: back off instead of insisting.
            consecutive_errors += 1
            if STATE["blocked_since"] is None:
                STATE["blocked_since"] = time.time()
            blocked_min = (time.time() - STATE["blocked_since"]) / 60
            wait_min = backoff[min(consecutive_errors, len(backoff)) - 1]
            STATE["next_try"] = time.time() + wait_min * 60
            STATE["errors_in_row"] = consecutive_errors
            print(f"{now()} blocked {blocked_min:.0f} min, retry in {wait_min} min")
            if blocked_min >= CONFIG["alert_after_blocked_minutes"] and not error_alert_sent:
                notifier.notify(
                    f"⚠️ Apple nos está bloqueando hace {blocked_min:.0f} min. "
                    f"Reintento cada {backoff[-1]} min y te aviso cuando se levante.\n"
                    f"Último error: {errors[-1][:120]}"
                )
                error_alert_sent = True
            if time.time() + wait_min * 60 > deadline:
                break
            listener.wait(wait_min * 60)
            continue

        if STATE["blocked_since"] is not None:
            took = fmt_duration(time.time() - STATE["blocked_since"])
            print(f"{now()} unblocked after {took}")
            if error_alert_sent:
                notifier.notify(f"✅ Apple volvió a responder (el bloqueo duró ~{took}).")
        consecutive_errors, error_alert_sent = 0, False
        STATE.update(errors_in_row=0, blocked_since=None, next_try=None)

        current = {a.key for a in items}
        new_items = [a for a in items if a.key not in notified]
        print(f"{now()} checked {checked} stores, available: {len(items)}, new: {len(new_items)}")
        if new_items and send_alert(items, parts):
            notified |= {a.key for a in new_items}

        if checked > 0:  # only trust "disappeared" when Apple actually answered
            for a in items:
                first_seen.setdefault(a.key, (a, time.time()))
            gone = [first_seen.pop(k) for k in list(first_seen) if k not in current]
            if gone:
                print(f"{now()} sold out: {len(gone)}")
                notifier.notify(format_sold_out(gone, parts))
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
