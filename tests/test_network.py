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
    assert "keywords=Checkout.com" in net.second_degree_url("Checkout.com") and "network=%5B%22S%22%5D" in net.second_degree_url("x")


def test_no_network_is_falsy(tmp_path):
    assert not network.Network(tmp_path)
