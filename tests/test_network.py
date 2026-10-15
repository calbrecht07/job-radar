"""LinkedIn network: import drops emails, exact normalised company matching, second-degree link."""
import csv
import zipfile

from radar import network

EXPORT = ('Notes:\n"When exporting your connection data, you may notice..."\n\n'
          "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
          "Ada,Lovelace,https://www.linkedin.com/in/ada,ada@x.com,Octopus Energy Group Ltd,Product Lead,01 Jan 2024\n"
          "Bo,Chen,https://www.linkedin.com/in/bo,,Bolt Threads,Engineer,02 Jan 2024\n"
          "Cy,Ng,https://www.linkedin.com/in/cy,,Checkout.com,Solutions Engineer,03 Jan 2024\n"
          ",,,,,,\n")


def test_import_zip_and_match(tmp_path):
    z = tmp_path / "Complete_LinkedInDataExport_04-15-2026.zip.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("Connections.csv", EXPORT)
    assert network.import_export(z, tmp_path) == 3
    rows = list(csv.DictReader((tmp_path / "network/connections.csv").open()))
    assert "email" not in ",".join(rows[0].keys()).lower() and rows[0]["name"] == "Ada Lovelace"
    net = network.Network(tmp_path)
    assert net.meta["exported"] == "2026-04-15"
    assert [p["name"] for p in net.at("Octopus Energy")] == ["Ada Lovelace"]       # suffixes ignored
    assert [p["name"] for p in net.at("Checkout.com")] == ["Cy Ng"]
    assert net.at("Bolt") == []                                                    # exact only: not Bolt Threads
    assert net.people_url("factory-hq") == "https://www.linkedin.com/company/factory-hq/people/" and net.people_url("") == ""


def test_no_network_is_falsy(tmp_path):
    assert not network.Network(tmp_path)


def test_linkedin_page_from_website(tmp_path):
    from unittest import mock
    html = '<footer><a href="https://www.linkedin.com/company/factory-hq/">LinkedIn</a> <a href="https://linkedin.com/company/factory-hq">x</a>' \
           '<a href="https://www.linkedin.com/company/some-partner">partner</a></footer>'
    assert network.linkedin_page(html) == "factory-hq" and network.linkedin_page("<p>none</p>") == ""
    (tmp_path / "pool").mkdir()
    (tmp_path / "pool/directory.csv").write_text("key,name,website,careers_url\nfactory.ai,Factory,https://factory.ai,\nquiet.co,Quiet,https://quiet.co,\n")

    class R:
        def __init__(self, text): self.status_code, self.text = 200, text
    with mock.patch("requests.get", lambda u, **k: R(html if "factory" in u else "<p>no links</p>")):
        got = network.resolve_pages(tmp_path, ["Factory", "Quiet", "Unknown Co"])
    assert got == {"Factory": "factory-hq", "Quiet": "", "Unknown Co": ""}
    with mock.patch("requests.get", side_effect=AssertionError("cached: no lookups")):
        assert network.resolve_pages(tmp_path, ["Factory", "Quiet"])["Factory"] == "factory-hq"
