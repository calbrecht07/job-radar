"""VC portfolio job boards as a company-search (and opportunity) source.

Nearly every VC "portfolio jobs" site runs on one of two platforms:
  * Getro   (talent.seedcamp.com, jobs.dawncapital.com, careers.atomico.com, jobs.accel.com, ...)
  * Consider (careers.balderton.com, careers.playfair.vc, jobs.phoenixcourt.vc, ...)
Both expose the portfolio companies (name, domain, locations, stage) and their jobs (title, locations,
apply URL). `detect(url)` tells which platform a board URL is; `companies()` and `jobs()` normalise both.
"""
from __future__ import annotations

import json
import re

import requests

UA = {"User-Agent": "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/128.0 Safari/537.36 job-radar/1.0"}
TIMEOUT = 40


# ------------------------------------------------------------------ detect
def detect(board_url: str) -> dict | None:
    """Returns {"platform": "getro"|"consider", "network_id"|"board": ..., "base": url} or None."""
    base = board_url.rstrip("/")
    try:
        r = requests.get(base + "/jobs", headers=UA, timeout=TIMEOUT)
        html = r.text
    except requests.RequestException:
        return None
    m = re.search(r'__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if m and "getro" in html:
        try:
            net = json.loads(m.group(1))["props"]["pageProps"]["network"]
            return {"platform": "getro", "network_id": str(net["id"]), "base": base, "name": net.get("name")}
        except Exception:
            pass
    if "Consider" in html and '"board":{' in html:
        i = html.find('"board":{') + len('"board":')
        depth, k = 0, i
        for k in range(i, len(html)):
            depth += (html[k] == "{") - (html[k] == "}")
            if depth == 0:
                break
        try:
            board = json.loads(html[i:k + 1])
        except Exception:
            return None
        tok = re.search(r'csrfToken(?:":|=)"?([A-Za-z0-9_-]{10,})', html)
        return {"platform": "consider", "board": board, "base": base, "csrf": tok.group(1) if tok else None,
                "cookies": r.cookies.get_dict()}
    return None


# ------------------------------------------------------------------- Getro
def _getro_post(network_id: str, what: str, body: dict, base: str) -> dict:
    r = requests.post(f"https://api.getro.com/api/v2/collections/{network_id}/search/{what}", json=body, timeout=TIMEOUT,
                      headers={**UA, "Accept": "application/json", "Content-Type": "application/json",
                               "Origin": base, "Referer": base + "/"})
    r.raise_for_status()
    return r.json()


def getro_companies(network_id: str, base: str, max_pages: int = 60) -> list[dict]:
    out, page = [], 1
    while page <= max_pages:
        data = _getro_post(network_id, "companies", {"page": page, "per_page": 100}, base)
        items = (data.get("results") or {}).get("companies") or []
        for c in items:
            out.append({"name": c.get("name"), "domain": c.get("domain"), "slug": c.get("slug"),
                        "locations": c.get("locations") or [], "stage": c.get("stage"),
                        "industries": c.get("visible_industry_tags") or c.get("industry_tags") or [],
                        "open_jobs": c.get("active_jobs_count"), "headcount_bucket": c.get("head_count"),
                        "board_page": f"{base}/companies/{c.get('slug')}"})
        if len(items) < 100:
            break
        page += 1
    return out


def getro_jobs(network_id: str, base: str, max_pages: int = 80) -> list[dict]:
    out, page = [], 1
    while page <= max_pages:
        data = _getro_post(network_id, "jobs", {"page": page, "per_page": 100}, base)
        items = (data.get("results") or {}).get("jobs") or []
        for j in items:
            org = j.get("organization") or {}
            wm = (j.get("work_mode") or "").lower()
            out.append({"id": str(j.get("id")), "company": org.get("name"), "company_slug": org.get("slug"),
                        "title": j.get("title"), "locations": j.get("locations") or j.get("searchable_locations") or [],
                        "remote": wm == "remote", "workplace": {"on_site": "onsite", "hybrid": "hybrid", "remote": "remote"}.get(wm, ""),
                        "url": j.get("url"), "published": j.get("created_at"), "department": "", "salary": "",
                        "description": ""})
        if len(items) < 100:
            break
        page += 1
    return out


# ---------------------------------------------------------------- Consider
def _consider_post(info: dict, what: str, page: int, size: int = 100) -> dict:
    r = requests.post(f"{info['base']}/api-boards/{what}", timeout=TIMEOUT, cookies=info.get("cookies") or {},
                      headers={**UA, "Accept": "application/json", "Content-Type": "application/json",
                               "X-CSRF-Token": info.get("csrf") or ""},
                      json={"meta": {"size": size, "page": page}, "board": info["board"], "query": {}, "grouped": False})
    r.raise_for_status()
    return r.json()


def consider_companies(info: dict, max_pages: int = 40) -> list[dict]:
    out, page = [], 0
    while page < max_pages:
        data = _consider_post(info, "search-companies", page)
        items = data.get("companies") or []
        for c in items:
            out.append({"name": c.get("name") or c.get("id"), "domain": c.get("domain"), "slug": c.get("slug") or c.get("id"),
                        "locations": c.get("locations") or [], "stage": c.get("stage"), "industries": c.get("markets") or [],
                        "open_jobs": sum(s.get("count", 0) for s in c.get("jobSources") or []),
                        "investors": c.get("investors") or [], "board_page": f"{info['base']}/companies/{c.get('slug') or c.get('id')}"})
        if len(items) < 100:
            break
        page += 1
    return out


def consider_jobs(info: dict, max_pages: int = 60) -> list[dict]:
    out, page = [], 0
    while page < max_pages:
        data = _consider_post(info, "search-jobs", page)
        items = data.get("jobs") or []
        for j in items:
            locs = j.get("locations") or []
            locs = [l if isinstance(l, str) else (l.get("label") or l.get("name") or "") for l in locs]
            remote = bool(j.get("remote")) or any("remote" in l.lower() for l in locs)
            out.append({"id": str(j.get("id") or j.get("applyUrl")), "company": j.get("companyName"), "company_slug": j.get("companySlug"),
                        "title": j.get("title"), "locations": [l for l in locs if l], "remote": remote,
                        "workplace": "remote" if remote else "", "url": j.get("applyUrl") or j.get("url"),
                        "published": j.get("timeStamp") or j.get("createdAt"), "department": ", ".join(j.get("departments") or []),
                        "salary": "", "description": ""})
        if len(items) < 100:
            break
        page += 1
    return out


# ----------------------------------------------------------------- unified
def companies(board_url: str) -> tuple[dict | None, list[dict]]:
    info = detect(board_url)
    if not info:
        return None, []
    if info["platform"] == "getro":
        return info, getro_companies(info["network_id"], info["base"])
    return info, consider_companies(info)


def jobs(info: dict) -> list[dict]:
    if info["platform"] == "getro":
        return getro_jobs(info["network_id"], info["base"])
    return consider_jobs(info)
