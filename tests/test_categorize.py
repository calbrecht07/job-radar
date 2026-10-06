"""Role families and industries for the report's filters (no network)."""
import json

from radar import categorize


def test_families():
    f = categorize.family
    assert f("Sales Engineer") == "Engineering & solutions"
    assert f("Forward Deployed Engineer") == "Engineering & solutions"
    assert f("Deployment Strategist") == "Engineering & solutions"
    assert f("Senior Product Manager") == "Product"
    assert f("Product Operations Lead, AI") == "Product"
    assert f("Implementation Manager (DACH)") == "Customer & implementation"
    assert f("Technical Account Manager - UK") == "Customer & implementation"
    assert f("Partnerships Manager (London)") == "Sales & partnerships"
    assert f("Chief of Staff") == "Strategy & operations"
    assert f("Founders Associate") == "Strategy & operations"
    assert f("Associate", "vc") == "Investing"
    assert f("Venture Partner") == "Investing"
    assert f("Barista") == "Other"


def test_industries(tmp_path):
    (tmp_path / "pool").mkdir()
    (tmp_path / "pool/directory.csv").write_text(
        "key,name,industries\nwater.co,Aqua Ltd,water treatment; utilities\nrobo.ai,Robo,robotics\npay.com,PayCo,payments\n")
    (tmp_path / "pool/companies.json").write_text(json.dumps({"companies": [{"name": "Orbit Co", "industries": ["Space Industry"]}]}))
    settings = {"company_search": {"industries": [{"name": "watertech", "search": ["water treatment", "desalination"]},
                                                  {"name": "physical AI", "search": ["robotics"]}, "space"]}}
    ind = categorize.Industries(tmp_path, settings)
    assert ind.of("Aqua") == ["watertech"]                 # suffix-insensitive name match
    assert ind.of("Robo") == ["physical AI"]
    assert ind.of("Orbit Co") == ["space"]
    assert ind.of("PayCo") == ["Fintech"]                  # generic fallback
    assert ind.of("Nobody") == ["Unknown"]


def test_report_marks_new_roles(tmp_path, monkeypatch):
    from radar import report
    d = tmp_path
    for sub in ("scan", "report", "pool"):
        (d / sub).mkdir()
    (d / "settings.yaml").write_text("locations: {local: {label: London}}\n")
    role = {"id": "a1", "title": "Chief of Staff", "url": "https://x/1", "pool": "local", "locations": ["London"], "first_seen": "2026-10-01"}
    (d / "scan/watchlist.json").write_text(json.dumps({"companies": {"Acme": {"kind": "startup", "roles": [role]}}}))
    (d / "scan/health.json").write_text("{}")
    (d / "judged.json").write_text(json.dumps({"a1": {"fit": "keep", "note": "", "reviewed": "2026-10-01", "industry": "watertech"}}))
    monkeypatch.setattr("radar.html.build", lambda data: None)
    first = report.build(d, skip_verify=True)
    r = first["watchlist_roles"][0]
    assert r["family"] == "Strategy & operations" and r["industries"] == ["watertech"] and not r["new"]
    # a role appearing in a later build is new, and stays new on the next rebuild
    role2 = {**role, "id": "b2", "title": "Product Manager", "url": "https://x/2"}
    (d / "scan/watchlist.json").write_text(json.dumps({"companies": {"Acme": {"kind": "startup", "roles": [role, role2]}}}))
    j = json.loads((d / "judged.json").read_text()); j["b2"] = {"fit": "stretch", "note": "", "reviewed": "2026-10-02"}
    (d / "judged.json").write_text(json.dumps(j))
    for _ in range(2):
        rows = {x["id"]: x for x in report.build(d, skip_verify=True)["watchlist_roles"]}
        assert rows["b2"]["new"] and not rows["a1"]["new"]


def test_archive_keeps_history(tmp_path, monkeypatch):
    """Cut, closed and vanished roles go to the archive with a reason, and survive later rebuilds."""
    from radar import report
    d = tmp_path
    for sub in ("scan", "report", "pool"):
        (d / sub).mkdir()
    (d / "settings.yaml").write_text("locations: {local: {label: London}}\n")
    live = {"id": "a1", "title": "Chief of Staff", "url": "https://jobs.ashbyhq.com/acme/1", "pool": "local", "locations": ["London"]}
    gone = {"id": "b2", "title": "Product Manager", "url": "https://jobs.ashbyhq.com/acme/2", "pool": "local", "locations": ["London"]}
    (d / "scan/watchlist.json").write_text(json.dumps({"companies": {"Acme": {"kind": "startup", "roles": [live, gone]}}}))
    (d / "scan/health.json").write_text("{}")
    (d / "judged.json").write_text(json.dumps({
        "a1": {"fit": "keep", "reviewed": "2026-10-01"}, "b2": {"fit": "stretch", "reviewed": "2026-10-01"},
        "c3": {"fit": "cut", "note": "too senior", "url": "https://acme.com/careers/head-of-operations-europe", "reviewed": "2026-10-01"}}))
    monkeypatch.setattr("radar.html.build", lambda data: None)
    first = {r["id"]: r for r in report.build(d, skip_verify=True)["archive"]}
    assert first["c3"]["status"] == "cut" and first["c3"]["why"] == "too senior"
    assert first["c3"]["title"] == "Head of operations europe" and "b2" not in first
    # next run: the PM posting has gone from the feed
    (d / "scan/watchlist.json").write_text(json.dumps({"companies": {"Acme": {"kind": "startup", "roles": [live]}}}))
    second = {r["id"]: r for r in report.build(d, skip_verify=True)["archive"]}
    assert second["b2"]["status"] == "gone" and second["b2"]["title"] == "Product Manager" and second["b2"]["company"] == "Acme"
    assert "a1" not in second and second["c3"]["status"] == "cut"
