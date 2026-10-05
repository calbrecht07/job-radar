# job-radar

A job-search radar that runs itself. It checks the job boards of the companies you care about every few hours, searches thousands of other companies' boards for the roles you want, and writes a report with a link to every matching role.

It reads the **public job feeds** companies use to power their careers pages (Ashby, Greenhouse, Lever, Workable, Breezy, Recruitee, Personio, SmartRecruiters), so new roles show up as soon as they're posted, often days before LinkedIn.

## How it works

| Layer | What | How |
|---|---|---|
| **1. Watchlist** | Companies you always want checked (`watchlist.csv`) | Every run: all their current matching roles. Companies without a feed: their careers page is checked for changes. Weekly: an audit of which job boards each careers page links to, so nothing is missed. |
| **2. Market search** | Every company in the market index (`index.csv`, thousands of boards) | Every run: new postings that match your settings (roles, location, on-site/hybrid/remote, company type, hard requirements, freshness). |
| **3. Discovery** | Grows the market index | Weekly: harvests job-board addresses from [Common Crawl](https://commoncrawl.org) and keeps boards that are hiring for your kind of role. You can also add boards by hand. |

Each run writes `report/report.md` (and `report.json`): watchlist companies with their matching roles, careers pages that changed, new market matches, and anything that needs attention. Every role links to the posting.

The filters are deliberately loose: they build a shortlist. A reviewer (you, or an AI agent) reads the job descriptions and marks roles in `judged.json` (`keep` / `stretch` / `cut`); the report shows the verdicts and hides cut roles.

## Set up your own

1. Create a **private** GitHub repo for your data (your settings and results stay private).
2. Copy into it:
   - `config.example/settings.yaml` → `settings.yaml` (edit: roles, city, remote regions, work modes, hard drops)
   - `config.example/watchlist.csv` → `watchlist.csv` (your target companies)
   - `config.example/index.csv` → `index.csv` (can start empty, header only)
   - `templates/workflows/*.yml` → `.github/workflows/`
3. In the data repo: **Settings → Actions → General → Workflow permissions → Read and write**.
4. Run the `radar` workflow once from the Actions tab, then `discover` to build the market index.

Finding a company's board: open its careers page and look at where the job links go (`jobs.ashbyhq.com/<slug>`, `boards.greenhouse.io/<slug>`, `jobs.lever.co/<slug>`, `apply.workable.com/<slug>`, `<slug>.breezy.hr`, `<slug>.recruitee.com`, `<slug>.jobs.personio.de`, `jobs.smartrecruiters.com/<slug>`). Leave `ats`/`slug` empty and set `careers_url` if it's none of those: the weekly audit will spot a supported board if the page links to one.

## Files in your data repo

| File | Written by | |
|---|---|---|
| `settings.yaml`, `watchlist.csv` | you | filters and target companies |
| `index.csv` | discovery (and you) | market-search boards; prefix a name with `#` to disable it |
| `inbox/add.csv` | you / an agent | `ats,slug,Company Name` lines to probe and add on the next discovery run |
| `judged.json` | reviewer | `{id: {"fit": "keep"/"stretch"/"cut", "note": "..."}}` |
| `report/report.md` | radar | the report |
| `output/pending.json` | radar | new matches waiting for review, with job descriptions |
| `output/watchlist.json`, `matches.json`, `health.json`, `board_audit.json` | radar | details behind the report |
| `state/` | radar | what's been seen, page snapshots |

## Running locally

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
python -m radar.run --data ../my-job-radar-data --dry-run      # fetch and print, write nothing
python -m radar.run --data ../my-job-radar-data --only "Encord"
python -m radar.report --data ../my-job-radar-data              # rebuild the report after reviewing
```

Be polite: the default schedule fetches each board six times a day, a load comparable to a person checking the careers page.
