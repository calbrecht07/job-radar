"""High-level labels for the report: a role family from the title, industries from what the pipeline knows
about the company. Both are for filtering the page; nothing is dropped by them.

    family(title, kind)            -> "Product" | "Engineering & solutions" | ...
    Industries(data, settings)     -> .of(company_name) -> ["watertech", "AI & software", ...]
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

# first match wins: "Sales Engineer" is solutions work, "Product Operations" is product
FAMILIES = [
    ("Investing", r"\binvest|\bventure|\bscout\b|portfolio|value creation|\beir\b|entrepreneur in residence"),
    ("Product", r"\bproduct\b|\bpm\b"),
    ("Engineering & solutions", r"solutions?|engineer|architect|deploy|pre-?sales|technical (specialist|consultant)|scientist|developer"),
    ("Customer & implementation", r"implementation|onboarding|technical account|account manag|customer|client|engagement manager|success"),
    ("Sales & partnerships", r"sales|partnership|business development|\bbd\b|commercial|go-to-market|\bgtm\b|revenue|alliances"),
    ("Strategy & operations", r"chief of staff|biz ?ops|business operations|strateg|operations|\bops\b|founder|special projects|"
                              r"generalist|expansion|general manager|\bgm\b|transformation|launch|programme|program|project|growth"),
]
VC_ONLY = re.compile(r"\banalyst\b|\bassociate\b|platform", re.I)

# fallback groups when none of the person's own industries matches
GENERIC = [
    ("AI & software", r"artificial intelligence|\bai\b|machine learning|software|saas|developer|data|cloud|cyber|security|computer"),
    ("Fintech", r"fintech|financ|payment|bank|insur|crypto|lending|wealth|accounting|trading"),
    ("Health & bio", r"health|medic|bio|pharma|clinic|therapeut|care\b|life science"),
    ("Climate & energy", r"climate|energy|renewable|solar|wind|battery|carbon|sustainab|electric"),
    ("Consumer", r"consumer|retail|e-?commerce|food|fashion|beauty|travel|media|gaming|entertainment|marketplace"),
    ("Industrial & mobility", r"industr|manufactur|logistic|supply chain|automotive|transport|mobility|aerospace|construct|robot"),
]


def family(title: str, kind: str = "") -> str:
    t = title or ""
    for name, pat in FAMILIES:
        if re.search(pat, t, re.I):
            return name
    if kind == "vc" and VC_ONLY.search(t):
        return "Investing"
    return "Other"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\b(plc|ltd|limited|llp|inc|group|holdings|the)\b", "", (s or "").lower()))


def _rx(words: list[str]) -> re.Pattern | None:
    words = [w.strip() for w in words if w and w.strip()]
    return re.compile("|".join(re.escape(w) for w in words), re.I) if words else None


class Industries:
    """Company name -> the person's industries (from settings) it belongs to, else a generic group."""

    def __init__(self, data: Path, settings: dict):
        cs = settings.get("company_search") or {}
        self.mine = []
        for item in cs.get("industries") or []:
            if isinstance(item, dict) and item.get("name"):
                self.mine.append((item["name"], _rx([item["name"]] + list(item.get("search") or []))))
            elif isinstance(item, str) and item.strip():
                self.mine.append((item.strip(), _rx([item.strip()])))
        self.generic = [(n, re.compile(p, re.I)) for n, p in GENERIC]
        self.labels: dict[str, set] = {}
        self._load(data)

    def _add(self, name: str, labels):
        if not name:
            return
        if isinstance(labels, str):
            labels = [l for l in re.split(r";\s*", labels) if l]
        self.labels.setdefault(_norm(name), set()).update(l for l in labels or [] if l)

    def _load(self, data: Path):
        p = data / "pool/directory.csv"
        if p.exists():
            with p.open(newline="") as f:
                for r in csv.DictReader(f):
                    self._add(r.get("name", ""), r.get("industries", ""))
        try:
            pool = json.loads((data / "pool/companies.json").read_text()).get("companies", [])
        except (FileNotFoundError, json.JSONDecodeError):
            pool = []
        for c in pool:
            self._add(c.get("name", ""), c.get("industries") or [])

    def of(self, company: str) -> list[str]:
        labels = " | ".join(sorted(self.labels.get(_norm(company), set())))
        if not labels:
            return ["Unknown"]
        mine = [n for n, rx in self.mine if rx and rx.search(labels)]
        if mine:
            return mine
        generic = [n for n, rx in self.generic if rx.search(labels)]
        return generic[:2] or ["Other"]
