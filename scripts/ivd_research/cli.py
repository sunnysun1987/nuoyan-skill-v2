import json
import inspect
from json import JSONDecodeError
from pathlib import Path
from typing import Optional

import typer

from .browser_session import (
    open_browser_session,
    prepare_browser_session,
    probe_browser_workflow,
)
from .browser_collect import run_browser_workflow, scout_browser_workflow
from .browser_workflows import browser_workflow, search_url_for_workflow
from .confirmations import update_confirmations
from .doctor import run_doctor, run_network_doctor
from .evidence import (
    commit_staged_evidence,
    generate_draft_evidence_cards,
    validate_staged_evidence,
)
from .import_finding import import_finding
from .knowledge.literature_graph import build_literature_knowledge
from .local_import import import_local
from .models import FailureType, Material
from .nmpa_manual import (
    collect as nmpa_manual_collect,
    import_nmpa_manual,
    prepare_nmpa_manual_plan,
    record_nmpa_manual_search,
)
from .paths import default_output_root
from .package import (
    build_standard_delivery,
    package_task,
    requires_life_science_research,
    verify_package,
)
from .project_profile import scenario_exclusion_message
from .query_plan import (
    default_query_plan,
    scenario_query_plans,
)
from .reports import build_report
from .research_integrity import (
    ResearchPolicy,
    build_research_integrity_audit,
    record_evidence_conflict,
    record_research_claim,
    record_research_iteration,
    validate_retrieval_policy,
)
from .review_excel import export_review, import_review
from .scenarios import (
    cma_lab_management,
    cmde_regulatory,
    patenthub_patents,
    openalex_literature,
    pubmed_pmc,
    standards_current,
    local_import_adapter,
    wiley_alz,
    yiigle_fulltext,
    yiigle_zhjyyxzz,
    yiigle_zhsjkzz,
)
from .scenarios.registry import get_scenario
from .scenarios.base import ScenarioResult
from .site_profiles import record_site_observation, site_profile
from .source_quality import build_source_quality_audit
from .source_adapters.life_science_research_bridge import (
    build_life_science_research_plan,
    import_life_science_findings,
)
from .source_adapters.csv_literature_import import import_literature_table
from .source_adapters.source_sites import all_source_sites, export_source_sites
from .jsonl import append_jsonl
from .staging import (
    commit_staged_report_sections,
    create_analysis_requests,
    validate_staged_report_sections,
)
from .status import (
    find_task,
    init_task,
    load_task,
    next_material_id,
    record_materials,
    save_task,
    status_payload,
    now_iso,
)
from .translation import setup_translation_engine, translate_materials, translation_status

app = typer.Typer(name="nuoyan", no_args_is_help=True)

REQUIRED_BUSINESS_CONFIRMATIONS = [
    "task_info",
    "keyword_pool",
    "collection_scope",
    "primary_query",
    "english_keywords",
    "sample_type",
    "platform",
    "methodology",
    "intended_use",
    "target_region",
    "competitor_scope",
    "literature_date_range",
    "literature_profile",
    "patent_scope",
]

CONFIRMATION_QUESTIONS = {
    "task_info": "项目对象是否已确认。",
    "keyword_pool": "核心中文/英文关键词池是否已确认。",
    "collection_scope": "本次是否执行完整立项调研来源采集。",
    "primary_query": "请确认中文主检索词，例如：目标检测项目 + 样本类型 + 方法学 + IVD。",
    "english_keywords": "请确认英文主检索式，覆盖靶标、疾病、样本和用途。",
    "sample_type": "请确认样本类型，例如：血浆/血清/全血/指尖血。",
    "platform": "请确认计划平台，例如：化学发光、磁微粒化学发光、ELISA、胶体金、POCT、质谱。",
    "methodology": "请确认方法学/检测原理，例如：免疫夹心法、竞争法、抗体对、校准品体系。",
    "intended_use": "请确认预期用途，例如：辅助诊断、风险分层、筛查/分诊、疗效监测或研究用途。",
    "target_region": "请确认目标地区，例如：中国注册优先/中国+欧盟/中国+美国。",
    "competitor_scope": "请确认竞品范围，例如：目标检测项目直接竞品、相邻检测项目、同方法学产品或同临床场景产品。",
    "literature_date_range": "请确认文献时间范围，例如：近 5 年、近 10 年或指定起止日期。",
    "literature_profile": "请确认文献检索 profile，例如：complete_literature、fulltext_first、core_must_read、quick_scan。",
    "patent_scope": "请确认专利检索范围，例如：中国/全球/PCT+中美欧日。",
}


def missing_business_confirmations(state) -> list[str]:
    missing = []
    confirmations = state.confirmations
    for key in REQUIRED_BUSINESS_CONFIRMATIONS:
        if key == "literature_date_range":
            value = confirmations.get("literature_date_range")
            years = confirmations.get("literature_years")
            if value in (False, None, "", [], {}) and years in (False, None, "", [], {}):
                missing.append(key)
            continue
        if key == "literature_profile":
            value = confirmations.get("literature_profile") or "complete_literature"
            if value in (False, None, "", [], {}):
                missing.append(key)
            continue
        value = confirmations.get(key)
        if value in (False, None, "", [], {}):
            missing.append(key)
    return missing


def confirmation_gate_payload(state, *, action: str) -> dict:
    missing_confirmations = missing_business_confirmations(state)
    return {
        "status": "needs_confirmation",
        "action": action,
        "missing_confirmations": missing_confirmations,
        "questions": {
            key: CONFIRMATION_QUESTIONS.get(key, key)
            for key in missing_confirmations
        },
        "message_zh": (
            "正式检索前必须先完成 IVD 研发业务检索画像确认。"
            "检测对象、样本类型、平台/方法学、预期用途、目标地区、竞品范围和专利范围会直接进入检索词与筛选逻辑。"
        ),
    }


def enforce_business_confirmations(state, *, action: str, json_output: bool) -> None:
    if not missing_business_confirmations(state):
        return
    emit(confirmation_gate_payload(state, action=action), json_output)
    raise typer.Exit(code=2)


def enforce_public_collection_policy(state, *, action: str, json_output: bool) -> None:
    try:
        validate_retrieval_policy(
            state.research_policy,
            external_provider=True,
        )
    except ValueError as exc:
        payload = {
            "status": "data_boundary_blocked",
            "action": action,
            "data_classification": state.research_policy.get(
                "data_classification",
                "",
            ),
            "message_zh": str(exc),
        }
        append_jsonl(
            Path(state.task_dir) / "logs" / "events.jsonl",
            {
                "time": now_iso(),
                "event": "public_collection_blocked",
                **payload,
            },
        )
        emit(payload, json_output)
        raise typer.Exit(code=2) from exc

SCENARIO_COLLECTORS = {
    "cmde_regulatory": cmde_regulatory.collect,
    "nmpa_competitor": nmpa_manual_collect,
    "standards_current": standards_current.collect,
    "patenthub_patents": patenthub_patents.collect,
    "yiigle_zhjyyxzz": yiigle_zhjyyxzz.collect,
    "yiigle_zhsjkzz": yiigle_zhsjkzz.collect,
    "cma_lab_management": cma_lab_management.collect,
    "wiley_alz": wiley_alz.collect,
    "pubmed_literature": pubmed_pmc.collect_pubmed,
    "pmc_fulltext": pubmed_pmc.collect_pmc,
    "openalex_literature": openalex_literature.collect,
    "yiigle_fulltext": yiigle_fulltext.collect,
    "local_import": local_import_adapter.collect,
}

DELIVERY_MANUAL_SCENARIOS = [
    "nmpa_competitor",
]

DELIVERY_HTTP_SCENARIOS = [
    "standards_current",
    "yiigle_zhjyyxzz",
    "yiigle_zhsjkzz",
    "cma_lab_management",
    "pubmed_literature",
    "pmc_fulltext",
    "openalex_literature",
    "wiley_alz",
    "yiigle_fulltext",
]

DELIVERY_BROWSER_SCENARIOS = [
    "cmde_regulatory",
    "patenthub_patents",
]


def emit(payload: dict, as_json: bool) -> None:
    if as_json:
        try:
            typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        except UnicodeEncodeError:
            typer.echo(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))
    else:
        typer.echo(payload)


def _prepare_nmpa_manual_for_action(task_dir: Path, *, action: str) -> dict:
    result = prepare_nmpa_manual_plan(task_dir)
    result["action"] = action
    append_jsonl(
        task_dir / "logs" / "events.jsonl",
        {
            "time": now_iso(),
            "event": "nmpa_manual_plan_prepared",
            "scenario_id": "nmpa_competitor",
            "action": action,
            "status": result["status"],
            "manual_phase": result["manual_phase"],
            "plan_path": result["plan_path"],
        },
    )
    return result


def _supports_kwarg(func, name: str) -> bool:
    try:
        return name in inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False


def _record_life_science_plan_if_required(task_dir: Path, state) -> dict:
    task_payload = state.model_dump(mode="json")
    if not requires_life_science_research(task_payload):
        return {"required": False}
    scenario = state.scenario_statuses.get("life_science_research")
    if scenario and scenario.material_count > 0 and scenario.status == "completed":
        return {"required": True, "status": "completed"}
    plan = build_life_science_research_plan(task_payload)
    plan_dir = task_dir / "staging" / "life_science_research"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / "external_plugin_query_plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    if scenario is not None:
        scenario.status = "needs_manual_review"
        scenario.last_message = (
            "课题涉及标志物/蛋白/机制/临床或公共科学数据库证据，"
            "需要通过 life-science-research 插件执行外部查询并导入材料管线。"
            f"插件查询计划已生成：{plan_path.relative_to(task_dir)}。"
        )
    return {"required": True, "status": "needs_manual_review", "plan_path": str(plan_path)}


def _life_science_first_gate(
    task_dir: Path,
    state,
    *,
    action: str,
    json_output: bool,
) -> dict:
    status = _record_life_science_plan_if_required(task_dir, state)
    save_task(state)
    if status.get("required") and status.get("status") != "completed":
        emit(
            {
                "status": "needs_life_science_research",
                "action": action,
                "life_science_plan": status,
                "message_zh": (
                    "当前课题需要先完成 life-science-research 外部科学数据库证据。"
                    "请先按计划调用插件查询并通过 import-life-science-findings 导入材料，"
                    "达到默认覆盖后再继续运行标准采集流水线。"
                ),
            },
            json_output,
        )
        raise typer.Exit(code=2)
    return status


def _defer_inapplicable_scenarios(state, plans_by_scenario: dict) -> list[str]:
    deferred: list[str] = []
    for scenario in SCENARIO_COLLECTORS:
        if scenario == "local_import" or scenario in plans_by_scenario:
            continue
        if scenario not in state.scenario_statuses:
            continue
        state.scenario_statuses[scenario].status = "deferred"
        state.scenario_statuses[scenario].last_message = scenario_exclusion_message(
            state,
            scenario,
        )
        deferred.append(scenario)
    return deferred


def _record_scenario_result(task_dir: Path, state, scenario: str, result) -> None:
    materials = [
        material if isinstance(material, Material) else Material.model_validate(material)
        for material in getattr(result, "materials", [])
    ]
    recorded_materials = record_materials(task_dir, materials)
    if scenario in state.scenario_statuses:
        state.scenario_statuses[scenario].status = result.status
        state.scenario_statuses[scenario].material_count += len(recorded_materials)
        if result.status != "completed":
            state.scenario_statuses[scenario].failure_count += 1
        state.scenario_statuses[scenario].last_message = result.message_zh


def _should_retry_scenario_result(result) -> bool:
    return not getattr(result, "materials", []) and result.status in {
        "no_results",
        "no_valid_materials",
        "collection_failed",
    }


def _attempt_log_row(plan, result) -> dict:
    return {
        "query_role": plan.params.get("query_role", ""),
        "query": plan.query,
        "retmax": plan.params.get("retmax", ""),
        "literature_profile": plan.params.get("literature_profile", ""),
        "status": result.status,
        "material_count": len(result.materials),
        "message_zh": result.message_zh,
    }


def _merge_plan_results(results: list[ScenarioResult], attempts: list[dict]) -> ScenarioResult | None:
    if not results:
        return None
    materials: list[Material] = []
    material_result: ScenarioResult | None = None
    for result in results:
        if result.materials:
            material_result = result
            materials.extend(result.materials)
    base = material_result or results[-1]
    collection_errors = [error for result in results for error in result.collection_errors]
    message = base.message_zh
    if len(attempts) > 1:
        message = f"{message} 已按 {len(attempts)} 个检索层级重试/执行。"
    if materials:
        message = f"{message} 合计形成 {len(materials)} 条材料。"
        warning_statuses = {
            FailureType.COLLECTION_FAILED.value,
            FailureType.DOWNLOAD_FAILED.value,
            FailureType.PARSE_FAILED.value,
            FailureType.PERMISSION_REQUIRED.value,
            FailureType.NEEDS_LOGIN.value,
        }
        has_warnings = bool(collection_errors) or any(result.status in warning_statuses for result in results)
        if has_warnings:
            message = f"{message} 部分检索层级失败，结果需结合采集异常复核。"
        return ScenarioResult(
            status="completed_with_warnings" if has_warnings else "completed",
            materials=materials,
            message_zh=message,
            collection_errors=collection_errors,
        )
    return ScenarioResult(
        status=base.status,
        materials=[],
        failure_type=base.failure_type,
        message_zh=message,
        collection_errors=collection_errors,
    )


def _collect_scenario_plans(
    *,
    task_id: str,
    task_dir: Path,
    collector,
    plans,
    extra_params: dict | None = None,
) -> tuple[ScenarioResult | None, list[dict], list[dict]]:
    attempts: list[dict] = []
    results: list[ScenarioResult] = []
    collection_results: list[dict] = []
    first_material_id = next_material_id(task_dir)
    try:
        first_material_number = int(first_material_id.split("-", 1)[1])
    except (IndexError, ValueError):
        first_material_number = 1
    for offset, plan in enumerate(plans):
        material_id = f"MAT-{first_material_number + offset:06d}"
        params = {
            "material_id": material_id,
            "query": plan.query,
            **(extra_params or {}),
            **plan.params,
        }
        try:
            result = collector(task_id=task_id, task_dir=task_dir, params=params)
        except Exception as exc:
            scenario = str(params.get("scenario_id") or "")
            result = _exception_result(scenario, exc)
        results.append(result)
        collection_results.append(result.model_dump(mode="json"))
        attempts.append(_attempt_log_row(plan, result))
        if result.materials and plan.params.get("continue_after_results"):
            continue
        if not _should_retry_scenario_result(result):
            break
    return _merge_plan_results(results, attempts), attempts, collection_results


def _exception_result(scenario: str, exc: Exception) -> ScenarioResult:
    return ScenarioResult(
        status=FailureType.COLLECTION_FAILED.value,
        failure_type=FailureType.COLLECTION_FAILED,
        message_zh=f"{scenario} 采集异常：{type(exc).__name__}: {exc}",
    )


def _run_network_preflight(task_dir: Path, *, enabled: bool, action: str) -> dict:
    if not enabled:
        return {
            "status": "skipped",
            "message_zh": "已跳过网络预检；如公网来源采集异常，必须另行记录失败原因和兜底动作。",
        }
    result = run_network_doctor(timeout=8)
    append_jsonl(
        task_dir / "logs" / "events.jsonl",
        {
            "time": now_iso(),
            "event": "network_preflight",
            "action": action,
            "network_ok": result.get("ok", False),
            "probes": result.get("probes", []),
            "message_zh": result.get("message_zh", ""),
        },
    )
    return result


def _browser_launch_modes(preferred: str) -> list[str]:
    modes = []
    preferred = str(preferred or "playwright")
    for mode in [preferred, "playwright"]:
        if mode and mode not in modes:
            modes.append(mode)
    return modes


def _is_edge_missing_result(result: dict) -> bool:
    text = " ".join(
        str(result.get(key, "") or "")
        for key in ["message_zh", "blocked_reason", "fallback_reason", "final_url"]
    )
    return "Microsoft Edge executable was not found" in text or "未找到 Edge" in text


def _record_browser_result(task_dir: Path, state, scenario: str, result: dict) -> None:
    materials = [
        material if isinstance(material, Material) else Material.model_validate(material)
        for material in result.get("materials", [])
    ]
    recorded_materials = record_materials(task_dir, materials)
    if scenario in state.scenario_statuses:
        state.scenario_statuses[scenario].status = result["status"]
        state.scenario_statuses[scenario].material_count += len(recorded_materials)
        if result["status"] not in {"completed", "search_results"}:
            state.scenario_statuses[scenario].failure_count += 1
        state.scenario_statuses[scenario].last_message = result["message_zh"]
    append_jsonl(
        task_dir / "logs" / "events.jsonl",
        {
            "time": now_iso(),
            "event": "delivery_browser_workflow_ran",
            "scenario_id": scenario,
            "status": result["status"],
            "query_role": result.get("query_role", ""),
            "attempted_query": result.get("attempted_query", ""),
            "material_count": len(materials),
            "target_url": result["target_url"],
            "final_url": result["final_url"],
            "snapshot_paths": result["snapshot_paths"],
            "message_zh": result["message_zh"],
        },
    )


@app.command("init-task")
def init_task_command(
    topic: str = typer.Option(..., "--topic"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    state = init_task(topic=topic, output_root=root)
    emit(
        {"task_id": state.task_id, "topic": state.topic, "task_dir": state.task_dir},
        json_output,
    )


@app.command("show-status")
def show_status_command(
    task_id: str = typer.Option(..., "--task-id"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    state = load_task(task_dir)
    emit(status_payload(state), json_output)


@app.command("doctor")
def doctor_command(
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    profile: str = typer.Option(
        "core",
        "--profile",
        help="体检档位：core 检查基础 CLI，standard 检查标准调研环境。",
    ),
    network: bool = typer.Option(False, "--network", help="检查 PubMed/OpenAlex 等公网采集通道。"),
    strict: bool = typer.Option(False, "--strict", help="体检不通过时返回非零退出码，供安装验收使用。"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    try:
        result = run_doctor(root, include_network=network, profile=profile)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--profile") from exc
    emit(result, json_output)
    if strict and not result["ok"]:
        raise typer.Exit(code=1)


@app.command("update-confirmations")
def update_confirmations_command(
    task_id: str = typer.Option(..., "--task-id"),
    values: Optional[str] = typer.Option(None, "--values-json"),
    values_json_file: Optional[Path] = typer.Option(None, "--values-json-file"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    if bool(values) == bool(values_json_file):
        raise typer.BadParameter("Provide exactly one of --values-json or --values-json-file")
    raw_values = values
    if values_json_file is not None:
        raw_values = values_json_file.read_text(encoding="utf-8-sig")
    try:
        payload = json.loads(raw_values or "")
    except JSONDecodeError as exc:
        raise typer.BadParameter("--values-json must be a JSON object") from exc
    try:
        result = update_confirmations(task_dir, payload)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    emit(result, json_output)


@app.command("source-quality")
def source_quality_command(
    task_id: str = typer.Option(..., "--task-id"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    emit(build_source_quality_audit(task_dir), json_output)


def _read_json_object(path: Path, option_name: str) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, JSONDecodeError) as exc:
        raise typer.BadParameter(f"{option_name} 必须指向可读取的 JSON 文件") from exc
    if not isinstance(payload, dict):
        raise typer.BadParameter(f"{option_name} 的 JSON 顶层必须是对象")
    return payload


@app.command("record-research-claim")
def record_research_claim_command(
    task_id: str = typer.Option(..., "--task-id"),
    claim: Path = typer.Option(..., "--claim"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    try:
        result = record_research_claim(task_dir, _read_json_object(claim, "--claim"))
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--claim") from exc
    emit(result, json_output)


@app.command("record-evidence-conflict")
def record_evidence_conflict_command(
    task_id: str = typer.Option(..., "--task-id"),
    conflict: Path = typer.Option(..., "--conflict"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    try:
        result = record_evidence_conflict(
            task_dir,
            _read_json_object(conflict, "--conflict"),
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--conflict") from exc
    emit(result, json_output)


@app.command("record-research-iteration")
def record_research_iteration_command(
    task_id: str = typer.Option(..., "--task-id"),
    iteration: Path = typer.Option(..., "--iteration"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    try:
        result = record_research_iteration(
            task_dir,
            _read_json_object(iteration, "--iteration"),
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--iteration") from exc
    emit(result, json_output)


@app.command("set-research-policy")
def set_research_policy_command(
    task_id: str = typer.Option(..., "--task-id"),
    policy: Path = typer.Option(..., "--policy"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    try:
        validated = ResearchPolicy.model_validate(
            _read_json_object(policy, "--policy")
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--policy") from exc
    state = load_task(task_dir)
    state.research_policy = validated.model_dump(mode="json")
    save_task(state)
    append_jsonl(
        task_dir / "logs" / "events.jsonl",
        {
            "time": now_iso(),
            "event": "research_policy_updated",
            "data_classification": validated.data_classification,
            "external_provider_allowed": validated.external_provider_allowed,
            "message_zh": "研究数据边界策略已更新。",
        },
    )
    emit(state.research_policy, json_output)


@app.command("research-integrity")
def research_integrity_command(
    task_id: str = typer.Option(..., "--task-id"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    emit(build_research_integrity_audit(task_dir), json_output)


@app.command("nmpa-manual-plan")
def nmpa_manual_plan_command(
    task_id: str = typer.Option(..., "--task-id"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    state = load_task(task_dir)
    enforce_business_confirmations(
        state,
        action="nmpa_manual_plan",
        json_output=json_output,
    )
    try:
        result = _prepare_nmpa_manual_for_action(
            task_dir,
            action="nmpa_manual_plan",
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    emit(result, json_output)


@app.command("record-nmpa-manual-search")
def record_nmpa_manual_search_command(
    task_id: str = typer.Option(..., "--task-id"),
    record: Path = typer.Option(..., "--record"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    try:
        result = record_nmpa_manual_search(task_dir, record)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    emit(result, json_output)


@app.command("import-nmpa-manual")
def import_nmpa_manual_command(
    task_id: str = typer.Option(..., "--task-id"),
    manifest: Path = typer.Option(..., "--manifest"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    try:
        result = import_nmpa_manual(task_dir, manifest)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    emit(result, json_output)


@app.command("run-scenario")
def run_scenario_command(
    task_id: str = typer.Option(..., "--task-id"),
    scenario: str = typer.Option(..., "--scenario"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    state = load_task(task_dir)
    if scenario in SCENARIO_COLLECTORS and scenario != "local_import":
        enforce_public_collection_policy(
            state,
            action=f"run_scenario:{scenario}",
            json_output=json_output,
        )
        enforce_business_confirmations(
            state,
            action=f"run_scenario:{scenario}",
            json_output=json_output,
        )
    adapter = get_scenario(scenario)
    plans_by_scenario = scenario_query_plans(state)
    if scenario in SCENARIO_COLLECTORS and scenario not in plans_by_scenario:
        result = ScenarioResult(
            status="deferred",
            message_zh=scenario_exclusion_message(state, scenario),
        )
        if scenario in state.scenario_statuses:
            state.scenario_statuses[scenario].status = result.status
            state.scenario_statuses[scenario].last_message = result.message_zh
            save_task(state)
        emit(result.model_dump(mode="json"), json_output)
        return
    if scenario == "nmpa_competitor":
        result = _prepare_nmpa_manual_for_action(
            task_dir,
            action="run_scenario:nmpa_competitor",
        )
        emit(result, json_output)
        return
    plans = plans_by_scenario.get(scenario) or default_query_plan(state)
    collector = SCENARIO_COLLECTORS.get(scenario)
    runner = collector or adapter.run
    result, attempts, _collection_results = _collect_scenario_plans(
        task_id=task_id,
        task_dir=task_dir,
        collector=runner,
        plans=plans,
        extra_params={"scenario_id": scenario},
    )

    if result is None:
        result = ScenarioResult(
            status="needs_manual_review",
            failure_type=FailureType.NEEDS_MANUAL_REVIEW,
            message_zh=f"{adapter.label_zh} 未生成可执行检索计划。",
        )
    _record_scenario_result(task_dir, state, scenario, result)
    append_jsonl(
        task_dir / "logs" / "events.jsonl",
        {
            "time": now_iso(),
            "event": "scenario_query_attempts",
            "scenario_id": scenario,
            "attempts": attempts,
        },
    )
    save_task(state)
    emit(result.model_dump(mode="json"), json_output)


@app.command("import-finding")
def import_finding_command(
    task_id: str = typer.Option(..., "--task-id"),
    title: str = typer.Option(..., "--title"),
    source: str = typer.Option("web_search", "--source"),
    source_url: str = typer.Option("", "--source-url"),
    content: Optional[str] = typer.Option(None, "--content"),
    content_file: Optional[Path] = typer.Option(None, "--content-file"),
    material_type: str = typer.Option("", "--material-type"),
    search_query: str = typer.Option("", "--search-query"),
    identifier: str = typer.Option("", "--identifier"),
    publication_date: str = typer.Option("", "--publication-date"),
    retrieval_kind: str = typer.Option(
        "",
        "--retrieval-kind",
        help="证据取得方式：search_result、fetched_page 或 supplied_document。",
    ),
    content_verified: Optional[bool] = typer.Option(
        None,
        "--content-verified/--content-unverified",
        help="是否已读取并核验底层正文；搜索摘要不得标记为已核验。",
    ),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Import an external finding (web search, Jina Reader, etc.) into the
    material pipeline so it contributes to evidence cards and reports."""
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    if bool(content) == bool(content_file):
        raise typer.BadParameter("Provide exactly one of --content or --content-file")
    body = content or ""
    if content_file is not None:
        body = content_file.read_text(encoding="utf-8", errors="ignore")
    result = import_finding(
        task_dir,
        title=title,
        source=source,
        source_url=source_url,
        content=body,
        material_type=material_type,
        search_query=search_query,
        identifier=identifier,
        publication_date=publication_date,
        retrieval_kind=retrieval_kind,
        content_verified=content_verified,
    )
    emit(result, json_output)


@app.command("import-local")
def import_local_command(
    task_id: str = typer.Option(..., "--task-id"),
    path: Path = typer.Option(..., "--path"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    try:
        result = import_local(task_id, task_dir, path)
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc
    emit(result, json_output)


@app.command("import-life-science-findings")
def import_life_science_findings_command(
    task_id: str = typer.Option(..., "--task-id"),
    findings_json_file: Path = typer.Option(..., "--findings-json-file"),
    query: str = typer.Option("", "--query"),
    skill_name: str = typer.Option("life-science-research:research-router-skill", "--skill-name"),
    plugin_name: str = typer.Option("life-science-research", "--plugin-name"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    state = load_task(task_dir)
    enforce_public_collection_policy(
        state,
        action="import_life_science_findings",
        json_output=json_output,
    )
    try:
        payload = json.loads(findings_json_file.read_text(encoding="utf-8-sig"))
    except JSONDecodeError as exc:
        raise typer.BadParameter("--findings-json-file must contain JSON") from exc
    findings = payload.get("findings", payload) if isinstance(payload, dict) else payload
    if not isinstance(findings, list):
        raise typer.BadParameter("life-science findings JSON must be a list or {'findings': [...]}.")
    result = import_life_science_findings(
        task_id,
        task_dir,
        findings,
        query=query,
        skill_name=skill_name,
        plugin_name=plugin_name,
    )
    emit(result, json_output)


@app.command("life-science-plan")
def life_science_plan_command(
    task_id: str = typer.Option(..., "--task-id"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    state = load_task(task_dir)
    status = _record_life_science_plan_if_required(task_dir, state)
    save_task(state)
    emit(
        {
            **status,
            "plan": build_life_science_research_plan(state.model_dump(mode="json"))
            if status.get("required")
            else None,
        },
        json_output,
    )


@app.command("source-sites")
def source_sites_command(
    output: Optional[Path] = typer.Option(None, "--output"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    if output:
        export_source_sites(output)
        emit({"source_sites_path": str(output), "count": len(all_source_sites())}, json_output)
        return
    emit(
        {"source_sites": [site.model_dump(mode="json") for site in all_source_sites()]},
        json_output,
    )


@app.command("import-literature-table")
def import_literature_table_command(
    task_id: str = typer.Option(..., "--task-id"),
    path: Path = typer.Option(..., "--path"),
    source: str = typer.Option("csv_literature_import", "--source"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    try:
        result = import_literature_table(task_dir, path, source=source)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    emit(result, json_output)


@app.command("build-knowledge")
def build_knowledge_command(
    task_id: str = typer.Option(..., "--task-id"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    emit(build_literature_knowledge(task_dir), json_output)


@app.command("create-analysis-requests")
def create_analysis_requests_command(
    task_id: str = typer.Option(..., "--task-id"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    emit(create_analysis_requests(task_dir), json_output)


@app.command("validate-staged")
def validate_staged_command(
    task_id: str = typer.Option(..., "--task-id"),
    staged_type: str = typer.Option(..., "--type"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    if staged_type == "evidence-card":
        emit(validate_staged_evidence(task_dir), json_output)
    elif staged_type == "report-section":
        emit(validate_staged_report_sections(task_dir), json_output)
    else:
        raise typer.BadParameter("Supported types: evidence-card, report-section.")


@app.command("commit-staged")
def commit_staged_command(
    task_id: str = typer.Option(..., "--task-id"),
    staged_type: str = typer.Option(..., "--type"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    if staged_type == "evidence-card":
        emit(commit_staged_evidence(task_dir), json_output)
    elif staged_type == "report-section":
        emit(commit_staged_report_sections(task_dir), json_output)
    else:
        raise typer.BadParameter("Supported types: evidence-card, report-section.")


@app.command("generate-evidence-cards")
def generate_evidence_cards_command(
    task_id: str = typer.Option(..., "--task-id"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    emit(generate_draft_evidence_cards(task_dir), json_output)


@app.command("export-review")
def export_review_command(
    task_id: str = typer.Option(..., "--task-id"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    emit(export_review(task_dir), json_output)


@app.command("import-review")
def import_review_command(
    task_id: str = typer.Option(..., "--task-id"),
    xlsx: Path = typer.Option(..., "--xlsx"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    try:
        result = import_review(task_dir, xlsx)
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc
    emit(result, json_output)


@app.command("build-report")
def build_report_command(
    task_id: str = typer.Option(..., "--task-id"),
    report_type: str = typer.Option(..., "--type"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    try:
        result = build_report(task_dir, report_type)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    emit(result, json_output)


@app.command("translate-materials")
def translate_materials_command(
    task_id: str = typer.Option(..., "--task-id"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    limit: int = typer.Option(0, "--limit", help="最多新增翻译段落数；0 表示不限。"),
    force: bool = typer.Option(False, "--force", help="重新生成已有缓存翻译。"),
    provider: str = typer.Option("", "--provider", help="翻译引擎：auto / argos / libretranslate / openai。默认 auto。"),
    model: str = typer.Option("", "--model", help="OpenAI-compatible 云端兜底模型名。"),
    base_url: str = typer.Option("", "--base-url", help="LibreTranslate 或 OpenAI-compatible 服务地址。"),
    api_key: str = typer.Option("", "--api-key", help="临时服务密钥；仅用于企业内网或管理员统一模型网关，避免写入命令历史。"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    result = translate_materials(
        task_dir,
        limit=limit,
        force=force,
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
    )
    result["translation_capability"] = translation_status(task_dir)
    emit(result, json_output)


@app.command("translation-status")
def translation_status_command(
    task_id: str = typer.Option(..., "--task-id"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    emit(translation_status(task_dir), json_output)


@app.command("setup-translation-engine")
def setup_translation_engine_command(
    provider: str = typer.Option("argos", "--provider", help="安装/检查翻译引擎；当前自动安装支持 argos。"),
    skip_model: bool = typer.Option(False, "--skip-model", help="只安装 Python 依赖，不自动下载/安装离线模型。"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    emit(setup_translation_engine(provider=provider, install_model=not skip_model), json_output)


@app.command("verify-package")
def verify_package_command(
    task_id: str = typer.Option(..., "--task-id"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    emit(verify_package(task_dir), json_output)


@app.command("build-standard-delivery")
def build_standard_delivery_command(
    task_id: str = typer.Option(..., "--task-id"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    state = load_task(task_dir)
    missing_confirmations = missing_business_confirmations(state)
    if missing_confirmations:
        append_jsonl(
            task_dir / "logs" / "events.jsonl",
            {
                "time": now_iso(),
                "event": "standard_delivery_built_with_missing_confirmations",
                "missing_confirmations": missing_confirmations,
                "message_zh": "标准交付目录在检索画像未完整确认时生成，只能作为草稿。",
            },
        )
    emit(build_standard_delivery(task_dir), json_output)


@app.command("package-task")
def package_task_command(
    task_id: str = typer.Option(..., "--task-id"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    emit(package_task(task_dir), json_output)


@app.command("run-full-pipeline")
def run_full_pipeline_command(
    task_id: str = typer.Option(..., "--task-id"),
    skip_collection: bool = typer.Option(False, "--skip-collection"),
    network_preflight: bool = typer.Option(True, "--network-preflight/--skip-network-preflight"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    state = load_task(task_dir)
    collection_results = []
    if not skip_collection:
        enforce_public_collection_policy(
            state,
            action="run_full_pipeline",
            json_output=json_output,
        )
        enforce_business_confirmations(
            state,
            action="run_full_pipeline",
            json_output=json_output,
        )
        life_science_plan = _life_science_first_gate(
            task_dir,
            state,
            action="run_full_pipeline",
            json_output=json_output,
        )
    else:
        life_science_plan = _life_science_first_gate(
            task_dir,
            state,
            action="run_full_pipeline:skip_collection",
            json_output=json_output,
        )
    network_status = {}
    if not skip_collection:
        network_status = _run_network_preflight(
            task_dir,
            enabled=network_preflight,
            action="run_full_pipeline",
        )
    if not skip_collection:
        plans_by_scenario = scenario_query_plans(state)
        _defer_inapplicable_scenarios(state, plans_by_scenario)
        save_task(state)
        for scenario, collector in SCENARIO_COLLECTORS.items():
            if scenario == "local_import" or scenario not in plans_by_scenario:
                continue
            if scenario == "nmpa_competitor":
                manual_result = _prepare_nmpa_manual_for_action(
                    task_dir,
                    action="run_full_pipeline",
                )
                collection_results.append(manual_result)
                state = load_task(task_dir)
                continue
            result, attempts, raw_results = _collect_scenario_plans(
                task_id=task_id,
                task_dir=task_dir,
                collector=collector,
                plans=plans_by_scenario.get(scenario) or default_query_plan(state),
                extra_params={"scenario_id": scenario},
            )
            collection_results.extend(raw_results)
            if attempts:
                append_jsonl(
                    task_dir / "logs" / "events.jsonl",
                    {
                        "time": now_iso(),
                        "event": "scenario_query_attempts",
                        "scenario_id": scenario,
                        "action": "run_full_pipeline",
                        "attempts": attempts,
                    },
                )
            if result is None:
                continue
            _record_scenario_result(task_dir, state, scenario, result)
            save_task(state)
        save_task(state)
    else:
        save_task(state)

    evidence_result = generate_draft_evidence_cards(task_dir)
    knowledge_result = build_literature_knowledge(task_dir)
    review_result = export_review(task_dir)
    materials_report = build_report(task_dir, "materials")
    feasibility_report = build_report(task_dir, "feasibility")
    standard_delivery = build_standard_delivery(task_dir)
    verification = verify_package(task_dir)
    emit(
        {
            "collection": collection_results,
            "life_science_plan": life_science_plan,
            "network_preflight": network_status,
            "evidence": evidence_result,
            "knowledge": knowledge_result,
            "review": review_result,
            "reports": {
                "materials": materials_report,
                "feasibility": feasibility_report,
            },
            "standard_delivery": standard_delivery,
            "verification": verification,
        },
        json_output,
    )


@app.command("run-delivery-pipeline")
def run_delivery_pipeline_command(
    task_id: str = typer.Option(..., "--task-id"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    browser_page_limit: int = typer.Option(5, "--browser-page-limit", min=1),
    http_page_limit: int = typer.Option(100, "--http-page-limit", min=1),
    headless: bool = typer.Option(True, "--headless/--headed"),
    launch_mode: str = typer.Option("edge-cdp", "--launch-mode"),
    profile_scope: str = typer.Option("task", "--profile-scope"),
    skip_collection: bool = typer.Option(False, "--skip-collection"),
    network_preflight: bool = typer.Option(True, "--network-preflight/--skip-network-preflight"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    state = load_task(task_dir)
    plans_by_scenario = scenario_query_plans(state)
    collection_results = []
    if not skip_collection:
        enforce_public_collection_policy(
            state,
            action="run_delivery_pipeline",
            json_output=json_output,
        )
    missing_confirmations = missing_business_confirmations(state)
    if missing_confirmations and not skip_collection:
        emit(confirmation_gate_payload(state, action="run_delivery_pipeline"), json_output)
        raise typer.Exit(code=2)
    life_science_plan = _life_science_first_gate(
        task_dir,
        state,
        action=(
            "run_delivery_pipeline:skip_collection"
            if skip_collection
            else "run_delivery_pipeline"
        ),
        json_output=json_output,
    )

    network_status = {}
    if not skip_collection:
        network_status = _run_network_preflight(
            task_dir,
            enabled=network_preflight,
            action="run_delivery_pipeline",
        )
        deferred_scenarios = _defer_inapplicable_scenarios(state, plans_by_scenario)
        save_task(state)
        for scenario in DELIVERY_MANUAL_SCENARIOS:
            if scenario not in plans_by_scenario:
                continue
            manual_result = _prepare_nmpa_manual_for_action(
                task_dir,
                action="run_delivery_pipeline",
            )
            collection_results.append(manual_result)
            state = load_task(task_dir)
        for scenario in DELIVERY_BROWSER_SCENARIOS:
            if scenario not in plans_by_scenario:
                continue
            result = None
            attempts = []
            for plan in plans_by_scenario.get(scenario) or []:
                for mode in _browser_launch_modes(launch_mode):
                    browser_kwargs = {
                        "query": plan.query,
                        "task_id": task_id,
                        "headless": headless,
                        "page_limit": browser_page_limit,
                        "methodology": str(plan.params.get("methodology", "")),
                        "launch_mode": mode,
                    }
                    if _supports_kwarg(run_browser_workflow, "profile_scope"):
                        browser_kwargs["profile_scope"] = profile_scope
                    try:
                        result = run_browser_workflow(task_dir, scenario, **browser_kwargs)
                    except Exception as exc:
                        result = _exception_result(scenario, exc).model_dump(mode="json")
                    result["query_role"] = plan.params.get("query_role", "")
                    result["attempted_query"] = plan.query
                    result["attempted_launch_mode"] = mode
                    collection_results.append(result)
                    attempts.append(
                        {
                            "query_role": plan.params.get("query_role", ""),
                            "query": plan.query,
                            "launch_mode": mode,
                            "status": result.get("status", ""),
                            "material_count": len(result.get("materials", []) or []),
                            "message_zh": result.get("message_zh", ""),
                        }
                    )
                    if not _is_edge_missing_result(result) or mode == "playwright":
                        break
                if result and (
                    result.get("materials")
                    or result.get("status") not in {"no_results", "collection_failed"}
                ):
                    break
            if result is None:
                continue
            if attempts:
                append_jsonl(
                    task_dir / "logs" / "events.jsonl",
                    {
                        "time": now_iso(),
                        "event": "scenario_query_attempts",
                        "scenario_id": scenario,
                        "action": "run_delivery_pipeline",
                        "attempts": attempts,
                    },
                )
            _record_browser_result(task_dir, state, scenario, result)
            save_task(state)

        for scenario in DELIVERY_HTTP_SCENARIOS:
            if scenario not in plans_by_scenario:
                continue
            collector = SCENARIO_COLLECTORS[scenario]
            result, attempts, raw_results = _collect_scenario_plans(
                task_id=task_id,
                task_dir=task_dir,
                collector=collector,
                plans=plans_by_scenario.get(scenario) or [],
                extra_params={"scenario_id": scenario, "page_limit": http_page_limit},
            )
            collection_results.extend(raw_results)
            if attempts:
                append_jsonl(
                    task_dir / "logs" / "events.jsonl",
                    {
                        "time": now_iso(),
                        "event": "scenario_query_attempts",
                        "scenario_id": scenario,
                        "action": "run_delivery_pipeline",
                        "attempts": attempts,
                    },
                )
            if result is not None:
                _record_scenario_result(task_dir, state, scenario, result)
                save_task(state)

        save_task(state)
    else:
        deferred_scenarios = []
        save_task(state)

    evidence_result = generate_draft_evidence_cards(task_dir)
    knowledge_result = build_literature_knowledge(task_dir)
    analysis_requests = create_analysis_requests(task_dir)
    review_result = export_review(task_dir)
    materials_report = build_report(task_dir, "materials")
    feasibility_report = build_report(task_dir, "feasibility")
    standard_delivery = build_standard_delivery(task_dir)
    verification = verify_package(task_dir)
    emit(
        {
            "collection": collection_results,
            "life_science_plan": life_science_plan,
            "network_preflight": network_status,
            "evidence": evidence_result,
            "knowledge": knowledge_result,
            "analysis_requests": analysis_requests,
            "review": review_result,
            "reports": {
                "materials": materials_report,
                "feasibility": feasibility_report,
            },
            "standard_delivery": standard_delivery,
            "verification": verification,
            "deferred": deferred_scenarios,
        },
        json_output,
    )


@app.command("site-profile")
def site_profile_command(
    scenario: str = typer.Option(..., "--scenario"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    try:
        result = site_profile(scenario)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    emit(result, json_output)


@app.command("record-site-observation")
def record_site_observation_command(
    task_id: str = typer.Option(..., "--task-id"),
    scenario: str = typer.Option(..., "--scenario"),
    observation_json: str = typer.Option(..., "--observation-json"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    try:
        observation = json.loads(observation_json)
        if not isinstance(observation, dict):
            raise ValueError("--observation-json must be a JSON object")
        result = record_site_observation(task_dir, scenario, observation)
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    emit(result, json_output)


@app.command("browser-workflow")
def browser_workflow_command(
    scenario: str = typer.Option(..., "--scenario"),
    query: str = typer.Option("", "--query"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    try:
        workflow = browser_workflow(scenario)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    result = {
        **workflow,
        "resolved_search_url": search_url_for_workflow(workflow, query) if query else workflow["entry_url"],
        "user_action_required_zh": (
            "需要用户在 Playwright 可见浏览器中完成登录或验证。"
            if workflow.get("requires_user_login") or workflow.get("requires_persistent_session")
            else "通常无需用户登录，但遇到验证或权限限制时必须由用户处理。"
        ),
    }
    emit(result, json_output)


@app.command("prepare-browser-session")
def prepare_browser_session_command(
    task_id: str = typer.Option(..., "--task-id"),
    scenario: str = typer.Option(..., "--scenario"),
    profile_scope: str = typer.Option("user", "--profile-scope"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    try:
        result = prepare_browser_session(task_dir, scenario, profile_scope=profile_scope)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    emit(result, json_output)


@app.command("open-browser-session")
def open_browser_session_command(
    task_id: str = typer.Option(..., "--task-id"),
    scenario: str = typer.Option(..., "--scenario"),
    url: Optional[str] = typer.Option(None, "--url"),
    headless: bool = typer.Option(False, "--headless"),
    background: bool = typer.Option(False, "--background"),
    profile_scope: str = typer.Option("user", "--profile-scope"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    try:
        result = open_browser_session(
            task_dir,
            scenario,
            url=url,
            headless=headless,
            background=background,
            profile_scope=profile_scope,
        )
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    emit(result, json_output)


@app.command("probe-browser-workflow")
def probe_browser_workflow_command(
    task_id: str = typer.Option(..., "--task-id"),
    scenario: str = typer.Option(..., "--scenario"),
    query: str = typer.Option(..., "--query"),
    headless: bool = typer.Option(True, "--headless/--headed"),
    profile_scope: str = typer.Option("user", "--profile-scope"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    state = load_task(task_dir)
    enforce_public_collection_policy(
        state,
        action=f"probe_browser_workflow:{scenario}",
        json_output=json_output,
    )
    try:
        result = probe_browser_workflow(
            task_dir,
            scenario,
            query=query,
            headless=headless,
            profile_scope=profile_scope,
        )
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    emit(result, json_output)


@app.command("run-browser-workflow")
def run_browser_workflow_command(
    task_id: str = typer.Option(..., "--task-id"),
    scenario: str = typer.Option(..., "--scenario"),
    query: str = typer.Option(..., "--query"),
    methodology: str = typer.Option("", "--methodology"),
    headless: bool = typer.Option(True, "--headless/--headed"),
    page_limit: int = typer.Option(1, "--page-limit", min=1),
    launch_mode: str = typer.Option("playwright", "--launch-mode"),
    profile_scope: str = typer.Option("user", "--profile-scope"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    state = load_task(task_dir)
    enforce_public_collection_policy(
        state,
        action=f"run_browser_workflow:{scenario}",
        json_output=json_output,
    )
    try:
        browser_kwargs = {
            "query": query,
            "task_id": task_id,
            "headless": headless,
            "page_limit": page_limit,
            "methodology": methodology,
            "launch_mode": launch_mode,
        }
        if _supports_kwarg(run_browser_workflow, "profile_scope"):
            browser_kwargs["profile_scope"] = profile_scope
        result = run_browser_workflow(task_dir, scenario, **browser_kwargs)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if scenario in state.scenario_statuses:
        materials = [
            material if isinstance(material, Material) else Material.model_validate(material)
            for material in result.get("materials", [])
        ]
        recorded_materials = record_materials(task_dir, materials)
        state.scenario_statuses[scenario].status = result["status"]
        state.scenario_statuses[scenario].material_count += len(recorded_materials)
        state.scenario_statuses[scenario].last_message = result["message_zh"]
        if result["status"] not in {"completed", "search_results"}:
            state.scenario_statuses[scenario].failure_count += 1
    save_task(state)
    append_jsonl(
        task_dir / "logs" / "events.jsonl",
        {
            "time": now_iso(),
            "event": "browser_workflow_ran",
            "scenario_id": scenario,
            "status": result["status"],
            "target_url": result["target_url"],
            "final_url": result["final_url"],
            "snapshot_paths": result["snapshot_paths"],
            "message_zh": result["message_zh"],
        },
    )
    emit(result, json_output)


@app.command("scout-browser-workflow")
def scout_browser_workflow_command(
    task_id: str = typer.Option(..., "--task-id"),
    scenario: str = typer.Option(..., "--scenario"),
    query: str = typer.Option(..., "--query"),
    headless: bool = typer.Option(True, "--headless/--headed"),
    page_limit: int = typer.Option(1, "--page-limit", min=1),
    launch_mode: str = typer.Option("playwright", "--launch-mode"),
    profile_scope: str = typer.Option("user", "--profile-scope"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    root = output_root or default_output_root()
    task_dir = find_task(root, task_id)
    state = load_task(task_dir)
    enforce_public_collection_policy(
        state,
        action=f"scout_browser_workflow:{scenario}",
        json_output=json_output,
    )
    try:
        scout_kwargs = {
            "query": query,
            "headless": headless,
            "page_limit": page_limit,
            "launch_mode": launch_mode,
        }
        if _supports_kwarg(scout_browser_workflow, "profile_scope"):
            scout_kwargs["profile_scope"] = profile_scope
        result = scout_browser_workflow(task_dir, scenario, **scout_kwargs)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    append_jsonl(
        task_dir / "logs" / "events.jsonl",
        {
            "time": now_iso(),
            "event": "browser_workflow_scouted",
            "scenario_id": scenario,
            "status": result["status"],
            "target_url": result["target_url"],
            "final_url": result["final_url"],
            "snapshot_paths": result["snapshot_paths"],
            "message_zh": result["message_zh"],
        },
    )
    emit(result, json_output)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
