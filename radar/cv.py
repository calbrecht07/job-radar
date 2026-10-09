"""Tailored CVs from the experience bank.

    python -m radar.cv --data PATH --check                      # validate profile/experience.yaml
    python -m radar.cv --data PATH --role <role id or URL>      # CV for a role in the report
    python -m radar.cv --data PATH --job job.txt --name acme-pm # CV for any job description
    ... --plan cvs/<slug>.plan.json                             # an agent's selection and phrasing

Selection: every achievement is scored against the job description (its skills and tags weigh most, then its
words); the best ones per role are kept, newest roles get more room, and the closest summary is used. A plan
can override the choice and shorten bullets, never add facts. Output: cvs/<slug>.html, and .pdf when the
headless browser is installed (requirements-browser.txt). Format and a fictional example of the bank:
config.example/experience.yaml; how an agent builds it with the person: agent/EXPERIENCE.md.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

STAR = ("situation", "task", "action", "result")
_STOP = set("""a an and are as at be by for from has have in is it its of on or our that the their them they this to
was we were will with you your role team work working across within who what when where how more than into over
experience years year strong ability skills including such about using use etc""".split())


# ---------------------------------------------------------------- bank
def load_bank(data: Path) -> dict:
    p = data / "profile/experience.yaml"
    if not p.exists():
        raise SystemExit(f"no experience bank at {p}: see agent/EXPERIENCE.md and config.example/experience.yaml")
    return yaml.safe_load(p.read_text()) or {}


def check(bank: dict) -> list[str]:
    """Problems that would make a weak or broken CV. Empty list = fine."""
    issues = []
    if not (bank.get("person") or {}).get("name"):
        issues.append("person.name is missing")
    roles = bank.get("roles") or []
    if not roles:
        issues.append("no roles")
    ids = Counter()
    for r in roles:
        where = f"{r.get('title', '?')} at {r.get('company', '?')}"
        for f in ("company", "title", "start"):
            if not r.get(f):
                issues.append(f"{where}: {f} is missing")
        for k in ("start", "end"):
            v = str(r.get(k) or "")
            if v and v != "present" and not re.fullmatch(r"\d{4}(-\d{2})?", v):
                issues.append(f"{where}: {k} should be YYYY-MM or 'present', not {v!r}")
        for a in r.get("achievements") or []:
            ids[a.get("id")] += 1
            missing = [s for s in STAR if not (a.get(s) or "").strip()]
            if not a.get("id"):
                issues.append(f"{where}: an achievement has no id")
            if missing:
                issues.append(f"{where} / {a.get('id')}: missing {', '.join(missing)}")
            elif not re.search(r"\d", a.get("result", "")):
                issues.append(f"{where} / {a.get('id')}: result has no number (fine if there truly is none)")
    issues += [f"achievement id {i!r} is used {n} times" for i, n in ids.items() if i and n > 1]
    return issues


# ------------------------------------------------------------- matching
def _words(text: str) -> list[str]:
    return [w.strip(".") for w in re.findall(r"[a-z][a-z0-9+#.]{1,}", (text or "").lower()) if w.strip(".") not in _STOP]


def _terms(text: str) -> Counter:
    w = _words(text)
    return Counter(w + [f"{a} {b}" for a, b in zip(w, w[1:])])


def score(achievement: dict, job: Counter) -> float:
    if not job:
        return 0.0
    text = " ".join(str(achievement.get(s, "")) for s in STAR)
    labels = " ".join(list(achievement.get("skills") or []) + list(achievement.get("tags") or []))
    s = sum(math.log1p(job[t]) for t in set(_terms(text)) if t in job)
    s += 3 * sum(math.log1p(job[t]) for t in set(_terms(labels)) if t in job)
    return s


def bullet(a: dict) -> str:
    action, result = (a.get("action") or "").strip().rstrip("."), (a.get("result") or "").strip()
    return f"{action}. {result}" if action and result else action or result


def select(bank: dict, job_text: str, plan: dict | None = None, per_role=(4, 3, 2), max_total: int = 10) -> dict:
    """{summary, roles: [{role fields, bullets: [text]}]} for the CV."""
    job = _terms(job_text)
    plan = plan or {}
    chosen_ids = plan.get("achievements")
    rephrase = plan.get("rephrase") or {}
    summaries = bank.get("summaries") or []
    summary = next((s["text"] for s in summaries if s.get("id") == plan.get("summary")), None)
    if summary is None and summaries:
        summary = max(summaries, key=lambda s: score({"action": s.get("text", ""), "tags": s.get("tags")}, job))["text"]
    out, total = [], 0
    for i, r in enumerate(bank.get("roles") or []):
        achs = r.get("achievements") or []
        if chosen_ids is not None:
            keep = [a for a in achs if a.get("id") in chosen_ids]
            keep.sort(key=lambda a: chosen_ids.index(a["id"]))
        else:
            room = per_role[min(i, len(per_role) - 1)]
            keep = sorted(achs, key=lambda a: -score(a, job))[:room]
            keep = keep[:max(0, max_total - total)] or achs[:1]   # every role shows at least one line
        total += len(keep)
        out.append({**{k: r.get(k, "") for k in ("company", "title", "location", "start", "end", "context")},
                    "bullets": [rephrase.get(a.get("id")) or bullet(a) for a in keep]})
    return {"summary": summary or "", "roles": out}


# ------------------------------------------------------------- the role
def find_role(data: Path, ref: str) -> tuple[dict, str]:
    """A role from the report by id or URL, with the best description the radar has."""
    def load(p, default):
        try:
            return json.loads((data / p).read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return default
    rep = load("report/report.json", {})
    roles = [r for k in ("watchlist_roles", "market_roles", "unverified_roles") for r in rep.get(k) or []]
    roles += load("report/archive.json", [])
    role = next((r for r in roles if ref in (r.get("id"), r.get("url"))), None)
    if not role:
        raise SystemExit(f"no role {ref!r} in the report or archive")
    desc = ""
    for p in ("scan/review_queue.json", "scan/pending.json"):
        hit = next((c for c in load(p, []) if c.get("id") == role.get("id")), None)
        if hit and hit.get("description"):
            desc = hit["description"]
            break
    return role, " ".join([role.get("title", ""), role.get("family", ""), " ".join(role.get("industries") or []), desc])


# --------------------------------------------------------------- render
CSS = """
@page{size:A4;margin:16mm 17mm}
:root{--ink:#1d2321;--muted:#5b6461;--rule:#cfd6d3;--accent:#2e5e55}
*{box-sizing:border-box}
body{margin:0;color:var(--ink);background:#fff;font:10.2pt/1.42 "Source Sans 3","Helvetica Neue",Arial,sans-serif}
.page{max-width:176mm;margin:0 auto;padding:12mm 0}
header{display:flex;flex-direction:column;gap:2px;padding-bottom:8px;border-bottom:1.5px solid var(--accent)}
h1{font:600 20pt/1.1 "Source Serif 4",Georgia,serif;margin:0;letter-spacing:.01em}
.headline{color:var(--accent);font-weight:600}
.contact{color:var(--muted);font-size:9pt;display:flex;flex-wrap:wrap;gap:0 12px}
.contact a{color:inherit;text-decoration:none}
h2{font:600 9pt/1 "Source Sans 3",Arial,sans-serif;text-transform:uppercase;letter-spacing:.12em;color:var(--accent);
   margin:14px 0 6px;padding-bottom:3px;border-bottom:1px solid var(--rule)}
.summary{margin:8px 0 0}
.role{margin:0 0 9px;break-inside:avoid}
.role-h{display:flex;justify-content:space-between;gap:12px;align-items:baseline}
.role-h b{font-weight:600}
.dates{color:var(--muted);font-size:9pt;white-space:nowrap;font-variant-numeric:tabular-nums}
.context{color:var(--muted);font-size:9pt;font-style:italic}
ul{margin:3px 0 0;padding-left:15px}
li{margin:1px 0}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:4px 18px}
.line{display:flex;justify-content:space-between;gap:12px}
"""


def _when(start, end) -> str:
    def f(v):
        v = str(v or "")
        if v == "present":
            return "Present"
        m = re.fullmatch(r"(\d{4})-(\d{2})", v)
        months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
        return f"{months[int(m.group(2)) - 1]} {m.group(1)}" if m else v
    return " – ".join(x for x in (f(start), f(end)) if x)


def render(bank: dict, sel: dict) -> str:
    e = lambda s: html.escape(str(s or ""))
    p = bank.get("person") or {}
    contact = [x for x in (p.get("location"), p.get("email"), p.get("phone")) if x]
    short = lambda u: re.sub(r"^https?://(www\.)?", "", u)
    contact += [f'<a href="{e(u)}">{e(short(u))}</a>' for u in p.get("links") or []]
    parts = [f"<header><h1>{e(p.get('name'))}</h1>",
             f'<div class="headline">{e(p.get("headline"))}</div>' if p.get("headline") else "",
             '<div class="contact">' + "".join(f"<span>{c if c.startswith('<a') else e(c)}</span>" for c in contact) + "</div></header>"]
    if sel.get("summary"):
        parts.append(f'<p class="summary">{e(sel["summary"])}</p>')
    parts.append("<h2>Experience</h2>")
    for r in sel["roles"]:
        where = ", ".join(x for x in (r["company"], r["location"]) if x)
        parts.append(f'<div class="role"><div class="role-h"><span><b>{e(r["title"])}</b> · {e(where)}</span>'
                     f'<span class="dates">{e(_when(r["start"], r["end"]))}</span></div>'
                     + (f'<div class="context">{e(r["context"])}</div>' if r.get("context") else "")
                     + "<ul>" + "".join(f"<li>{e(b)}</li>" for b in r["bullets"]) + "</ul></div>")
    if bank.get("education"):
        parts.append("<h2>Education</h2>")
        for ed in bank["education"]:
            parts.append(f'<div class="line"><span><b>{e(ed.get("degree"))}</b> · {e(ed.get("school"))}</span>'
                         f'<span class="dates">{e(_when(ed.get("start"), ed.get("end")))}</span></div>')
    extra = [(k, ", ".join(v) if isinstance(v, list) else v) for k, v in (bank.get("skills") or {}).items()]
    if bank.get("languages"):
        extra.append(("Languages", ", ".join(bank["languages"])))
    if extra:
        parts.append("<h2>Skills</h2><div class=\"cols\">" +
                     "".join(f"<div><b>{e(k)}:</b> {e(v)}</div>" for k, v in extra) + "</div>")
    title = f"{p.get('name', 'CV')} CV"
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{e(title)}</title>'
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;600&'
            f'family=Source+Serif+4:wght@600&display=swap"><style>{CSS}</style></head><body><div class="page">'
            + "".join(parts) + "</div></body></html>")


def to_pdf(html_text: str, out: Path) -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        page = b.new_page()
        page.set_content(html_text, wait_until="networkidle")
        page.pdf(path=str(out), format="A4", print_background=True, prefer_css_page_size=True)
        b.close()
    return True


def slug(*parts: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", "-".join(p for p in parts if p).lower()).strip("-")[:80] or "cv"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=".")
    ap.add_argument("--check", action="store_true", help="validate the experience bank and stop")
    ap.add_argument("--role", help="role id or URL from the report")
    ap.add_argument("--job", help="a text file with any job description")
    ap.add_argument("--name", help="output file name (default: company-title)")
    ap.add_argument("--plan", help="JSON with summary, achievements (ids, in order), rephrase {id: bullet}")
    ap.add_argument("--no-pdf", action="store_true")
    a = ap.parse_args(argv)
    data = Path(a.data).resolve()
    bank = load_bank(data)
    issues = check(bank)
    if a.check:
        print("\n".join(issues) or "experience bank OK")
        return 1 if any("missing" in i or "no roles" in i or "should be" in i for i in issues) else 0
    if a.role:
        role, job_text = find_role(data, a.role)
        name = a.name or slug(role.get("company", ""), role.get("title", ""))
    elif a.job:
        job_text, name = Path(a.job).read_text(), a.name or slug(Path(a.job).stem)
    else:
        raise SystemExit("give --role or --job (or --check)")
    plan = json.loads(Path(a.plan).read_text()) if a.plan else None
    sel = select(bank, job_text, plan)
    out = data / "cvs"
    out.mkdir(exist_ok=True)
    page = render(bank, sel)
    (out / f"{name}.html").write_text(page)
    pdf = not a.no_pdf and to_pdf(page, out / f"{name}.pdf")
    print(f"cv: cvs/{name}.html" + (f" and cvs/{name}.pdf" if pdf else " (install requirements-browser.txt for a PDF)"))
    for r in sel["roles"]:
        print(f"  {r['title']} at {r['company']}: {len(r['bullets'])} bullet(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
