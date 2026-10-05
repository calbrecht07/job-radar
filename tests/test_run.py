"""End-to-end: a temp data dir, mocked feeds and careers page."""
import json
import shutil
from pathlib import Path
from unittest import mock

from radar import ats, pages, run

EX = Path(__file__).parent.parent / "config.example"

FEEDS = {
    ("ashby", "acme"): [
        {"id": "1", "title": "Chief of Staff", "locations": ["London"], "remote": False, "workplace": "hybrid",
         "url": "https://jobs.ashbyhq.com/acme/1", "published": "2026-10-01", "department": "", "salary": "",
         "description": "Great role"},
        {"id": "2", "title": "Senior Software Engineer", "locations": ["London"], "remote": False, "workplace": "",
         "url": "https://jobs.ashbyhq.com/acme/2", "published": "2026-10-01", "department": "", "salary": "",
         "description": "code"}],
    ("greenhouse", "remoteco"): [
        {"id": "9", "title": "Solutions Engineer", "locations": ["Remote - EMEA"], "remote": None, "workplace": "",
         "url": "https://boards.greenhouse.io/remoteco/jobs/9", "published": None, "department": "", "salary": "",
         "description": "Remote role"}],
}


def fake_fetch(a, s):
    if (a, s) not in FEEDS:
        raise ats.NotFound(s)
    return [dict(p) for p in FEEDS[(a, s)]]


PAGE1 = "<html><body>" + "".join(f"<p>Line {i}</p>" for i in range(30)) + \
        '<a href="https://acme2.teamtailor.com/jobs">Jobs</a></body></html>'
PAGE2 = PAGE1.replace("Line 3<", "Head of Partnerships<")


def make_data(tmp: Path):
    shutil.copy(EX / "settings.yaml", tmp / "settings.yaml")
    (tmp / "watchlist.csv").write_text(
        "name,kind,ats,slug,careers_url,source,note\n"
        "Acme,startup,ashby,acme,,,\n"
        "PageCo,startup,,,https://pageco.example/careers,,\n")
    (tmp / "index.csv").write_text("name,kind,ats,slug,added,source\nRemoteCo,startup,greenhouse,remoteco,2026-10-01,discover\n")


def test_two_runs(tmp_path):
    make_data(tmp_path)
    html = {"v": PAGE1}

    class R:
        status_code = 200
        @property
        def text(self):
            return html["v"]

    with mock.patch.object(ats, "fetch", fake_fetch), \
         mock.patch.object(pages.requests, "get", lambda *a, **k: R()):
        assert run.main(["--data", str(tmp_path)]) == 0
        pend = json.loads((tmp_path / "output/pending.json").read_text())
        titles = {(c["layer"], c["title"]) for c in pend}
        assert titles == {("watchlist", "Chief of Staff"), ("market", "Solutions Engineer")}
        wl = json.loads((tmp_path / "output/watchlist.json").read_text())["companies"]
        assert wl["Acme"]["roles"][0]["title"] == "Chief of Staff"
        assert wl["PageCo"]["page_status"] == "first_check"
        rep = (tmp_path / "report/report.md").read_text()
        assert "[Chief of Staff](https://jobs.ashbyhq.com/acme/1)" in rep and "Solutions Engineer" in rep

        # second run: nothing new; page changed
        html["v"] = PAGE2
        assert run.main(["--data", str(tmp_path), "--audit"]) == 0
        h = json.loads((tmp_path / "output/health.json").read_text())
        assert h["new_postings"] == 0 and h["pages_changed"] == 1
        wl = json.loads((tmp_path / "output/watchlist.json").read_text())["companies"]
        assert "Head of Partnerships" in wl["PageCo"]["page_added"]
        assert wl["Acme"]["roles"], "watchlist keeps showing current open roles"
        audit = json.loads((tmp_path / "output/board_audit.json").read_text())["mismatches"]
        assert audit[0]["found_on_page"] == ["teamtailor:acme2"]

    # reviewer cuts the market role -> hidden from the report
    pend_id = [c["id"] for c in pend if c["layer"] == "market"][0]
    (tmp_path / "judged.json").write_text(json.dumps({pend_id: {"fit": "cut"}}))
    from radar import report
    r = report.build(tmp_path)
    assert not r["market_roles"] and r["hidden_cut"] == 1
