# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Toner monitoring tools for Kyocera printers (color and B&W) via SNMP. Used to evaluate whether buying toner cartridges outright is cheaper than pay-per-click service agreements.

Files:
- `kyocera_toner_check.sh` — standalone bash CLI for manual spot-checks
- `monitor.py` — **library module** (not a standalone server); exposes business logic imported by the top-level `command-central` server
- `cron_check.py` — daily cron script; queries all printers, appends a history snapshot to `toner_log.json`, and sends low-toner alert emails (once per cartridge lifetime). When an alert fires, any other supplies within `threshold + 30` days are bundled into the same email to enable grouped purchasing.
- `index.html` — browser dashboard; served at `/kyocera/` by the top-level server
- `printers.json` — persistent printer list (managed via `add_printer()`/`remove_printer()` or direct JSON edit); names are set manually
- `kyocera_config.json` — notification settings (`notify_enabled`, `notify_days_threshold`); managed via the dashboard UI or direct JSON edit (auto-created)
- `notify_sent.json` — deduplication state for email alerts; tracks `{ip: {supply_name: install_timestamp}}` of already-notified cartridges (auto-managed by `cron_check.py`)
- `toner_log.json` — toner history; keyed by IP → supply name; includes baseline, last_seen, and a `history[]` array of `initial`/`replaced`/`snapshot` events
- `cron.log` — stdout/stderr output from `cron_check.py` (auto-created)

## Running

This project is served as a sub-project of [command-central](https://github.com/casperbudtz/command-central). Start the top-level server:

```bash
python3 /path/to/command-central/server.py
# Dashboard at: http://localhost:8080/kyocera/
```

The bash CLI can still be run standalone:

```bash
./kyocera_toner_check.sh [printer-ip] [community]
# Defaults: IP=192.168.1.210, community=public
./kyocera_toner_check.sh 192.168.1.211   # second printer
```

The daily cron script is also standalone:

```bash
python3 cron_check.py   # manually trigger a snapshot run
```

Requires `snmpget` (from `snmp` package: `sudo apt install snmp`).

## monitor.py — Library API

`monitor.py` is imported by the top-level server via `importlib`. Do not add an HTTP server or `if __name__ == "__main__"` block. Public interface:

| Symbol | Type | Description |
|---|---|---|
| `PRINTERS` | list | Printer dicts (`ip`, `name`), loaded from `printers.json` on import |
| `load_printers()` | function | Read printer list from `printers.json` |
| `save_printers(printers)` | function | Atomically write printer list to `printers.json` |
| `add_printer(ip, name=None)` | function | Add printer (uses IP as name if omitted), persists to JSON, returns entry |
| `remove_printer(ip)` | function | Remove printer by IP, persists to JSON, returns `True`/`False` |
| `load_config()` | function | Read `kyocera_config.json` (returns defaults if missing) |
| `save_config(cfg)` | function | Atomically write `kyocera_config.json` |
| `check_printer(ip, community, record_snapshot=False)` | function | Full SNMP query; returns toner/coverage JSON. Pass `record_snapshot=True` to append a history entry (used by cron). |
| `load_log()` | function | Read `toner_log.json` |
| `save_log(data)` | function | Atomically write `toner_log.json` |
| `apply_log(..., record_snapshot=False)` | function | Update cartridge baseline; return coverage fields including `days_left`. When `record_snapshot=True` and no initial/replaced event occurred, appends a `"snapshot"` entry to `history[]`. |

## Network

| IP | Name | Model |
|----|------|-------|
| `192.168.1.210` | Kyocera Kontor | MA3500cifx (color) |
| `192.168.1.211` | Kyocera Kitte  | MA3500cifx (color) |
| `192.168.1.212` | Kyocera Mette  | B&W |

SNMP must be enabled on the printer via Command Center RX (web UI at printer IP): Network Settings → Protocol → SNMPv1/v2c = On, community string = `public`.

## Key Details

### SNMP OID Map

Base: `1.3.6.1.2.1.43`

| OID suffix           | Description                                |
|----------------------|--------------------------------------------|
| `.11.1.1.4.1.x`      | Supply type (3=toner, 4=wasteToner)        |
| `.11.1.1.6.1.x`      | Supply description (string)                |
| `.11.1.1.8.1.x`      | Max capacity (rated pages at 5% coverage)  |
| `.11.1.1.9.1.x`      | Current level (remaining pages at 5%)      |
| `.10.2.1.4.1.1`      | Total printed page count                   |

Kyocera vendor MIB base: `1.3.6.1.4.1.1347.42.2.1.1.1`

| OID suffix | Description               |
|------------|---------------------------|
| `.6.1.1`   | Total page count (all)    |
| `.7.1.1`   | Mono (B&W) page count     |
| `.8.1.1`   | Color page count          |

Page count used per supply for coverage calculation:
- **Black**: total page count (Black is consumed by both B&W and color prints)
- **CMY**: color page count only (CMY are not consumed by B&W prints)

Supply indices vary by printer model. Supply type OID (`.4.1.x`) is used to detect waste toner boxes at any index. Color printers (MA3500cifx): indices 1–4 are toners (C/M/Y/K), index 5 is waste. B&W printers: index 1 is the toner, index 2 is waste.

Supply names are derived from the SNMP description string: full colour words ("Cyan", "Black") or Kyocera model-number suffixes (TK-5370**C**S=Cyan, TK-5370**K**S=Black, TK-3400**S**→Black fallback).

Waste toner level values: `-3` = OK, `-1` = Unknown, `0` = Full/replace.

### Cartridges (TK-5370 series)

| Cartridge type | Black (K) | Cyan/Magenta/Yellow |
|----------------|-----------|---------------------|
| Starter        | 3,500 pp  | 2,500 pp            |
| Full           | 7,000 pp  | 5,000 pp            |

Starter vs. full is auto-detected by comparing SNMP-reported max capacity to the full cartridge thresholds (`FULL_CAPACITY_K=7000`, `FULL_CAPACITY_CMY=5000`).

### Coverage Formula

All SNMP capacity values are in pages at 5% coverage (ISO/IEC 19798):

```
consumed        = max - current   (in 5%-coverage-page units)
actual_coverage = (consumed / pages_printed) × 5%
effective_yield = max × (pages_printed / consumed)
days_left       = current / (consumed / days_since_install)
```

Coverage and days-left estimates are unreliable until meaningful toner has been consumed (nearly-full cartridges give noisy readings); the UI marks them "early" until 100 pages have been printed since install. Best results from periodic runs across a full cartridge lifecycle.

### toner_log.json Structure

```json
{
  "<printer-ip>": {
    "<supply-name>": {
      "baseline":  { "timestamp": "…", "page_count": 0, "level": 2500, "max": 2500 },
      "last_seen": { "timestamp": "…", "level": 2325, "page_count": 20 },
      "history": [
        { "event": "initial",  "timestamp": "…", "page_count": 0,  "level": 2500, "max": 2500 },
        { "event": "snapshot", "timestamp": "…", "page_count": 20, "level": 2325, "max": 2500 },
        { "event": "replaced", "timestamp": "…", "page_count": 80, "level": 2500, "max": 5000 }
      ]
    }
  }
}
```

History events: `initial` (first scan), `snapshot` (daily cron), `replaced` (cartridge swap detected).

### Cron Job

`cron_check.py` is installed at `55 23 * * *` (23:55 daily) in the system crontab. It appends one `snapshot` per toner supply per run, providing the time-series data that powers days-left estimation. Output is appended to `cron.log`.

To re-install or move to a different machine:

```bash
crontab -e
# Add:
55 23 * * * /usr/bin/python3 /home/casper/Documents/Code/Kyocera/cron_check.py >> /home/casper/Documents/Code/Kyocera/cron.log 2>&1
```

### Locale

`LC_NUMERIC=C` is set to force dot as decimal separator, fixing issues with Danish/European locales that use comma.

## Potential Enhancements

- CSV export of `toner_log.json` history for trending in spreadsheets
- Cost-per-page calculation with configurable cartridge prices
