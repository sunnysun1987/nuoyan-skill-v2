from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

from ivd_research.reports import asset_root


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_ASSET_ROOT = REPO_ROOT / "assets"
EXPECTED_REPORT_ASSETS = {
    "styles/report.css",
    "templates/evidence-card.md",
    "templates/feasibility-report.html",
    "templates/materials-report.html",
    "templates/review-workbook-layout.md",
    "templates/standard-delivery-report.html",
}


def test_report_runtime_uses_package_local_assets():
    assert asset_root() == REPO_ROOT / "scripts" / "ivd_research" / "assets"
    assert {
        path.relative_to(asset_root()).as_posix()
        for path in asset_root().rglob("*")
        if path.is_file()
    } == EXPECTED_REPORT_ASSETS


def test_skill_and_package_report_assets_are_identical():
    for relative_path in EXPECTED_REPORT_ASSETS:
        assert (asset_root() / relative_path).read_bytes() == (
            SKILL_ASSET_ROOT / relative_path
        ).read_bytes()


def test_built_wheel_contains_report_assets(tmp_path: Path):
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(tmp_path),
            str(REPO_ROOT),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    wheels = list(tmp_path.glob("nuoyan_skill_v2-*.whl"))
    assert len(wheels) == 1

    with ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
    assert {
        f"ivd_research/assets/{relative_path}"
        for relative_path in EXPECTED_REPORT_ASSETS
    } <= names

