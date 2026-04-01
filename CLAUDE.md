# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

Tracks WordPress.org plugin directory search rankings for **accessibility-plus** (https://wordpress.org/plugins/accessibility-plus/) against two competitors:
- `accessibility-checker` (Equalize Digital)
- `wp-accessibility` (Joe Dolson)

Each run queries the WordPress.org Plugin API for 32 keywords, records positions, generates a static HTML dashboard (`index.html`), and sends a daily Slack summary to the Mozilor workspace `#plugin-keyword-tracking` channel.

The dashboard is publicly hosted on GitHub Pages: https://safwana-wy.github.io/accessibility-keyword-tracker/

## Commands

```bash
# Full run: fetch positions, update dashboard, send Slack alert
python3 tracker.py

# Regenerate dashboard from existing data without hitting the API
python3 tracker.py --dry

# Install dependency
pip3 install requests
```

## Daily automation (cron at 8am)

```
0 8 * * * cd /Users/safwanata/Desktop/ClaudeProjects/accessibility-keyword-tracker && python3 tracker.py >> data/tracker.log 2>&1 && git add index.html data/positions.json && git commit -m "Daily update $(date +\%Y-\%m-\%d)" && git push >> data/tracker.log 2>&1
```

## Architecture

**`tracker.py`** — single-file Python script with four responsibilities:
1. `run_check()` — queries `https://api.wordpress.org/plugins/info/1.2/` for each keyword × slug combination, records 1-indexed positions
2. `generate_dashboard()` — writes `index.html` as a self-contained static page (no external dependencies)
3. `send_slack()` — posts a Block Kit message to the Slack webhook with daily summary, position changes, and competitor comparison
4. `send_email()` — optional HTML email alert (disabled by default)

**Data flow:**
- Positions are stored in `data/positions.json` with structure `{ "YYYY-MM-DD": { "slug": { "keyword": position_int_or_null } } }`
- `index.html` is committed to `main` branch and served via GitHub Pages — it updates every time the cron pushes
- Changes are detected by comparing today vs yesterday in the same JSON file

**Secrets handling:**
- `secrets.json` (gitignored) holds the Slack webhook URL under key `slack_webhook_url`
- `load_config()` merges secrets into the config at runtime — `config.json` contains no credentials and is safe to commit
- Never put the webhook URL or any credentials in `config.json`

## Adding or changing keywords

Edit the `KEYWORDS` list in `tracker.py` (lines 24–61). Keywords are grouped by category in comments. Each new keyword adds 3 API calls per run (one per slug). The 32-keyword × 3-slug current setup takes ~2.5 minutes to complete due to the 1.5s delay between requests.

## Adding a competitor

Add the slug to `competitors` in `config.json` and add a display label to `SLUG_LABELS` in `tracker.py`.

## Keyword context

Keywords were chosen based on competitor tag analysis (accessibility-checker, wp-accessibility) and WordPress.org search intent. Priority targets where the plugin is underperforming vs competitors:
- `accessibility plugin`, `wordpress accessibility`, `wcag plugin` — high volume, currently #8–#23
- `wp accessibility`, `a11y` — competitor brand terms
- `wcag 2.2`, `AODA` — currently not ranking at all; adding these to the plugin description would help
