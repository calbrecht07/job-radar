"""Job Radar run: check every company, keep postings that pass settings.yaml, write the report.

Usage:  python -m radar.scan --data PATH_TO_YOUR_DATA_REPO [--audit] [--only "A,B"] [--dry-run]

Data directory layout (your private repo):
  settings.yaml          filters (see config.example/settings.yaml)
  wishlist.csv          Layer 1: companies you always want checked
                         name,kind,ats,slug,careers_url,source,note
  index.csv              Layer 2: the market index (grown by discovery)
                         name,kind,ats,slug,added,source
  judged.json            optional reviewer verdicts {id: {fit, note, ...}} (written by the reviewer)
Written:
  scan/pending.json    new candidates (both layers), kept pending_days
  scan/review_queue.json  every open role without a verdict in judged.json, with its description
  scan/watchlist.json  every watchlist company's current matching roles / page status
  scan/health.json     run stats, failing boards
  scan/board_audit.json  (--audit) job boards found on watchlist careers pages
  scan/runs.log
  report/report.md, report/report.json
  state/seen.json, state/companies.json, state/pages.json
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from sources import boards as ats, jobdata, pages
from radar import careers, report
from radar.filters import Filters, Verdict

NOW = datetime.now(timezone.utc)
TODAY = NOW.date().isoformat()


# ----------------------------------------------------------------- helpers
def load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, data, indent=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=indent, ensure_ascii=False, sort_keys=isinstance(data, dict)) + "\n")
    tmp.replace(path)


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return [{k: (v or "").strip() for k, v in r.items() if k} for r in csv.DictReader(f)
                if (r.get("name") or "").strip() and not r["name"].strip().startswith("#")]


def load_settings(data: Path) -> dict:
    return yaml.safe_load((data / "settings.yaml").read_text()) or {}


def posting_key(ck: str, p: dict) -> str:
    return p.get("url") or f"{ck}:{p.get('id')}"


def cand_id(key: str) -> str:
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def too_old(published, cutoff_iso: str) -> bool:
    """True if a posting's date is known and before the cutoff."""
    if not published:
        return False
    try:
        d = datetime.fromisoformat(str(published).replace("Z", "+00:00"))
    except ValueError:
        try:
            d = datetime.strptime(str(published)[:10], "%Y-%m-%d")
        except ValueError:
            return False
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.isoformat() < cutoff_iso


def build_targets(watch: list[dict], index: list[dict], pstate: dict) -> list[dict]:
    """One entry per job board. Watchlist wins over the index for the same board.
    Watchlist rows without a board use one detected on their careers page (if supported)."""
    targets, seen = [], set()
    for w in watch:
        a, s = w.get("ats", "").lower(), w.get("slug", "")
        if not (a and s):
            for b in (pstate.get(w["name"]) or {}).get("boards", []):
                ba, bs = b.split(":", 1)
                if ba in ats.ADAPTERS and ats.plausible(w["name"], bs, w.get("careers_url", "")):
                    a, s, w = ba, bs, {**w, "board_detected": True}
                    break
        if a and s and a in ats.ADAPTERS and (a, s.lower()) not in seen:
            seen.add((a, s.lower()))
            targets.append({**w, "ats": a, "slug": s, "layer": "watchlist"})
    for r in index:
        a, s = r.get("ats", "").lower(), r.get("slug", "")
        if a in ats.ADAPTERS and s and (a, s.lower()) not in seen:
            seen.add((a, s.lower()))
            targets.append({**r, "ats": a, "slug": s, "layer": "market"})
    return targets


def _epoch_iso(v):
    """Portfolio boards give epoch seconds; feeds give ISO strings. Normalise to ISO."""
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v, tz=timezone.utc).isoformat()
    return v


def _host(url: str) -> str:
    from urllib.parse import urlparse
    h = urlparse(url or "").netloc.lower()
    h = h[4:] if h.startswith("www.") else h
    parts = h.split(".")
    return ".".join(parts[-3:]) if len(parts) > 2 and parts[-2] in ("co", "com", "org", "ac") else ".".join(parts[-2:])


def fill_descriptions(cands: list, limit: int, workers: int) -> int:
    """Fetch job-page text for candidates whose feed had no description (careers-page links,
    Breezy, SmartRecruiters, BambooHR), so the reviewer never has to open pages itself."""
    todo = [c for c in cands if not c.get("description") and c.get("url")][:limit]

    def one(c):
        try:
            wd = ats.workday_description(c["url"]) if "myworkday" in c["url"] else None
            if wd:
                c["description"] = wd[:7000]
                c["flags"] = [f for f in c.get("flags", []) if "open the link" not in f]
                return
            r = pages.requests.get(c["url"], headers=pages.UA, timeout=25)
            if r.status_code < 400:
                text = "\n".join(pages.page_text(r.text))
                c["description"] = text[:7000]
                c["flags"] = [f for f in c.get("flags", []) if "open the link" not in f] + \
                             (["description from job page"] if len(text) > 400 else ["job page loads with JavaScript: open the link"])
        except Exception:
            pass
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, todo))
    return len(todo)


def fetch_one(c: dict):
    try:
        return c, ats.fetch(c["ats"], c["slug"]), None
    except ats.NotFound:
        return c, None, "not_found"
    except Exception as e:  # network, 5xx, bad JSON
        return c, None, f"{type(e).__name__}: {str(e)[:160]}"


def directory_page(d: dict):
    """Current jobs on a directory company's careers page: schema.org job data if the page has it, else the
    posting links on it. Pages that need a browser come with their jobs from the weekly browser pass.
    -> (d, [posting], error)"""
    if d.get("jobs") is not None:
        return d, [{"title": j.get("title", ""), "url": j.get("url", ""), "locations": j.get("locations") or [],
                    "remote": j.get("remote"), "description": "", "_flags": ["found by the weekly browser check"]}
                   for j in d["jobs"] if j.get("url")], None
    try:
        r = pages.requests.get(d["careers_url"], headers=careers.HEADERS, timeout=25)
        if r.status_code >= 400:
            return d, [], f"HTTP {r.status_code}"
        found = [p for p in jobdata.postings(r.text, r.url) if not jobdata.expired(p)]
        if found:
            return d, found, None
        return d, [{"title": j["title"], "url": j["url"], "locations": [], "description": ""}
                   for j in careers.own_jobs(r.text, r.url, d["name"])], None
    except Exception as e:
        return d, [], type(e).__name__


def locate_html(html: str, url: str, flt: Filters) -> dict:
    """Where a job page says the role is: its job data if present, else the page text naming the person's
    city or a remote region. {} when it names neither."""
    data = jobdata.postings(html, url)
    if data:
        best = data[0]
        return {"locations": best["locations"], "remote": best["remote"], "workplace": best["workplace"],
                "description": best["description"], "published": best.get("published"),
                "_flags": ["location from the job page's data"]}
    text = "\n".join(pages.page_text(html))
    m = next((rx.search(text) for rx in flt.local if rx.search(text)), None)
    if m:
        return {"locations": [m.group(0)], "description": text[:7000], "_flags": ["location read from the job page text: check it"]}
    if re.search(r"\b(fully )?remote\b", text, re.I):
        m = next((rx.search(text) for rx in flt.allowed if rx.search(text)), None)
        return {"remote": True, "locations": [f"Remote - {m.group(0)}"] if m else ["Remote"], "description": text[:7000],
                "_flags": ["remote read from the job page text: check it"]}
    return {"description": text[:7000]}


def located(p: dict, flt: Filters) -> dict:
    """Open a posting found as a link and read where it is (locate_html). A page that names neither the
    person's city nor a remote region is left without a location (dropped)."""
    p = {**p, "_flags": []}
    try:
        r = pages.requests.get(p["url"], headers=careers.HEADERS, timeout=25)
        if r.status_code >= 400:
            return p
    except Exception:
        return p
    return {**p, **locate_html(r.text, p["url"], flt)}


def candidate(c: dict, p: dict, v, key: str, max_desc: int) -> dict:
    desc = p.get("description") or ""
    return {
        "id": cand_id(key),
        "found": NOW.isoformat(timespec="minutes"),
        "layer": c["layer"],
        "pool": v.pool,
        "company": c["name"],
        "kind": c.get("kind") or "startup",
        "ats": c["ats"],
        "title": p.get("title"),
        "locations": p.get("locations"),
        "workplace": p.get("workplace") or ("remote" if p.get("remote") else ""),
        "url": p.get("url"),
        "published": p.get("published"),
        "department": p.get("department"),
        "salary": p.get("salary"),
        "flags": v.flags,
        "description": desc[:max_desc] + ("…" if len(desc) > max_desc else ""),
    }


# -------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("RADAR_DATA", "."), help="your data directory")
    ap.add_argument("--audit", action="store_true", help="also audit job boards on all watchlist careers pages")
    ap.add_argument("--only", help="comma-separated company names (debug)")
    ap.add_argument("--workers", type=int, default=int(os.environ.get("RADAR_WORKERS", 16)))
    ap.add_argument("--dry-run", action="store_true", help="don't write anything")
    args = ap.parse_args(argv)
    data = Path(args.data).resolve()

    cfg = load_settings(data)
    flt = Filters(cfg)
    fresh, outcfg = cfg.get("freshness") or {}, cfg.get("output") or {}
    max_desc = int(outcfg.get("max_description_chars", 7000))
    silent_sources = set(fresh.get("first_fetch_silent_sources") or [])
    ff_age = fresh.get("first_fetch_max_age_days")
    ff_cutoff = (NOW - timedelta(days=int(ff_age))).isoformat() if ff_age else None
    # companies the radar discovered (not ones the person already knew): their open roles are news to the
    # person, so the first fetch uses the market window instead of first_fetch_max_age_days
    discovered_sources = set(fresh.get("first_fetch_discovered_sources") or ["directory", "portfolio", "research", "discover", "added"])
    mk_age = int(fresh.get("market_max_age_days") or 0)
    mk_cutoff = (NOW - timedelta(days=mk_age)).isoformat() if mk_age else None

    watch = read_csv(data / "wishlist.csv")
    index = read_csv(data / "index.csv")
    if args.only:
        want = {n.strip().lower() for n in args.only.split(",")}
        watch = [w for w in watch if w["name"].lower() in want]
        index = [r for r in index if r["name"].lower() in want]

    seen: dict = load_json(data / "state/seen.json", {})
    cstate: dict = load_json(data / "state/companies.json", {})
    pstate: dict = load_json(data / "state/pages.json", {})
    pending: list = load_json(data / "scan/pending.json", [])
    targets = build_targets(watch, index, pstate)

    stats = {"run_at": NOW.isoformat(timespec="minutes"), "watchlist": len(watch), "boards": len(targets),
             "ok": 0, "failed": [], "not_found": [], "postings": 0, "new_postings": 0,
             "new_candidates": {"watchlist": 0, "market": 0}, "dropped": {}}
    drop = lambda r: stats["dropped"].__setitem__(r, stats["dropped"].get(r, 0) + 1)
    new_cands, current_keys, failed = [], set(), set()
    wl_status: dict = {}
    wl_full: dict = {}          # watchlist roles with descriptions, for the review queue

    # ---- job-board feeds (both layers)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        results = list(ex.map(fetch_one, targets))
    for c, posts, err in results:
        ck = f"{c['ats']}:{c['slug']}"
        st = cstate.setdefault(ck, {"name": c["name"], "first_fetched": None, "fails": 0})
        if c["layer"] == "watchlist":
            wl_status[c["name"]] = {"kind": c.get("kind") or "startup", "board": ck,
                                    "board_detected": bool(c.get("board_detected")),
                                    "careers_url": c.get("careers_url", ""), "roles": [], "postings": 0}
        if err:
            failed.add(ck)
            st["fails"] = st.get("fails", 0) + 1
            st["last_error"] = err
            row = {"company": c["name"], "layer": c["layer"], "board": ck, "error": err, "consecutive_fails": st["fails"]}
            (stats["not_found"] if err == "not_found" else stats["failed"]).append(row)
            if c["layer"] == "watchlist":
                wl_status[c["name"]]["error"] = err
            continue
        first_fetch = st.get("first_fetched") is None
        st.update({"fails": 0, "last_ok": TODAY, "jobs": len(posts)})
        st.pop("last_error", None)
        if first_fetch:
            st["first_fetched"] = TODAY
        silent = first_fetch and (c.get("source") or "") in silent_sources
        stats["ok"] += 1
        stats["postings"] += len(posts)
        if c["layer"] == "watchlist":
            wl_status[c["name"]]["postings"] = len(posts)

        for p in posts:
            key = posting_key(ck, p)
            current_keys.add(key)
            is_new = key not in seen
            if is_new:
                seen[key] = [TODAY, ck]
                stats["new_postings"] += 1
            if not is_new and c["layer"] == "market":
                continue                                  # market: only new postings matter
            v = flt.evaluate(p, c)
            if not v.keep:
                if is_new:
                    drop(v.reason)
                continue
            if c["layer"] == "watchlist":                 # status: every current match
                wl_full[cand_id(key)] = candidate(c, p, v, key, max_desc)
                wl_status[c["name"]]["roles"].append({
                    "id": cand_id(key), "title": p.get("title"), "url": p.get("url"), "pool": v.pool,
                    "locations": p.get("locations"), "workplace": p.get("workplace"),
                    "first_seen": seen[key][0], "flags": v.flags})
            if not is_new:
                continue
            if silent:
                drop("first_fetch_silent")
                continue
            if first_fetch and ff_cutoff and (c.get("source") or "") not in discovered_sources \
                    and too_old(p.get("published"), ff_cutoff):
                drop("first_fetch_older")
                continue
            # a company the radar just discovered: its London roles are news at any age (still listed = open);
            # remote roles keep the market window (old "anywhere in Europe" listings are mostly evergreen)
            discovered_first = first_fetch and (c.get("source") or "") in discovered_sources and v.pool == "local"
            if c["layer"] == "market" and mk_cutoff and not discovered_first and too_old(p.get("published"), mk_cutoff):
                drop("market_too_old")
                continue
            new_cands.append(candidate(c, p, v, key, max_desc))
            stats["new_candidates"][c["layer"]] += 1
    stats["feed_seconds"] = round(time.time() - t0, 1)

    # ---- own-site jobs seen on VC portfolio boards (pool/portfolio_jobs.json): companies whose
    # postings live on their own website, so no feed covers them. Judged like any posting.
    wl_keys = {re.sub(r"[^a-z0-9]", "", w["name"].lower()) for w in watch}
    for j in load_json(data / "pool/portfolio_jobs.json", []):
        if not j.get("url") or not j.get("title"):
            continue
        key = j["url"]
        current_keys.add(key)
        is_new = key not in seen
        ck = "portfolio:" + (j.get("company_slug") or j.get("company") or "")
        if is_new:
            seen[key] = [TODAY, ck]
            stats["new_postings"] += 1
        if not is_new:
            continue
        comp_key = re.sub(r"[^a-z0-9]", "", (j.get("company") or "").lower())
        c = {"name": j.get("company") or "", "kind": "startup", "layer": "watchlist" if comp_key in wl_keys else "market",
             "ats": "portfolio", "slug": j.get("company_slug") or "", "source": "portfolio"}
        v = flt.evaluate(j, c)
        if not v.keep:
            drop(v.reason)
            continue
        if mk_cutoff and too_old(_epoch_iso(j.get("published")), mk_cutoff):
            drop("market_too_old")
            continue
        v.flags = v.flags + [f"via {j.get('vc', 'VC')} portfolio board"]
        new_cands.append(candidate(c, {**j, "published": _epoch_iso(j.get("published"))}, v, key, max_desc))
        stats["new_candidates"][c["layer"]] += 1

    # ---- careers pages (watchlist companies without a supported board; all of them with --audit)
    page_rows = [w for w in watch if w.get("careers_url") and
                 (args.audit or not any(t["name"] == w["name"] and t["layer"] == "watchlist" for t in targets))]
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        checked = list(ex.map(lambda w: (w, pages.check_page(w["careers_url"], pstate.get(w["name"]))), page_rows))
    audit = []
    for w, res in checked:
        pstate[w["name"]] = res
        # job links found on an own-site careers page: treated like postings (title filter only;
        # location isn't known from a link, so the reviewer checks it)
        if w["name"] not in {t["name"] for t in targets if t["layer"] == "watchlist"}:
            c = {**w, "layer": "watchlist", "ats": "page", "slug": w["name"], "kind": w.get("kind") or "startup"}
            own_host = _host(w["careers_url"])
            for j in res.get("jobs") or []:
                h = _host(j["url"])
                path_bits = [b for b in j["url"].split("/")[3:5] if b]
                if not (h == own_host or h.endswith("." + own_host) or own_host.endswith("." + h)
                        or any(ats.plausible(w["name"], b, w["careers_url"]) for b in [h.split(".")[0]] + path_bits)):
                    continue   # e.g. a VC page linking to portfolio companies' jobs
                key = j["url"]
                current_keys.add(key)
                is_new = key not in seen
                if is_new:
                    seen[key] = [TODAY, "page:" + w["name"]]
                    stats["new_postings"] += 1
                if not flt.title_ok(j["title"], c["kind"]):
                    continue
                p = {"id": key, "title": j["title"], "locations": [], "url": key, "description": ""}
                v = Verdict(True, pool="local", flags=["from careers page: location and details not in feed, open the link"])
                wl_full[cand_id(key)] = candidate(c, p, v, key, max_desc)
                res.setdefault("matching", []).append({"id": cand_id(key), "title": j["title"], "url": key,
                                                       "pool": "local", "locations": [], "workplace": "",
                                                       "first_seen": seen[key][0], "flags": v.flags})
                if is_new:
                    new_cands.append(candidate(c, p, v, key, max_desc))
                    stats["new_candidates"]["watchlist"] += 1
        configured = f"{w.get('ats', '').lower()}:{w.get('slug', '')}" if w.get("ats") else ""
        if w["name"] not in wl_status:          # page-only company
            wl_status[w["name"]] = {"kind": w.get("kind") or "startup", "board": "", "careers_url": w["careers_url"],
                                    "page_status": res.get("status"), "page_added": res.get("added", []),
                                    "page_error": res.get("error"), "page_jobs": len(res.get("jobs") or []),
                                    "roles": res.get("matching", [])}
        boards = [b for b in res.get("boards") or [] if ats.plausible(w["name"], b.split(":", 1)[1], w["careers_url"])]
        if boards and configured not in boards:
            audit.append({"company": w["name"], "configured": configured or None, "found_on_page": boards,
                          "careers_url": w["careers_url"]})
    for w in watch:
        if w["name"] not in wl_status:
            wl_status[w["name"]] = {"kind": w.get("kind") or "startup", "board": "", "careers_url": w.get("careers_url", ""),
                                    "page_status": "not_checked" if not w.get("careers_url") else "skipped",
                                    "note": w.get("note", ""), "roles": []}
    stats["pages_checked"] = len(checked)
    stats["pages_changed"] = sum(1 for _, r in checked if r.get("status") == "changed")

    # ---- directory careers pages (market layer): companies whose jobs live only on their own website,
    # found by radar.directory. Checked in rotation; title matches are opened to find the location.
    cs = cfg.get("company_search") or {}
    dir_pages = load_json(data / "pool/directory_pages.json", [])
    if args.only:
        dir_pages = [d for d in dir_pages if d["name"].lower() in want]
    dstate: dict = load_json(data / "state/directory_pages.json", {})
    per_run = int(cs.get("directory_pages_per_run") or -(-len(dir_pages) // 6))   # whole list daily at 6 scans/day
    due = sorted(dir_pages, key=lambda d: dstate.get(d["key"], {}).get("checked", ""))[:per_run]
    job_page_budget = [int(cs.get("directory_job_pages_per_run") or 60)]
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        dir_results = list(ex.map(directory_page, due))
    dir_stats = {"checked": len(due), "errors": 0, "links": 0, "title_matches": 0, "deferred": 0}
    for d, jobs, err in dir_results:
        st = dstate.setdefault(d["key"], {})
        st["checked"] = NOW.isoformat(timespec="minutes")
        if err:
            st["error"] = err
            dir_stats["errors"] += 1
            continue
        st.pop("error", None)
        st["jobs"] = len(jobs)
        dir_stats["links"] += len(jobs)
        c = {"name": d["name"], "kind": d.get("kind") or "startup", "layer": "market", "ats": "page", "slug": d["key"],
             "source": "directory", "careers_url": d["careers_url"]}
        for p in jobs:
            key = p["url"]
            current_keys.add(key)
            if key in seen:
                continue
            if not flt.title_ok(p.get("title") or "", c["kind"]):
                seen[key] = [TODAY, "dirpage:" + d["key"]]
                stats["new_postings"] += 1
                drop("title")
                continue
            if not p.get("locations") and not p.get("remote"):
                if job_page_budget[0] <= 0:
                    dir_stats["deferred"] += 1          # not marked seen: picked up on the next run
                    continue
                job_page_budget[0] -= 1
                p = located(p, flt)
            seen[key] = [TODAY, "dirpage:" + d["key"]]
            stats["new_postings"] += 1
            v = flt.evaluate(p, c)
            if not v.keep:
                drop(v.reason)
                continue
            if mk_cutoff and too_old(p.get("published"), mk_cutoff):
                drop("market_too_old")
                continue
            dir_stats["title_matches"] += 1
            v.flags = v.flags + p.get("_flags", [])
            new_cands.append(candidate(c, p, v, key, max_desc))
            stats["new_candidates"]["market"] += 1
    stats["directory_pages"] = dir_stats

    # ---- prune + queue
    cutoff = (NOW - timedelta(days=90)).date().isoformat()
    seen = {k: v for k, v in seen.items() if k in current_keys or v[0] >= cutoff or v[1] in failed}
    keep_after = (NOW - timedelta(days=int(outcfg.get("pending_days", 10)))).isoformat()

    def still_passes(c):  # re-apply today's settings so changes also clean the queue
        v = flt.evaluate({"title": c.get("title", ""), "locations": c.get("locations") or [],
                          "remote": c.get("workplace") == "remote", "workplace": c.get("workplace", ""),
                          "description": c.get("description", "")}, {"kind": c.get("kind")})
        if v.keep:
            c["pool"], c["flags"] = v.pool, v.flags
        return v.keep

    old = [p for p in pending if p.get("found", "") >= keep_after]
    kept = [p for p in old if still_passes(p)]
    stats["queue_refiltered_out"] = len(old) - len(kept)
    new_cands.sort(key=lambda x: (x["layer"] != "watchlist", x["kind"] != "vc", x["pool"], x["company"]))
    pending = new_cands + kept
    stats["pending_total"] = len(pending)
    stats["failed"].sort(key=lambda x: -x["consecutive_fails"])

    print(json.dumps({k: v for k, v in stats.items() if k not in ("failed", "not_found")}, indent=1))
    for c in new_cands:
        print(f"  + [{c['layer']}/{c['pool']}] {c['company']}: {c['title']} ({'; '.join(c['locations'] or [])})")
    if args.dry_run:
        return 0

    save_json(data / "state/seen.json", seen, indent=0)
    save_json(data / "state/live.json", sorted(current_keys), indent=0)
    save_json(data / "state/companies.json", cstate)
    save_json(data / "state/pages.json", pstate)
    save_json(data / "state/directory_pages.json", dstate)
    save_json(data / "scan/pending.json", pending)
    # archive of every candidate (no descriptions) for the report, 60 days
    arch_after = (NOW - timedelta(days=60)).isoformat()
    matches = [{k: v for k, v in c.items() if k != "description"} for c in new_cands]
    matches += [m for m in load_json(data / "scan/matches.json", []) if m.get("found", "") >= arch_after]
    save_json(data / "scan/matches.json", matches)
    save_json(data / "scan/health.json", stats)
    save_json(data / "scan/watchlist.json", {"updated": stats["run_at"], "companies": wl_status})
    # review queue: every open role without a verdict yet (watchlist roles + new market matches)
    judged = load_json(data / "judged.json", {})
    queue, qids = [], set()
    for c in list(wl_full.values()) + pending:
        if c["id"] not in judged and c["id"] not in qids:
            qids.add(c["id"])
            queue.append(c)
    queue.sort(key=lambda x: (x["layer"] != "watchlist", x["kind"] != "vc", x["company"].lower()))
    stats["descriptions_fetched"] = fill_descriptions(queue, limit=150, workers=args.workers)
    save_json(data / "scan/review_queue.json", queue)
    stats["review_queue"] = len(queue)
    save_json(data / "scan/health.json", stats)
    if args.audit:
        save_json(data / "scan/board_audit.json", {"updated": stats["run_at"], "mismatches": audit})
    log = data / "scan/runs.log"
    lines = log.read_text().splitlines() if log.exists() else []
    lines.append(f"{stats['run_at']} watchlist={len(watch)} boards={len(targets)} ok={stats['ok']} "
                 f"failed={len(stats['failed'])} not_found={len(stats['not_found'])} postings={stats['postings']} "
                 f"new={stats['new_postings']} cand_watch={stats['new_candidates']['watchlist']} "
                 f"cand_market={stats['new_candidates']['market']} pages={stats['pages_checked']}")
    log.write_text("\n".join(lines[-500:]) + "\n")
    report.build(data, cfg, skip_verify=os.environ.get('RADAR_SKIP_VERIFY') == '1')
    return 0


if __name__ == "__main__":
    sys.exit(main())
