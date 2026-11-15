"""iPhone Duo pickup watcher.

  python main.py --once            # single check, print result
  python main.py --duration 330    # loop for 330 minutes (used by GitHub Actions)
  python main.py --test-notify     # send a test message to configured channels
"""
from __future__ import annotations

import argparse
import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from services import apple_checker, notifier

CONFIG = json.loads((Path(__file__).parent / "config.json").read_text())


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def format_alert(items) -> str:
    lines = ["iPhone Duo DISPONIBLE para pickup:"]
    for a in items:
        lines.append(f"- {a.model} @ {a.store_name} ({a.city}, {a.miles:.0f} mi) {a.quote}".rstrip())
    lines.append(f"Reservar: {CONFIG['product_url']}")
    return "\n".join(lines)


def run(duration_min: float, interval: int) -> None:
    deadline = time.time() + duration_min * 60
    notified = set()  # (store, part) currently known as available
    consecutive_errors = 0
    error_alert_sent = False

    while True:
        items, checked, errors = apple_checker.check(CONFIG)
        if errors:
            for e in errors:
                print(f"{now()} [error] {e}")
        if errors and checked == 0:
            consecutive_errors += 1
            if consecutive_errors >= CONFIG["alert_after_consecutive_errors"] and not error_alert_sent:
                notifier.notify(
                    "iPhone Duo watcher: Apple no responde hace "
                    f"{consecutive_errors} chequeos seguidos. Último error: {errors[-1][:200]}"
                )
                error_alert_sent = True
        else:
            if error_alert_sent:
                notifier.notify("iPhone Duo watcher: volvió a funcionar.")
            consecutive_errors, error_alert_sent = 0, False

        current = {a.key for a in items}
        new_items = [a for a in items if a.key not in notified]
        print(f"{now()} checked {checked} stores, available: {len(items)}, new: {len(new_items)}")
        if new_items:
            # Include everything currently available so the message is complete.
            if notifier.notify(format_alert(items)):
                notified |= {a.key for a in new_items}
        # Forget items that went out of stock, so a restock notifies again.
        notified &= current

        if time.time() + interval > deadline:
            break
        time.sleep(interval + random.uniform(-10, 10))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true")
    p.add_argument("--duration", type=float, default=0, help="minutes to keep looping")
    p.add_argument("--interval", type=int, default=CONFIG["interval_seconds"])
    p.add_argument("--test-notify", action="store_true")
    args = p.parse_args()

    if args.test_notify:
        items, checked, errors = apple_checker.check(CONFIG)
        msg = (
            f"iPhone Duo watcher: prueba OK. Revisé {checked} tiendas a <= {CONFIG['max_miles']} mi "
            f"de {CONFIG['origin']['name']}; disponibles ahora: {len(items)}."
        )
        if errors:
            msg += f" Errores: {len(errors)}"
        ok = notifier.notify(msg)
        raise SystemExit(0 if ok else 1)

    if args.once:
        items, checked, errors = apple_checker.check(CONFIG)
        for e in errors:
            print(f"[error] {e}")
        print(f"Checked {checked} stores. Available: {len(items)}")
        if items:
            print(format_alert(items))
        return

    run(args.duration or 60, args.interval)


if __name__ == "__main__":
    main()
