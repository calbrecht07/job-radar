"""Liveness check: never publish a role whose posting has closed.

A role is LIVE if its posting key was seen in the latest scan of a job-board feed (state/live.json), or,
for roles that didn't come from a feed (careers-page links, portfolio boards, roles added by hand),
if its URL still answers with a page that doesn't say the job is gone.

    verify.run(data)  -> {"checked": n, "closed": [...]}   writes scan/closed.json
report.build() calls this and hides every closed role.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests

from sources import boards as ats

UA = {"User-Agent": "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/128.0 Safari/537.36 job-radar/1.0"}
GONE = re.compile(
    r"(job|position|role|posting|opening|vacancy|listing)[^.]{0,40}(no longer (available|accepting|open|active)|"
    r"has (been )?(filled|closed|expired|removed)|is (closed|unavailable|not available)|not found|does ?n[o']t exist|"
    r"could ?n[o']t be found)|no longer (available|accepting applications)|this job is closed|applications (are )?closed|"
    r"position (has been )?filled|job not found|page not found|404", re.I)
# a board's front page: host root, or host + one path segment (jobs.ashbyhq.com/acme, boards.greenhouse.io/acme),
# optionally followed by /jobs or /careers
BOARD_HOME = re.compile(r"^https?://[^/]+(/[\w.%-]+)?(/(jobs|careers))?/?$")


def _load(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


_FEED_CACHE: dict = {}


def check_via_feed(url: str) -> tuple[bool, str] | None:
    """If the URL is on a supported job board, ask the board's feed whether the posting is still listed.
    Returns None when the URL isn't on a supported board."""
    hit = next(((a, s) for a, s in ats.detect_boards(url) if a in ats.ADAPTERS), None)
    if not hit:
        return None
    a, slug = hit
    key = f"{a}:{slug.lower()}"
    if key not in _FEED_CACHE:
        try:
            _FEED_CACHE[key] = {p.get("url", "").rstrip("/") for p in ats.fetch(a, slug)} | \
                               {str(p.get("id")) for p in ats.fetch(a, slug)}
        except ats.NotFound:
            _FEED_CACHE[key] = set()
        except Exception as e:
            _FEED_CACHE[key] = None
    feed = _FEED_CACHE[key]
    if feed is None:
        return True, "unverified: board feed unavailable"
    u = url.rstrip("/")
    last = u.rsplit("/", 1)[-1].split("?")[0]
    if u in feed or last in feed or any(f.endswith("/" + last) for f in feed if f):
        return True, "ok (listed in board feed)"
    return False, "not in the company's job-board feed any more"


def check_url(url: str) -> tuple[bool, str]:
    """(live, reason). Conservative: network trouble counts as live (don't hide roles on a hiccup)."""
    via = check_via_feed(url)
    if via is not None:
        return via
    try:
        r = requests.get(url, headers=UA, timeout=25, allow_redirects=True)
    except requests.RequestException as e:
        return True, f"unverified: {type(e).__name__}"
    if r.status_code in (404, 410):
        return False, f"HTTP {r.status_code}"
    if r.status_code >= 400:
        return True, f"unverified: HTTP {r.status_code}"
    if r.url.rstrip("/") != url.rstrip("/") and BOARD_HOME.match(r.url.rstrip("/")) and not BOARD_HOME.match(url.rstrip("/")):
        return False, "redirected to board home"
    head = re.sub(r"<script.*?</script>|<style.*?</style>", " ", r.text[:60000], flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", head)
    text = re.sub(r"\s+", " ", text)
    m = GONE.search(text[:6000])          # the notice is near the top of the page
    if m and "404" not in m.group(0):
        return False, f'page says "{m.group(0)[:60]}"'
    if m and len(text) < 1500:
        return False, "404 page"
    if len(text) < 400:
        return True, "unverified: page needs JavaScript"
    return True, "ok"


def run(data: Path, workers: int = 12) -> dict:
    live_keys = set(_load(data / "state/live.json", []))
    judged = _load(data / "judged.json", {})
    wl = _load(data / "scan/watchlist.json", {}).get("companies", {})
    matches = _load(data / "scan/matches.json", [])
    extra = _load(data / "extra_roles.json", [])
    prev = _load(data / "scan/closed.json", {}).get("closed", {})

    # roles the report may show
    roles = {}
    for name, s in wl.items():
        for r in s.get("roles", []):
            roles[r["id"]] = r | {"company": name}
    for m in matches:
        roles.setdefault(m["id"], m)
    for e in extra:
        if not e.get("closed"):
            roles.setdefault(e["id"], e)
    # drop roles already judged cut (not shown anyway)
    roles = {k: v for k, v in roles.items() if (judged.get(k) or {}).get("fit") != "cut"}

    closed, to_check, unverified = {}, [], {}
    for rid, r in roles.items():
        url = r.get("url") or ""
        if url in live_keys:
            continue                                    # seen in a live feed this scan: live
        if rid in prev and prev[rid].get("permanent"):
            closed[rid] = prev[rid]
            continue
        to_check.append((rid, r))

    def one(item):
        rid, r = item
        live, reason = check_url(r.get("url") or "")
        return rid, r, live, reason

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for rid, r, live, reason in ex.map(one, to_check):
            if not live:
                closed[rid] = {"company": r.get("company"), "title": r.get("title"), "url": r.get("url"),
                               "reason": reason, "closed_on": datetime.now(timezone.utc).date().isoformat(),
                               "permanent": reason.startswith("HTTP") or "says" in reason}
            elif reason.startswith("unverified"):
                unverified[rid] = reason
    out = {"updated": datetime.now(timezone.utc).isoformat(timespec="minutes"), "checked": len(to_check),
           "feed_live": len(roles) - len(to_check), "closed": closed, "unverified": unverified}
    (data / "scan").mkdir(exist_ok=True)
    (data / "scan/closed.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    # mirror into extra_roles.json so hand-added roles stay closed
    changed = False
    for e in extra:
        if e.get("id") in closed and not e.get("closed"):
            e["closed"] = True
            changed = True
    if changed:
        (data / "extra_roles.json").write_text(json.dumps(extra, indent=1, ensure_ascii=False) + "\n")
    return out
