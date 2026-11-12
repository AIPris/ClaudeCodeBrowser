"""scripts/update_addon.py helpers (the Firefox/Mozilla steps are verified live)."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def update_addon():
    spec = importlib.util.spec_from_file_location("update_addon", ROOT / "scripts" / "update_addon.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bump_patch_changes_only_the_version(update_addon):
    text = '{\n  "manifest_version": 2,\n  "version": "1.4.9",\n  "name": "X"\n}\n'
    new_text, version = update_addon.bump_patch(text)
    assert version == "1.4.10"
    assert new_text == text.replace("1.4.9", "1.4.10")


def test_bump_patch_needs_a_semver_version(update_addon):
    with pytest.raises(ValueError):
        update_addon.bump_patch('{"version": "1.4"}')


def test_installed_versions_reads_every_profile(update_addon, tmp_path):
    for profile, version in (("a.default", "1.4.3"), ("b.dev", "1.4.5")):
        (tmp_path / profile).mkdir()
        (tmp_path / profile / "extensions.json").write_text(json.dumps({"addons": [
            {"id": "other@x", "version": "9.9"}, {"id": "ccb@me", "version": version}]}))
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "extensions.json").write_text("{not json")

    found = update_addon.installed_versions("ccb@me", str(tmp_path / "*" / "extensions.json"))

    assert sorted(found) == ["1.4.3", "1.4.5"]


def test_version_order_is_numeric(update_addon):
    assert update_addon.version_tuple("1.4.10") > update_addon.version_tuple("1.4.9")


def test_components_match_the_installer(update_addon):
    installer = (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")
    for rel in update_addon.COMPONENTS:
        assert Path(rel).name in installer, f"{rel} is not installed by install.ps1"
        assert (ROOT / rel).is_file()
