"""Wikidata: companies by city and by industry, for any city and any free-text industry.

    find_place("London")                -> {"qid", "label", "population", "country"}
    companies_in_city(place_qid)        -> companies headquartered in the city or any district inside it
    find_industries("space")            -> industry items whose companies Wikidata knows (with subclasses)
    companies_in_industries(qids, ...)  -> companies in those industries, any headquarters

Coverage is notable companies (big, listed, well known); the long tail comes from other sources.
Queries are split small: the public endpoint times out after ~60 s and rate-limits bursts.
"""
from __future__ import annotations

import re
import time

import requests

API = "https://www.wikidata.org/w/api.php"
SPARQL = "https://query.wikidata.org/sparql"
UA = {"User-Agent": "job-radar/1.0 (open-source job search tool; https://github.com/calbrecht07/job-radar)"}
_S = requests.Session()
_S.headers.update(UA)

# organisation types that are never employers worth watching for a job search
NOT_EMPLOYERS = re.compile(
    r"charit|nonprofit|non-profit|non-governmental|embassy|high commission|consulate|stock (exchange|market)|"
    r"trading facility|political party|learned society|professional (association|body)|trade association|"
    r"regulatory|school|museum|football club|sports club|think tank|advocacy|record label|magazine|newspaper|"
    r"television (station|channel)|radio station|imprint|website$|brand$|fictional|defunct|former", re.I)
COMPANY = re.compile(r"business|enterprise|company|firm|\bbank\b|startup|corporation|conglomerate|agency|"
                     r"consultan|insurer|investment|fund|manufacturer|developer|operator|retailer|publisher|"
                     r"studio|laborator|airline|utility|cooperative|partnership", re.I)


def _sparql(query: str, retries: int = 3) -> list[dict]:
    last = None
    for i in range(retries):
        try:
            r = _S.get(SPARQL, params={"query": query, "format": "json"}, timeout=90)
            if r.status_code == 429:                   # throttled: wait as asked, but never stall a run
                try:
                    wait = int(r.headers.get("Retry-After", 30))
                except ValueError:
                    wait = 30
                last = "HTTP 429"
                time.sleep(min(wait, 60))
                continue
            if r.ok:
                return r.json()["results"]["bindings"]
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = type(e).__name__
        time.sleep(5 * (i + 1))
    raise RuntimeError(f"wikidata query failed: {last}")


def _qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def _v(b: dict, k: str) -> str:
    return (b.get(k) or {}).get("value", "")


def _search(text: str, limit: int = 10) -> list[dict]:
    r = _S.get(API, params={"action": "wbsearchentities", "search": text, "language": "en", "type": "item",
                            "limit": limit, "format": "json"}, timeout=30)
    r.raise_for_status()
    return r.json().get("search", [])


# ------------------------------------------------------------------- places
def find_place(name: str, country: str = "") -> dict | None:
    """The most populous human settlement called `name` (optionally in `country`)."""
    cands = [c["id"] for c in _search(name, 10)]
    if not cands:
        return None
    vals = " ".join("wd:" + q for q in cands)
    rows = _sparql(f"""SELECT ?p ?pLabel ?pop ?cLabel WHERE {{
        VALUES ?p {{ {vals} }}
        ?p wdt:P31/wdt:P279* wd:Q486972 .
        OPTIONAL {{ ?p wdt:P1082 ?pop }}
        OPTIONAL {{ ?p wdt:P17 ?c }}
        SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }} }}""")
    best = {}
    for b in rows:
        if country and country.lower() not in _v(b, "cLabel").lower():
            continue
        q = _qid(_v(b, "p"))
        pop = float(_v(b, "pop") or 0)
        if q not in best or pop > best[q]["population"]:
            best[q] = {"qid": q, "label": _v(b, "pLabel"), "population": pop, "country": _v(b, "cLabel")}
    return max(best.values(), key=lambda x: x["population"]) if best else None


def places_within(place_qid: str) -> list[str]:
    """The place and every administrative area / district located inside it (London: ~3,000)."""
    rows = _sparql(f"SELECT DISTINCT ?p WHERE {{ ?p wdt:P131* wd:{place_qid} }}")
    return list(dict.fromkeys([place_qid] + [_qid(_v(b, "p")) for b in rows]))


# ---------------------------------------------------------------- companies
def _collect(rows: list[dict], into: dict, source: str):
    for b in rows:
        q = _qid(_v(b, "c"))
        e = into.setdefault(q, {"wikidata": q, "name": _v(b, "cLabel"), "website": _v(b, "site"), "employees": 0,
                                "types": set(), "industries": set(), "hq": _v(b, "hqLabel"),
                                "description": _v(b, "desc"), "sources": set()})
        e["sources"].add(source)
        try:
            e["employees"] = max(e["employees"], int(float(_v(b, "emp") or 0)))
        except ValueError:
            pass
        if _v(b, "typeLabel"):
            e["types"].add(_v(b, "typeLabel"))
        if _v(b, "indLabel"):
            e["industries"].add(_v(b, "indLabel"))


def _finish(found: dict) -> list[dict]:
    out = []
    for e in found.values():
        types = "; ".join(sorted(e["types"]))
        if re.fullmatch(r"Q\d+", e["name"]) or not e["website"]:
            continue
        if NOT_EMPLOYERS.search(types) or not (COMPANY.search(types) or e["industries"] or e["employees"]):
            continue
        out.append({**e, "types": sorted(e["types"]), "industries": sorted(e["industries"]), "sources": sorted(e["sources"])})
    return sorted(out, key=lambda e: (-e["employees"], e["name"].lower()))


_FIELDS = """?c wdt:P856 ?site ; wdt:P31 ?type .
      FILTER NOT EXISTS { ?c wdt:P576 [] }
      OPTIONAL { ?c wdt:P1128 ?emp }
      OPTIONAL { ?c wdt:P452 ?ind }
      OPTIONAL { ?c schema:description ?desc FILTER(lang(?desc) = "en") }
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }"""


def companies_in_city(place_qid: str, batch: int = 150, pause: float = 1.0) -> list[dict]:
    places = places_within(place_qid)
    found: dict = {}
    for i in range(0, len(places), batch):
        vals = " ".join("wd:" + p for p in places[i:i + batch])
        rows = _sparql(f"""SELECT ?c ?cLabel ?site ?emp ?typeLabel ?indLabel ?desc ?hqLabel WHERE {{
          VALUES ?hq {{ {vals} }}
          ?c wdt:P159 ?hq .
          {_FIELDS} }}""")
        _collect(rows, found, "wikidata:city")
        time.sleep(pause)
    return _finish(found)


# --------------------------------------------------------------- industries
def find_industries(term: str, min_companies: int = 3) -> list[dict]:
    """Industry items matching free text ("space", "fintech", "biotech"), keeping only those Wikidata
    actually uses as a company's industry (P452). Returns [{qid, label, companies}]."""
    cands = {}
    singular = re.sub(r"(?<=[a-z])s\b", "", term)            # "video games" -> "video game"
    for text in dict.fromkeys((term, f"{term} industry", singular, f"{singular} industry")):
        for c in _search(text, 10):
            cands.setdefault(c["id"], c.get("label", c["id"]))
    if not cands:
        return []
    def counted(values_clause: str) -> list[dict]:
        rows = _sparql(f"""SELECT ?i ?iLabel (COUNT(DISTINCT ?c) AS ?n) WHERE {{
            {values_clause}
            ?c wdt:P452 ?i .
            SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
          }} GROUP BY ?i ?iLabel""")
        return [{"qid": _qid(_v(b, "i")), "label": _v(b, "iLabel"), "companies": int(_v(b, "n") or 0)} for b in rows]

    # 1. search hits that companies actually use as their industry ("space" the place is never one)
    direct = [o for o in counted("VALUES ?i { %s }" % " ".join("wd:" + q for q in cands)) if o["companies"] >= min_companies]
    if not direct:
        return []
    # 2. their subclasses, two levels down (space industry -> satellite manufacturing -> ...)
    sub_qids, frontier = [], [o["qid"] for o in direct]
    for _ in range(2):                    # one level per query: path operators time out on the public endpoint
        if not frontier:
            break
        level = [_qid(_v(b, "i")) for b in _sparql(
            "SELECT DISTINCT ?i WHERE { VALUES ?root { %s } ?i wdt:P279 ?root } LIMIT 300"
            % " ".join("wd:" + q for q in frontier[:100]))]
        frontier = [q for q in level if q not in sub_qids]
        sub_qids += frontier
    subs = []
    for i in range(0, len(sub_qids), 100):
        subs += counted("VALUES ?i { %s }" % " ".join("wd:" + q for q in sub_qids[i:i + 100]))
    # Wikidata's subclass tree wanders (biotechnology > cheesemaking, logistics > fast fashion): keep a
    # subclass only if its label shares a word stem with the search term or with a matched industry
    stems = {w[:5] for w in re.findall(r"[a-z]{4,}", " ".join([term] + [o["label"] for o in direct]).lower())} - {"indus"}
    related = lambda label: bool(stems & {w[:5] for w in re.findall(r"[a-z]{4,}", label.lower())})
    out = {o["qid"]: o for o in direct + [s for s in subs if s["companies"] >= min_companies and related(s["label"])]}
    return sorted(out.values(), key=lambda o: -o["companies"])


def companies_in_industries(industry_qids: list[str], country_qid: str = "", min_employees: int = 0,
                            label: str = "", limit: int = 4000) -> list[dict]:
    """Companies in these industries, any headquarters. Narrowed to a country and/or a minimum size, so a
    broad industry ("software") doesn't return the whole world; the careers discovery later keeps only
    companies that post roles in the person's city."""
    if not industry_qids:
        return []
    vals = " ".join("wd:" + q for q in industry_qids)
    where = []
    if country_qid and min_employees:
        where.append(f"{{ ?c wdt:P17 wd:{country_qid} }} UNION {{ ?c wdt:P1128 ?e2 FILTER(?e2 >= {min_employees}) }}")
    elif country_qid:
        where.append(f"?c wdt:P17 wd:{country_qid} .")
    elif min_employees:
        where.append(f"?c wdt:P1128 ?e2 FILTER(?e2 >= {min_employees})")
    rows = _sparql(f"""SELECT ?c ?cLabel ?site ?emp ?typeLabel ?indLabel ?desc ?hqLabel WHERE {{
      VALUES ?ind {{ {vals} }}
      ?c wdt:P452 ?ind .
      {" ".join(where)}
      OPTIONAL {{ ?c wdt:P159 ?hq }}
      {_FIELDS} }} LIMIT {limit}""")
    found: dict = {}
    _collect(rows, found, f"wikidata:industry:{label}" if label else "wikidata:industry")
    return _finish(found)


def country_of(place_qid: str) -> str:
    rows = _sparql(f"SELECT ?c WHERE {{ wd:{place_qid} wdt:P17 ?c }} LIMIT 1")
    return _qid(_v(rows[0], "c")) if rows else ""
