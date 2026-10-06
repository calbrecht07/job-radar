"""A headless browser for careers pages that plain requests can't read (job lists built by JavaScript, simple
bot checks). Used only by the weekly company search, never the 6x/day scan.

    with Browser() as b:
        page = b.render("https://example.com/careers")   # {status, final_url, html, requests, json}
    jobs_from_json(page["json"], base_url)                # postings found in the page's own data calls

Requires `pip install playwright && python -m playwright install chromium` (requirements-browser.txt).
`available()` says whether it can run here; callers fall back to plain requests when it can't.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin

_SKIP = re.compile(r"google-analytics|googletagmanager|doubleclick|facebook|hotjar|segment\.|sentry|intercom|hubspot|"
                   r"cookielaw|onetrust|cookiebot|datadog|newrelic|optimizely|clarity\.ms|linkedin|twitter|tiktok|youtube", re.I)
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0 Safari/537.36")


def available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


class Browser:
    """One Chromium for many pages. Not thread-safe: use it from one thread."""

    def __init__(self, timeout_s: int = 25, settle_s: float = 6):
        self.timeout_ms, self.settle_ms = timeout_s * 1000, int(settle_s * 1000)
        self._pw = self._browser = self._ctx = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._ctx = self._browser.new_context(user_agent=UA, locale="en-GB", viewport={"width": 1366, "height": 900})
        self._ctx.route(re.compile(r".*\.(png|jpe?g|gif|webp|svg|woff2?|ttf|mp4|webm|ico)(\?.*)?$"), lambda r: r.abort())
        return self

    def __exit__(self, *exc):
        for x in (self._ctx, self._browser):
            try:
                x and x.close()
            except Exception:
                pass
        if self._pw:
            self._pw.stop()

    def render(self, url: str) -> dict:
        out = {"status": None, "final_url": url, "html": "", "requests": [], "json": [], "error": ""}
        page = self._ctx.new_page()
        responses = []
        page.on("request", lambda r: out["requests"].append(r.url))
        page.on("response", lambda r: responses.append(r))
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            out["status"] = resp.status if resp else None
            try:
                page.wait_for_load_state("networkidle", timeout=self.settle_ms)
            except Exception:
                pass                                   # pages that never go quiet: take what loaded
            try:
                page.mouse.wheel(0, 4000)              # lazy job lists load on scroll
                page.wait_for_timeout(800)
            except Exception:
                pass
            out["final_url"], out["html"] = page.url, page.content()
            for r in responses[:400]:
                ctype = (r.headers or {}).get("content-type", "")
                if "json" not in ctype or _SKIP.search(r.url) or r.status >= 400:
                    continue
                try:
                    body = r.body()
                    if len(body) < 3_000_000:
                        out["json"].append((r.url, json.loads(body)))
                except Exception:
                    pass
                if len(out["json"]) >= 40:
                    break
        except Exception as e:
            out["error"] = type(e).__name__
        finally:
            try:
                page.close()
            except Exception:
                pass
        out["requests"] = [u for u in out["requests"] if not _SKIP.search(u)][:600]
        return out


# ---------------------------------------------------------------- job data in JSON
_TITLE_KEYS = ("title", "jobTitle", "job_title", "name", "position", "positionName", "text", "displayName")
_URL_KEYS = ("absolute_url", "absoluteUrl", "url", "jobUrl", "job_url", "applyUrl", "apply_url", "hostedUrl", "link",
             "externalUrl", "canonicalUrl", "careersUrl", "permalink", "detailsUrl")
_LOC_KEYS = ("location", "locations", "locationName", "location_name", "city", "office", "offices", "locationsText",
             "workplace", "region", "country", "jobLocation", "addressLocality", "categories")
_LOCKEY = re.compile(r"location|city|office|country|region", re.I)        # groupedLocation, primaryCity, ...
_ID_KEYS = ("id", "jobId", "job_id", "uuid", "reqId", "requisitionId", "slug", "shortcode")
_JOBBY = re.compile(r"engineer|manager|lead|director|analyst|associate|specialist|consultant|designer|scientist|"
                    r"operations|strateg|product|sales|account|developer|architect|head of|officer|intern|recruit|"
                    r"marketing|finance|legal|counsel|support|success|partner|coordinator|executive|generalist", re.I)


def _text(v) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        for k in ("name", "label", "text", "title", "city", "value", "displayName"):
            if isinstance(v.get(k), str):
                parts = [v[k]] + [v.get(x) for x in ("country", "countryName") if isinstance(v.get(x), str)]
                return ", ".join(p for p in parts if p)
    if isinstance(v, list):
        return " | ".join(t for t in (_text(x) for x in v) if t)
    return ""


def _posting(d: dict, base: str) -> dict | None:
    title = next((d[k] for k in _TITLE_KEYS if isinstance(d.get(k), str) and 3 <= len(d[k]) <= 140), "")
    if not title:
        return None
    url = next((d[k] for k in _URL_KEYS if isinstance(d.get(k), str) and len(d[k]) > 1), "")
    ident = next((str(d[k]) for k in _ID_KEYS if d.get(k) not in (None, "")), "")
    locs = []
    keys = [k for k in _LOC_KEYS if k in d] + [k for k in d if k not in _LOC_KEYS and _LOCKEY.search(k)]
    for k in keys:
        t = _text(d[k])
        if t and len(t) < 300 and t not in locs:
            locs.append(t)
    if url and not url.startswith("http"):
        url = urljoin(base, url)
    remote = bool(re.search(r"\bremote\b", " ".join(locs), re.I)) or d.get("remote") is True or d.get("isRemote") is True
    return {"id": ident or url or title, "title": title.strip(), "url": url, "locations": locs[:3], "remote": remote}


def jobs_from_json(payloads: list, base: str) -> list[dict]:
    """Postings in a page's own data calls: the largest list of objects that look like jobs (a title-like field
    that reads like a role, plus a link, id or location)."""
    best: list[dict] = []

    def walk(o, depth=0):
        nonlocal best
        if depth > 8:
            return
        if isinstance(o, list) and len(o) >= 1 and all(isinstance(x, dict) for x in o[:20]):
            posts = [p for p in (_posting(x, base) for x in o) if p and (p["url"] or p["id"] or p["locations"])]
            # a job list: mostly role-like titles, and either several items or ones that carry a location
            if posts and sum(1 for p in posts if _JOBBY.search(p["title"])) >= max(1, len(posts) // 2) \
                    and (len(posts) >= 8 or sum(1 for p in posts if p["locations"]) >= len(posts) // 2 + 1) \
                    and len(posts) > len(best):
                best = posts
        if isinstance(o, dict):
            for v in o.values():
                walk(v, depth + 1)
        elif isinstance(o, list):
            for v in o[:200]:
                walk(v, depth + 1)

    for _, data in payloads:
        walk(data)
    return best[:500]
