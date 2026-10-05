"""schema.org JobPosting data embedded in web pages (JSON-LD), the format Google Jobs reads.

Many companies that post jobs only on their own website still publish this block on each job page, and
some on their listing page. It gives title, location, remote flag and closing date without parsing the
page's layout, so it works across custom careers sites.

    postings(html) -> [normalised posting]   (same shape as sources.boards adapters)
"""
from __future__ import annotations

import html as htmlmod
import json
import re
from datetime import datetime, timezone

from sources.boards import strip_html

_BLOCK = re.compile(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", re.S | re.I)


def _is_posting(o: dict) -> bool:
    t = o.get("@type")
    return t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t)


def _text(v) -> str:
    if isinstance(v, dict):
        v = v.get("name") or v.get("@value") or ""
    return htmlmod.unescape(str(v or "")).strip()


def _locations(o: dict) -> list[str]:
    out = []
    jl = o.get("jobLocation") or []
    for loc in jl if isinstance(jl, list) else [jl]:
        if not isinstance(loc, dict):
            continue
        addr = loc.get("address") or {}
        if isinstance(addr, str):
            out.append(addr)
            continue
        bits = [_text(addr.get(k)) for k in ("addressLocality", "addressRegion", "addressCountry")]
        place = ", ".join(b for b in bits if b) or _text(loc.get("name"))
        if place:
            out.append(place)
    for req in o.get("applicantLocationRequirements") or []:
        if isinstance(req, dict) and req.get("name"):
            out.append(f"Remote - {_text(req['name'])}")
    return list(dict.fromkeys(out))


def _salary(o: dict) -> str:
    bs = o.get("baseSalary")
    if not isinstance(bs, dict):
        return ""
    v = bs.get("value") or {}
    cur = bs.get("currency") or ""
    if isinstance(v, dict):
        lo, hi, unit = v.get("minValue"), v.get("maxValue"), v.get("unitText") or ""
        rng = "–".join(str(x) for x in (lo, hi) if x is not None) or str(v.get("value") or "")
        return f"{cur} {rng} {unit}".strip() if rng else ""
    return f"{cur} {v}".strip()


def expired(p: dict) -> bool:
    """True if the posting's validThrough date has passed."""
    vt = p.get("valid_through")
    if not vt:
        return False
    try:
        d = datetime.fromisoformat(str(vt).replace("Z", "+00:00"))
    except ValueError:
        try:
            d = datetime.strptime(str(vt)[:10], "%Y-%m-%d")
        except ValueError:
            return False
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d < datetime.now(timezone.utc)


def postings(raw_html: str, page_url: str = "") -> list[dict]:
    found: list[dict] = []

    def walk(o):
        if isinstance(o, list):
            for x in o:
                walk(x)
        elif isinstance(o, dict):
            if _is_posting(o):
                found.append(o)
            for k in ("@graph", "itemListElement", "item", "mainEntity"):
                if k in o:
                    walk(o[k])

    for block in _BLOCK.findall(raw_html or ""):
        try:
            walk(json.loads(block.strip()))
        except (json.JSONDecodeError, ValueError):
            # some sites put raw newlines inside strings
            try:
                walk(json.loads(re.sub(r"[\r\n\t]+", " ", block.strip())))
            except (json.JSONDecodeError, ValueError):
                pass

    out = []
    for o in found:
        lt = str(o.get("jobLocationType") or "").upper()
        remote = "TELECOMMUTE" in lt
        url = _text(o.get("url")) or page_url
        ident = o.get("identifier")
        ident = _text(ident.get("value") if isinstance(ident, dict) else ident) or url
        out.append({
            "id": ident,
            "title": _text(o.get("title")),
            "locations": _locations(o),
            "remote": remote,
            "workplace": "remote" if remote else "",
            "url": url,
            "published": _text(o.get("datePosted")) or None,
            "department": _text(o.get("occupationalCategory") or o.get("industry")),
            "salary": _salary(o),
            "description": strip_html(_text(o.get("description"))),
            "valid_through": _text(o.get("validThrough")) or None,
            "company": _text(o.get("hiringOrganization")),
        })
    return [p for p in out if p["title"]]
