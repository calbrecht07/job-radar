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


def from_list_page(url: str, max_companies: int = 2000) -> list[dict]:
    r = requests.get(url, headers=pages.UA, timeout=30)
    r.raise_for_status()
    own = _reg(r.url)
    out, seen = [], set()
    for href, inner in pages._A.findall(r.text):
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
    return out


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
