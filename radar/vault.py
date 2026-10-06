"""Mirror the data repo into the person's notes vault (one direction: repo -> vault).

    python -m radar.vault --data PATH [--vault PATH]

GitHub is the single source of truth; the vault is a read-only copy that catches up whenever a run can reach
it (a scheduled run bound to the computer, or any session on it). Nothing flows back: settings, wishlist and
profile are changed in the repo. When no vault root is reachable it prints so and exits 0.

From `agent/config.yaml` -> `vault` (paths relative to the root):
  roots               candidate vault roots, first existing wins ($HOME expanded); `root` is also accepted
  settings_note       its ```yaml block is replaced with settings.yaml, under a read-only notice
  report_note         overwritten with report/report.md
  alerts_note         new keep/stretch roles (local pool) as table rows under <!-- ALERTS START -->
  remote_alerts_note  same for the remote pool
  log_note            new scan/snoopy_log.md entries under <!-- LOG START -->, newest first
Bookkeeping: scan/vault_sync.json (last_sync, mirrored role ids).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

NOW = datetime.now(timezone.utc)
FIT = {"keep": "✅ Keep", "stretch": "🟡 Stretch"}
READ_ONLY = ("**Read-only copy.** The radar's settings live in GitHub (`settings.yaml` in the data repo); this note "
             "mirrors them whenever the radar can reach this vault. Edits here are not picked up: to change a setting, "
             "ask Claude (in Claude Code or your chat), e.g. \"add Space Capital to my portfolio boards\".")


def _load(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def find_root(vcfg: dict, override: str | None) -> Path | None:
    cands = [override] if override else []
    cands += list(vcfg.get("roots") or []) + ([vcfg["root"]] if vcfg.get("root") else [])
    for c in cands:
        p = Path(os.path.expandvars(os.path.expanduser(str(c))))
        if p.is_dir():
            return p
    return None


def _cell(s) -> str:
    return str(s or "–").replace("|", "/").replace("\n", " ").strip() or "–"


def _insert_after(text: str, marker: str, block: str) -> str:
    if marker in text:
        i = text.index(marker) + len(marker)
        return text[:i] + "\n\n" + block.strip("\n") + "\n" + text[i:]
    return text.rstrip("\n") + "\n\n" + marker + "\n\n" + block.strip("\n") + "\n"


def settings_note(path: Path, settings_text: str) -> bool:
    if not path.exists():
        path.write_text(f"# Job Radar Settings\n\n{READ_ONLY}\n\n```yaml\n{settings_text.rstrip()}\n```\n")
        return True
    s = path.read_text()
    m = re.search(r"```yaml\n.*?```", s, re.S)
    block = f"```yaml\n{settings_text.rstrip()}\n```"
    new = s[:m.start()] + block + s[m.end():] if m else s.rstrip("\n") + "\n\n" + block + "\n"
    if READ_ONLY not in new:                                   # once: put the notice above the block
        mm = re.search(r"```yaml\n", new)
        new = new[:mm.start()] + READ_ONLY + "\n\n" + new[mm.start():]
    if new != s:
        path.write_text(new)
        return True
    return False


def alert_rows(roles: list[dict]) -> str:
    rows = ["| Company | Role | Fit | Key requirements | Salary | Posted |", "|---|---|---|---|---|---|"]
    for r in roles:
        req = " · ".join(x for x in [r.get("note"), "; ".join(r.get("flags") or []),
                                     "; ".join(r.get("locations") or [])[:80], r.get("workplace")] if x)
        posted = (r.get("published") or r.get("first_seen") or r.get("found") or "")[:10]
        title = _cell(r.get("title")).replace("[", "(").replace("]", ")")
        rows.append(f"| {_cell(r.get('company'))} ❔ | [{title}]({r.get('url', '')}) | {FIT.get(r.get('fit'), r.get('fit'))} | "
                    f"{_cell(req)} | {_cell(r.get('salary'))} | {_cell(posted)} |")
    return "\n".join(rows)


def alerts(path: Path, roles: list[dict], heading: str) -> list[str]:
    """Add roles not yet in the note (matched by URL). Returns the ids written."""
    text = path.read_text() if path.exists() else f"# {path.stem}\n\n<!-- ALERTS START -->\n"
    fresh = [r for r in roles if r.get("url") and r["url"] not in text]
    if not fresh:
        return []
    fresh.sort(key=lambda r: (r.get("kind") != "vc", r.get("fit") != "keep", (r.get("company") or "").lower()))
    keep = sum(1 for r in fresh if r.get("fit") == "keep")
    block = (f"### {heading}\n\nNew judged roles from the radar ({keep} ✅ Keep, {len(fresh) - keep} 🟡 Stretch), confirmed live "
             f"when the report was built. Sponsor status not checked (❔).\n\n{alert_rows(fresh)}\n")
    path.write_text(_insert_after(text, "<!-- ALERTS START -->", block))
    return [r.get("id") for r in fresh]


def log_note(path: Path, log_text: str) -> int:
    entries = re.split(r"(?m)^(?=### )", log_text)
    entries = [e.strip("\n") for e in entries if e.startswith("### ")]
    text = path.read_text() if path.exists() else "# Radar Updates\n\n<!-- LOG START -->\n"
    new = [e for e in entries if e.splitlines()[0] not in text]
    if not new:
        return 0
    path.write_text(_insert_after(text, "<!-- LOG START -->", "\n\n".join(new)))   # snoopy_log is newest first
    return len(new)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("RADAR_DATA", "."))
    ap.add_argument("--vault", help="vault root (default: agent/config.yaml vault.roots)")
    args = ap.parse_args(argv)
    data = Path(args.data).resolve()
    cfg = yaml.safe_load((data / "agent/config.yaml").read_text()) or {}
    vcfg = cfg.get("vault") or {}
    if not vcfg:
        print("vault: not configured")
        return 0
    root = find_root(vcfg, args.vault)
    if not root:
        print("vault: not reachable from this run, skipped (it catches up on the next run that can reach it)")
        return 0
    state = _load(data / "scan/vault_sync.json", {})
    done: dict = {}
    if vcfg.get("settings_note"):
        done["settings"] = settings_note(root / vcfg["settings_note"], (data / "settings.yaml").read_text())
    if vcfg.get("report_note") and (data / "report/report.md").exists():
        md = (data / "report/report.md").read_text()
        link = cfg.get("report_artifact")
        (root / vcfg["report_note"]).write_text((f"[Open the live report]({link}) (filters, hide, New badges)\n\n" if link else "") + md)
        done["report"] = True
    rep = _load(data / "report/report.json", {})
    roles = [r for k in ("watchlist_roles", "market_roles") for r in rep.get(k) or [] if r.get("fit") in FIT]
    heading = NOW.strftime("%Y-%m-%d %H:%M UTC") + " · Job Radar"
    written = []
    if vcfg.get("alerts_note"):
        written += alerts(root / vcfg["alerts_note"], [r for r in roles if r.get("pool") != "remote"], heading)
    if vcfg.get("remote_alerts_note"):
        written += alerts(root / vcfg["remote_alerts_note"], [r for r in roles if r.get("pool") == "remote"], heading)
    done["alerts"] = len(written)
    if vcfg.get("log_note") and (data / "scan/snoopy_log.md").exists():
        done["log_entries"] = log_note(root / vcfg["log_note"], (data / "scan/snoopy_log.md").read_text())
    state.update({"last_sync": NOW.isoformat(timespec="minutes"), "root": str(root),
                  "mirrored": sorted(set(state.get("mirrored") or []) | {w for w in written if w})})
    (data / "scan").mkdir(exist_ok=True)
    (data / "scan/vault_sync.json").write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n")
    print(f"vault: mirrored to {root}: " + ", ".join(f"{k}={v}" for k, v in done.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
