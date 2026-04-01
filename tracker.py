#!/usr/bin/env python3
"""
WordPress.org Keyword Position Tracker
Tracks search ranking for accessibility-plus and competitors in the WP.org plugin directory.

Usage:
  python tracker.py          # Full run: check positions, update dashboard, send Slack alert
  python tracker.py --dry    # Regenerate dashboard from existing data only (no API calls)
"""

import json
import os
import sys
import time
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# ---------------------------------------------------------------------------
# KEYWORD LIST
# ---------------------------------------------------------------------------
KEYWORDS = [
    # Broad category
    "accessibility plugin",
    "wordpress accessibility",
    "web accessibility",
    "accessibility checker",
    "accessibility tool",
    # Compliance standards
    "wcag plugin",
    "wcag compliance",
    "wcag 2.2",
    "ada compliance",
    "ada plugin",
    "section 508",
    "EAA compliance",
    "European Accessibility Act",
    "EN 301 549",
    "AODA",
    # Feature-specific
    "accessibility statement generator",
    "accessibility statement",
    "accessibility scan",
    "accessibility audit",
    "accessibility remediation",
    "accessibility issues wordpress",
    "color contrast plugin",
    "alt text plugin",
    "screen reader plugin",
    "keyboard navigation plugin",
    "skip links",
    "focus indicator",
    "accessibility toolbar",
    # Technical / developer
    "a11y",
    "wp accessibility",
    "ARIA plugin",
    "accessible wordpress",
]

# ---------------------------------------------------------------------------
# PATHS
# ---------------------------------------------------------------------------
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE   = os.path.join(BASE_DIR, "config.json")
SECRETS_FILE  = os.path.join(BASE_DIR, "secrets.json")
DATA_FILE   = os.path.join(BASE_DIR, "data", "positions.json")
DASHBOARD_FILE = os.path.join(BASE_DIR, "index.html")

SLUG_LABELS = {
    "accessibility-plus":    "Accessibility Plus",
    "accessibility-checker": "Accessibility Checker",
    "wp-accessibility":      "WP Accessibility",
}

# ---------------------------------------------------------------------------
# CONFIG / DATA HELPERS
# ---------------------------------------------------------------------------

def load_config():
    with open(CONFIG_FILE) as f:
        config = json.load(f)
    # Load secrets from secrets.json (gitignored) and merge in
    if os.path.exists(SECRETS_FILE):
        with open(SECRETS_FILE) as f:
            secrets = json.load(f)
        if secrets.get("slack_webhook_url"):
            config.setdefault("slack", {})["webhook_url"] = secrets["slack_webhook_url"]
        if secrets.get("email_password"):
            config.setdefault("email", {})["password"] = secrets["email_password"]
    return config


def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            return json.load(f)
    return {}


def save_data(data):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


def migrate_data(data, plugin_slug):
    """
    Migrate old flat structure  { date: { keyword: pos } }
    to new per-slug structure   { date: { slug: { keyword: pos } } }
    """
    migrated = False
    for date, day_data in data.items():
        # If any top-level value is int/None (not a dict), it's the old format
        sample = next(iter(day_data.values()), None)
        if not isinstance(sample, dict):
            data[date] = {plugin_slug: day_data}
            migrated = True
    if migrated:
        print("  [migrate] Old data structure converted to per-slug format.")
    return data


# ---------------------------------------------------------------------------
# WORDPRESS.ORG API
# ---------------------------------------------------------------------------

def check_position(keyword, slug, per_page=100, delay=1.5):
    """
    Return the 1-indexed position of slug in WP.org search for keyword.
    Returns None if not found in top per_page results, 'error' on failure.
    """
    url = "https://api.wordpress.org/plugins/info/1.2/"
    params = {
        "action":   "query_plugins",
        "search":   keyword,
        "per_page": per_page,
        "page":     1,
    }
    try:
        time.sleep(delay)
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        plugins = resp.json().get("plugins", [])
        for i, plugin in enumerate(plugins):
            if plugin.get("slug") == slug:
                return i + 1
        return None
    except Exception as exc:
        print(f"    [ERROR] '{keyword}' / '{slug}': {exc}")
        return "error"


# ---------------------------------------------------------------------------
# POSITION CHECK RUN
# ---------------------------------------------------------------------------

def run_check(config):
    plugin_slug = config["plugin_slug"]
    competitors  = config.get("competitors", [])
    all_slugs    = [plugin_slug] + competitors
    per_page     = config.get("results_per_page", 100)
    delay        = config.get("request_delay_seconds", 1.5)

    data = load_data()
    data = migrate_data(data, plugin_slug)

    today = datetime.utcnow().strftime("%Y-%m-%d")
    if today not in data:
        data[today] = {}
    for slug in all_slugs:
        if slug not in data[today]:
            data[today][slug] = {}

    dates     = sorted(k for k in data.keys() if k != today)
    yesterday = dates[-1] if dates else None

    changes = []  # position changes for plugin_slug only

    print(f"\nChecking {len(KEYWORDS)} keywords × {len(all_slugs)} slugs...\n")
    header = f"  {'KEYWORD':<40}" + "".join(f"  {s:<28}" for s in all_slugs)
    print(header)
    print("  " + "─" * (len(header) - 2))

    for keyword in KEYWORDS:
        row = f"  {keyword:<40}"

        for slug in all_slugs:
            position = check_position(keyword, slug, per_page=per_page, delay=delay)

            if position != "error":
                data[today][slug][keyword] = position

            pos_str = f"#{position}" if isinstance(position, int) else (str(position) if position else "—")
            row += f"  {pos_str:<28}"

        print(row)

        # Detect changes in our plugin only
        if yesterday:
            prev    = data[yesterday].get(plugin_slug, {}).get(keyword)
            current = data[today][plugin_slug].get(keyword)
            if (
                isinstance(prev, int)
                and isinstance(current, int)
                and prev != current
            ):
                changes.append({"keyword": keyword, "prev": prev, "current": current})

    save_data(data)
    print(f"\nData saved. {len(changes)} change(s) for '{plugin_slug}'.")
    return data, changes


# ---------------------------------------------------------------------------
# DASHBOARD
# ---------------------------------------------------------------------------

def _pos_display(pos):
    if isinstance(pos, int):
        return f"#{pos}"
    return "—"


def generate_dashboard(data, config):
    plugin_slug = config["plugin_slug"]
    competitors  = config.get("competitors", [])
    all_slugs    = [plugin_slug] + competitors

    dates     = sorted(data.keys())
    today     = dates[-1] if dates else None
    yesterday = dates[-2] if len(dates) >= 2 else None

    today_data = {slug: data[today].get(slug, {}) for slug in all_slugs} if today else {}
    our_data   = today_data.get(plugin_slug, {})

    # ── Summary stats ────────────────────────────────────────────────────────
    ranking  = [v for v in our_data.values() if isinstance(v, int)]
    top10    = sum(1 for v in ranking if v <= 10)
    top30    = sum(1 for v in ranking if v <= 30)
    not_rank = sum(1 for k in KEYWORDS if not isinstance(our_data.get(k), int))

    # Count keywords where we beat ALL competitors
    beating = 0
    losing  = 0
    for kw in KEYWORDS:
        our = our_data.get(kw)
        if not isinstance(our, int):
            continue
        comp_positions = [
            today_data[c].get(kw)
            for c in competitors
            if isinstance(today_data.get(c, {}).get(kw), int)
        ]
        if comp_positions:
            if our < min(comp_positions):
                beating += 1
            elif our > min(comp_positions):
                losing += 1

    # ── Sort keywords: ranking first (by position asc), not-ranking last ─────
    def sort_key(kw):
        pos = our_data.get(kw)
        return (0, pos) if isinstance(pos, int) else (1, 9999)
    sorted_kws = sorted(KEYWORDS, key=sort_key)

    # ── Competitor column headers ─────────────────────────────────────────────
    comp_headers = "".join(
        f'<th>{SLUG_LABELS.get(c, c)}</th>'
        for c in competitors
    )

    # ── Table rows ────────────────────────────────────────────────────────────
    rows_html = ""
    for kw in sorted_kws:
        our   = our_data.get(kw)
        prev  = data[yesterday].get(plugin_slug, {}).get(kw) if yesterday else None

        # Our position cell
        if not isinstance(our, int):
            our_cell  = '<span class="pos-none">Not in top 100</span>'
            row_class = "row-none"
            trend_html = '<span class="trend-none">—</span>'
        else:
            our_cell = f'<span class="pos-num">#{our}</span>'
            if prev is None:
                trend_html = '<span class="trend-new">New</span>'
                row_class  = "row-new"
            elif our < prev:
                trend_html = f'<span class="trend-up">↑ +{prev - our} <small>(was #{prev})</small></span>'
                row_class  = "row-up"
            elif our > prev:
                trend_html = f'<span class="trend-down">↓ −{our - prev} <small>(was #{prev})</small></span>'
                row_class  = "row-down"
            else:
                trend_html = '<span class="trend-stable">→</span>'
                row_class  = "row-stable"

        # Competitor cells
        comp_cells = ""
        for comp in competitors:
            comp_pos = today_data.get(comp, {}).get(kw)
            if not isinstance(comp_pos, int):
                comp_cells += '<td class="comp-cell comp-none">—</td>'
            elif not isinstance(our, int):
                comp_cells += f'<td class="comp-cell">#{comp_pos}</td>'
            elif our < comp_pos:
                comp_cells += f'<td class="comp-cell comp-win" title="You\'re #{our}, they\'re #{comp_pos}">#{comp_pos} <span class="badge-win">▲{comp_pos - our}</span></td>'
            elif our > comp_pos:
                comp_cells += f'<td class="comp-cell comp-lose" title="You\'re #{our}, they\'re #{comp_pos}">#{comp_pos} <span class="badge-lose">▼{our - comp_pos}</span></td>'
            else:
                comp_cells += f'<td class="comp-cell comp-tie">#{comp_pos} <span class="badge-tie">tie</span></td>'

        # History cells (last 7 days, our plugin only)
        hist_html = ""
        for date in dates[-7:]:
            pos = data[date].get(plugin_slug, {}).get(kw)
            if isinstance(pos, int):
                cls = "hist-top10" if pos <= 10 else ("hist-top30" if pos <= 30 else "")
                hist_html += f'<td class="hist-cell {cls}">#{pos}</td>'
            else:
                hist_html += '<td class="hist-cell hist-none">—</td>'

        rows_html += f"""
        <tr class="{row_class}">
          <td class="kw-cell">{kw}</td>
          <td class="pos-cell">{our_cell}</td>
          {comp_cells}
          <td class="trend-cell">{trend_html}</td>
          {hist_html}
        </tr>"""

    # History date headers
    hist_headers = "".join(
        f'<th class="hist-header">{d[5:]}</th>'
        for d in dates[-7:]
    )

    # ── Competitor comparison summary table ───────────────────────────────────
    comp_summary_rows = ""
    win_kws  = []
    lose_kws = []
    for kw in sorted_kws:
        our = our_data.get(kw)
        if not isinstance(our, int):
            continue
        row_parts = f'<td class="kw-cell">{kw}</td><td class="pos-cell"><span class="pos-num">#{our}</span></td>'
        overall = "neutral"
        for comp in competitors:
            comp_pos = today_data.get(comp, {}).get(kw)
            if not isinstance(comp_pos, int):
                row_parts += '<td class="comp-cell comp-none">—</td>'
            elif our < comp_pos:
                row_parts += f'<td class="comp-cell comp-win">#{comp_pos}</td>'
                overall = "win"
            elif our > comp_pos:
                row_parts += f'<td class="comp-cell comp-lose">#{comp_pos}</td>'
                if overall != "win":
                    overall = "lose"
            else:
                row_parts += f'<td class="comp-cell comp-tie">#{comp_pos}</td>'

        badge = ""
        if overall == "win":
            badge = '<span class="badge badge-green">Winning</span>'
            win_kws.append(kw)
        elif overall == "lose":
            badge = '<span class="badge badge-red">Behind</span>'
            lose_kws.append(kw)
        else:
            badge = '<span class="badge badge-gray">Neutral</span>'

        comp_summary_rows += f'<tr>{row_parts}<td>{badge}</td></tr>'

    comp_col_headers = "".join(
        f'<th>{SLUG_LABELS.get(c, c)}</th>'
        for c in competitors
    )

    last_updated = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Accessibility Plus – Keyword Tracker</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #f1f5f9; color: #1e293b; font-size: 14px;
    }}

    /* ── Header ─────────────────────────────────────────────── */
    .header {{
      background: linear-gradient(135deg, #1d4ed8, #2563eb);
      color: #fff; padding: 20px 32px;
      display: flex; justify-content: space-between; align-items: center;
    }}
    .header h1 {{ font-size: 1.2rem; font-weight: 700; letter-spacing: -.02em; }}
    .header p  {{ font-size: 0.78rem; opacity: .72; margin-top: 3px; }}
    .header .updated {{ font-size: 0.72rem; opacity: .6; text-align: right; }}

    /* ── Layout ─────────────────────────────────────────────── */
    .container {{ max-width: 1400px; margin: 0 auto; padding: 24px 32px; }}
    .section-title {{
      font-size: 0.78rem; font-weight: 700; text-transform: uppercase;
      letter-spacing: .07em; color: #94a3b8; margin: 28px 0 12px;
    }}

    /* ── Stat cards ─────────────────────────────────────────── */
    .stats {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 14px; margin-bottom: 28px; }}
    .card {{
      background: #fff; border-radius: 10px; padding: 18px 20px;
      box-shadow: 0 1px 3px rgba(0,0,0,.08);
    }}
    .card .value {{ font-size: 2rem; font-weight: 800; color: #2563eb; line-height: 1; }}
    .card .label {{ font-size: 0.7rem; text-transform: uppercase; letter-spacing: .06em; color: #94a3b8; margin-top: 5px; }}
    .card.c-green .value {{ color: #16a34a; }}
    .card.c-red   .value {{ color: #dc2626; }}
    .card.c-purple .value {{ color: #7c3aed; }}

    /* ── Table wrapper ──────────────────────────────────────── */
    .table-wrap {{
      background: #fff; border-radius: 10px;
      box-shadow: 0 1px 3px rgba(0,0,0,.08); overflow-x: auto;
    }}
    .table-title {{
      padding: 13px 18px; font-size: 0.82rem; font-weight: 600;
      color: #475569; border-bottom: 1px solid #e2e8f0; background: #f8fafc;
      display: flex; justify-content: space-between; align-items: center;
    }}
    .table-title .legend {{
      display: flex; gap: 16px; font-weight: 400; font-size: 0.75rem; color: #64748b;
    }}
    .legend-dot {{
      display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 4px;
    }}

    table {{ width: 100%; border-collapse: collapse; }}
    th {{
      padding: 9px 13px; text-align: left; font-size: 0.68rem;
      text-transform: uppercase; letter-spacing: .06em;
      color: #94a3b8; background: #f8fafc;
      border-bottom: 1px solid #e2e8f0; white-space: nowrap;
    }}
    td {{ padding: 9px 13px; border-bottom: 1px solid #f1f5f9; vertical-align: middle; }}
    tr:last-child td {{ border-bottom: none; }}
    tr:hover td {{ background: rgba(0,0,0,.015); }}

    .kw-cell   {{ font-weight: 500; min-width: 210px; }}
    .pos-cell  {{ font-size: .95rem; font-weight: 700; min-width: 90px; }}
    .trend-cell {{ min-width: 150px; }}

    .pos-num  {{ color: #1e293b; }}
    .pos-none {{ font-size: 0.75rem; color: #cbd5e1; font-weight: 400; }}

    .trend-up     {{ color: #16a34a; font-weight: 600; }}
    .trend-down   {{ color: #dc2626; font-weight: 600; }}
    .trend-stable {{ color: #94a3b8; }}
    .trend-new    {{ color: #7c3aed; font-weight: 600; }}
    .trend-none   {{ color: #cbd5e1; }}
    small         {{ font-weight: 400; opacity: .72; }}

    /* ── Row highlights ─────────────────────────────────────── */
    .row-down td {{ background: #fff5f5; }}
    .row-up   td {{ background: #f0fdf4; }}
    .row-new  td {{ background: #faf5ff; }}
    .row-none    {{ opacity: .6; }}

    /* ── Competitor cells ───────────────────────────────────── */
    .comp-cell {{ text-align: center; min-width: 130px; font-size: 0.82rem; }}
    .comp-none {{ color: #cbd5e1; }}
    .comp-win  {{ color: #15803d; background: #f0fdf4; }}
    .comp-lose {{ color: #b91c1c; background: #fff5f5; }}
    .comp-tie  {{ color: #6b7280; }}

    .badge-win  {{ font-size: 0.65rem; background: #dcfce7; color: #15803d; padding: 1px 5px; border-radius: 3px; margin-left: 3px; }}
    .badge-lose {{ font-size: 0.65rem; background: #fee2e2; color: #b91c1c; padding: 1px 5px; border-radius: 3px; margin-left: 3px; }}
    .badge-tie  {{ font-size: 0.65rem; background: #f3f4f6; color: #6b7280; padding: 1px 5px; border-radius: 3px; margin-left: 3px; }}

    /* ── Badge pills ────────────────────────────────────────── */
    .badge {{ font-size: 0.7rem; padding: 2px 8px; border-radius: 99px; font-weight: 600; }}
    .badge-green  {{ background: #dcfce7; color: #15803d; }}
    .badge-red    {{ background: #fee2e2; color: #b91c1c; }}
    .badge-gray   {{ background: #f3f4f6; color: #6b7280; }}

    /* ── History cells ──────────────────────────────────────── */
    .hist-header {{ text-align: center; }}
    .hist-cell   {{ text-align: center; color: #64748b; font-size: 0.75rem; min-width: 52px; }}
    .hist-top10  {{ color: #16a34a; font-weight: 700; }}
    .hist-top30  {{ color: #2563eb; font-weight: 600; }}
    .hist-none   {{ color: #e2e8f0; }}

    @media (max-width: 900px) {{
      .stats {{ grid-template-columns: repeat(3, 1fr); }}
      .container {{ padding: 16px; }}
    }}
  </style>
</head>
<body>

<header class="header">
  <div>
    <h1>Accessibility Plus — Keyword Position Tracker</h1>
    <p>WordPress.org Plugin Directory · {len(KEYWORDS)} keywords · vs {len(competitors)} competitor(s)</p>
  </div>
  <div class="updated">Last updated<br>{last_updated}</div>
</header>

<div class="container">

  <!-- ── Stats ── -->
  <div class="stats">
    <div class="card">
      <div class="value">{len(ranking)}/{len(KEYWORDS)}</div>
      <div class="label">Ranking</div>
    </div>
    <div class="card c-green">
      <div class="value">{top10}</div>
      <div class="label">In Top 10</div>
    </div>
    <div class="card">
      <div class="value">{top30}</div>
      <div class="label">In Top 30</div>
    </div>
    <div class="card c-red">
      <div class="value">{not_rank}</div>
      <div class="label">Not Ranking</div>
    </div>
    <div class="card c-green">
      <div class="value">{beating}</div>
      <div class="label">Beating Competitors</div>
    </div>
    <div class="card c-red">
      <div class="value">{losing}</div>
      <div class="label">Behind Competitors</div>
    </div>
  </div>

  <!-- ── Competitor Comparison ── -->
  <div class="section-title">Competitor Comparison</div>
  <div class="table-wrap" style="margin-bottom: 28px;">
    <div class="table-title">
      <span>Head-to-head: Your Position vs Competitors (per keyword)</span>
      <div class="legend">
        <span><span class="legend-dot" style="background:#16a34a"></span>Winning (your # is lower)</span>
        <span><span class="legend-dot" style="background:#dc2626"></span>Behind</span>
        <span><span class="legend-dot" style="background:#9ca3af"></span>Tied / No data</span>
      </div>
    </div>
    <table>
      <thead>
        <tr>
          <th>Keyword</th>
          <th>Your Position</th>
          {comp_col_headers}
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {comp_summary_rows}
      </tbody>
    </table>
  </div>

  <!-- ── Full Keyword Table ── -->
  <div class="section-title">All Keywords — Position History</div>
  <div class="table-wrap">
    <div class="table-title">
      <span>Daily positions · sorted by current rank</span>
      <div class="legend">
        <span><span class="legend-dot" style="background:#16a34a"></span>Improved</span>
        <span><span class="legend-dot" style="background:#dc2626"></span>Declined</span>
        <span><span class="legend-dot" style="background:#7c3aed"></span>New entry</span>
        <span><span class="legend-dot" style="background:#e2e8f0;border:1px solid #d1d5db"></span>Not top 100</span>
      </div>
    </div>
    <table>
      <thead>
        <tr>
          <th>Keyword</th>
          <th>Your Position</th>
          {comp_headers}
          <th>Change vs Yesterday</th>
          {hist_headers}
        </tr>
      </thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>
  </div>

</div><!-- /container -->
</body>
</html>"""

    with open(DASHBOARD_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Dashboard saved → {DASHBOARD_FILE}")


# ---------------------------------------------------------------------------
# SLACK NOTIFICATION
# ---------------------------------------------------------------------------

def send_slack(data, changes, config):
    slack_cfg = config.get("slack", {})
    if not slack_cfg.get("enabled"):
        return

    webhook_url = slack_cfg.get("webhook_url", "")
    if not webhook_url or "YOUR/WEBHOOK" in webhook_url:
        print("[SLACK] Skipped — no valid webhook URL in config.")
        return

    plugin_slug = config["plugin_slug"]
    competitors  = config.get("competitors", [])

    dates     = sorted(data.keys())
    today     = dates[-1] if dates else None
    today_data = {slug: data[today].get(slug, {}) for slug in [plugin_slug] + competitors} if today else {}
    our_data   = today_data.get(plugin_slug, {})

    ranking = [v for v in our_data.values() if isinstance(v, int)]
    top10   = sum(1 for v in ranking if v <= 10)
    top3    = sum(1 for v in ranking if v <= 3)

    declined = [c for c in changes if c["current"] > c["prev"]]
    improved = [c for c in changes if c["current"] < c["prev"]]

    # ── Competitor insight ────────────────────────────────────────────────────
    win_kws  = []
    lose_kws = []
    for kw in KEYWORDS:
        our = our_data.get(kw)
        if not isinstance(our, int):
            continue
        comp_pos = [
            today_data[c].get(kw)
            for c in competitors
            if isinstance(today_data.get(c, {}).get(kw), int)
        ]
        if not comp_pos:
            continue
        best = min(comp_pos)
        if our < best:
            win_kws.append((kw, our, best))
        elif our > best:
            lose_kws.append((kw, our, best))

    # ── Build Slack blocks ────────────────────────────────────────────────────
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🔍 Daily Keyword Report — Accessibility Plus"},
        },
        {
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": f"*{datetime.utcnow().strftime('%B %d, %Y')}*  ·  WordPress.org Plugin Directory  ·  {len(KEYWORDS)} keywords tracked",
            }],
        },
        {"type": "divider"},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Ranking:*\n{len(ranking)}/{len(KEYWORDS)} keywords"},
                {"type": "mrkdwn", "text": f"*In Top 10:*\n{top10} keywords"},
                {"type": "mrkdwn", "text": f"*In Top 3:*\n{top3} keywords"},
                {"type": "mrkdwn", "text": f"*Changes today:*\n↑ {len(improved)} improved · ↓ {len(declined)} declined"},
            ],
        },
    ]

    if declined:
        mention = slack_cfg.get("mention_on_decline", "")
        prefix  = f"{mention} " if mention and len(declined) >= 3 else ""
        lines   = [f"• *{c['keyword']}*  #{c['prev']} → #{c['current']} _(↓{c['current'] - c['prev']})_" for c in declined]
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{prefix}*📉 Declined ({len(declined)})*\n" + "\n".join(lines)},
        })

    if improved:
        lines = [f"• *{c['keyword']}*  #{c['prev']} → #{c['current']} _(↑{c['prev'] - c['current']})_" for c in improved]
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*📈 Improved ({len(improved)})*\n" + "\n".join(lines)},
        })

    if lose_kws:
        lines = [f"• *{kw}*  — You #{our}, best competitor #{best}" for kw, our, best in lose_kws[:6]]
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*⚠️ Behind a competitor ({len(lose_kws)} keywords)*\n" + "\n".join(lines)},
        })

    if win_kws:
        lines = [f"• *{kw}*  — You #{our}, best competitor #{best}" for kw, our, best in win_kws[:6]]
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*🏆 Beating all competitors ({len(win_kws)} keywords)*\n" + "\n".join(lines)},
        })

    blocks.append({"type": "divider"})
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": "Open `dashboard.html` for full history and visual comparison table."}],
    })

    try:
        resp = requests.post(webhook_url, json={"blocks": blocks}, timeout=10)
        if resp.status_code == 200:
            print("Slack notification sent.")
        else:
            print(f"[SLACK ERROR] {resp.status_code}: {resp.text}")
    except Exception as exc:
        print(f"[SLACK ERROR] {exc}")


# ---------------------------------------------------------------------------
# EMAIL ALERT
# ---------------------------------------------------------------------------

def send_email(changes, config):
    email_cfg = config.get("email", {})
    if not email_cfg.get("enabled") or not changes:
        return

    declined = [c for c in changes if c["current"] > c["prev"]]
    improved = [c for c in changes if c["current"] < c["prev"]]

    subject = (
        f"[Accessibility Plus] {len(declined)} keyword(s) declined · "
        f"{datetime.utcnow().strftime('%Y-%m-%d')}"
    )

    def table_rows(items, direction):
        rows = ""
        color = "#dc2626" if direction == "down" else "#16a34a"
        for c in items:
            delta = abs(c["current"] - c["prev"])
            arrow = "↓" if direction == "down" else "↑"
            rows += f"""<tr>
              <td style="padding:7px 12px;font-weight:500">{c["keyword"]}</td>
              <td style="padding:7px 12px;color:{color};font-weight:700">
                {arrow} #{c["current"]} <span style="color:#94a3b8;font-weight:400">(was #{c["prev"]}, Δ{delta})</span>
              </td></tr>"""
        return rows

    body = f"""<html><body style="font-family:-apple-system,sans-serif;max-width:600px;margin:0 auto;color:#1e293b">
    <div style="background:#2563eb;color:#fff;padding:18px 22px;border-radius:8px 8px 0 0">
      <h2 style="margin:0;font-size:1rem">Keyword Position Update</h2>
      <p style="margin:3px 0 0;opacity:.75;font-size:.8rem">accessibility-plus · {datetime.utcnow().strftime('%B %d, %Y')}</p>
    </div>
    <div style="padding:18px 22px;background:#fff;border:1px solid #e2e8f0;border-top:none;border-radius:0 0 8px 8px">
    {"<h3 style='color:#dc2626;margin:0 0 8px'>⬇ Declined</h3><table width='100%' style='border-collapse:collapse;background:#fff5f5;border-radius:6px'>" + table_rows(declined, "down") + "</table>" if declined else ""}
    {"<h3 style='color:#16a34a;margin:20px 0 8px'>⬆ Improved</h3><table width='100%' style='border-collapse:collapse;background:#f0fdf4;border-radius:6px'>" + table_rows(improved, "up") + "</table>" if improved else ""}
    <p style="margin-top:20px;font-size:.72rem;color:#94a3b8">Open dashboard.html for full history and competitor comparison.</p>
    </div></body></html>"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = email_cfg["from"]
        msg["To"]      = email_cfg["to"]
        msg.attach(MIMEText(body, "html"))
        with smtplib.SMTP_SSL(email_cfg["smtp_host"], email_cfg["smtp_port"]) as server:
            server.login(email_cfg["username"], email_cfg["password"])
            server.sendmail(email_cfg["from"], email_cfg["to"], msg.as_string())
        print(f"Email alert sent to {email_cfg['to']}")
    except Exception as exc:
        print(f"[EMAIL ERROR] {exc}")


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    dry_run = "--dry" in sys.argv
    config  = load_config()

    if dry_run:
        print("Dry run — regenerating dashboard from existing data.")
        data = migrate_data(load_data(), config["plugin_slug"])
        if not data:
            print("No data found. Run without --dry first.")
            sys.exit(1)
        generate_dashboard(data, config)
    else:
        data, changes = run_check(config)
        generate_dashboard(data, config)
        send_slack(data, changes, config)
        send_email(changes, config)

    print("\nDone.")
