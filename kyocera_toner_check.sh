#!/bin/bash
# ============================================================================
# Kyocera ECOSYS MA3500cifx Toner Status & Coverage Calculator
# ============================================================================
#
# PURPOSE:
#   Monitors toner levels and calculates actual page coverage on a Kyocera
#   ECOSYS MA3500cifx (or MA4000cifx) color laser printer via SNMP.
#   This helps determine real cost-per-print by comparing actual toner
#   consumption against the manufacturer's rated capacity (which assumes
#   5% page coverage per ISO/IEC 19798).
#
# BACKGROUND:
#   Casper's office runs Kyocera ECOSYS printers (MA3500cifx and MA4000cifx)
#   and is evaluating whether buying toner cartridges outright is cheaper
#   than a pay-per-click service agreement. Historical data from a Toshiba
#   e-STUDIO479CS showed actual coverage well below the industry-standard
#   5% (roughly 3% black, under 2% color), meaning cartridges last much
#   longer than rated — making per-cartridge purchasing significantly
#   cheaper than click contracts that charge flat rates assuming 5%.
#
#   The Kyocera does not natively report per-page coverage statistics like
#   the Toshiba did, but it does expose toner levels and page counts via
#   SNMP (OID tree 1.3.6.1.2.1.43). This script queries those OIDs and
#   calculates the estimated coverage and effective cartridge yield.
#
# TONER CARTRIDGE INFO:
#   Model: TK-5370 (European) / TK-5372 (US)
#   Starter cartridges (shipped with printer):
#     Black (K): 3,500 pages @ 5%  |  Cyan/Magenta/Yellow: 2,500 pages @ 5%
#   Full replacement cartridges:
#     Black (K): 7,000 pages @ 5%  |  Cyan/Magenta/Yellow: 5,000 pages @ 5%
#   The script auto-detects starter vs full cartridges based on SNMP
#   reported max capacity.
#
# SNMP OID MAP (Kyocera MA3500cifx supply indices):
#   Index 1 = Cyan       (TK-5370C / TK-5370CS starter)
#   Index 2 = Magenta    (TK-5370M / TK-5370MS starter)
#   Index 3 = Yellow     (TK-5370Y / TK-5370YS starter)
#   Index 4 = Black      (TK-5370K / TK-5370KS starter)
#   Index 5 = Waste Toner Box
#
#   Key OIDs:
#     .43.11.1.1.6.1.x  = Supply description (string)
#     .43.11.1.1.8.1.x  = Max capacity (rated pages at 5% coverage)
#     .43.11.1.1.9.1.x  = Current level (remaining pages at 5% coverage)
#     .43.10.2.1.4.1.1   = Total printed page count
#
# SNMP SETUP:
#   The printer must have SNMPv1/v2c enabled (it is by default).
#   Verify in Command Center RX (web interface at printer's IP):
#     Network Settings → Protocol → SNMPv1/v2c = On
#     Management Settings → SNMP → Read Community = "public"
#
# NETWORK:
#   Casper's printers are on the local network:
#     MA3500cifx #1: 192.168.1.210
#     MA3500cifx #2: 192.168.1.211 (if applicable)
#
# DEPENDENCIES:
#   - snmpget (part of the 'snmp' package: sudo apt install snmp)
#   - awk (standard on all Linux systems)
#
# USAGE:
#   ./kyocera_toner_check.sh [printer-ip] [community]
#     printer-ip  : IP address of the printer (default: 192.168.1.210)
#     community   : SNMP community string (default: public)
#
#   Examples:
#     ./kyocera_toner_check.sh                        # uses defaults
#     ./kyocera_toner_check.sh 192.168.1.211          # second printer
#     ./kyocera_toner_check.sh 192.168.1.210 public   # explicit community
#
# OUTPUT:
#   1. Toner level report: remaining % per cartridge with visual bar
#   2. Coverage estimation: actual coverage %, effective yield projection
#
# COVERAGE CALCULATION:
#   The SNMP "max capacity" and "current level" are expressed in pages
#   at 5% coverage. So if max=7000 and current=3500, that means half the
#   toner is consumed — equivalent to 3500 pages at 5% coverage.
#
#   Formula:
#     consumed = max - current (in 5%-coverage-page equivalents)
#     actual_coverage = (consumed / actual_pages_printed) × 5%
#     effective_yield = max × (actual_pages_printed / consumed)
#
#   Example: If 1000 "toner units" consumed over 5000 actual pages:
#     actual_coverage = (1000/5000) × 5% = 1.0%
#     effective_yield = 7000 × (5000/1000) = 35,000 pages per cartridge
#
# ACCURACY NOTE:
#   Coverage estimates improve as more toner is consumed. Results from
#   nearly-full cartridges are unreliable due to initial calibration and
#   toner settling. Best results come from running this periodically
#   across a full cartridge lifecycle.
#
# PRTG INTEGRATION NOTE:
#   An attempt was made to create a PowerShell PRTG EXE/Script sensor,
#   but the PRTG server (Windows Server 2012 R2) lacked the SNMP client
#   feature and it failed to install. Alternative approaches:
#   - Use PRTG "SNMP Custom Advanced" sensors with the raw OIDs above
#     (gives toner levels and page counts but not calculated coverage)
#   - Run this script via PRTG's SSH Script Advanced sensor on the Linux box
#   - Install Net-SNMP for Windows on the PRTG server
#
# FUTURE ENHANCEMENTS TO CONSIDER:
#   - CSV logging mode (append timestamp + values to a CSV for trending)
#   - Multi-printer mode (loop through multiple IPs)
#   - Alert thresholds (email notification when toner drops below X%)
#   - Cron job for periodic data collection
#   - Cost-per-page calculation with configurable cartridge prices
#
# ============================================================================

PRINTER_IP="${1:-192.168.1.210}"
COMMUNITY="${2:-public}"

# Force dot as decimal separator (fixes issues with Danish/European locales)
export LC_NUMERIC=C

# SNMP OID base paths
OID_SUPPLY_DESC="1.3.6.1.2.1.43.11.1.1.6.1"
OID_SUPPLY_MAX="1.3.6.1.2.1.43.11.1.1.8.1"
OID_SUPPLY_LEVEL="1.3.6.1.2.1.43.11.1.1.9.1"
OID_PAGE_COUNT="1.3.6.1.2.1.43.10.2.1.4.1.1"

# Color names mapped to SNMP indices (based on your printer's order)
SUPPLY_NAMES=("Cyan" "Magenta" "Yellow" "Black")
SUPPLY_INDICES=(1 2 3 4)

# Rated capacities for full replacement cartridges (TK-5370 at 5% coverage)
# Used to detect starter vs full cartridges
FULL_CAPACITY_CMY=5000
FULL_CAPACITY_K=7000

# ----------------------------------------------------------------------------
# Functions
# ----------------------------------------------------------------------------

snmp_get() {
    local oid="$1"
    local result
    result=$(snmpget -v2c -c "$COMMUNITY" -Oqv "$PRINTER_IP" "$oid" 2>/dev/null)
    if [ $? -ne 0 ] || [ -z "$result" ]; then
        echo "ERROR"
    else
        echo "$result" | tr -d '"'
    fi
}

print_separator() {
    printf '  %.0s' {1..1}
    printf '%0.s─' {1..68}
    printf '\n'
}

# ----------------------------------------------------------------------------
# Check prerequisites
# ----------------------------------------------------------------------------

if ! command -v snmpget &> /dev/null; then
    echo "Error: snmpget not found. Install with: sudo apt install snmp"
    exit 1
fi

# Quick connectivity test
test_result=$(snmpget -v2c -c "$COMMUNITY" -Oqv "$PRINTER_IP" "$OID_SUPPLY_DESC.1" 2>/dev/null)
if [ $? -ne 0 ] || [ -z "$test_result" ]; then
    echo "Error: Cannot reach printer at $PRINTER_IP (community: $COMMUNITY)"
    exit 1
fi

# ----------------------------------------------------------------------------
# Gather data
# ----------------------------------------------------------------------------

# Get total page count
PAGE_COUNT=$(snmp_get "$OID_PAGE_COUNT")
if [ "$PAGE_COUNT" = "ERROR" ]; then
    # Try alternative OID for page count
    PAGE_COUNT=$(snmpget -v2c -c "$COMMUNITY" -Oqv "$PRINTER_IP" "1.3.6.1.2.1.43.10.2.1.4.1.1" 2>/dev/null)
    if [ $? -ne 0 ] || [ -z "$PAGE_COUNT" ]; then
        PAGE_COUNT="N/A"
    fi
fi

# Get supply data
declare -a DESC MAX LEVEL
for i in "${!SUPPLY_INDICES[@]}"; do
    idx="${SUPPLY_INDICES[$i]}"
    DESC[$i]=$(snmp_get "${OID_SUPPLY_DESC}.${idx}")
    MAX[$i]=$(snmp_get "${OID_SUPPLY_MAX}.${idx}")
    LEVEL[$i]=$(snmp_get "${OID_SUPPLY_LEVEL}.${idx}")
done

# Get waste toner status
WASTE_LEVEL=$(snmp_get "${OID_SUPPLY_LEVEL}.5")

# ----------------------------------------------------------------------------
# Display results
# ----------------------------------------------------------------------------

echo ""
echo "  ╔════════════════════════════════════════════════════════════════════╗"
echo "  ║         Kyocera ECOSYS MA3500cifx — Toner Status Report          ║"
echo "  ╚════════════════════════════════════════════════════════════════════╝"
echo ""
echo "  Printer IP:     $PRINTER_IP"
echo "  Total Pages:    $PAGE_COUNT"
echo "  Date:           $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

# --- Toner Levels ---
print_separator
printf "  %-12s %-14s %8s %8s %8s   %-10s\n" "Supply" "Cartridge" "Max" "Current" "Remain" "Bar"
print_separator

for i in "${!SUPPLY_INDICES[@]}"; do
    name="${SUPPLY_NAMES[$i]}"
    desc="${DESC[$i]}"
    max="${MAX[$i]}"
    level="${LEVEL[$i]}"

    if [ "$max" = "ERROR" ] || [ "$level" = "ERROR" ] || [ "$max" -le 0 ] 2>/dev/null; then
        printf "  %-12s %-14s %8s %8s %8s\n" "$name" "$desc" "?" "?" "?"
        continue
    fi

    pct=$(awk "BEGIN { printf \"%.1f\", ($level / $max) * 100 }")
    pct_int=${pct%.*}

    # Build a simple bar (20 chars wide)
    bar_filled=$((pct_int / 5))
    bar_empty=$((20 - bar_filled))
    bar=$(printf '%0.s█' $(seq 1 $bar_filled 2>/dev/null))
    bar="${bar}$(printf '%0.s░' $(seq 1 $bar_empty 2>/dev/null))"

    # Detect starter cartridge
    cartridge_note=""
    if [ "$i" -lt 3 ] && [ "$max" -lt "$FULL_CAPACITY_CMY" ]; then
        cartridge_note=" (starter)"
    elif [ "$i" -eq 3 ] && [ "$max" -lt "$FULL_CAPACITY_K" ]; then
        cartridge_note=" (starter)"
    fi

    printf "  %-12s %-14s %8d %8d %7.1f%%   %s\n" \
        "$name" "${desc}${cartridge_note}" "$max" "$level" "$pct" "$bar"
done

# Waste toner
waste_status="OK"
if [ "$WASTE_LEVEL" = "-3" ]; then
    waste_status="OK (sufficient)"
elif [ "$WASTE_LEVEL" = "-1" ]; then
    waste_status="Unknown"
elif [ "$WASTE_LEVEL" = "0" ]; then
    waste_status="FULL — replace!"
fi
printf "  %-12s %-14s %8s %8s %8s\n" "Waste Toner" "" "—" "—" "$waste_status"

print_separator
echo ""

# --- Coverage Estimation ---
echo "  ┌──────────────────────────────────────────────────────────────────┐"
echo "  │                    Coverage Estimation                          │"
echo "  └──────────────────────────────────────────────────────────────────┘"
echo ""
echo "  The SNMP max capacity is rated at 5% page coverage."
echo "  By comparing toner consumed vs pages printed, we can estimate"
echo "  your actual average coverage per color."
echo ""

printf "  %-12s %10s %10s %12s %12s\n" "Supply" "Consumed" "Pages" "Est.Cover" "Eff.Yield"
print_separator

for i in "${!SUPPLY_INDICES[@]}"; do
    name="${SUPPLY_NAMES[$i]}"
    max="${MAX[$i]}"
    level="${LEVEL[$i]}"

    if [ "$max" = "ERROR" ] || [ "$level" = "ERROR" ] || [ "$PAGE_COUNT" = "N/A" ] 2>/dev/null; then
        printf "  %-12s %10s %10s %12s %12s\n" "$name" "?" "?" "?" "?"
        continue
    fi

    # Toner consumed (in rated-page units)
    consumed=$((max - level))

    if [ "$consumed" -le 0 ] || [ "$PAGE_COUNT" -le 0 ] 2>/dev/null; then
        printf "  %-12s %10d %10s %12s %12s\n" "$name" "$consumed" "$PAGE_COUNT" "too early" "too early"
        continue
    fi

    # The consumed value represents how many "5% coverage pages" worth of
    # toner has been used. If we've printed more actual pages than toner
    # consumed, our coverage is below 5%.
    #
    # Formula: actual_coverage = (consumed / pages_printed) * 5%
    # Effective yield = how many pages this cartridge will actually last
    #                 = max_capacity * (pages_printed / consumed)

    coverage=$(awk "BEGIN { printf \"%.2f\", ($consumed / $PAGE_COUNT) * 5.0 }")
    eff_yield=$(awk "BEGIN { printf \"%.0f\", $max * ($PAGE_COUNT / $consumed) }")

    printf "  %-12s %10d %10s %10s%% %12s\n" \
        "$name" "$consumed" "$PAGE_COUNT" "$coverage" "$eff_yield"
done

print_separator
echo ""
echo "  Est.Cover  = estimated actual coverage percentage per page"
echo "  Eff.Yield  = estimated total pages per cartridge at your usage"
echo "  Consumed   = toner used, in 5%-coverage-page equivalents"
echo ""
echo "  Note: These estimates become more accurate over time as more"
echo "  toner is consumed. Results from a nearly-full cartridge will"
echo "  be unreliable. For best results, run this periodically and"
echo "  compare across cartridge replacements."
echo ""
