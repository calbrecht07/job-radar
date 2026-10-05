"""Directories: pages that list companies (VC portfolios, accelerator alumni, association members, award lists).

    companies(source) -> [{"name", "website"}]

source = {"url": ..., "type": "getro" | "consider" | "list" | "auto"}
  getro / consider  VC portfolio boards (sources.portfolio), which give each company's domain
  list              any other page: external links on it are taken as member companies
  auto              portfolio platform if detected, else list
"""
from __future__ import annotations

import html as htmlmod
import re
from urllib.parse import urljoin, urlparse

import requests

from sources import boards, pages, portfolio

_SKIP_HOSTS = re.compile(
    r"linkedin|facebook|twitter|x\.com|instagram|youtube|tiktok|medium\.com|github\.com|google\.|apple\.com|"
    r"microsoft\.com|wikipedia|crunchbase|glassdoor|indeed|eventbrite|mailchimp|hubspot|typeform|calendly|"
    r"vimeo|spotify|substack|bit\.ly|goo\.gl|t\.co$|wordpress\.(com|org)|squarespace|wix\.com|webflow|"
    r"cloudflare|gov\.uk$|europa\.eu|doubleclick|cookiebot|onetrust|w3\.org|schema\.org|gstatic|"
    r"ashbyhq|greenhouse\.io|lever\.co|workable|myworkdayjobs|getro|consider\.com", re.I)


def _reg(url: str) -> str:
    h = urlparse(url).netloc.lower().split(":")[0]
    h = h[4:] if h.startswith("www.") else h
    parts = h.split(".")
    if len(parts) > 2 and parts[-2] in ("co", "com", "org", "ac", "net") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


_CHROME = re.compile(r"<(header|nav|footer)\b[^>]*>.*?</\1>", re.S | re.I)
MIN_COMPANIES = 8                     # fewer outbound company links than this: not a directory page


def from_list_page(url: str, max_companies: int = 2000) -> list[dict]:
    """Outbound links on a page listing companies. Header, nav and footer links (sister sites, sponsors) are
    ignored, and a page with fewer than MIN_COMPANIES companies returns [] rather than noise: most directory
    sites link to their own profile pages, which this can't use."""
    r = requests.get(url, headers=pages.UA, timeout=30)
    r.raise_for_status()
    own = _reg(r.url)
    out, seen = [], set()
    for href, inner in pages._A.findall(_CHROME.sub(" ", r.text)):
        link = urljoin(r.url, htmlmod.unescape(href.strip()))
        if not link.startswith("http"):
            continue
        dom = _reg(link)
        if not dom or dom == own or dom in seen or _SKIP_HOSTS.search(dom):
            continue
        text = re.sub(r"\s+", " ", htmlmod.unescape(pages._TAG.sub(" ", inner))).strip()
        if not text:                                   # logo links: use the image's alt text
            m = re.search(r'alt=["\']([^"\']{2,60})["\']', inner)
            text = m.group(1).strip() if m else ""
        if not text or len(text) > 60 or re.search(r"\b(read more|learn more|visit|website|click here|here)\b", text, re.I):
            text = dom.split(".")[0].replace("-", " ").title()
        seen.add(dom)
        out.append({"name": text, "website": f"https://{dom}"})
        if len(out) >= max_companies:
            break
    return out if len(out) >= MIN_COMPANIES else []


def companies(source: dict) -> list[dict]:
    url, kind = source["url"], (source.get("type") or "auto").lower()
    if kind in ("getro", "consider", "auto"):
        try:
            info, comps = portfolio.companies(url)
        except portfolio.TooBroad:
            info, comps = None, []
        if info:
            return [{"name": c.get("name"), "website": ("https://" + c["domain"]) if c.get("domain") else ""}
                    for c in comps if c.get("name")]
        if kind != "auto":
            return []
    return from_list_page(url)


if __name__ == "__main__":            # preview what the pipeline would read: python -m sources.directories URL [type]
    import sys
    found = companies({"url": sys.argv[1], "type": sys.argv[2] if len(sys.argv) > 2 else "auto"})
    print(f"{len(found)} companies")
    for c in found[:40]:
        print(f"  {c['name'][:40]:40} {c['website']}")
