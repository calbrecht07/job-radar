# job-radar: notes for Claude Code

This is the **engine** of a two-repo system. It is public and must never contain personal data. The private
data repo (`../job-radar-carlos` for Carlos) holds settings, wishlist, results and profile; its GitHub
workflows check this repo out as `.engine` and run `PYTHONPATH=.engine python -m radar.<module> --data .`.

## What runs where (do not change without good reason)

| Piece | Where | When | Code |
|---|---|---|---|
| Company search | GitHub Actions in the data repo (`companies.yml`) | Mondays 03:23 UTC | `radar/companies.py`, `radar/discover.py`, `sources/portfolio.py`, `sources/news.py` |
| Opportunity search + liveness check + report | GitHub Actions in the data repo (`scan.yml`) | 6×/day | `radar/scan.py`, `radar/filters.py`, `radar/verify.py`, `radar/report.py`, `radar/html.py` |
| Snoopy (the only AI step) | Claude scheduled task, weekdays 10:00 & 17:00 Lisbon | reads the repo, judges the queue, publishes report page, writes Obsidian vault | `agent/SNOOPY.md`, `agent/VAULT.md` |
| Evening digest | the person's Claude chat, 19:00 Lisbon | reads `scan/snoopy_log.md` | `agent/templates/digest-prompt.md` |

Design rules that came from painful experience:
- **Snoopy never opens web pages.** Scheduled Claude runs ask for approval on every new domain; it was unusable. All fetching is in the workflows; Snoopy only reads files in the data repo.
- **Nothing is published unless confirmed live.** `radar/verify.py` runs inside `report.build()`. A role is live if it was in the latest feed scan (`state/live.json`) or its page passes `check_url`. Pages the code can't read (JS shells like Welcome to the Jungle/Otta) go to `report.unverified_roles`, shown separately as "check before applying", never in the main lists.
- **Only judged roles are published.** Unjudged roles sit in `scan/review_queue.json` until Snoopy writes a verdict to `judged.json`.
- One agent, one chat. Don't add scheduled tasks or new chats; extend Snoopy or the workflows.
- Agents never apply, email, or edit CVs/trackers; never state facts about the person not in `profile/`.

## Developing here

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q                                           # 37 tests, < 1 s
python -m radar.scan --data ../job-radar-carlos --dry-run     # full scan, nothing written (~40 s, 700 boards)
python -m radar.report --data ../job-radar-carlos             # verify + report.md/json + index.html
python -m radar.companies --data ../job-radar-carlos --skip-discovery
RADAR_SKIP_VERIFY=1 ...                                       # skip liveness check when offline
```

The job sites are reachable from a laptop, so iterate locally; the cloud sandbox Claude.ai uses cannot reach them (that's why development moved here). Don't commit anything under `../job-radar-carlos/{scan,state,pool,report}` from a local run unless you mean to; the workflows own those directories and a push race just causes a rebase.

After changing the engine: run tests, push, then dispatch `scan.yml` in the data repo
(`gh api -X POST repos/<owner>/<data-repo>/actions/workflows/scan.yml/dispatches -f ref=main`) and check the run;
GraphQL (`gh workflow run`) may be blocked, REST works.

## Module map

- `sources/boards.py`: one adapter per job-board platform (Ashby, Greenhouse, Lever, Workable, Breezy, Recruitee, Personio, SmartRecruiters, BambooHR, Teamtailor, Trakstar, Rippling, Workday). Workday slugs have two forms, `tenant.wdN/site` and `wdN/tenant/site`; big employers list 1,000+ postings, so `WORKDAY_MAX` caps paging and multi-location postings are resolved one by one (`WORKDAY_DETAIL_MAX`); their job pages need JavaScript, so `workday_description()` reads descriptions from the API. `detect_boards(html)` finds board links on a careers page; `plausible()` rejects boards that belong to another company (VC pages linking portfolio jobs). Add a platform here: adapter function returning the common posting dict, entry in `ADAPTERS`, pattern in `BOARD_PATTERNS`, test in `tests/test_ats.py`.
- `sources/pages.py`: careers pages without a feed: job links, change detection.
- `sources/portfolio.py`: VC portfolio boards. Getro (POST api.getro.com, paged 12/20) and Consider (POST `<board>/api-boards/*`, paging ignored, `size=total`). Boards on neither platform return nothing: drop them from `settings.yaml` or add an adapter.
- `sources/news.py`: RSS funding/expansion news.
- `sources/jobdata.py`: schema.org `JobPosting` blocks (JSON-LD) in any page: title, locations, remote, closing date. Used by discovery, the scan and `verify.py`.
- `sources/wikidata.py`: companies by city (HQ in the city or any district in it) and by free-text industry (search terms → industry items companies actually use, two subclass levels, filtered by shared word stems). The public endpoint times out at ~60 s, so queries are split small; never use property paths over big trees.
- `sources/directories.py`: pages listing companies: Getro/Consider portfolio boards, or any `list` page whose outbound links are the companies.
- `radar/careers.py`: careers discovery for one company: website → careers page (links, sitemap, common paths) → one hop to the job search page → read method (`feed`, `jobdata`, `enterprise`, `page`, `js_only`, `no_jobs`, `none`, `blocked`, `dead`). `own_jobs()` keeps only links that look like single postings (not careers navigation) and belong to the company.
- `radar/directory.py`: the company directory. Collects from Wikidata (city + industries), directory pages (settings + the shared `sources.csv`), the portfolio pool and research (`inbox/research.csv`, `inbox/sources.csv`), dedupes on domain then name, runs careers discovery (new first, research/portfolio/industry before size; `discovery_per_run`, `recheck_days`), writes the shared `cities/<city>/companies.csv` and the person's `pool/directory.csv`, feeds `index.csv` (source=directory) and `pool/directory_pages.json` (own-site pages the scan watches).
- `radar/filters.py`: `settings.yaml` → `Verdict(keep, pool, reason, flags)` per posting. Local vs remote pools, residency flags for single-country remote, drops, experience flags.
- `radar/scan.py`: builds targets (wishlist wins, index fills), fetches all feeds in threads, filters, writes `scan/`, `state/`, fetches job descriptions for the queue, calls `report.build`. Directory careers pages are checked in rotation (all once a day by default, `state/directory_pages.json`); a title match is opened (`located()`) to read the location from job data or page text, bounded by `directory_job_pages_per_run`, and unopened matches wait for the next run.
- `radar/verify.py`: liveness. `check_via_feed` (board feed membership, cached), `check_url` (404/410, redirect to board home, closed-job notices anywhere on the page via `GONE_STRONG`, title must appear in visible text or the result is "unverified"; schema.org job data with a passed `validThrough` = closed, a future one vouches for a JavaScript page). Conservative: network trouble = live.
- `radar/browser.py`: headless Chromium (Playwright, `requirements-browser.txt`) for the weekly company search only. `render()` returns the rendered HTML, every network request and the JSON responses; `jobs_from_json()` finds the job list in a page's own data calls. `careers.render_discover()` uses it for companies plain requests couldn't read (`RENDER_METHODS`): job board seen in network calls -> feed; else jobs from data calls or rendered posting links -> method `rendered`, jobs kept in `pool/rendered_jobs.json` and handed to the scan through `pool/directory_pages.json` (the scan never runs a browser). Wanted titles without a location get their job page rendered.
- `radar/vault.py`: one-way mirror into the person's notes vault (settings note, report note, alerts, log); quiet skip when the vault isn't reachable. GitHub is the source of truth; nothing is read back from the vault.
- `radar/report.py` + `radar/html.py`: `report/report.md`, `report.json`, self-contained `index.html` (the artifact page).
- `radar/companies.py`, `radar/discover.py`: weekly pool; Common Crawl harvest and hidden-board finder for wishlist companies.

## Known gaps / backlog

- Platforms without a feed adapter: SuccessFactors, Oracle, Phenom, Eightfold, iCIMS, Pinpoint, HiBob, Dover, Jobvite, Homerun, Join, Welcome to the Jungle/Otta. Discovery recognises them (`careers.ENTERPRISE`) and counts them in `pool/directory.json` → `enterprise_systems_without_adapter`: build adapters in that order. Meanwhile they're watched as pages.
- Company directory: Companies House (UK registry; needs a free API key) and Web Data Commons (yearly JobPosting extract from Common Crawl; large files) are not sources yet. Sites that refuse automated requests (HTTP 403, ~7% of companies) are marked `blocked`, and JavaScript-only careers pages `js_only`: both would need a headless browser.
- The shared directory repo is `calbrecht07/job-radar-directory` (public). Workflows read it; they write back only with a `DIRECTORY_TOKEN` secret, else each person keeps their copy in `pool/directory.csv`.
- Full Common Crawl discovery has not yet run in the current layout (only `--skip-discovery` runs); the step is throttled hard by index.commoncrawl.org, hence the pacing in `discover.cc_get`.
- `scan/matches.json` is a 60-day archive; junk from an old bug (VC careers pages linking portfolio jobs) was purged by hand once. If it reappears, the fix belongs in `sources/pages.job_links` / `boards.plausible`.
- GitHub's own `schedule:` trigger proved unreliable (one scheduled scan in a day, 2h20m late). The data repo is triggered by cron-job.org instead (see `agent/ONBOARDING.md`); the workflow schedules stay as a backup. If scans stop, check the token's expiry first.
- The vault mirror needs a run that can reach the vault (scheduled task bound to the Mac with the folder attached, or a session on the Mac); otherwise it skips and the next such run catches up.

## Data repo layout (for reference)

`settings.yaml` (filters + company_search sources) · `wishlist.csv` (`name,kind,ats,slug,careers_url,source,note`) · `index.csv` (market pool of boards) · `judged.json` (`{id:{fit,note,url,reviewed,source}}`) · `extra_roles.json` (roles found off-feed) · `profile/{brief,rules,experience,handover}.md` · `agent/config.yaml` (person, repos, artifact URL, schedule, vault paths) · `pool/` `scan/` `state/` `report/` (workflow output) · `.github/workflows/{scan,companies}.yml`.
