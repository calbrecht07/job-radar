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

- `sources/boards.py`: one adapter per job-board platform (Ashby, Greenhouse, Lever, Workable, Breezy, Recruitee, Personio, SmartRecruiters, BambooHR, Teamtailor, Trakstar, Rippling). `detect_boards(html)` finds board links on a careers page; `plausible()` rejects boards that belong to another company (VC pages linking portfolio jobs). Add a platform here: adapter function returning the common posting dict, entry in `ADAPTERS`, pattern in `BOARD_PATTERNS`, test in `tests/test_ats.py`.
- `sources/pages.py`: careers pages without a feed: job links, change detection.
- `sources/portfolio.py`: VC portfolio boards. Getro (POST api.getro.com, paged 12/20) and Consider (POST `<board>/api-boards/*`, paging ignored, `size=total`). Boards on neither platform return nothing: drop them from `settings.yaml` or add an adapter.
- `sources/news.py`: RSS funding/expansion news.
- `radar/filters.py`: `settings.yaml` → `Verdict(keep, pool, reason, flags)` per posting. Local vs remote pools, residency flags for single-country remote, drops, experience flags.
- `radar/scan.py`: builds targets (wishlist wins, index fills), fetches all feeds in threads, filters, writes `scan/`, `state/`, fetches job descriptions for the queue, calls `report.build`.
- `radar/verify.py`: liveness. `check_via_feed` (board feed membership, cached), `check_url` (404/410, redirect to board home, closed-job notices anywhere on the page via `GONE_STRONG`, title must appear in visible text or the result is "unverified"). Conservative: network trouble = live.
- `radar/report.py` + `radar/html.py`: `report/report.md`, `report.json`, self-contained `index.html` (the artifact page).
- `radar/companies.py`, `radar/discover.py`: weekly pool; Common Crawl harvest and hidden-board finder for wishlist companies.

## Known gaps / backlog

- Platforms without a public feed: Pinpoint, Workday, HiBob, Dover, Jobvite, iCIMS, SuccessFactors, Homerun, Join, Welcome to the Jungle/Otta. Companies on them are watched via careers-page change detection only.
- Full Common Crawl discovery has not yet run in the current layout (only `--skip-discovery` runs); the step is throttled hard by index.commoncrawl.org, hence the pacing in `discover.cc_get`.
- `scan/matches.json` is a 60-day archive; junk from an old bug (VC careers pages linking portfolio jobs) was purged by hand once. If it reappears, the fix belongs in `sources/pages.job_links` / `boards.plausible`.
- Snoopy's vault write needs the scheduled task bound to the person's Mac with the vault folder attached; otherwise it skips and logs it.

## Data repo layout (for reference)

`settings.yaml` (filters + company_search sources) · `wishlist.csv` (`name,kind,ats,slug,careers_url,source,note`) · `index.csv` (market pool of boards) · `judged.json` (`{id:{fit,note,url,reviewed,source}}`) · `extra_roles.json` (roles found off-feed) · `profile/{brief,rules,experience,handover}.md` · `agent/config.yaml` (person, repos, artifact URL, schedule, vault paths) · `pool/` `scan/` `state/` `report/` (workflow output) · `.github/workflows/{scan,companies}.yml`.
