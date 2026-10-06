from unittest import mock
from sources import portfolio as pf


def test_getro_mapping():
    page = {"results": {"companies": [{"name": "9fin", "domain": "9fin.com", "slug": "9fin", "locations": ["London, UK"],
                                       "stage": "series_c", "visible_industry_tags": ["Fintech"], "active_jobs_count": 3}]}}
    with mock.patch.object(pf, "_getro_post", lambda nid, what, body, base: page):
        out = pf.getro_companies("4186", "https://talent.x.com")
    assert out[0]["domain"] == "9fin.com" and out[0]["open_jobs"] == 3


def test_consider_mapping():
    page = {"jobs": [{"id": 1, "title": "Chief of Staff", "companyName": "Lawhive", "companySlug": "lawhive",
                      "locations": ["London, UK"], "applyUrl": "https://jobs.ashbyhq.com/lawhive/abc"}]}
    page["total"] = 1
    with mock.patch.object(pf, "_consider_post", lambda info, what, page_, size=100: page):
        out = pf.consider_jobs({"base": "https://careers.b.com", "board": {"id": "b"}})
    assert out[0]["company"] == "Lawhive" and out[0]["url"].startswith("https://jobs.ashbyhq.com")


def test_consider_company_offices():
    from unittest import mock
    from sources import portfolio
    data = {"total": 1, "companies": [{"id": "s1", "name": "Stripe", "domain": "stripe.com", "slug": "stripe",
                                       "officeLocations": ["Dublin, Ireland", "London"], "stages": ["Growth"], "markets": ["Fintech"]}]}
    with mock.patch.object(portfolio, "_consider_post", lambda info, what, page, size=100: data):
        out = portfolio.consider_companies({"base": "https://jobs.vc.com"})
    assert out[0]["locations"] == ["Dublin, Ireland", "London"] and out[0]["stage"] == "Growth"
