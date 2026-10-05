import json
from pathlib import Path

from radar import report, verify


def _data(tmp_path: Path) -> Path:
    (tmp_path / "scan").mkdir()
    (tmp_path / "settings.yaml").write_text("locations:\n  local:\n    label: London\n")
    roles = [{"id": f"r{i}", "title": f"Role {i}", "url": f"https://x.test/{i}", "locations": ["London"], "pool": "local",
              "workplace": "", "flags": [], "first_seen": "2026-10-01"} for i in range(3)]
    (tmp_path / "scan/watchlist.json").write_text(json.dumps({"companies": {"Acme": {"kind": "startup", "roles": roles}}}))
    (tmp_path / "scan/health.json").write_text(json.dumps({"run_at": "2026-10-05T10:00", "boards": 1}))
    (tmp_path / "judged.json").write_text(json.dumps({f"r{i}": {"fit": "keep", "note": "ok"} for i in range(3)}))
    return tmp_path


def test_closed_hidden_and_unverified_parked(tmp_path, monkeypatch):
    data = _data(tmp_path)
    monkeypatch.setattr(verify, "run", lambda d: {"updated": "2026-10-05T11:00", "checked": 3,
                                                   "closed": {"r1": {"reason": "HTTP 404"}},
                                                   "unverified": {"r2": "unverified: page needs JavaScript"}})
    rep = report.build(data)
    assert [r["id"] for r in rep["watchlist_roles"]] == ["r0"]
    assert [r["id"] for r in rep["unverified_roles"]] == ["r2"]
    md = (data / "report/report.md").read_text()
    assert "Role 1" not in md and "Could not verify" in md and "Role 2" in md
    assert "confirmed live" in md
