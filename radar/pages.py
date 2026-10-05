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

from radar import ats

UA = {"User-Agent": "Mozilla/5.0 (compatible; job-radar/1.0; personal job search tool)"}

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
