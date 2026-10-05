"""Grow the market index (index.csv in your data directory).

Sources:
  --commoncrawl   harvest company slugs from Common Crawl's URL index for each job-board domain
  --names FILE    guess slugs for company names (one per line, or a CSV with a `name` column)
  --slugs FILE    explicit "ats,slug[,name]" lines (e.g. added by the Claude Scout)

Every candidate (ats, slug) not already indexed is probed. A company is added when its
live feed shows it is relevant: at least one posting that passes filters.yaml today, or
at least `--min-remote` postings that are remote and open to Europe/UK/anywhere.
"""
from __future__ import annotations

import argparse
import os
import csv
import json
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs

import requests
import yaml

from radar import ats
from radar.filters import Filters

CC = "https://index.commoncrawl.org"
DOMAINS = {
    "jobs.ashbyhq.com": "ashby",
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "job-boards.eu.greenhouse.io": "greenhouse-eu",
    "jobs.lever.co": "lever",
    "jobs.eu.lever.co": "lever-eu",
    "apply.workable.com": "workable",
}
RESERVED = {"", "j", "api", "embed", "jobs", "careers", "static", "assets", "favicon.ico", "robots.txt",
            "sitemap.xml", "oauth", "login", "privacy", "terms", "candidates", "v1", "posting-api"}


def slug_from_url(u: str):
    try:
        p = urlparse(u if "://" in u else "https://" + u)
    except ValueError:
        return None
    host = p.netloc.lower().split(":")[0]
    a = DOMAINS.get(host)
    if not a:
        return None
    parts = [x for x in p.path.split("/") if x]
    if a.startswith("greenhouse") and parts[:2] == ["embed", "job_board"]:
        s = (parse_qs(p.query).get("for") or [""])[0]
    else:
        s = unquote(parts[0]) if parts else ""
    s = s.strip()
    if s.lower() in RESERVED or len(s) > 80 or not re.match(r"^[\w.\- ]+$", s):
        return None
    return a, s


def cc_get(url: str, params: dict, timeout: int = 180):
    """Common Crawl's index throttles hard: pace requests and back off on 503/non-JSON."""
    import time
    for attempt in range(7):
        time.sleep(2)
        try:
            r = requests.get(url, params=params, timeout=timeout,
                             headers={"User-Agent": "job-radar/1.0 (personal job search)"})
            if r.status_code == 200 and r.text.lstrip().startswith("{"):
                return r.text
            if r.status_code == 404:
                return ""
            why = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            why = type(e).__name__
        wait = min(15 * 2 ** attempt, 300)
        print(f"    cc retry {attempt + 1} in {wait}s ({why})", file=sys.stderr)
        time.sleep(wait)
    return None


def commoncrawl_slugs(max_pages: int, collections: int = 2, seed: int = 0) -> set:
    colls = requests.get(f"{CC}/collinfo.json", timeout=60).json()[:collections]
    out, rnd = set(), random.Random(seed)
    for coll in (c["id"] for c in colls):
        print(f"Common Crawl collection: {coll}", file=sys.stderr)
        base = f"{CC}/{coll}-index"
        for dom in DOMAINS:
            txt = cc_get(base, {"url": f"{dom}/*", "output": "json", "showNumPages": "true"}, timeout=120)
            if not txt:
                print(f"  {dom}: page count unavailable", file=sys.stderr)
                continue
            n = json.loads(txt).get("pages", 0)
            pages = list(range(n))
            if n > max_pages:  # spread the sample across the alphabet
                pages = sorted(rnd.sample(pages, max_pages))
            before = len(out)
            for pg in pages:
                txt = cc_get(base, {"url": f"{dom}/*", "output": "json", "fl": "url", "page": pg})
                for line in (txt or "").splitlines():
                    try:
                        hit = slug_from_url(json.loads(line)["url"])
                    except Exception:
                        continue
                    if hit:
                        out.add(hit)
            print(f"  {dom}: {n} pages, sampled {len(pages)}, +{len(out) - before} slugs", file=sys.stderr)
    return out


def name_variants(name: str) -> list[str]:
    base = re.sub(r"\(.*?\)", "", name).strip().lower()
    words = re.findall(r"[a-z0-9]+", base)
    v = {"".join(words), "-".join(words), words[0] if words else "", "_".join(words)}
    if len(words) > 1:
        v.add("".join(words[:2]))
    for suffix in ("ai", "hq", "inc", "labs", "careers", "jobs"):
        v.add("".join(words) + suffix)
        v.add("-".join(words) + "-" + suffix)
    return [x for x in v if x]


def probe(a: str, s: str, flt: Filters, min_remote: int, keep_empty: bool = False):
    try:
        posts = ats.fetch(a, s)
    except Exception:
        return None
    if not posts:
        return {"jobs": 0, "matches": 0, "remote": 0, "local": 0} if keep_empty else None
    comp = {"kind": "startup"}
    matches = [p for p in posts if flt.evaluate(p, comp).keep]
    remote_ok = [p for p in posts if flt.is_remote(p) and flt.remote_region(p) == (True, None)]
    local = [p for p in posts if flt.local_match(p) == (True, None)]
    if not keep_empty and not matches and len(remote_ok) < min_remote:
        return None
    return {"jobs": len(posts), "matches": len(matches), "remote": len(remote_ok), "local": len(local)}


def fill_watchlist(data: Path, workers: int = 16) -> int:
    """Watchlist rows with no ats/slug: try name variants on every supported platform; fill the first
    plausible board that answers with at least one posting. Writes watchlist.csv in place."""
    path = data / "watchlist.csv"
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    fields = list(rows[0].keys()) if rows else []
    todo = [r for r in rows if not r.get("ats") and not r["name"].startswith("#")]
    platforms = [a for a in ats.ADAPTERS if not a.endswith("-eu")]

    flt = Filters(yaml.safe_load((data / "settings.yaml").read_text()))

    def full_name_variants(name):
        base = re.sub(r"\(.*?\)|/.*$", "", name).strip().lower()
        words = re.findall(r"[a-z0-9]+", base)
        if not words:
            return []
        stems = {"".join(words), "-".join(words)}
        out = set(stems)
        for st in stems:
            out |= {st + suf for suf in ("", "ai", "hq", "-ai", "-hq", "careers", "-careers", "jobs")}
        return sorted(out)

    def try_company(r):
        # a board only counts if it uses the company's full name AND has UK/London roles
        # (generic words like "connect" or "frontline" belong to other companies)
        for v in full_name_variants(r["name"]):
            for a in platforms:
                try:
                    posts = ats.fetch(a, v)
                except Exception:
                    continue
                uk = [p for p in posts if flt.local_match(p)[0] or (flt.is_remote(p) and flt.remote_region(p)[0])]
                if uk:
                    return r, a, v, len(posts)
        return r, None, None, 0

    found = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for r, a, v, n in ex.map(try_company, todo):
            if a:
                r["ats"], r["slug"] = a, v
                r["note"] = (r.get("note") or "") + f" board found by name probe ({n} postings)"
                found += 1
                print(f"  + {r['name']}: {a}/{v} ({n} postings)", file=sys.stderr)
            else:
                print(f"  - {r['name']}: no board on supported platforms", file=sys.stderr)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(json.dumps({"checked": len(todo), "found": found}))
    return 0


def pretty(slug: str) -> str:
    s = re.sub(r"[-_.]+", " ", slug).strip()
    return s.title() if s.islower() else s


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("RADAR_DATA", "."), help="your data directory")
    ap.add_argument("--commoncrawl", action="store_true")
    ap.add_argument("--max-pages", type=int, default=8, help="Common Crawl index pages per domain")
    ap.add_argument("--names")
    ap.add_argument("--slugs")
    ap.add_argument("--max-probe", type=int, default=4000)
    ap.add_argument("--min-remote", type=int, default=2)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--source", default="discover")
    ap.add_argument("--keep-empty", action="store_true", help="add boards that exist but have no postings (Scout)")
    ap.add_argument("--fill-watchlist", action="store_true",
                    help="find hidden job boards for watchlist companies without one (tries name variants on every platform)")
    args = ap.parse_args(argv)

    data = Path(args.data).resolve()
    flt = Filters(yaml.safe_load((data / "settings.yaml").read_text()))
    # every row in either list counts as known, including ones disabled with a leading "#"
    existing = set()
    for fname in ("index.csv", "watchlist.csv"):
        if (data / fname).exists():
            with (data / fname).open(newline="") as f:
                existing |= {((r.get("ats") or "").strip().lower(), (r.get("slug") or "").strip().lower())
                             for r in csv.DictReader(f)}
    rejected_path = data / "state/rejected.json"
    # rejected: "ats|slug" -> date probed. Re-probed after 60 days (hiring changes).
    rejected = json.loads(rejected_path.read_text()) if rejected_path.exists() else {}
    stale = (date.today() - timedelta(days=60)).isoformat()
    rejected = {k: d for k, d in rejected.items() if d >= stale}

    if args.fill_watchlist:
        return fill_watchlist(data, args.workers)

    cands: dict[tuple, str] = {}
    if args.commoncrawl:
        for a, s in commoncrawl_slugs(args.max_pages):
            cands.setdefault((a, s), pretty(s))
    if args.names:
        text = Path(args.names).read_text()
        names = [r["name"] for r in csv.DictReader(text.splitlines())] if text.startswith("name") else text.splitlines()
        for n in filter(None, map(str.strip, names)):
            for v in name_variants(n):
                for a in ("ashby", "greenhouse", "lever", "workable"):
                    cands.setdefault((a, v), n)
    if args.slugs:
        for line in Path(args.slugs).read_text().splitlines():
            parts = [x.strip() for x in line.split(",")]
            if len(parts) >= 2 and parts[0] in set(DOMAINS.values()) | set(ats.ADAPTERS):
                cands[(parts[0], parts[1])] = parts[2] if len(parts) > 2 and parts[2] else pretty(parts[1])

    todo = [(k, n) for k, n in cands.items() if (k[0], k[1].lower()) not in existing and f"{k[0]}|{k[1]}" not in rejected]
    random.Random(1).shuffle(todo)
    todo = todo[: args.max_probe]
    print(f"{len(cands)} candidates, {len(todo)} to probe", file=sys.stderr)

    added, found_names = [], set()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(probe, a, s, flt, args.min_remote, args.keep_empty): (a, s, n) for (a, s), n in todo}
        for f in as_completed(futs):
            a, s, n = futs[f]
            res = f.result()
            if res is None:
                rejected[f"{a}|{s}"] = date.today().isoformat()
                continue
            if args.names and n in found_names:  # one board per named company
                continue
            found_names.add(n)
            added.append({"name": n, "kind": "startup", "ats": a, "slug": s,
                          "added": date.today().isoformat(), "source": args.source})
            print(f"  + {n} ({a}/{s}) jobs={res['jobs']} matches={res['matches']} remote={res['remote']} local={res['local']}",
                  file=sys.stderr)

    if added:
        idx = data / "index.csv"
        new_file = not idx.exists()
        with idx.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["name", "kind", "ats", "slug", "added", "source"])
            if new_file:
                w.writeheader()
            for r in sorted(added, key=lambda r: r["name"].lower()):
                w.writerow(r)
    rejected_path.parent.mkdir(exist_ok=True)
    rejected_path.write_text(json.dumps(rejected, indent=0, sort_keys=True) + "\n")
    print(json.dumps({"probed": len(todo), "added": len(added)}))


if __name__ == "__main__":
    sys.exit(main())
