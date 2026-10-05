"""Job Radar run: check every company, keep postings that pass settings.yaml, write the report.

Usage:  python -m radar.run --data PATH_TO_YOUR_DATA_REPO [--audit] [--only "A,B"] [--dry-run]

Data directory layout (your private repo):
  settings.yaml          filters (see config.example/settings.yaml)
  watchlist.csv          Layer 1: companies you always want checked
                         name,kind,ats,slug,careers_url,source,note
  index.csv              Layer 2: the market index (grown by discovery)
                         name,kind,ats,slug,added,source
  judged.json            optional reviewer verdicts {id: {fit, note, ...}} (written by the reviewer)
Written:
  output/pending.json    new candidates (both layers), kept pending_days
  output/review_queue.json  every open role without a verdict in judged.json, with its description
  output/watchlist.json  every watchlist company's current matching roles / page status
  output/health.json     run stats, failing boards
  output/board_audit.json  (--audit) job boards found on watchlist careers pages
  output/runs.log
  report/report.md, report/report.json
  state/seen.json, state/companies.json, state/pages.json
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from radar import ats, pages, report
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


def fetch_one(c: dict):
    try:
        return c, ats.fetch(c["ats"], c["slug"]), None
    except ats.NotFound:
        return c, None, "not_found"
    except Exception as e:  # network, 5xx, bad JSON
        return c, None, f"{type(e).__name__}: {str(e)[:160]}"


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
    mk_age = int(fresh.get("market_max_age_days") or 0)
    mk_cutoff = (NOW - timedelta(days=mk_age)).isoformat() if mk_age else None

    watch = read_csv(data / "watchlist.csv")
    index = read_csv(data / "index.csv")
    if args.only:
        want = {n.strip().lower() for n in args.only.split(",")}
        watch = [w for w in watch if w["name"].lower() in want]
        index = [r for r in index if r["name"].lower() in want]

    seen: dict = load_json(data / "state/seen.json", {})
    cstate: dict = load_json(data / "state/companies.json", {})
    pstate: dict = load_json(data / "state/pages.json", {})
    pending: list = load_json(data / "output/pending.json", [])
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
            if first_fetch and ff_cutoff and too_old(p.get("published"), ff_cutoff):
                drop("first_fetch_older")
                continue
            if c["layer"] == "market" and mk_cutoff and too_old(p.get("published"), mk_cutoff):
                drop("market_too_old")
                continue
            new_cands.append(candidate(c, p, v, key, max_desc))
            stats["new_candidates"][c["layer"]] += 1
    stats["feed_seconds"] = round(time.time() - t0, 1)

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
            for j in res.get("jobs") or []:
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
    save_json(data / "state/companies.json", cstate)
    save_json(data / "state/pages.json", pstate)
    save_json(data / "output/pending.json", pending)
    # archive of every candidate (no descriptions) for the report, 60 days
    arch_after = (NOW - timedelta(days=60)).isoformat()
    matches = [{k: v for k, v in c.items() if k != "description"} for c in new_cands]
    matches += [m for m in load_json(data / "output/matches.json", []) if m.get("found", "") >= arch_after]
    save_json(data / "output/matches.json", matches)
    save_json(data / "output/health.json", stats)
    save_json(data / "output/watchlist.json", {"updated": stats["run_at"], "companies": wl_status})
    # review queue: every open role without a verdict yet (watchlist roles + new market matches)
    judged = load_json(data / "judged.json", {})
    queue, qids = [], set()
    for c in list(wl_full.values()) + pending:
        if c["id"] not in judged and c["id"] not in qids:
            qids.add(c["id"])
            queue.append(c)
    queue.sort(key=lambda x: (x["layer"] != "watchlist", x["kind"] != "vc", x["company"].lower()))
    save_json(data / "output/review_queue.json", queue)
    stats["review_queue"] = len(queue)
    save_json(data / "output/health.json", stats)
    if args.audit:
        save_json(data / "output/board_audit.json", {"updated": stats["run_at"], "mismatches": audit})
    log = data / "output/runs.log"
    lines = log.read_text().splitlines() if log.exists() else []
    lines.append(f"{stats['run_at']} watchlist={len(watch)} boards={len(targets)} ok={stats['ok']} "
                 f"failed={len(stats['failed'])} not_found={len(stats['not_found'])} postings={stats['postings']} "
                 f"new={stats['new_postings']} cand_watch={stats['new_candidates']['watchlist']} "
                 f"cand_market={stats['new_candidates']['market']} pages={stats['pages_checked']}")
    log.write_text("\n".join(lines[-500:]) + "\n")
    report.build(data, cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
