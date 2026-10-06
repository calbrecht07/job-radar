# Onboarding: set up job-radar for a new person

You are helping someone set up their own job radar. Work through this with them, then create their data repo. The whole setup is: one private data repo, two GitHub workflows, one scheduled agent (Snoopy), one report page.

## 1. Ask (one message, all questions at once)

1. **Roles.** Which job titles or role families do you want? Which do you never want (e.g. sales quota roles, internships, pure engineering)?
2. **Where.** Which city for on-site/hybrid roles? Is remote OK, and from which regions (e.g. UK, EU, anywhere)? Any countries to exclude?
3. **Work mode.** On-site, hybrid, remote, or any mix?
4. **Company types and industries.** Startups/scaleups, established corporates, VC firms, any mix? Which industries, in their own words (e.g. "fintech, space, climate tech")? Any kinds of company to skip?
5. **Hard limits.** Years of experience you won't stretch to; visa or work-permit constraints; languages you don't speak; anything else that is an automatic no.
6. **Wishlist.** Companies you already want watched (names are enough; the collector finds their job boards).
7. **VC portfolio boards.** Which VCs' job boards should feed the company pool? (Give sensible defaults for their region if they don't know.)
8. **Your background**, for judging fit: a CV or a paragraph per role with dates, plus education and languages. This becomes `profile/brief.md`. Only facts they give you.
9. **Notes app.** Do you keep notes in Obsidian or similar and want the report and alerts written there? (Optional.)
10. **Schedule.** What time(s) should Snoopy run, and in which time zone? Weekdays only?

## 2. Build the data repo

Ask them to create a **private, empty** GitHub repo and give the agent app access to it. Then:

- `settings.yaml`: start from `config.example/settings.yaml`; translate their answers into `roles`, `locations`, `work_modes`, `company_kinds`, `drops`, `flags`, `company_search` (set `city`, `country` and their free-text `industries`: these drive the company directory).
- Company directory: the shared public directory (`job-radar-directory`, one folder per city) is checked out by the `companies` workflow. If their city has no folder yet, the first run creates it. Optionally add a repo secret `DIRECTORY_TOKEN` (a token with write access to the directory repo) so what their runs learn is shared back.
- Offer research (`agent/RESEARCH.md`): "Want me to find companies and directories in your industries now?" Do it with them present; it seeds `inbox/research.csv` and `inbox/sources.csv`.
- `wishlist.csv`: their companies (`name,kind,ats,slug,careers_url,source,note`; leave `ats`/`slug` empty if unknown, set `source=seed`).
- `index.csv`: header only.
- `profile/brief.md`: who they are, in the shape of `agent/templates/brief.md`. `profile/rules.md`: their judging rules, from `agent/templates/rules.md`.
- `agent/config.yaml`: from `agent/templates/config.yaml` (name, repos, artifact URL once published, vault section if any, schedule).
- `.github/workflows/`: copy `templates/workflows/scan.yml` and `templates/workflows/companies.yml`. In the repo settings: Actions → Workflow permissions → **Read and write**.
- `judged.json` = `{}`, `extra_roles.json` = `[]`.

## 3. First runs

1. Start the `companies` workflow (builds the pool), then `scan` (first scan + report).
   GitHub runs scheduled workflows "best effort": on a new repo they can run hours late or not at all. Set up an external trigger: a free [cron-job.org](https://cron-job.org) job per workflow that POSTs to `https://api.github.com/repos/<owner>/<data-repo>/actions/workflows/<scan|companies>.yml/dispatches` with headers `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28` and body `{"ref":"main"}` (companies: `{"ref":"main","inputs":{"skip_discovery":"false"}}`), at the workflows' cron times in UTC. The token is a fine-grained personal access token (Repository access: only the data repo; Permissions: Repository → Actions: Read and write). The person creates it and pastes it into cron-job.org themselves; never ask for it. A test run returns 204. Note the token's expiry: the scans stop when it lapses.
2. Review the first queue yourself with the person present: it calibrates the rules. Adjust `settings.yaml` and `profile/rules.md` with what you learn.
3. Publish `report/index.html` as an artifact; put its URL in `agent/config.yaml`.
4. Create the scheduled task from `agent/templates/task-prompt.md` at their chosen times. It must not require web fetches; if it needs their computer (vault), bind it to that computer.
5. Add the daily digest if they want one place to read results (`agent/templates/digest-prompt.md`).

Tell them: edit `settings.yaml` (or their settings note) to change what is found; edit `wishlist.csv` to change who is watched; the report page updates after every Snoopy run.
