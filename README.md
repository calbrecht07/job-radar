# job-radar

A job-search radar you hand to your AI assistant. The code finds the companies and the openings; one agent, **Snoopy**, judges them against your background and keeps a report with a link to every matching role. You answer the onboarding questions once, then it runs itself.

**Give this to your assistant:** "Set up job-radar for me. Read `agent/ONBOARDING.md` in github.com/calbrecht07/job-radar and follow it."

## Getting started

1. **Create your private data repo** and let your assistant follow `agent/ONBOARDING.md`: it asks what you want
   (roles, city, industries, limits) and writes your settings.
2. **Build your experience bank, once** (`agent/EXPERIENCE.md`). Your assistant interviews you role by role and
   writes every achievement in STAR form: **S**ituation, **T**ask, **A**ction, **R**esult, with the result
   measured where you can. Have a CV? Share it and the assistant drafts the bank from it, then only asks about
   the gaps (usually the numbers). Nothing is invented: what isn't in the bank never appears anywhere.
   The bank is `profile/experience.yaml`; `config.example/experience.yaml` is a fictional example.
3. **Let it run.** Snoopy judges every role against your experience bank; the report shows what fits.
4. **Tailored CV for a role you like:** ask your assistant, or run
   `python -m radar.cv --data <your data repo> --role <role id or link>`. It picks the achievements that match
   that job description, uses the closest summary, and writes a one-page CV to `cvs/` (HTML, and PDF with the
   headless browser installed). Your assistant can refine the choice and shorten bullets, never add facts.

## How it works

```
  weekly: COMPANY SEARCH            6x daily: OPPORTUNITY SEARCH          1-2x daily: SNOOPY (the agent)
  ┌─────────────────────────┐       ┌────────────────────────────────┐    ┌────────────────────────────┐
  │ your wishlist           │       │ every company in the pool:     │    │ reads the review queue     │
  │ VC portfolio boards     │ ───▶  │  job-board feeds (12 platforms)│ ─▶ │ judges: keep/stretch/cut   │
  │ funding news (RSS)      │ pool/ │  own-site careers pages        │    │ Monday: reviews new        │
  │ Common Crawl discovery  │       │  portfolio-board postings      │    │   companies for the        │
  │ hidden-board finder     │       │  directory careers pages       │    │   wishlist                 │
  │ company directory       │       │ filter → queue + descriptions  │    │                            │
  └─────────────────────────┘       └────────────────────────────────┘    │ publishes the report page  │
        GitHub Actions                      GitHub Actions                 │ writes your notes, notifies│
                                                                           └────────────────────────────┘
```

- **Company search** (`radar.companies`, weekly): pulls portfolio companies and jobs from VC job boards (Getro and Consider, the two platforms behind most of them), funding and expansion news from RSS feeds, job boards harvested from Common Crawl, and hidden boards for your wishlist companies. Writes `pool/`.
- **Company directory** (`radar.directory`, weekly): every company worth watching in your city, whatever its size: Wikidata (companies headquartered in your city, and companies in your free-text industries), directory pages (accelerators, associations, award lists, VC portfolios), the portfolio pool, and companies an agent researched for you on request (`agent/RESEARCH.md`). Each company's careers page is found and classified by how its jobs can be read. Feeds join `index.csv`; companies that post only on their own website are watched as pages. The directory is shared per city in a public repo, so the next person in the same city starts with it.
- **Opportunity search** (`radar.scan`, six times a day): reads every company's job board directly, so a role shows up within hours of posting. Reads careers pages of companies without a feed, and the portfolio-board postings of companies that only post on their own site. Loose keyword filters build the **review queue**, with each job description fetched.
- **Snoopy** (`agent/SNOOPY.md`): the only AI step. Reads the queue, applies your rules, writes verdicts, rebuilds and publishes the report. On Mondays it reviews the week's new companies and news and promotes the good ones to your wishlist. It never opens web pages, so it never needs approvals.

Two repos: this one (framework, public, no personal data) and your private **data repo** (settings, wishlist, pool, results, profile). Workflows in your data repo check this one out and run it.

## Supported job-board platforms

Ashby, Greenhouse, Lever, Workable, Breezy, Recruitee, Personio, SmartRecruiters, BambooHR, Teamtailor, Trakstar, Rippling, Workday. Portfolio boards: Getro, Consider. Any careers page that carries schema.org job data (the format Google Jobs reads) is read directly. Anything else: the careers page is read for job links, and each matching link is opened to find its location.

## Repo layout

| Path | What |
|---|---|
| `sources/boards.py` | one adapter per job-board platform, plus board detection on careers pages |
| `sources/pages.py` | own-site careers pages: job links, change detection |
| `sources/portfolio.py` | VC portfolio boards (Getro, Consider): companies and jobs |
| `sources/news.py` | funding / expansion news from RSS |
| `sources/jobdata.py` | schema.org job data in any page |
| `sources/wikidata.py` | companies by city and by industry |
| `sources/directories.py` | pages that list companies |
| `radar/careers.py` | careers discovery: website → careers page → read method |
| `radar/directory.py` | weekly company directory → shared directory, `index.csv`, pages to watch |
| `radar/companies.py` | weekly company search → `pool/` |
| `radar/discover.py` | Common Crawl harvest and hidden-board finder → `index.csv` |
| `radar/scan.py` | opportunity search → `scan/`, `state/` |
| `radar/filters.py` | your settings applied to postings |
| `radar/report.py`, `radar/html.py` | the report (markdown, JSON, page) |
| `agent/SNOOPY.md` | the agent's instructions |
| `agent/ONBOARDING.md` | how an assistant sets a new person up |
| `agent/RESEARCH.md` | finding companies and directories on request |
| `agent/EXPERIENCE.md` | building the experience bank with you; tailored CVs |
| `radar/cv.py` | tailored CVs from the experience bank |
| `radar/network.py` | your LinkedIn connections on every role |
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
| `inbox/research.csv`, `inbox/sources.csv` | research agent | companies and directory pages found on request; processed into `inbox/done/` |
| `pool/directory.csv`, `pool/directory.json` | company search | your copy of the city directory; run summary and companies with matching roles |
| `profile/experience.yaml` | you, with your assistant | your experience bank (STAR), the source for judging and CVs |
| `profile/brief.md`, `profile/rules.md` | you | who you are (generated from the bank); how to judge |
| `cvs/` | `radar.cv` | tailored CVs, on request |
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
