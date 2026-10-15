from unittest import mock
from radar import verify


class R:
    def __init__(self, status, text, url):
        self.status_code, self.text, self.url = status, text, url


def test_check_url_cases():
    cases = [
        (R(404, "", "https://x.com/jobs/1"), False),
        (R(200, "<html><body><h1>This job is no longer available</h1> apply elsewhere</body></html>", "https://x.com/jobs/1"), False),
        (R(200, "<html><body><h1>Chief of Staff</h1><p>We are hiring. 5 years experience in 2024.</p></body></html>", "https://x.com/jobs/1"), True),
        (R(200, "<html>home</html>", "https://jobs.ashbyhq.com/acme"), False),   # redirected to board home
    ]
    for resp, expected in cases:
        with mock.patch.object(verify.requests, "get", lambda *a, **k: resp):
            live, reason = verify.check_url("https://x.com/jobs/1")
            assert live == expected, reason


def test_title_shown():
    from radar.verify import _title_shown
    assert _title_shown("Senior Founder's Associate", "<h1>Senior Founder's Associate</h1> at Kernel")
    assert not _title_shown("Senior Founder's Associate", "<div id=root></div>" + "lorem " * 200)
    assert _title_shown("", "anything")


def test_gone_strong_anywhere():
    from radar.verify import GONE_STRONG
    page = "nav " * 2000 + "Kernel AI Senior Founder's Associate. Job no longer available. Salary £65-80k"
    assert GONE_STRONG.search(page)
    assert not GONE_STRONG.search("We are hiring a Senior Associate. Apply now. Page not found in footer links")


def test_aggregator_copy_judged_by_its_original():
    gh = "https://boards.greenhouse.io/acme/jobs/123"
    pages = {"https://johngannonblog.com/vc-jobs/associate-acme/":
             R(200, f'<html><body><h1>VC Associate at Acme</h1><p>{"text " * 200}</p><a href="{gh}">apply here</a></body></html>',
               "https://johngannonblog.com/vc-jobs/associate-acme/")}
    with mock.patch.object(verify.requests, "get", lambda u, **k: pages[u]), \
         mock.patch.object(verify.ats, "fetch", lambda a, s: [{"url": "https://boards.greenhouse.io/acme/jobs/999", "id": 999}]):
        verify._FEED_CACHE.clear()
        live, why = verify.check_url("https://johngannonblog.com/vc-jobs/associate-acme/", "VC Associate")
    assert not live and why.startswith("original posting:")
    # an aggregator page with no original link can't prove anything
    pages2 = {"https://www.indeed.co.uk/viewjob?jk=1": R(200, "<h1>VC Associate</h1>" + "text " * 200, "https://www.indeed.co.uk/viewjob?jk=1")}
    with mock.patch.object(verify.requests, "get", lambda u, **k: pages2[u]):
        live, why = verify.check_url("https://www.indeed.co.uk/viewjob?jk=1", "VC Associate")
    assert live and why.startswith("unverified: third-party")
