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
from sources import jobdata

UA = {"User-Agent": "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/128.0 Safari/537.36 job-radar/1.0"}
GONE = re.compile(
    r"(job|position|role|posting|opening|vacancy|listing)[^.]{0,40}(no longer (available|accepting|open|active)|"
    r"has (been )?(filled|closed|expired|removed)|is (closed|unavailable|not available)|not found|does ?n[o']t exist|"
    r"could ?n[o']t be found)|no longer (available|accepting applications)|this job is closed|applications (are )?closed|"
    r"position (has been )?filled|job not found|page not found|404", re.I)
GONE_STRONG = re.compile(
    r"(job|position|role|posting|opening|vacancy)[^.]{0,30}(no longer (available|accepting|open|active)|"
    r"has (been )?(filled|closed|expired)|is (closed|unavailable))|no longer accepting applications|"
    r"this job is closed|applications (are )?(now )?closed|position (has been )?filled", re.I)
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
            posts = ats.fetch(a, slug)
            _FEED_CACHE[key] = {p.get("url", "").rstrip("/") for p in posts} | {str(p.get("id")) for p in posts}
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


# job aggregators, newsletters and job sites that copy postings: their pages outlive the job, so they never prove
# a role is open. The original posting they link to does.
AGGREGATORS = re.compile(
    r"johngannonblog|welcometothejungle|otta\.com|builtin(london|nyc|)\.|linkedin\.com/jobs|indeed\.|glassdoor|"
    r"wellfound|angel\.co|workinstartups|escapethecity|siftedjobs|venture5|goingvc|startup\.jobs|remoteok|"
    r"weworkremotely|workatastartup|jobs\.ac\.uk|totaljobs|reed\.co\.uk|cwjobs|adzuna|simplyhired|jooble|talent\.com|"
    r"climatebase|workonclimate|techjobsforgood|jobboardfinder|jobsinvc|vcjobs", re.I)
_POSTING = re.compile(r"/(job|jobs|j|p|o|positions?|openings?|vacanc\w*|requisitions?)/[^/?#\s\"]+|gh_jid=|lever\.co/[^/]+/[0-9a-f-]{8,}|"
                      r"ashbyhq\.com/[^/]+/[0-9a-f-]{8,}", re.I)


def original_posting(page_html: str, page_url: str) -> str:
    """The company's own posting that a third-party listing links to (its 'apply' link): a link to a job
    platform with a posting path. "" if none."""
    from urllib.parse import urljoin
    import html as htmlmod
    for href in re.findall(r'href=["\']([^"\']+)["\']', page_html or ""):
        u = urljoin(page_url, htmlmod.unescape(href))
        if u.startswith("http") and not AGGREGATORS.search(u) and _POSTING.search(u) and ats.detect_boards(u):
            return u
    return ""


def _title_shown(title: str, text: str) -> bool:
    """Does the page text mention the role? Three longest title words are enough (Senior Founder's Associate ->
    founder, associate, senior)."""
    words = sorted({w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'’-]{2,}", title or "")}, key=len, reverse=True)[:3]
    low = text.lower()
    return not words or all(w in low for w in words)


def check_url(url: str, title: str = "") -> tuple[bool, str]:
    """(live, reason). Conservative: network trouble counts as live (don't hide roles on a hiccup), but a page
    that doesn't even show the role's title can't be called verified (JavaScript app shells, e.g. WTTJ/Otta)."""
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
    # a copy of a posting (aggregator, newsletter, job site): judge the original it links to, never the copy
    orig = original_posting(r.text, r.url) if (AGGREGATORS.search(url) or AGGREGATORS.search(r.url)) else ""
    if orig and orig.rstrip("/") != url.rstrip("/"):
        live, why = check_url(orig, title)
        return live, f"original posting: {why}"
    if AGGREGATORS.search(url) or AGGREGATORS.search(r.url):
        return True, "unverified: third-party listing, the company's own posting wasn't found"
    if r.url.rstrip("/") != url.rstrip("/") and BOARD_HOME.match(r.url.rstrip("/")) and not BOARD_HOME.match(url.rstrip("/")):
        return False, "redirected to board home"
    head = re.sub(r"<script.*?</script>|<style.*?</style>", " ", r.text[:60000], flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", head)
    text = re.sub(r"\s+", " ", text)
    m = GONE.search(text[:6000])          # weak signals ("404", "page not found") only count near the top
    if m and "404" not in m.group(0):
        return False, f'page says "{m.group(0)[:60]}"'
    if m and len(text) < 1500:
        return False, "404 page"
    m = GONE_STRONG.search(text)          # explicit closed-job notices count anywhere (WTTJ puts it mid-page)
    if m:
        return False, f'page says "{m.group(0)[:60]}"'
    # schema.org JobPosting data: a passed closing date means closed; a future one vouches for a page whose
    # visible text needs JavaScript. Without a closing date it proves nothing (stale blocks exist).
    data = [p for p in jobdata.postings(r.text, url) if not title or _title_shown(title, p["title"])]
    if data and all(jobdata.expired(p) for p in data):
        return False, f"job data says it closed on {str(data[0]['valid_through'])[:10]}"
    open_until = next((p["valid_through"] for p in data if p.get("valid_through")), None)
    if (len(text) < 400 or (title and not _title_shown(title, text))) and open_until:
        return True, f"ok (job data: open until {str(open_until)[:10]})"
    if len(text) < 400:
        return True, "unverified: page needs JavaScript"
    if title and not _title_shown(title, text):   # visible text only: meta tags do not count
        return True, "unverified: page doesn't show the role (needs JavaScript)"
    return True, "ok"


def link_dead(url: str) -> str:
    """Why a job link is dead even though the posting is live in its feed, or "" (also "" on network trouble)."""
    hit = next(((a, s) for a, s in ats.detect_boards(url) if a in ("ashby", "greenhouse", "lever")), None)
    if hit and ats.hosted_board_live(*hit) is False:
        return f"the company switched off its {hit[0]} job pages"
    try:
        r = requests.get(url, headers=UA, timeout=15, allow_redirects=True)
    except requests.RequestException:
        return ""
    if r.status_code in (404, 410):
        return f"HTTP {r.status_code}"
    if r.url.rstrip("/") != url.rstrip("/") and BOARD_HOME.match(r.url.rstrip("/")) and not BOARD_HOME.match(url.rstrip("/")):
        return "redirects to the job board's home page"
    return ""


def _careers_pages(data: Path) -> dict:
    import csv
    pages = {}
    for f in ("wishlist.csv", "pool/directory.csv"):
        p = data / f
        if p.exists():
            with p.open(newline="") as fh:
                for r in csv.DictReader(fh):
                    if r.get("careers_url") and r.get("name"):
                        pages.setdefault(r["name"].lower(), r["careers_url"])
    return pages


def relink(company: str, title: str, careers_url: str) -> tuple[str, bool]:
    """The role's own page on the company's careers site if a link there carries its title, else the careers
    page itself. -> (url, exact)"""
    import html as htmlmod
    from sources import pages
    try:
        r = requests.get(careers_url, headers=UA, timeout=20)
        want = re.sub(r"\W+", " ", (title or "").lower()).strip()
        for href, inner in pages._A.findall(r.text):
            text = re.sub(r"\W+", " ", htmlmod.unescape(pages._TAG.sub(" ", inner)).lower()).strip()
            if want and want in text:
                from urllib.parse import urljoin
                return urljoin(r.url, htmlmod.unescape(href)), True
    except requests.RequestException:
        pass
    return careers_url, False


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

    # boards read successfully in the latest scan, by company: a posting that came from one of them and isn't in
    # it any more has closed, whatever its page says (job pages often need JavaScript, so the page can't tell)
    cstate = _load(data / "state/companies.json", {})
    last_scan = max((v.get("last_ok") or "" for v in cstate.values()), default="")
    fresh_boards: dict = {}
    for k, v in cstate.items():
        if v.get("last_ok") == last_scan and not v.get("fails"):
            fresh_boards.setdefault((v.get("name") or "").lower(), set()).add(k.split(":", 1)[0])

    def source_closed(r) -> str:
        src = (r.get("ats") or "").lower()
        if src == "portfolio":
            return "no longer on the VC portfolio board"
        if src in ats.ADAPTERS and src in fresh_boards.get((r.get("company") or "").lower(), set()):
            return f"no longer in the company's {src} feed"
        return ""

    closed, to_check, unverified, relinked = {}, [], {}, {}
    feed_live = []
    for rid, r in roles.items():
        url = r.get("url") or ""
        if url in live_keys:
            feed_live.append((rid, r))                  # in a live feed: the job exists; its link is checked below
            continue
        if rid in prev and prev[rid].get("permanent"):
            closed[rid] = prev[rid]
            continue
        why = source_closed(r)
        if why:
            closed[rid] = {"company": r.get("company"), "title": r.get("title"), "url": url, "reason": why,
                           "closed_on": datetime.now(timezone.utc).date().isoformat(), "permanent": True}
            continue
        if url.startswith("mailto:"):
            unverified[rid] = "unverified: applications by email"
            continue
        to_check.append((rid, r))

    def one(item):
        rid, r = item
        live, reason = check_url(r.get("url") or "", r.get("title") or "")
        return rid, r, live, reason

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for rid, r, live, reason in ex.map(one, to_check):
            if not live:
                closed[rid] = {"company": r.get("company"), "title": r.get("title"), "url": r.get("url"),
                               "reason": reason, "closed_on": datetime.now(timezone.utc).date().isoformat(),
                               "permanent": reason.startswith("HTTP") or "says" in reason}
            elif reason.startswith("unverified"):
                unverified[rid] = reason
    # the posting is in its feed, but does the link open it? (Cursor: feed lists jobs whose hosted pages are off)
    careers = _careers_pages(data)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        dead = list(ex.map(lambda item: (item, link_dead(item[1].get("url") or "")), feed_live))
    for (rid, r), why in dead:
        if not why:
            continue
        page = careers.get((r.get("company") or "").lower())
        if page:
            new, exact = relink(r.get("company", ""), r.get("title", ""), page)
            relinked[rid] = {"url": new, "exact": exact, "why": why}
            if not exact:
                unverified[rid] = "unverified: listed by the company; its own job link is broken, so this opens their careers page"
        else:
            unverified[rid] = f"unverified: the job link is broken ({why})"
    out = {"updated": datetime.now(timezone.utc).isoformat(timespec="minutes"), "checked": len(to_check),
           "links_checked": len(feed_live), "relinked": relinked,
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
