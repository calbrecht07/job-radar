# job-radar

A job-search radar you hand to your AI assistant. The code finds the companies and the openings; one agent, **Snoopy**, judges them against your background and keeps a report with a link to every matching role. You answer the onboarding questions once, then it runs itself.

**Give this to your assistant:** "Set up job-radar for me. Read `agent/ONBOARDING.md` in github.com/calbrecht07/job-radar and follow it."

## How it works

```
  weekly: COMPANY SEARCH            6x daily: OPPORTUNITY SEARCH          1-2x daily: SNOOPY (the agent)
  ┌─────────────────────────┐       ┌────────────────────────────────┐    ┌────────────────────────────┐
  │ your wishlist           │       │ every company in the pool:     │    │ reads the review queue     │
  │ VC portfolio boards     │ ───▶  │  job-board feeds (12 platforms)│ ─▶ │ judges: keep/stretch/cut   │
  │ funding news (RSS)      │ pool/ │  own-site careers pages        │    │ Monday: reviews new        │
  │ Common Crawl discovery  │       │  portfolio-board postings      │    │   companies for the        │
  │ hidden-board finder     │       │ filter → queue + descriptions  │    │   wishlist                 │
  └─────────────────────────┘       └────────────────────────────────┘    │ publishes the report page  │
        GitHub Actions                      GitHub Actions                 │ writes your notes, notifies│
                                                                           └────────────────────────────┘
```

- **Company search** (`radar.companies`, weekly): pulls portfolio companies and jobs from VC job boards (Getro and Consider, the two platforms behind most of them), funding and expansion news from RSS feeds, job boards harvested from Common Crawl, and hidden boards for your wishlist companies. Writes `pool/`.
- **Opportunity search** (`radar.scan`, six times a day): reads every company's job board directly, so a role shows up within hours of posting. Reads careers pages of companies without a feed, and the portfolio-board postings of companies that only post on their own site. Loose keyword filters build the **review queue**, with each job description fetched.
- **Snoopy** (`agent/SNOOPY.md`): the only AI step. Reads the queue, applies your rules, writes verdicts, rebuilds and publishes the report. On Mondays it reviews the week's new companies and news and promotes the good ones to your wishlist. It never opens web pages, so it never needs approvals.

Two repos: this one (framework, public, no personal data) and your private **data repo** (settings, wishlist, pool, results, profile). Workflows in your data repo check this one out and run it.

## Supported job-board platforms

Ashby, Greenhouse, Lever, Workable, Breezy, Recruitee, Personio, SmartRecruiters, BambooHR, Teamtailor, Trakstar, Rippling. Portfolio boards: Getro, Consider. Anything else: the careers page is read for job links and watched for changes.

## Repo layout

| Path | What |
|---|---|
| `sources/boards.py` | one adapter per job-board platform, plus board detection on careers pages |
| `sources/pages.py` | own-site careers pages: job links, change detection |
| `sources/portfolio.py` | VC portfolio boards (Getro, Consider): companies and jobs |
| `sources/news.py` | funding / expansion news from RSS |
| `radar/companies.py` | weekly company search → `pool/` |
| `radar/discover.py` | Common Crawl harvest and hidden-board finder → `index.csv` |
| `radar/scan.py` | opportunity search → `scan/`, `state/` |
| `radar/filters.py` | your settings applied to postings |
| `radar/report.py`, `radar/html.py` | the report (markdown, JSON, page) |
| `agent/SNOOPY.md` | the agent's instructions |
| `agent/ONBOARDING.md` | how an assistant sets a new person up |
| `agent/VAULT.md` | optional: writing results into a notes app |
| `agent/templates/` | config, profile, rules and scheduled-task prompt templates |
| `config.example/` | example `settings.yaml`, `wishlist.csv`, `index.csv` |
| `templates/workflows/` | the two GitHub workflows for your data repo |

## Your data repo

| File | Written by | |
|---|---|---|
| `settings.yaml` | you | roles, locations, work modes, hard drops, company-search sources |
| `wishlist.csv` | you, Snoopy | companies always checked: `name,kind,ats,slug,careers_url,source,note` |
| `index.csv` | company search | the wider market pool (boards from discovery and portfolio links); `#name` disables a row |
| `inbox/add.csv` | you | `ats,slug,Company` lines to add on the next company search |
| `profile/brief.md`, `profile/rules.md` | you | who you are; how to judge |
| `agent/config.yaml` | you | where things are (repos, artifact, schedule, optional vault) |
| `pool/` | company search | `companies.json` (the unified pool), `portfolio.json`, `portfolio_jobs.json`, `news.json`, `new_this_week.json` |
| `scan/` | opportunity search, Snoopy | `review_queue.json`, `pending.json`, `watchlist.json`, `health.json`, `snoopy_log.md` |
| `judged.json`, `extra_roles.json` | Snoopy | verdicts; roles found outside the feeds |
| `report/` | both | `report.md`, `report.json`, `index.html` |
| `state/` | opportunity search | what has been seen, page snapshots |

## Running locally

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
python -m radar.companies --data ../my-data --skip-discovery
python -m radar.scan --data ../my-data --dry-run
python -m radar.report --data ../my-data
```

Be polite to the sites you read: the default schedule reads each board six times a day, about what a person checking a careers page would do.
