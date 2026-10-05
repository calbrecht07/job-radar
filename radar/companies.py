"""Weekly company search: build the company pool from every source.

    python -m radar.companies --data PATH [--skip-discovery]

Sources (all configured in settings.yaml → company_search):
  wishlist.csv            companies you flagged by hand (always in the pool)
  portfolio boards        VC job boards (Getro / Consider): their portfolio companies + jobs
  news feeds              funding / expansion news of the last week (for the agent to judge)
  discovery               Common Crawl harvest of job boards (radar.discover), grows index.csv

Writes (data repo):
  pool/portfolio.json     companies per board, with domain, locations, stage, open jobs, detected ATS
  pool/portfolio_jobs.json  jobs from the boards whose apply link is NOT a supported ATS (own-site postings)
  pool/news.json          news items that look like a raise / expansion
  pool/companies.json     the unified pool: one entry per company, with sources and boards
  pool/new_this_week.json what changed since the last run (for the agent's Monday review)
  index.csv               += ATS boards discovered through portfolio job links (source=portfolio)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import yaml

from radar.scan import load_json, read_csv, save_json
from sources import boards as ats
from sources import news as news_src
from sources import portfolio

NOW = datetime.now(timezone.utc)
TODAY = NOW.date().isoformat()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _domain(url_or_domain: str) -> str:
    d = (url_or_domain or "").lower()
    if "://" in d:
        d = urlparse(d).netloc
    return d[4:] if d.startswith("www.") else d


def board_from_url(url: str):
    """(ats, slug) if the apply URL is on a supported platform."""
    for a, s in ats.detect_boards(url or ""):
        if a in ats.ADAPTERS:
            return a, s
    return None


def region_ok(locations: list[str], rx: re.Pattern | None) -> bool:
    return True if rx is None else any(rx.search(l or "") for l in locations)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("RADAR_DATA", "."))
    ap.add_argument("--skip-discovery", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args(argv)
    data = Path(args.data).resolve()
    cfg = yaml.safe_load((data / "settings.yaml").read_text()) or {}
    cs = cfg.get("company_search") or {}
    region = re.compile("|".join(cs.get("region_terms") or []), re.I) if cs.get("region_terms") else None
    (data / "pool").mkdir(exist_ok=True)

    wishlist = read_csv(data / "wishlist.csv")
    index = read_csv(data / "index.csv")
    known_boards = {(r.get("ats", "").lower(), r.get("slug", "").lower()) for r in wishlist + index if r.get("ats")}
    known_names = {_norm(r["name"]) for r in wishlist}
    prev_pool = {c["key"]: c for c in load_json(data / "pool/companies.json", {}).get("companies", [])}

    # ---- portfolio boards
    boards_cfg = cs.get("portfolio_boards") or []
    port_companies, port_jobs, board_health = [], [], []

    def one_board(b):
        url, owner = (b["url"], b.get("vc", "")) if isinstance(b, dict) else (b, "")
        try:
            info, comps = portfolio.companies(url)
            if not info:
                return owner, url, None, [], [], "not a Getro/Consider board (or unreachable)"
            jobs = portfolio.jobs(info)
            return owner, url, info["platform"], comps, jobs, None
        except portfolio.TooBroad as e:
            return owner, url, "consider", [], [], f"skipped: {e}"
        except Exception as e:
            return owner, url, None, [], [], f"{type(e).__name__}: {str(e)[:120]}"

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        results = list(ex.map(one_board, boards_cfg))
    for owner, url, platform, comps, jobs, err in results:
        board_health.append({"vc": owner, "url": url, "platform": platform, "companies": len(comps), "jobs": len(jobs), "error": err})
        for c in comps:
            c["vc"] = owner or _domain(url)
            port_companies.append(c)
        for j in jobs:
            j["vc"] = owner or _domain(url)
            port_jobs.append(j)

    # ATS boards revealed by portfolio job links -> index.csv
    new_index_rows, seen_new = [], set()
    jobs_by_company: dict[str, list] = {}
    for j in port_jobs:
        jobs_by_company.setdefault(_norm(j.get("company")), []).append(j)
        b = board_from_url(j.get("url"))
        if b and (b[0], b[1].lower()) not in known_boards and (b[0], b[1].lower()) not in seen_new:
            seen_new.add((b[0], b[1].lower()))
            new_index_rows.append({"name": j.get("company") or b[1], "kind": "startup", "ats": b[0], "slug": b[1],
                                   "added": TODAY, "source": "portfolio"})
    # jobs not on a supported ATS: keep as own-site postings for the scan
    own_site_jobs = [j for j in port_jobs if not board_from_url(j.get("url"))]

    # ---- news
    news_items = news_src.collect(cs.get("news_feeds"), days=int(cs.get("news_days", 8)), region_terms=cs.get("region_terms"))

    # ---- unified pool
    pool: dict[str, dict] = {}
    def add(key, name, kind, source, **extra):
        e = pool.setdefault(key, {"key": key, "name": name, "kind": kind, "sources": [], "boards": [], "domain": "",
                                  "locations": [], "stage": None, "industries": [], "vcs": [], "first_seen": TODAY})
        if source not in e["sources"]:
            e["sources"].append(source)
        for k, v in extra.items():
            if v and not e.get(k):
                e[k] = v
            elif isinstance(v, list) and v:
                e[k] = list(dict.fromkeys(e.get(k, []) + v))
        if key in prev_pool:
            e["first_seen"] = prev_pool[key].get("first_seen", TODAY)
        return e

    for r in wishlist:
        e = add(_norm(r["name"]), r["name"], r.get("kind") or "startup", "wishlist", domain=_domain(r.get("careers_url", "")))
        if r.get("ats"):
            e["boards"] = list(dict.fromkeys(e["boards"] + [f"{r['ats']}:{r['slug']}"]))
    for r in index:
        e = add(_norm(r["name"]), r["name"], r.get("kind") or "startup", r.get("source") or "discover")
        e["boards"] = list(dict.fromkeys(e["boards"] + [f"{r['ats']}:{r['slug']}"]))
    for r in new_index_rows:
        e = add(_norm(r["name"]), r["name"], "startup", "portfolio")
        e["boards"] = list(dict.fromkeys(e["boards"] + [f"{r['ats']}:{r['slug']}"]))
    for c in port_companies:
        e = add(_norm(c.get("name")), c.get("name"), "startup", "portfolio", domain=_domain(c.get("domain") or ""),
                locations=c.get("locations") or [], stage=c.get("stage"), industries=c.get("industries") or [],
                vcs=[c["vc"]] + (c.get("investors") or []))
        e["open_jobs_on_board"] = c.get("open_jobs")
        e["in_region"] = region_ok(c.get("locations") or [], region)

    companies = sorted(pool.values(), key=lambda c: c["name"].lower())
    new_keys = [c["key"] for c in companies if c["key"] not in prev_pool]
    in_region_new = [c for c in companies if c["key"] in new_keys and c.get("in_region", True) and "wishlist" not in c["sources"]]

    # ---- write
    save_json(data / "pool/portfolio.json", {"updated": NOW.isoformat(timespec="minutes"), "boards": board_health,
                                            "companies": port_companies})
    save_json(data / "pool/portfolio_jobs.json", own_site_jobs)
    save_json(data / "pool/news.json", {"updated": NOW.isoformat(timespec="minutes"), "items": news_items})
    save_json(data / "pool/companies.json", {"updated": NOW.isoformat(timespec="minutes"), "count": len(companies), "companies": companies})
    save_json(data / "pool/new_this_week.json", {
        "updated": NOW.isoformat(timespec="minutes"),
        "new_companies_in_region": [{k: c.get(k) for k in ("name", "domain", "locations", "stage", "industries", "vcs", "sources", "boards", "open_jobs_on_board")}
                                     for c in in_region_new],
        "new_companies_total": len(new_keys),
        "new_boards_from_portfolio": new_index_rows,
        "news": news_items[:150],
        "board_health": board_health})
    if new_index_rows:
        with (data / "index.csv").open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["name", "kind", "ats", "slug", "added", "source"], lineterminator="\n")
            for r in new_index_rows:
                w.writerow(r)
    print(json.dumps({"portfolio_boards": len(boards_cfg), "portfolio_companies": len(port_companies), "portfolio_jobs": len(port_jobs),
                      "own_site_jobs": len(own_site_jobs), "new_index_rows": len(new_index_rows), "news": len(news_items),
                      "pool": len(companies), "new_in_region": len(in_region_new),
                      "board_errors": [b for b in board_health if b["error"]]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
