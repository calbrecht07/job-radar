"""Careers pages: change detection + job-board audit for watchlist companies.

For each watchlist company with a careers_url:
  * fetch the page, extract visible text, compare with last run -> changed / unchanged
  * keep the lines that were added since last time (likely new job titles)
  * detect which job boards the page links to (Ashby, Greenhouse, Teamtailor, ...)
Pages that render jobs with JavaScript show little text: marked "js_only" so a
reviewer checks them in a browser now and then.
"""
from __future__ import annotations

import hashlib
import html as htmlmod
import re

import requests

from sources import boards as ats

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/128.0 Safari/537.36 job-radar/1.0",
      "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-GB,en;q=0.9"}

_A = re.compile(r"<a\b[^>]*?href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.S | re.I)
_JOBLIKE = re.compile(r"/(jobs?|careers?|positions?|vacanc|openings?|roles?|opportunit|join-us|o|p|j)/[^/?#]+|gh_jid=|lever\.co/|ashbyhq\.com/|"
                      r"workable\.com/|teamtailor\.com/jobs/|breezy\.hr/p/|recruitee\.com/o/|personio\.\w+/job/|bamboohr\.com/careers/\d|"
                      r"hire\.trakstar\.com/jobs/|pinpointhq\.com/|rippling\.com/[^/]+/jobs/|charliehr\.com/", re.I)
_NAV = re.compile(r"^(jobs?|careers?|apply( now)?|view( all)?( jobs| roles| openings)?|see (all|more)|open (roles|positions)|"
                  r"learn more|read more|join us|here|more|back|next|previous|home|about|blog|contact|privacy|terms|"
                  r"cookie.*|log ?in|sign ?up|linkedin|twitter|x|instagram|github|all jobs|current openings)$", re.I)


def job_links(raw_html: str, base_url: str) -> list[dict]:
    """Links on a careers page that look like individual job postings: [{title, url}]."""
    from urllib.parse import urljoin
    out, seen = [], set()
    for href, inner in _A.findall(raw_html or ""):
        text = re.sub(r"\s+", " ", htmlmod.unescape(_TAG.sub(" ", inner))).strip()
        url = urljoin(base_url, htmlmod.unescape(href.strip()))
        if not url.startswith("http") or url.rstrip("/") == base_url.rstrip("/") or url in seen:
            continue
        if not (6 <= len(text) <= 140) or _NAV.match(text) or not _JOBLIKE.search(url):
            continue
        seen.add(url)
        out.append({"title": text, "url": url})
    return out[:200]

_DROP = re.compile(r"<(script|style|noscript|svg|head)[^>]*>.*?</\1>", re.S | re.I)
_BLOCK = re.compile(r"<(br|/p|/li|/h\d|/div|/a|/tr|/td|/section|/article|/span)[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")


def page_text(raw_html: str) -> list[str]:
    s = _DROP.sub(" ", raw_html or "")
    s = _BLOCK.sub("\n", s)
    s = htmlmod.unescape(_TAG.sub(" ", s))
    lines = []
    for line in s.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if 2 < len(line) < 200 and line not in lines:
            lines.append(line)
    return lines


def check_page(url: str, prev: dict | None) -> dict:
    """Returns the new page state (also used as the report row)."""
    prev = prev or {}
    out = {"url": url}
    try:
        r = requests.get(url, headers=UA, timeout=30, allow_redirects=True)
        out["http"] = r.status_code
        if r.status_code >= 400:
            out["status"] = "error"
            out["error"] = f"HTTP {r.status_code}"
            out["lines_hash"] = prev.get("lines_hash")
            return out
        raw = r.text
    except requests.RequestException as e:
        return {**out, "status": "error", "error": type(e).__name__, "lines_hash": prev.get("lines_hash")}

    lines = page_text(raw)
    out["jobs"] = job_links(raw, getattr(r, "url", None) or url)
    out["boards"] = [f"{a}:{s}" for a, s in ats.detect_boards(raw)]
    h = hashlib.sha1("\n".join(lines).encode()).hexdigest()[:16]
    out["lines_hash"] = h
    out["text_lines"] = len(lines)
    old = set(prev.get("lines") or [])
    out["lines"] = lines[:400]                     # kept for the next diff
    if len(lines) < 25:
        out["status"] = "js_only"                  # little server-rendered text
    elif not prev.get("lines_hash"):
        out["status"] = "first_check"
    elif h == prev.get("lines_hash"):
        out["status"] = "unchanged"
    else:
        out["status"] = "changed"
        out["added"] = [l for l in lines if l not in old][:60]
    return out
