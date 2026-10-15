"""The person's LinkedIn network, for "you know someone there" on every role.

    python -m radar.network --data PATH --import ~/Downloads/Complete_LinkedInDataExport_....zip

Import: LinkedIn's data export (Settings > Data privacy > Get a copy of your data > Connections) -> the data repo's
network/connections.csv (name, profile URL, company, position, connected on; emails are dropped). Personal data:
it lives only in the person's private data repo, never in the framework or the shared directory.

Matching is by company name after removing legal and generic suffixes ("Octopus Energy Group Ltd" = "Octopus
Energy"), exact only: a missed match is better than telling someone they know people at the wrong company.
Second-degree connections aren't in the export and LinkedIn offers no public way to look them up, so each company
links to its LinkedIn People page (linkedin.com/company/<page>/people/), which lists who works there and the
person's mutual connections. The company's LinkedIn page is read from its own website (state/linkedin_pages.json);
a company whose page can't be found gets no link rather than a keyword search that matches anyone.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

FIELDS = ["name", "url", "company", "position", "connected_on"]
_SUFFIX = re.compile(r"\b(ltd|limited|plc|llp|llc|inc|incorporated|corp|corporation|co|company|gmbh|ag|sa|sas|bv|nv|"
                     r"group|holdings?|uk|london|europe|emea|global|international|technologies|technology|tech|hq|"
                     r"the|\.com|\.io|\.ai)\b", re.I)


def norm(company: str) -> str:
    s = re.sub(r"\(.*?\)", " ", (company or "").lower())
    s = s.replace("&", " and ")
    s = _SUFFIX.sub(" ", s)
    return re.sub(r"[^a-z0-9]", "", s)


def _rows_from_export(text: str) -> list[dict]:
    """LinkedIn puts a few lines of notes above the header."""
    lines = text.lstrip("﻿").splitlines()
    start = next((i for i, l in enumerate(lines) if l.startswith("First Name")), 0)
    out = []
    for r in csv.DictReader(io.StringIO("\n".join(lines[start:]))):
        name = f"{(r.get('First Name') or '').strip()} {(r.get('Last Name') or '').strip()}".strip()
        if not name:
            continue
        out.append({"name": name, "url": (r.get("URL") or "").strip(), "company": (r.get("Company") or "").strip(),
                    "position": (r.get("Position") or "").strip(), "connected_on": (r.get("Connected On") or "").strip()})
    return out


def import_export(src: Path, data: Path) -> int:
    if src.suffix == ".zip" or zipfile.is_zipfile(src):
        with zipfile.ZipFile(src) as z:
            name = next(n for n in z.namelist() if n.lower().endswith("connections.csv"))
            text = z.read(name).decode("utf-8-sig", errors="replace")
    else:
        text = src.read_text(encoding="utf-8-sig", errors="replace")
    rows = _rows_from_export(text)
    out = data / "network"
    out.mkdir(exist_ok=True)
    with (out / "connections.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    m = re.search(r"(\d{2})-(\d{2})-(\d{4})", src.name)
    exported = f"{m.group(3)}-{m.group(1)}-{m.group(2)}" if m else datetime.now(timezone.utc).date().isoformat()
    (out / "imported.json").write_text(json.dumps({"exported": exported, "imported": datetime.now(timezone.utc).date().isoformat(),
                                                  "connections": len(rows)}, indent=1) + "\n")
    return len(rows)


class Network:
    def __init__(self, data: Path):
        self.by_company: dict[str, list[dict]] = {}
        self.meta = {}
        p = data / "network/connections.csv"
        if not p.exists():
            return
        with p.open(newline="") as f:
            for r in csv.DictReader(f):
                k = norm(r.get("company", ""))
                if len(k) >= 2:
                    self.by_company.setdefault(k, []).append(r)
        try:
            self.meta = json.loads((data / "network/imported.json").read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def __bool__(self):
        return bool(self.by_company)

    def at(self, company: str) -> list[dict]:
        return self.by_company.get(norm(company), [])

    @staticmethod
    def people_url(linkedin_page: str) -> str:
        """The company's LinkedIn People page: its employees, with the person's mutual connections."""
        return f"https://www.linkedin.com/company/{quote(linkedin_page)}/people/" if linkedin_page else ""


# ------------------------------------------------------- company LinkedIn pages
_LI = re.compile(r"linkedin\.com/company/([A-Za-z0-9%._-]+)")
_LI_SKIP = {"linkedin", "company", "share", "showcase"}


def linkedin_page(html_text: str) -> str:
    """The company page a website links to (its own; the most frequent one if several)."""
    found = [m.rstrip("/.").lower() for m in _LI.findall(html_text or "") if m.lower() not in _LI_SKIP]
    if not found:
        return ""
    from collections import Counter
    return Counter(found).most_common(1)[0][0]


def _on_job_platform(url: str) -> bool:
    from sources import boards
    return bool(boards.detect_boards(url)) or bool(re.search(
        r"ashbyhq\.com|greenhouse\.io|lever\.co|workable\.com|myworkdayjobs|smartrecruiters|teamtailor|personio|"
        r"recruitee|bamboohr|breezy\.hr|getro|consider\.com|fountain\.com|pinpointhq|rippling|comeet", url or "", re.I))


def resolve_pages(data: Path, companies: list[str], budget: int = 80, role_urls: dict | None = None) -> dict:
    """{company: linkedin page} for these companies, from state/linkedin_pages.json, looking up (at most `budget`)
    missing ones on their website and careers page. Network trouble leaves a company for the next run; a
    website that loads but links no LinkedIn page is remembered as "" for 30 days."""
    import requests
    from datetime import timedelta
    path = data / "state/linkedin_pages.json"
    try:
        cache = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        cache = {}
    today = datetime.now(timezone.utc).date()
    sites = {}
    for f, field in (("pool/directory.csv", "website"), ("pool/directory.csv", "careers_url"), ("wishlist.csv", "careers_url")):
        p = data / f
        if p.exists():
            with p.open(newline="") as fh:
                for r in csv.DictReader(fh):
                    if r.get(field) and r.get("name") and not _on_job_platform(r[field]):
                        sites.setdefault(norm(r["name"]), []).append(r[field])
    for c, u in (role_urls or {}).items():                  # a role on the company's own domain names its site
        if u and u.startswith("http") and not _on_job_platform(u):
            from urllib.parse import urlparse
            sites.setdefault(norm(c), []).append(f"https://{urlparse(u).netloc}")
    # companies known only through a job board: the board names their website (Ashby's page data, Lever's header)
    try:
        cstate = json.loads((data / "state/companies.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        cstate = {}
    boards_by_name = {}
    for key, v in cstate.items():
        boards_by_name.setdefault(norm(v.get("name", "")), []).append(key)
    from radar.careers import HEADERS
    looked = 0

    def board_website(company: str) -> str:
        for key in boards_by_name.get(norm(company), []):
            ats_name, slug = key.split(":", 1)
            try:
                if ats_name == "ashby":
                    t = requests.get(f"https://jobs.ashbyhq.com/{slug}", headers=HEADERS, timeout=15).text
                    m = re.search(r'"publicWebsite"\s*:\s*"([^"]+)"', t)
                    if m:
                        return m.group(1)
                elif ats_name in ("lever", "lever-eu"):
                    host = "jobs.eu.lever.co" if ats_name == "lever-eu" else "jobs.lever.co"
                    t = requests.get(f"https://{host}/{slug}", headers=HEADERS, timeout=15).text
                    m = re.search(r'class="main-header-logo"[^>]*href="(https?://[^"]+)"', t) or \
                        re.search(r'href="(https?://[^"]+)"[^>]*class="main-header-logo"', t)
                    if m:
                        return m.group(1)
            except requests.RequestException:
                continue
        return ""
    for c in dict.fromkeys(companies):
        k = norm(c)
        hit = cache.get(k)
        if hit and (hit.get("page") or hit.get("checked", "") >= (today - timedelta(days=30)).isoformat()):
            continue
        if looked >= budget:
            continue
        urls = list(dict.fromkeys(sites.get(k, [])))
        looked += 1
        if not urls:
            site = board_website(c)
            urls = [site] if site else []
        if not urls:
            cache[k] = {"company": c, "page": "", "checked": today.isoformat(), "why": "no website known"}
            continue
        page, reached = "", False
        for u in urls[:2]:
            try:
                r = requests.get(u if u.startswith("http") else "https://" + u, headers=HEADERS, timeout=15)
                reached = reached or r.status_code < 400
                page = linkedin_page(r.text)
            except requests.RequestException:
                continue
            if page:
                break
        if reached:
            cache[k] = {"company": c, "page": page, "checked": today.isoformat()}
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(cache, indent=1, ensure_ascii=False, sort_keys=True) + "\n")
    return {c: (cache.get(norm(c)) or {}).get("page", "") for c in companies}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=".")
    ap.add_argument("--import", dest="src", required=True, help="LinkedIn export zip or Connections.csv")
    a = ap.parse_args(argv)
    n = import_export(Path(a.src).expanduser(), Path(a.data).resolve())
    print(f"network: {n} connections imported")
    return 0


if __name__ == "__main__":
    sys.exit(main())
