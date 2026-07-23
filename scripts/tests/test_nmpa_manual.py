from pathlib import Path

from ivd_research.confirmations import update_confirmations
from ivd_research.jsonl import read_json
from ivd_research.nmpa_manual import collect, prepare_nmpa_manual_plan
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


def test_standard_nmpa_collector_returns_manual_plan_without_materials(tmp_path: Path):
    task_dir = confirmed_task(tmp_path)

    result = collect("TASK-1", task_dir, {"query": "CRP"})

    assert result.status == "needs_manual_review"
    assert result.materials == []
    assert "人工" in result.message_zh
