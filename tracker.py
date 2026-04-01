#!/usr/bin/env python3
"""
WordPress.org Keyword Position Tracker
Tracks search rankings and active installs for multiple plugins and their competitors.

Usage:
  python tracker.py          # Full run: fetch positions + installs, update dashboard, send Slack
  python tracker.py --dry    # Regenerate dashboard from existing data only (no API calls)

To add a new plugin, add an entry to the "plugins" array in config.json.
Keyword positions are stored in data/positions.json.
Active installs use the _installs key alongside keyword positions.
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
# PATHS
# ---------------------------------------------------------------------------
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE    = os.path.join(BASE_DIR, "config.json")
SECRETS_FILE   = os.path.join(BASE_DIR, "secrets.json")
DATA_FILE      = os.path.join(BASE_DIR, "data", "positions.json")
DASHBOARD_FILE = os.path.join(BASE_DIR, "index.html")

# ---------------------------------------------------------------------------
# CONFIG / DATA HELPERS
# ---------------------------------------------------------------------------

def load_config():
    with open(CONFIG_FILE) as f:
        config = json.load(f)

    # Migrate old flat format { plugin_slug, competitors } → new plugins array
    if "plugin_slug" in config and "plugins" not in config:
        config["plugins"] = [{
            "slug":        config.pop("plugin_slug"),
            "name":        "My Plugin",
            "competitors": [
                {"slug": s, "name": s}
                for s in config.pop("competitors", [])
            ],
            "keywords": config.pop("keywords", []),
        }]

    # Merge secrets (gitignored)
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


def keyword_positions(slug_data):
    """Return only keyword→position entries, skipping internal _keys."""
    return {k: v for k, v in slug_data.items() if not k.startswith("_")}


def format_installs(n):
    if not isinstance(n, int):
        return "—"
    if n >= 1_000_000:
        return f"{n // 1_000_000}M+"
    if n >= 1_000:
        return f"{n // 1_000:,}K+"
    return f"{n:,}+"


# ---------------------------------------------------------------------------
# WORDPRESS.ORG API
# ---------------------------------------------------------------------------

def check_position(keyword, slug, per_page=100, delay=1.5):
    """Return 1-indexed position of slug in WP.org search results, or None/error."""
    url = "https://api.wordpress.org/plugins/info/1.2/"
    params = {"action": "query_plugins", "search": keyword, "per_page": per_page, "page": 1}
    try:
        time.sleep(delay)
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        for i, plugin in enumerate(resp.json().get("plugins", [])):
            if plugin.get("slug") == slug:
                return i + 1
        return None
    except Exception as exc:
        print(f"    [ERROR] position '{keyword}' / '{slug}': {exc}")
        return "error"


def fetch_installs(slug, delay=1.0):
    """Return active_installs integer for slug from WP.org Plugin API."""
    url = "https://api.wordpress.org/plugins/info/1.2/"
    params = {"action": "plugin_information", "slug": slug, "fields[active_installs]": 1}
    try:
        time.sleep(delay)
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get("active_installs")
    except Exception as exc:
        print(f"    [ERROR] installs '{slug}': {exc}")
        return None


# ---------------------------------------------------------------------------
# POSITION CHECK RUN
# ---------------------------------------------------------------------------

def run_check(config):
    data    = load_data()
    today   = datetime.utcnow().strftime("%Y-%m-%d")
    plugins = config["plugins"]
    delay   = config.get("request_delay_seconds", 1.5)
    per_pg  = config.get("results_per_page", 100)

    if today not in data:
        data[today] = {}

    all_changes = {}  # { plugin_slug: [changes] }

    for plugin_cfg in plugins:
        p_slug      = plugin_cfg["slug"]
        p_name      = plugin_cfg.get("name", p_slug)
        keywords    = plugin_cfg.get("keywords", [])
        competitors = plugin_cfg.get("competitors", [])
        all_slugs   = [p_slug] + [c["slug"] for c in competitors]

        # Ensure today's entries exist
        for slug in all_slugs:
            if slug not in data[today]:
                data[today][slug] = {}

        # Find yesterday for change detection
        dates_so_far = sorted(k for k in data if k != today)
        yesterday    = dates_so_far[-1] if dates_so_far else None

        changes = []

        print(f"\n{'─'*70}")
        print(f"  {p_name}  ({p_slug})")
        print(f"  {len(keywords)} keywords × {len(all_slugs)} slugs")
        print(f"{'─'*70}")
        header = f"  {'KEYWORD':<40}" + "".join(f"  {s:<26}" for s in all_slugs)
        print(header)
        print("  " + "─" * (len(header) - 2))

        for keyword in keywords:
            row = f"  {keyword:<40}"
            for slug in all_slugs:
                pos = check_position(keyword, slug, per_page=per_pg, delay=delay)
                if pos != "error":
                    data[today][slug][keyword] = pos
                pos_str = f"#{pos}" if isinstance(pos, int) else (str(pos) if pos else "—")
                row += f"  {pos_str:<26}"
            print(row)

            # Change detection (primary plugin only)
            if yesterday:
                prev    = keyword_positions(data[yesterday].get(p_slug, {})).get(keyword)
                current = keyword_positions(data[today].get(p_slug, {})).get(keyword)
                if isinstance(prev, int) and isinstance(current, int) and prev != current:
                    changes.append({"keyword": keyword, "prev": prev, "current": current})

        # Fetch active installs for all slugs
        print(f"\n  Fetching active installs...")
        for slug in all_slugs:
            installs = fetch_installs(slug, delay=delay)
            data[today][slug]["_installs"] = installs
            print(f"    {slug:<40}  {format_installs(installs)}")

        all_changes[p_slug] = changes
        print(f"\n  {len(changes)} position change(s) for '{p_slug}'.")

    save_data(data)
    print(f"\nAll data saved.")
    return data, all_changes


# ---------------------------------------------------------------------------
# DASHBOARD GENERATOR
# ---------------------------------------------------------------------------

def generate_dashboard(data, config):
    plugins = config["plugins"]
    dates   = sorted(data.keys())
    today   = dates[-1] if dates else None
    yesterday = dates[-2] if len(dates) >= 2 else None

    tabs_html    = ""
    content_html = ""

    for p_idx, plugin_cfg in enumerate(plugins):
        p_slug      = plugin_cfg["slug"]
        p_name      = plugin_cfg.get("name", p_slug)
        keywords    = plugin_cfg.get("keywords", [])
        competitors = plugin_cfg.get("competitors", [])
        comp_slugs  = [c["slug"] for c in competitors]
        all_slugs   = [p_slug] + comp_slugs

        our_raw   = data[today].get(p_slug, {}) if today else {}
        our_kws   = keyword_positions(our_raw)
        our_inst  = our_raw.get("_installs")

        prev_raw  = data[yesterday].get(p_slug, {}) if yesterday else {}
        prev_inst = prev_raw.get("_installs")

        # ── Stats ──────────────────────────────────────────────────────────
        ranking  = [v for v in our_kws.values() if isinstance(v, int)]
        top10    = sum(1 for v in ranking if v <= 10)
        top30    = sum(1 for v in ranking if v <= 30)
        not_rank = sum(1 for k in keywords if not isinstance(our_kws.get(k), int))

        beating = losing = 0
        for kw in keywords:
            our = our_kws.get(kw)
            if not isinstance(our, int):
                continue
            comp_pos = [
                keyword_positions(data[today].get(c, {})).get(kw)
                for c in comp_slugs
                if isinstance(keyword_positions(data[today].get(c, {})).get(kw), int)
            ] if today else []
            if comp_pos:
                if our < min(comp_pos):
                    beating += 1
                elif our > min(comp_pos):
                    losing += 1

        # Installs trend
        if isinstance(our_inst, int) and isinstance(prev_inst, int):
            inst_delta = our_inst - prev_inst
            if inst_delta > 0:
                inst_trend = f'<span style="color:#16a34a;font-size:.75rem">▲ +{format_installs(inst_delta)}</span>'
            elif inst_delta < 0:
                inst_trend = f'<span style="color:#dc2626;font-size:.75rem">▼ {format_installs(abs(inst_delta))}</span>'
            else:
                inst_trend = '<span style="color:#94a3b8;font-size:.75rem">No change</span>'
        else:
            inst_trend = ""

        # ── Sort keywords ──────────────────────────────────────────────────
        def sort_key(kw):
            pos = our_kws.get(kw)
            return (0, pos) if isinstance(pos, int) else (1, 9999)
        sorted_kws = sorted(keywords, key=sort_key)

        # ── Installs comparison row ────────────────────────────────────────
        installs_comp_cells = f'<td class="kw-cell" style="font-weight:700">Active Installs</td>'
        installs_comp_cells += f'<td class="pos-cell"><span class="inst-val">{format_installs(our_inst)}</span><br>{inst_trend}</td>'
        for comp in competitors:
            c_raw  = data[today].get(comp["slug"], {}) if today else {}
            c_inst = c_raw.get("_installs")
            p_c_raw  = data[yesterday].get(comp["slug"], {}) if yesterday else {}
            p_c_inst = p_c_raw.get("_installs")
            cell_cls = ""
            if isinstance(our_inst, int) and isinstance(c_inst, int):
                cell_cls = "comp-win" if our_inst > c_inst else "comp-lose"
            installs_comp_cells += f'<td class="comp-cell {cell_cls}" style="font-weight:600">{format_installs(c_inst)}</td>'

        # ── Competitor comparison table ────────────────────────────────────
        comp_col_headers = "".join(f'<th>{c["name"]}</th>' for c in competitors)

        comp_rows = f'<tr style="background:#f0f9ff">{installs_comp_cells}<td></td></tr>'
        for kw in sorted_kws:
            our = our_kws.get(kw)
            if not isinstance(our, int):
                continue
            row_parts = f'<td class="kw-cell">{kw}</td>'
            row_parts += f'<td class="pos-cell"><span class="pos-num">#{our}</span></td>'
            overall = "neutral"
            for comp in competitors:
                c_kws = keyword_positions(data[today].get(comp["slug"], {})) if today else {}
                c_pos = c_kws.get(kw)
                if not isinstance(c_pos, int):
                    row_parts += '<td class="comp-cell comp-none">—</td>'
                elif our < c_pos:
                    row_parts += f'<td class="comp-cell comp-win">#{c_pos} <span class="badge-win">▲{c_pos-our}</span></td>'
                    overall = "win"
                elif our > c_pos:
                    row_parts += f'<td class="comp-cell comp-lose">#{c_pos} <span class="badge-lose">▼{our-c_pos}</span></td>'
                    if overall != "win":
                        overall = "lose"
                else:
                    row_parts += f'<td class="comp-cell comp-tie">#{c_pos} <span class="badge-tie">tie</span></td>'

            badge = (
                '<span class="badge badge-green">Winning</span>' if overall == "win" else
                '<span class="badge badge-red">Behind</span>'    if overall == "lose" else
                '<span class="badge badge-gray">Neutral</span>'
            )
            comp_rows += f'<tr>{row_parts}<td>{badge}</td></tr>'

        # ── Full keyword table rows ────────────────────────────────────────
        comp_headers_full = "".join(f'<th>{c["name"]}</th>' for c in competitors)
        hist_headers      = "".join(f'<th class="hist-header">{d[5:]}</th>' for d in dates[-7:])

        kw_rows = ""
        for kw in sorted_kws:
            our  = our_kws.get(kw)
            prev = keyword_positions(prev_raw).get(kw) if yesterday else None

            if not isinstance(our, int):
                our_cell   = '<span class="pos-none">Not in top 100</span>'
                row_class  = "row-none"
                trend_html = '<span class="trend-none">—</span>'
            else:
                our_cell = f'<span class="pos-num">#{our}</span>'
                if prev is None:
                    trend_html = '<span class="trend-new">New</span>'; row_class = "row-new"
                elif our < prev:
                    trend_html = f'<span class="trend-up">↑ +{prev-our} <small>(was #{prev})</small></span>'; row_class = "row-up"
                elif our > prev:
                    trend_html = f'<span class="trend-down">↓ −{our-prev} <small>(was #{prev})</small></span>'; row_class = "row-down"
                else:
                    trend_html = '<span class="trend-stable">→</span>'; row_class = "row-stable"

            comp_cells = ""
            for comp in competitors:
                c_kws = keyword_positions(data[today].get(comp["slug"], {})) if today else {}
                c_pos = c_kws.get(kw)
                if not isinstance(c_pos, int):
                    comp_cells += '<td class="comp-cell comp-none">—</td>'
                elif not isinstance(our, int):
                    comp_cells += f'<td class="comp-cell">#{c_pos}</td>'
                elif our < c_pos:
                    comp_cells += f'<td class="comp-cell comp-win">#{c_pos} <span class="badge-win">▲{c_pos-our}</span></td>'
                elif our > c_pos:
                    comp_cells += f'<td class="comp-cell comp-lose">#{c_pos} <span class="badge-lose">▼{our-c_pos}</span></td>'
                else:
                    comp_cells += f'<td class="comp-cell comp-tie">#{c_pos} <span class="badge-tie">tie</span></td>'

            hist_cells = ""
            for date in dates[-7:]:
                pos = keyword_positions(data[date].get(p_slug, {})).get(kw)
                if isinstance(pos, int):
                    cls = "hist-top10" if pos <= 10 else ("hist-top30" if pos <= 30 else "")
                    hist_cells += f'<td class="hist-cell {cls}">#{pos}</td>'
                else:
                    hist_cells += '<td class="hist-cell hist-none">—</td>'

            kw_rows += f"""
            <tr class="{row_class}">
              <td class="kw-cell">{kw}</td>
              <td class="pos-cell">{our_cell}</td>
              {comp_cells}
              <td class="trend-cell">{trend_html}</td>
              {hist_cells}
            </tr>"""

        # ── Assemble tab ───────────────────────────────────────────────────
        active_cls = "active" if p_idx == 0 else ""

        tabs_html += f'<button class="tab {active_cls}" onclick="showTab({p_idx}, this)">{p_name}</button>'

        content_html += f"""
        <div id="tab-{p_idx}" class="tab-content {active_cls}">

          <!-- Stats -->
          <div class="stats">
            <div class="card c-blue">
              <div class="value">{format_installs(our_inst)}</div>
              <div class="label">Active Installs</div>
              <div style="margin-top:4px;min-height:16px">{inst_trend}</div>
            </div>
            <div class="card">
              <div class="value">{len(ranking)}/{len(keywords)}</div>
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
            <div class="card c-green">
              <div class="value">{beating}</div>
              <div class="label">Beating Competitors</div>
            </div>
            <div class="card c-red">
              <div class="value">{losing}</div>
              <div class="label">Behind Competitors</div>
            </div>
          </div>

          <!-- Competitor Comparison -->
          <div class="section-title">Competitor Comparison</div>
          <div class="table-wrap" style="margin-bottom:28px">
            <div class="table-title">
              <span>Head-to-head position and active installs per keyword</span>
              <div class="legend">
                <span><span class="legend-dot" style="background:#16a34a"></span>Winning</span>
                <span><span class="legend-dot" style="background:#dc2626"></span>Behind</span>
              </div>
            </div>
            <table>
              <thead><tr>
                <th>Keyword</th><th>Your Position</th>{comp_col_headers}<th>Status</th>
              </tr></thead>
              <tbody>{comp_rows}</tbody>
            </table>
          </div>

          <!-- Full keyword history -->
          <div class="section-title">All Keywords — Position History</div>
          <div class="table-wrap">
            <div class="table-title">
              <span>Daily positions · sorted by current rank</span>
              <div class="legend">
                <span><span class="legend-dot" style="background:#16a34a"></span>Improved</span>
                <span><span class="legend-dot" style="background:#dc2626"></span>Declined</span>
                <span><span class="legend-dot" style="background:#7c3aed"></span>New</span>
              </div>
            </div>
            <table>
              <thead><tr>
                <th>Keyword</th><th>Your Position</th>{comp_headers_full}
                <th>Change vs Yesterday</th>{hist_headers}
              </tr></thead>
              <tbody>{kw_rows}</tbody>
            </table>
          </div>

        </div>"""

    last_updated = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    plugin_count = len(plugins)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>WP Keyword Tracker</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f1f5f9; color: #1e293b; font-size: 14px; }}

    /* ── Header ── */
    .header {{ background: linear-gradient(135deg, #1d4ed8, #2563eb); color: #fff;
               padding: 18px 32px; display: flex; justify-content: space-between; align-items: center; }}
    .header h1 {{ font-size: 1.15rem; font-weight: 700; letter-spacing: -.02em; }}
    .header p  {{ font-size: 0.78rem; opacity: .72; margin-top: 3px; }}
    .header .updated {{ font-size: 0.72rem; opacity: .6; text-align: right; }}

    /* ── Tabs ── */
    .tab-bar {{ background: #1e40af; padding: 0 32px; display: flex; gap: 2px; }}
    .tab {{ background: transparent; border: none; color: rgba(255,255,255,.65);
             padding: 12px 20px; font-size: .85rem; font-weight: 500; cursor: pointer;
             border-bottom: 3px solid transparent; transition: all .15s; }}
    .tab:hover  {{ color: #fff; }}
    .tab.active {{ color: #fff; border-bottom-color: #fff; background: rgba(255,255,255,.1); }}

    /* ── Layout ── */
    .container {{ max-width: 1400px; margin: 0 auto; padding: 24px 32px; }}
    .tab-content {{ display: none; }}
    .tab-content.active {{ display: block; }}
    .section-title {{ font-size: .78rem; font-weight: 700; text-transform: uppercase;
                       letter-spacing: .07em; color: #94a3b8; margin: 28px 0 12px; }}

    /* ── Stat cards ── */
    .stats {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 14px; margin-bottom: 28px; }}
    .card {{ background: #fff; border-radius: 10px; padding: 18px 20px;
              box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
    .card .value {{ font-size: 1.75rem; font-weight: 800; color: #2563eb; line-height: 1; }}
    .card .label {{ font-size: .7rem; text-transform: uppercase; letter-spacing: .06em;
                    color: #94a3b8; margin-top: 5px; }}
    .card.c-green .value {{ color: #16a34a; }}
    .card.c-red   .value {{ color: #dc2626; }}
    .card.c-blue  .value {{ color: #0891b2; }}
    .inst-val {{ font-size: 1.75rem; font-weight: 800; color: #0891b2; }}

    /* ── Table wrapper ── */
    .table-wrap {{ background: #fff; border-radius: 10px;
                   box-shadow: 0 1px 3px rgba(0,0,0,.08); overflow-x: auto; }}
    .table-title {{ padding: 13px 18px; font-size: .82rem; font-weight: 600; color: #475569;
                    border-bottom: 1px solid #e2e8f0; background: #f8fafc;
                    display: flex; justify-content: space-between; align-items: center; }}
    .table-title .legend {{ display: flex; gap: 16px; font-weight: 400; font-size: .75rem; color: #64748b; }}
    .legend-dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 4px; }}

    table {{ width: 100%; border-collapse: collapse; }}
    th {{ padding: 9px 13px; text-align: left; font-size: .68rem; text-transform: uppercase;
           letter-spacing: .06em; color: #94a3b8; background: #f8fafc;
           border-bottom: 1px solid #e2e8f0; white-space: nowrap; }}
    td {{ padding: 9px 13px; border-bottom: 1px solid #f1f5f9; vertical-align: middle; }}
    tr:last-child td {{ border-bottom: none; }}
    tr:hover td {{ background: rgba(0,0,0,.012); }}

    .kw-cell   {{ font-weight: 500; min-width: 200px; }}
    .pos-cell  {{ font-size: .95rem; font-weight: 700; min-width: 90px; }}
    .trend-cell {{ min-width: 150px; }}
    .pos-num  {{ color: #1e293b; }}
    .pos-none {{ font-size: .75rem; color: #cbd5e1; font-weight: 400; }}

    .trend-up     {{ color: #16a34a; font-weight: 600; }}
    .trend-down   {{ color: #dc2626; font-weight: 600; }}
    .trend-stable {{ color: #94a3b8; }}
    .trend-new    {{ color: #7c3aed; font-weight: 600; }}
    .trend-none   {{ color: #cbd5e1; }}
    small         {{ font-weight: 400; opacity: .72; }}

    .row-down td {{ background: #fff5f5; }}
    .row-up   td {{ background: #f0fdf4; }}
    .row-new  td {{ background: #faf5ff; }}
    .row-none    {{ opacity: .6; }}

    .comp-cell  {{ text-align: center; min-width: 120px; font-size: .82rem; }}
    .comp-none  {{ color: #cbd5e1; }}
    .comp-win   {{ color: #15803d; background: #f0fdf4; }}
    .comp-lose  {{ color: #b91c1c; background: #fff5f5; }}
    .comp-tie   {{ color: #6b7280; }}

    .badge-win  {{ font-size: .65rem; background: #dcfce7; color: #15803d; padding: 1px 5px; border-radius: 3px; margin-left: 3px; }}
    .badge-lose {{ font-size: .65rem; background: #fee2e2; color: #b91c1c; padding: 1px 5px; border-radius: 3px; margin-left: 3px; }}
    .badge-tie  {{ font-size: .65rem; background: #f3f4f6; color: #6b7280; padding: 1px 5px; border-radius: 3px; margin-left: 3px; }}

    .badge       {{ font-size: .7rem; padding: 2px 8px; border-radius: 99px; font-weight: 600; }}
    .badge-green {{ background: #dcfce7; color: #15803d; }}
    .badge-red   {{ background: #fee2e2; color: #b91c1c; }}
    .badge-gray  {{ background: #f3f4f6; color: #6b7280; }}

    .hist-header {{ text-align: center; }}
    .hist-cell   {{ text-align: center; color: #64748b; font-size: .75rem; min-width: 52px; }}
    .hist-top10  {{ color: #16a34a; font-weight: 700; }}
    .hist-top30  {{ color: #2563eb; font-weight: 600; }}
    .hist-none   {{ color: #e2e8f0; }}

    @media (max-width: 900px) {{
      .stats {{ grid-template-columns: repeat(3, 1fr); }}
      .container {{ padding: 16px; }}
      .tab-bar {{ padding: 0 16px; }}
    }}
  </style>
</head>
<body>

<header class="header">
  <div>
    <h1>WordPress Plugin Keyword Tracker</h1>
    <p>{plugin_count} plugin(s) tracked · WordPress.org Plugin Directory</p>
  </div>
  <div class="updated">Last updated<br>{last_updated}</div>
</header>

<nav class="tab-bar">
  {tabs_html}
</nav>

<div class="container">
  {content_html}
</div>

<script>
function showTab(idx, btn) {{
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + idx).classList.add('active');
  btn.classList.add('active');
}}
</script>
</body>
</html>"""

    with open(DASHBOARD_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Dashboard saved → {DASHBOARD_FILE}")


# ---------------------------------------------------------------------------
# SLACK NOTIFICATION
# ---------------------------------------------------------------------------

def send_slack(data, all_changes, config):
    slack_cfg = config.get("slack", {})
    if not slack_cfg.get("enabled"):
        return
    webhook_url = slack_cfg.get("webhook_url", "")
    if not webhook_url or "YOUR/WEBHOOK" in webhook_url:
        print("[SLACK] Skipped — no valid webhook URL.")
        return

    dates    = sorted(data.keys())
    today    = dates[-1] if dates else None
    yesterday = dates[-2] if len(dates) >= 2 else None
    plugins  = config["plugins"]

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "📊 Daily WP Keyword Report"}},
        {"type": "context", "elements": [{"type": "mrkdwn",
            "text": f"*{datetime.utcnow().strftime('%B %d, %Y')}*  ·  WordPress.org Plugin Directory  ·  {len(plugins)} plugin(s)"}]},
        {"type": "divider"},
    ]

    for plugin_cfg in plugins:
        p_slug   = plugin_cfg["slug"]
        p_name   = plugin_cfg.get("name", p_slug)
        keywords = plugin_cfg.get("keywords", [])
        comp_slugs = [c["slug"] for c in plugin_cfg.get("competitors", [])]

        our_raw  = data[today].get(p_slug, {}) if today else {}
        our_kws  = keyword_positions(our_raw)
        our_inst = our_raw.get("_installs")

        prev_raw  = data[yesterday].get(p_slug, {}) if yesterday else {}
        prev_inst = prev_raw.get("_installs")

        ranking = [v for v in our_kws.values() if isinstance(v, int)]
        top10   = sum(1 for v in ranking if v <= 10)
        top3    = sum(1 for v in ranking if v <= 3)

        changes  = all_changes.get(p_slug, [])
        declined = [c for c in changes if c["current"] > c["prev"]]
        improved = [c for c in changes if c["current"] < c["prev"]]

        # Installs delta
        if isinstance(our_inst, int) and isinstance(prev_inst, int) and our_inst != prev_inst:
            delta = our_inst - prev_inst
            inst_str = f"{format_installs(our_inst)}  ({'▲' if delta > 0 else '▼'} {format_installs(abs(delta))})"
        else:
            inst_str = format_installs(our_inst)

        # Competitor wins/losses
        win_kws = lose_kws = []
        if today:
            win_kws  = []
            lose_kws = []
            for kw in keywords:
                our = our_kws.get(kw)
                if not isinstance(our, int):
                    continue
                comp_pos = [
                    keyword_positions(data[today].get(c, {})).get(kw)
                    for c in comp_slugs
                    if isinstance(keyword_positions(data[today].get(c, {})).get(kw), int)
                ]
                if not comp_pos:
                    continue
                best = min(comp_pos)
                if our < best:
                    win_kws.append((kw, our, best))
                elif our > best:
                    lose_kws.append((kw, our, best))

        plugin_blocks = [
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*{p_name}*\n`{p_slug}`"},
                {"type": "mrkdwn", "text": f"*Active Installs:*\n{inst_str}"},
                {"type": "mrkdwn", "text": f"*Ranking:*\n{len(ranking)}/{len(keywords)} keywords"},
                {"type": "mrkdwn", "text": f"*Top 10 / Top 3:*\n{top10} / {top3} keywords"},
                {"type": "mrkdwn", "text": f"*Changes:*\n↑ {len(improved)} improved · ↓ {len(declined)} declined"},
                {"type": "mrkdwn", "text": f"*vs Competitors:*\n🏆 {len(win_kws)} winning · ⚠️ {len(lose_kws)} behind"},
            ]},
        ]

        if declined:
            mention = slack_cfg.get("mention_on_decline", "")
            prefix  = f"{mention} " if mention and len(declined) >= 3 else ""
            lines   = [f"• *{c['keyword']}*  #{c['prev']} → #{c['current']} _(↓{c['current']-c['prev']})_" for c in declined]
            plugin_blocks.append({"type": "section",
                "text": {"type": "mrkdwn", "text": f"{prefix}*📉 Declined ({len(declined)})*\n" + "\n".join(lines)}})

        if improved:
            lines = [f"• *{c['keyword']}*  #{c['prev']} → #{c['current']} _(↑{c['prev']-c['current']})_" for c in improved]
            plugin_blocks.append({"type": "section",
                "text": {"type": "mrkdwn", "text": f"*📈 Improved ({len(improved)})*\n" + "\n".join(lines)}})

        if lose_kws:
            lines = [f"• *{kw}*  — You #{our}, best competitor #{best}" for kw, our, best in lose_kws[:5]]
            plugin_blocks.append({"type": "section",
                "text": {"type": "mrkdwn", "text": f"*⚠️ Behind a competitor*\n" + "\n".join(lines)}})

        blocks += plugin_blocks
        blocks.append({"type": "divider"})

    blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
        "text": "View full dashboard → https://safwana-wy.github.io/accessibility-keyword-tracker/"}]})

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

def send_email(all_changes, config):
    email_cfg = config.get("email", {})
    if not email_cfg.get("enabled"):
        return
    any_changes = any(v for v in all_changes.values())
    if not any_changes:
        return

    lines = []
    for plugin_cfg in config["plugins"]:
        p_slug = plugin_cfg["slug"]
        p_name = plugin_cfg.get("name", p_slug)
        changes = all_changes.get(p_slug, [])
        if not changes:
            continue
        declined = [c for c in changes if c["current"] > c["prev"]]
        improved = [c for c in changes if c["current"] < c["prev"]]
        lines.append(f"<h3>{p_name}</h3>")
        if declined:
            lines.append("<p style='color:#dc2626'>Declined: " +
                ", ".join(f"{c['keyword']} (#{c['prev']}→#{c['current']})" for c in declined) + "</p>")
        if improved:
            lines.append("<p style='color:#16a34a'>Improved: " +
                ", ".join(f"{c['keyword']} (#{c['prev']}→#{c['current']})" for c in improved) + "</p>")

    subject = f"[WP Keyword Tracker] Position changes · {datetime.utcnow().strftime('%Y-%m-%d')}"
    body    = f"<html><body style='font-family:sans-serif;max-width:600px;margin:0 auto'>" \
              f"<h2>Keyword Position Changes</h2>{''.join(lines)}</body></html>"

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = email_cfg["from"]
        msg["To"]      = email_cfg["to"]
        msg.attach(MIMEText(body, "html"))
        with smtplib.SMTP_SSL(email_cfg["smtp_host"], email_cfg["smtp_port"]) as server:
            server.login(email_cfg["username"], email_cfg["password"])
            server.sendmail(email_cfg["from"], email_cfg["to"], msg.as_string())
        print(f"Email sent to {email_cfg['to']}")
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
        data = load_data()
        if not data:
            print("No data found. Run without --dry first.")
            sys.exit(1)
        generate_dashboard(data, config)
    else:
        data, all_changes = run_check(config)
        generate_dashboard(data, config)
        send_slack(data, all_changes, config)
        send_email(all_changes, config)

    print("\nDone.")
