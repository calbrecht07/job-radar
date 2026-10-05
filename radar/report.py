"""Build report/report.md and report/report.json from the latest run + reviewer verdicts.

Run standalone after reviewing:  python -m radar.report --data PATH
Reviewer verdicts live in judged.json: {candidate_id: {"fit": "keep"|"stretch"|"cut", "note": "...", ...}}
Roles judged "cut" are hidden from the report (counted at the bottom).
Roles found outside the feeds go in extra_roles.json: [{id, company, kind, title, url, pool, locations,
  fit, note, found, layer, closed}] (layer defaults to watchlist; set closed: true when the posting is gone).
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

FIT = {"keep": "✅ Keep", "stretch": "🟡 Stretch", None: "⏳ to review"}


def _load(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _md(s) -> str:
    return str(s or "").replace("|", "/").replace("\n", " ").strip()


def where(r: dict, local_label: str) -> str:
    locs = "; ".join(r.get("locations") or [])
    wp = (r.get("workplace") or "").lower()
    if r.get("pool") == "remote":
        return f"Remote · {locs}"[:70]
    return f"{local_label}{' · ' + wp if wp and wp != 'remote' else ''}" if local_label.lower() in locs.lower() else locs[:70]


def build(data: Path, cfg: dict | None = None) -> dict:
    cfg = cfg or yaml.safe_load((data / "settings.yaml").read_text()) or {}
    local_label = ((cfg.get("locations") or {}).get("local") or {}).get("label") or "Local"
    days = int((cfg.get("output") or {}).get("report_market_days", 7))
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).isoformat()

    wl = _load(data / "output/watchlist.json", {}).get("companies", {})
    health = _load(data / "output/health.json", {})
    audit = _load(data / "output/board_audit.json", {}).get("mismatches", [])
    matches = _load(data / "output/matches.json", [])
    judged = _load(data / "judged.json", {})

    def verdict(rid):
        j = judged.get(rid) or {}
        return j.get("fit"), j.get("note", "")

    # ---- watchlist
    wl_roles, quiet, pages_rows, hidden, matches_extra = [], [], [], 0, []
    for name, s in sorted(wl.items(), key=lambda kv: (kv[1].get("kind") != "vc", kv[0].lower())):
        shown = []
        for r in s.get("roles", []):
            fit, note = verdict(r["id"])
            if fit == "cut":
                hidden += 1
                continue
            shown.append({**r, "company": name, "kind": s.get("kind"), "fit": fit, "note": note})
        wl_roles += shown
        if s.get("board") == "" and s.get("page_status"):
            pages_rows.append({"company": name, "status": s["page_status"], "added": s.get("page_added") or [],
                               "error": s.get("page_error"), "url": s.get("careers_url")})
        elif not shown:
            quiet.append({"company": name, "error": s.get("error")})

    # roles a reviewer found off-feed (careers pages, VC sites, web search): extra_roles.json
    for e in _load(data / "extra_roles.json", []):
        if e.get("closed") or e.get("fit") == "cut":
            continue
        row = {"pool": "local", "locations": [], "workplace": "", "flags": [], "first_seen": e.get("found", "")[:10], **e}
        (wl_roles if e.get("layer", "watchlist") == "watchlist" else matches_extra).append(row)
    rank = {"keep": 0, "stretch": 1, None: 2}
    wl_roles.sort(key=lambda r: (r.get("kind") != "vc", rank.get(r.get("fit"), 3), r["company"].lower()))

    # ---- market (last N days, not on the watchlist)
    mk = []
    for m in matches:
        if m.get("layer") != "market" or m.get("found", "") < since:
            continue
        fit, note = verdict(m["id"])
        if fit == "cut":
            hidden += 1
            continue
        mk.append({**m, "fit": fit, "note": note})
    mk += [{**m, "found": m.get("found") or m.get("first_seen", "")} for m in matches_extra]
    mk.sort(key=lambda m: (m.get("kind") != "vc", rank.get(m.get("fit"), 3), m["company"].lower()))

    rep = {"updated": health.get("run_at"), "boards": health.get("boards"), "watchlist_size": len(wl),
           "watchlist_roles": wl_roles, "watchlist_quiet": quiet, "pages": pages_rows,
           "market_days": days, "market_roles": mk, "hidden_cut": hidden,
           "failing": (health.get("not_found") or []) + [f for f in health.get("failed") or [] if f.get("consecutive_fails", 0) >= 2],
           "audit": audit}
    out = data / "report"
    out.mkdir(exist_ok=True)
    (out / "report.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False) + "\n")
    (out / "report.md").write_text(render_md(rep, local_label))
    from radar import html
    html.build(data)
    return rep


def render_md(rep: dict, local_label: str) -> str:
    L = []
    with_roles = len({r["company"] for r in rep["watchlist_roles"]})
    L += ["# Job Radar report", "",
          f"Updated {(rep.get('updated') or '')[:16].replace('T', ' ')} UTC · {rep.get('boards')} job boards checked · "
          f"{rep['watchlist_size']} watchlist companies", ""]

    L += ["## 1. Watchlist", "", f"**{with_roles} of {rep['watchlist_size']} companies have matching roles open.**", ""]
    if rep["watchlist_roles"]:
        L += ["| Company | Role | Where | Fit | Since | Notes |", "|---|---|---|---|---|---|"]
        for r in rep["watchlist_roles"]:
            notes = "; ".join(filter(None, [r.get("note")] + (r.get("flags") or [])))
            vc = " (VC)" if r.get("kind") == "vc" else ""
            L.append(f"| {_md(r['company'])}{vc} | [{_md(r['title'])}]({r['url']}) | {_md(where(r, local_label))} | "
                     f"{FIT.get(r.get('fit'), r.get('fit'))} | {r.get('first_seen', '')} | {_md(notes)[:160]} |")
        L.append("")
    changed = [p for p in rep["pages"] if p["status"] == "changed"]
    other = [p for p in rep["pages"] if p["status"] != "changed"]
    if rep["pages"]:
        L += ["### Careers pages (companies without a job-board feed)", ""]
        for p in changed:
            added = "; ".join(p["added"][:8])
            L.append(f"- **[{_md(p['company'])}]({p['url']})**: page changed. New lines: {_md(added)[:300]}")
        groups = {}
        for p in other:
            groups.setdefault(p["status"], []).append(f"[{_md(p['company'])}]({p['url']})" if p.get("url") else _md(p["company"]))
        labels = {"unchanged": "Unchanged", "first_check": "First check (baseline saved)",
                  "js_only": "Loads jobs with JavaScript: check in a browser", "error": "Couldn't load",
                  "not_checked": "No feed or careers page: check by web search"}
        for st, items in groups.items():
            L.append(f"- {labels.get(st, st)}: {', '.join(items)}")
        L.append("")
    if rep["watchlist_quiet"]:
        L += ["### No matching roles right now", "",
              ", ".join(_md(q["company"]) + (" ⚠️" if q.get("error") else "") for q in rep["watchlist_quiet"]), ""]

    L += [f"## 2. Market search (last {rep['market_days']} days)", ""]
    if rep["market_roles"]:
        L += ["| Company | Role | Where | Fit | Found | Notes |", "|---|---|---|---|---|---|"]
        for r in rep["market_roles"]:
            notes = "; ".join(filter(None, [r.get("note")] + (r.get("flags") or [])))
            vc = " (VC)" if r.get("kind") == "vc" else ""
            L.append(f"| {_md(r['company'])}{vc} | [{_md(r['title'])}]({r['url']}) | {_md(where(r, local_label))} | "
                     f"{FIT.get(r.get('fit'), r.get('fit'))} | {r.get('found', '')[:10]} | {_md(notes)[:160]} |")
        L.append("")
    else:
        L += ["No new market matches in this period.", ""]

    L += ["## 3. Needs attention", ""]
    for f in rep["failing"]:
        L.append(f"- Job board not responding: {_md(f['company'])} ({f['board']}: {_md(f['error'])[:80]})")
    for a in rep["audit"]:
        L.append(f"- Board check: {_md(a['company'])} is configured as `{a['configured'] or 'none'}`, "
                 f"careers page links to {', '.join('`' + b + '`' for b in a['found_on_page'])}")
    if not rep["failing"] and not rep["audit"]:
        L.append("- Nothing.")
    if rep["hidden_cut"]:
        L += ["", f"_{rep['hidden_cut']} roles reviewed and cut are hidden._"]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=".")
    a = ap.parse_args()
    r = build(Path(a.data).resolve())
    print(f"report: {len(r['watchlist_roles'])} watchlist roles, {len(r['market_roles'])} market roles")
