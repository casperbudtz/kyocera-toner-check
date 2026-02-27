# Kyocera Toner Check

Monitors toner levels and calculates actual page coverage on Kyocera ECOSYS MA3500cifx/MA4000cifx color laser printers via SNMP.

> **Part of [command-central](https://github.com/casperbudtz/command-central).** The web dashboard (`monitor.py` + `index.html`) is served at `/kyocera/` by the top-level server. This repo is included as a git submodule.

## Background

Kyocera (and most printer vendors) rate toner cartridge yield at 5% page coverage per ISO/IEC 19798. Pay-per-click service agreements also charge flat rates assuming this 5% figure. In practice, most offices print well below 5% average coverage — meaning cartridges last significantly longer than rated, and buying outright is often cheaper than a click contract.

This script queries the printer's SNMP interface to compare actual toner consumption against pages printed, giving you a real coverage percentage and effective cartridge yield.

## Requirements

```bash
sudo apt install snmp
```

## Running

### Web dashboard (recommended)

Start the top-level command-central server:

```bash
python3 /path/to/command-central/server.py
# Open http://localhost:8080/kyocera/
```

The dashboard shows live toner levels, coverage estimation, and **estimated days remaining** per cartridge based on your actual consumption rate.

### Daily cron snapshot (recommended)

`cron_check.py` records a toner snapshot once a day, building the history that powers the days-left estimate. Set it up once:

```bash
crontab -e
# Add:
55 23 * * * /usr/bin/python3 /path/to/kyocera/cron_check.py >> /path/to/kyocera/cron.log 2>&1
```

You can also trigger it manually at any time:

```bash
python3 cron_check.py
```

### Bash CLI (standalone)

```bash
./kyocera_toner_check.sh [printer-ip] [community]
```

| Argument     | Default         | Description               |
|--------------|-----------------|---------------------------|
| `printer-ip` | `192.168.1.210` | IP address of the printer |
| `community`  | `public`        | SNMP community string     |

Examples:
```bash
./kyocera_toner_check.sh                        # use defaults
./kyocera_toner_check.sh 192.168.1.211          # second printer
./kyocera_toner_check.sh 192.168.1.210 public   # explicit args
```

## Output

The script produces two sections:

**1. Toner Level Report** — remaining percentage per cartridge with a visual bar, and starter vs. full cartridge detection.

```
  ╔════════════════════════════════════════════════════════════════════╗
  ║         Kyocera ECOSYS MA3500cifx — Toner Status Report          ║
  ╚════════════════════════════════════════════════════════════════════╝

  Printer IP:     192.168.1.210
  Total Pages:    12453
  Date:           2025-06-01 09:12:44

  ────────────────────────────────────────────────────────────────────
  Supply       Cartridge       Max    Current   Remain   Bar
  ────────────────────────────────────────────────────────────────────
  Cyan         TK-5370C       5000       3821    76.4%   ███████████████░░░░░
  Magenta      TK-5370M       5000       4102    82.0%   ████████████████░░░░
  Yellow       TK-5370Y       5000       4390    87.8%   █████████████████░░░
  Black        TK-5370K       7000       5230    74.7%   ██████████████░░░░░░
  Waste Toner                   —          —       OK
```

**2. Coverage Estimation** — estimated actual coverage % and projected pages per cartridge at your real usage.

```
  Supply         Consumed      Pages     Est.Cover     Eff.Yield
  ────────────────────────────────────────────────────────────────────
  Cyan               1179      12453        0.47%         52,925
  Magenta             898      12453        0.36%         69,485
  Yellow              610      12453        0.24%        102,148
  Black              1770      12453        0.71%         49,254
```

## How Coverage and Days Left Are Calculated

The SNMP max/current values are expressed in pages at 5% coverage. The script derives:

```
consumed        = max - current        (toner used, in 5%-coverage-page units)
actual_coverage = (consumed / pages_printed) × 5%
effective_yield = max × (pages_printed / consumed)
days_left       = current / (consumed / days_since_install)
```

> Estimates improve as more toner is consumed. Results from nearly-full cartridges are noisy — the dashboard marks them "early" until 100 pages have been printed since install. Run the daily cron job (`cron_check.py`) for the most accurate days-left projection over a full cartridge lifecycle.

## Cartridges (TK-5370 series)

| Type    | Black (K) | Cyan / Magenta / Yellow |
|---------|-----------|-------------------------|
| Starter | 3,500 pp  | 2,500 pp                |
| Full    | 7,000 pp  | 5,000 pp                |

Starter vs. full cartridges are auto-detected from the SNMP-reported max capacity.

## SNMP Setup

SNMP must be enabled on the printer. Access the web UI (Command Center RX) at the printer's IP address:

- Network Settings → Protocol → SNMPv1/v2c = **On**
- Management Settings → SNMP → Read Community = `public`
