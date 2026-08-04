import importlib.util
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ivd_research import research_integrity
from ivd_research.cli import app
from ivd_research.jsonl import append_jsonl
from ivd_research.package import build_standard_delivery, verify_package
from ivd_research.review_excel import export_review
from ivd_research.status import init_task, load_task, save_task


def test_research_integrity_module_exists():
    assert importlib.util.find_spec("ivd_research.research_integrity") is not None


def test_research_integrity_models_are_exposed():
    for name in (
        "ResearchClaim",
        "EvidenceConflict",
        "ResearchIteration",
        "ResearchPolicy",
    ):
        assert hasattr(research_integrity, name), name


def test_research_integrity_behavior_api_is_exposed():
    for name in (
        "record_research_claim",
        "record_evidence_conflict",
        "record_research_iteration",
        "build_research_integrity_audit",
        "validate_retrieval_target",
    ):
        assert hasattr(research_integrity, name), name


def _new_task(tmp_path: Path) -> Path:
    return Path(init_task("研究完整性测试", tmp_path).task_dir)


def test_new_task_initializes_research_integrity_contract(tmp_path: Path):
    state = init_task("研究完整性初始化", tmp_path)
    task_dir = Path(state.task_dir)

    assert state.research_policy["required"] is True
    assert state.research_policy["data_classification"] == "public"
    assert (task_dir / "data" / "research_claims.jsonl").exists()
    assert (task_dir / "data" / "evidence_conflicts.jsonl").exists()
    assert (task_dir / "logs" / "research_iterations.jsonl").exists()


def test_verify_package_exposes_research_integrity_gate(tmp_path: Path):
    task_dir = _new_task(tmp_path)

    result = verify_package(task_dir)

    assert result["research_integrity_required"] is True
    assert result["research_integrity_ready"] is False
    assert result["research_integrity"]["status"] == "needs_action"


def _write_material_and_card(
    task_dir: Path,
    *,
    material_id: str,
    card_id: str,
    publisher: str,
    retrieval_kind: str = "fetched_page",
    content_verified: bool = True,
) -> None:
    append_jsonl(
        task_dir / "data" / "materials.jsonl",
        {
            "material_id": material_id,
            "source_scenario": "pubmed_literature",
            "source_url": f"https://example.org/{material_id}",
            "source_name": "PubMed",
            "raw_fields": {
                "publisher": publisher,
                "retrieval_kind": retrieval_kind,
                "content_verified": content_verified,
            },
        },
    )
    append_jsonl(
        task_dir / "data" / "evidence_cards.jsonl",
        {
            "evidence_card_id": card_id,
            "material_id": material_id,
            "needs_review": False,
        },
    )


def _record_saturation(task_dir: Path) -> None:
    research_integrity.record_research_iteration(
        task_dir,
        {
            "iteration_id": "ITER-001",
            "direction": "反向证据审计",
            "pivot_axis": "claim_stance",
            "quality_targets_met": True,
        },
    )
    research_integrity.record_research_iteration(
        task_dir,
        {
            "iteration_id": "ITER-002",
            "direction": "覆盖缺口审计",
            "pivot_axis": "coverage_gap",
            "quality_targets_met": True,
        },
    )


def test_search_result_cannot_support_claim(tmp_path: Path):
    task_dir = _new_task(tmp_path)
    _write_material_and_card(
        task_dir,
        material_id="MAT-000001",
        card_id="EC-000001",
        publisher="Journal A",
        retrieval_kind="search_result",
        content_verified=False,
    )
    research_integrity.record_research_claim(
        task_dir,
        {
            "claim_id": "CLM-000001",
            "text": "该标志物具有诊断价值。",
            "status": "supported",
            "evidence_card_ids": ["EC-000001"],
            "needs_human_review": False,
        },
    )
    _record_saturation(task_dir)

    audit = research_integrity.build_research_integrity_audit(task_dir)

    assert audit["ready"] is False
    assert "unverified_search_result" in {item["issue_type"] for item in audit["issues"]}


def test_high_impact_claim_requires_independent_publishers(tmp_path: Path):
    task_dir = _new_task(tmp_path)
    _write_material_and_card(
        task_dir,
        material_id="MAT-000001",
        card_id="EC-000001",
        publisher="Journal A",
    )
    _write_material_and_card(
        task_dir,
        material_id="MAT-000002",
        card_id="EC-000002",
        publisher="Journal A",
    )
    research_integrity.record_research_claim(
        task_dir,
        {
            "claim_id": "CLM-000001",
            "text": "建议进入产品开发阶段。",
            "claim_type": "recommendation",
            "status": "supported",
            "evidence_card_ids": ["EC-000001", "EC-000002"],
            "confidence": "high",
            "impact": "high",
            "inference": True,
            "needs_human_review": False,
        },
    )
    _record_saturation(task_dir)

    audit = research_integrity.build_research_integrity_audit(task_dir)

    assert audit["independent_publisher_count"] == 1
    assert "insufficient_independent_publishers" in {
        item["issue_type"] for item in audit["issues"]
    }


def test_disputed_claim_requires_conflict_record(tmp_path: Path):
    task_dir = _new_task(tmp_path)
    _write_material_and_card(
        task_dir,
        material_id="MAT-000001",
        card_id="EC-000001",
        publisher="Journal A",
    )
    research_integrity.record_research_claim(
        task_dir,
        {
            "claim_id": "CLM-000001",
            "text": "不同研究对 cut-off 的结论不一致。",
            "status": "disputed",
            "evidence_card_ids": ["EC-000001"],
            "needs_human_review": False,
        },
    )
    _record_saturation(task_dir)

    audit = research_integrity.build_research_integrity_audit(task_dir)

    assert "missing_conflict_record" in {item["issue_type"] for item in audit["issues"]}


def test_reviewed_claim_requires_reviewer_role_and_timestamp(tmp_path: Path):
    task_dir = _new_task(tmp_path)
    _write_material_and_card(
        task_dir,
        material_id="MAT-000001",
        card_id="EC-000001",
        publisher="Journal A",
    )
    research_integrity.record_research_claim(
        task_dir,
        {
            "claim_id": "CLM-000001",
            "text": "该论断被直接标成已复核，但没有复核轨迹。",
            "status": "supported",
            "evidence_card_ids": ["EC-000001"],
            "needs_human_review": False,
        },
    )
    _record_saturation(task_dir)

    audit = research_integrity.build_research_integrity_audit(task_dir)

    assert "claim_review_trace_missing" in {
        item["issue_type"] for item in audit["issues"]
    }


def test_two_stale_rounds_require_structural_pivot(tmp_path: Path):
    task_dir = _new_task(tmp_path)
    for index, axis in enumerate(("source_lane", "language"), start=1):
        research_integrity.record_research_iteration(
            task_dir,
            {
                "iteration_id": f"ITER-{index:03d}",
                "direction": f"低产出方向 {index}",
                "pivot_axis": axis,
                "quality_targets_met": False,
            },
        )

    state = research_integrity.research_iteration_state(task_dir)

    assert state["stale_count"] == 2
    assert state["pivot_required"] is True
    assert state["ready_for_validation"] is False


def test_two_distinct_zero_yield_audits_reach_saturation(tmp_path: Path):
    task_dir = _new_task(tmp_path)
    _record_saturation(task_dir)

    state = research_integrity.research_iteration_state(task_dir)

    assert state["saturation_count"] == 2
    assert state["ready_for_validation"] is True


def test_repeated_research_direction_is_rejected(tmp_path: Path):
    task_dir = _new_task(tmp_path)
    research_integrity.record_research_iteration(
        task_dir,
        {
            "iteration_id": "ITER-001",
            "direction": "Official Sources",
            "pivot_axis": "source_lane",
        },
    )

    with pytest.raises(ValueError, match="研究方向已记录"):
        research_integrity.record_research_iteration(
            task_dir,
            {
                "iteration_id": "ITER-002",
                "direction": " official   sources ",
                "pivot_axis": "language",
            },
        )


def test_internal_target_is_blocked_from_public_provider():
    with pytest.raises(ValueError, match="内部或机密数据不得发送到公共采集服务"):
        research_integrity.validate_retrieval_target(
            "http://127.0.0.1:8080/private",
            {
                "data_classification": "internal",
                "external_provider_allowed": False,
                "internal_access_approved": True,
                "approved_internal_route": "company-search",
            },
            external_provider=True,
        )


def test_public_target_rejects_secret_bearing_url():
    with pytest.raises(ValueError, match="凭据或签名参数"):
        research_integrity.validate_retrieval_target(
            "https://example.org/report?access_token=secret",
            {"data_classification": "public"},
        )


def test_high_impact_publisher_floor_cannot_be_lowered_below_two():
    with pytest.raises(ValueError):
        research_integrity.ResearchPolicy(high_impact_min_publishers=1)


def test_research_integrity_cli_records_claim_and_updates_policy(tmp_path: Path):
    state = init_task("研究完整性 CLI", tmp_path)
    claim_path = tmp_path / "claim.json"
    claim_path.write_text(
        """{
          "claim_id": "CLM-CLI-001",
          "text": "CLI 可以登记研究论断。",
          "status": "unsupported",
          "needs_human_review": true
        }""",
        encoding="utf-8",
    )
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(
        """{
          "required": true,
          "data_classification": "internal",
          "external_provider_allowed": false,
          "internal_access_approved": true,
          "approved_internal_route": "company-search"
        }""",
        encoding="utf-8",
    )
    runner = CliRunner()
    common = ["--task-id", state.task_id, "--output-root", str(tmp_path), "--json"]

    claim_result = runner.invoke(
        app,
        ["record-research-claim", "--claim", str(claim_path), *common],
    )
    policy_result = runner.invoke(
        app,
        ["set-research-policy", "--policy", str(policy_path), *common],
    )
    audit_result = runner.invoke(app, ["research-integrity", *common])

    assert claim_result.exit_code == 0, claim_result.output
    assert policy_result.exit_code == 0, policy_result.output
    assert audit_result.exit_code == 0, audit_result.output
    assert "CLM-CLI-001" in (
        Path(state.task_dir) / "data" / "research_claims.jsonl"
    ).read_text(encoding="utf-8")
    assert load_task(Path(state.task_dir)).research_policy["data_classification"] == "internal"


def test_internal_policy_blocks_public_collection_pipeline(tmp_path: Path):
    state = init_task("内部任务公网采集门禁", tmp_path)
    state.research_policy = {
        "required": True,
        "data_classification": "internal",
        "external_provider_allowed": False,
        "internal_access_approved": True,
        "approved_internal_route": "company-search",
    }
    save_task(state)

    result = CliRunner().invoke(
        app,
        [
            "run-full-pipeline",
            "--task-id",
            state.task_id,
            "--output-root",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert "data_boundary_blocked" in result.output


def test_standard_delivery_surfaces_research_integrity_audit(tmp_path: Path):
    task_dir = _new_task(tmp_path)
    export_review(task_dir)

    delivery = build_standard_delivery(task_dir)

    html = Path(delivery["delivery_dir"], "00_立项调研综合报告.html").read_text(
        encoding="utf-8"
    )
    workbook = __import__("openpyxl").load_workbook(
        Path(delivery["delivery_dir"], "01_证据审阅与补证任务表.xlsx")
    )
    trace_audit = Path(
        delivery["delivery_dir"],
        "90_系统追溯数据",
        "05_运行日志_logs",
        "research_integrity_audit.json",
    )

    assert "研究论证完整性" in html
    assert "尚未登记可追溯的研究论断" in html
    assert "论断与冲突" in workbook.sheetnames
    assert trace_audit.exists()
