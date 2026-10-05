"""Company directory, careers discovery, Workday, JobPosting data (fixtures only, no network)."""
from datetime import datetime, timedelta, timezone
from unittest import mock

from radar import careers, directory, scan, verify
from radar.filters import Filters
from sources import boards as ats
from sources import directories, jobdata

SETTINGS = {"roles": {"include": ["chief of staff", "solutions? architect", "product manager"], "exclude": ["intern"]},
            "company_kinds": ["startup", "vc", "corporate"],
            "locations": {"local": {"label": "London", "match": ["london"]},
                          "remote": {"allowed_regions": ["europe", "uk"], "blocked_regions": ["united states"]}}}


class R:
    def __init__(self, status=200, text="", url="https://x.com/", headers=None, encoding="utf-8"):
        self.encoding = encoding
        self.status_code, self.text, self.url = status, text, url
        self.headers = headers or {"content-type": "text/html"}

    def iter_content(self, n):
        yield self.text.encode()

    def close(self):
        pass

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


# ---------------------------------------------------------------- Workday
def test_workday_detection_forms():
    assert ats.detect_boards("https://acme.wd3.myworkdayjobs.com/en-US/Acme_Careers/job/x") == [("workday", "acme.wd3/Acme_Careers")]
    assert ats.detect_boards("https://wd5.myworkdaysite.com/recruiting/acme/Ext?x=1") == [("workday", "wd5/acme/Ext")]
    assert ats.detect_boards("https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/Ext/jobs") == [("workday", "acme.wd1/Ext")]
    assert ats.plausible("Acme plc", "wd5/acme/Ext") and not ats.plausible("Other Co", "acme.wd3/Acme_Careers")


def test_workday_adapter_pages_and_resolves_multi_location():
    page1 = {"total": 3, "jobPostings": [
        {"title": "Chief of Staff", "externalPath": "/job/London/Chief-of-Staff_R1", "locationsText": "London",
         "postedOn": "Posted Today", "remoteType": "Hybrid", "bulletFields": ["R1"]},
        {"title": "Product Manager", "externalPath": "/job/x/PM_R2", "locationsText": "2 Locations",
         "postedOn": "Posted 3 Days Ago", "bulletFields": ["R2"]}]}
    page2 = {"total": 3, "jobPostings": [{"title": "Analyst", "externalPath": "/job/Paris/A_R3", "locationsText": "Paris",
                                          "postedOn": "Posted 30+ Days Ago", "bulletFields": ["R3"]}]}
    calls = []

    def post(url, body):
        calls.append(body["offset"])
        return page1 if body["offset"] == 0 else page2

    detail = {"jobPostingInfo": {"location": "London", "additionalLocations": ["Dublin"], "jobDescription": "<p>Hi</p>"}}
    with mock.patch.object(ats, "_post", post), mock.patch.object(ats, "_get", lambda url, params=None: detail), \
            mock.patch("time.sleep"):
        out = ats.fetch("workday", "acme.wd3/Ext")
    assert calls == [0, 2] and len(out) == 3
    assert out[0]["url"] == "https://acme.wd3.myworkdayjobs.com/Ext/job/London/Chief-of-Staff_R1"
    assert out[0]["workplace"] == "hybrid" and out[0]["published"] == datetime.now(timezone.utc).date().isoformat()
    assert out[1]["locations"] == ["London", "Dublin"] and out[1]["description"] == "Hi"
    assert out[2]["published"] == (datetime.now(timezone.utc).date() - timedelta(days=30)).isoformat()


# ------------------------------------------------------------ JobPosting
LD = """<html><script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"JobPosting",
"title":"Chief of Staff &amp; Strategy","identifier":{"value":"J-9"},"datePosted":"2026-09-30","validThrough":"%s",
"description":"&lt;p&gt;Lead &lt;b&gt;projects&lt;/b&gt;&lt;/p&gt;","hiringOrganization":{"name":"Acme"},
"jobLocation":{"@type":"Place","address":{"addressLocality":"London","addressCountry":"GB"}},
"baseSalary":{"currency":"GBP","value":{"minValue":80000,"maxValue":95000,"unitText":"YEAR"}}}]}</script></html>"""


def test_jobdata_parses_graph_and_expiry():
    future = (datetime.now(timezone.utc) + timedelta(days=20)).date().isoformat()
    p = jobdata.postings(LD % future, "https://acme.com/jobs/9")[0]
    assert p["title"] == "Chief of Staff & Strategy" and p["id"] == "J-9" and p["company"] == "Acme"
    assert p["locations"] == ["London, GB"] and p["description"] == "Lead projects"
    assert p["salary"] == "GBP 80000–95000 YEAR" and p["url"] == "https://acme.com/jobs/9"
    assert not jobdata.expired(p)
    assert jobdata.expired(jobdata.postings(LD % "2020-01-01")[0])
    assert jobdata.postings("<script type='application/ld+json'>{broken</script>") == []


def test_verify_uses_job_data():
    shell = '<html><head>%s</head><body><div id="root"></div></body></html>'
    future = (datetime.now(timezone.utc) + timedelta(days=20)).date().isoformat()
    for when, live in ((future, True), ("2020-01-01", False)):
        resp = R(text=shell % (LD % when), url="https://acme.com/jobs/9")
        with mock.patch.object(verify.requests, "get", lambda *a, **k: resp):
            ok, reason = verify.check_url("https://acme.com/jobs/9", "Chief of Staff & Strategy")
        assert ok == live, reason
    # no closing date: a JavaScript shell stays unverified
    resp = R(text=shell % (LD.replace(',"validThrough":"%s"', "")), url="https://acme.com/jobs/9")
    with mock.patch.object(verify.requests, "get", lambda *a, **k: resp):
        ok, reason = verify.check_url("https://acme.com/jobs/9", "Chief of Staff & Strategy")
    assert ok and reason.startswith("unverified")


# ------------------------------------------------------- careers discovery
HOME = """<a href="/about">About</a><a href="/careers">Careers</a><a href="https://linkedin.com/company/acme">Jobs on LinkedIn</a>"""
CAREERS = """<h1>Life at Acme</h1><a href="/careers/life-at-acme">Life at Acme Corp</a>
<a href="/careers/search">Search all jobs</a>"""
SEARCH = """<a href="/careers/job/chief-of-staff-1234">Chief of Staff, London</a>
<a href="/careers/job/intern-5678">Summer Intern</a><a href="https://other.com/jobs/77">Other company role</a>"""


def test_discover_follows_to_job_list():
    site = {"https://acme.com": R(text=HOME, url="https://acme.com/"),
            "https://acme.com/careers": R(text=CAREERS, url="https://acme.com/careers"),
            "https://acme.com/careers/search": R(text=SEARCH, url="https://acme.com/careers/search")}
    with mock.patch.object(careers.requests, "get", lambda url, **k: site[url.rstrip("/")]):
        p = careers.discover({"name": "Acme", "website": "https://acme.com"})
    assert p["method"] == "page" and p["careers_url"] == "https://acme.com/careers/search"
    assert [j["title"] for j in p["jobs"]] == ["Chief of Staff, London", "Summer Intern"]   # other.com dropped


def test_discover_feed_enterprise_and_failures():
    home = '<a href="https://acme.wd3.myworkdayjobs.com/Acme_Ext">Careers</a>'
    with mock.patch.object(careers.requests, "get", lambda url, **k: R(text=home, url=url)), \
            mock.patch.object(ats, "fetch", lambda a, s: [{"title": "PM", "url": "u", "locations": ["London"]}]):
        p = careers.discover({"name": "Acme", "website": "https://acme.com"})
    assert p["method"] == "feed" and p["board"] == "workday:acme.wd3/Acme_Ext" and len(p["jobs"]) == 1
    home = '<a href="https://career5.successfactors.eu/career?company=acme">Careers</a>'
    with mock.patch.object(careers.requests, "get", lambda url, **k: R(text=home, url=url)):
        assert careers.discover({"name": "Acme", "website": "acme.com"})["enterprise"] == "successfactors"
    with mock.patch.object(careers.requests, "get", lambda url, **k: R(status=403)):
        assert careers.discover({"name": "Acme", "website": "acme.com"})["method"] == "blocked"
    with mock.patch.object(careers.requests, "get", mock.Mock(side_effect=careers.requests.ConnectionError)):
        assert careers.discover({"name": "Acme", "website": "acme.com"})["method"] == "dead"


def test_registrable():
    assert careers.registrable("https://careers.acme.co.uk/x") == "acme.co.uk"
    assert careers.registrable("www.acme.com") == "acme.com"


# ----------------------------------------------------------------- directory
def test_merge_dedupes_and_infers_kind():
    d, names = {}, {}
    directory.merge(d, {"name": "Acme plc", "website": "https://www.acme.com", "sources": ["wikidata:city"],
                        "employees": 5000, "industries": ["fintech"]}, names)
    directory.merge(d, {"name": "Acme", "website": "", "sources": ["research"], "industries": ["payments"]}, names)
    directory.merge(d, {"name": "Tiny AI", "website": "https://tiny.ai", "sources": ["portfolio"]}, names)
    directory.merge(d, {"name": "Old Bank", "website": "https://oldbank.com", "sources": ["wikidata:city"]}, names)
    assert set(d) == {"acme.com", "tiny.ai", "oldbank.com"}
    assert d["acme.com"]["sources"] == "wikidata:city; research" and d["acme.com"]["industries"] == "fintech; payments"
    assert d["acme.com"]["kind"] == "corporate" and d["tiny.ai"]["kind"] == "startup" and d["oldbank.com"]["kind"] == "corporate"


def test_priority_order():
    new = {"a", "b", "c"}
    rows = [{"key": "c", "sources": "wikidata:city", "employees": "90000"},
            {"key": "a", "sources": "research"},
            {"key": "b", "sources": "wikidata:industry:space", "industries": "space"},
            {"key": "old", "sources": "research", "checked": "2026-01-01"}]
    assert [r["key"] for r in sorted(rows, key=lambda r: directory.priority(r, new))] == ["a", "b", "c", "old"]


def test_list_page_directory():
    page = """<a href="https://member-one.com">Member One</a><a href="https://linkedin.com/x">LinkedIn</a>
    <a href="/about">About</a><a href="https://www.member-two.co.uk/en"><img alt="Member Two Ltd" src="l.png"></a>
    <a href="https://member-one.com/careers">Member One careers</a>"""
    with mock.patch.object(directories.requests, "get", lambda *a, **k: R(text=page, url="https://assoc.org/members")):
        out = directories.from_list_page("https://assoc.org/members")
    assert out == [{"name": "Member One", "website": "https://member-one.com"},
                   {"name": "Member Two Ltd", "website": "https://member-two.co.uk"}]


def test_located_reads_job_page():
    flt = Filters(SETTINGS)
    with mock.patch.object(scan.pages.requests, "get", lambda *a, **k: R(text="<p>Chief of Staff</p><p>Based in our London office</p>" * 5)):
        p = scan.located({"title": "Chief of Staff", "url": "https://acme.com/job/1"}, flt)
    assert p["locations"] == ["London"] and flt.evaluate(p, {"kind": "corporate"}).keep
    with mock.patch.object(scan.pages.requests, "get", lambda *a, **k: R(text="<p>Chief of Staff</p><p>Based in Chicago</p>" * 5)):
        p = scan.located({"title": "Chief of Staff", "url": "https://acme.com/job/1"}, flt)
    assert not p.get("locations") and flt.evaluate(p, {"kind": "corporate"}).reason == "location"


def test_company_time_budget():
    import time as _t
    careers._LOCAL.deadline = _t.monotonic() - 1
    try:
        try:
            careers._get("https://slow.example")
            assert False, "should refuse once the budget is spent"
        except careers.OverBudget:
            pass
    finally:
        careers._LOCAL.deadline = None


def test_parse_industries():
    assert directory.parse_industries(["fintech", {"name": "watertech", "search": ["water treatment", "desalination"]}, "", None]) == \
        [("fintech", []), ("watertech", ["water treatment", "desalination"])]
