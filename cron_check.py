#!/usr/bin/env python3
"""
Kyocera Toner Monitor — daily cron snapshot.

Queries all configured printers via SNMP and appends a "snapshot" history
entry to toner_log.json for each toner supply.  Run just before midnight so
you accumulate one data-point per day, giving the dashboard enough history to
estimate days-left and coverage trends.

Setup (run once):
    crontab -e
    # Add the following line:
    55 23 * * * /usr/bin/python3 /home/casper/Documents/Code/Kyocera/cron_check.py >> /home/casper/Documents/Code/Kyocera/cron.log 2>&1

Requires: snmpget  (sudo apt install snmp)
"""

import importlib.util
import os
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Load monitor.py as a library module (mirrors how server.py uses it).
_spec   = importlib.util.spec_from_file_location("monitor", os.path.join(SCRIPT_DIR, "monitor.py"))
monitor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(monitor)


def main():
    printers = monitor.load_printers()
    if not printers:
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] No printers configured — nothing to do.",
              file=sys.stderr)
        return 0

    errors = 0
    for p in printers:
        ip   = p["ip"]
        name = p.get("name", ip)
        result = monitor.check_printer(ip, record_snapshot=True)

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if result.get("error"):
            print(f"[{ts}] ERROR  {name} ({ip}): {result['error']}", file=sys.stderr)
            errors += 1
            continue

        pages = result.get("page_count", "?")
        parts = []
        for s in result.get("supplies", []):
            if s.get("error"):
                parts.append(f"{s['name']}: ERR")
                continue
            dl     = s.get("days_left")
            dl_str = f"{dl}d left" if dl is not None else "?"
            parts.append(f"{s['name']}: {s['percent']:.1f}% ({dl_str})")

        print(f"[{ts}] OK     {name} ({ip}) — {pages} pages — " + ", ".join(parts))

    return errors


if __name__ == "__main__":
    sys.exit(main())
