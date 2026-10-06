"""Repair job boards that stopped working, so the report's "Needs attention" list stays short.

    python -m radar.repair --data PATH [--min-fails 3] [--max 60]

For every board that failed `min_fails` scans in a row (scan/health.json):
  1. look for the company's current board: its careers page (wishlist careers_url, or the directory's website)
     through careers discovery; a different working board replaces the old one in wishlist.csv / index.csv
  2. otherwise: a wishlist company keeps its careers_url and is watched as a page (ats/slug cleared);
     a market company's index.csv row is disabled (`#name`), the convention for switched-off rows
For every board audit mismatch (scan/board_audit.json): a wishlist row without a board gets the board its
careers page links to, if that board works.
Every change is logged to scan/repairs.json. Runs weekly in the companies workflow; safe to run any time.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from radar import careers
from sources import boards as ats

NOW = datetime.now(timezone.utc)


def _load(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def read_rows(path: Path) -> tuple[list[str], list[dict]]:
    """All rows, disabled ones (`#name`) included, so the file can be written back unchanged otherwise."""
    if not path.exists():
        return [], []
    with path.open(newline="") as f:
        r = csv.DictReader(f)
        return list(r.fieldnames or []), [dict(x) for x in r]


def write_rows(path: Path, fields: list[str], rows: list[dict]):
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def works(board: str) -> int | None:
    """Number of postings on a board, or None if it doesn't answer."""
    a, s = board.split(":", 1)
    if a not in ats.ADAPTERS:
        return None
    try:
        return len(ats.fetch(a, s))
    except Exception:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("RADAR_DATA", "."))
    ap.add_argument("--min-fails", type=int, default=3)
    ap.add_argument("--max", type=int, default=60, help="careers pages to check per run")
    args = ap.parse_args(argv)
    data = Path(args.data).resolve()
    health = _load(data / "scan/health.json", {})
    failing = [f for f in (health.get("not_found") or []) + (health.get("failed") or [])
               if int(f.get("consecutive_fails") or 0) >= args.min_fails]
    audit = _load(data / "scan/board_audit.json", {}).get("mismatches") or []
    wf, wish = read_rows(data / "wishlist.csv")
    xf, index = read_rows(data / "index.csv")
    websites = {}
    p = data / "pool/directory.csv"
    if p.exists():
        with p.open(newline="") as f:
            for r in csv.DictReader(f):
                if r.get("website"):
                    websites.setdefault(_norm(r.get("name")), r.get("careers_url") or r["website"])
    log, checked = [], 0

    def rows_for(board: str):
        a, s = board.split(":", 1)
        hits = [("wishlist", r) for r in wish if r.get("ats", "").lower() == a and r.get("slug", "") == s]
        hits += [("index", r) for r in index if r.get("ats", "").lower() == a and r.get("slug", "") == s
                 and not r.get("name", "").startswith("#")]
        return hits

    for f in failing:
        board, company = f.get("board", ""), f.get("company", "")
        if ":" not in board or (f.get("error") or "").startswith("HTTPError: 429"):
            continue                                     # rate limits pass on their own
        hits = rows_for(board)
        if not hits:
            continue
        url = next((r.get("careers_url") for src, r in hits if src == "wishlist" and r.get("careers_url")), "") \
            or websites.get(_norm(company), "")
        new = None
        if url and checked < args.max:
            checked += 1
            prof = careers.discover({"name": company, "website": url})
            if prof.get("method") == "feed" and prof.get("board") and prof["board"] != board and works(prof["board"]) is not None:
                new = prof["board"]
        for src, r in hits:
            if new:
                r["ats"], r["slug"] = new.split(":", 1)
                log.append({"company": company, "from": board, "action": "moved", "to": new, "list": src})
            elif src == "wishlist":
                r["ats"], r["slug"] = "", ""
                if not r.get("careers_url") and url:
                    r["careers_url"] = url
                log.append({"company": company, "from": board, "action": "page watch" if r.get("careers_url") else "no board",
                            "list": src})
            else:
                r["name"] = "#" + r["name"]
                log.append({"company": company, "from": board, "action": "disabled", "list": src})

    for m in audit:
        found = [b for b in m.get("found_on_page") or [] if b.split(":", 1)[0] in ats.ADAPTERS]
        row = next((r for r in wish if r.get("name") == m.get("company")), None)
        if not row or row.get("ats") or not found:
            continue
        n = works(found[0])
        if n is not None:
            row["ats"], row["slug"] = found[0].split(":", 1)
            log.append({"company": m["company"], "action": "board added", "to": found[0], "postings": n, "list": "wishlist"})

    # disabled companies get a fresh careers discovery on the next weekly run (they may have moved somewhere new)
    stale = {_norm(e["company"]) for e in log if e["action"] in ("disabled", "page watch", "no board")}
    dpath = data / "pool/directory.csv"
    if stale and dpath.exists():
        df, drows = read_rows(dpath)
        for r in drows:
            if _norm(r.get("name")) in stale:
                r["checked"] = ""
        write_rows(dpath, df, drows)
    if log:
        write_rows(data / "wishlist.csv", wf, wish)
        write_rows(data / "index.csv", xf, index)
        hist = _load(data / "scan/repairs.json", [])
        hist = [{**e, "on": NOW.date().isoformat()} for e in log] + hist
        (data / "scan/repairs.json").write_text(json.dumps(hist[:500], indent=1, ensure_ascii=False) + "\n")
    for e in log:
        print(f"  {e['action']:12} {e['company']}: {e.get('from', '')} -> {e.get('to', '')}")
    print(f"repair: {len(failing)} failing boards, {len(audit)} audit mismatches, {len(log)} changes, {checked} careers pages checked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
