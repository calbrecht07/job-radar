# Research: finding companies on request

The person asks for companies in a space ("find space-industry companies in London", "who builds climate
software in Lisbon?"). You research, and hand your findings to the pipeline; the pipeline does the rest.

**When this runs:** only when the person asks, in their chat or in Claude Code, with them present. Research
opens web pages and searches, which a scheduled run must never do (see `SNOOPY.md`). If a scheduled run is
asked to research, it writes the request to its log for the person instead.

## What to look for

1. **Companies** in the space and the person's city (or remote-friendly ones in their region). Prefer evidence
   that they exist now and employ people there: an office address, a careers page, recent news.
2. **Directories**, which matter more than single companies: pages that *list* companies in the space and
   stay current. Trade association member lists, accelerator/incubator alumni pages, VC portfolio pages,
   government cluster maps, award shortlists. Each directory you add is re-read every week by the pipeline,
   so new members are found without anyone searching again.

Skip recruiters, job aggregators, and listicles that won't be updated.

**Check every directory before adding it**: `PYTHONPATH=<framework> python -m sources.directories <url>` prints what
the pipeline would read. The best directories are VC and accelerator job boards on Getro or Consider (they give each
company's website and location): look for "jobs" or "careers" boards of investors in the space. Most association and
"top companies" pages link to their own profile pages, not to company websites; the reader returns nothing for
them, so add their companies to `research.csv` instead.

## Where to write (data repo)

`inbox/research.csv`, one company per row:
```
name,website,industries,kind,evidence
Orbex,https://orbex.space,space; launch,startup,UK Space Agency launch partner; office in London listed on site
```
`industries` is free text, `;`-separated. `kind` is `startup`, `corporate` or `vc` (leave empty if unsure).
`evidence` is one line on why it belongs: the person reads it.

`inbox/sources.csv`, one directory per row:
```
url,name,type,industries
https://www.ukspace.org/members/,UKspace members,list,space
https://talent.seedcamp.com,Seedcamp,getro,
```
`type`: `getro` or `consider` for VC portfolio job boards on those platforms, `list` for any other page whose
links are the member companies, `auto` if unsure.

Commit and push (`git pull --rebase` first). The next weekly company search (or a manual run of the
`companies` workflow) adds the companies to the directory, finds each careers page and how to read it, and
starts watching them. Directories go into the shared directory for the city, so other people searching the
same city benefit too. Processed files move to `inbox/done/`.

## Tell the person

How many companies and directories you added, the three most promising companies with one line each, and
that results appear after the next company search (or that they can start the `companies` workflow now).
Never state facts about the person that aren't in `profile/`, and never contact any company.
