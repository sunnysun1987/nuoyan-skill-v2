from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def test_ci_enforces_release_and_runtime_health_gates():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert '      - "v*"' in workflow
    assert "run: nuoyan doctor --strict --json" in workflow
    assert "run: python -m compileall -q scripts" in workflow
    assert "run: git diff --check" in workflow

