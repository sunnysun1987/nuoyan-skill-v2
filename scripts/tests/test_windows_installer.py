from pathlib import Path
import tomllib

from ivd_research.constants import WORKFLOW_VERSION


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "install-windows.ps1"


def test_windows_installer_uses_isolated_complete_runtime():
    script = INSTALLER.read_text(encoding="utf-8")

    assert ".codex\\skills\\nuoyan-skill-v2" in script
    assert ".venv\\Scripts\\python.exe" in script
    assert "[browser,pdf,translation]" in script
    assert '@("-m", "playwright", "install", "chromium")' in script
    assert '"setup-translation-engine", "--provider", "argos"' in script
    assert "--profile standard --network --strict --json" in script
    assert "pip install --user" not in script


def test_windows_installer_checks_prerequisites_and_supports_verify_only():
    script = INSTALLER.read_text(encoding="utf-8")

    assert "Git for Windows" in script
    assert "Python 3.11" in script
    assert "[switch]$VerifyOnly" in script
    assert "Life Science Research" in script


def test_windows_runtime_is_documented_for_agent_not_business_user():
    skill = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")
    guide = (REPO_ROOT / "docs" / "windows-standard-environment.md").read_text(
        encoding="utf-8"
    )

    assert ".venv\\Scripts\\nuoyan.exe" in skill
    assert "业务同事不执行命令行" in guide
    assert "doctor --profile standard --network --strict" in guide


def test_windows_environment_release_has_distinct_version():
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["version"] == "2.2.1"
    assert "v2.2.1" in WORKFLOW_VERSION
    assert "V2.2.1" in (REPO_ROOT / "README.md").read_text(encoding="utf-8")
