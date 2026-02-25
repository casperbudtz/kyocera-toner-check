# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Single bash script (`kyocera_toner_check.sh`) that monitors toner levels and calculates actual page coverage on Kyocera ECOSYS MA3500cifx/MA4000cifx printers via SNMP. Used to evaluate whether buying toner cartridges outright is cheaper than pay-per-click service agreements.

## Running

```bash
./kyocera_toner_check.sh [printer-ip] [community]
# Defaults: IP=192.168.1.210, community=public
```

Requires `snmpget` (from `snmp` package: `sudo apt install snmp`).

## Key Details

- Queries SNMP OID tree `1.3.6.1.2.1.43` for supply levels and page counts
- Supply indices: 1=Cyan, 2=Magenta, 3=Yellow, 4=Black, 5=Waste Toner Box
- Auto-detects starter vs full cartridges (TK-5370 series) based on SNMP-reported max capacity
- Coverage formula: `actual_coverage = (consumed / pages_printed) × 5%` where consumed = max - current level in rated-page units
- `LC_NUMERIC=C` is set to handle Danish/European locale decimal separator issues
