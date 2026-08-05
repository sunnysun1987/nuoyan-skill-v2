from __future__ import annotations

from pathlib import Path
import re
import shutil
from typing import Any

from .jsonl import read_jsonl, write_jsonl
from .models import DownloadFile, Material
from .nmpa_manual_contract import (
    NMPA_SEARCH_URL,
    NMPA_SOURCE_SITE,
    SCENARIO_ID,
    sha256_file,
)
from .status import (
    find_duplicate_material,
    next_material_id,
    now_iso,
    record_materials,
)


def copy_attempt_evidence(
    task_dir: Path,
    *,
    session_segment: str,
    attempt_id: str,
    source_paths: list[Path],
) -> list[DownloadFile]:
    target_dir = (
        task_dir
        / "downloads"
        / "competitors"
        / "nmpa_manual"
        / session_segment
        / attempt_id
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    copied: list[DownloadFile] = []
    for index, source in enumerate(source_paths, start=1):
        safe_name = re.sub(r"[^\w.-]+", "_", source.name, flags=re.UNICODE)
        target = target_dir / f"{index:03d}_{safe_name}"
        if source != target.resolve():
            shutil.copy2(source, target)
        copied.append(
            DownloadFile(
                original_filename=source.name,
                stored_filename=target.name,
                relative_path=target.relative_to(task_dir).as_posix(),
                source_url=NMPA_SEARCH_URL,
                sha256=sha256_file(target),
                status="imported",
            )
        )
    return copied


def _merge_downloads(*groups: list[DownloadFile]) -> list[DownloadFile]:
    merged: dict[tuple[str, str], DownloadFile] = {}
    for group in groups:
        for item in group:
            merged[(item.sha256, item.relative_path)] = item
    return list(merged.values())


def _result_text(result: dict[str, Any], registration_type: str) -> str:
    labels = [
        ("注册类别", registration_type),
        ("注册证编号", result["registration_certificate_number"]),
        ("产品名称", result["product_name"]),
        ("注册人", result["registrant"]),
        ("型号规格", result.get("model", "")),
        ("适用范围", result.get("scope", "")),
        ("批准日期", result.get("approval_date", "")),
        ("有效期至", result.get("valid_until", "")),
        ("NMPA 来源", result["source_url"]),
    ]
    return "\n".join(f"{label}：{value}" for label, value in labels if value) + "\n"


def _replace_material(task_dir: Path, material: Material) -> None:
    path = task_dir / "data" / "materials.jsonl"
    rows = list(read_jsonl(path))
    for index, row in enumerate(rows):
        if str(row.get("material_id") or "") == material.material_id:
            rows[index] = material.model_dump(mode="json")
            write_jsonl(path, rows)
            return
    raise ValueError(f"无法更新材料 {material.material_id}：原记录不存在。")


def upsert_result_material(
    task_dir: Path,
    *,
    task_id: str,
    session_id: str,
    attempt: dict[str, Any],
    result: dict[str, Any],
    downloads: list[DownloadFile],
) -> tuple[str, bool]:
    registration_number = result["registration_certificate_number"]
    duplicate_key = f"registration_certificate_number:{registration_number.lower()}"
    duplicate = find_duplicate_material(
        task_dir,
        {
            "source_scenario": SCENARIO_ID,
            "possible_duplicate_keys": [duplicate_key],
        },
    )
    material_id = (
        str(duplicate.get("material_id") or "") if duplicate else next_material_id(task_dir)
    )
    existing_raw = dict(duplicate.get("raw_fields") or {}) if duplicate else {}
    existing_attempt_ids = [
        str(value) for value in existing_raw.get("manual_attempt_ids", []) if value
    ]
    attempt_ids = list(dict.fromkeys([*existing_attempt_ids, attempt["attempt_id"]]))
    existing_downloads = (
        [
            DownloadFile.model_validate(item)
            for item in (duplicate.get("download_files") or [])
        ]
        if duplicate
        else []
    )
    merged_downloads = _merge_downloads(existing_downloads, downloads)

    text_path = (
        task_dir
        / "extracted_text"
        / "competitors"
        / f"{material_id}_nmpa_manual.txt"
    )
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text(
        _result_text(result, str(attempt["registration_type"])),
        encoding="utf-8",
    )
    relative_text = text_path.relative_to(task_dir).as_posix()
    raw_fields = {
        **existing_raw,
        **result,
        "registration_type": attempt["registration_type"],
        "manual_search_session_id": session_id,
        "manual_attempt_ids": attempt_ids,
        "manual_evidence_files": [
            item.model_dump(mode="json") for item in merged_downloads
        ],
        "query": attempt["query"],
        "query_role": attempt["query_role"],
        "source_name": NMPA_SOURCE_SITE.display_name,
    }
    material = Material(
        material_id=material_id,
        task_id=task_id,
        source_scenario=SCENARIO_ID,
        material_type="competitor",
        title=result["product_name"],
        source_url=result["source_url"],
        search_keyword_or_query=attempt["query"],
        collection_path={
            "scenario_id": SCENARIO_ID,
            "official_search_url": NMPA_SEARCH_URL,
            "registration_type": attempt["registration_type"],
            "search_session_id": session_id,
            "manual_attempt_ids": attempt_ids,
            "imported_via": "nmpa_manual_import",
        },
        collection_time=now_iso(),
        adapter_id="nmpa_manual_import",
        adapter_version="1.0.0",
        raw_fields=raw_fields,
        download_status="imported",
        download_files=merged_downloads,
        extracted_text_status="completed",
        extracted_text_path=relative_text,
        content_snapshot_path=relative_text,
        possible_duplicate_keys=[duplicate_key],
        source_site_id=SCENARIO_ID,
        source_name=NMPA_SOURCE_SITE.display_name,
        evidence_lane="competitor_registration",
        source_run_id=session_id,
    )
    if duplicate:
        _replace_material(task_dir, material)
        return material_id, False
    recorded = record_materials(task_dir, [material])
    return material_id, bool(recorded)
