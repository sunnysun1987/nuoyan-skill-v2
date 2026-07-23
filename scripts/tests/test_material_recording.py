import json
from pathlib import Path

from ivd_research.import_finding import import_finding
from ivd_research.jsonl import read_jsonl
from ivd_research.models import Material
from ivd_research.status import init_task, load_task, next_material_id, record_materials


def _material(material_id: str, source: str, key: str) -> Material:
    return Material(
        material_id=material_id,
        task_id="TASK-TEST",
        source_scenario=source,
        material_type="literature",
        title=f"Material {material_id}",
        collection_time="2026-07-16T00:00:00+08:00",
        possible_duplicate_keys=[key],
    )


def test_record_materials_deduplicates_within_source_only(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "materials.jsonl").touch()

    first = _material("MAT-000001", "pubmed_literature", "doi:10.1/test")
    duplicate = _material("MAT-000002", "pubmed_literature", "doi:10.1/test")
    second_source = _material("MAT-000003", "pmc_fulltext", "doi:10.1/test")

    assert record_materials(tmp_path, [first]) == [first]
    assert record_materials(tmp_path, [duplicate, second_source]) == [second_source]
    assert len(read_jsonl(data_dir / "materials.jsonl")) == 2


def test_next_material_id_uses_highest_existing_id_after_cleanup(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    path = data_dir / "materials.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(_material(value, "source", value).model_dump(mode="json"))
            for value in ["MAT-000002", "MAT-000010"]
        )
        + "\n",
        encoding="utf-8",
    )

    assert next_material_id(tmp_path) == "MAT-000011"


def test_next_material_id_counts_multi_result_suffix_ids(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    path = data_dir / "materials.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(_material(value, "source", value).model_dump(mode="json"))
            for value in ["MAT-000010-001", "MAT-000010-002"]
        )
        + "\n",
        encoding="utf-8",
    )

    assert next_material_id(tmp_path) == "MAT-000011"


def test_import_finding_preserves_task_id_and_skips_repeat(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "materials.jsonl").touch()
    (tmp_path / "task.json").write_text(
        json.dumps({"task_id": "TASK-REAL"}),
        encoding="utf-8",
    )

    first = import_finding(
        tmp_path,
        title="Official product",
        source="nmpa_competitor",
        source_url="https://example.test/search",
        content="Official product content",
        material_type="competitor",
        identifier="CERT-001",
    )
    repeated = import_finding(
        tmp_path,
        title="Official product",
        source="nmpa_competitor",
        source_url="https://example.test/search",
        content="Official product content",
        material_type="competitor",
        identifier="CERT-001",
    )

    materials = read_jsonl(data_dir / "materials.jsonl")
    assert first["recorded"] is True
    assert repeated["recorded"] is False
    assert repeated["material_id"] == first["material_id"]
    assert len(materials) == 1
    assert materials[0]["task_id"] == "TASK-REAL"


def test_generic_import_finding_cannot_close_nmpa_scenario(tmp_path):
    state = init_task("CRP 定量检测试剂盒", tmp_path)
    task_dir = Path(state.task_dir)

    result = import_finding(
        task_dir,
        title="NMPA 人工线索",
        source="nmpa_competitor",
        source_url=(
            "https://www.nmpa.gov.cn/datasearch/home-index.html#category=ylqx"
        ),
        content="国械注准20261234567 C反应蛋白测定试剂盒",
        material_type="competitor",
        identifier="国械注准20261234567",
    )

    scenario = load_task(task_dir).scenario_statuses["nmpa_competitor"]
    assert result["recorded"] is True
    assert scenario.material_count == 1
    assert scenario.status == "needs_manual_review"
    assert "专用人工导入" in scenario.last_message
