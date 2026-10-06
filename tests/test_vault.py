"""Vault mirror: repo -> notes, idempotent, read-only settings (no network, temp dirs)."""
import json

import yaml

from radar import vault


def setup(tmp_path):
    data, root = tmp_path / "data", tmp_path / "vault"
    for d in (data / "agent", data / "scan", data / "report", root / "Notes"):
        d.mkdir(parents=True)
    (data / "agent/config.yaml").write_text(yaml.safe_dump({"report_artifact": "https://claude.ai/artifact/x", "vault": {
        "roots": ["/does/not/exist", str(root)], "settings_note": "Notes/Settings.md", "report_note": "Notes/Report.md",
        "alerts_note": "Notes/Alerts.md", "remote_alerts_note": "Notes/Remote.md", "log_note": "Notes/Log.md"}}))
    (data / "settings.yaml").write_text("roles:\n  include: [chief of staff]\ncompany_search:\n  city: London\n")
    (data / "report/report.md").write_text("# Job Radar report\n")
    (data / "report/report.json").write_text(json.dumps({"watchlist_roles": [
        {"id": "a", "company": "Acme", "title": "Chief of Staff", "url": "https://x/a", "fit": "keep", "pool": "local", "note": "fits"},
        {"id": "b", "company": "Beta", "title": "PM", "url": "https://x/b", "fit": "stretch", "pool": "remote"}],
        "market_roles": [{"id": "c", "company": "Cut", "title": "X", "url": "https://x/c", "fit": "cut", "pool": "local"}]}))
    (data / "scan/snoopy_log.md").write_text("# Review log\n\n### 2026-10-06 11:05 · Snoopy\n- two\n\n### 2026-10-06 09:00 · Snoopy\n- one\n")
    (root / "Notes/Settings.md").write_text("# Settings\n\nEdit below.\n\n```yaml\nroles: {}\n```\n")
    (root / "Notes/Alerts.md").write_text("# Alerts\n\n<!-- ALERTS START -->\n\n| old | [x](https://x/a) |\n")
    (root / "Notes/Log.md").write_text("# Log\n\n<!-- LOG START -->\n### 2026-10-06 09:00 · Snoopy\n- one\n")
    return data, root


def test_mirror_once_and_idempotent(tmp_path):
    data, root = setup(tmp_path)
    assert vault.main(["--data", str(data)]) == 0
    s = (root / "Notes/Settings.md").read_text()
    assert "city: London" in s and "Read-only copy" in s
    assert (root / "Notes/Report.md").read_text().startswith("[Open the live report](https://claude.ai/artifact/x)")
    assert "https://x/a" in (root / "Notes/Alerts.md").read_text() and (root / "Notes/Alerts.md").read_text().count("https://x/a") == 1
    assert "https://x/b" in (root / "Notes/Remote.md").read_text()
    assert "https://x/c" not in (root / "Notes/Alerts.md").read_text()          # cut roles never mirrored
    log = (root / "Notes/Log.md").read_text()
    assert log.index("11:05") < log.index("09:00") and log.count("09:00 · Snoopy") == 1
    snapshot = {p.name: p.read_text() for p in (root / "Notes").iterdir()}
    vault.main(["--data", str(data)])
    again = {p.name: p.read_text() for p in (root / "Notes").iterdir()}
    assert again == snapshot                                                     # nothing doubles on a re-run
    assert json.loads((data / "scan/vault_sync.json").read_text())["mirrored"] == ["b"]


def test_unreachable_vault_is_a_quiet_skip(tmp_path, capsys):
    data, root = setup(tmp_path)
    cfg = yaml.safe_load((data / "agent/config.yaml").read_text()); cfg["vault"]["roots"] = ["/nope"]
    (data / "agent/config.yaml").write_text(yaml.safe_dump(cfg))
    assert vault.main(["--data", str(data)]) == 0 and "not reachable" in capsys.readouterr().out
