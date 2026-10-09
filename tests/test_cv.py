"""CV builder on the fictional example bank: check, selection by relevance, plans can't add facts."""
import shutil
from pathlib import Path

from radar import cv

EX = Path(__file__).parent.parent / "config.example/experience.yaml"


def bank_dir(tmp_path):
    (tmp_path / "profile").mkdir()
    shutil.copy(EX, tmp_path / "profile/experience.yaml")
    return tmp_path


def test_example_bank_is_valid(tmp_path):
    assert cv.check(cv.load_bank(bank_dir(tmp_path))) == []


def test_check_flags_missing_star_parts():
    bank = {"person": {"name": "X"}, "roles": [{"company": "C", "title": "T", "start": "2024-1",
                                                "achievements": [{"id": "a", "action": "did it"}]}]}
    issues = cv.check(bank)
    assert any("missing situation, task, result" in i for i in issues) and any("YYYY-MM" in i for i in issues)


def test_selection_follows_the_job(tmp_path):
    bank = cv.load_bank(bank_dir(tmp_path))
    product = cv.select(bank, "Product Manager: user research, onboarding funnels, SQL, fintech payments", per_role=(1, 1))
    ops = cv.select(bank, "Market expansion lead: launch new cities, hiring, carrier negotiation", per_role=(1, 1))
    assert product["summary"].startswith("Product-minded") and ops["summary"].startswith("Operator")
    assert "Manchester" in ops["roles"][0]["bullets"][0] and "routing" not in ops["roles"][0]["bullets"][0]
    assert "KYC" in product["roles"][1]["bullets"][0]


def test_plan_picks_and_shortens_but_html_has_only_bank_content(tmp_path):
    d = bank_dir(tmp_path)
    bank = cv.load_bank(d)
    sel = cv.select(bank, "", {"summary": "ops", "achievements": ["nw-dispatch"],
                               "rephrase": {"nw-dispatch": "Cut missed delivery windows from 12% to 3%."}})
    assert sel["roles"][0]["bullets"] == ["Cut missed delivery windows from 12% to 3%."] and sel["roles"][1]["bullets"] == []
    page = cv.render(bank, sel)
    assert "Alex Morgan" in page and "Cut missed delivery windows" in page and "<script" not in page


def test_cli_writes_html(tmp_path):
    d = bank_dir(tmp_path)
    (d / "job.txt").write_text("Operations lead, process design, SQL")
    assert cv.main(["--data", str(d), "--job", str(d / "job.txt"), "--name", "ops", "--no-pdf"]) == 0
    assert (d / "cvs/ops.html").read_text().count("<li>") >= 3
