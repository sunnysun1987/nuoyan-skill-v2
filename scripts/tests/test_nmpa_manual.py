from pathlib import Path

import pytest
from typer.testing import CliRunner

from ivd_research import cli as cli_module
from ivd_research.cli import app
from ivd_research.confirmations import update_confirmations
from ivd_research.import_finding import import_finding
from ivd_research.jsonl import read_json, read_jsonl, write_json
from ivd_research.nmpa_manual import (
    collect,
    import_nmpa_manual,
    prepare_nmpa_manual_plan,
    record_nmpa_manual_search,
)
from ivd_research.package import scenario_coverage_warnings
from ivd_research.status import init_task, load_task


FULL_CONFIRMATIONS = {
    "task_info": True,
    "keyword_pool": True,
    "collection_scope": True,
    "primary_query": "CRP 定量检测试剂盒",
    "english_keywords": "C-reactive protein quantitative immunoassay",
    "chinese_synonyms": "C反应蛋白；CRP",
    "sample_type": "血清；血浆；全血",
    "platform": "化学发光",
    "methodology": "免疫分析",
    "intended_use": "炎症辅助诊断",
    "target_region": "中国",
    "competitor_scope": "NMPA 已注册同类产品",
    "literature_date_range": {"start": "2021-01-01", "end": "2026-07-23"},
    "literature_profile": "complete_literature",
    "patent_scope": "中国",
}


def confirmed_task(tmp_path: Path, *, competitor_scope: str = "NMPA 已注册同类产品") -> Path:
    state = init_task("CRP 定量检测试剂盒", tmp_path)
    task_dir = Path(state.task_dir)
    update_confirmations(
        task_dir,
        {**FULL_CONFIRMATIONS, "competitor_scope": competitor_scope},
    )
    return task_dir


def prepared_task(
    tmp_path: Path,
    *,
    competitor_scope: str = "仅境内产品",
) -> tuple[Path, dict]:
    task_dir = confirmed_task(tmp_path, competitor_scope=competitor_scope)
    return task_dir, prepare_nmpa_manual_plan(task_dir)["plan"]


def write_search_record(
    task_dir: Path,
    plan: dict,
    *,
    result_counts: dict[str, int] | None = None,
    attempt_ids: set[str] | None = None,
    task_id: str | None = None,
    official_search_url: str | None = None,
) -> Path:
    counts = result_counts or {}
    selected = [
        attempt
        for attempt in plan["attempts"]
        if attempt_ids is None or attempt["attempt_id"] in attempt_ids
    ]
    path = task_dir / "manual" / "nmpa" / "search_record_input.json"
    write_json(
        path,
        {
            "schema_version": "1.0",
            "task_id": task_id or plan["task_id"],
            "search_session_id": "SESSION-001",
            "operator_confirmed": True,
            "attempts": [
                {
                    "attempt_id": attempt["attempt_id"],
                    "query": attempt["query"],
                    "registration_type": attempt["registration_type"],
                    "search_time": "2026-07-23T10:00:00+08:00",
                    "official_search_url": official_search_url
                    or attempt["official_search_url"],
                    "result_count": counts.get(attempt["attempt_id"], 0),
                    "notes": "测试检索记录",
                }
                for attempt in selected
            ],
        },
    )
    return path


def write_import_manifest(
    task_dir: Path,
    plan: dict,
    *,
    result_counts: dict[str, int] | None = None,
    attempt_ids: set[str] | None = None,
    include_evidence: bool = True,
    complete_optional_fields: bool = True,
    extra_payload: dict | None = None,
) -> Path:
    counts = result_counts or {}
    selected = [
        attempt
        for attempt in plan["attempts"]
        if attempt_ids is None or attempt["attempt_id"] in attempt_ids
    ]
    manifest_attempts = []
    for index, attempt in enumerate(selected, start=1):
        evidence_files = []
        if include_evidence:
            evidence = task_dir / "manual" / "nmpa" / f"evidence-{index:03d}.png"
            evidence.write_bytes(b"\x89PNG\r\n\x1a\nmanual evidence")
            evidence_files.append(str(evidence))
        results = []
        for result_index in range(counts.get(attempt["attempt_id"], 0)):
            record = {
                "registration_certificate_number": f"国械注准20261234{index:02d}{result_index:02d}",
                "product_name": "C反应蛋白测定试剂盒（化学发光法）",
                "registrant": "测试医疗器械有限公司",
                "source_url": attempt["official_search_url"],
            }
            if complete_optional_fields:
                record.update(
                    {
                        "model": "100测试/盒",
                        "scope": "用于体外定量检测C反应蛋白。",
                        "approval_date": "2026-01-02",
                        "valid_until": "2031-01-01",
                    }
                )
            results.append(record)
        manifest_attempts.append(
            {
                "attempt_id": attempt["attempt_id"],
                "evidence_files": evidence_files,
                "results": results,
            }
        )

    path = task_dir / "manual" / "nmpa" / "import_manifest_input.json"
    payload = {
        "schema_version": "1.0",
        "task_id": plan["task_id"],
        "search_session_id": "SESSION-001",
        "capture_complete": True,
        "attempts": manifest_attempts,
        **(extra_payload or {}),
    }
    write_json(path, payload)
    return path


def test_prepare_nmpa_manual_plan_sets_awaiting_user_search(tmp_path: Path):
    task_dir = confirmed_task(tmp_path, competitor_scope="仅境内产品")

    result = prepare_nmpa_manual_plan(task_dir)

    state = load_task(task_dir)
    manual = state.scenario_statuses["nmpa_competitor"].manual_collection
    assert result["status"] == "needs_manual_review"
    assert manual is not None
    assert manual.phase == "awaiting_user_search"
    assert manual.required_attempt_ids
    assert {row["registration_type"] for row in result["plan"]["attempts"]} == {
        "境内医疗器械（注册）"
    }
    assert manual.required_attempt_ids == [
        row["attempt_id"] for row in result["plan"]["attempts"]
    ]

    manual_dir = task_dir / "manual" / "nmpa"
    assert (manual_dir / "search_plan.json").exists()
    assert (manual_dir / "search_plan.md").exists()
    assert (manual_dir / "search_record_template.json").exists()
    assert (manual_dir / "import_manifest_template.json").exists()
    assert read_json(manual_dir / "search_plan.json")["task_id"] == state.task_id


def test_prepare_nmpa_manual_plan_keeps_both_categories_by_default(tmp_path: Path):
    task_dir = confirmed_task(tmp_path)

    result = prepare_nmpa_manual_plan(task_dir)

    assert {row["registration_type"] for row in result["plan"]["attempts"]} == {
        "境内医疗器械（注册）",
        "进口医疗器械（注册）",
    }


@pytest.mark.parametrize(
    ("competitor_scope", "expected_registration_type"),
    [
        ("仅境内产品，不含进口产品", "境内医疗器械（注册）"),
        ("仅进口产品，不含境内产品", "进口医疗器械（注册）"),
    ],
)
def test_prepare_nmpa_manual_plan_honors_explicit_scope_exclusions(
    tmp_path: Path,
    competitor_scope: str,
    expected_registration_type: str,
):
    task_dir = confirmed_task(tmp_path, competitor_scope=competitor_scope)

    result = prepare_nmpa_manual_plan(task_dir)

    assert {row["registration_type"] for row in result["plan"]["attempts"]} == {
        expected_registration_type
    }


def test_standard_nmpa_collector_returns_manual_plan_without_materials(tmp_path: Path):
    task_dir = confirmed_task(tmp_path)

    result = collect("TASK-1", task_dir, {"query": "CRP"})

    assert result.status == "needs_manual_review"
    assert result.materials == []
    assert "人工" in result.message_zh


def test_cli_routes_nmpa_to_manual_plan_without_legacy_network(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    task_dir = confirmed_task(tmp_path)
    state = load_task(task_dir)
    legacy_calls = []

    def fail_if_legacy_collector_runs(**kwargs):
        legacy_calls.append(kwargs)
        raise AssertionError("legacy NMPA collector must not run")

    monkeypatch.setattr(
        "ivd_research.scenarios.nmpa_api.collect_nmpa_http",
        fail_if_legacy_collector_runs,
    )
    result = CliRunner().invoke(
        app,
        [
            "run-scenario",
            "--task-id",
            state.task_id,
            "--scenario",
            "nmpa_competitor",
            "--output-root",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert legacy_calls == []
    payload = read_json(task_dir / "manual" / "nmpa" / "search_plan.json")
    assert payload["attempts"]
    assert "awaiting_user_search" in result.stdout
    persisted = load_task(task_dir).scenario_statuses["nmpa_competitor"]
    assert persisted.manual_collection is not None
    assert persisted.manual_collection.phase == "awaiting_user_search"
    assert list(read_jsonl(task_dir / "data" / "materials.jsonl")) == []


def test_nmpa_manual_cli_commands_complete_verified_zero_flow(tmp_path: Path):
    task_dir = confirmed_task(tmp_path)
    state = load_task(task_dir)
    runner = CliRunner()
    common = ["--task-id", state.task_id, "--output-root", str(tmp_path), "--json"]

    plan_result = runner.invoke(app, ["nmpa-manual-plan", *common])
    assert plan_result.exit_code == 0
    plan = read_json(task_dir / "manual" / "nmpa" / "search_plan.json")
    first_attempt_id = plan["attempts"][0]["attempt_id"]
    partial_record_path = write_search_record(
        task_dir,
        plan,
        attempt_ids={first_attempt_id},
    )

    partial_result = runner.invoke(
        app,
        [
            "record-nmpa-manual-search",
            *common,
            "--record",
            str(partial_record_path),
        ],
    )
    assert partial_result.exit_code == 0
    assert "awaiting_user_search" in partial_result.stdout

    record_path = write_search_record(task_dir, plan)

    record_result = runner.invoke(
        app,
        [
            "record-nmpa-manual-search",
            *common,
            "--record",
            str(record_path),
        ],
    )
    assert record_result.exit_code == 0
    assert "awaiting_import" in record_result.stdout

    manifest_path = write_import_manifest(task_dir, plan)
    import_result = runner.invoke(
        app,
        [
            "import-nmpa-manual",
            *common,
            "--manifest",
            str(manifest_path),
        ],
    )
    assert import_result.exit_code == 0
    assert '"status": "no_results"' in import_result.stdout


def test_nmpa_manual_plan_command_requires_confirmed_business_scope(tmp_path: Path):
    state = init_task("CRP 定量检测试剂盒", tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "nmpa-manual-plan",
            "--task-id",
            state.task_id,
            "--output-root",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert '"status": "needs_confirmation"' in result.stdout
    assert not (Path(state.task_dir) / "manual" / "nmpa" / "search_plan.json").exists()


def test_delivery_pipeline_classifies_nmpa_as_manual_not_http():
    assert "nmpa_competitor" in cli_module.DELIVERY_MANUAL_SCENARIOS
    assert "nmpa_competitor" not in cli_module.DELIVERY_HTTP_SCENARIOS
    assert (
        cli_module.SCENARIO_COLLECTORS["nmpa_competitor"]
        is cli_module.nmpa_manual_collect
    )


def test_record_search_sets_awaiting_import_without_closing_source(tmp_path: Path):
    task_dir, plan = prepared_task(tmp_path)
    record = write_search_record(task_dir, plan)

    result = record_nmpa_manual_search(task_dir, record)

    scenario = load_task(task_dir).scenario_statuses["nmpa_competitor"]
    assert result["status"] == "needs_manual_review"
    assert scenario.status == "needs_manual_review"
    assert scenario.manual_collection is not None
    assert scenario.manual_collection.phase == "awaiting_import"
    assert set(scenario.manual_collection.recorded_attempt_ids) == {
        item["attempt_id"] for item in plan["attempts"]
    }
    assert list(read_jsonl(task_dir / "data" / "materials.jsonl")) == []
    events = list(read_jsonl(task_dir / "logs" / "events.jsonl"))
    assert events[-1]["event"] == "nmpa_manual_search_recorded"


def test_partial_search_record_remains_awaiting_user_search(tmp_path: Path):
    task_dir, plan = prepared_task(tmp_path)
    first_id = plan["attempts"][0]["attempt_id"]
    record = write_search_record(task_dir, plan, attempt_ids={first_id})

    result = record_nmpa_manual_search(task_dir, record)

    assert result["manual_phase"] == "awaiting_user_search"
    assert result["remaining_attempt_ids"]


def test_search_record_rejects_wrong_task_and_non_nmpa_url(tmp_path: Path):
    task_dir, plan = prepared_task(tmp_path)
    wrong_task = write_search_record(task_dir, plan, task_id="TASK-WRONG")
    with pytest.raises(ValueError, match="task_id"):
        record_nmpa_manual_search(task_dir, wrong_task)

    wrong_url = write_search_record(
        task_dir,
        plan,
        official_search_url="https://example.com/fake-nmpa",
    )
    with pytest.raises(ValueError, match="NMPA"):
        record_nmpa_manual_search(task_dir, wrong_url)


def test_zero_results_require_visible_evidence(tmp_path: Path):
    task_dir, plan = prepared_task(tmp_path)
    record_nmpa_manual_search(task_dir, write_search_record(task_dir, plan))
    manifest = write_import_manifest(task_dir, plan, include_evidence=False)

    with pytest.raises(ValueError, match="evidence_files"):
        import_nmpa_manual(task_dir, manifest)

    scenario = load_task(task_dir).scenario_statuses["nmpa_competitor"]
    assert scenario.status != "no_results"


def test_zero_results_reject_disguised_image_evidence(tmp_path: Path):
    task_dir, plan = prepared_task(tmp_path)
    record_nmpa_manual_search(task_dir, write_search_record(task_dir, plan))
    manifest = write_import_manifest(task_dir, plan)
    payload = read_json(manifest)
    disguised = Path(payload["attempts"][0]["evidence_files"][0])
    disguised.write_bytes(b"this is not a png image")

    with pytest.raises(ValueError, match="内容格式"):
        import_nmpa_manual(task_dir, manifest)


def test_verified_zero_results_close_only_after_all_attempts(tmp_path: Path):
    task_dir, plan = prepared_task(tmp_path)
    record_nmpa_manual_search(task_dir, write_search_record(task_dir, plan))
    manifest = write_import_manifest(task_dir, plan)

    result = import_nmpa_manual(task_dir, manifest)

    scenario = load_task(task_dir).scenario_statuses["nmpa_competitor"]
    assert result["status"] == "no_results"
    assert scenario.manual_collection is not None
    assert scenario.manual_collection.phase == "verified_no_results"
    assert scenario.manual_collection.zero_results_verified is True
    assert not any(
        "NMPA" in warning for warning in scenario_coverage_warnings(task_dir)
    )


def test_new_generic_nmpa_clue_reopens_verified_zero_result(tmp_path: Path):
    task_dir, plan = prepared_task(tmp_path)
    record_nmpa_manual_search(task_dir, write_search_record(task_dir, plan))
    import_nmpa_manual(task_dir, write_import_manifest(task_dir, plan))

    import_finding(
        task_dir,
        title="新发现的 NMPA 注册线索",
        source="nmpa_competitor",
        source_url=(
            "https://www.nmpa.gov.cn/datasearch/home-index.html#category=ylqx"
        ),
        content="国械注准20261234567 C反应蛋白测定试剂盒",
        material_type="competitor",
        identifier="国械注准20261234567",
    )

    scenario = load_task(task_dir).scenario_statuses["nmpa_competitor"]
    assert scenario.status == "needs_manual_review"
    assert scenario.manual_collection is not None
    assert scenario.manual_collection.phase == "awaiting_import"
    assert scenario.manual_collection.zero_results_verified is False


def test_existing_nmpa_clue_prevents_later_zero_result_closure(tmp_path: Path):
    task_dir, plan = prepared_task(tmp_path)
    import_finding(
        task_dir,
        title="待核验的 NMPA 注册线索",
        source="nmpa_competitor",
        source_url=(
            "https://www.nmpa.gov.cn/datasearch/home-index.html#category=ylqx"
        ),
        content="国械注准20261234567 C反应蛋白测定试剂盒",
        material_type="competitor",
        identifier="国械注准20261234567",
    )
    record_nmpa_manual_search(task_dir, write_search_record(task_dir, plan))

    result = import_nmpa_manual(task_dir, write_import_manifest(task_dir, plan))

    scenario = load_task(task_dir).scenario_statuses["nmpa_competitor"]
    assert result["status"] == "needs_manual_review"
    assert result["manual_phase"] == "awaiting_import"
    assert "冲突" in result["message_zh"]
    assert scenario.manual_collection is not None
    assert scenario.manual_collection.zero_results_verified is False


def test_partial_manifest_does_not_close_source(tmp_path: Path):
    task_dir, plan = prepared_task(tmp_path)
    record_nmpa_manual_search(task_dir, write_search_record(task_dir, plan))
    first_id = plan["attempts"][0]["attempt_id"]
    manifest = write_import_manifest(task_dir, plan, attempt_ids={first_id})

    result = import_nmpa_manual(task_dir, manifest)

    assert result["status"] == "needs_manual_review"
    assert result["manual_phase"] == "awaiting_import"
    assert result["remaining_attempt_ids"]


def test_result_import_creates_traceable_material_and_card(tmp_path: Path):
    task_dir, plan = prepared_task(tmp_path)
    first_id = plan["attempts"][0]["attempt_id"]
    counts = {first_id: 1}
    record_nmpa_manual_search(
        task_dir,
        write_search_record(task_dir, plan, result_counts=counts),
    )
    manifest = write_import_manifest(task_dir, plan, result_counts=counts)

    result = import_nmpa_manual(task_dir, manifest)

    assert result["status"] == "completed"
    materials = list(read_jsonl(task_dir / "data" / "materials.jsonl"))
    assert len(materials) == 1
    material = materials[0]
    assert material["source_scenario"] == "nmpa_competitor"
    assert material["adapter_id"] == "nmpa_manual_import"
    assert material["raw_fields"]["registration_certificate_number"].startswith(
        "国械注准"
    )
    assert material["download_files"][0]["sha256"]
    cards = list(read_jsonl(task_dir / "data" / "evidence_cards.jsonl"))
    assert len(cards) == 1
    assert cards[0]["material_id"] == material["material_id"]
    assert cards[0]["needs_review"] is True
    events = list(read_jsonl(task_dir / "logs" / "events.jsonl"))
    assert events[-1]["event"] == "nmpa_manual_import_completed"
    assert events[-1]["status"] == "completed"
    assert not any(
        "NMPA" in warning for warning in scenario_coverage_warnings(task_dir)
    )


def test_import_is_idempotent_for_registration_number(tmp_path: Path):
    task_dir, plan = prepared_task(tmp_path)
    first_id = plan["attempts"][0]["attempt_id"]
    counts = {first_id: 1}
    record_nmpa_manual_search(
        task_dir,
        write_search_record(task_dir, plan, result_counts=counts),
    )
    manifest = write_import_manifest(task_dir, plan, result_counts=counts)

    import_nmpa_manual(task_dir, manifest)
    import_nmpa_manual(task_dir, manifest)

    assert len(read_jsonl(task_dir / "data" / "materials.jsonl")) == 1
    assert len(read_jsonl(task_dir / "data" / "evidence_cards.jsonl")) == 1


def test_replaying_unchanged_search_record_preserves_verified_zero_status(
    tmp_path: Path,
):
    task_dir, plan = prepared_task(tmp_path)
    record = write_search_record(task_dir, plan)
    record_nmpa_manual_search(task_dir, record)
    import_nmpa_manual(task_dir, write_import_manifest(task_dir, plan))

    result = record_nmpa_manual_search(task_dir, record)

    assert result["status"] == "no_results"
    assert result["manual_phase"] == "verified_no_results"


def test_changed_search_record_invalidates_prior_zero_result(tmp_path: Path):
    task_dir, plan = prepared_task(tmp_path)
    record_nmpa_manual_search(task_dir, write_search_record(task_dir, plan))
    import_nmpa_manual(task_dir, write_import_manifest(task_dir, plan))
    first_id = plan["attempts"][0]["attempt_id"]

    result = record_nmpa_manual_search(
        task_dir,
        write_search_record(task_dir, plan, result_counts={first_id: 1}),
    )

    scenario = load_task(task_dir).scenario_statuses["nmpa_competitor"]
    assert result["status"] == "needs_manual_review"
    assert result["manual_phase"] == "awaiting_import"
    assert scenario.manual_collection is not None
    assert first_id not in scenario.manual_collection.validated_attempt_ids
    assert scenario.manual_collection.zero_results_verified is False


def test_tampered_persisted_search_record_is_rejected(tmp_path: Path):
    task_dir, plan = prepared_task(tmp_path)
    record_nmpa_manual_search(task_dir, write_search_record(task_dir, plan))
    scenario = load_task(task_dir).scenario_statuses["nmpa_competitor"]
    assert scenario.manual_collection is not None
    persisted_path = task_dir / scenario.manual_collection.search_record_path
    persisted = read_json(persisted_path)
    first_id = plan["attempts"][0]["attempt_id"]
    persisted["attempts"][0]["result_count"] = 1
    write_json(persisted_path, persisted)
    manifest = write_import_manifest(task_dir, plan, result_counts={first_id: 1})

    with pytest.raises(ValueError, match="校验值"):
        import_nmpa_manual(task_dir, manifest)


def test_import_rejects_credentials_missing_files_and_result_count_mismatch(
    tmp_path: Path,
):
    task_dir, plan = prepared_task(tmp_path)
    first_id = plan["attempts"][0]["attempt_id"]
    counts = {first_id: 1}
    record_nmpa_manual_search(
        task_dir,
        write_search_record(task_dir, plan, result_counts=counts),
    )

    credentials = write_import_manifest(
        task_dir,
        plan,
        result_counts=counts,
        extra_payload={"cookie": "must-not-be-stored"},
    )
    with pytest.raises(ValueError, match="敏感字段"):
        import_nmpa_manual(task_dir, credentials)

    missing = write_import_manifest(task_dir, plan, result_counts=counts)
    payload = read_json(missing)
    payload["attempts"][0]["evidence_files"] = ["missing-screenshot.png"]
    write_json(missing, payload)
    with pytest.raises(ValueError, match="不存在"):
        import_nmpa_manual(task_dir, missing)

    mismatch = write_import_manifest(task_dir, plan, result_counts={})
    with pytest.raises(ValueError, match="result_count"):
        import_nmpa_manual(task_dir, mismatch)


@pytest.mark.parametrize(
    "sensitive_key",
    ["cookie", "api_key", "access_token", "client_secret", "authorization"],
)
def test_import_rejects_common_credential_field_names(
    tmp_path: Path,
    sensitive_key: str,
):
    task_dir, plan = prepared_task(tmp_path)
    record_nmpa_manual_search(task_dir, write_search_record(task_dir, plan))
    manifest = write_import_manifest(
        task_dir,
        plan,
        extra_payload={"browser_context": {sensitive_key: "must-not-be-stored"}},
    )

    with pytest.raises(ValueError, match="敏感字段"):
        import_nmpa_manual(task_dir, manifest)


def test_incomplete_optional_fields_close_with_warnings(tmp_path: Path):
    task_dir, plan = prepared_task(tmp_path)
    first_id = plan["attempts"][0]["attempt_id"]
    counts = {first_id: 1}
    record_nmpa_manual_search(
        task_dir,
        write_search_record(task_dir, plan, result_counts=counts),
    )
    manifest = write_import_manifest(
        task_dir,
        plan,
        result_counts=counts,
        complete_optional_fields=False,
    )

    result = import_nmpa_manual(task_dir, manifest)

    assert result["status"] == "completed_with_warnings"
    assert result["warnings"]


def test_evidence_card_failure_does_not_mark_source_completed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    task_dir, plan = prepared_task(tmp_path)
    first_id = plan["attempts"][0]["attempt_id"]
    counts = {first_id: 1}
    record_nmpa_manual_search(
        task_dir,
        write_search_record(task_dir, plan, result_counts=counts),
    )
    manifest = write_import_manifest(task_dir, plan, result_counts=counts)

    def fail_card_generation(task_dir: Path) -> dict:
        raise RuntimeError("card generation failed")

    monkeypatch.setattr(
        "ivd_research.nmpa_manual.generate_draft_evidence_cards",
        fail_card_generation,
    )

    with pytest.raises(RuntimeError, match="card generation failed"):
        import_nmpa_manual(task_dir, manifest)

    scenario = load_task(task_dir).scenario_statuses["nmpa_competitor"]
    assert scenario.status == "needs_manual_review"
    assert scenario.manual_collection is not None
    assert scenario.manual_collection.phase == "awaiting_import"
