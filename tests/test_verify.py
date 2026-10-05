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
