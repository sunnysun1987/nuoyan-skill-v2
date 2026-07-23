from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

from .jsonl import read_json, write_json
from .models import FailureType, ManualCollectionState, ScenarioStatus
from .query_plan import scenario_query_plans
from .scenarios.base import ScenarioResult
from .source_adapters.source_sites import SOURCE_SITES
from .status import load_task, now_iso, save_task


SCENARIO_ID = "nmpa_competitor"
MANUAL_DIR = Path("manual/nmpa")
PLAN_PATH = MANUAL_DIR / "search_plan.json"
PLAN_MARKDOWN_PATH = MANUAL_DIR / "search_plan.md"
SEARCH_RECORD_TEMPLATE_PATH = MANUAL_DIR / "search_record_template.json"
IMPORT_MANIFEST_TEMPLATE_PATH = MANUAL_DIR / "import_manifest_template.json"
NMPA_SOURCE_SITE = next(
    site for site in SOURCE_SITES if site.source_site_id == SCENARIO_ID
)
NMPA_SEARCH_URL = NMPA_SOURCE_SITE.base_url

DOMESTIC_REGISTRATION = "境内医疗器械（注册）"
IMPORT_REGISTRATION = "进口医疗器械（注册）"


def _registration_types(competitor_scope: Any) -> list[str]:
    scope = "".join(str(competitor_scope or "").split())
    domestic_markers = ("仅境内", "只查境内", "只要境内", "境内产品", "国内产品", "国产")
    import_markers = ("仅进口", "只查进口", "只要进口", "进口产品")
    domestic_only = any(marker in scope for marker in domestic_markers)
    import_only = any(marker in scope for marker in import_markers)
    if domestic_only and not import_only:
        return [DOMESTIC_REGISTRATION]
    if import_only and not domestic_only:
        return [IMPORT_REGISTRATION]
    return [DOMESTIC_REGISTRATION, IMPORT_REGISTRATION]


def _attempt_id(registration_type: str, query: str) -> str:
    digest = sha256(f"{registration_type}\0{query}".encode()).hexdigest()[:12]
    return f"NMPA-{digest.upper()}"


def _build_attempts(state: Any) -> list[dict[str, Any]]:
    categories = _registration_types(state.confirmations.get("competitor_scope"))
    plans = scenario_query_plans(state).get(SCENARIO_ID, [])
    attempts: list[dict[str, Any]] = []
    for plan in plans:
        for registration_type in categories:
            attempts.append(
                {
                    "attempt_id": _attempt_id(registration_type, plan.query),
                    "query": plan.query,
                    "query_role": str(plan.params.get("query_role") or ""),
                    "registration_type": registration_type,
                    "official_search_url": NMPA_SEARCH_URL,
                    "required": True,
                }
            )
    return attempts


def _attempt_signature(plan: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [
        (
            str(item.get("attempt_id") or ""),
            str(item.get("query") or ""),
            str(item.get("registration_type") or ""),
        )
        for item in plan.get("attempts", [])
    ]


def _search_record_template(task_id: str, attempts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "task_id": task_id,
        "search_session_id": "",
        "operator_confirmed": False,
        "attempts": [
            {
                "attempt_id": item["attempt_id"],
                "query": item["query"],
                "registration_type": item["registration_type"],
                "search_time": "",
                "official_search_url": item["official_search_url"],
                "result_count": None,
                "notes": "",
            }
            for item in attempts
        ],
    }


def _import_manifest_template(task_id: str, attempts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "task_id": task_id,
        "search_session_id": "",
        "capture_complete": False,
        "attempts": [
            {
                "attempt_id": item["attempt_id"],
                "evidence_files": [],
                "results": [],
            }
            for item in attempts
        ],
    }


def _plan_markdown(plan: dict[str, Any]) -> str:
    lines = [
        "# NMPA 竞品注册人工检索计划",
        "",
        f"- 任务编号：{plan['task_id']}",
        f"- 课题：{plan['topic']}",
        f"- 官方入口：{plan['official_search_url']}",
        "",
        "## 操作要求",
        "",
        "1. 在用户自己的浏览器中打开官方入口，按下表逐项检索。",
        "2. 如出现登录或验证码，由用户在浏览器内完成；不要提供密码、Cookie 或令牌。",
        "3. 每项保存截图或官方导出，并记录实际结果数。",
        "4. 未执行、未记录或未提供证据时，该信源保持待人工处理，不得判定为无结果。",
        "",
        "## 必做检索",
        "",
        "| 尝试编号 | 注册类别 | 查询词 | 查询层级 |",
        "| --- | --- | --- | --- |",
    ]
    for item in plan["attempts"]:
        query = str(item["query"]).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {item['attempt_id']} | {item['registration_type']} | "
            f"{query} | {item['query_role']} |"
        )
    lines.append("")
    return "\n".join(lines)


def prepare_nmpa_manual_plan(task_dir: Path) -> dict[str, Any]:
    task_dir = Path(task_dir)
    state = load_task(task_dir)
    attempts = _build_attempts(state)
    if not attempts:
        raise ValueError("NMPA 人工检索计划为空，请先确认项目关键词。")

    plan_path = task_dir / PLAN_PATH
    previous_plan = read_json(plan_path) if plan_path.exists() else {}
    generated_at = now_iso()
    plan = {
        "schema_version": "1.0",
        "task_id": state.task_id,
        "topic": state.topic,
        "generated_at": generated_at,
        "competitor_scope": state.confirmations.get("competitor_scope", ""),
        "official_search_url": NMPA_SEARCH_URL,
        "attempts": attempts,
    }
    same_plan = bool(previous_plan) and _attempt_signature(previous_plan) == _attempt_signature(plan)

    write_json(plan_path, plan)
    markdown_path = task_dir / PLAN_MARKDOWN_PATH
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(_plan_markdown(plan), encoding="utf-8")

    search_template_path = task_dir / SEARCH_RECORD_TEMPLATE_PATH
    manifest_template_path = task_dir / IMPORT_MANIFEST_TEMPLATE_PATH
    if not same_plan or not search_template_path.exists():
        write_json(search_template_path, _search_record_template(state.task_id, attempts))
    if not same_plan or not manifest_template_path.exists():
        write_json(manifest_template_path, _import_manifest_template(state.task_id, attempts))

    scenario = state.scenario_statuses.get(SCENARIO_ID)
    if scenario is None:
        scenario = ScenarioStatus(scenario_id=SCENARIO_ID, label_zh="NMPA 竞品注册信息")
        state.scenario_statuses[SCENARIO_ID] = scenario
    previous_manual = scenario.manual_collection
    if same_plan and previous_manual is not None:
        manual = previous_manual.model_copy(deep=True)
        if manual.phase == "not_started":
            manual.phase = "awaiting_user_search"
    else:
        manual = ManualCollectionState(
            phase="awaiting_user_search",
            required_attempt_ids=[item["attempt_id"] for item in attempts],
        )
    manual.plan_path = PLAN_PATH.as_posix()
    manual.last_updated = generated_at
    scenario.manual_collection = manual

    closed_phases = {"completed", "completed_with_warnings", "verified_no_results"}
    if manual.phase not in closed_phases:
        scenario.status = FailureType.NEEDS_MANUAL_REVIEW.value
        scenario.last_message = (
            "NMPA 标准采集采用人工辅助流程。请在用户浏览器中完成计划内检索，"
            f"并保存可见证据：{PLAN_MARKDOWN_PATH.as_posix()}。"
        )
    save_task(state)
    return {
        "status": scenario.status,
        "manual_phase": manual.phase,
        "message_zh": scenario.last_message,
        "plan_path": PLAN_PATH.as_posix(),
        "search_record_template_path": SEARCH_RECORD_TEMPLATE_PATH.as_posix(),
        "import_manifest_template_path": IMPORT_MANIFEST_TEMPLATE_PATH.as_posix(),
        "plan": plan,
    }


def collect(task_id: str, task_dir: Path, params: dict[str, Any]) -> ScenarioResult:
    del task_id, params
    result = prepare_nmpa_manual_plan(Path(task_dir))
    status = str(result["status"])
    failure_type = (
        FailureType.NEEDS_MANUAL_REVIEW
        if status == FailureType.NEEDS_MANUAL_REVIEW.value
        else None
    )
    return ScenarioResult(
        status=status,
        materials=[],
        failure_type=failure_type,
        message_zh=str(result["message_zh"]),
    )
