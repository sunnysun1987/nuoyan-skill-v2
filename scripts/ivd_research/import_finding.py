"""Import external research findings (web search, Jina Reader, etc.) into the
material pipeline so they contribute to evidence cards, Excel review, and reports.

This fills the critical gap where agent-side web search results were invisible
to the CLI-driven HTML/Excel/zip pipeline.
"""

from pathlib import Path
from typing import Any

from .jsonl import append_jsonl, read_json, read_jsonl
from .models import ManualCollectionState, Material
from .status import (
    find_duplicate_material,
    load_task,
    next_material_id,
    now_iso,
    record_materials,
    save_task,
)


MATERIAL_TYPE_LABELS = {
    "regulatory": "监管资料",
    "competitor": "竞品注册",
    "standard": "现行标准",
    "patent": "专利",
    "literature": "文献",
    "local_import": "本地导入",
    "unknown": "未确认",
}


def _infer_material_type(title: str, content: str, hint: str) -> str:
    """Guess material type from available signals."""
    haystack = f"{title} {content[:500]}".lower()
    if hint and hint in MATERIAL_TYPE_LABELS:
        return hint
    type_signals = [
        ("regulatory", ["指导原则", "审评报告", "征求意见", "cmde", "注册审查", "技术审查"]),
        ("competitor", ["注册证", "nmpa", "医疗器械注册", "境内医疗器械", "进口医疗器械", "国械注准"]),
        ("standard", ["标准", "gb/t", "yy/t", "gb ", "yy ", "db", "ics", "ccs"]),
        ("patent", ["专利", "公开号", "cn", "us", "权利要求", "说明书"]),
        ("literature", ["doi", "文献", "期刊", "论文", "杂志", "指南", "共识", "meta分析"]),
    ]
    for mtype, signals in type_signals:
        if any(s in haystack for s in signals):
            return mtype
    return "literature"


def _update_scenario_after_import(
    task_dir: Path,
    task_payload: dict[str, Any],
    *,
    source: str,
) -> str:
    if not isinstance(task_payload.get("scenario_statuses"), dict):
        return ""
    state = load_task(task_dir)
    scenario = state.scenario_statuses.get(source)
    if scenario is None:
        return ""
    scenario.material_count = sum(
        1
        for material in read_jsonl(task_dir / "data" / "materials.jsonl")
        if material.get("source_scenario") == source
    )
    if source == "nmpa_competitor":
        if scenario.manual_collection is None:
            scenario.manual_collection = ManualCollectionState(
                phase="not_started",
                last_updated=now_iso(),
            )
        if scenario.manual_collection.phase == "verified_no_results":
            scenario.manual_collection.phase = "awaiting_import"
            scenario.manual_collection.zero_results_verified = False
            scenario.manual_collection.last_updated = now_iso()
        closed_positive_phases = {"completed", "completed_with_warnings"}
        if scenario.manual_collection.phase not in closed_positive_phases:
            scenario.status = "needs_manual_review"
            scenario.last_message = (
                "通用 import-finding 导入的 NMPA 材料仅作为待复核线索；"
                "仍须完成 NMPA 专用人工导入、可见证据核验和计划覆盖，才能关闭信源。"
            )
    else:
        scenario.status = "completed"
        scenario.last_message = (
            f"通过 {source} 可见官方页面/外部发现导入材料，"
            f"当前累计 {scenario.material_count} 条。"
        )
    save_task(state)
    append_jsonl(
        task_dir / "logs" / "events.jsonl",
        {
            "time": now_iso(),
            "event": "import_finding_scenario_updated",
            "scenario_id": source,
            "status": scenario.status,
            "material_count": scenario.material_count,
        },
    )
    return scenario.status


def import_finding(
    task_dir: Path,
    *,
    title: str,
    source: str = "web_search",
    source_url: str = "",
    content: str,
    material_type: str = "",
    taxonomy_tags: list[str] | None = None,
    identifier: str = "",
    publication_date: str = "",
    evidence_strength: str = "needs_review",
    search_query: str = "",
    extra_raw_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Import a single external finding as a material record.

    Returns a dict with material_id and relative_paths for downstream use.
    """
    material_type = _infer_material_type(title, content, material_type)
    task = read_json(task_dir / "task.json")
    task_id = str(task.get("task_id") or "")
    duplicate_keys = [
        f"identifier:{identifier.strip().lower()}"
        if identifier.strip()
        else f"source-title:{source_url.strip().lower()}|{' '.join(title.lower().split())}"
    ]
    duplicate = find_duplicate_material(
        task_dir,
        {
            "source_scenario": source,
            "source_url": source_url,
            "title": title,
            "possible_duplicate_keys": duplicate_keys,
        },
    )
    if duplicate:
        scenario_status = _update_scenario_after_import(
            task_dir,
            task,
            source=source,
        )
        return {
            "material_id": duplicate.get("material_id", ""),
            "material_type": duplicate.get("material_type", material_type),
            "extracted_text_path": duplicate.get("extracted_text_path", ""),
            "taxonomy_tags": taxonomy_tags or [],
            "evidence_strength": evidence_strength,
            "recorded": False,
            "scenario_status": scenario_status,
        }
    material_id = next_material_id(task_dir)

    # Store extracted text
    text_dir = task_dir / "extracted_text" / material_type
    text_dir.mkdir(parents=True, exist_ok=True)
    text_path = text_dir / f"{material_id}_imported.txt"
    text_path.write_text(content, encoding="utf-8")
    relative_text = str(text_path.relative_to(task_dir))

    # Build raw fields
    raw_fields: dict[str, Any] = {
        "import_source": source,
        "summary": content[:500] if len(content) > 500 else content,
        "full_content_length": len(content),
        **(extra_raw_fields or {}),
    }
    if identifier:
        raw_fields["identifier"] = identifier
    if publication_date:
        raw_fields["publication_date"] = publication_date

    material = Material(
        material_id=material_id,
        task_id=task_id,
        source_scenario=source,
        material_type=material_type,
        title=title,
        source_url=source_url,
        search_keyword_or_query=search_query,
        collection_path={
            "scenario_id": source,
            "source_url": source_url,
            "imported_via": "import-finding CLI",
        },
        collection_time=now_iso(),
        adapter_id="import_finding",
        adapter_version="1.0.0",
        raw_fields=raw_fields,
        download_status="not_applicable",
        extracted_text_status="completed",
        extracted_text_path=relative_text,
        content_snapshot_path=relative_text,
        possible_duplicate_keys=duplicate_keys,
    )
    materials = [material]
    recorded_materials = record_materials(task_dir, materials)

    scenario_status = _update_scenario_after_import(
        task_dir,
        task,
        source=source,
    )

    return {
        "material_id": material_id,
        "material_type": material_type,
        "extracted_text_path": relative_text,
        "taxonomy_tags": taxonomy_tags or [],
        "evidence_strength": evidence_strength,
        "recorded": bool(recorded_materials),
        "scenario_status": scenario_status,
    }
