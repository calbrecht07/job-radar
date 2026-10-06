"""Careers discovery: company website -> careers page -> how its jobs can be read.

    profile = careers.discover({"name": ..., "website": ...})

Profile fields:
  careers_url   the page that lists (or links to) the jobs
  method        feed      a supported job-board feed (board = "ats:slug"; goes into index.csv)
                page      jobs are links on the careers page (monitored by the scan, title filter + job page)
                jobdata   the careers page carries schema.org JobPosting data
                enterprise  an enterprise system with no adapter yet (enterprise = name); monitored as a page
                js_only   the careers page needs JavaScript and shows nothing readable
                no_jobs   a careers page with no job links right now
                none      no careers page found
                blocked   the site refuses automated requests (HTTP 403/429): check by hand
                dead      the website doesn't answer or is gone
  board, enterprise, jobs (current [{title, url, locations}] when readable), final_url, error

Discovery runs weekly in the company search, never in the 6x/day scan. It stays polite: at most ~6 requests
per company, one company per thread, no retries on refusals.
"""
from __future__ import annotations

import html as htmlmod
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests

from sources import boards, jobdata, pages

HEADERS = {**pages.UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
           "Accept-Language": "en-GB,en;q=0.9"}
TIMEOUT = 15

# enterprise recruiting systems without a feed adapter (yet): recognised so they're reported, then watched as pages
ENTERPRISE = [
    ("successfactors", r"successfactors\.(?:com|eu)|jobs\.sap\.com|rmkcdn\.successfactors|/careersection/"),
    ("oracle", r"oraclecloud\.com/hcmUI/CandidateExperience|\.fa\.[\w-]+\.oraclecloud\.com"),
    ("taleo", r"taleo\.net"),
    ("eightfold", r"eightfold\.ai"),
    ("phenom", r"phenompeople\.com|cdn\.phenompeople"),
    ("icims", r"\.icims\.com"),
    ("avature", r"avature\.net"),
    ("jobvite", r"jobs\.jobvite\.com"),
    ("tribepad", r"tribepad\.com"),
    ("jobtrain", r"jobtrain\.co\.uk"),
    ("cornerstone", r"\.csod\.com"),
    ("pinpoint", r"pinpointhq\.com"),
    ("hibob", r"careers\.hibob\.com"),
    ("dayforce", r"dayforcehcm\.com"),
    ("ukg", r"ultipro\.com|recruiting\.ultipro|ukg\.net"),
    ("beamery", r"beamery\.com"),
    ("join", r"join\.com/companies"),
    ("homerun", r"homerun\.co"),
    ("dover", r"app\.dover\.com"),
]
FALLBACK_PATHS = ("/careers", "/jobs", "/join-us")

_CAREER_TXT = re.compile(r"\b(careers?|jobs?|vacanc(y|ies)|join (us|our team|the team)|work (with|for) us|"
                         r"working (at|here|with us)|we'?re hiring|open (roles|positions))\b", re.I)
_CAREER_URL = re.compile(r"career|/jobs?\b|vacanc|join-?us|work-?with-?us|work-?for-?us|opportunit|myworkdayjobs|"
                         r"successfactors|recruit", re.I)
_NEXT_TXT = re.compile(r"(search|view|see|browse|explore|find|current|all|open|latest) (all |our |current )?"
                       r"(jobs|roles|vacancies|opportunities|positions|openings)|job search|vacancies|apply now|"
                       r"open (roles|positions)", re.I)
_SIDE_PAGE = re.compile(r"early[- ]?careers?|graduate|universit|student|intern|apprentice|alumni|culture|benefits|life-at|our-people|stories", re.I)
_IFRAME = re.compile(r"<iframe[^>]+src=[\"']([^\"']+)[\"']", re.I)
_A = pages._A
_TAG = pages._TAG
_SOCIAL = re.compile(r"linkedin\.com|facebook\.com|twitter\.com|x\.com/|instagram\.com|youtube\.com|glassdoor|indeed\.", re.I)


COMPANY_SECONDS = 30            # whole-discovery budget per company: slow sites must not hold a thread
MAX_BYTES = 6_000_000            # some homepages are 4 MB+ (Ramp) and name their job board near the end
_LOCAL = threading.local()


class OverBudget(requests.Timeout):
    pass


def _get(url: str):
    """GET with a per-company deadline: a slow-drip server or a huge sitemap can't stall the run."""
    deadline = getattr(_LOCAL, "deadline", None)
    left = (deadline - time.monotonic()) if deadline else TIMEOUT
    if left <= 1:
        raise OverBudget(f"company time budget used up before {url}")
    r = requests.get(url, headers=HEADERS, timeout=(min(8, left), min(TIMEOUT, left)), allow_redirects=True, stream=True)
    ctype = r.headers.get("content-type", "")
    chunks, size = [], 0
    if "html" in ctype or "xml" in ctype or "json" in ctype or not ctype:
        for chunk in r.iter_content(65536):
            chunks.append(chunk)
            size += len(chunk)
            if size >= MAX_BYTES or (deadline and time.monotonic() > deadline):
                break
    r.close()
    raw = b"".join(chunks)
    text = raw.decode(r.encoding or "utf-8", errors="replace")
    return r, text


def registrable(host_or_url: str) -> str:
    h = urlparse(host_or_url).netloc if "://" in host_or_url else host_or_url
    h = h.lower().split(":")[0]
    h = h[4:] if h.startswith("www.") else h
    parts = h.split(".")
    if len(parts) > 2 and parts[-2] in ("co", "com", "org", "ac", "gov", "net") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _links(raw: str, base: str):
    for href, inner in _A.findall(raw or ""):
        text = re.sub(r"\s+", " ", htmlmod.unescape(_TAG.sub(" ", inner))).strip()
        url = urljoin(base, htmlmod.unescape(href.strip())).split("#")[0]
        if url.startswith("http") and not _SOCIAL.search(url):
            yield url, text


def career_links(raw: str, base: str) -> list[str]:
    """Links on a homepage that lead to careers, best first."""
    scored = {}
    for url, text in _links(raw, base):
        if len(text) > 60:
            continue
        s = (2 if _CAREER_TXT.search(text) else 0) + (1 if _CAREER_URL.search(url) else 0)
        if s >= 2:
            if _SIDE_PAGE.search(url) or _SIDE_PAGE.search(text):
                s -= 1.5                               # early careers, graduates, life-at pages: last resort
            if re.search(r"/(careers?|jobs|open-(positions|roles)|vacancies)/?$", urlparse(url).path, re.I):
                s += 0.5                               # the main careers page itself
            scored[url] = max(scored.get(url, 0), s)
    return sorted(scored, key=lambda u: (-scored[u], len(u)))[:4]


def next_links(raw: str, base: str) -> list[str]:
    """From a careers landing page: the job search / vacancies page, enterprise systems, job iframes."""
    out = []
    for url, text in _links(raw, base):
        if (len(text) <= 60 and _NEXT_TXT.search(text)) or any(re.search(p, url, re.I) for _, p in ENTERPRISE) \
                or boards.detect_boards(url):
            out.append(url)
    out += [urljoin(base, s) for s in _IFRAME.findall(raw or "") if _CAREER_URL.search(s)]
    return [u for u in dict.fromkeys(out) if u.rstrip("/") != base.rstrip("/")][:3]


def enterprise(blob: str) -> list[str]:
    return [name for name, pat in ENTERPRISE if re.search(pat, blob or "", re.I)]


def _sitemap_careers(home_url: str) -> str | None:
    try:
        r, raw = _get(urljoin(home_url, "/sitemap.xml"))
    except requests.RequestException:
        return None
    if r.status_code >= 400:
        return None
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", raw)
    hits = [u for u in locs if re.search(r"/(careers?|jobs|vacancies|join-us|work-with-us)/?$", u, re.I)]
    return min(hits, key=len) if hits else None


# a single posting's URL, as opposed to careers navigation (/careers/life-at-acme, /careers/featured-careers)
_POSTING_URL = re.compile(r"/(job|jobs|vacanc\w*|positions?|openings?|requisitions?|reqs?|roles?|apply|job-details?)/[^/?#]+|"
                          r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-|/\d{4,}[-/]|"
                          r"[?&](job_?id|jobid|gh_jid|req(uisition)?_?id|vacancy_?id|id)=|\d{3,}[^/]*/?$|"
                          r"ashbyhq\.com/|lever\.co/|greenhouse\.io/|workable\.com/|smartrecruiters\.com/", re.I)


def own_jobs(raw: str, url: str, company: str) -> list[dict]:
    """Job postings linked from a careers page that belong to this company (not careers navigation, not a VC
    page linking its portfolio companies' jobs)."""
    own = registrable(url)
    out = []
    for j in pages.job_links(raw, url):
        if not _POSTING_URL.search(j["url"]):
            continue
        host = registrable(j["url"])
        if host == own or any(boards.plausible(company, b) for b in [host.split(".")[0]] + j["url"].split("/")[3:5] if b):
            out.append({"title": j["title"], "url": j["url"], "locations": []})
    return out


def discover(company: dict, seconds: float = COMPANY_SECONDS) -> dict:
    """See the module docstring. Every request shares one deadline of `seconds`."""
    _LOCAL.deadline = time.monotonic() + seconds
    try:
        return _discover(company)
    finally:
        _LOCAL.deadline = None


def _discover(company: dict) -> dict:
    name, site = company.get("name", ""), company.get("website", "")
    prof = {"checked": datetime.now(timezone.utc).date().isoformat(), "careers_url": "", "method": "none",
            "board": "", "enterprise": "", "jobs": [], "final_url": "", "error": ""}
    if not site:
        return {**prof, "method": "dead", "error": "no website"}
    if not site.startswith("http"):
        site = "https://" + site
    try:
        r, home = _get(site)
    except requests.RequestException as e:
        return {**prof, "method": "dead", "error": type(e).__name__}
    prof["final_url"] = r.url
    if r.status_code in (401, 403, 429):
        return {**prof, "method": "blocked", "error": f"HTTP {r.status_code}"}
    if r.status_code >= 400:
        return {**prof, "method": "dead", "error": f"HTTP {r.status_code}"}

    # 1. find the careers page
    visited, texts = [r.url], [home]
    cands = career_links(home, r.url)
    if not cands:
        sm = _sitemap_careers(r.url)
        cands = [sm] if sm else []
    if not cands:
        for path in FALLBACK_PATHS:
            try:
                rr, raw = _get(urljoin(r.url, path))
            except requests.RequestException:
                continue
            if rr.status_code < 400 and _CAREER_TXT.search(pages._DROP.sub(" ", raw)[:300000]):
                cands = [rr.url]
                break
    # boards linked straight from the homepage count too
    if not cands and not boards.detect_boards(home):
        return prof

    for u in cands[:2]:
        try:
            rr, raw = _get(u)
        except requests.RequestException as e:
            prof["error"] = type(e).__name__
            continue
        if rr.status_code >= 400:
            prof["error"] = f"careers page HTTP {rr.status_code}"
            continue
        visited.append(rr.url)
        texts.append(raw)
        prof["careers_url"] = rr.url
        # 2. one more hop if the landing page has no jobs of its own
        if not own_jobs(raw, rr.url, name) and not jobdata.postings(raw) and not boards.detect_boards(raw):
            for nxt in next_links(raw, rr.url):
                try:
                    r3, raw3 = _get(nxt)
                except requests.RequestException:
                    continue
                if r3.status_code < 400:
                    visited.append(r3.url)
                    texts.append(raw3)
                    prof["careers_url"] = r3.url
                    break
        break

    blob = "\n".join(texts) + "\n" + "\n".join(visited)
    # 3. how can the jobs be read? best method first
    for a, s in boards.detect_boards(blob):
        if a in boards.ADAPTERS and boards.plausible(name, s, site):
            prof.update(method="feed", board=f"{a}:{s}")
            # the board is what matters (the scan reads its jobs); Workday feeds run to 30+ requests, so skip
            if a == "workday" or (_LOCAL.deadline and _LOCAL.deadline - time.monotonic() < 5):
                return prof
            try:
                prof["jobs"] = [{"title": p["title"], "url": p["url"], "locations": p.get("locations") or [],
                                 "remote": p.get("remote")} for p in boards.fetch(a, s)]
            except Exception as e:
                prof["error"] = f"feed {a}:{s}: {type(e).__name__}"
            return prof
    last_url, last = visited[-1], texts[-1] if len(texts) > 1 else ""
    found = jobdata.postings(last, last_url) if last else []
    if found:
        prof.update(method="jobdata", jobs=[{"title": p["title"], "url": p["url"], "locations": p["locations"],
                                             "remote": p["remote"]} for p in found if not jobdata.expired(p)])
        return prof
    ent = enterprise(blob)
    links = own_jobs(last, last_url, name) if last else []
    if ent:
        prof.update(method="enterprise", enterprise=ent[0], jobs=links)
    elif links:
        prof.update(method="page", jobs=links)
    elif last:
        prof["method"] = "js_only" if len(pages.page_text(last)) < 25 else "no_jobs"
    return prof


# ------------------------------------------------------------ browser pass
RENDER_METHODS = ("js_only", "no_jobs", "blocked", "none")


def render_discover(company: dict, prof: dict, browser, wanted=None, locate=None, max_job_pages: int = 12) -> dict:
    """Second pass with a real browser for companies plain requests couldn't read (RENDER_METHODS).
    In order of preference: a job board the page loads (-> feed), jobs in the page's own data calls, posting
    links in the rendered page. Titles that pass `wanted(title)` but carry no location get their job page
    rendered and `locate(html, url)` -> {locations, remote}. Returns the updated profile; method "rendered"
    keeps its jobs in profile["jobs"] for the scan."""
    from radar.browser import jobs_from_json
    name, site = company.get("name", ""), company.get("website", "")
    if site and not site.startswith("http"):
        site = "https://" + site
    out = {**prof, "rendered": datetime.now(timezone.utc).date().isoformat()}
    start = prof.get("careers_url") or site
    if not start:
        return out
    pages = [browser.render(start)]
    if not pages[0]["html"]:
        out["error"] = f"render: {pages[0]['error'] or pages[0]['status']}"
        return out
    if not prof.get("careers_url"):                         # find the careers page in the rendered homepage
        cl = career_links(pages[0]["html"], pages[0]["final_url"])
        if cl:
            pages.append(browser.render(cl[0]))
    last = pages[-1]

    def found(pg):
        return own_jobs(pg["html"], pg["final_url"], name) or jobs_from_json(pg["json"], pg["final_url"]) \
            or boards.detect_boards(pg["html"] + "\n" + "\n".join(pg["requests"]))
    if not found(last):                                     # the job list one page deeper, or on another site
        for nxt in next_links(last["html"], last["final_url"])[:1]:
            pages.append(browser.render(nxt))
    last = pages[-1]
    if last["html"]:
        out["careers_url"] = last["final_url"]
    blob = "\n".join(p["html"] + "\n" + "\n".join(p["requests"]) for p in pages)

    for a, s in boards.detect_boards(blob):
        if a in boards.ADAPTERS and boards.plausible(name, s, site):
            out.update(method="feed", board=f"{a}:{s}", jobs=[], error="")
            return out
    data = []
    for p in pages:
        data += [x for x in jobdata.postings(p["html"], p["final_url"]) if not jobdata.expired(x)]
    from_json = max((jobs_from_json(p["json"], p["final_url"]) for p in pages), key=len, default=[])
    links = max(([{"title": j["title"], "url": j["url"], "locations": [], "remote": None}
                  for j in own_jobs(p["html"], p["final_url"], name)] for p in pages), key=len, default=[])
    located_json = sum(1 for j in from_json if j["locations"])
    if data:
        jobs = [{"title": x["title"], "url": x["url"], "locations": x["locations"], "remote": x["remote"]} for x in data]
    elif from_json and (located_json * 2 >= len(from_json) or len(from_json) > len(links)):
        jobs = from_json
    else:
        jobs = links
    if not jobs:
        ent = enterprise(blob)
        if ent:
            out.update(method="enterprise", enterprise=ent[0])
        return out
    # where are the roles that matter? open their pages (bounded)
    opened = 0
    for j in jobs:
        if opened >= max_job_pages or j.get("locations") or not j.get("url") or (wanted and not wanted(j["title"])):
            continue
        opened += 1
        pg = browser.render(j["url"])
        if pg["html"] and locate:
            j.update({k: v for k, v in locate(pg["html"], j["url"]).items() if v})
    out.update(method="rendered", jobs=jobs[:500], error="")
    return out
