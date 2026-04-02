# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

Tracks WordPress.org plugin directory search rankings and active installs for multiple WebToffee plugins and their competitors. Each daily run queries the WordPress.org Plugin API, records keyword positions and active install counts, generates a tabbed HTML dashboard (`index.html`), and sends a Slack summary to the Mozilor workspace `#plugin-keyword-tracking` channel.

Dashboard (GitHub Pages): https://safwana-wy.github.io/accessibility-keyword-tracker/
GitHub repo: https://github.com/Safwana-WY/accessibility-keyword-tracker

## Plugins currently tracked

| Plugin | Slug | Competitors |
|---|---|---|
| Accessibility Plus | `accessibility-plus` | `accessibility-checker`, `wp-accessibility` |
| Alt Text Generator AI | `alt-text-generator` | `ai-alt-text-generator`, `ai-image-alt-text-generator-for-wp`, `alt-text-generator-gpt-vision` |
| AccessYes Accessibility Widget | `accessibility-widget` | `accessibe`, `accessibility-onetap`, `userway-accessibility-widget` |

## Commands

```bash
# Full run: fetch positions + installs, update dashboard, send Slack alert
python3 tracker.py

# Regenerate dashboard from existing data without hitting the API
python3 tracker.py --dry

# Install dependency
pip3 install requests
```

## Git workflow

After every change to the project (code, config, dashboard, docs), push to GitHub immediately:

```bash
git add <changed files>
git commit -m "Short description of change"
git push
```

Never leave changes uncommitted or unpushed at the end of a session. The GitHub Pages dashboard (`index.html`) is served directly from `main`, so changes only go live once pushed.

## Daily automation (cron at 8am)

```
0 8 * * * cd /Users/safwanata/Desktop/ClaudeProjects/accessibility-keyword-tracker && python3 tracker.py >> data/tracker.log 2>&1 && git add index.html data/positions.json && git commit -m "Daily update $(date +\%Y-\%m-\%d)" && git push >> data/tracker.log 2>&1
```

## Architecture

**`tracker.py`** — single-file Python script with four responsibilities:
1. `run_check()` — loops over all plugins in config, queries `https://api.wordpress.org/plugins/info/1.2/` for each keyword × slug, records 1-indexed positions; also calls `fetch_installs()` per slug
2. `generate_dashboard()` — writes `index.html` as a self-contained tabbed static page (one tab per plugin, no external dependencies)
3. `send_slack()` — posts a Block Kit message covering all plugins: installs, changes, competitor wins/losses
4. `send_email()` — optional HTML email alert (disabled by default)

**`config.json`** — single source of truth for all plugin definitions, keywords, and notification settings. No code changes needed to add plugins or keywords.

**Data flow:**
- Positions stored in `data/positions.json`: `{ "YYYY-MM-DD": { "slug": { "keyword": position, "_installs": N } } }`
- Keys starting with `_` are internal metadata (installs); `keyword_positions()` helper strips them when processing rankings
- `index.html` committed to `main` and served via GitHub Pages — updates every time the cron pushes
- Changes detected by comparing today vs yesterday per plugin

**Secrets handling:**
- `secrets.json` (gitignored) holds `slack_webhook_url` and optionally `email_password`
- `load_config()` merges secrets at runtime — `config.json` has no credentials and is safe to commit
- Never put credentials in `config.json`

## Adding a new plugin

Add an entry to the `plugins` array in `config.json`. No code changes required:

```json
{
  "slug": "your-plugin-slug",
  "name": "Display Name",
  "competitors": [
    {"slug": "competitor-slug", "name": "Competitor Name"}
  ],
  "keywords": ["keyword one", "keyword two"]
}
```

The slug must match exactly what appears in the WordPress.org URL: `wordpress.org/plugins/<slug>/`.

## Adding or changing keywords

Edit the `keywords` array for the relevant plugin in `config.json`. Each new keyword adds one API call per tracked slug per run. Current run time is ~8–10 minutes for all three plugins due to the 1.5s delay between requests.

## Keyword context

**Accessibility Plus** — Keywords chosen from competitor tag analysis (accessibility-checker, wp-accessibility). Priority gaps:
- `accessibility plugin`, `wordpress accessibility`, `wcag plugin` — high volume, currently #8–#23
- `wcag 2.2`, `AODA` — not ranking at all; adding to plugin description would help
- `wp accessibility`, `a11y` — competitor brand terms worth targeting

**Alt Text Generator AI** — Significant ground to make up; all competitors outrank on core terms:
- `alt text generator` — currently #18 vs competitors at #2–#4
- `image alt text`, `alt text` — ranking #45–#58, competitors at #7–#28
- `bulk alt text generator` — best current position (#15); prioritise defending this
- Active installs (20+) well behind competitors (600–1K+); early-stage plugin

**AccessYes Accessibility Widget** — Competing against well-established overlay tools:
- Targets the overlay/widget market segment (distinct from Accessibility Plus which is a code-level fixer)
- `accessibe alternative`, `userway alternative` — competitor brand searches worth targeting
- Active installs (10K+) vs OneTap (40K+) and UserWay (80K+)
- Tags used by competitors: `accessibility widget`, `web accessibility`, `wp accessibility`
