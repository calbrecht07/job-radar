import yaml
from pathlib import Path

from radar.filters import Filters

F = Filters(yaml.safe_load((Path(__file__).parent.parent / "config.example" / "settings.yaml").read_text()))
S = {"kind": "startup", "pools": "uk"}
VC = {"kind": "vc", "pools": "uk"}
R = {"kind": "startup", "pools": "remote"}


def post(title, locs, remote=None, desc="", workplace=""):
    return {"title": title, "locations": locs, "remote": remote, "workplace": workplace, "description": desc}


def test_local_keep():
    v = F.evaluate(post("Deployment Strategist", ["London"]), S)
    assert v.keep and v.pool == "local" and v.flags == ["no description in feed: open the link"]


def test_region_wide_flagged():
    v = F.evaluate(post("Chief of Staff", ["Cambridge, United Kingdom"], desc="x"), S)
    assert v.keep and "check the city" in v.flags[0]


def test_work_mode_setting():
    import copy
    cfg = yaml.safe_load((Path(__file__).parent.parent / "config.example" / "settings.yaml").read_text())
    cfg["work_modes"] = ["remote", "hybrid"]
    f = Filters(cfg)
    assert not f.evaluate(post("Chief of Staff", ["London"], workplace="onsite", desc="x"), S).keep
    assert f.evaluate(post("Chief of Staff", ["London"], workplace="hybrid", desc="x"), S).keep


def test_title_excluded():
    assert not F.evaluate(post("Senior Software Engineer", ["London"]), S).keep
    assert not F.evaluate(post("Director of Solutions Engineering", ["London"]), S).keep
    assert not F.evaluate(post("Strategy Intern", ["London"]), S).keep


def test_vc_only_titles():
    assert F.evaluate(post("Investment Associate", ["London, UK"]), VC).keep
    assert not F.evaluate(post("Associate", ["London"]), S).keep


def test_london_remote_is_remote_pool():
    v = F.evaluate(post("Chief of Staff", ["Remote - UK"], remote=True, desc="x"), R)
    assert v.keep and v.pool == "remote"


def test_remote_europe():
    v = F.evaluate(post("Solutions Engineer", ["Remote - Europe"], remote=True, desc="x"), R)
    assert v.keep and v.pool == "remote" and not v.flags


def test_remote_germany():
    v = F.evaluate(post("Solutions Consultant (DACH)", ["Berlin, Germany"], remote=True), R)
    assert v.keep and v.pool == "remote" and any("residency" in f for f in v.flags)


def test_remote_other_regions():
    assert not F.evaluate(post("Field Engineer - South Korea", ["South Korea"], remote=True), R).keep
    assert not F.evaluate(post("Solutions Engineer", ["Remote - Brazil"], remote=True), R).keep
    v = F.evaluate(post("Product Manager", ["Barcelona; Spain"], remote=True), R)
    assert v.keep and "residency" in v.flags[0]


def test_french_internship():
    assert not F.evaluate(post("Stage - Sales Strategy Analyst (x/f/m)", ["Paris"], remote=True), R).keep


def test_remote_us_only_dropped():
    v = F.evaluate(post("Forward Deployed Strategist", ["Remote - United States"], remote=True), R)
    assert not v.keep


def test_remote_unspecified_flagged():
    v = F.evaluate(post("Chief of Staff", ["Remote"], remote=True), R)
    assert v.keep and any("unspecified" in f for f in v.flags)


def test_onsite_non_uk_dropped():
    assert not F.evaluate(post("Product Manager", ["Paris, France"]), R).keep


def test_desc_drops():
    assert not F.evaluate(post("Chief of Staff", ["London"], desc="You have 10+ years of experience"), S).keep
    assert not F.evaluate(post("Chief of Staff", ["London"], desc="We are unable to sponsor visas"), S).keep
    # sponsorship rule is UK-only
    assert F.evaluate(post("Chief of Staff", ["Remote - EMEA"], remote=True, desc="We are unable to sponsor visas"), R).keep
    assert not F.evaluate(post("Solutions Engineer", ["Remote"], remote=True,
                                desc="Must be authorized to work in the United States"), R).keep


def test_flags():
    v = F.evaluate(post("Solutions Engineer", ["Remote - EMEA"], remote=True,
                        desc="Hired via Deel as a contractor. 5+ years experience. Native French speaker."), R)
    assert v.keep
    assert {"EOR / contractor setup", "5-7 years asked", "language requirement"} <= set(v.flags)


def test_staff_titles():
    assert not F.evaluate(post("Staff Solutions Engineer", ["London"]), S).keep
    assert F.evaluate(post("Chief of Staff, CEO Office", ["London"]), S).keep


def test_too_old():
    from radar.scan import too_old
    assert too_old("2025-01-01T00:00:00Z", "2026-09-14T00:00:00+00:00")
    assert not too_old("2026-10-01", "2026-09-14T00:00:00+00:00")
    assert not too_old(None, "2026-09-14T00:00:00+00:00")
    assert not too_old("garbage", "2026-09-14T00:00:00+00:00")
