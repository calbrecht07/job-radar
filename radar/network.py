"""The person's LinkedIn network, for "you know someone there" on every role.

    python -m radar.network --data PATH --import ~/Downloads/Complete_LinkedInDataExport_....zip

Import: LinkedIn's data export (Settings > Data privacy > Get a copy of your data > Connections) -> the data repo's
network/connections.csv (name, profile URL, company, position, connected on; emails are dropped). Personal data:
it lives only in the person's private data repo, never in the framework or the shared directory.

Matching is by company name after removing legal and generic suffixes ("Octopus Energy Group Ltd" = "Octopus
Energy"), exact only: a missed match is better than telling someone they know people at the wrong company.
Second-degree connections aren't in the export and LinkedIn offers no public way to look them up, so each company
gets a link to LinkedIn's own people search, filtered to the person's second-degree network.
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
    def second_degree_url(company: str) -> str:
        """LinkedIn people search for the company, second-degree network only (opens in the person's own login)."""
        return ("https://www.linkedin.com/search/results/people/?keywords=" + quote(company)
                + "&network=%5B%22S%22%5D&origin=FACETED_SEARCH")


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
