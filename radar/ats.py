"""Adapters for public job-board feeds. Each returns a list of normalised postings.

Normalised posting:
  id, title, locations (list[str]), remote (bool|None), workplace (str),
  url, published (ISO str|None), department, salary, description (plain text)
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone

import requests

UA = {"User-Agent": "job-radar/1.0 (personal job search tool)"}
TIMEOUT = 25


class NotFound(Exception):
    pass


def _resp(url: str, params: dict | None = None):
    r = requests.get(url, params=params, headers=UA, timeout=TIMEOUT)
    if r.status_code in (404, 410):
        raise NotFound(url)
    r.raise_for_status()
    return r


def _get(url: str, params: dict | None = None):
    return _resp(url, params).json()


def _get_text(url: str, params: dict | None = None) -> str:
    return _resp(url, params).text


_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")


def strip_html(s: str | None) -> str:
    if not s:
        return ""
    s = html.unescape(s)  # Greenhouse double-escapes
    s = re.sub(r"<(br|/p|/li|/h\d|/div)[^>]*>", "\n", s, flags=re.I)
    s = _TAG.sub(" ", s)
    s = html.unescape(s)
    s = _WS.sub(" ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def _iso_ms(ms) -> str | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()
    except Exception:
        return None


# ------------------------------------------------------------------ Ashby
def ashby(slug: str) -> list[dict]:
    data = _get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", {"includeCompensation": "true"})
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""]
        locs += [s.get("location", "") for s in (j.get("secondaryLocations") or []) if isinstance(s, dict)]
        addr = (j.get("address") or {}).get("postalAddress") or {}
        if addr.get("addressCountry"):
            locs.append(addr["addressCountry"])
        wp = (j.get("workplaceType") or "").lower()
        comp = j.get("compensation") or {}
        out.append({
            "id": j.get("id"),
            "title": j.get("title", ""),
            "locations": [l for l in locs if l],
            "remote": bool(j.get("isRemote")) or wp == "remote",
            "workplace": wp or ("remote" if j.get("isRemote") else ""),
            "url": j.get("jobUrl") or f"https://jobs.ashbyhq.com/{slug}/{j.get('id')}",
            "published": j.get("publishedAt"),
            "department": j.get("department") or j.get("team") or "",
            "salary": comp.get("compensationTierSummary") or comp.get("scrapeableCompensationSalarySummary") or "",
            "description": j.get("descriptionPlain") or strip_html(j.get("descriptionHtml")),
        })
    return out


# ------------------------------------------------------------- Greenhouse
def greenhouse(slug: str, eu_first: bool = False) -> list[dict]:
    # boards-api.greenhouse.io serves both US and EU (job-boards.eu.greenhouse.io) boards.
    data = _get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", {"content": "true"})
    out = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        locs = [loc] + [o.get("name", "") for o in (j.get("offices") or [])]
        desc = strip_html(j.get("content"))
        out.append({
            "id": str(j.get("id")),
            "title": j.get("title", ""),
            "locations": [l for l in locs if l],
            "remote": None,  # Greenhouse has no flag; inferred from location text
            "workplace": "",
            "url": j.get("absolute_url", ""),
            "published": j.get("first_published") or j.get("updated_at"),
            "department": ", ".join(d.get("name", "") for d in (j.get("departments") or [])),
            "salary": "",
            "description": desc,
        })
    return out


# ------------------------------------------------------------------ Lever
def lever(slug: str, eu_first: bool = False) -> list[dict]:
    hosts = ["api.lever.co", "api.eu.lever.co"]
    if eu_first:
        hosts.reverse()
    data = None
    for h in hosts:
        try:
            got = _get(f"https://{h}/v0/postings/{slug}", {"mode": "json"})
        except (NotFound, requests.ConnectionError):
            continue
        data = got
        if got:  # non-empty: done; empty: still try the other region
            break
    if data is None:
        raise NotFound(slug)
    out = []
    for j in data:
        cat = j.get("categories") or {}
        locs = [cat.get("location", "")] + list(cat.get("allLocations") or [])
        wp = (j.get("workplaceType") or "").lower()
        parts = [j.get("descriptionPlain") or ""]
        for lst in j.get("lists") or []:
            parts.append(lst.get("text", ""))
            parts.append(strip_html(lst.get("content")))
        parts.append(j.get("additionalPlain") or "")
        sr = j.get("salaryRange") or {}
        salary = f"{sr.get('currency','')} {sr.get('min','')}-{sr.get('max','')} {sr.get('interval','')}".strip() if sr else ""
        out.append({
            "id": j.get("id"),
            "title": j.get("text", ""),
            "locations": list(dict.fromkeys(l for l in locs if l)),
            "remote": wp == "remote",
            "workplace": "" if wp == "unspecified" else wp,
            "url": j.get("hostedUrl", ""),
            "published": _iso_ms(j.get("createdAt")),
            "department": cat.get("team") or cat.get("department") or "",
            "salary": salary if sr else "",
            "description": "\n".join(p for p in parts if p),
        })
    return out


# --------------------------------------------------------------- Workable
def workable(slug: str) -> list[dict]:
    data = _get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}", {"details": "true"})
    out = []
    for j in data.get("jobs", []):
        locs = []
        for l in j.get("locations") or []:
            locs.append(", ".join(x for x in [l.get("city"), l.get("region"), l.get("country")] if x))
        if not locs:
            locs.append(", ".join(x for x in [j.get("city"), j.get("state"), j.get("country")] if x))
        code = j.get("shortcode", "")
        out.append({
            "id": code,
            "title": j.get("title", ""),
            "locations": [l for l in locs if l],
            "remote": bool(j.get("telecommuting")),
            "workplace": "remote" if j.get("telecommuting") else "",
            "url": j.get("url") or f"https://apply.workable.com/{slug}/j/{code}/",
            "published": j.get("published_on") or j.get("created_at"),
            "department": j.get("department") or "",
            "salary": "",
            "description": strip_html(j.get("description")),
        })
    return out


# ------------------------------------------------------------------ Breezy
def breezy(slug: str) -> list[dict]:
    data = _get(f"https://{slug}.breezy.hr/json")
    out = []
    for j in data if isinstance(data, list) else []:
        loc = j.get("location") or {}
        locs = [loc.get("name", "")] + [l.get("name", "") for l in (j.get("locations") or []) if isinstance(l, dict)]
        remote = bool(loc.get("is_remote"))
        out.append({
            "id": j.get("id") or j.get("friendly_id"),
            "title": j.get("name", ""),
            "locations": list(dict.fromkeys(l for l in locs if l)),
            "remote": remote,
            "workplace": "remote" if remote else "",
            "url": j.get("url", ""),
            "published": j.get("published_date"),
            "department": j.get("department") or "",
            "salary": j.get("salary") or "",
            "description": "",  # Breezy's feed has no description; Claude opens the link
        })
    return out


# ---------------------------------------------------------------- Recruitee
def recruitee(slug: str) -> list[dict]:
    data = _get(f"https://{slug}.recruitee.com/api/offers/")
    out = []
    for j in data.get("offers", []):
        locs = [j.get("location", "")] + [", ".join(x for x in [l.get("city"), l.get("country")] if x)
                                          for l in (j.get("locations") or []) if isinstance(l, dict)]
        remote = bool(j.get("remote"))
        wp = "remote" if remote else ("hybrid" if j.get("hybrid") else ("onsite" if j.get("on_site") else ""))
        out.append({
            "id": str(j.get("id")),
            "title": j.get("title", ""),
            "locations": list(dict.fromkeys(l for l in locs if l)),
            "remote": remote,
            "workplace": wp,
            "url": j.get("careers_url", ""),
            "published": j.get("published_at") or j.get("created_at"),
            "department": j.get("department") or "",
            "salary": "",
            "description": strip_html((j.get("description") or "") + "\n" + (j.get("requirements") or "")),
        })
    return out


# ----------------------------------------------------------------- Personio
def personio(slug: str) -> list[dict]:
    import xml.etree.ElementTree as ET
    root = ET.fromstring(_get_text(f"https://{slug}.jobs.personio.de/xml", {"language": "en"}))
    out = []
    for pos in root.iter("position"):
        g = lambda tag: (pos.findtext(tag) or "").strip()
        offices = [g("office")] + [(o.text or "").strip() for o in pos.findall("additionalOffices/office")]
        desc = "\n".join(strip_html((d.findtext("name") or "") + "\n" + (d.findtext("value") or ""))
                         for d in pos.findall("jobDescriptions/jobDescription"))
        out.append({
            "id": g("id"),
            "title": g("name"),
            "locations": [o for o in offices if o],
            "remote": None,
            "workplace": "",
            "url": f"https://{slug}.jobs.personio.de/job/{g('id')}",
            "published": g("createdAt") or None,
            "department": g("department"),
            "salary": "",
            "description": desc,
        })
    return out


# ---------------------------------------------------------- SmartRecruiters
def smartrecruiters(slug: str) -> list[dict]:
    out, offset = [], 0
    while True:
        data = _get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings", {"limit": 100, "offset": offset})
        items = data.get("content", [])
        for j in items:
            loc = j.get("location") or {}
            place = ", ".join(x for x in [loc.get("city"), loc.get("region"), loc.get("country")] if x)
            remote = bool(loc.get("remote"))
            out.append({
                "id": j.get("id"),
                "title": j.get("name", ""),
                "locations": [place] if place else [],
                "remote": remote,
                "workplace": "remote" if remote else ("hybrid" if loc.get("hybrid") else ""),
                "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
                "published": j.get("releasedDate"),
                "department": (j.get("department") or {}).get("label", ""),
                "salary": "",
                "description": "",  # needs a per-posting call; Claude opens the link
            })
        offset += len(items)
        if not items or offset >= data.get("totalFound", 0) or offset >= 1000:
            break
    return out


# ----------------------------------------------------------------- BambooHR
def bamboohr(slug: str) -> list[dict]:
    data = _get(f"https://{slug}.bamboohr.com/careers/list")
    out = []
    for j in data.get("result", []):
        loc = j.get("location") or {}
        place = ", ".join(x for x in [loc.get("city"), loc.get("state"), loc.get("country")] if x)
        ats_loc = j.get("atsLocation") or {}
        if not place:
            place = ", ".join(x for x in [ats_loc.get("city"), ats_loc.get("state"), ats_loc.get("country")] if x)
        remote = bool(j.get("isRemote")) or (j.get("locationType") == "1")
        out.append({
            "id": str(j.get("id")),
            "title": j.get("jobOpeningName", ""),
            "locations": [place] if place else [],
            "remote": remote,
            "workplace": "remote" if remote else "",
            "url": f"https://{slug}.bamboohr.com/careers/{j.get('id')}",
            "published": None,
            "department": j.get("departmentLabel") or "",
            "salary": "",
            "description": "",
        })
    return out


ADAPTERS = {
    "bamboohr": bamboohr,
    "ashby": ashby,
    "greenhouse": greenhouse, "greenhouse-eu": greenhouse,
    "lever": lever, "lever-eu": lambda s: lever(s, eu_first=True),
    "workable": workable,
    "breezy": breezy,
    "recruitee": recruitee,
    "personio": personio,
    "smartrecruiters": smartrecruiters,
}


def fetch(ats: str, slug: str) -> list[dict]:
    fn = ADAPTERS.get(ats.lower())
    if not fn:
        raise ValueError(f"unsupported ats {ats}")
    return fn(slug)


# ------------------------------------------------- board detection (audit)
BOARD_PATTERNS = [
    ("ashby", r"jobs\.ashbyhq\.com/([\w.%-]+)"),
    ("ashby", r"api\.ashbyhq\.com/posting-api/job-board/([\w.%-]+)"),
    ("greenhouse", r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)"),
    ("greenhouse", r"boards-api\.greenhouse\.io/v1/boards/([\w-]+)"),
    ("lever", r"jobs\.lever\.co/([\w.-]+)"),
    ("lever-eu", r"jobs\.eu\.lever\.co/([\w.-]+)"),
    ("workable", r"apply\.workable\.com/([\w-]+)"),
    ("breezy", r"([\w-]+)\.breezy\.hr"),
    ("recruitee", r"([\w-]+)\.recruitee\.com"),
    ("personio", r"([\w-]+)\.jobs\.personio\.(?:de|com)"),
    ("smartrecruiters", r"(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)"),
    ("bamboohr", r"([\w-]+)\.bamboohr\.com/(?:careers|jobs)"),
    # recognised but not supported by a feed adapter (reported, checked by page)
    ("teamtailor", r"([\w-]+)\.teamtailor\.com"),
    ("pinpoint", r"([\w-]+)\.pinpointhq\.com"),
    ("workday", r"([\w-]+)\.wd\d+\.myworkdayjobs\.com"),
    ("hibob", r"([\w-]+)\.careers\.hibob\.com"),
    ("rippling", r"ats\.rippling\.com/([\w-]+)"),
    ("dover", r"app\.dover\.com/jobs/([\w-]+)"),
    ("trakstar", r"([\w-]+)\.hire\.trakstar\.com"),
]
_IGNORE_SLUGS = {"www", "api", "app", "jobs", "careers", "embed", "j", "static", "assets", "cdn", "js",
                 "staticfe", "resources", "bhrpendo", "support", "help", "marketing"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def plausible(company: str, slug: str, url: str = "") -> bool:
    """Does a board slug plausibly belong to this company (and not e.g. a VC's portfolio company)?"""
    sl = _norm(re.sub(r"[-_](careers?|jobs|hq|inc|ltd|\d+)$", "", slug, flags=re.I))
    if len(sl) < 3:
        return False
    names = {_norm(company), _norm(re.sub(r"\(.*?\)", "", company))}
    names |= {_norm(w) for w in re.findall(r"[A-Za-z0-9]+", company) if len(w) >= 4}
    host = re.sub(r"^www\.", "", (re.findall(r"https?://([^/]+)", url) or [""])[0].lower())
    if host:
        names.add(_norm(host.split(".")[0]))
    return any(n and (n.startswith(sl) or sl.startswith(n) or sl in n) for n in names if len(n) >= 3)


def detect_boards(html_text: str) -> list[tuple[str, str]]:
    """Job boards referenced in a careers page's HTML: [(ats, slug)], supported ones first."""
    found = []
    for ats_name, pat in BOARD_PATTERNS:
        for m in re.finditer(pat, html_text or "", re.I):
            slug = m.group(1).strip().rstrip(".")
            if slug.lower() in _IGNORE_SLUGS:
                continue
            if (ats_name, slug) not in found:
                found.append((ats_name, slug))
    return sorted(found, key=lambda x: x[0] not in ADAPTERS)
