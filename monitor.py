#!/usr/bin/env python3
"""
Kyocera Toner Monitor — library module
Business logic for querying printer toner status via SNMP and maintaining
a log of cartridge baselines for coverage calculations.

Imported by the top-level server.py; not intended to be run standalone.
"""

import json
import os
import socket
import subprocess
from datetime import datetime

SCRIPT_DIR     = os.path.dirname(os.path.abspath(__file__))
LOG_FILE       = os.path.join(SCRIPT_DIR, "toner_log.json")
PRINTERS_FILE  = os.path.join(SCRIPT_DIR, "printers.json")

# A cartridge replacement is recorded when the toner level both rises by
# more than this many rated pages AND is at or above 85 % of max capacity
# (i.e. a fresh cartridge was installed, not just a small SNMP fluctuation).
REPLACEMENT_THRESHOLD = 100

# Minimum pages printed since install before coverage estimates are
# considered meaningful enough to display without a warning.
MIN_RELIABLE_PAGES = 100


# ── Printer list persistence ─────────────────────────────────────────────────

def load_printers():
    """Load printer list from printers.json."""
    try:
        with open(PRINTERS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_printers(printers):
    """Atomically write the printer list to printers.json."""
    tmp = PRINTERS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(printers, f, indent=2)
        f.write("\n")
    os.replace(tmp, PRINTERS_FILE)


def resolve_name(ip):
    """Try to resolve a human-readable name for the given IP.

    Attempts in order:
      1. Reverse DNS (socket.gethostbyaddr)
      2. NetBIOS lookup (nmblookup -A)
      3. Falls back to the raw IP string
    """
    # 1. Reverse DNS
    try:
        hostname = socket.gethostbyaddr(ip)[0]
        if hostname and hostname != ip:
            return hostname
    except (socket.herror, socket.gaierror, OSError):
        pass

    # 2. NetBIOS via nmblookup
    try:
        r = subprocess.run(
            ["nmblookup", "-A", ip],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                line = line.strip()
                if "<00>" in line and "<UNIQUE>" in line.upper():
                    return line.split()[0]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # 3. Fallback
    return ip


def add_printer(ip, name=None):
    """Add a printer. Resolves name if not provided. Returns the new entry."""
    printers = load_printers()
    # Avoid duplicates
    for p in printers:
        if p["ip"] == ip:
            return p
    if not name:
        name = resolve_name(ip)
    entry = {"ip": ip, "name": name}
    printers.append(entry)
    save_printers(printers)
    return entry


def remove_printer(ip):
    """Remove a printer by IP. Returns True if found and removed."""
    printers = load_printers()
    filtered = [p for p in printers if p["ip"] != ip]
    if len(filtered) == len(printers):
        return False
    save_printers(filtered)
    return True


PRINTERS = load_printers()

OID_SUPPLY_DESC  = "1.3.6.1.2.1.43.11.1.1.6.1"
OID_SUPPLY_MAX   = "1.3.6.1.2.1.43.11.1.1.8.1"
OID_SUPPLY_LEVEL = "1.3.6.1.2.1.43.11.1.1.9.1"
OID_PAGE_COUNT   = "1.3.6.1.2.1.43.10.2.1.4.1.1"

SUPPLY_NAMES   = ["Cyan", "Magenta", "Yellow", "Black"]
SUPPLY_INDICES = [1, 2, 3, 4]

FULL_CAPACITY_CMY = 5000
FULL_CAPACITY_K   = 7000


# ── SNMP ──────────────────────────────────────────────────────────────────────

def snmp_get(ip, community, oid):
    """Query one SNMP OID. Returns the value string, or None on failure."""
    env = os.environ.copy()
    env["LC_NUMERIC"] = "C"
    try:
        r = subprocess.run(
            ["snmpget", "-v2c", "-c", community, "-Oqv", ip, oid],
            capture_output=True, text=True, timeout=5, env=env,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return None
        return r.stdout.strip().strip('"')
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


# ── Log ───────────────────────────────────────────────────────────────────────

def load_log():
    try:
        with open(LOG_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_log(data):
    tmp = LOG_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, LOG_FILE)


# ── Per-supply log update & coverage calculation ──────────────────────────────

def apply_log(log, ip, name, page_count, level, max_val, timestamp):
    """
    Update log[ip][name] in-place with the latest SNMP reading.

    - If no entry exists yet, records current values as the initial baseline.
    - If the toner level has risen by more than REPLACEMENT_THRESHOLD since the
      last reading, records a new baseline (cartridge replaced).
    - Calculates coverage metrics from the delta since the current baseline.

    Returns a dict of coverage fields to merge into the supply record.
    """
    printer_log = log.setdefault(ip, {})
    entry = printer_log.get(name)
    event = None

    if entry is None:
        # First time seeing this supply — seed baseline from current values.
        event = "initial"
        entry = {
            "baseline": {
                "timestamp":  timestamp,
                "page_count": page_count,
                "level":      level,
                "max":        max_val,
            },
            "last_seen": {
                "timestamp":  timestamp,
                "level":      level,
                "page_count": page_count,
            },
            "history": [{
                "event":      "initial",
                "timestamp":  timestamp,
                "page_count": page_count,
                "level":      level,
                "max":        max_val,
            }],
        }
        printer_log[name] = entry

    else:
        last_level = entry["last_seen"]["level"]

        if level > last_level + REPLACEMENT_THRESHOLD and level >= 0.85 * max_val:
            # Level jumped back to near-full capacity — cartridge was replaced.
            event = "replaced"
            entry["baseline"] = {
                "timestamp":  timestamp,
                "page_count": page_count,
                "level":      level,
                "max":        max_val,
            }
            entry["history"].append({
                "event":      "replaced",
                "timestamp":  timestamp,
                "page_count": page_count,
                "level":      level,
                "max":        max_val,
            })

        # Always update last_seen.
        entry["last_seen"] = {
            "timestamp":  timestamp,
            "level":      level,
            "page_count": page_count,
        }

    # ── Coverage from baseline deltas ────────────────────────────────────────
    bl             = entry["baseline"]
    consumed_since = bl["level"] - level          # rated-page units consumed since install
    pages_since    = page_count  - bl["page_count"]  # actual pages printed since install

    # Guard against noise (consumed can go slightly negative on fresh installs).
    consumed_since = max(consumed_since, 0)

    est_coverage = None
    eff_yield    = None
    reliable     = False

    if consumed_since > 0 and pages_since > 0:
        est_coverage = round((consumed_since / pages_since) * 5.0, 2)
        eff_yield    = round(bl["max"] * (pages_since / consumed_since))
        reliable     = pages_since >= MIN_RELIABLE_PAGES

    return {
        "install_timestamp":      bl["timestamp"],
        "install_page_count":     bl["page_count"],
        "install_level":          bl["level"],
        "pages_since_install":    pages_since,
        "consumed_since_install": consumed_since,
        "estimated_coverage":     est_coverage,
        "effective_yield":        eff_yield,
        "coverage_reliable":      reliable,
        "cartridge_event":        event,   # "initial" | "replaced" | None
    }


# ── Main query ────────────────────────────────────────────────────────────────

def check_printer(ip, community="public"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if snmp_get(ip, community, f"{OID_SUPPLY_DESC}.1") is None:
        return {
            "printer_ip": ip,
            "timestamp":  timestamp,
            "error":      f"Cannot reach printer at {ip} (community: {community})",
        }

    page_count = None
    try:
        page_count = int(snmp_get(ip, community, OID_PAGE_COUNT))
    except (TypeError, ValueError):
        pass

    log = load_log()
    supplies = []

    for i, idx in enumerate(SUPPLY_INDICES):
        name      = SUPPLY_NAMES[i]
        desc      = snmp_get(ip, community, f"{OID_SUPPLY_DESC}.{idx}") or "?"
        max_raw   = snmp_get(ip, community, f"{OID_SUPPLY_MAX}.{idx}")
        level_raw = snmp_get(ip, community, f"{OID_SUPPLY_LEVEL}.{idx}")

        try:
            max_val   = int(max_raw)
            level_val = int(level_raw)
        except (TypeError, ValueError):
            supplies.append({"name": name, "description": desc, "error": True})
            continue

        if max_val <= 0:
            supplies.append({"name": name, "description": desc, "error": True})
            continue

        pct        = (level_val / max_val) * 100
        is_starter = (max_val < FULL_CAPACITY_CMY) if i < 3 else (max_val < FULL_CAPACITY_K)

        cov = {}
        if page_count is not None:
            cov = apply_log(log, ip, name, page_count, level_val, max_val, timestamp)

        supplies.append({
            "name":        name,
            "description": desc,
            "is_starter":  is_starter,
            "max":         max_val,
            "current":     level_val,
            "percent":     round(pct, 1),
            **cov,
        })

    save_log(log)

    waste_raw    = snmp_get(ip, community, f"{OID_SUPPLY_LEVEL}.5")
    waste_status = "Unknown"
    try:
        wv = int(waste_raw)
        if wv == -3:
            waste_status = "OK"
        elif wv == 0:
            waste_status = "FULL — replace!"
    except (TypeError, ValueError):
        pass

    return {
        "printer_ip":  ip,
        "timestamp":   timestamp,
        "page_count":  page_count,
        "supplies":    supplies,
        "waste_toner": {"status": waste_status},
        "error":       None,
    }
