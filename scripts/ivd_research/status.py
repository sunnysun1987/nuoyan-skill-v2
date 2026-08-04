from datetime import datetime
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from .constants import TAXONOMY_VERSION, WORKFLOW_VERSION
from .jsonl import append_jsonl, read_json, read_jsonl, write_json
from .models import RecommendedAction, ScenarioStatus, TaskState
from .paths import new_task_dir, new_task_id
from .research_integrity import ResearchPolicy
from .scenarios.registry import all_scenarios


DEFAULT_CONFIRMATIONS = {
    "task_info": False,
    "keyword_pool": False,
    "collection_scope": False,
    "methodology": False,
    "platform": "",
    "sample_type": "",
    "intended_use": "",
    "target_region": "",
    "target_user": "",
    "competitor_scope": "",
    "literature_date_range": False,
    "literature_years": 5,
    "literature_profile": "complete_literature",
    # None means "use the selected literature profile default".
    # Explicit smaller limits are only honored by lightweight profiles such as
    # quick_scan; standard complete profiles keep their own minimum depth.
    "literature_retmax": None,
    "patent_scope": False,
    "primary_query": "",
    "english_keywords": "",
    "english_method_keywords": "",
    "chinese_synonyms": "",
    # Defaults to conservative auto-detection. Set to False only when a
    # confirmed scope explicitly excludes scientific database evidence.
    "life_science_required": "auto",
    "life_science_scope": "auto",
}


def now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def init_task(topic: str, output_root: Path) -> TaskState:
    task_id = new_task_id()
    task_dir = new_task_dir(output_root, topic)
    create_task_directories(task_dir)
    statuses = {
        scenario.scenario_id: ScenarioStatus(
            scenario_id=scenario.scenario_id,
            label_zh=scenario.label_zh,
        )
        for scenario in all_scenarios()
    }
    state = TaskState(
        task_id=task_id,
        topic=topic,
        task_dir=str(task_dir),
        created_at=now_iso(),
        workflow_version=WORKFLOW_VERSION,
        taxonomy_version=TAXONOMY_VERSION,
        confirmations=dict(DEFAULT_CONFIRMATIONS),
        scenario_statuses=statuses,
        research_policy=ResearchPolicy().model_dump(mode="json"),
    )
    write_json(task_dir / "task.json", state.model_dump(mode="json"))
    ensure_jsonl_files(task_dir)
    return state


def create_task_directories(task_dir: Path) -> None:
    for relative in [
        "data",
        "staging/analysis_requests",
        "staging/evidence_cards",
        "staging/report_sections",
        "downloads/regulatory",
        "downloads/competitors",
        "downloads/standards",
        "downloads/patents",
        "downloads/literature",
        "downloads/literature/pubmed",
        "downloads/literature/pmc",
        "downloads/literature/pmc_pdf",
        "downloads/literature/openalex",
        "downloads/local_import",
        "extracted_text/regulatory",
        "extracted_text/competitors",
        "extracted_text/standards",
        "extracted_text/patents",
        "extracted_text/literature",
        "extracted_text/local_import",
        "extracted_text/chunks",
        "evidence_cards/markdown",
        "evidence_cards/json",
        "review",
        "reports",
        "logs",
        "packages",
    ]:
        (task_dir / relative).mkdir(parents=True, exist_ok=True)


def ensure_jsonl_files(task_dir: Path) -> None:
    for relative in [
        "data/materials.jsonl",
        "data/evidence_cards.jsonl",
        "data/report_sections.jsonl",
        "data/report_evidence_view.jsonl",
        "data/review_imports.jsonl",
        "data/report_versions.jsonl",
        "data/research_claims.jsonl",
        "data/evidence_conflicts.jsonl",
        "logs/events.jsonl",
        "logs/debug.jsonl",
        "logs/research_iterations.jsonl",
    ]:
        path = task_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)


def find_task(output_root: Path, task_id: str) -> Path:
    for task_json in output_root.glob("*/task.json"):
        data = read_json(task_json)
        if data.get("task_id") == task_id:
            return task_json.parent
    raise FileNotFoundError(f"Task not found: {task_id}")


def load_task(task_dir: Path) -> TaskState:
    data = read_json(task_dir / "task.json")
    confirmations = data.setdefault("confirmations", {})
    for key, value in DEFAULT_CONFIRMATIONS.items():
        confirmations.setdefault(key, value)
    data.setdefault("research_policy", ResearchPolicy().model_dump(mode="json"))
    return TaskState.model_validate(data)


def save_task(state: TaskState) -> None:
    write_json(Path(state.task_dir) / "task.json", state.model_dump(mode="json"))


def next_material_id(task_dir: Path) -> str:
    highest = 0
    for material in read_jsonl(task_dir / "data" / "materials.jsonl"):
        match = re.fullmatch(
            r"MAT-(\d+)(?:-\d+)?",
            str(material.get("material_id") or ""),
        )
        if match:
            highest = max(highest, int(match.group(1)))
    return f"MAT-{highest + 1:06d}"


def record_materials(task_dir: Path, materials: list) -> list:
    existing_keys: set[tuple[str, str]] = set()
    for material in read_jsonl(task_dir / "data" / "materials.jsonl"):
        source = str(material.get("source_scenario") or "")
        for key in material_record_keys(material):
            existing_keys.add((source, key))

    recorded = []
    for material in materials:
        payload = material.model_dump(mode="json")
        source = str(payload.get("source_scenario") or "")
        keys = material_record_keys(payload)
        if keys and any((source, key) in existing_keys for key in keys):
            continue
        append_jsonl(
            task_dir / "data" / "materials.jsonl",
            payload,
        )
        recorded.append(material)
        for key in keys:
            existing_keys.add((source, key))
    return recorded


def material_record_keys(material: dict) -> set[str]:
    keys = {
        str(key).strip().lower()
        for key in material.get("possible_duplicate_keys") or []
        if str(key).strip()
    }
    if not keys:
        source_url = str(material.get("source_url") or "").strip().lower()
        title = " ".join(str(material.get("title") or "").lower().split())
        if source_url:
            keys.add(f"url:{source_url}")
        elif title:
            keys.add(f"title:{title}")
    return keys


def find_duplicate_material(task_dir: Path, candidate: dict) -> dict | None:
    source = str(candidate.get("source_scenario") or "")
    keys = material_record_keys(candidate)
    if not keys:
        return None
    for material in read_jsonl(task_dir / "data" / "materials.jsonl"):
        if str(material.get("source_scenario") or "") != source:
            continue
        if keys.intersection(material_record_keys(material)):
            return material
    return None


def recommended_next_action(state: TaskState) -> RecommendedAction:
    if not state.confirmations.get("task_info"):
        return RecommendedAction(
            action_id="confirm_task_info",
            label_zh="确认任务信息",
            reason_zh="项目对象尚未确认。",
        )
    if not state.confirmations.get("keyword_pool"):
        return RecommendedAction(
            action_id="confirm_keyword_pool",
            label_zh="确认关键词池",
            reason_zh="关键词池尚未确认，无法稳定执行各场景检索。",
        )
    for scenario in state.scenario_statuses.values():
        if scenario.status in {"needs_login", "permission_required"}:
            return RecommendedAction(
                action_id=f"open_browser_session:{scenario.scenario_id}",
                label_zh=f"打开浏览器处理 {scenario.label_zh}",
                reason_zh=(
                    f"{scenario.label_zh} 当前状态为 {scenario.status}。"
                    f"请运行 open-browser-session --scenario {scenario.scenario_id}，"
                    "在可见浏览器中完成登录或真人验证后再继续采集。"
                ),
            )
    return RecommendedAction(
        action_id="run_full_pipeline",
        label_zh="运行完整调研流水线",
        reason_zh="关键前置项已确认，可以开始采集。",
    )


def status_payload(state: TaskState) -> dict:
    task_dir = Path(state.task_dir)
    return {
        "task_summary": {
            "task_id": state.task_id,
            "topic": state.topic,
            "task_dir": state.task_dir,
        },
        "confirmation_status": state.confirmations,
        "scenario_statuses": {
            key: value.model_dump(mode="json")
            for key, value in state.scenario_statuses.items()
        },
        "material_counts": {
            "total": count_lines(task_dir / "data" / "materials.jsonl")
        },
        "evidence_card_counts": {
            "total": count_lines(task_dir / "data" / "evidence_cards.jsonl")
        },
        "recommended_next_action": recommended_next_action(state).model_dump(
            mode="json"
        ),
        "available_actions": [
            "confirm_task_info",
            "confirm_keyword_pool",
            "run_scenario",
            "import_local",
            "export_review",
            "build_report",
        ]
        + [
            f"open_browser_session:{scenario.scenario_id}"
            for scenario in state.scenario_statuses.values()
            if scenario.status in {"needs_login", "permission_required"}
        ],
    }


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
