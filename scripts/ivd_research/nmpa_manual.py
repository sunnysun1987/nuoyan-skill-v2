from __future__ import annotations

from pathlib import Path
from typing import Any

from .evidence import generate_draft_evidence_cards
from .jsonl import append_jsonl, read_json, read_jsonl, write_json
from .models import FailureType, ManualCollectionState, ScenarioStatus
from .nmpa_manual_contract import (
    IMPORT_MANIFESTS_DIR,
    IMPORT_MANIFEST_TEMPLATE_PATH,
    NMPA_SEARCH_URL,
    PLAN_MARKDOWN_PATH,
    PLAN_PATH,
    SCENARIO_ID,
    SEARCH_RECORDS_DIR,
    SEARCH_RECORD_TEMPLATE_PATH,
    attempt_id as _attempt_id,
    attempt_signature as _attempt_signature,
    load_plan as _load_plan,
    plan_attempt_map as _plan_attempt_map,
    record_evidence_signature as _record_evidence_signature,
    registration_types as _registration_types,
    reject_sensitive_fields as _reject_sensitive_fields,
    safe_path_segment as _safe_path_segment,
    sha256_file as _sha256_file,
    validate_manifest_attempt as _validate_manifest_attempt,
    validate_record_attempt as _validate_record_attempt,
    validate_task_id as _validate_task_id,
)
from .nmpa_manual_materials import (
    copy_attempt_evidence as _copy_attempt_evidence,
    upsert_result_material as _upsert_result_material,
)
from .query_plan import scenario_query_plans
from .scenarios.base import ScenarioResult
from .status import load_task, now_iso, save_task


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




def _search_record_path(session_segment: str) -> Path:
    return SEARCH_RECORDS_DIR / f"{session_segment}.json"


def _manifest_path(session_segment: str) -> Path:
    return IMPORT_MANIFESTS_DIR / f"{session_segment}.json"


def record_nmpa_manual_search(task_dir: Path, record_path: Path) -> dict[str, Any]:
    task_dir = Path(task_dir)
    record_path = Path(record_path)
    payload = read_json(record_path)
    _reject_sensitive_fields(payload)
    state = load_task(task_dir)
    _validate_task_id(payload, state.task_id)
    if payload.get("operator_confirmed") is not True:
        raise ValueError("operator_confirmed 必须为 true，才能记录人工检索。")

    scenario = state.scenario_statuses.get(SCENARIO_ID)
    if scenario is None or scenario.manual_collection is None:
        raise ValueError("NMPA 人工检索状态不存在，请先生成检索计划。")
    manual = scenario.manual_collection
    session_id = str(payload.get("search_session_id") or "").strip()
    session_segment = _safe_path_segment(session_id, label="search_session_id")
    plan = _load_plan(task_dir)
    current_plan_sha256 = _sha256_file(task_dir / PLAN_PATH)
    if manual.plan_sha256 and manual.plan_sha256 != current_plan_sha256:
        raise ValueError("NMPA 检索计划校验值不一致，请重新生成检索计划。")
    manual.plan_sha256 = current_plan_sha256
    plan_attempts = _plan_attempt_map(plan)
    incoming = payload.get("attempts")
    if not isinstance(incoming, list) or not incoming:
        raise ValueError("search record 至少需要包含一个 attempt。")
    normalized = [
        _validate_record_attempt(item, plan_attempts=plan_attempts) for item in incoming
    ]
    incoming_ids = [item["attempt_id"] for item in normalized]
    if len(incoming_ids) != len(set(incoming_ids)):
        raise ValueError("search record 中存在重复 attempt_id。")

    relative_record_path = _search_record_path(session_segment)
    persisted_path = task_dir / relative_record_path
    same_record_cycle = manual.search_record_path == relative_record_path.as_posix()
    existing: dict[str, Any] = {}
    if persisted_path.exists() and same_record_cycle:
        if (
            manual.search_record_sha256
            and _sha256_file(persisted_path) != manual.search_record_sha256
        ):
            raise ValueError("已保存的 NMPA 检索记录校验值不一致，请新建检索会话。")
        existing = read_json(persisted_path)
        if str(existing.get("search_session_id") or "") != session_id:
            raise ValueError("search_session_id 与已保存的检索记录不一致。")
    existing_attempts = {
        str(item["attempt_id"]): item for item in existing.get("attempts", [])
    }
    changed_ids = [
        item["attempt_id"]
        for item in normalized
        if item["attempt_id"] in existing_attempts
        and _record_evidence_signature(item)
        != _record_evidence_signature(existing_attempts[item["attempt_id"]])
    ]
    new_ids = [
        item["attempt_id"]
        for item in normalized
        if item["attempt_id"] not in existing_attempts
    ]
    merged = dict(existing_attempts)
    merged.update({item["attempt_id"]: item for item in normalized})
    required_ids = [str(item["attempt_id"]) for item in plan["attempts"]]
    ordered_attempts = [merged[item_id] for item_id in required_ids if item_id in merged]
    persisted = {
        "schema_version": "1.0",
        "task_id": state.task_id,
        "search_session_id": session_id,
        "operator_confirmed": True,
        "recorded_at": now_iso(),
        "attempts": ordered_attempts,
    }
    write_json(persisted_path, persisted)

    previous_phase = manual.phase
    previous_status = scenario.status
    previous_message = scenario.last_message
    if not same_record_cycle:
        manual.validated_attempt_ids = []
        manual.imported_material_ids = []
        manual.manifest_path = ""
        manual.manifest_sha256 = ""
        manual.zero_results_verified = False
    elif changed_ids:
        changed_set = set(changed_ids)
        manual.validated_attempt_ids = [
            item_id
            for item_id in manual.validated_attempt_ids
            if item_id not in changed_set
        ]
        manual.zero_results_verified = False
        if manual.manifest_path:
            saved_manifest_path = task_dir / manual.manifest_path
            if saved_manifest_path.exists():
                saved_manifest = read_json(saved_manifest_path)
                saved_manifest["attempts"] = [
                    item
                    for item in saved_manifest.get("attempts", [])
                    if str(item.get("attempt_id") or "") not in changed_set
                ]
                saved_manifest["updated_at"] = now_iso()
                write_json(saved_manifest_path, saved_manifest)
                manual.manifest_sha256 = _sha256_file(saved_manifest_path)
    recorded_ids = [item["attempt_id"] for item in ordered_attempts]
    manual.required_attempt_ids = required_ids
    manual.recorded_attempt_ids = recorded_ids
    manual.search_record_path = relative_record_path.as_posix()
    manual.search_record_sha256 = _sha256_file(persisted_path)
    manual.observed_result_count = sum(
        int(item["result_count"]) for item in ordered_attempts
    )
    remaining_ids = [item_id for item_id in required_ids if item_id not in recorded_ids]
    manual.last_updated = now_iso()
    closed_phases = {"completed", "completed_with_warnings", "verified_no_results"}
    preserve_closed_state = (
        same_record_cycle
        and not new_ids
        and not changed_ids
        and previous_phase in closed_phases
    )
    if preserve_closed_state:
        manual.phase = previous_phase
        scenario.status = previous_status
        scenario.last_message = previous_message
    else:
        manual.phase = "awaiting_user_search" if remaining_ids else "awaiting_import"
        scenario.status = FailureType.NEEDS_MANUAL_REVIEW.value
        if remaining_ids:
            scenario.last_message = (
                f"NMPA 已记录 {len(recorded_ids)} 项人工检索，"
                f"仍有 {len(remaining_ids)} 项未执行；信源保持待人工处理。"
            )
        else:
            scenario.last_message = (
                "NMPA 计划内检索已全部记录，正在等待截图或官方导出及结构化结果导入；"
                "此时不得判定为无结果。"
            )
    save_task(state)
    append_jsonl(
        task_dir / "logs" / "events.jsonl",
        {
            "time": now_iso(),
            "event": "nmpa_manual_search_recorded",
            "scenario_id": SCENARIO_ID,
            "search_session_id": session_id,
            "record_path": relative_record_path.as_posix(),
            "recorded_attempt_ids": recorded_ids,
            "remaining_attempt_ids": remaining_ids,
            "invalidated_attempt_ids": changed_ids,
            "observed_result_count": manual.observed_result_count,
            "status": scenario.status,
            "manual_phase": manual.phase,
        },
    )
    return {
        "status": scenario.status,
        "manual_phase": manual.phase,
        "record_path": relative_record_path.as_posix(),
        "recorded_attempt_ids": recorded_ids,
        "remaining_attempt_ids": remaining_ids,
        "observed_result_count": manual.observed_result_count,
        "message_zh": scenario.last_message,
    }


def _manifest_warning_list(manifest: dict[str, Any]) -> list[str]:
    return list(
        dict.fromkeys(
            str(warning)
            for attempt in manifest.get("attempts", [])
            for warning in attempt.get("warnings", [])
            if str(warning)
        )
    )


def import_nmpa_manual(task_dir: Path, manifest_path: Path) -> dict[str, Any]:
    task_dir = Path(task_dir)
    manifest_path = Path(manifest_path)
    payload = read_json(manifest_path)
    _reject_sensitive_fields(payload)
    state = load_task(task_dir)
    _validate_task_id(payload, state.task_id)
    if payload.get("capture_complete") is not True:
        raise ValueError("capture_complete 必须为 true，才能导入该批人工采集结果。")

    scenario = state.scenario_statuses.get(SCENARIO_ID)
    if scenario is None or scenario.manual_collection is None:
        raise ValueError("NMPA 人工检索状态不存在，请先生成检索计划。")
    manual = scenario.manual_collection
    if not manual.search_record_path:
        raise ValueError("尚未记录 NMPA 人工检索，不能导入。")
    plan = _load_plan(task_dir)
    current_plan_sha256 = _sha256_file(task_dir / PLAN_PATH)
    if not manual.plan_sha256 or manual.plan_sha256 != current_plan_sha256:
        raise ValueError("NMPA 检索计划校验值不一致，请重新生成检索计划。")
    search_record_path = task_dir / manual.search_record_path
    if (
        not manual.search_record_sha256
        or _sha256_file(search_record_path) != manual.search_record_sha256
    ):
        raise ValueError("已保存的 NMPA 检索记录校验值不一致，请重新记录检索。")
    search_record = read_json(search_record_path)
    _reject_sensitive_fields(search_record)
    _validate_task_id(search_record, state.task_id)
    if search_record.get("operator_confirmed") is not True:
        raise ValueError("已保存的 NMPA 检索记录缺少人工确认。")
    session_id = str(payload.get("search_session_id") or "").strip()
    if session_id != str(search_record.get("search_session_id") or ""):
        raise ValueError("search_session_id 与人工检索记录不一致。")
    session_segment = _safe_path_segment(session_id, label="search_session_id")

    plan_attempts = _plan_attempt_map(plan)
    record_items = search_record.get("attempts")
    if not isinstance(record_items, list) or not record_items:
        raise ValueError("已保存的 NMPA 检索记录没有 attempts。")
    normalized_records = [
        _validate_record_attempt(item, plan_attempts=plan_attempts)
        for item in record_items
    ]
    record_ids = [item["attempt_id"] for item in normalized_records]
    if len(record_ids) != len(set(record_ids)):
        raise ValueError("已保存的 NMPA 检索记录存在重复 attempt_id。")
    if record_ids != manual.recorded_attempt_ids:
        raise ValueError("NMPA 检索记录与任务状态不一致，请重新记录检索。")
    recorded_attempts = {item["attempt_id"]: item for item in normalized_records}
    incoming = payload.get("attempts")
    if not isinstance(incoming, list) or not incoming:
        raise ValueError("import manifest 至少需要包含一个 attempt。")
    normalized = [
        _validate_manifest_attempt(
            item,
            manifest_path=manifest_path,
            plan_attempts=plan_attempts,
            recorded_attempts=recorded_attempts,
        )
        for item in incoming
    ]
    incoming_ids = [item["attempt_id"] for item in normalized]
    if len(incoming_ids) != len(set(incoming_ids)):
        raise ValueError("import manifest 中存在重复 attempt_id。")

    relative_manifest_path = _manifest_path(session_segment)
    persisted_manifest_path = task_dir / relative_manifest_path
    existing_manifest: dict[str, Any] = {}
    if (
        manual.manifest_path == relative_manifest_path.as_posix()
        and persisted_manifest_path.exists()
    ):
        if (
            not manual.manifest_sha256
            or _sha256_file(persisted_manifest_path) != manual.manifest_sha256
        ):
            raise ValueError("已保存的 NMPA 导入清单校验值不一致。")
        existing_manifest = read_json(persisted_manifest_path)
        _reject_sensitive_fields(existing_manifest)
        _validate_task_id(existing_manifest, state.task_id)
        if str(existing_manifest.get("search_session_id") or "") != session_id:
            raise ValueError("已保存的 NMPA 导入清单会话不一致。")

    normalized_manifest_attempts: list[dict[str, Any]] = []
    material_ids: list[str] = []
    new_material_count = 0
    for item in normalized:
        plan_attempt = plan_attempts[item["attempt_id"]]
        downloads = _copy_attempt_evidence(
            task_dir,
            session_segment=session_segment,
            attempt_id=item["attempt_id"],
            source_paths=item["evidence_source_paths"],
        )
        normalized_manifest_attempts.append(
            {
                "attempt_id": item["attempt_id"],
                "evidence_files": [
                    download.model_dump(mode="json") for download in downloads
                ],
                "results": item["results"],
                "warnings": item["warnings"],
                "validated_at": now_iso(),
            }
        )
        material_attempt = {
            **plan_attempt,
            "query": plan_attempt["query"],
            "query_role": plan_attempt.get("query_role", ""),
        }
        for result in item["results"]:
            material_id, created = _upsert_result_material(
                task_dir,
                task_id=state.task_id,
                session_id=session_id,
                attempt=material_attempt,
                result=result,
                downloads=downloads,
            )
            material_ids.append(material_id)
            new_material_count += int(created)

    merged_attempts = {
        str(item["attempt_id"]): item
        for item in existing_manifest.get("attempts", [])
    }
    merged_attempts.update(
        {item["attempt_id"]: item for item in normalized_manifest_attempts}
    )
    required_ids = [str(item["attempt_id"]) for item in plan["attempts"]]
    persisted_manifest = {
        "schema_version": "1.0",
        "task_id": state.task_id,
        "search_session_id": session_id,
        "capture_complete": True,
        "updated_at": now_iso(),
        "attempts": [
            merged_attempts[item_id]
            for item_id in required_ids
            if item_id in merged_attempts
        ],
    }
    write_json(persisted_manifest_path, persisted_manifest)
    manual.manifest_sha256 = _sha256_file(persisted_manifest_path)

    validated_ids = [item["attempt_id"] for item in persisted_manifest["attempts"]]
    manual.required_attempt_ids = required_ids
    manual.validated_attempt_ids = validated_ids
    manual.imported_material_ids = list(
        dict.fromkeys([*manual.imported_material_ids, *material_ids])
    )
    manual.manifest_path = relative_manifest_path.as_posix()
    manual.observed_result_count = sum(
        int(item["result_count"]) for item in recorded_attempts.values()
    )
    remaining_ids = [item_id for item_id in required_ids if item_id not in validated_ids]
    unrecorded_ids = [
        item_id for item_id in required_ids if item_id not in manual.recorded_attempt_ids
    ]
    warnings = _manifest_warning_list(persisted_manifest)
    all_zero = all(
        int(recorded_attempts[item_id]["result_count"]) == 0
        for item_id in required_ids
        if item_id in recorded_attempts
    ) and set(recorded_attempts) == set(required_ids)
    nmpa_material_count = sum(
        1
        for material in read_jsonl(task_dir / "data" / "materials.jsonl")
        if material.get("source_scenario") == SCENARIO_ID
    )

    if remaining_ids:
        scenario.status = FailureType.NEEDS_MANUAL_REVIEW.value
        manual.phase = "awaiting_user_search" if unrecorded_ids else "awaiting_import"
        manual.zero_results_verified = False
        scenario.last_message = (
            f"NMPA 已核验 {len(validated_ids)} 项导入，仍有 {len(remaining_ids)} 项未完成；"
            "信源保持待人工处理。"
        )
    elif all_zero and nmpa_material_count:
        scenario.status = FailureType.NEEDS_MANUAL_REVIEW.value
        manual.phase = "awaiting_import"
        manual.zero_results_verified = False
        scenario.last_message = (
            "NMPA 本次计划内检索均记录为零结果，但任务中已有 NMPA 正向材料线索，"
            "两者存在冲突；须复核线索或补充专用正向导入，不能关闭为无结果。"
        )
    elif all_zero:
        scenario.status = FailureType.NO_RESULTS.value
        manual.phase = "verified_no_results"
        manual.zero_results_verified = True
        scenario.last_message = (
            "NMPA 计划内全部检索均已记录为零结果，并已逐项核验截图或官方导出。"
        )
    elif warnings:
        scenario.status = "completed_with_warnings"
        manual.phase = "completed_with_warnings"
        manual.zero_results_verified = False
        scenario.last_message = (
            f"NMPA 人工导入已覆盖全部计划项，但有 {len(warnings)} 个可选字段缺失，"
            "需复核后才能满足业务就绪。"
        )
    else:
        scenario.status = "completed"
        manual.phase = "completed"
        manual.zero_results_verified = False
        scenario.last_message = "NMPA 人工检索、可见证据和结构化结果已覆盖全部计划项。"

    manual.last_updated = now_iso()
    scenario.material_count = nmpa_material_count
    evidence_result = (
        generate_draft_evidence_cards(task_dir)
        if material_ids
        else {"generated_count": 0, "committed_count": 0}
    )
    if material_ids:
        card_material_ids = {
            str(card.get("material_id") or "")
            for card in read_jsonl(task_dir / "data" / "evidence_cards.jsonl")
        }
        missing_card_ids = [
            material_id
            for material_id in dict.fromkeys(material_ids)
            if material_id not in card_material_ids
        ]
        if missing_card_ids:
            raise RuntimeError(
                "NMPA 材料已写入，但证据卡未成功落库："
                + ", ".join(missing_card_ids)
            )
    save_task(state)
    append_jsonl(
        task_dir / "logs" / "events.jsonl",
        {
            "time": now_iso(),
            "event": "nmpa_manual_import_completed",
            "scenario_id": SCENARIO_ID,
            "search_session_id": session_id,
            "manifest_path": relative_manifest_path.as_posix(),
            "validated_attempt_ids": validated_ids,
            "remaining_attempt_ids": remaining_ids,
            "material_ids": list(dict.fromkeys(material_ids)),
            "new_material_count": new_material_count,
            "warning_count": len(warnings),
            "status": scenario.status,
            "manual_phase": manual.phase,
        },
    )
    return {
        "status": scenario.status,
        "manual_phase": manual.phase,
        "manifest_path": relative_manifest_path.as_posix(),
        "validated_attempt_ids": validated_ids,
        "remaining_attempt_ids": remaining_ids,
        "material_ids": list(dict.fromkeys(material_ids)),
        "new_material_count": new_material_count,
        "warnings": warnings,
        "evidence_cards": evidence_result,
        "message_zh": scenario.last_message,
    }


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
    manual.plan_sha256 = _sha256_file(plan_path)
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
