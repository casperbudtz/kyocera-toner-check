# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Toner monitoring tools for Kyocera ECOSYS MA3500cifx/MA4000cifx printers via SNMP. Used to evaluate whether buying toner cartridges outright is cheaper than pay-per-click service agreements.

Files:
- `kyocera_toner_check.sh` — standalone bash CLI for manual spot-checks
- `monitor.py` — **library module** (not a standalone server); exposes business logic imported by the top-level `command-central` server
- `index.html` — browser dashboard; served at `/kyocera/` by the top-level server

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

Requires `snmpget` (from `snmp` package: `sudo apt install snmp`).

## monitor.py — Library API

`monitor.py` is imported by the top-level server via `importlib`. Do not add an HTTP server or `if __name__ == "__main__"` block. Public interface:

| Symbol | Type | Description |
|---|---|---|
| `PRINTERS` | list | Configured printer dicts (`ip`, `name`) |
| `check_printer(ip, community)` | function | Full SNMP query; returns toner/coverage JSON |
| `load_log()` | function | Read `toner_log.json` |
| `save_log(data)` | function | Atomically write `toner_log.json` |
| `apply_log(...)` | function | Update cartridge baseline; return coverage fields |

## Network

- MA3500cifx #1: `192.168.1.210` (default)
- MA3500cifx #2: `192.168.1.211`

SNMP must be enabled on the printer via Command Center RX (web UI at printer IP): Network Settings → Protocol → SNMPv1/v2c = On, community string = `public`.

## Key Details

### SNMP OID Map

Base: `1.3.6.1.2.1.43`

| OID suffix           | Description                                |
|----------------------|--------------------------------------------|
| `.11.1.1.6.1.x`      | Supply description (string)                |
| `.11.1.1.8.1.x`      | Max capacity (rated pages at 5% coverage)  |
| `.11.1.1.9.1.x`      | Current level (remaining pages at 5%)      |
| `.10.2.1.4.1.1`      | Total printed page count                   |

Supply indices: 1=Cyan, 2=Magenta, 3=Yellow, 4=Black, 5=Waste Toner Box

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
```

Coverage estimates are unreliable until meaningful toner has been consumed (nearly-full cartridges give noisy readings). Best results from periodic runs across a full cartridge lifecycle.

### Locale

`LC_NUMERIC=C` is set to force dot as decimal separator, fixing issues with Danish/European locales that use comma.

## PRTG Integration

A PowerShell PRTG EXE/Script sensor was attempted but failed (PRTG server on Windows Server 2012 R2 lacked the SNMP client feature). Alternatives:
- PRTG "SNMP Custom Advanced" sensors using the raw OIDs above (toner levels and page counts, but no calculated coverage)
- PRTG SSH Script Advanced sensor pointing to this script on a Linux box
- Install Net-SNMP for Windows on the PRTG server

## Potential Enhancements

Ideas noted in the script header (not yet implemented):
- CSV logging mode for trending (append timestamp + values)
- Multi-printer loop over multiple IPs
- Alert thresholds (e.g. email when toner < X%)
- Cron job for periodic collection
- Cost-per-page calculation with configurable cartridge prices
