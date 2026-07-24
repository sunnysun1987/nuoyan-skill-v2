import pytest
from typer.testing import CliRunner

from ivd_research import doctor
from ivd_research.cli import app
from ivd_research.doctor import codex_plugin_check, run_doctor


def test_doctor_uses_registered_cli_name(tmp_path):
    result = run_doctor(tmp_path)
    messages = "\n".join(check["impact_zh"] for check in result["checks"])

    assert "ivd-research" not in messages
    assert "nuoyan 命令" in messages


def test_standard_profile_reports_complete_research_environment(tmp_path):
    result = run_doctor(tmp_path, profile="standard", codex_home=tmp_path / ".codex")
    check_ids = {check["id"] for check in result["checks"]}

    assert result["profile"] == "standard"
    assert result["core_ready"] is True
    assert result["standard_ready"] is False
    assert result["ok"] is False
    assert {
        "runtime_source",
        "distribution_conflict",
        "skill_install",
        "playwright_browser",
        "pdf_toolchain",
        "translation_engine",
        "life_science_plugin",
        "browser_plugin",
        "chrome_plugin",
        "network_preflight",
        "ocr_runtime",
    } <= check_ids


def test_standard_profile_requires_network_preflight(tmp_path):
    result = run_doctor(tmp_path, profile="standard", codex_home=tmp_path / ".codex")
    network_check = next(check for check in result["checks"] if check["id"] == "network_preflight")

    assert network_check["required"] is True
    assert network_check["ok"] is False
    assert "--network" in network_check["impact_zh"]


def test_plugin_check_distinguishes_cached_from_enabled(tmp_path):
    codex_home = tmp_path / ".codex"
    manifest = (
        codex_home
        / "plugins"
        / "cache"
        / "openai-api-curated"
        / "life-science-research"
        / "1.0.3"
        / ".codex-plugin"
        / "plugin.json"
    )
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}", encoding="utf-8")
    (codex_home / "config.toml").write_text("[plugins]\n", encoding="utf-8")

    check = codex_plugin_check(
        "life-science-research@openai-api-curated",
        "Life Science Research",
        codex_home=codex_home,
    )

    assert check["ok"] is False
    assert check["details"]["cached"] is True
    assert check["details"]["enabled"] is False
    assert "已缓存但未启用" in check["impact_zh"]


def test_plugin_check_requires_installed_cache_when_config_says_enabled(tmp_path):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        '[plugins."browser@openai-bundled"]\nenabled = true\n',
        encoding="utf-8",
    )

    check = codex_plugin_check(
        "browser@openai-bundled",
        "Browser",
        codex_home=codex_home,
    )

    assert check["details"]["enabled"] is True
    assert check["details"]["cached"] is False
    assert check["ok"] is False


def test_plugin_check_is_ready_when_enabled_and_cached(tmp_path):
    codex_home = tmp_path / ".codex"
    manifest = (
        codex_home
        / "plugins"
        / "cache"
        / "openai-bundled"
        / "browser"
        / "26.715.72359"
        / ".codex-plugin"
        / "plugin.json"
    )
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}", encoding="utf-8")
    (codex_home / "config.toml").write_text(
        '[plugins."browser@openai-bundled"]\nenabled = true\n',
        encoding="utf-8",
    )

    check = codex_plugin_check("browser@openai-bundled", "Browser", codex_home=codex_home)

    assert check["ok"] is True


def test_runtime_source_requires_exact_workflow_version(monkeypatch, tmp_path):
    expected_root = tmp_path / "skills" / "nuoyan-skill-v2"
    expected_root.mkdir(parents=True)
    monkeypatch.setattr(doctor.Path, "samefile", lambda self, other: True)
    monkeypatch.setattr(
        doctor,
        "_read_toml",
        lambda path: {"project": {"version": "2.1.1"}},
    )
    monkeypatch.setattr(doctor.metadata, "version", lambda name: "2.1.1")

    check = doctor._runtime_source_check(tmp_path)

    assert check["details"]["same_source"] is True
    assert check["details"]["distribution_version"] == "2.1.1"
    assert check["details"]["version_matches"] is False
    assert check["ok"] is False


def test_doctor_rejects_unknown_profile(tmp_path):
    with pytest.raises(ValueError, match="profile"):
        run_doctor(tmp_path, profile="unknown")


def test_doctor_strict_returns_nonzero_for_unready_environment(monkeypatch):
    monkeypatch.setattr(
        "ivd_research.cli.run_doctor",
        lambda *args, **kwargs: {"ok": False, "checks": []},
    )

    result = CliRunner().invoke(app, ["doctor", "--strict", "--json"])

    assert result.exit_code == 1
    assert '"ok": false' in result.stdout
