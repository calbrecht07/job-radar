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


def test_closed_hidden_and_unverified_tagged(tmp_path, monkeypatch):
    data = _data(tmp_path)
    monkeypatch.setattr(verify, "run", lambda d: {"updated": "2026-10-05T11:00", "checked": 3,
                                                   "closed": {"r1": {"reason": "HTTP 404"}},
                                                   "unverified": {"r2": "unverified: page needs JavaScript"}})
    rep = report.build(data)
    # closed roles are gone; unverifiable ones stay in their list, tagged for the person to check
    assert [r["id"] for r in rep["watchlist_roles"]] == ["r0", "r2"]
    assert rep["watchlist_roles"][1]["check_link"] == "page needs JavaScript" and "check_link" not in rep["watchlist_roles"][0]
    assert [r["id"] for r in rep["unverified_roles"]] == ["r2"]
    md = (data / "report/report.md").read_text()
    assert "Role 1" not in md and "Could not verify" in md and "Role 2" in md and "check the link" in md
    assert "confirmed live" in md


def test_feed_role_missing_from_fresh_feed_is_closed(tmp_path):
    data = _data(tmp_path)
    (data / "state").mkdir()
    (data / "state/live.json").write_text(json.dumps(["https://x.test/0", "https://x.test/1", "https://x.test/2"]))
    (data / "state/companies.json").write_text(json.dumps({"workable:oqc": {"name": "OQC", "last_ok": "2026-10-07", "fails": 0},
                                                          "workable:other": {"name": "Old Co", "last_ok": "2026-10-01", "fails": 3}}))
    (data / "scan/matches.json").write_text(json.dumps([
        {"id": "m1", "company": "OQC", "ats": "workable", "title": "PM", "url": "https://apply.workable.com/j/AB12", "layer": "market"},
        {"id": "m2", "company": "VC Co", "ats": "portfolio", "title": "Ops", "url": "mailto:jobs@vc.co", "layer": "market"}]))
    out = verify.run(data)
    assert out["closed"]["m1"]["reason"] == "no longer in the company's workable feed"
    assert out["closed"]["m2"]["reason"] == "no longer on the VC portfolio board"


def test_rebuild_reuses_the_scans_liveness_check(tmp_path, monkeypatch):
    data = _data(tmp_path)
    (data / "scan/closed.json").write_text(json.dumps({"updated": "2026-10-05T10:05", "closed": {"r1": {"reason": "HTTP 404"}},
                                                       "unverified": {}, "relinked": {"r2": {"url": "https://acme.com/careers/role-2", "exact": True}}}))
    called = []
    monkeypatch.setattr(verify, "run", lambda d: called.append(1) or {})
    rep = report.build(data)                       # health.run_at 10:00 < closed.json 10:05: reuse
    assert not called and [r["id"] for r in rep["watchlist_roles"]] == ["r0", "r2"]
    assert rep["watchlist_roles"][1]["url"] == "https://acme.com/careers/role-2"
    monkeypatch.setenv("RADAR_FORCE_VERIFY", "1")
    report.build(data)
    assert called


def test_link_dead_and_relink(monkeypatch):
    from unittest import mock
    from sources import boards as ats

    class R:
        def __init__(self, status=200, text="", url=""):
            self.status_code, self.text, self.url = status, text, url
    monkeypatch.setattr(ats, "hosted_board_live", lambda a, s: False)
    assert "switched off" in verify.link_dead("https://jobs.ashbyhq.com/acme/123")
    monkeypatch.setattr(ats, "hosted_board_live", lambda a, s: True)
    with mock.patch.object(verify.requests, "get", lambda *a, **k: R(404, "", "https://x.test/1")):
        assert verify.link_dead("https://x.test/1") == "HTTP 404"
    page = '<a href="/careers/solutions-architect">Solutions Architect - Western Europe</a>'
    with mock.patch.object(verify.requests, "get", lambda *a, **k: R(200, page, "https://acme.com/careers")):
        assert verify.relink("Acme", "Solutions Architect - Western Europe", "https://acme.com/careers") == \
            ("https://acme.com/careers/solutions-architect", True)
