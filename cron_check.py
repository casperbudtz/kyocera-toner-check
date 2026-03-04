#!/usr/bin/env python3
"""
Kyocera Toner Monitor — daily cron snapshot + email notifications.

Queries all configured printers via SNMP and appends a "snapshot" history
entry to toner_log.json for each toner supply.  Run just before midnight so
you accumulate one data-point per day, giving the dashboard enough history to
estimate days-left and coverage trends.

If email notifications are enabled (kyocera_config.json), sends one email per
cartridge lifetime when any toner drops below the configured days threshold.
Deduplication is tracked in notify_sent.json — the alert fires once per
cartridge install and resets automatically when a cartridge is replaced.

Setup (run once):
    crontab -e
    # Add the following line:
    55 23 * * * /usr/bin/python3 /home/casper/Documents/Code/Kyocera/cron_check.py >> /home/casper/Documents/Code/Kyocera/cron.log 2>&1

Requires: snmpget  (sudo apt install snmp)
"""

import importlib.util
import json
import os
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Load monitor.py as a library module (mirrors how server.py uses it).
_spec   = importlib.util.spec_from_file_location("monitor", os.path.join(SCRIPT_DIR, "monitor.py"))
monitor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(monitor)

EMAIL_CONFIG_FILE  = Path(SCRIPT_DIR).parent / "email_config.json"
NOTIFY_SENT_FILE   = Path(SCRIPT_DIR) / "notify_sent.json"

_EMAIL_DEFAULTS = {
    "host": "", "port": 25, "from_email": "", "admin_email": "",
}


def _load_json(path, defaults):
    try:
        with open(path) as f:
            return {**defaults, **json.load(f)}
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(defaults)


def _save_notify_sent(data):
    tmp = str(NOTIFY_SENT_FILE) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, str(NOTIFY_SENT_FILE))


def _smtp_send_raw(cfg, msg):
    with smtplib.SMTP(cfg["host"], int(cfg["port"])) as s:
        s.send_message(msg)


def _pricerunner_url(product_model):
    if not product_model:
        return None
    q = "Kyocera " + product_model.replace("-", " ")
    encoded = q.replace(" ", "%20")
    return (f"https://www.pricerunner.dk/results?q={encoded}"
            f"&suggestionsActive=true&suggestionClicked=false&suggestionReverted=false")


def _send_alert_email(email_cfg, alerts):
    """Build and send a toner alert email.

    alerts: list of dicts with keys:
        printer_name, ip, supply_name, product_model, days_left, percent, install_timestamp
    """
    n       = len(alerts)
    subject = f"Kyocera Toner Alert — {n} supply{'s' if n > 1 else ''} low"

    # Group by printer for readability
    by_printer = {}
    for a in alerts:
        by_printer.setdefault((a["printer_name"], a["ip"]), []).append(a)

    # ── Plain-text body ───────────────────────────────────────────────────────
    text_lines = ["The following toner cartridges need attention:\n"]
    for (pname, ip), supplies in by_printer.items():
        text_lines.append(f"{pname} ({ip}):")
        for s in supplies:
            model = s.get("product_model") or "—"
            days  = s.get("days_left", "?")
            pct   = s.get("percent", 0)
            text_lines.append(f"  • {s['supply_name']} ({model}) — {days}d left ({pct:.1f}%)")
            url = _pricerunner_url(s.get("product_model"))
            if url:
                text_lines.append(f"    Order: {url}")
        text_lines.append("")
    text_lines.append("---\nKyocera Toner Monitor — Command Central")
    text_body = "\n".join(text_lines)

    # ── HTML body ─────────────────────────────────────────────────────────────
    TONER_COLORS = {
        "Cyan": "#00bcd4", "Magenta": "#d81b60",
        "Yellow": "#f9a825", "Black": "#424242",
    }

    rows_html = ""
    for (pname, ip), supplies in by_printer.items():
        rows_html += f"""
        <tr>
          <td colspan="4"
              style="padding:10px 12px 4px;background:#f3f4f6;font-weight:700;
                     font-size:.85rem;color:#1a1a2e;border-top:2px solid #e0e4ea;">
            {pname} <span style="font-weight:400;color:#6b7280;font-size:.8rem">({ip})</span>
          </td>
        </tr>"""
        for s in supplies:
            model    = s.get("product_model") or "—"
            days     = s.get("days_left", "?")
            pct      = s.get("percent", 0)
            dot_col  = TONER_COLORS.get(s["supply_name"], "#999")
            day_col  = "#dc2626" if isinstance(days, int) and days < 7 else "#d97706"
            url      = _pricerunner_url(s.get("product_model"))
            link     = f'<a href="{url}" style="color:#0d6efd">Search PriceRunner.dk ↗</a>' if url else "—"
            rows_html += f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #f3f4f6;vertical-align:middle">
            <span style="display:inline-block;width:10px;height:10px;border-radius:50%;
                         background:{dot_col};margin-right:6px;vertical-align:middle"></span>
            {s["supply_name"]}
          </td>
          <td style="padding:8px 12px;border-bottom:1px solid #f3f4f6;font-family:monospace">{model}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #f3f4f6;
                     font-weight:700;color:{day_col}">{days}d / {pct:.1f}%</td>
          <td style="padding:8px 12px;border-bottom:1px solid #f3f4f6;font-size:.8rem">{link}</td>
        </tr>"""

    html_body = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
             background:#f0f2f5;margin:0;padding:24px;color:#1a1a2e">
  <div style="max-width:620px;margin:0 auto">
    <div style="background:#1a1a2e;color:#fff;padding:16px 20px;border-radius:10px 10px 0 0">
      <div style="font-size:1rem;font-weight:700">Kyocera Toner Alert</div>
      <div style="font-size:.78rem;opacity:.6;margin-top:2px">{n} supply{'s' if n > 1 else ''} below threshold</div>
    </div>
    <div style="background:#fff;border:1px solid #e0e4ea;border-top:none;
                border-radius:0 0 10px 10px;overflow:hidden">
      <table width="100%" cellpadding="0" cellspacing="0"
             style="border-collapse:collapse;font-size:.85rem">
        <thead>
          <tr style="background:#f8f9fa">
            <th style="padding:8px 12px;text-align:left;font-size:.72rem;color:#6b7280;
                       text-transform:uppercase;letter-spacing:.06em;border-bottom:1px solid #e0e4ea">
              Supply</th>
            <th style="padding:8px 12px;text-align:left;font-size:.72rem;color:#6b7280;
                       text-transform:uppercase;letter-spacing:.06em;border-bottom:1px solid #e0e4ea">
              Product</th>
            <th style="padding:8px 12px;text-align:left;font-size:.72rem;color:#6b7280;
                       text-transform:uppercase;letter-spacing:.06em;border-bottom:1px solid #e0e4ea">
              Days Left</th>
            <th style="padding:8px 12px;text-align:left;font-size:.72rem;color:#6b7280;
                       text-transform:uppercase;letter-spacing:.06em;border-bottom:1px solid #e0e4ea">
              Order</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
      <div style="padding:14px 16px;font-size:.75rem;color:#6b7280;
                  border-top:1px solid #f3f4f6;background:#f8f9fa">
        Kyocera Toner Monitor — Command Central
      </div>
    </div>
  </div>
</body></html>"""

    # ── Assemble and send ─────────────────────────────────────────────────────
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = email_cfg["from_email"]
    msg["To"]      = email_cfg["admin_email"]
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))
    _smtp_send_raw(email_cfg, msg)


def _check_notifications(results):
    """Send alert email for supplies newly below threshold (once per cartridge lifetime)."""
    kyocera_cfg = monitor.load_config()
    if not kyocera_cfg.get("notify_enabled"):
        return

    email_cfg = _load_json(EMAIL_CONFIG_FILE, _EMAIL_DEFAULTS)
    if not email_cfg["host"] or not email_cfg["admin_email"]:
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] NOTIFY  Skipping — SMTP not configured",
              file=sys.stderr)
        return

    threshold    = int(kyocera_cfg.get("notify_days_threshold", 7))
    notify_sent  = _load_json(NOTIFY_SENT_FILE, {})
    new_alerts   = []
    updated_sent = {k: dict(v) for k, v in notify_sent.items()}  # deep copy

    for ip, result in results.items():
        if result.get("error"):
            continue
        printer_name = result.get("printer_name", ip)
        for s in result.get("supplies", []):
            if s.get("error"):
                continue
            days_left = s.get("days_left")
            if days_left is None or days_left > threshold:
                continue

            supply_name       = s["name"]
            install_timestamp = s.get("install_timestamp", "")

            # Check deduplication: was this cartridge's threshold already notified?
            already_notified = (
                notify_sent.get(ip, {}).get(supply_name) == install_timestamp
            )
            if already_notified:
                continue

            new_alerts.append({
                "printer_name":    printer_name,
                "ip":              ip,
                "supply_name":     supply_name,
                "product_model":   s.get("product_model"),
                "days_left":       days_left,
                "percent":         s.get("percent", 0),
                "install_timestamp": install_timestamp,
            })
            updated_sent.setdefault(ip, {})[supply_name] = install_timestamp

    if not new_alerts:
        return

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        _send_alert_email(email_cfg, new_alerts)
        _save_notify_sent(updated_sent)
        print(f"[{ts}] NOTIFY  Alert sent — {len(new_alerts)} supply{'s' if len(new_alerts)>1 else ''} below {threshold}d threshold")
    except Exception as e:
        print(f"[{ts}] NOTIFY  Failed to send alert email: {e}", file=sys.stderr)


def main():
    printers = monitor.load_printers()
    if not printers:
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] No printers configured — nothing to do.",
              file=sys.stderr)
        return 0

    errors  = 0
    results = {}  # ip → result (for notification check)

    for p in printers:
        ip   = p["ip"]
        name = p.get("name", ip)
        result = monitor.check_printer(ip, record_snapshot=True)
        result["printer_name"] = name
        results[ip] = result

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

    _check_notifications(results)
    return errors


if __name__ == "__main__":
    sys.exit(main())
