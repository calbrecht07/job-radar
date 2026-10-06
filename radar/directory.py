"""Company directory: every company worth watching in a city, how to read its jobs, and where it came from.

    python -m radar.directory --data PATH [--directory PATH] [--max N] [--no-wikidata]

Sources, all driven by settings.yaml -> company_search (nothing here is specific to one person):
  wikidata:city        companies headquartered in `city` (any district inside it)
  wikidata:industry    companies in each free-text `industries` term: in your country, or anywhere if they
                       have >= `industry_min_employees` staff (the scan keeps only roles in your city)
  directory:<name>     pages listing companies: `directories` in settings + the shared city sources.csv
                       (VC portfolio boards, accelerator alumni, association members, award lists)
  portfolio            companies on your VC portfolio boards (pool/companies.json, from radar.companies)
  research             companies an agent found on request (inbox/research.csv)

Every company then goes through careers discovery (radar.careers): careers page -> how its jobs can be read.
New companies first, then the oldest checks (`recheck_days`), at most --max per run.

The shared directory (a separate public repo, one folder per city) holds only public facts: name, website,
industries, careers page, read method. Nothing about the person. Layout:
  cities/<city>/companies.csv   the directory
  cities/<city>/sources.csv     directory pages for that city (url,name,type,industries,added,added_by)

Written to the person's data repo:
  index.csv                     += companies with a supported feed (source=directory)
  pool/directory_pages.json     own-site careers pages for the scan to watch (method page/jobdata/enterprise)
  pool/directory.json           run summary + newly discovered companies with matching roles (for the agent)
  inbox/done/                   processed research files
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from radar import careers
from radar.filters import Filters
from radar.scan import load_json, read_csv, save_json
from sources import boards as ats
from sources import directories, wikidata

NOW = datetime.now(timezone.utc)
TODAY = NOW.date().isoformat()

COLUMNS = ["key", "name", "website", "kind", "industries", "employees", "wikidata", "sources", "careers_url",
           "method", "board", "enterprise", "checked", "first_seen", "error", "rendered"]
SOURCE_COLUMNS = ["url", "name", "type", "industries", "added", "added_by"]
WATCH_METHODS = ("page", "jobdata", "enterprise", "rendered")


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\b(plc|ltd|limited|llp|inc|group|holdings|the|uk|\(.*?\))\b", "", (s or "").lower()))


def company_key(name: str, website: str) -> str:
    return careers.registrable(website) if website else "name:" + norm(name)


def kind_of(e: dict) -> str:
    """corporate = large or listed; startup otherwise (the default the rest of the engine assumes)."""
    srcs, types = e.get("sources", ""), " ".join(e.get("types") or [])
    try:
        emp = int(e.get("employees") or 0)
    except ValueError:
        emp = 0
    if e.get("kind") in ("vc",):
        return "vc"
    if re.search(r"portfolio|research", srcs) and emp < 1000 and e.get("kind") != "corporate":
        return "startup"
    if emp >= 500 or re.search(r"public company|conglomerate|multinational", types, re.I):
        return "corporate"
    # known only to Wikidata (notable, no startup source) and not known to be small: established company
    if srcs and all(s.startswith("wikidata") for s in srcs.split("; ")) and not (0 < emp < 200):
        return "corporate"
    return e.get("kind") or "startup"


def priority(r: dict, new_keys: set) -> tuple:
    """Discovery order: new before rechecks; research and portfolio finds, then industry matches, then size."""
    srcs = r.get("sources", "")
    rank = 0 if "research" in srcs else 1 if "portfolio" in srcs or "directory:" in srcs else \
        2 if "wikidata:industry" in srcs or r.get("industries") else 3
    try:
        emp = int(r.get("employees") or 0)
    except ValueError:
        emp = 0
    return (r["key"] not in new_keys, r.get("checked") or "", rank, -emp)


# ------------------------------------------------------------------ files
def read_directory(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(newline="") as f:
        return {r["key"]: {k: (r.get(k) or "") for k in COLUMNS} for r in csv.DictReader(f) if r.get("key")}


def write_directory(path: Path, rows: dict[str, dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in sorted(rows.values(), key=lambda r: r["name"].lower()):
            w.writerow({k: r.get(k, "") for k in COLUMNS})
    tmp.replace(path)


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return [{k: (v or "").strip() for k, v in r.items() if k} for r in csv.DictReader(f)]


def append_rows(path: Path, rows: list[dict], columns: list[str]):
    if not rows:
        return
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, lineterminator="\n", extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------- merging
def merge(directory: dict, cand: dict, names: dict | None = None) -> dict:
    """Add or enrich a company. Matches on website domain, then on normalised name.
    `names` (norm(name) -> key) speeds up repeated merges; it is kept up to date."""
    if names is None:
        names = {norm(r["name"]): k for k, r in directory.items() if norm(r["name"])}
    key = company_key(cand.get("name", ""), cand.get("website", ""))
    n = norm(cand.get("name", ""))
    if (key.startswith("name:") or key not in directory) and n in names:
        key = names[n]
    e = directory.setdefault(key, {c: "" for c in COLUMNS} | {"key": key, "first_seen": TODAY})
    if n and n not in names:
        names[n] = key
    for f in ("name", "website", "wikidata", "careers_url"):
        if cand.get(f) and not e.get(f):
            e[f] = cand[f]
    if cand.get("employees") and str(cand["employees"]) not in ("0", ""):
        e["employees"] = str(max(int(e.get("employees") or 0), int(cand["employees"])))
    for f in ("industries", "sources"):
        vals = [v for v in (e.get(f) or "").split("; ") if v] + list(cand.get(f) or [])
        e[f] = "; ".join(dict.fromkeys(v for v in vals if v))
    e["kind"] = kind_of({**e, "types": cand.get("types"), "kind": cand.get("kind") or e.get("kind")})
    return e


# ---------------------------------------------------------------- sources
def parse_industries(raw) -> list[tuple[str, list[str]]]:
    """settings industries: plain text ("fintech") or {name, search: [terms]} when the person's word isn't one
    Wikidata uses ("watertech" -> water treatment, desalination). With `search`, only those terms are searched;
    the name stays the label. -> [(name, search terms)]"""
    out = []
    for item in raw or []:
        if isinstance(item, dict) and item.get("name"):
            out.append((str(item["name"]), [str(t) for t in item.get("search") or []]))
        elif isinstance(item, str) and item.strip():
            out.append((item.strip(), []))
    return out


def from_wikidata(cs: dict, log) -> list[dict]:
    city, country = cs.get("city"), cs.get("country", "")
    out = []
    place = wikidata.find_place(city, country) if city else None
    if not place:
        log(f"wikidata: city {city!r} not found")
        return out
    log(f"wikidata: {place['label']} ({place['qid']}, {place['country']})")
    industries = parse_industries(cs.get("industries"))
    city_cos = wikidata.companies_in_city(place["qid"])
    # industries also match a company's description (Wikidata has no item for e.g. "climate tech")
    for c in city_cos:
        text = (c.get("description") or "") + " " + " ".join(c["industries"])
        c["industries"] = c["industries"] + [name for name, terms in industries
                                             if any(re.search(r"\b" + re.escape(t), text, re.I) for t in [name] + terms)]
    out += city_cos
    log(f"wikidata: {len(city_cos)} companies headquartered in {place['label']}")
    country_qid = wikidata.country_of(place["qid"])
    min_emp = int(cs.get("industry_min_employees") or 1000)
    for name, terms in industries:
        inds: dict = {}
        for term in terms or [name]:
            try:
                for i in wikidata.find_industries(term):
                    inds.setdefault(i["qid"], i)
            except RuntimeError as e:
                log(f"wikidata: industry {name!r} / {term!r} failed: {e}")
            time.sleep(1)
        try:
            found = wikidata.companies_in_industries(list(inds), country_qid=country_qid,
                                                     min_employees=min_emp, label=name) if inds else []
        except RuntimeError as e:
            log(f"wikidata: industry {name!r} failed: {e}")
            continue
        for c in found:
            c["industries"] = list(dict.fromkeys(c["industries"] + [name]))
        out += found
        log(f"wikidata: industry {name!r}: {len(inds)} Wikidata industries, {len(found)} companies"
            + ("" if inds else " (no Wikidata industry matches: add `search` terms, or ask for research)"))
    return out


def in_area(locations: list[str], flt: Filters, region: re.Pattern | None) -> bool:
    """A company belongs in this city's directory if any of its locations is the city, the person's region,
    or a remote region they accept. Companies with no location given are kept (can't tell)."""
    locs = [l for l in locations or [] if l]
    if not locs:
        return True
    text = " | ".join(locs)
    return bool(any(rx.search(text) for rx in flt.local + flt.region_wide + flt.allowed)
                or (region and region.search(text)))


def from_directories(sources: list[dict], log, flt: Filters | None = None, region: re.Pattern | None = None,
                     outside: set | None = None) -> list[dict]:
    """Companies on directory pages. Portfolio boards list companies worldwide: those located elsewhere are
    left out (their keys go into `outside`, so earlier runs' rows can be pruned)."""
    out = []
    for s in sources:
        try:
            found = directories.companies(s)
        except Exception as e:
            log(f"directory {s.get('name') or s['url']}: {type(e).__name__}")
            continue
        label = s.get("name") or careers.registrable(s["url"])
        inds = [i.strip() for i in (s.get("industries") or "").split(";") if i.strip()] if isinstance(s.get("industries"), str) \
            else list(s.get("industries") or [])
        kept = 0
        for c in found:
            if flt is not None and not in_area(c.get("locations"), flt, region):
                if outside is not None:
                    outside.add(company_key(c.get("name", ""), c.get("website", "")))
                continue
            kept += 1
            out.append({**c, "sources": [f"directory:{label}"], "industries": inds})
        log(f"directory {label}: {len(found)} companies, {kept} in the area")
    return out


def from_pool(data: Path) -> list[dict]:
    """Portfolio-board companies with a domain, from the weekly company search."""
    out = []
    for c in load_json(data / "pool/companies.json", {}).get("companies", []):
        if c.get("domain") and "portfolio" in c.get("sources", []) and c.get("in_region", True):
            out.append({"name": c["name"], "website": "https://" + c["domain"], "sources": ["portfolio"],
                        "industries": c.get("industries") or [], "kind": "startup"})
    return out


def from_research(data: Path) -> tuple[list[dict], list[dict], list[Path]]:
    inbox = data / "inbox"
    comps, srcs, files = [], [], []
    p = inbox / "research.csv"
    if p.exists():
        files.append(p)
        for r in read_rows(p):
            if r.get("name") or r.get("website"):
                comps.append({"name": r.get("name") or "", "website": r.get("website") or "", "sources": ["research"],
                              "industries": [i.strip() for i in (r.get("industries") or "").split(";") if i.strip()],
                              "kind": r.get("kind") or ""})
    p = inbox / "sources.csv"
    if p.exists():
        files.append(p)
        srcs = [r for r in read_rows(p) if r.get("url")]
    return comps, srcs, files


# ------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("RADAR_DATA", "."))
    ap.add_argument("--directory", default=os.environ.get("RADAR_DIRECTORY"), help="shared directory repo (default <data>/.directory)")
    ap.add_argument("--max", type=int, default=None, help="max companies to discover this run")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--minutes", type=int, default=None, help="time budget for discovery (default discovery_minutes or 90)")
    ap.add_argument("--no-wikidata", action="store_true")
    ap.add_argument("--no-discovery", action="store_true", help="only collect companies")
    ap.add_argument("--no-browser", action="store_true", help="skip the browser pass")
    ap.add_argument("--browser-minutes", type=int, default=None, help="time budget for the browser pass (default browser_minutes or 60)")
    args = ap.parse_args(argv)
    data = Path(args.data).resolve()
    cfg = yaml.safe_load((data / "settings.yaml").read_text()) or {}
    cs = cfg.get("company_search") or {}
    if not cs.get("city"):
        print("company_search.city is not set in settings.yaml: nothing to do")
        return 0
    flt = Filters(cfg)
    root = Path(args.directory).resolve() if args.directory else data / ".directory"
    city_dir = root / "cities" / slug(cs["city"])
    dpath = city_dir / "companies.csv"
    local_copy = data / "pool/directory.csv"        # the person's copy: kept even without write access to the shared repo
    directory = read_directory(dpath)
    for k, r in read_directory(local_copy).items():
        if k not in directory or (r.get("checked") or "") > (directory[k].get("checked") or ""):
            directory[k] = r
    before = set(directory)
    logs: list[str] = []
    log = lambda m: (logs.append(m), print(m, flush=True))

    # ---- collect
    research, new_sources, inbox_files = from_research(data)
    shared_sources = read_rows(city_dir / "sources.csv")
    known_src = {s["url"].rstrip("/") for s in shared_sources}
    added_src = [{"url": s["url"], "name": s.get("name", ""), "type": s.get("type") or "auto",
                  "industries": s.get("industries", ""), "added": TODAY, "added_by": s.get("added_by") or "research"}
                 for s in new_sources if s["url"].rstrip("/") not in known_src]
    append_rows(city_dir / "sources.csv", added_src, SOURCE_COLUMNS)
    dir_sources = shared_sources + added_src + [{"url": d["url"], "name": d.get("name", ""), "type": d.get("type") or "auto",
                                                 "industries": d.get("industries") or []}
                                                for d in cs.get("directories") or [] if d.get("url")]
    cands = []
    if not args.no_wikidata:
        try:
            cands += from_wikidata(cs, log)
        except RuntimeError as e:
            log(f"wikidata unavailable: {e}")
    region = re.compile("|".join(cs["region_terms"]), re.I) if cs.get("region_terms") else None
    outside: set = set()
    cands += from_directories(dir_sources, log, flt, region, outside)
    # rows that came only from directory boards and turned out to be elsewhere (earlier runs kept them)
    pruned = [k for k, r in directory.items() if k in outside
              and all(src.startswith("directory:") for src in (r.get("sources") or "").split("; ") if src)]
    for k in pruned:
        del directory[k]
    if pruned:
        log(f"directory: removed {len(pruned)} companies located outside the area")
    before -= set(pruned)
    cands += from_pool(data)
    cands += research
    names = {norm(r["name"]): k for k, r in directory.items() if norm(r["name"])}
    for c in cands:
        merge(directory, c, names)
    new_keys = set(directory) - before
    log(f"collected {len(cands)} candidates; directory {len(before)} -> {len(directory)} companies ({len(new_keys)} new)")

    # ---- careers discovery: new first, then the stalest
    recheck = int(cs.get("recheck_days") or 30)
    stale_before = (NOW - timedelta(days=recheck)).date().isoformat()
    dead_before = (NOW - timedelta(days=recheck * 3)).date().isoformat()
    due = [r for r in directory.values() if not r.get("checked")
           or (r["method"] in ("dead", "blocked", "none") and r["checked"] < dead_before)
           or (r["method"] not in ("dead", "blocked", "none") and r["checked"] < stale_before)]
    due.sort(key=lambda r: priority(r, new_keys))
    limit = args.max if args.max is not None else int(cs.get("discovery_per_run") or 800)
    due = [] if args.no_discovery else due[:limit]
    found_roles = []
    t0 = time.time()
    budget = 60 * (args.minutes if args.minutes is not None else int(cs.get("discovery_minutes") or 90))
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(careers.discover, {"name": r["name"], "website": r["website"]}): r for r in due}
        for i, fut in enumerate(as_completed(futs), 1):
            if time.time() - t0 > budget:              # out of time: stop cleanly, the rest waits for next week
                cancelled = sum(f.cancel() for f in futs)
                log(f"discovery: time budget reached, {cancelled} companies left for the next run")
                break
            r = futs[fut]
            done += 1
            try:
                p = fut.result()
            except Exception as e:
                p = {"checked": TODAY, "method": "dead", "error": f"crash: {type(e).__name__}"}
            for f in ("careers_url", "method", "board", "enterprise", "checked", "error"):
                r[f] = p.get(f, "") or ""
            company = {"kind": r.get("kind") or "startup"}
            matches = [j for j in p.get("jobs") or []        # full filter where the location is known
                       if (flt.evaluate(j, company).keep if (j.get("locations") or j.get("remote"))
                           else flt.title_ok(j.get("title") or "", company["kind"]))]
            if matches:
                found_roles.append({"company": r["name"], "key": r["key"], "method": r["method"], "careers_url": r["careers_url"],
                                    "kind": r["kind"], "roles": matches[:5], "matching": len(matches)})
            if i % 100 == 0:                           # save as we go: a killed run keeps its work
                log(f"discovery: {i}/{len(due)}")
                write_directory(dpath, directory)
                write_directory(local_copy, directory)
    log(f"discovery: {done} companies in {round(time.time() - t0)} s")
    write_directory(dpath, directory)
    write_directory(local_copy, directory)

    # ---- browser pass: careers pages plain requests couldn't read (JavaScript job lists, simple bot checks)
    rpath = data / "pool/rendered_jobs.json"
    rendered_jobs: dict = load_json(rpath, {})
    from radar import browser as br
    if not (args.no_browser or args.no_discovery) and cs.get("browser", True) is not False and br.available():
        from radar.scan import locate_html
        wanted = lambda t: flt.title_ok(t or "", "startup")
        locate = lambda html, url: {k: v for k, v in locate_html(html, url, flt).items() if k in ("locations", "remote")}
        rdue = [r for r in directory.values() if r.get("method") in careers.RENDER_METHODS and r.get("website")
                and (not r.get("rendered") or r["rendered"] < stale_before)]
        rdue.sort(key=lambda r: priority(r, new_keys))
        rbudget = 60 * (args.browser_minutes if args.browser_minutes is not None else int(cs.get("browser_minutes") or 60))
        t1, rdone, rfeeds = time.time(), 0, 0
        try:
            with br.Browser() as b:
                for r in rdue:
                    if time.time() - t1 > rbudget:
                        log(f"browser: time budget reached, {len(rdue) - rdone} companies left for the next run")
                        break
                    try:
                        prof = careers.render_discover({"name": r["name"], "website": r["website"]},
                                                       {k: r.get(k, "") for k in ("careers_url", "method", "board", "enterprise", "error")},
                                                       b, wanted=wanted, locate=locate)
                    except Exception as e:
                        prof = {"rendered": TODAY, "error": f"render crash: {type(e).__name__}"}
                    rdone += 1
                    for f in ("careers_url", "method", "board", "enterprise", "error", "rendered"):
                        if f in prof:
                            r[f] = prof.get(f) or ""
                    if r["method"] == "rendered":
                        rendered_jobs[r["key"]] = {"at": TODAY, "jobs": prof.get("jobs") or []}
                        m = [j for j in prof.get("jobs") or [] if wanted(j.get("title")) and (j.get("locations") or j.get("remote"))
                             and flt.evaluate(j, {"kind": r.get("kind") or "startup"}).keep]
                        if m:
                            found_roles.append({"company": r["name"], "key": r["key"], "method": "rendered", "careers_url": r["careers_url"],
                                                "kind": r["kind"], "roles": m[:5], "matching": len(m)})
                    else:
                        rendered_jobs.pop(r["key"], None)
                        rfeeds += r["method"] == "feed"
                    if rdone % 25 == 0:
                        write_directory(dpath, directory); write_directory(local_copy, directory); save_json(rpath, rendered_jobs)
        except Exception as e:
            log(f"browser unavailable: {type(e).__name__}: {str(e)[:120]}")
        log(f"browser: {rdone} companies in {round(time.time() - t1)} s, {rfeeds} became job-board feeds, "
            f"{sum(1 for r in directory.values() if r.get('method') == 'rendered')} read as rendered pages")
        write_directory(dpath, directory)
        write_directory(local_copy, directory)
    save_json(rpath, {k: v for k, v in rendered_jobs.items() if k in directory and directory[k].get("method") == "rendered"})

    # ---- the person's side: feeds into index.csv, pages for the scan
    wishlist, index = read_csv(data / "wishlist.csv"), read_csv(data / "index.csv")
    known = {(r.get("ats", "").lower(), r.get("slug", "").lower()) for r in wishlist + index}
    with (data / "index.csv").open() as f:          # disabled rows (#name) count as known too
        known |= {(r.get("ats", "").lower(), r.get("slug", "").lower()) for r in csv.DictReader(f)}
    known_names = {norm(r["name"]) for r in wishlist + index}
    new_index = []
    for r in directory.values():
        if r["method"] == "feed" and ":" in r["board"]:
            a, s = r["board"].split(":", 1)
            if a in ats.ADAPTERS and (a, s.lower()) not in known:
                known.add((a, s.lower()))
                new_index.append({"name": r["name"], "kind": r["kind"], "ats": a, "slug": s, "added": TODAY, "source": "directory"})
    append_rows(data / "index.csv", new_index, ["name", "kind", "ats", "slug", "added", "source"])
    watch = [{"key": r["key"], "name": r["name"], "kind": r["kind"], "careers_url": r["careers_url"], "method": r["method"],
              "enterprise": r["enterprise"],
              **({"jobs": (rendered_jobs.get(r["key"]) or {}).get("jobs") or []} if r["method"] == "rendered" else {})}
             for r in directory.values() if r["method"] in WATCH_METHODS and r["careers_url"] and norm(r["name"]) not in known_names]
    save_json(data / "pool/directory_pages.json", sorted(watch, key=lambda w: w["name"].lower()))

    methods: dict[str, int] = {}
    for r in directory.values():
        m = r["method"] or "unchecked"
        methods[m] = methods.get(m, 0) + 1
    ents: dict[str, int] = {}
    for r in directory.values():
        if r["enterprise"]:
            ents[r["enterprise"]] = ents.get(r["enterprise"], 0) + 1
    found_roles.sort(key=lambda x: -x["matching"])
    summary = {"updated": NOW.isoformat(timespec="minutes"), "city": cs["city"], "companies": len(directory),
               "new_companies": len(new_keys), "discovered_this_run": done, "methods": methods,
               "enterprise_systems_without_adapter": dict(sorted(ents.items(), key=lambda x: -x[1])),
               "new_index_rows": len(new_index), "pages_watched": len(watch),
               "companies_with_matching_titles": found_roles[:100], "log": logs}
    save_json(data / "pool/directory.json", summary)

    for p in inbox_files:                                  # processed: keep a dated copy, out of the inbox
        done = data / "inbox/done" / f"{TODAY}-{p.name}"
        done.parent.mkdir(parents=True, exist_ok=True)
        p.replace(done)
    print(json.dumps({k: v for k, v in summary.items() if k not in ("companies_with_matching_titles", "log")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
