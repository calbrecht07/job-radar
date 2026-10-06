"""End-to-end: a temp data dir, mocked feeds and careers page."""
import json
import shutil
from pathlib import Path
from unittest import mock

from sources import boards as ats, pages
from radar import scan as run

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
        '<a href="/careers/chief-of-staff-london">Chief of Staff, London</a><a href="/careers/">View all jobs</a>' + \
        '<a href="/careers/backend-engineer">Senior Backend Engineer</a>' + \
        '<a href="https://pageco.pinpointhq.com/en/postings">Jobs</a></body></html>'
PAGE2 = PAGE1.replace("Line 3<", "Head of Partnerships<")


def make_data(tmp: Path):
    shutil.copy(EX / "settings.yaml", tmp / "settings.yaml")
    (tmp / "wishlist.csv").write_text(
        "name,kind,ats,slug,careers_url,source,note\n"
        "Acme,startup,ashby,acme,,,\n"
        "PageCo,startup,,,https://pageco.example/careers,,\n")
    (tmp / "index.csv").write_text("name,kind,ats,slug,added,source\nRemoteCo,startup,greenhouse,remoteco,2026-10-01,discover\n")


def test_two_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_SKIP_VERIFY", "1")
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
        pend = json.loads((tmp_path / "scan/pending.json").read_text())
        titles = {(c["layer"], c["title"]) for c in pend}
        assert titles == {("watchlist", "Chief of Staff"), ("market", "Solutions Engineer"),
                          ("watchlist", "Chief of Staff, London")}
        wl = json.loads((tmp_path / "scan/watchlist.json").read_text())["companies"]
        assert wl["Acme"]["roles"][0]["title"] == "Chief of Staff"
        assert wl["PageCo"]["page_status"] == "first_check"
        q = json.loads((tmp_path / "scan/review_queue.json").read_text())
        assert {c["title"] for c in q} == {"Chief of Staff", "Solutions Engineer", "Chief of Staff, London"}
        assert wl["PageCo"]["roles"][0]["url"] == "https://pageco.example/careers/chief-of-staff-london"
        rep = (tmp_path / "report/report.md").read_text()
        assert "jobs.ashbyhq.com/acme/1" not in rep and "waiting for Snoopy" in rep   # unjudged: not published
        ids = {c["title"]: c["id"] for c in q}
        (tmp_path / "judged.json").write_text(json.dumps({ids["Chief of Staff"]: {"fit": "keep"}, ids["Solutions Engineer"]: {"fit": "stretch", "note": "gap"}}))
        from radar import report as rep_mod
        rep_mod.build(tmp_path, skip_verify=True)
        rep = (tmp_path / "report/report.md").read_text()
        assert "[Chief of Staff](https://jobs.ashbyhq.com/acme/1)" in rep and "Solutions Engineer" in rep

        # second run: nothing new; page changed
        html["v"] = PAGE2
        assert run.main(["--data", str(tmp_path), "--audit"]) == 0
        h = json.loads((tmp_path / "scan/health.json").read_text())
        assert h["new_postings"] == 0 and h["pages_changed"] == 1
        wl = json.loads((tmp_path / "scan/watchlist.json").read_text())["companies"]
        assert "Head of Partnerships" in wl["PageCo"]["page_added"]
        assert wl["Acme"]["roles"], "watchlist keeps showing current open roles"
        audit = json.loads((tmp_path / "scan/board_audit.json").read_text())["mismatches"]
        assert audit[0]["found_on_page"] == ["pinpoint:pageco"]

    # reviewer cuts the market role -> hidden from the report
    pend_id = [c["id"] for c in pend if c["layer"] == "market"][0]
    j = json.loads((tmp_path / "judged.json").read_text()); j[pend_id] = {"fit": "cut"}
    (tmp_path / "judged.json").write_text(json.dumps(j))
    from radar import report
    r = report.build(tmp_path, skip_verify=True)
    assert not r["market_roles"] and r["hidden_cut"] == 1


def test_first_fetch_window_for_discovered_companies(tmp_path, monkeypatch):
    """A board the radar discovered gets the market window (45 days) on its first fetch; a board the person
    listed by hand keeps first_fetch_max_age_days (21)."""
    from datetime import datetime, timedelta, timezone
    monkeypatch.setenv("RADAR_SKIP_VERIFY", "1")
    shutil.copy(EX / "settings.yaml", tmp_path / "settings.yaml")
    (tmp_path / "wishlist.csv").write_text("name,kind,ats,slug,careers_url,source,note\n")
    (tmp_path / "index.csv").write_text("name,kind,ats,slug,added,source\n"
                                        "Found Co,corporate,ashby,found,2026-10-05,directory\n"
                                        "Known Co,startup,ashby,known,2026-10-05,manual\n")
    month_ago = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
    post = lambda slug: [{"id": "1", "title": "Chief of Staff", "locations": ["London"], "remote": False, "workplace": "",
                          "url": f"https://jobs.ashbyhq.com/{slug}/1", "published": month_ago, "description": "x"}]
    with mock.patch.object(ats, "fetch", lambda a, s: post(s)):
        assert run.main(["--data", str(tmp_path)]) == 0
    pend = json.loads((tmp_path / "scan/pending.json").read_text())
    assert [c["company"] for c in pend] == ["Found Co"]
    assert json.loads((tmp_path / "scan/health.json").read_text())["dropped"]["first_fetch_older"] == 1
