"""Keyword filters driven by settings.yaml. Inclusive by design: the AI reviewer (or you) makes the real call.

Pools:
  local  - an on-site/hybrid posting in your city (settings.locations.local)
  remote - a remote posting open to a region you can work from (settings.locations.remote)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


def _rx(patterns):
    return [re.compile(p, re.I) for p in patterns or []]


def _first(rxs, text):
    for r in rxs:
        m = r.search(text)
        if m:
            return m
    return None


def _any(rxs, text):
    return _first(rxs, text) is not None


@dataclass
class Verdict:
    keep: bool
    pool: str = ""                     # "local" or "remote"
    reason: str = ""                   # why dropped
    flags: list = field(default_factory=list)


class Filters:
    _GENERIC = re.compile(r"remote|anywhere|fully|work from home|\bwfh\b|hybrid|flexible|home[- ]based|[-–,;/()|]|\s", re.I)

    def __init__(self, s: dict):
        roles = s.get("roles") or {}
        self.inc = _rx(roles.get("include"))
        self.inc_vc = _rx(roles.get("include_vc"))
        self.exc = _rx(roles.get("exclude"))
        self.kinds = set(s.get("company_kinds") or ["startup", "vc"])
        self.modes = set(s.get("work_modes") or ["onsite", "hybrid", "remote"])
        loc = s.get("locations") or {}
        local = loc.get("local") or {}
        self.local_label = local.get("label") or "Local"
        self.local = _rx(local.get("match"))
        self.region_wide = _rx(local.get("region_wide"))
        r = loc.get("remote") or {}
        self.allowed = _rx(r.get("allowed_regions"))
        self.country = _rx(r.get("country_limited"))
        self.blocked = _rx(r.get("blocked_regions"))
        self.keep_unspecified = r.get("keep_unspecified", True)
        self.drops = [(re.compile(d["pattern"], re.I), d["reason"], set(d.get("pools") or ["local", "remote"]))
                      for d in s.get("drops") or []]
        self.flags = [(re.compile(d["pattern"], re.I), d["flag"]) for d in s.get("flags") or []]

    # -- pieces -------------------------------------------------------------
    def title_ok(self, title: str, kind: str) -> bool:
        if _any(self.exc, title):
            return False
        if _any(self.inc, title):
            return True
        return kind == "vc" and _any(self.inc_vc, title)

    @staticmethod
    def locs(p: dict) -> str:
        return " | ".join(p.get("locations") or [])

    @classmethod
    def is_remote(cls, p: dict) -> bool:
        if p.get("remote") or (p.get("workplace") or "").lower() == "remote":
            return True
        return bool(re.search(r"\bremote\b|anywhere|distributed", cls.locs(p), re.I))

    def remote_region(self, p: dict) -> tuple[bool, str | None]:
        """allowed region -> ok; one listed country -> ok + residency flag;
        nothing but "remote" -> ok + unspecified flag; any other place -> drop."""
        text = self.locs(p)
        if _any(self.allowed, text):
            return True, None
        if _any(self.blocked, text):
            return False, None
        m = _first(self.country, text)
        if m:
            return True, f"residency: remote limited to {m.group(0).title()}"
        if not self._GENERIC.sub("", text):
            return self.keep_unspecified, "remote region unspecified: check the JD"
        return False, None

    def local_match(self, p: dict) -> tuple[bool, str | None]:
        text = self.locs(p)
        if _any(self.local, text):
            return True, None
        if _any(self.region_wide, text):
            return True, f"not clearly {self.local_label}: check the city"
        return False, None

    # -- main ---------------------------------------------------------------
    def evaluate(self, p: dict, company: dict) -> Verdict:
        kind = company.get("kind") or "startup"
        if kind not in self.kinds:
            return Verdict(False, reason="company kind")
        if not self.title_ok(p.get("title", ""), kind):
            return Verdict(False, reason="title")

        pool, flags = "", []
        remote = self.is_remote(p)
        if remote and "remote" in self.modes:
            ok, flag = self.remote_region(p)
            if ok:
                pool = "remote"
                flags += [flag] if flag else []
        if not pool:
            wp = (p.get("workplace") or "").lower()
            if wp in ("onsite", "on-site", "hybrid") and wp.replace("-", "") not in self.modes:
                return Verdict(False, reason="work mode")
            ok, flag = self.local_match(p)
            if ok and (self.modes & {"onsite", "hybrid"}):
                pool = "local"
                flags += [flag] if flag else []
        if not pool:
            return Verdict(False, reason="location")

        desc = p.get("description") or ""
        for rx, reason, pools in self.drops:
            if pool in pools and rx.search(desc):
                return Verdict(False, pool=pool, reason=reason)
        for rx, flag in self.flags:
            if rx.search(desc):
                flags.append(flag)
        if not desc:
            flags.append("no description in feed: open the link")
        return Verdict(True, pool=pool, flags=flags)
