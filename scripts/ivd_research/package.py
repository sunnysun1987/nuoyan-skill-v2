import hashlib
import json
import shutil
import zipfile
from pathlib import Path

from .jsonl import count_jsonl_rows, read_jsonl, write_json
from .nmpa_manual_contract import nmpa_manual_gate_warnings
from .paths import safe_topic
from .project_profile import (
    NETWORK_SENSITIVE_SCENARIOS,
    formal_scenarios_for,
    profile_text,
)
from .quality import build_collection_alerts
from .research_integrity import build_research_integrity_audit
from .source_quality import build_source_quality_audit
from .status import now_iso

FORMAL_SCENARIOS = [
    "cmde_regulatory",
    "nmpa_competitor",
    "standards_current",
    "patenthub_patents",
    "yiigle_zhjyyxzz",
    "cma_lab_management",
    "pubmed_literature",
    "pmc_fulltext",
    "openalex_literature",
    "yiigle_fulltext",
]

LIFE_SCIENCE_RESEARCH_SCENARIO = "life_science_research"
LIFE_SCIENCE_TRIGGER_TERMS = [
    "标志物",
    "生物标志物",
    "蛋白",
    "基因",
    "靶标",
    "机制",
    "通路",
    "网络",
    "临床试验",
    "遗传",
    "变异",
    "公共数据库",
    "biomarker",
    "protein",
    "gene",
    "target",
    "pathway",
    "mechanism",
    "clinical trial",
    "genetic",
    "variant",
]
LIFE_SCIENCE_AUTO_PRODUCT_TERMS = [
    "ivd",
    "体外诊断",
    "检测试剂盒",
    "测定试剂盒",
    "检测试剂",
    "测定试剂",
    "检测项目",
    "检测产品",
    "定量检测",
    "定性检测",
    "免疫分析",
    "免疫层析",
    "化学发光",
    "poct",
    "assay",
    "test kit",
    "diagnostic kit",
    "immunoassay",
]
LIFE_SCIENCE_SCOPE_DISABLED_VALUES = {
    "false",
    "no",
    "skip",
    "none",
    "not_applicable",
    "not applicable",
    "registry_only",
    "competitor_only",
    "regulatory_only",
    "无需",
    "不需要",
    "不适用",
    "跳过",
    "仅注册",
    "只做注册",
    "仅竞品",
    "只做竞品",
    "只做注册/竞品/标准",
}
LIFE_SCIENCE_SCOPE_ENABLED_VALUES = {
    "true",
    "yes",
    "required",
    "include",
    "auto_required",
    "需要",
    "必须",
    "包含",
    "启用",
}
MIN_LIFE_SCIENCE_MATERIALS = 12
MIN_LIFE_SCIENCE_DATABASES = 5
MIN_LIFE_SCIENCE_LANES = 4

BUSINESS_READY_SCENARIO_STATUSES = {"completed", "no_results"}
DEFERRED_SCENARIO_STATUSES = {"deferred"}

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

DELIVERY_DIR_NAME = "交付目录"
STANDARD_REPORT_NAME = "00_立项调研综合报告.html"
STANDARD_REVIEW_NAME = "01_证据审阅与补证任务表.xlsx"
EVIDENCE_CARD_DIR_NAME = "02_证据卡"
TRACE_DIR_NAME = "90_系统追溯数据"
TRACE_TOP_LEVELS = [
    "task.json",
    "manifest.json",
    "data",
    "downloads",
    "extracted_text",
    "evidence_cards",
    "logs",
    "reports",
    "review",
    "staging",
    "knowledge",
]

TRACE_DISPLAY_NAMES = {
    "task.json": "00_任务配置_task.json",
    "manifest.json": "00_交付校验清单_manifest.json",
    "data": "01_原始材料数据_data",
    "downloads": "02_下载原文与网页快照_downloads",
    "extracted_text": "03_全文抽取文本_extracted_text",
    "evidence_cards": "04_证据卡文件_evidence_cards",
    "logs": "05_运行日志_logs",
    "reports": "06_内部历史报告_reports",
    "review": "07_审阅表与导入校验_review",
    "staging": "08_分析暂存区_staging",
    "knowledge": "09_本地知识索引_knowledge",
}

TRACE_SUBDIR_DISPLAY_NAMES = {
    # Preserve the legacy source key when packaging old tasks, but do not leak
    # the historical analyte name into new delivery directory labels.
    "fallback_historical_ptau181": "01_历史证据兜底_historical_evidence",
    "fallback_official_api": "02_官方接口兜底_fallback_official_api",
    "literature": "03_文献材料_literature",
    "pubmed": "01_PubMed旧批次_pubmed",
    "pubmed_100": "02_PubMed近五年100条_pubmed_100",
    "pmc": "03_PMC旧批次_pmc",
    "pmc_100": "04_PMC近五年100条_pmc_100",
    "openalex": "05_OpenAlex旧批次_openalex",
    "openalex_100": "06_OpenAlex近五年100条_openalex_100",
    "articles": "01_PMC单篇XML_articles",
    "competitor": "01_竞品材料_competitor",
    "regulatory": "02_法规材料_regulatory",
    "standard": "03_标准材料_standard",
    "patent": "04_专利材料_patent",
    "local_import": "05_本地导入_local_import",
    "chunks": "06_文本分块_chunks",
    "json": "01_JSON证据卡_json",
    "markdown": "02_Markdown证据卡_markdown",
    "analysis_requests": "01_分析请求_analysis_requests",
    "evidence_cards": "02_证据卡暂存_evidence_cards",
    "report_sections": "03_报告章节暂存_report_sections",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_manifest(task_dir: Path) -> list[dict]:
    files = []
    for path in sorted(task_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(task_dir)
        if relative.parts and relative.parts[0] == "packages":
            continue
        try:
            size = path.stat().st_size
            digest = sha256_file(path)
        except FileNotFoundError:
            continue
        files.append(
            {
                "path": str(relative),
                "size": size,
                "sha256": digest,
            }
        )
    return files


def standard_delivery_dir(task_dir: Path) -> Path:
    return task_dir / DELIVERY_DIR_NAME


def standard_delivery_paths(task_dir: Path) -> dict[str, Path]:
    delivery_dir = standard_delivery_dir(task_dir)
    return {
        "delivery_dir": delivery_dir,
        "report": delivery_dir / STANDARD_REPORT_NAME,
        "review": delivery_dir / STANDARD_REVIEW_NAME,
        "evidence_cards": delivery_dir / EVIDENCE_CARD_DIR_NAME,
        "trace": delivery_dir / TRACE_DIR_NAME,
    }


def _copy_file_if_exists(source: Path, target: Path) -> bool:
    if not source.exists() or not source.is_file():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return True


def _copy_tree_contents(source: Path, target: Path) -> None:
    if not source.exists():
        return
    if source.is_file():
        _copy_file_if_exists(source, target)
        return
    target.mkdir(parents=True, exist_ok=True)
    for item in source.rglob("*"):
        if not item.is_file():
            continue
        relative = item.relative_to(source)
        mapped_parent = Path()
        for part in relative.parent.parts:
            mapped_parent /= TRACE_SUBDIR_DISPLAY_NAMES.get(part, part)
        _copy_file_if_exists(item, target / mapped_parent / relative.name)


def _write_delivery_readme(task_dir: Path, delivery_dir: Path) -> None:
    task_path = task_dir / "task.json"
    topic = ""
    task_id = ""
    if task_path.exists():
        try:
            task = json.loads(task_path.read_text(encoding="utf-8"))
            topic = str(task.get("topic") or "")
            task_id = str(task.get("task_id") or "")
        except json.JSONDecodeError:
            pass
    readme = delivery_dir / TRACE_DIR_NAME / "00_追溯数据说明.md"
    readme.parent.mkdir(parents=True, exist_ok=True)
    readme.write_text(
        "\n".join(
            [
                "# 系统追溯数据说明",
                "",
                f"生成时间：{now_iso()}",
                f"任务ID：{task_id}",
                f"课题：{topic}",
                "",
                "本目录用于系统复核、调试和审计，不作为研发业务人员默认阅读入口。",
                "业务默认阅读入口为交付目录根部的 HTML 综合报告、Excel 审阅与补证任务表和 02_证据卡。",
                "",
                "主要内容包括：",
                "- 01_原始材料数据_data：材料、证据卡、报告版本等 JSONL 数据。",
                "- 02_下载原文与网页快照_downloads：公开来源检索结果、下载文件、网页快照和接口原始返回。文献来源按 PubMed、PMC、OpenAlex 分子目录。",
                "- 03_全文抽取文本_extracted_text：已抽取的全文文本。文献全文通常在 03_文献材料_literature 子目录中。",
                "- 04_证据卡文件_evidence_cards：证据卡导出文件。",
                "- 05_运行日志_logs：运行事件和调试日志。",
                "- 06_内部历史报告_reports：系统内部历史报告文件。",
                "- 07_审阅表与导入校验_review：系统内部审阅表与导入校验文件。",
                "- 08_分析暂存区_staging：分析章节暂存文件。",
                "",
                "已下载或已保存的文献材料位置：",
                "- PDF/原文下载文件：02_下载原文与网页快照_downloads/03_文献材料_literature/。",
                "- PubMed 近五年 100 条原始结果：02_下载原文与网页快照_downloads/03_文献材料_literature/02_PubMed近五年100条_pubmed_100/。",
                "- PMC 近五年 100 条开放全文原始结果：02_下载原文与网页快照_downloads/03_文献材料_literature/04_PMC近五年100条_pmc_100/。",
                "- OpenAlex 近五年 100 条原始结果：02_下载原文与网页快照_downloads/03_文献材料_literature/06_OpenAlex近五年100条_openalex_100/。",
                "- 已抽取全文文本：03_全文抽取文本_extracted_text/03_文献材料_literature/。",
                "- 若 HTML 报告中显示“已保存文本/快照”，优先到 03_全文抽取文本_extracted_text/03_文献材料_literature/ 查找对应 MAT 编号文件。",
                "- 若 HTML 报告中显示“未下载”，表示当前仅保留题录、摘要或网页链接，尚未取得本地 PDF/全文文件。",
            ]
        ),
        encoding="utf-8",
    )


def build_standard_delivery(task_dir: Path) -> dict:
    """Build the business-facing standard delivery folder.

    The default user-facing package has exactly three top-level entries:
    00 HTML report, 01 Excel review workbook, and 90 trace data.
    Legacy internal task folders remain under the task directory but are not
    the business-facing delivery surface.
    """
    paths = standard_delivery_paths(task_dir)
    delivery_dir = paths["delivery_dir"]
    delivery_dir.mkdir(parents=True, exist_ok=True)

    from .reports import build_standard_report
    from .knowledge.literature_graph import build_literature_knowledge
    from .translation import translation_status

    knowledge_result = build_literature_knowledge(task_dir)
    translation_result = translation_status(task_dir)
    research_integrity_result = build_research_integrity_audit(task_dir)
    write_json(
        task_dir / "logs" / "research_integrity_audit.json",
        research_integrity_result,
    )

    report_result = build_standard_report(task_dir, output=paths["report"])
    report_ok = Path(report_result["report_path"]).exists()

    review_source = task_dir / "review" / STANDARD_REVIEW_NAME
    if not review_source.exists():
        review_source = task_dir / "review" / "evidence_review_v001.xlsx"
    review_ok = _copy_file_if_exists(review_source, paths["review"])

    evidence_card_dir = paths["evidence_cards"]
    if evidence_card_dir.exists():
        shutil.rmtree(evidence_card_dir)
    evidence_card_source = task_dir / "evidence_cards" / "markdown"
    evidence_card_dir.mkdir(parents=True, exist_ok=True)
    evidence_card_count = 0
    if evidence_card_source.exists():
        for source in sorted(evidence_card_source.glob("*.md")):
            if _copy_file_if_exists(source, evidence_card_dir / source.name):
                evidence_card_count += 1

    trace_dir = paths["trace"]
    if trace_dir.exists():
        shutil.rmtree(trace_dir)
    trace_dir.mkdir(parents=True, exist_ok=True)
    for name in TRACE_TOP_LEVELS:
        if name == DELIVERY_DIR_NAME:
            continue
        source = task_dir / name
        if not source.exists():
            continue
        target = trace_dir / TRACE_DISPLAY_NAMES.get(name, name)
        _copy_tree_contents(source, target)

    from .source_adapters.source_sites import export_source_sites

    export_source_sites(trace_dir / "01_原始材料数据_data" / "source_sites_v21.json")

    _write_delivery_readme(task_dir, delivery_dir)

    top_level_entries = sorted(
        path.name for path in delivery_dir.iterdir() if path.name != ".DS_Store"
    )
    manifest = {
        "generated_at": now_iso(),
        "delivery_dir": str(delivery_dir),
        "standard_outputs": {
            STANDARD_REPORT_NAME: report_ok,
            STANDARD_REVIEW_NAME: review_ok,
            EVIDENCE_CARD_DIR_NAME: evidence_card_count > 0,
            TRACE_DIR_NAME: trace_dir.exists() and trace_dir.is_dir(),
        },
        "evidence_card_count": evidence_card_count,
        "knowledge": knowledge_result,
        "translation": translation_result,
        "research_integrity": research_integrity_result,
        "top_level_entries": top_level_entries,
        "topic_safe_name": safe_topic(
            json.loads((task_dir / "task.json").read_text(encoding="utf-8")).get("topic", "")
        )
        if (task_dir / "task.json").exists()
        else "",
    }
    write_json(trace_dir / "standard_delivery_manifest.json", manifest)
    return manifest


def scenario_coverage_warnings(task_dir: Path) -> list[str]:
    task_path = task_dir / "task.json"
    if not task_path.exists():
        return ["缺少 task.json，无法确认正式采集场景覆盖情况。"]
    try:
        task = json.loads(task_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ["task.json 无法解析，无法确认正式采集场景覆盖情况。"]

    statuses = task.get("scenario_statuses", {})
    warnings: list[str] = []
    for scenario_id in formal_scenarios_for(task):
        scenario = statuses.get(scenario_id)
        if not scenario:
            warnings.append(f"正式场景 {scenario_id} 缺少状态记录，不能判定业务可交付。")
            continue
        status = scenario.get("status", "not_started")
        last_message = str(scenario.get("last_message") or "")
        if status in DEFERRED_SCENARIO_STATUSES:
            continue
        if status not in BUSINESS_READY_SCENARIO_STATUSES:
            warnings.append(
                f"正式场景 {scenario_id} 当前状态为 {status}，不能判定业务可交付。{last_message}"
            )
            continue
        if scenario_id == "nmpa_competitor":
            warnings.extend(nmpa_manual_gate_warnings(task_dir, scenario))
        if status == "no_results" and not last_message:
            warnings.append(
                f"正式场景 {scenario_id} 为 no_results 但缺少说明，不能判定业务可交付。"
            )
    return warnings


def missing_business_confirmations(task: dict) -> list[str]:
    confirmations = task.get("confirmations") or {}
    missing = []
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


def fallback_warnings(collection_alerts: dict) -> list[str]:
    warnings = []
    for scenario in collection_alerts.get("fallback_missing_scenarios", []):
        label = scenario.get("label_zh") or scenario.get("scenario_id")
        warnings.append(
            f"正式场景 {label} 失败后缺少兜底动作记录，必须补充重试、公开来源导入、浏览器观察或人工补证任务。"
        )
    return warnings


def latest_network_preflight(task_dir: Path) -> dict | None:
    events = [
        event
        for event in read_jsonl(task_dir / "logs" / "events.jsonl")
        if event.get("event") == "network_preflight"
    ]
    return events[-1] if events else None


def network_warnings(network_preflight: dict | None) -> list[str]:
    if not network_preflight:
        return [
            "尚未执行正式公网采集网络预检；建议运行 doctor --network 或保留流水线默认 network-preflight。"
        ]
    if network_preflight.get("network_ok", False):
        return []
    warnings = ["正式公网采集网络预检失败，PubMed/PMC/OpenAlex 等公网来源可能无法自动采集。"]
    for probe in network_preflight.get("probes", []):
        label = probe.get("label_zh") or probe.get("id") or probe.get("host")
        errors = [
            probe.get("python_dns_error", ""),
            probe.get("python_https_error", ""),
            probe.get("curl_https_error", ""),
        ]
        detail = "；".join(error for error in errors if error)
        warnings.append(f"网络预检失败来源：{label}。{detail}")
    return warnings


def _life_science_scope_override(confirmations: dict) -> bool | None:
    explicit = confirmations.get("life_science_required")
    if isinstance(explicit, bool):
        return explicit
    explicit_text = str(explicit or "").strip().lower()
    scope_text = str(confirmations.get("life_science_scope") or "").strip().lower()
    for value in [explicit_text, scope_text]:
        if not value or value == "auto":
            continue
        if value in LIFE_SCIENCE_SCOPE_ENABLED_VALUES:
            return True
        if value in LIFE_SCIENCE_SCOPE_DISABLED_VALUES:
            return False
    return None


def requires_life_science_research(task: dict) -> bool:
    confirmations = task.get("confirmations") or {}
    override = _life_science_scope_override(confirmations)
    if override is not None:
        return override

    haystack = profile_text(task)
    return any(term.lower() in haystack for term in LIFE_SCIENCE_TRIGGER_TERMS) or (
        bool(confirmations.get("collection_scope"))
        and any(term in haystack for term in LIFE_SCIENCE_AUTO_PRODUCT_TERMS)
    )


def life_science_coverage(task_dir: Path, task: dict) -> dict:
    materials = [
        row
        for row in read_jsonl(task_dir / "data" / "materials.jsonl")
        if row.get("source_scenario") == LIFE_SCIENCE_RESEARCH_SCENARIO
        or row.get("source_site_id") == LIFE_SCIENCE_RESEARCH_SCENARIO
    ]
    source_runs = [
        row
        for row in read_jsonl(task_dir / "data" / "source_runs.jsonl")
        if row.get("plugin_name") == "life-science-research"
        or row.get("source_run_id", "").startswith("LSR-")
    ]
    databases = {
        str(
            material.get("raw_fields", {}).get("source_database")
            or material.get("collection_path", {}).get("source_database")
            or ""
        ).strip()
        for material in materials
    }
    databases.update(
        database.strip()
        for run in source_runs
        for database in str(run.get("source_database") or "").split(";")
        if database.strip()
    )
    lanes = {
        str(
            material.get("evidence_lane")
            or material.get("raw_fields", {}).get("evidence_lane")
            or material.get("collection_path", {}).get("evidence_lane")
            or ""
        ).strip()
        for material in materials
    }
    lanes.discard("")
    scenario = (task.get("scenario_statuses") or {}).get(LIFE_SCIENCE_RESEARCH_SCENARIO) or {}
    return {
        "required": requires_life_science_research(task),
        "status": scenario.get("status", "not_started"),
        "material_count": len(materials),
        "source_run_count": len(source_runs),
        "database_count": len([item for item in databases if item]),
        "lane_count": len(lanes),
        "databases": sorted(item for item in databases if item),
        "lanes": sorted(lanes),
        "minimums": {
            "materials": MIN_LIFE_SCIENCE_MATERIALS,
            "databases": MIN_LIFE_SCIENCE_DATABASES,
            "lanes": MIN_LIFE_SCIENCE_LANES,
        },
    }


def life_science_warnings(coverage: dict) -> list[str]:
    if not coverage.get("required"):
        return []
    warnings: list[str] = []
    status = coverage.get("status") or "not_started"
    material_count = int(coverage.get("material_count") or 0)
    database_count = int(coverage.get("database_count") or 0)
    lane_count = int(coverage.get("lane_count") or 0)
    minimums = coverage.get("minimums") or {}
    if status in {"not_started", "", None} or material_count == 0:
        warnings.append(
            "课题涉及标志物、蛋白、机制、临床试验或公共科学数据库线索，"
            "但尚未导入 life-science-research 插件证据，不能判定业务可交付。"
        )
        return warnings
    if material_count < minimums.get("materials", MIN_LIFE_SCIENCE_MATERIALS):
        warnings.append(
            "life-science-research 插件证据数量不足："
            f"当前 {material_count} 条，至少需要 {MIN_LIFE_SCIENCE_MATERIALS} 条。"
        )
    if database_count < minimums.get("databases", MIN_LIFE_SCIENCE_DATABASES):
        warnings.append(
            "life-science-research 插件数据库覆盖不足："
            f"当前 {database_count} 个，至少需要 {MIN_LIFE_SCIENCE_DATABASES} 个。"
        )
    if lane_count < minimums.get("lanes", MIN_LIFE_SCIENCE_LANES):
        warnings.append(
            "life-science-research 插件证据通道覆盖不足："
            f"当前 {lane_count} 个，至少需要 {MIN_LIFE_SCIENCE_LANES} 个。"
        )
    return warnings


def unresolved_network_scenarios(task: dict) -> list[dict]:
    statuses = task.get("scenario_statuses") or {}
    unresolved = []
    for scenario_id in NETWORK_SENSITIVE_SCENARIOS:
        scenario = statuses.get(scenario_id) or {}
        status = scenario.get("status", "not_started")
        material_count = int(scenario.get("material_count") or 0)
        last_message = str(scenario.get("last_message") or "")
        if status == "completed" and material_count > 0:
            continue
        if status == "no_results" and last_message:
            continue
        unresolved.append(
            {
                "scenario_id": scenario_id,
                "status": status,
                "material_count": material_count,
                "last_message": last_message,
            }
        )
    return unresolved


def verify_package(task_dir: Path) -> dict:
    required = [
        "task.json",
        "data/materials.jsonl",
        "data/evidence_cards.jsonl",
        "logs/events.jsonl",
    ]
    missing = [item for item in required if not (task_dir / item).exists()]
    material_count = count_jsonl_rows(task_dir / "data" / "materials.jsonl")
    evidence_count = count_jsonl_rows(task_dir / "data" / "evidence_cards.jsonl")
    reviewed_cards = list(read_jsonl(task_dir / "data" / "reviewed_evidence_cards.jsonl"))
    delivery_paths = standard_delivery_paths(task_dir)
    standard_report_exists = delivery_paths["report"].exists()
    standard_review_exists = delivery_paths["review"].exists()
    standard_trace_exists = delivery_paths["trace"].exists() and delivery_paths["trace"].is_dir()
    source_sites_path = delivery_paths["trace"] / "01_原始材料数据_data" / "source_sites_v21.json"
    knowledge_dir = task_dir / "knowledge"
    metric_facts_path = knowledge_dir / "metric_facts.jsonl"
    literature_graph_path = knowledge_dir / "literature_graph.json"
    topic_index_path = knowledge_dir / "topic_index.json"
    review_validation_path = task_dir / "review" / "import_validation_v001.json"
    review_import_ok = False
    if review_validation_path.exists():
        try:
            review_import_ok = bool(
                json.loads(review_validation_path.read_text(encoding="utf-8")).get("ok")
            )
        except json.JSONDecodeError:
            review_import_ok = False

    warnings = []
    task = {}
    if (task_dir / "task.json").exists():
        try:
            task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            task = {}
    scenario_statuses = list((task.get("scenario_statuses") or {}).values())
    missing_confirmations = missing_business_confirmations(task)
    materials = list(read_jsonl(task_dir / "data" / "materials.jsonl"))
    evidence_cards = list(read_jsonl(task_dir / "data" / "evidence_cards.jsonl"))
    collection_alerts = build_collection_alerts(
        materials=materials,
        evidence_cards=evidence_cards,
        scenario_statuses=scenario_statuses,
        required_scenario_ids=formal_scenarios_for(task),
    )
    source_quality = build_source_quality_audit(
        task_dir,
        task=task,
        materials=materials,
        scenario_statuses=scenario_statuses,
        required_scenario_ids=formal_scenarios_for(task),
    )
    research_integrity = build_research_integrity_audit(task_dir, task=task)
    life_science = life_science_coverage(task_dir, task)
    life_science_gate_warnings = life_science_warnings(life_science)
    network_preflight = latest_network_preflight(task_dir)
    network_unresolved_scenarios = unresolved_network_scenarios(task)
    if missing_confirmations:
        warnings.append(
            "检索画像未完整确认，不能作为正式采集交付。缺少字段："
            + "；".join(missing_confirmations)
        )
    if material_count == 0:
        warnings.append("严重告警：当前任务尚未登记任何材料。该状态应优先排查采集链路、网络、权限或检索式，不能解释为未发现证据。")
    if evidence_count == 0:
        warnings.append("严重告警：当前任务尚未生成任何证据卡，报告不能支撑业务复核。")
    for message in collection_alerts["critical_messages"]:
        if message not in warnings:
            warnings.append(message)
    if not standard_report_exists:
        warnings.append(f"尚未生成标准交付报告：{DELIVERY_DIR_NAME}/{STANDARD_REPORT_NAME}。")

    if not standard_review_exists:
        warnings.append(f"尚未生成标准交付审阅表：{DELIVERY_DIR_NAME}/{STANDARD_REVIEW_NAME}。")
    if not standard_trace_exists:
        warnings.append(f"尚未生成系统追溯数据目录：{DELIVERY_DIR_NAME}/{TRACE_DIR_NAME}/。")
    if not source_sites_path.exists():
        warnings.append("尚未在追溯数据中固化 V2.1 标准信源配置 source_sites_v21.json。")
    if not literature_graph_path.exists() or not topic_index_path.exists():
        warnings.append("尚未生成 V2.1 本地知识索引 literature_graph.json / topic_index.json。")
    if not review_import_ok:
        warnings.append("尚未导入通过校验的人工复核结果，当前报告只能视为自动草稿。")

    delivery_artifacts_ready = (
        not missing
        and material_count > 0
        and evidence_count > 0
        and standard_report_exists
        and standard_review_exists
        and standard_trace_exists
    )
    included_reviewed_cards = [
        card for card in reviewed_cards if card.get("include_in_report")
    ]
    unresolved_reviewed_cards = [
        card
        for card in included_reviewed_cards
        if card.get("needs_review", True)
        or card.get("evidence_strength") == "needs_review"
    ]
    final_review_ready = (
        delivery_artifacts_ready
        and review_import_ok
        and bool(included_reviewed_cards)
        and not unresolved_reviewed_cards
    )
    if review_import_ok and not included_reviewed_cards:
        warnings.append("人工复核结果未纳入任何证据，最终报告仍无可引用证据。")
    if unresolved_reviewed_cards:
        warnings.append("人工复核结果中仍存在 needs_review 证据，不能作为最终报告。")

    coverage_warnings = scenario_coverage_warnings(task_dir)
    coverage_warnings.extend(life_science_gate_warnings)
    warnings.extend(coverage_warnings)
    warnings.extend(fallback_warnings(collection_alerts))
    for issue in source_quality.get("issues", []):
        if issue.get("severity") == "high":
            warnings.append(
                f"采集质量审计：{issue.get('source')} {issue.get('finding')} {issue.get('recommendation')}"
            )
    for issue in research_integrity.get("issues", []):
        warnings.append(
            "研究完整性审计："
            f"{issue.get('finding', '')} 建议：{issue.get('recommendation', '')}"
        )
    warnings.extend(network_warnings(network_preflight))
    scenario_coverage_ready = not coverage_warnings
    search_profile_ready = not missing_confirmations
    fallback_ready = collection_alerts.get("fallback_missing_count", 0) == 0
    network_ready = (
        not network_preflight
        or network_preflight.get("network_ok", False)
        or not network_unresolved_scenarios
    )
    source_quality_ready = bool(source_quality.get("ready", False))
    research_integrity_required = bool(research_integrity.get("required", True))
    research_integrity_ready = bool(research_integrity.get("ready", False))
    v21_assets_ready = (
        source_sites_path.exists()
        and literature_graph_path.exists()
        and topic_index_path.exists()
    )
    business_ready = (
        delivery_artifacts_ready
        and v21_assets_ready
        and search_profile_ready
        and final_review_ready
        and scenario_coverage_ready
        and fallback_ready
        and network_ready
        and source_quality_ready
        and (not research_integrity_required or research_integrity_ready)
    )
    counts = {
        "materials": material_count,
        "evidence_cards": evidence_count,
        "reviewed_evidence_cards": len(reviewed_cards),
        "included_reviewed_evidence_cards": len(included_reviewed_cards),
        "metric_facts": count_jsonl_rows(metric_facts_path),
    }

    manifest = {
        "generated_at": now_iso(),
        "task_dir": str(task_dir),
        "missing": missing,
        "warnings": warnings,
        "delivery_artifacts_ready": delivery_artifacts_ready,
        "v21_assets_ready": v21_assets_ready,
        "final_review_ready": final_review_ready,
        "scenario_coverage_ready": scenario_coverage_ready,
        "search_profile_ready": search_profile_ready,
        "missing_confirmations": missing_confirmations,
        "fallback_ready": fallback_ready,
        "network_ready": network_ready,
        "source_quality_ready": source_quality_ready,
        "research_integrity_required": research_integrity_required,
        "research_integrity_ready": research_integrity_ready,
        "life_science_coverage": life_science,
        "network_preflight": network_preflight,
        "network_unresolved_scenarios": network_unresolved_scenarios,
        "business_ready": business_ready,
        "counts": counts,
        "collection_alerts": collection_alerts,
        "source_quality": source_quality,
        "research_integrity": research_integrity,
        "standard_delivery": {
            "delivery_dir": str(delivery_paths["delivery_dir"]),
            "report": str(delivery_paths["report"]),
            "review": str(delivery_paths["review"]),
            "trace": str(delivery_paths["trace"]),
            "report_exists": standard_report_exists,
            "review_exists": standard_review_exists,
            "trace_exists": standard_trace_exists,
        },
        "files": file_manifest(task_dir),
    }
    manifest_path = task_dir / "manifest.json"
    write_json(manifest_path, manifest)
    return {
        "ok": business_ready,
        "delivery_artifacts_ready": delivery_artifacts_ready,
        "v21_assets_ready": manifest["v21_assets_ready"],
        "final_review_ready": final_review_ready,
        "scenario_coverage_ready": scenario_coverage_ready,
        "search_profile_ready": search_profile_ready,
        "missing_confirmations": missing_confirmations,
        "fallback_ready": fallback_ready,
        "network_ready": network_ready,
        "source_quality_ready": source_quality_ready,
        "research_integrity_required": research_integrity_required,
        "research_integrity_ready": research_integrity_ready,
        "life_science_coverage": life_science,
        "network_preflight": network_preflight,
        "network_unresolved_scenarios": network_unresolved_scenarios,
        "business_ready": business_ready,
        "counts": counts,
        "standard_delivery": manifest["standard_delivery"],
        "collection_alerts": collection_alerts,
        "source_quality": source_quality,
        "research_integrity": research_integrity,
        "missing": missing,
        "warnings": warnings,
        "manifest_path": str(manifest_path),
    }


def package_task(task_dir: Path) -> dict:
    verification = verify_package(task_dir)
    package_path = task_dir / "packages" / "task_package_v001.zip"
    package_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(package_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(task_dir.rglob("*")):
            if not path.is_file() or path == package_path:
                continue
            archive.write(path, path.relative_to(task_dir))
    return {"package_path": str(package_path), "verification": verification}
