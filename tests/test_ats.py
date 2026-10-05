"""Adapter parsing tests with recorded-shape fixtures (no network)."""
from unittest import mock

from radar import ats


def fake_get(payloads):
    def _get(url, params=None):
        for frag, data in payloads.items():
            if frag in url:
                if data == 404:
                    raise ats.NotFound(url)
                return data
        raise AssertionError(url)
    return _get


def test_ashby():
    data = {"jobs": [{"id": "a1", "title": "Deployment Strategist", "location": "London",
                      "secondaryLocations": [{"location": "Remote - UK"}], "isRemote": False,
                      "workplaceType": "Hybrid", "jobUrl": "https://jobs.ashbyhq.com/x/a1",
                      "publishedAt": "2026-10-01T09:00:00Z", "department": "GTM",
                      "descriptionPlain": "Hello", "isListed": True,
                      "compensation": {"compensationTierSummary": "£80K – £100K"}},
                     {"id": "a2", "title": "Hidden", "isListed": False}]}
    with mock.patch.object(ats, "_get", fake_get({"ashbyhq": data})):
        out = ats.fetch("ashby", "x")
    assert len(out) == 1
    p = out[0]
    assert p["locations"] == ["London", "Remote - UK"] and p["workplace"] == "hybrid"
    assert p["salary"] == "£80K – £100K" and p["url"].endswith("/a1")


def test_greenhouse_eu_fallback():
    data = {"jobs": [{"id": 7, "title": "Chief of Staff", "location": {"name": "London"},
                      "absolute_url": "https://job-boards.eu.greenhouse.io/x/jobs/7",
                      "content": "&lt;p&gt;Great &amp;amp; role&lt;/p&gt;", "departments": [{"name": "Ops"}],
                      "updated_at": "2026-10-02"}]}
    with mock.patch.object(ats, "_get", fake_get({"boards-api.greenhouse.io": data})):
        out = ats.fetch("greenhouse", "x")
    assert out[0]["description"] == "Great & role"
    assert out[0]["department"] == "Ops"


def test_lever_eu_and_remote():
    data = [{"id": "l1", "text": "Solutions Engineer", "categories": {"location": "Berlin", "team": "Sales",
             "allLocations": ["Berlin", "Remote - Germany"]}, "workplaceType": "remote",
             "hostedUrl": "https://jobs.eu.lever.co/x/l1", "createdAt": 1759300000000,
             "descriptionPlain": "Intro", "lists": [{"text": "Requirements", "content": "<li>3 years</li>"}]}]
    with mock.patch.object(ats, "_get", fake_get({"api.lever.co": [], "api.eu.lever.co": data})):
        out = ats.fetch("lever", "x")
    p = out[0]
    assert p["remote"] and p["locations"] == ["Berlin", "Remote - Germany"]
    assert "3 years" in p["description"] and p["published"].startswith("2025")


def test_workable():
    data = {"name": "X", "jobs": [{"title": "Investment Associate", "shortcode": "ABC", "country": "United Kingdom",
                                    "city": "London", "telecommuting": False, "url": "https://apply.workable.com/j/ABC",
                                    "description": "<p>Join</p>", "published_on": "2026-10-01"}]}
    with mock.patch.object(ats, "_get", fake_get({"workable": data})):
        out = ats.fetch("workable", "x")
    assert out[0]["locations"] == ["London, United Kingdom"] and out[0]["description"] == "Join"


def test_breezy():
    data = [{"id": "b1", "name": "Investment Associate", "url": "https://bgf.breezy.hr/p/b1",
             "location": {"name": "London, UK", "is_remote": False}, "published_date": "2026-10-01"}]
    with mock.patch.object(ats, "_get", fake_get({"breezy.hr": data})):
        out = ats.fetch("breezy", "bgf")
    assert out[0]["title"] == "Investment Associate" and out[0]["locations"] == ["London, UK"]


def test_recruitee():
    data = {"offers": [{"id": 5, "title": "Chief of Staff", "location": "London", "remote": False, "hybrid": True,
                        "careers_url": "https://x.recruitee.com/o/cos", "description": "<p>A</p>",
                        "requirements": "<p>B</p>", "published_at": "2026-10-01"}]}
    with mock.patch.object(ats, "_get", fake_get({"recruitee.com": data})):
        out = ats.fetch("recruitee", "x")
    assert out[0]["workplace"] == "hybrid" and "A" in out[0]["description"] and "B" in out[0]["description"]


def test_personio():
    xml = """<workzag-jobs><position><id>42</id><office>Berlin</office><name>Solutions Engineer</name>
    <department>Sales</department><createdAt>2026-10-01T10:00:00+00:00</createdAt>
    <jobDescriptions><jobDescription><name>Tasks</name><value>&lt;p&gt;Do things&lt;/p&gt;</value></jobDescription>
    </jobDescriptions></position></workzag-jobs>"""
    with mock.patch.object(ats, "_get_text", lambda url, params=None: xml):
        out = ats.fetch("personio", "x")
    assert out[0]["url"] == "https://x.jobs.personio.de/job/42" and "Do things" in out[0]["description"]


def test_smartrecruiters():
    data = {"totalFound": 1, "content": [{"id": "77", "name": "Partnerships Manager", "releasedDate": "2026-10-01",
             "location": {"city": "London", "country": "gb", "remote": False}, "department": {"label": "BD"}}]}
    with mock.patch.object(ats, "_get", fake_get({"smartrecruiters": data})):
        out = ats.fetch("smartrecruiters", "x")
    assert out[0]["url"] == "https://jobs.smartrecruiters.com/x/77" and out[0]["locations"] == ["London, gb"]


def test_plausible():
    assert ats.plausible("Cleo", "cleo-2")
    assert ats.plausible("Frontline Ventures", "frontlinevc", "https://frontline.vc/careers/")
    assert not ats.plausible("Molten Ventures", "iceye", "https://www.moltenventures.com/opportunities")
    assert not ats.plausible("Octopus Ventures", "oneclick-ui")


def test_bamboohr():
    data = {"result": [{"id": 51, "jobOpeningName": "Investment Associate", "departmentLabel": "Investments",
                        "location": {"city": "London", "state": None}, "isRemote": None}]}
    with mock.patch.object(ats, "_get", fake_get({"bamboohr.com": data})):
        out = ats.fetch("bamboohr", "illuminatefinancial")
    assert out[0]["url"] == "https://illuminatefinancial.bamboohr.com/careers/51" and out[0]["locations"] == ["London"]
