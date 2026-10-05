"""Funding and expansion news as a company-search source.

Reads RSS feeds listed in settings (news.feeds) and keeps items from the last N days whose
title or summary mentions a raise, a launch into the target region, or a new office. The weekly
company search writes them to pool/news.json; the agent reads that file (never the web) and
decides which companies to add to the wishlist.
"""
from __future__ import annotations

import html as htmlmod
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

UA = {"User-Agent": "Mozilla/5.0 (compatible; job-radar/1.0; personal job search tool)"}
_TAG = re.compile(r"<[^>]+>")

DEFAULT_FEEDS = [
    "https://www.uktech.news/feed",
    "https://sifted.eu/feed",
    "https://techcrunch.com/category/venture/feed/",
    "https://www.eu-startups.com/feed",
    "https://tech.eu/feed/",
    "https://www.businesscloud.co.uk/feed/",
]
_TRIGGER = re.compile(
    r"\b(raise[sd]?|raising|secures?|closes?|lands?|bags?|nabs?|series [a-e]\b|seed round|pre-seed|funding|"
    r"opens? (an? )?(new )?(office|hq)|launch(es|ed)? in|expands? (in)?to|enter(s|ing)? the|hiring|new fund|fund [iv]+\b|"
    r"\$\d|€\d|£\d|\d+m\b|\d+ ?million)", re.I)


def _text(s: str | None) -> str:
    return re.sub(r"\s+", " ", htmlmod.unescape(_TAG.sub(" ", s or ""))).strip()


def _date(s: str | None):
    if not s:
        return None
    try:
        return parsedate_to_datetime(s)
    except Exception:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None


def fetch_feed(url: str) -> list[dict]:
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for it in root.iter("item"):                      # RSS 2.0
        items.append({"title": _text(it.findtext("title")), "url": (it.findtext("link") or "").strip(),
                      "summary": _text(it.findtext("description"))[:600], "published": _date(it.findtext("pubDate")),
                      "source": url})
    ns = "{http://www.w3.org/2005/Atom}"
    for e in root.iter(ns + "entry"):                 # Atom
        link = next((l.get("href") for l in e.findall(ns + "link") if l.get("rel", "alternate") == "alternate"), "")
        items.append({"title": _text(e.findtext(ns + "title")), "url": link or "",
                      "summary": _text(e.findtext(ns + "summary") or e.findtext(ns + "content"))[:600],
                      "published": _date(e.findtext(ns + "updated") or e.findtext(ns + "published")), "source": url})
    return items


def collect(feeds: list[str] | None, days: int = 8, region_terms: list[str] | None = None) -> list[dict]:
    """News items from the last `days` that look like a funding/expansion event. If region_terms is
    given (e.g. ["london", "uk"]), items must also mention one of them."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    region = re.compile("|".join(region_terms), re.I) if region_terms else None
    out, seen = [], set()
    for f in feeds or DEFAULT_FEEDS:
        try:
            items = fetch_feed(f)
        except Exception as e:
            out.append({"error": f"{f}: {type(e).__name__}"})
            continue
        for it in items:
            if it["url"] in seen or not it["title"]:
                continue
            d = it.get("published")
            if d and d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            if d and d < cutoff:
                continue
            blob = it["title"] + " " + it["summary"]
            if not _TRIGGER.search(blob):
                continue
            if region and not region.search(blob):
                continue
            seen.add(it["url"])
            it["published"] = d.isoformat() if d else None
            out.append(it)
    out.sort(key=lambda x: x.get("published") or "", reverse=True)
    return out
