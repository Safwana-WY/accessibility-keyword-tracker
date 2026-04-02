# Accessibility Keyword Tracker

Tracks WordPress.org plugin directory search rankings and active installs for WebToffee accessibility plugins and their competitors. Runs daily, generates a live dashboard, and sends a Slack summary.

**Live dashboard:** https://safwana-wy.github.io/accessibility-keyword-tracker/

---

## Plugins tracked

| Plugin | Slug | Competitors |
|---|---|---|
| Accessibility Plus | `accessibility-plus` | Accessibility Checker, WP Accessibility |
| Alt Text Generator AI | `alt-text-generator` | AI Alt Text Generator, AI Image Alt Text for WP, Alt Text GPT Vision |
| AccessYes Accessibility Widget | `accessibility-widget` | accessiBe, Accessibility OneTap, UserWay |

---

## How it works

Each daily run:
1. Queries the WordPress.org Plugin API for each keyword × plugin slug combination
2. Records 1-indexed search positions and active install counts in `data/positions.json`
3. Generates a tabbed HTML dashboard (`index.html`) served via GitHub Pages
4. Sends a Slack summary to the Mozilor `#plugin-keyword-tracking` channel with installs, position changes, and competitor comparisons

---

## Setup

### 1. Install dependencies

```bash
pip3 install requests
```

### 2. Configure secrets

Create `secrets.json` in the project root (this file is gitignored):

```json
{
  "slack_webhook_url": "https://hooks.slack.com/services/..."
}
```

### 3. Run manually

```bash
# Full run: fetch positions + installs, update dashboard, send Slack alert
python3 tracker.py

# Regenerate dashboard from existing data without hitting the API
python3 tracker.py --dry
```

---

## Automation

A cron job runs the tracker daily at 8am, updates the dashboard, and pushes to GitHub:

```
0 8 * * * cd /path/to/accessibility-keyword-tracker && python3 tracker.py >> data/tracker.log 2>&1 && git add index.html data/positions.json && git commit -m "Daily update $(date +\%Y-\%m-\%d)" && git push >> data/tracker.log 2>&1
```

---

## Configuration

All plugin definitions, keywords, and notification settings live in `config.json` — no code changes needed.

### Adding a new plugin

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

The slug must match the WordPress.org URL: `wordpress.org/plugins/<slug>/`

### Adding or changing keywords

Edit the `keywords` array for the relevant plugin in `config.json`. Each keyword adds one API call per tracked slug per run. Current run time is ~8–10 minutes for all three plugins due to a 1.5s delay between requests.

---

## Project structure

```
accessibility-keyword-tracker/
├── tracker.py          # Main script
├── config.json         # Plugin definitions, keywords, notification settings
├── secrets.json        # Slack webhook (gitignored)
├── index.html          # Generated dashboard (served via GitHub Pages)
├── requirements.txt    # Python dependencies
└── data/
    ├── positions.json  # Historical rankings and install counts
    └── tracker.log     # Cron run logs
```

---

## Data format

Positions are stored in `data/positions.json`:

```json
{
  "YYYY-MM-DD": {
    "plugin-slug": {
      "keyword one": 5,
      "keyword two": 12,
      "_installs": 10000
    }
  }
}
```

Keys starting with `_` are internal metadata (e.g. `_installs`). Position values are 1-indexed; a plugin not found in the top 100 results is not recorded.
