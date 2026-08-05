from pathlib import Path
import re
from typing import Any
from datetime import date

from jinja2 import Template

from .jsonl import append_jsonl, read_json, read_jsonl
from .constants import WORKFLOW_VERSION
from .project_profile import (
    AD_TERMS,
    RESPIRATORY_TERMS,
    formal_scenarios_for,
    has_confirmed_project_profile,
    is_ad_project as profile_is_ad_project,
    project_domain as profile_project_domain,
    project_subject,
)
from .project_relevance import assess_material_relevance
from .query_plan import resolved_literature_profile
from .quality import (
    OPTIONAL_NOT_STARTED_SCENARIOS,
    build_collection_alerts,
    fallback_materials_for_scenario,
)
from .source_quality import build_source_quality_audit
from .research_integrity import build_research_integrity_audit
from .status import now_iso
from .translation import (
    extract_parameters,
    is_mostly_english,
    load_translation_cache,
    text_hash,
    translation_status,
)


REPORT_SECTIONS = [
    "临床意义",
    "临床应用定位",
    "目标使用场景",
    "目标人群",
    "诊疗路径",
    "金标准与参照方法",
    "指南与共识",
    "专家和组织意见",
    "市场准入与收费",
    "市场定位",
    "竞争格局",
    "出口与注册",
    "技术可行性",
    "参考物质",
    "安全要求",
    "原材料可获得性",
    "其他发现 / 待归类线索",
]

SOURCE_DISPLAY_LIMIT = 100
STATUS_LABELS = {
    "completed": "completed / 已完成",
    "completed_with_warnings": "completed_with_warnings / 部分完成",
    "collection_failed": "collection_failed / 采集失败",
    "needs_login": "needs_login / 需要登录",
    "permission_required": "permission_required / 权限受限",
    "needs_manual_review": "needs_manual_review / 需人工复核",
    "not_started": "not_started / 未启动",
    "no_results": "no_results / 未发现结果",
    "deferred": "deferred / 暂缓",
    "draft": "draft / 草稿",
}


def report_display_title(topic: str) -> str:
    title = str(topic or "").strip() or "项目"
    suffix_replacements = [
        ("立项可行性全量验证调研", "调研分析综述"),
        ("立项可行性调研", "调研分析综述"),
        ("可行性全量验证调研", "调研分析综述"),
        ("可行性调研", "调研分析综述"),
        ("立项调研", "调研分析综述"),
        ("调研", "调研分析综述"),
    ]
    for suffix, replacement in suffix_replacements:
        if title.endswith(suffix):
            return f"{title[:-len(suffix)]}{replacement}"
    if title.endswith("综述"):
        return title
    return f"{title}调研分析综述"


def asset_root() -> Path:
    return Path(__file__).resolve().parent / "assets"


def local_path(material: dict) -> str:
    if material.get("download_files"):
        return material["download_files"][0].get("relative_path", "")
    return material.get("collection_path", {}).get("stored_file", "")


def _first_raw(raw: dict, *keys: str) -> str:
    for key in keys:
        value = raw.get(key, "")
        if isinstance(value, list):
            value = "；".join(str(item) for item in value if str(item).strip())
        value = str(value or "").strip()
        if value:
            return value
    return ""


def _extract_identifier_from_text(text: str, label: str) -> str:
    match = re.search(rf"{re.escape(label)}[：:]\s*([A-Za-z0-9./_\-]+)", str(text or ""))
    return match.group(1).strip() if match else ""


def _clean_identifier(value: str, kind: str) -> str:
    value = str(value or "").strip().strip("；;,.")
    if not value or value.upper() in {"PMID", "PMCID", "DOI", "NONE", "NULL"}:
        return ""
    if kind == "pmid":
        match = re.search(r"\d{6,10}", value)
        return match.group(0) if match else ""
    if kind == "pmcid":
        match = re.search(r"PMC\d+", value, re.IGNORECASE)
        return match.group(0).upper() if match else ""
    if kind == "doi":
        match = re.search(r"10\.\S+", value)
        return match.group(0).strip("；;,.") if match else ""
    return value


def _date_sort_value(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    match = re.search(r"(\d{4})(?:[-/ ]([A-Za-z]{3,9}|\d{1,2}))?(?:[-/ ](\d{1,2}))?", raw)
    if not match:
        return raw
    year = int(match.group(1))
    month_text = match.group(2) or "1"
    month_names = {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "sept": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
    month = month_names.get(month_text[:3].lower(), None) if month_text.isalpha() else int(month_text)
    day = int(match.group(3) or 1)
    return f"{year:04d}-{month:02d}-{day:02d}"


def _display_publication_date(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    normalized = _date_sort_value(raw)
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", normalized)
    if not match:
        return raw
    year, month, day = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    is_month_only = bool(re.fullmatch(r"\d{4}[-/]\d{1,2}", raw))
    is_future = date(year, month, day) > date.today()
    if is_future:
        if is_month_only:
            return f"{year:04d}-{month:02d}（待核验，来源返回未来刊期）"
        return f"{year:04d}-{month:02d}-{day:02d}（待核验，来源返回未来日期）"
    if is_month_only:
        return f"{year:04d}-{month:02d}"
    return raw


def _clean_summary_text(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    lines = []
    skip_prefixes = (
        "来源：",
        "PMID：",
        "PMCID：",
        "DOI：",
        "期刊：",
        "发表日期：",
        "检索口径：",
        "OpenAlex ID：",
        "原始接口文件：",
        "业务相关性：",
        "历史任务路径：",
        "历史材料ID：",
        "历史来源场景：",
        "原始来源URL：",
        "复用边界：",
    )
    capture = False
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line in {"摘要：", "摘要/结构化摘要：", "全文片段："}:
            capture = True
            continue
        if any(line.startswith(prefix) for prefix in skip_prefixes):
            continue
        if capture or len(line) > 18:
            lines.append(line)
    result = " ".join(lines).strip() or cleaned
    return result[:900]


def _structured_abstract_items(raw: dict[str, Any]) -> list[dict[str, str]]:
    sections = raw.get("abstract_sections") or []
    items: list[dict[str, str]] = []
    if isinstance(sections, list):
        for section in sections:
            if not isinstance(section, dict):
                continue
            label = str(section.get("label") or "").strip() or "Abstract"
            text = " ".join(str(section.get("text") or "").split())
            if text:
                items.append({"label": label, "text": text})
    if not items:
        abstract = " ".join(str(raw.get("abstract") or raw.get("summary") or "").split())
        if abstract:
            items.append({"label": "Abstract", "text": abstract})
    return items


def _structured_abstract_text(raw: dict[str, Any], *, max_chars: int = 1800) -> str:
    lines = [f"{item['label']}: {item['text']}" for item in _structured_abstract_items(raw)]
    keywords = raw.get("keywords") or []
    if isinstance(keywords, list):
        keyword_text = "；".join(str(item).strip() for item in keywords if str(item).strip())
    else:
        keyword_text = str(keywords or "").strip()
    if keyword_text:
        lines.append(f"Keywords: {keyword_text}")
    text = "\n".join(lines).strip()
    return text[:max_chars]


def _source_record_limit(materials: list[dict]) -> list[dict]:
    grouped: dict[str, int] = {}
    limited = []
    for item in materials:
        source = str(item.get("source_scenario") or "")
        grouped[source] = grouped.get(source, 0) + 1
        if grouped[source] <= SOURCE_DISPLAY_LIMIT:
            limited.append(item)
    return limited


def evidence_by_material(evidence_cards: list[dict]) -> dict[str, list[dict]]:
    grouped = {}
    for card in evidence_cards:
        grouped.setdefault(card.get("material_id", ""), []).append(card)
    return grouped


def normalize_materials(
    materials: list[dict],
    evidence_cards: list[dict] | None = None,
    *,
    task_dir: Path | None = None,
    confirmations: dict | None = None,
) -> list[dict]:
    from .constants import MATERIAL_TYPE_LABELS
    from .scenarios.registry import all_scenarios

    _scenario_labels = {s.scenario_id: s.label_zh for s in all_scenarios()}
    evidence = evidence_by_material(evidence_cards or [])
    normalized = []
    for material in materials:
        row = dict(material)
        row["raw_fields"] = row.get("raw_fields") or {}
        material_cards = evidence.get(material.get("material_id", ""), [])
        row["local_path"] = local_path(material)
        raw = row["raw_fields"]
        text_blob = " ".join(
            str(value or "")
            for value in [
                row.get("title", ""),
                raw.get("summary", ""),
                raw.get("abstract", ""),
                raw.get("identifier", ""),
            ]
        )
        row["pmid"] = _clean_identifier(
            _first_raw(raw, "pmid", "PMID") or _extract_identifier_from_text(text_blob, "PMID"),
            "pmid",
        )
        row["pmcid"] = _clean_identifier(
            _first_raw(raw, "pmcid", "PMCID") or _extract_identifier_from_text(text_blob, "PMCID"),
            "pmcid",
        )
        row["doi"] = _clean_identifier(
            _first_raw(raw, "doi", "DOI") or _extract_identifier_from_text(text_blob, "DOI"),
            "doi",
        )
        row["publication_date"] = _first_raw(raw, "publication_date", "publication_year") or row.get("publication_date", "")
        row["publication_date_display"] = _display_publication_date(row["publication_date"])
        row["publication_sort_date"] = _date_sort_value(row["publication_date"])
        row["display_year"] = str(row.get("publication_date") or "")[:4]
        row["journal"] = _first_raw(raw, "journal", "source", "source_display_name")
        row["has_download"] = bool(row.get("download_files")) or row.get("download_status") in {"downloaded", "linked_snapshot"}
        if row.get("download_files"):
            row["download_status_zh"] = "已下载原文/PDF，见追溯目录 02_下载原文与网页快照_downloads"
        elif row.get("extracted_text_path"):
            row["download_status_zh"] = "已保存文本/快照，见追溯目录 03_全文抽取文本_extracted_text"
        else:
            row["download_status_zh"] = "未下载，仅保留题录/摘要/链接"
        row["display_url"] = (
            row.get("source_url")
            or _first_raw(raw, "pubmed_url", "pmc_url", "pdf_url", "url", "landing_page_url", "oa_url")
            or (f"https://doi.org/{row['doi']}" if row.get("doi") else "")
        )
        # Chinese display labels
        row["material_type_zh"] = MATERIAL_TYPE_LABELS.get(
            row.get("material_type", ""), row.get("material_type", "")
        )
        row["source_scenario_zh"] = _scenario_labels.get(
            row.get("source_scenario", ""), row.get("source_scenario", "")
        )
        row["taxonomy_tags"] = sorted(
            {
                tag
                for card in material_cards
                for tag in (card.get("taxonomy_tags") or [])
            }
        )
        row["evidence_strengths"] = sorted(
            {card.get("evidence_strength", "needs_review") for card in material_cards}
        )
        row["include_in_report"] = any(card.get("include_in_report") for card in material_cards)
        row["evidence_summaries"] = [
            card.get("summary", "") for card in material_cards if card.get("summary")
        ]
        row["abstract_sections_display"] = _structured_abstract_items(raw)
        row["abstract_sections_translated"] = []
        row["structured_abstract"] = _structured_abstract_text(raw)
        row["structured_summary"] = (
            row["structured_abstract"]
            or _clean_summary_text(
                _first_raw(raw, "abstract", "summary", "content")
                or " ".join(row.get("evidence_summaries") or [])
            )
        )
        display_text = row["structured_summary"] or _first_raw(raw, "scope", "basic_info_text", "full_visible_text")
        row["summary_translation_zh"] = ""
        row["summary_translation_status"] = "not_configured" if display_text else "not_needed"
        row["parameter_items"] = extract_parameters(display_text)
        row["status_text"] = " ".join(
            str(value or "")
            for value in [
                material.get("download_status", ""),
                material.get("extracted_text_status", ""),
                material.get("failure_type", ""),
                material.get("failure_reason", ""),
            ]
        ).strip()
        relevance = assess_material_relevance(row, confirmations)
        row["project_relevant"] = relevance["relevant"]
        row["project_relevance_reason"] = relevance["reason"]
        row["project_relevance_matches"] = (
            relevance["matched_aliases"] + relevance["matched_context_terms"]
        )
        normalized.append(row)
    return normalized


def build_excluded_material_rows(materials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reason_labels = {
        "no_project_signal_in_material": "未匹配到当前项目的目标物或用途信号",
        "animal_only_standard_for_human_project": "动物专用标准，不适用于当前人用项目",
    }
    rows: list[dict[str, Any]] = []
    for material in materials:
        if material.get("project_relevant", True):
            continue
        reason_code = str(material.get("project_relevance_reason") or "")
        rows.append(
            {
                "material_id": str(material.get("material_id") or ""),
                "title": str(material.get("title") or "未命名材料"),
                "source": str(
                    material.get("source_scenario_zh")
                    or material.get("source_scenario")
                    or "未知来源"
                ),
                "source_url": str(material.get("source_url") or ""),
                "reason": reason_labels.get(reason_code, reason_code or "项目相关性不足"),
                "matches": "；".join(material.get("project_relevance_matches") or []),
            }
        )
    return rows


def clean_report_text(text: str) -> str:
    cleaned = str(text or "")
    cleaned = cleaned.replace(" 专利标题（英）：", "；英文标题：")
    return cleaned


def clean_evidence_summary(text: str) -> str:
    cleaned = clean_report_text(text)
    cleaned = cleaned.replace("自动草稿证据卡：", "")
    cleaned = re.sub(r"PMID[：:]\s*[A-Za-z0-9./_\-]+[；;]?", "", cleaned)
    cleaned = re.sub(r"PMCID[：:]\s*[A-Za-z0-9./_\-]+[；;]?", "", cleaned)
    cleaned = re.sub(r"DOI[：:]\s*[A-Za-z0-9./_\-()]+[；;]?", "", cleaned)
    cleaned = re.sub(r"期刊[：:]\s*[^。；;]+[；;]?", "", cleaned)
    cleaned = re.sub(r"出版日期[：:]\s*[^。；;]+[；;]?", "", cleaned)
    cleaned = re.sub(r"发表日期[：:]\s*[^。；;]+[；;]?", "", cleaned)
    cleaned = re.sub(r"检索口径[：:]\s*[^。]+", "", cleaned)
    cleaned = cleaned.replace("该卡基于已采集材料生成，需研发人员复核后使用。", "")
    cleaned = cleaned.strip(" ；;。")
    return cleaned or "已采集材料线索，需复核后形成可引用结论。"


def is_section_fact(text: str) -> bool:
    value = str(text or "").strip()
    return value.startswith(
        (
            "Abstract[",
            "Keywords",
            "中文译文",
            "摘录中文译文",
            "参数",
            "摘录参数",
            "摘录线索",
        )
    )


def display_key_facts(items: list[str], summary: str) -> list[str]:
    facts: list[str] = []
    seen = set()
    low_value = {
        "已采集材料线索，需复核后形成可引用结论。",
        "已采集材料线索，需复核后形成可引用结论",
        "全文状态：completed",
        "全文状态: completed",
    }
    for item in items:
        if is_section_fact(item):
            continue
        fact = clean_evidence_summary(item)
        if not fact or fact in low_value:
            continue
        if fact == summary:
            continue
        if fact in seen:
            continue
        seen.add(fact)
        facts.append(fact)
    return facts[:6]


def normalize_evidence_cards(evidence_cards: list[dict]) -> list[dict]:
    from .constants import EVIDENCE_STRENGTH_LABELS, MATERIAL_TYPE_LABELS

    normalized = []
    for card in evidence_cards:
        row = dict(card)
        row["title"] = clean_display_title(row.get("title", ""))
        row["summary"] = clean_evidence_summary(row.get("summary", ""))
        row["key_excerpts"] = [
            {**excerpt, "text": clean_evidence_summary(excerpt.get("text", ""))}
            for excerpt in row.get("key_excerpts", [])
        ]
        row["key_facts"] = [
            clean_evidence_summary(item)
            for item in row.get("key_facts", [])
            if clean_evidence_summary(item)
        ]
        row["display_facts"] = display_key_facts(row["key_facts"], row["summary"])
        parameter_facts = [
            fact
            for fact in row["key_facts"]
            if fact.startswith("参数") or fact.startswith("摘录参数")
            or fact.startswith("参数事实")
        ]
        metric_parameter_facts = [
            f"{fact.get('metric_type', '')}：{fact.get('value', '')}；{fact.get('excerpt', '')[:160]}"
            for fact in row.get("metric_facts", [])
            if isinstance(fact, dict)
        ]
        row["translation_facts"] = []
        row["parameter_facts"] = (metric_parameter_facts + parameter_facts)[:10]
        # Map internal keys to Chinese display labels
        row["material_type_zh"] = MATERIAL_TYPE_LABELS.get(
            row.get("material_type", ""), row.get("material_type", "")
        )
        row["evidence_strength_zh"] = EVIDENCE_STRENGTH_LABELS.get(
            row.get("evidence_strength", ""), row.get("evidence_strength", "")
        )
        normalized.append(row)
    return normalized


def cards_by_section(
    evidence_cards: list[dict],
    *,
    include_only: bool = True,
) -> dict[str, list[dict]]:
    grouped = {section: [] for section in REPORT_SECTIONS}
    fallback = "其他发现 / 待归类线索"
    for card in evidence_cards:
        if include_only and not card.get("include_in_report"):
            continue
        tags = card.get("taxonomy_tags") or []
        target = next((tag for tag in tags if tag in grouped), fallback)
        grouped[target].append(card)
    return grouped


def report_sections_by_title(report_sections: list[dict]) -> dict[str, dict]:
    grouped = {}
    for section in report_sections:
        title = str(section.get("section_title") or "").strip()
        if title:
            grouped[title] = section
    return grouped


def normalize_report_sections(report_sections: list[dict]) -> list[dict]:
    normalized = []
    for section in report_sections:
        row = dict(section)
        row["facts"] = [clean_report_text(item) for item in row.get("facts", [])]
        row["analysis"] = clean_report_text(row.get("analysis", ""))
        row["evidence_gaps"] = [
            clean_report_text(item) for item in row.get("evidence_gaps", [])
        ]
        row["supporting_evidence_refs"] = row.get("supporting_evidence_refs", [])
        normalized.append(row)
    return normalized


def normalize_scenario_statuses(scenario_statuses: list[dict]) -> list[dict]:
    from .scenarios.registry import all_scenarios

    _label_map = {s.scenario_id: s.label_zh for s in all_scenarios()}
    normalized = []
    for scenario in scenario_statuses:
        row = dict(scenario)
        scenario_id = row.get("scenario_id", "")
        row["label_zh"] = _label_map.get(scenario_id, scenario_id)
        row["status_display"] = STATUS_LABELS.get(row.get("status", ""), f"{row.get('status', '')} / 待确认")
        if row.get("status") in {"needs_login", "permission_required"}:
            if scenario_id == "patenthub_patents":
                row["next_action"] = (
                    "需要用户登录 PatentHub：agent 先执行 open-browser-session "
                    "--scenario patenthub_patents --background 打开持久化浏览器；"
                    "用户完成登录/验证后通知 agent 继续，再重新运行 PatentHub 采集。"
                )
            else:
                row["next_action"] = (
                    f"open-browser-session --scenario {scenario_id} --headless false，"
                    "然后在可见浏览器中完成登录或真人验证。"
                )
        else:
            row["next_action"] = ""
        normalized.append(row)
    return normalized


def _visible_scenario_statuses(
    scenario_statuses: list[dict[str, Any]],
    required_scenario_ids: list[str] | set[str],
) -> list[dict[str, Any]]:
    required_ids = set(required_scenario_ids)
    visible: list[dict[str, Any]] = []
    for scenario in scenario_statuses:
        scenario_id = scenario.get("scenario_id")
        if scenario_id in required_ids:
            visible.append(scenario)
            continue
        if scenario.get("material_count", 0):
            visible.append(scenario)
    return visible


def _business_gap_for_scenario(
    scenario: dict[str, Any],
    *,
    fallback_materials: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    scenario_id = str(scenario.get("scenario_id") or "")
    label = str(scenario.get("label_zh") or scenario_id or "未命名来源")
    status = str(scenario.get("status") or "")
    fallback_materials = fallback_materials or []
    manual_labels = {
        "collection_failed": "未采集到",
        "needs_login": "需要登录后补采",
        "permission_required": "需要权限后补采",
        "no_results": "未发现匹配材料",
        "not_started": "尚未采集",
        "download_failed": "原文未取得",
        "parse_failed": "材料需人工整理",
    }
    type_label = manual_labels.get(status, "需人工确认")
    defaults = {
        "missing": "该来源的材料尚未形成可复核证据。",
        "impact": "会影响报告完整性和正式立项判断。",
        "action": "请业务人员补充合法来源文件、链接或完成来源复核后再重新生成报告。",
        "owner": "项目 / 研发",
    }
    mapping = {
        "nmpa_competitor": {
            "missing": "NMPA 官方同类产品注册证字段未完整采集。",
            "impact": "影响国内竞品格局、注册参照、样本类型、方法学和预期用途判断。",
            "action": "注册或产品人员人工核验 NMPA 官方数据库，补齐注册证编号、注册人、有效期、样本类型、方法学、预期用途和说明书链接。",
            "owner": "注册 / 产品",
        },
        "patenthub_patents": {
            "missing": "PatentHub 专利详情和/或全文尚未完成采集。",
            "impact": "影响自由实施、核心引物/探针/组合方案专利风险和可绕开空间判断。",
            "action": "知识产权或研发人员登录合法专利数据库，补齐高相关专利全文、权利要求、法律状态、地域和到期日。",
            "owner": "知识产权 / 研发",
        },
        "cmde_regulatory": {
            "missing": "CMDE 指导原则、审评报告或征求意见材料未形成有效命中。",
            "impact": "影响注册路径、临床评价、性能评价和申报资料要求判断。",
            "action": "注册人员按产品类别和预期用途人工复核 CMDE/NMPA 文件，必要时补充可类比指导原则。",
            "owner": "注册",
        },
        "standards_current": {
            "missing": "现行标准号、标准名称或适用条款未完整确认。",
            "impact": "影响性能验证、样本处理、安全要求和质量体系约束判断。",
            "action": "研发/质量人员补查国家标准、行业标准和团体标准，标注标准状态、适用条款和与本项目的关系。",
            "owner": "研发 / 质量",
        },
        "chinese_journal": {
            "missing": "中文期刊或中文全文材料未形成有效命中。",
            "impact": "影响国内临床应用、实验室流程和本土医学语境判断。",
            "action": "医学或项目人员补充中文期刊、指南解读、医院实验室资料或合法全文文件。",
            "owner": "医学 / 项目",
        },
        "wanfang_literature": {
            "missing": "中文期刊全文数据库材料未形成有效命中。",
            "impact": "影响国内临床应用、样本处理和实验室管理证据完整性。",
            "action": "医学或项目人员补充合法中文全文、题录或可审阅链接。",
            "owner": "医学 / 项目",
        },
        "external_life_science": {
            "missing": "外部科学数据库证据尚未启动或尚未导入。",
            "impact": "影响机制、靶标、临床试验或公共数据库线索的补充完整性。",
            "action": "如项目需要机制或公共数据库证据，补充运行外部科学数据库检索并导入材料管线。",
            "owner": "研发 / 医学",
        },
    }
    item = {**defaults, **mapping.get(scenario_id, {})}
    fallback_titles = [
        clean_display_title(str(material.get("title") or material.get("source_url") or "未命名材料"))
        for material in fallback_materials[:3]
    ]
    if fallback_materials:
        fallback_text = "；".join(fallback_titles)
        item["missing"] = (
            f"{item['missing']} 原采集通道尚未完全闭环；系统已匹配 "
            f"{len(fallback_materials)} 条同类型公开兜底材料"
            + (f"：{fallback_text}。" if fallback_text else "。")
        )
        item["impact"] = (
            "已形成部分替代证据，可用于初步研判；但官方字段、全文完整性或来源权限仍未关闭，正式立项前必须复核。"
        )
        item["action"] = (
            "先按兜底材料推进内部复核，同时保留原通道补采任务；补齐官方字段、全文或登录库核验后再更新报告。"
        )
        type_label = "已公开兜底部分补齐"
    return {
        "source": label,
        "status": type_label,
        "status_level": "warn" if fallback_materials else "danger",
        "missing": item["missing"],
        "impact": item["impact"],
        "action": item["action"],
        "owner": item["owner"],
        "fallback_count": len(fallback_materials),
        "fallback_titles": fallback_titles,
    }


def build_business_collection_gaps(
    collection_alerts: dict[str, Any],
    scenario_statuses: list[dict[str, Any]],
    materials: list[dict[str, Any]] | None = None,
    required_scenario_ids: list[str] | set[str] | None = None,
) -> list[dict[str, Any]]:
    """Convert technical collection statuses into business-facing supplement tasks."""
    required_ids = set(required_scenario_ids or [])
    materials = materials or []
    relevant_statuses = {
        "collection_failed",
        "permission_required",
        "needs_login",
        "download_failed",
        "parse_failed",
        "no_results",
        "not_started",
    }
    scenarios = [
        item
        for item in scenario_statuses
        if item.get("status") in relevant_statuses
        and item.get("scenario_id") not in OPTIONAL_NOT_STARTED_SCENARIOS
        and (not required_ids or item.get("scenario_id") in required_ids)
    ]
    seen: set[tuple[str, str]] = set()
    rows: list[dict[str, Any]] = []
    for scenario in scenarios:
        row = _business_gap_for_scenario(
            scenario,
            fallback_materials=fallback_materials_for_scenario(
                materials,
                str(scenario.get("scenario_id") or ""),
            ),
        )
        key = (row["source"], row["missing"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    return rows


def build_collection_gap_summary(
    collection_gap_rows: list[dict[str, Any]],
    *,
    materials: list[dict[str, Any]],
    evidence_cards: list[dict[str, Any]],
) -> dict[str, Any]:
    owner_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for row in collection_gap_rows:
        owner = row.get("owner", "未分配")
        status = row.get("status", "需处理")
        owner_counts[owner] = owner_counts.get(owner, 0) + 1
        status_counts[status] = status_counts.get(status, 0) + 1
    fallback_covered_count = sum(1 for row in collection_gap_rows if row.get("fallback_count"))
    uncovered_gap_count = len(collection_gap_rows) - fallback_covered_count
    priority = []
    for owner in ["注册 / 产品", "知识产权 / 研发", "注册", "研发 / 质量", "医学 / 项目"]:
        count = owner_counts.get(owner)
        if count:
            priority.append(f"{owner}：{count} 项")
    if not priority and owner_counts:
        priority = [f"{owner}：{count} 项" for owner, count in sorted(owner_counts.items())[:5]]
    if collection_gap_rows:
        if fallback_covered_count:
            headline = (
                f"当前报告已形成 {len(materials)} 条材料和 {len(evidence_cards)} 张证据卡，"
                f"仍有 {uncovered_gap_count} 项资料缺口未补齐；另有 {fallback_covered_count} 项已用公开兜底材料部分补齐，"
                "需要人工复核官方字段或来源完整性。"
            )
        else:
            headline = (
                f"当前报告已形成 {len(materials)} 条材料和 {len(evidence_cards)} 张证据卡，"
                f"但仍有 {len(collection_gap_rows)} 项资料缺口需要人工补充或复核。"
            )
    else:
        headline = "当前未识别到需要单独处理的资料缺口。"
    return {
        "headline": headline,
        "gap_count": uncovered_gap_count,
        "total_gap_task_count": len(collection_gap_rows),
        "fallback_covered_count": fallback_covered_count,
        "owner_counts": owner_counts,
        "status_counts": status_counts,
        "priority_text": "；".join(priority) if priority else "暂无重点责任角色。",
    }


def _by_type(materials: list[dict], material_type: str) -> list[dict]:
    return [item for item in materials if item.get("material_type") == material_type]


def _titles(materials: list[dict], limit: int = 5) -> list[str]:
    return [clean_display_title(item.get("title", "")) for item in materials[:limit] if item.get("title")]


def clean_display_title(title: str) -> str:
    return str(title or "").split(" 专利标题（英）：", 1)[0].strip()


def _short_text(text: str, limit: int = 260) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _raw(material: dict, key: str) -> str:
    value = (material.get("raw_fields") or {}).get(key, "")
    return "；".join(str(item) for item in value) if isinstance(value, list) else str(value or "")


def _competitor_lines(competitors: list[dict]) -> list[str]:
    lines = []
    for item in competitors[:8]:
        raw = item.get("raw_fields") or {}
        parts = [
            item.get("title", ""),
            raw.get("registration_certificate_number", ""),
            raw.get("registrant", ""),
            raw.get("methodology", ""),
            raw.get("approval_date", ""),
        ]
        line = " / ".join(str(part) for part in parts if part)
        if line:
            lines.append(line)
    return lines


def build_feasibility_analysis(
    materials: list[dict],
    evidence_cards: list[dict],
    scenario_statuses: list[dict],
) -> dict[str, Any]:
    competitors = _by_type(materials, "competitor")
    literature = _by_type(materials, "literature")
    patents = _by_type(materials, "patent")
    standards = _by_type(materials, "standard")
    regulatory = _by_type(materials, "regulatory")
    extracted_count = sum(1 for item in materials if item.get("extracted_text_path"))
    downloaded_count = sum(1 for item in materials if item.get("download_files"))
    blocked = [
        item
        for item in scenario_statuses
        if item.get("status") in {"needs_login", "permission_required", "collection_failed", "no_results"}
    ]

    judgement_parts = []
    if competitors:
        judgement_parts.append(
            f"已检索到 {len(competitors)} 条 NMPA 竞品/同类注册材料，说明该方向存在明确注册参照。"
        )
    if literature:
        judgement_parts.append(
            f"已采集 {len(literature)} 条中文期刊/CMA 文献材料，可支撑临床背景、检测价值和应用定位的初步论证。"
        )
    if standards or regulatory:
        judgement_parts.append(
            f"已采集 {len(standards)} 条现行标准和 {len(regulatory)} 条监管/指导原则材料，可作为注册路径和性能评价要求的参照。"
        )
    if patents:
        judgement_parts.append(
            f"已采集 {len(patents)} 条 PatentHub 专利材料，但需要人工复核其与目标 IVD 产品的直接相关性。"
        )
    if not judgement_parts:
        judgement_parts.append("当前材料不足以形成可执行立项判断，应先补齐核心来源。")

    clinical = []
    for item in literature[:5]:
        abstract = _raw(item, "abstract") or _raw(item, "summary")
        clinical.append(
            {
                "title": clean_display_title(item.get("title", "")),
                "point": abstract[:260] if abstract else "该文献已采集题录/正文线索，需在证据卡复核阶段确认可引用结论。",
            }
        )

    technology = []
    for item in competitors[:5]:
        raw = item.get("raw_fields") or {}
        technology.append(
            {
                "title": clean_display_title(item.get("title", "")),
                "point": "；".join(
                    str(value)
                    for value in [
                        raw.get("methodology", ""),
                        raw.get("scope", ""),
                        raw.get("sample_type", ""),
                    ]
                    if value
                )
                or "注册详情已采集，可用于对照方法学、样本类型和预期用途。",
            }
        )

    ip_points = []
    for item in patents[:5]:
        raw = item.get("raw_fields") or {}
        title = item.get("title", "")
        abstract = raw.get("abstract") or raw.get("basic_info_text", "")
        relevance = "需人工复核"
        if any(token in f"{title} {abstract}" for token in ["疫苗", "猪", "兽用"]):
            relevance = "可能不是目标人用 IVD 方向的直接专利，应作为排除/风险线索记录"
        ip_points.append(
            {
                "title": clean_display_title(title),
                "identifier": raw.get("publication_number", ""),
                "relevance": relevance,
                "point": str(abstract)[:260],
            }
        )

    gaps = []
    if not competitors:
        gaps.append("缺少 NMPA 竞品注册材料，无法判断国内注册参照和竞争格局。")
    if not literature:
        gaps.append("缺少临床文献材料，临床意义、目标人群和诊疗路径只能保留为待证实。")
    if not standards:
        gaps.append("缺少现行标准材料，性能评价、样本处理和安全要求仍需补充。")
    if not patents:
        gaps.append("缺少专利材料，知识产权风险和可绕开空间无法判断。")
    if blocked:
        gaps.append("存在未完成或受限场景：" + "；".join(f"{item.get('scenario_id')}={item.get('status')}" for item in blocked))
    if any(item.get("download_status") == "permission_required" for item in patents):
        gaps.append("PatentHub 部分 PDF/全文下载受权限限制，当前仅基于页面可见信息形成初步判断。")
    if not gaps:
        gaps.append("当前材料已覆盖主要 MVP 来源，但所有自动证据卡仍需研发人员复核后才能作为正式报告结论。")

    return {
        "overall_judgement": " ".join(judgement_parts),
        "source_basis": {
            "materials": len(materials),
            "evidence_cards": len(evidence_cards),
            "extracted_text": extracted_count,
            "downloaded_originals": downloaded_count,
        },
        "competitor_lines": _competitor_lines(competitors),
        "clinical_points": clinical,
        "technology_points": technology,
        "standard_titles": _titles(standards),
        "regulatory_titles": _titles(regulatory),
        "ip_points": ip_points,
        "evidence_gaps": gaps,
        "next_actions": [
            "优先复核 NMPA 竞品的预期用途、方法学、样本类型和注册证有效期。",
            "复核文献证据卡，把摘要线索归入临床意义、目标人群、诊疗路径和技术可行性。",
            "对 PatentHub 结果做相关性筛选，排除动物疫苗等非目标 IVD 方向材料。",
        ],
    }


def build_business_decision(
    *,
    materials: list[dict],
    literature_materials: list[dict],
    regulatory_materials: list[dict],
    competitor_materials: list[dict],
    standard_materials: list[dict],
    patent_materials: list[dict],
    scenario_map: dict[str, dict],
    confirmations: dict | None = None,
) -> dict[str, Any]:
    """Build business-facing decision text, not development diagnostics."""
    if _is_respiratory_project(materials, confirmations):
        literature_count = len(literature_materials)
        missing_domains = []
        if not regulatory_materials:
            missing_domains.append("法规/指导原则")
        if not competitor_materials:
            missing_domains.append("NMPA 竞品注册")
        if not standard_materials:
            missing_domains.append("现行标准")
        if not patent_materials:
            missing_domains.append("专利")
        signals = build_respiratory_signal_summary(literature_materials)
        topics = signals.get("topics", {})
        if literature_count and missing_domains:
            conclusion = (
                "当前已形成甲乙流/呼吸道病原体多重联检的文献证据基础，可支持继续推进立项调研，"
                "但尚不能形成完整立项建议。主要原因是注册路径、同类产品、标准和专利风险证据仍需补齐或人工复核。"
            )
        elif literature_count:
            conclusion = (
                "当前证据支持甲型/乙型流感多重联检方向进入立项复核。建议按“呼吸道感染病原体鉴别诊断/快速分流”定位推进，"
                "先完成注册证字段、参照方法、性能方案和专利全文复核后再进入正式立项会。"
            )
        else:
            conclusion = "当前尚未形成可用于立项判断的呼吸道病原体联检证据基础，应先完成核心来源采集。"
        basis = [
            (
                f"已采集文献材料 {literature_count} 条，其中甲型流感相关 {topics.get('influenza_a', 0)} 条、"
                f"乙型流感相关 {topics.get('influenza_b', 0)} 条、多重/联检相关 {topics.get('multiplex', 0)} 条、"
                f"RT-PCR/核酸检测相关 {topics.get('pcr', 0)} 条、性能评价相关 {topics.get('performance', 0)} 条。"
            ),
            "该方向的研发判断应围绕检测组合、样本类型、参照方法、阳性/阴性符合率、LoD、交叉反应、干扰和临床使用场景展开。",
        ]
        if competitor_materials:
            basis.append(
                f"已导入 {len(competitor_materials)} 条竞品/同类注册线索，可用于识别已上市产品的靶标组合、方法学、样本类型和预期用途。"
            )
        if regulatory_materials:
            basis.append(f"已导入 {len(regulatory_materials)} 条法规/注册路径材料，可用于确认 IVD 注册管理、分类规则和临床评价边界。")
        if standard_materials:
            basis.append(f"已导入 {len(standard_materials)} 条标准/性能控制材料，可用于设计 LoD、精密度、交叉反应、干扰和样本稳定性验证。")
        if patent_materials:
            basis.append(f"已形成 {len(patent_materials)} 条专利检索策略和风险提示，后续需由专利人员完成全文和权利要求复核。")
        if missing_domains:
            basis.append("尚缺：" + "、".join(missing_domains) + "。这些缺口会影响注册可行性、竞品定位、性能验证边界和知识产权风险判断。")
        cannot_conclude = []
        if not competitor_materials:
            cannot_conclude.append("不能判断国内是否已有同类甲乙流/呼吸道病原体多重联检注册产品、主流方法学、样本类型和预期用途表述。")
        if not regulatory_materials:
            cannot_conclude.append("不能判断该方向的注册路径、临床评价和性能评价要求。")
        if not standard_materials:
            cannot_conclude.append("不能判断适用标准、性能验证项目和实验室安全/质量体系约束。")
        if not patent_materials:
            cannot_conclude.append("不能判断引物/探针、检测组合、反应体系和自由实施空间的专利风险。")
        if not cannot_conclude:
            cannot_conclude.extend(
                [
                    "不能直接认定 NMPA 官方数据库字段已经完整核验；注册证编号、注册人、有效期、适用样本和说明书仍需人工复核。",
                    "不能直接形成自由实施结论；引物/探针、靶标组合、反应体系、内参设计和平台绑定专利需要专利全文审阅。",
                    "不能直接确定最终性能指标；LoD、包容性、交叉反应、干扰、阳性符合率和阴性符合率需要研发与注册共同确认。",
                    "自动证据卡仍未人工复核，不能直接作为最终立项会结论。",
                ]
            )
        return {
            "conclusion": conclusion,
            "basis": basis,
            "cannot_conclude": cannot_conclude,
            "recommendation": (
                "建议进入立项复核阶段：注册人员核验国内同类注册证字段，研发人员输出多重核酸检测性能验证方案，"
                "医学/临床人员确认目标人群与结果解释边界，专利人员完成 FTO 初筛。四项完成后再形成正式立项结论。"
            ),
        }

    literature_count = len(literature_materials)
    literature_signals = build_literature_signal_summary(literature_materials, confirmations)
    marker = literature_signals["marker"]
    missing_domains = []
    if not regulatory_materials:
        missing_domains.append("法规/指导原则")
    if not competitor_materials:
        missing_domains.append("NMPA 竞品注册")
    if not standard_materials:
        missing_domains.append("现行标准")
    if not patent_materials:
        missing_domains.append("专利")

    is_ad_project = _is_ad_biomarker_project(materials, confirmations)
    if literature_count and missing_domains:
        conclusion = (
            "当前已形成文献证据基础，可支持继续推进立项调研，但尚不能形成完整立项建议。"
            "主要原因是注册路径、同类产品、标准和专利风险证据尚未补齐。"
        )
    elif literature_count and is_ad_project:
        conclusion = (
            f"当前证据支持 AD 血液 {marker} 方向继续进入立项复核。"
            "建议按“辅助诊断/风险评估/转诊分层”定位推进，先完成注册证字段、专利全文和性能方案复核后再进入正式立项会。"
        )
    elif literature_count:
        conclusion = (
            f"当前证据支持 {marker} 项目继续进入立项复核。"
            "建议围绕预期用途、样本类型、平台方法学、参照方法和性能验证方案完成正式复核后再进入立项会。"
        )
    else:
        conclusion = (
            "当前尚未形成可用于立项判断的证据基础，应先完成核心来源采集。"
        )

    basis = [
        (
            f"已采集文献材料 {literature_count} 条，覆盖 PubMed、PMC、OpenAlex、中国指南和国际对标资料，可支撑临床价值、目标人群和检测场景复核。"
            f"其中含摘要 {literature_signals['with_abstract']} 条、结构化 Abstract {literature_signals['with_structured']} 条、可解析文本 {literature_signals['extracted']} 份。"
        ),
        (
            f"{marker} 与 AD 病理、认知下降风险和血液标志物临床应用相关，文献链条可支撑研发继续做方法学和临床性能论证。"
            if is_ad_project
            else f"{marker} 相关材料可支撑研发继续围绕目标临床场景、样本类型、平台方法学和性能指标开展立项论证。"
        ),
    ]
    if competitor_materials:
        basis.append(
            f"已导入 {len(competitor_materials)} 条竞品/同类注册线索，可用于识别目标物、平台方法学、样本类型和注册参照。"
        )
    if regulatory_materials:
        basis.append(
            f"已导入 {len(regulatory_materials)} 条法规/注册路径材料，可用于确认 IVD 注册管理、分类规则和预期用途边界。"
        )
    if standard_materials:
        basis.append(
            f"已导入 {len(standard_materials)} 条标准/性能控制材料，可用于设计 LoD/LoQ、精密度、线性、干扰、参考区间和样本稳定性验证。"
        )
    if patent_materials:
        basis.append(
            f"已形成 {len(patent_materials)} 条专利检索策略和风险提示，后续需由专利人员完成全文和权利要求复核。"
        )
    if missing_domains:
        basis.append("尚缺：" + "、".join(missing_domains) + "。这些缺口会影响注册可行性、竞品定位、性能验证边界和知识产权风险判断。")
    if scenario_map.get("pmc_fulltext", {}).get("status") == "completed":
        basis.append("PMC 已补入开放全文材料，但 PDF 是否可下载仍需逐条核验。")

    cannot_conclude = []
    if not competitor_materials:
        cannot_conclude.append("不能判断国内是否已有同类注册产品、主流方法学、样本类型和预期用途表述。")
    if not regulatory_materials:
        cannot_conclude.append("不能判断该方向的注册路径、临床评价和性能评价要求。")
    if not standard_materials:
        cannot_conclude.append("不能判断适用标准、性能验证项目和安全/质量体系约束。")
    if not patent_materials:
        cannot_conclude.append("不能判断知识产权风险、自由实施空间和技术绕开方向。")
    if not cannot_conclude:
        cannot_conclude.extend(
            [
                "不能直接认定 NMPA 官方数据库字段已经完整核验；注册证编号、注册人、有效期、适用样本和说明书仍需人工复核。",
                f"不能直接形成自由实施结论；{marker} 抗体表位、校准品、算法、试剂盒组合和平台绑定专利需要专利全文审阅。",
                "不能直接确定最终性能指标；参考区间、cut-off、灰区比例、临床灵敏度/特异性和不同平台一致性需要研发与医学共同确认。",
                "自动证据卡仍未人工复核，不能直接作为最终立项会结论。",
            ]
        )

    recommendation = (
        "建议进入立项复核阶段：注册人员核验国内同类注册证字段，研发人员输出化学发光免疫法性能验证方案，医学人员确认适用人群与临床解释边界，专利人员完成 FTO 初筛。四项完成后再形成正式立项结论。"
        if is_ad_project
        else "建议进入立项复核阶段：注册人员核验国内同类注册证字段，研发人员输出平台方法学和性能验证方案，医学人员确认适用场景与结果解释边界，专利人员完成 FTO 初筛。四项完成后再形成正式立项结论。"
    )

    return {
        "conclusion": conclusion,
        "basis": basis,
        "cannot_conclude": cannot_conclude,
        "recommendation": recommendation,
    }


METRIC_TYPE_LABELS = {
    "sensitivity": "检出灵敏度",
    "specificity": "检出特异性",
    "AUC": "曲线下面积 AUC",
    "cutoff": "判定阈值 / cut-off",
    "sample_size": "样本量",
    "HR": "风险比 HR",
    "OR": "比值比 OR",
    "CI": "置信区间 CI",
}

METRIC_TYPE_EXPLANATIONS = {
    "sensitivity": "阳性样本被正确检出的比例，通常用于评估漏检风险。",
    "specificity": "阴性样本被正确判为阴性的比例，通常用于评估误报风险。",
    "AUC": "区分阳性与阴性/目标状态的综合能力，越接近 1 通常越好。",
    "cutoff": "将结果判为阳性、阴性或风险分层的阈值，需要结合平台和样本类型复核。",
    "sample_size": "用于该研究、评价或分析的人数/样本数，影响结论稳定性。",
    "HR": "暴露组或阳性组发生结局的相对风险，需要结合置信区间解读。",
    "OR": "结局发生优势的相对比值，需要结合研究设计和置信区间解读。",
    "CI": "统计估计的不确定性范围，区间越宽通常代表不确定性越大。",
}


def metric_type_label(metric_type: str) -> str:
    key = str(metric_type or "").strip()
    return METRIC_TYPE_LABELS.get(key, key or "待复核指标")


def metric_type_explanation(metric_type: str) -> str:
    key = str(metric_type or "").strip()
    return METRIC_TYPE_EXPLANATIONS.get(key, "从原文中抽取的参数事实，需结合原文语境人工复核。")


def material_display_title(material: dict | None, fallback: str = "") -> str:
    if not material:
        return fallback or "-"
    return clean_display_title(material.get("title") or fallback or material.get("material_id") or "-")


def material_href(material: dict | None) -> str:
    if not material:
        return ""
    return str(material.get("display_url") or material.get("source_url") or "")


def evidence_card_anchor(card_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", str(card_id or "").strip())
    return f"evidence-card-{value}" if value else ""


def build_metric_fact_rows(
    metric_facts: list[dict],
    *,
    materials_by_id: dict[str, dict],
    screening_cards: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    card_by_id = {str(card.get("card_id") or ""): card for card in screening_cards}
    rows: list[dict[str, Any]] = []
    for fact in metric_facts:
        material_id = str(fact.get("material_id") or "")
        card_id = str(fact.get("evidence_card_id") or "")
        material = materials_by_id.get(material_id, {})
        metric_type = str(fact.get("metric_type") or "")
        card = card_by_id.get(card_id, {})
        search_text = " ".join(
            str(part or "")
            for part in [
                metric_type_label(metric_type),
                metric_type,
                fact.get("value", ""),
                material_display_title(material, material_id),
                card_id,
                fact.get("sample_type", ""),
                fact.get("platform", ""),
                fact.get("reference_standard", ""),
                fact.get("excerpt", ""),
            ]
        )
        rows.append(
            {
                "metric_fact_id": fact.get("metric_fact_id", ""),
                "metric_type": metric_type,
                "metric_type_zh": metric_type_label(metric_type),
                "metric_explanation": metric_type_explanation(metric_type),
                "value": fact.get("value", ""),
                "value_label": "参数值 / 结果值",
                "value_explanation": "原文报告的具体数值或区间，必须结合指标名称、样本类型和原文语境解读。",
                "material_id": material_id,
                "material_title": material_display_title(material, material_id),
                "material_href": material_href(material),
                "evidence_card_id": card_id,
                "evidence_card_anchor": evidence_card_anchor(card_id),
                "evidence_card_title": card.get("display_title") or card.get("title") or card_id,
                "sample_type": fact.get("sample_type") or "-",
                "platform": fact.get("platform") or "-",
                "reference_standard": fact.get("reference_standard") or "-",
                "excerpt": _short_text(fact.get("excerpt", ""), 360),
                "search_text": search_text.lower(),
            }
        )
    return rows


SECTION_EVIDENCE_TERMS: dict[str, list[str]] = {
    "临床意义": ["clinical", "symptom", "impact", "burden", "诊断", "感染", "流感", "clinical"],
    "临床应用定位": ["diagnosis", "diagnostic", "differential", "screening", "triage", "鉴别", "分流"],
    "目标使用场景": ["hospital", "outpatient", "emergency", "icu", "point-of-care", "门诊", "急诊", "住院"],
    "目标人群": ["children", "pediatric", "adult", "elderly", "patient", "儿童", "成人", "患者"],
    "诊疗路径": ["workflow", "turnaround", "treatment", "clinical", "路径", "用药", "隔离"],
    "金标准与参照方法": ["reference", "comparator", "rt-pcr", "pcr", "sequencing", "参照", "比对"],
    "指南与共识": ["guideline", "consensus", "standard", "who", "cdc", "指南", "共识", "标准"],
    "专家和组织意见": ["society", "expert", "organization", "consensus", "专家", "组织", "协会"],
    "市场准入与收费": ["cost", "price", "reimbursement", "market", "收费", "医保", "价格"],
    "市场定位": ["market", "position", "panel", "multiplex", "定位", "组合"],
    "竞争格局": ["competitor", "registration", "manufacturer", "product", "注册", "竞品", "产品"],
    "出口与注册": ["regulatory", "registration", "fda", "ivdr", "nmpa", "注册", "申报"],
    "技术可行性": ["sensitivity", "specificity", "lod", "auc", "pcr", "assay", "灵敏度", "特异性", "最低检出限"],
    "参考物质": ["control", "calibrator", "reference material", "标准品", "质控", "参考物质"],
    "安全要求": ["safety", "false positive", "false negative", "risk", "安全", "假阳性", "假阴性"],
    "原材料可获得性": ["primer", "probe", "enzyme", "reagent", "antibody", "引物", "探针", "原料"],
    "其他发现 / 待归类线索": [],
}


def _evidence_text(material: dict, card: dict | None = None) -> str:
    raw = material.get("raw_fields") or {}
    parts = [
        material.get("title", ""),
        material.get("structured_summary", ""),
        raw.get("abstract", ""),
        raw.get("summary", ""),
        raw.get("keywords", ""),
        raw.get("full_visible_text", ""),
    ]
    if card:
        parts.extend([card.get("title", ""), card.get("summary", ""), card.get("exact_data", "")])
        parts.extend(card.get("key_facts") or [])
    return " ".join(str(part or "") for part in parts)


def _match_score(text: str, terms: list[str]) -> int:
    lowered = text.lower()
    return sum(1 for term in terms if term.lower() in lowered)


def build_section_evidence_rows(
    section_title: str,
    *,
    materials: list[dict],
    screening_cards: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    terms = SECTION_EVIDENCE_TERMS.get(section_title, [])
    materials_by_id = {item.get("material_id"): item for item in materials}
    rows: list[dict[str, str]] = []
    for card in screening_cards:
        material = materials_by_id.get(card.get("material_id"), {})
        if not material:
            continue
        if material.get("project_relevant") is False:
            continue
        text = _evidence_text(material, card)
        score = _match_score(text, terms) if terms else 1
        if score <= 0:
            continue
        excerpt = (
            card.get("summary")
            or material.get("structured_summary")
            or _first_raw(material.get("raw_fields") or {}, "abstract", "summary", "full_visible_text")
        )
        rows.append(
            {
                "score": score,
                "material_title": material_display_title(material, card.get("title", "")),
                "material_href": material_href(material),
                "excerpt": _short_text(excerpt, 260),
                "evidence_card_id": card.get("card_id", ""),
                "evidence_card_title": card.get("display_title") or card.get("title") or card.get("card_id", ""),
                "evidence_card_anchor": evidence_card_anchor(card.get("card_id", "")),
                "priority_label": card.get("priority_label", ""),
            }
        )
    rows.sort(key=lambda row: (-int(row.get("score") or 0), row.get("material_title", "")))
    return rows


def attach_analysis_evidence_rows(
    sections: list[dict[str, Any]],
    *,
    materials: list[dict],
    screening_cards: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    enriched = []
    for section in sections:
        row = dict(section)
        row["evidence_rows"] = build_section_evidence_rows(
            str(row.get("title") or ""),
            materials=materials,
            screening_cards=screening_cards,
        )
        enriched.append(row)
    return enriched


REPORT_SECTION_STRENGTH_LABELS = {
    "strong": "强",
    "moderate": "中等",
    "weak": "弱",
    "gap": "证据缺口",
}


def build_report_section_evidence_rows(
    section: dict[str, Any],
    *,
    materials: list[dict],
    screening_cards: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Render only the evidence explicitly cited by a validated report section."""
    materials_by_id = {str(item.get("material_id") or ""): item for item in materials}
    cards_by_id = {str(card.get("card_id") or ""): card for card in screening_cards}
    rows: list[dict[str, Any]] = []
    for order, ref in enumerate(section.get("supporting_evidence_refs") or []):
        if not isinstance(ref, dict):
            continue
        material_id = str(ref.get("material_id") or "")
        card_id = str(ref.get("evidence_card_id") or "")
        material = materials_by_id.get(material_id)
        card = cards_by_id.get(card_id)
        if not material or not card:
            continue
        rows.append(
            {
                "score": 1000 - order,
                "material_title": material_display_title(
                    material,
                    card.get("display_title") or card.get("title") or material_id,
                ),
                "material_href": material_href(material),
                "excerpt": _short_text(ref.get("excerpt") or "", 260),
                "evidence_card_id": card_id,
                "evidence_card_title": (
                    card.get("display_title") or card.get("title") or card_id
                ),
                "evidence_card_anchor": evidence_card_anchor(card_id),
                "priority_label": card.get("priority_label", ""),
            }
        )
    return rows


def merge_validated_report_sections(
    base_sections: list[dict[str, Any]],
    report_sections: list[dict[str, Any]],
    *,
    materials: list[dict],
    screening_cards: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Overlay validated project analysis onto the standard tabbed workbench.

    The standard report remains the only user-facing entry. Rule-generated
    sections are retained as a fallback when no validated section exists.
    """
    report_sections_by_title = {
        str(section.get("section_title") or "").strip(): section
        for section in report_sections
        if str(section.get("section_title") or "").strip()
    }
    merged: list[dict[str, Any]] = []
    for base in base_sections:
        row = dict(base)
        row.setdefault("facts", [])
        row.setdefault("gaps", [row.get("gap", "")] if row.get("gap") else [])
        row.setdefault("evidence_strength_label", "")
        row.setdefault("confidence_level", "")
        row.setdefault("review_status", "规则分析待复核")
        row.setdefault("analysis_source", "rule_draft")
        report_section = report_sections_by_title.get(str(row.get("title") or ""))
        if report_section:
            facts = [
                clean_report_text(item)
                for item in report_section.get("facts") or []
                if clean_report_text(item)
            ]
            gaps = [
                clean_report_text(item)
                for item in report_section.get("evidence_gaps") or []
                if clean_report_text(item)
            ]
            evidence_rows = build_report_section_evidence_rows(
                report_section,
                materials=materials,
                screening_cards=screening_cards,
            )
            row.update(
                {
                    "analysis": clean_report_text(report_section.get("analysis", "")),
                    "facts": facts,
                    "gaps": gaps,
                    "gap": "；".join(gaps),
                    "evidence": (
                        f"本章已绑定 {len(evidence_rows)} 条经过结构校验的直接支撑证据。"
                        if evidence_rows
                        else "本章当前没有可直接绑定的支撑证据，结论按证据缺口处理。"
                    ),
                    "evidence_rows": evidence_rows,
                    "evidence_strength_label": REPORT_SECTION_STRENGTH_LABELS.get(
                        str(report_section.get("evidence_strength_summary") or ""),
                        str(report_section.get("evidence_strength_summary") or ""),
                    ),
                    "confidence_level": str(
                        report_section.get("confidence_level") or ""
                    ),
                    "review_status": (
                        "待人工复核"
                        if report_section.get("needs_human_review", True)
                        else "已标记完成复核"
                    ),
                    "analysis_source": "validated_report_section",
                }
            )
        merged.append(row)
    return merged


def build_expert_decision(
    business_decision: dict[str, Any],
    *,
    materials: list[dict],
    screening_summary: dict[str, Any],
    knowledge_status: dict[str, Any],
    failed_scenarios: list[dict],
    collection_gap_summary: dict[str, Any],
    confirmations: dict | None = None,
) -> dict[str, Any]:
    is_respiratory = _is_respiratory_project(materials, confirmations)
    core_count = screening_summary.get("core", 0)
    metric_count = knowledge_status.get("metric_fact_count", 0)
    gap_count = collection_gap_summary.get("gap_count", 0)
    if is_respiratory:
        judgement = (
            "建议进入立项复核，而不是直接立项。当前证据已能说明甲/乙型流感及呼吸道病原体多重检测具有明确临床使用场景，"
            "但注册参照、性能验证边界、竞品字段和专利风险仍需逐项关闭。"
        )
        positioning = "优先按“呼吸道感染病原体鉴别诊断 / 发热门急诊快速分流 / 检验科多重核酸检测菜单”定位，不宜宣称覆盖所有呼吸道感染或替代临床综合诊断。"
        validation = "研发验证应优先锁定样本类型、参照试剂、LoD、包容性、交叉反应、干扰、阳性符合率、阴性符合率和多靶标兼容性。"
    else:
        judgement = business_decision.get("conclusion", "")
        intended_use = str((confirmations or {}).get("intended_use") or "已确认预期用途")
        sample_type = str((confirmations or {}).get("sample_type") or "目标样本")
        platform = str(
            (confirmations or {}).get("platform")
            or (confirmations or {}).get("methodology")
            or "目标平台/方法学"
        )
        positioning = (
            f"优先按已确认预期用途“{intended_use}”定位，并明确目标人群、结果解释和不适用边界；"
            "自动证据不能把检测结果扩大为未经确认的独立确诊结论。"
        )
        validation = (
            f"研发验证应围绕 {sample_type} 与 {platform}，优先锁定参照方法、检出限/定量限、线性、精密度、"
            "准确度、分析特异性、干扰、稳定性、临床性能和平台一致性。"
        )
    confidence = "中"
    if gap_count or failed_scenarios:
        confidence = "中低"
    if core_count >= 100 and metric_count >= 1000 and not gap_count:
        confidence = "中高"
    return {
        "judgement": judgement,
        "confidence": confidence,
        "positioning": positioning,
        "validation_focus": validation,
        "evidence_readout": (
            f"当前形成 {len(materials)} 条材料、{screening_summary.get('total', 0)} 张证据卡、"
            f"{core_count} 张核心必读证据卡和 {metric_count} 条指标事实；仍有 {gap_count} 项资料缺口。"
        ),
        "go_no_go": (
            "Go with conditions：允许继续立项复核和方案设计，但正式立项会前必须关闭注册/竞品/标准/专利和关键性能证据缺口。"
            if core_count or metric_count
            else "No-go for decision：当前证据不足以支持立项判断，应先补齐核心材料和证据卡。"
        ),
        "next_gate": "下一关口：完成 NMPA 同类产品字段核验、性能验证方案、核心文献人工复核和 FTO 初筛后，再输出正式立项建议。",
    }


def build_search_profile(task: dict) -> list[dict[str, str]]:
    confirmations = task.get("confirmations") or {}
    profile = resolved_literature_profile(
        type(
            "TaskLike",
            (),
            {"topic": task.get("topic", ""), "confirmations": confirmations},
        )()
    )
    date_range = confirmations.get("literature_date_range") or {}
    if isinstance(date_range, dict):
        date_text = " 至 ".join(
            part
            for part in [
                str(date_range.get("start") or date_range.get("from") or ""),
                str(date_range.get("end") or date_range.get("to") or ""),
            ]
            if part
        )
    else:
        date_text = str(date_range or "")
    rows = [
        ("标题/课题", task.get("topic", "")),
        ("核心检索词", confirmations.get("primary_query", "")),
        ("英文检索词", confirmations.get("english_keywords", "")),
        ("样本类型", confirmations.get("sample_type", "")),
        ("平台", confirmations.get("platform", "")),
        ("方法学/检测原理", confirmations.get("methodology", "")),
        ("预期用途", confirmations.get("intended_use", "")),
        ("目标地区", confirmations.get("target_region", "")),
        ("竞品范围", confirmations.get("competitor_scope", "")),
        ("专利范围", confirmations.get("patent_scope", "")),
        ("文献时间范围", date_text or f"近 {confirmations.get('literature_years', 5)} 年"),
        ("文献检索 profile", f"{profile.get('label_zh', '')}（{profile.get('profile_id', '')}）"),
        (
            "文献单源召回上限",
            "全量（已确认风险）"
            if profile.get("retmax") == "all"
            else f"每个英文文献源默认最多召回 {profile.get('retmax')} 条",
        ),
        ("方法学扩展检索", "英文文献源按核心词、方法学扩展词和宽检索词分层执行"),
        ("单渠道展示上限", f"每个检索渠道最多展示 {SOURCE_DISPLAY_LIMIT} 条"),
    ]
    return [{"label": label, "value": str(value or "-")} for label, value in rows]


def _material_title_join(materials: list[dict], limit: int = 3) -> str:
    titles = [item.get("title", "") for item in materials[:limit] if item.get("title")]
    if len(materials) > limit:
        titles.append(f"另 {len(materials) - limit} 条")
    return "；".join(titles) if titles else "暂无材料"


def _count_topic(materials: list[dict], terms: list[str]) -> int:
    count = 0
    for item in materials:
        raw = item.get("raw_fields") or {}
        text = " ".join(
            str(value or "")
            for value in [
                item.get("title", ""),
                raw.get("abstract", ""),
                raw.get("full_visible_text", ""),
                raw.get("keywords", ""),
                raw.get("mesh_terms", ""),
            ]
        )
        if _contains_any(text, terms):
            count += 1
    return count


def _sample_titles(materials: list[dict], terms: list[str], limit: int = 4) -> str:
    titles = []
    for item in materials:
        raw = item.get("raw_fields") or {}
        text = f"{item.get('title', '')} {raw.get('abstract', '')} {raw.get('keywords', '')}"
        if _contains_any(text, terms):
            title = clean_display_title(item.get("title", ""))
            if title and title not in titles:
                titles.append(title)
        if len(titles) >= limit:
            break
    return "；".join(titles) if titles else "暂无直接题名线索"


def _contains_any(text: str, terms: list[str]) -> bool:
    lowered = str(text or "").lower()
    return any(str(term or "").lower() in lowered for term in terms)


def _profile_source(confirmations: dict | None = None, topic: str = "") -> dict[str, Any]:
    return {"topic": topic, "confirmations": confirmations or {}}


def _is_respiratory_project(
    materials: list[dict],
    confirmations: dict | None = None,
    topic: str = "",
) -> bool:
    source = _profile_source(confirmations, topic)
    if has_confirmed_project_profile(confirmations) or topic:
        return profile_project_domain(source) == "respiratory"

    # Legacy tasks may not have confirmations. Material titles are only used
    # when no authoritative project profile exists.
    material_text = " ".join(item.get("title", "") for item in materials[:200]).lower()
    return _contains_any(material_text, RESPIRATORY_TERMS)


def _is_ad_biomarker_project(
    materials: list[dict],
    confirmations: dict | None = None,
    topic: str = "",
) -> bool:
    source = _profile_source(confirmations, topic)
    if has_confirmed_project_profile(confirmations) or topic:
        return profile_is_ad_project(source)

    material_text = " ".join(item.get("title", "") for item in materials[:200]).lower()
    return _contains_any(material_text, AD_TERMS) or bool(re.search(r"\bad\b", material_text))


def _target_marker(materials: list[dict], confirmations: dict | None = None) -> str:
    if has_confirmed_project_profile(confirmations):
        return project_subject(_profile_source(confirmations), fallback="目标检测项目")
    for material in materials:
        title = str(material.get("title", "") or "").strip()
        raw = material.get("raw_fields") or {}
        legacy_text = " ".join(
            str(value or "")
            for value in [title, raw.get("abstract", ""), raw.get("keywords", "")]
        ).strip()
        if legacy_text:
            subject = project_subject({"confirmations": {"primary_query": legacy_text}})
            if subject != "目标检测项目":
                return subject
    return "目标检测项目"


def build_literature_signal_summary(
    literature_materials: list[dict],
    confirmations: dict | None = None,
) -> dict[str, Any]:
    total = len(literature_materials)
    pubmed = sum(1 for item in literature_materials if item.get("source_scenario") == "pubmed_literature")
    pmc = sum(1 for item in literature_materials if item.get("source_scenario") == "pmc_fulltext")
    extracted = sum(1 for item in literature_materials if item.get("extracted_text_path"))
    with_abstract = sum(1 for item in literature_materials if (item.get("raw_fields") or {}).get("abstract"))
    with_structured = sum(1 for item in literature_materials if (item.get("raw_fields") or {}).get("abstract_sections"))
    marker = _target_marker(literature_materials, confirmations)
    method_terms = []
    for key in ["platform", "methodology", "english_method_keywords"]:
        value = (confirmations or {}).get(key, "")
        values = value if isinstance(value, list) else re.split(r"[；;、，,|\n]+", str(value or ""))
        method_terms.extend(str(item or "").strip() for item in values if len(str(item or "").strip()) >= 2)
    topics = {
        "blood": _count_topic(literature_materials, ["blood", "plasma", "serum", "血浆", "血清", "血液"]),
        "csf": _count_topic(literature_materials, ["CSF", "cerebrospinal fluid", "脑脊液"]),
        "mci": _count_topic(literature_materials, ["MCI", "mild cognitive impairment", "轻度认知"]),
        "diagnosis": _count_topic(literature_materials, ["diagnosis", "diagnostic", "诊断", "differential"]),
        "screening": _count_topic(literature_materials, ["screening", "primary care", "triage", "筛查", "转诊"]),
        "risk": _count_topic(literature_materials, ["risk", "prediction", "predict", "prognosis", "progression", "风险", "预测", "进展"]),
        "amyloid_reference": _count_topic(literature_materials, ["amyloid PET", "Aβ", "Abeta", "amyloid pathology", "淀粉样"]),
        "tau_reference": _count_topic(literature_materials, ["tau PET", "tau pathology", "神经原纤维", "tau pathology"]),
        "csf_reference": _count_topic(literature_materials, ["CSF biomarker", "cerebrospinal fluid biomarker", "脑脊液标志物"]),
        "auc": _count_topic(literature_materials, ["AUC", "area under", "sensitivity", "specificity", "灵敏度", "特异性"]),
        "immunoassay": _count_topic(literature_materials, ["immunoassay", "ELISA", "Simoa", "chemiluminescence", "ECL", "免疫", "化学发光"]),
        "methodology": _count_topic(literature_materials, method_terms) if method_terms else 0,
    }
    return {
        "marker": marker,
        "total": total,
        "pubmed": pubmed,
        "pmc": pmc,
        "extracted": extracted,
        "with_abstract": with_abstract,
        "with_structured": with_structured,
        "topics": topics,
        "examples": {
            "blood": _sample_titles(literature_materials, ["blood", "plasma", "serum"], 4),
            "diagnosis": _sample_titles(literature_materials, ["diagnosis", "diagnostic"], 4),
            "risk": _sample_titles(literature_materials, ["risk", "prediction", "progression"], 4),
            "reference": _sample_titles(literature_materials, ["amyloid PET", "tau PET", "CSF", "pathology"], 4),
            "technology": _sample_titles(literature_materials, ["immunoassay", "ELISA", "Simoa", "chemiluminescence", "ECL"], 4),
            "methodology": _sample_titles(literature_materials, method_terms, 4) if method_terms else "暂无直接题名线索",
        },
    }


def build_respiratory_signal_summary(
    literature_materials: list[dict],
    confirmations: dict | None = None,
) -> dict[str, Any]:
    total = len(literature_materials)
    pubmed = sum(1 for item in literature_materials if item.get("source_scenario") == "pubmed_literature")
    pmc = sum(1 for item in literature_materials if item.get("source_scenario") == "pmc_fulltext")
    extracted = sum(1 for item in literature_materials if item.get("extracted_text_path"))
    with_abstract = sum(1 for item in literature_materials if (item.get("raw_fields") or {}).get("abstract"))
    with_structured = sum(1 for item in literature_materials if (item.get("raw_fields") or {}).get("abstract_sections"))
    topics = {
        "influenza_a": _count_topic(literature_materials, ["influenza a", "flu a", "甲型流感", "甲流"]),
        "influenza_b": _count_topic(literature_materials, ["influenza b", "flu b", "乙型流感", "乙流"]),
        "multiplex": _count_topic(literature_materials, ["multiplex", "panel", "多重", "联检"]),
        "pcr": _count_topic(literature_materials, ["rt-pcr", "real-time pcr", "pcr", "核酸", "荧光"]),
        "antigen": _count_topic(literature_materials, ["antigen", "抗原", "lateral flow", "免疫层析"]),
        "respiratory_sample": _count_topic(literature_materials, ["nasopharyngeal", "oropharyngeal", "nasal swab", "throat swab", "鼻咽", "咽拭子", "鼻拭子"]),
        "diagnosis": _count_topic(literature_materials, ["diagnosis", "diagnostic", "诊断", "鉴别"]),
        "children": _count_topic(literature_materials, ["children", "pediatric", "paediatric", "儿童"]),
        "hospital": _count_topic(literature_materials, ["hospital", "outpatient", "emergency", "icu", "住院", "门诊", "急诊"]),
        "performance": _count_topic(literature_materials, ["sensitivity", "specificity", "lod", "limit of detection", "灵敏度", "特异性", "最低检出限"]),
        "standard": _count_topic(literature_materials, ["guideline", "standard", "who", "cdc", "指南", "标准"]),
    }
    return {
        "total": total,
        "pubmed": pubmed,
        "pmc": pmc,
        "extracted": extracted,
        "with_abstract": with_abstract,
        "with_structured": with_structured,
        "topics": topics,
        "examples": {
            "influenza": _sample_titles(literature_materials, ["influenza a", "influenza b", "甲型流感", "乙型流感"], 4),
            "multiplex": _sample_titles(literature_materials, ["multiplex", "panel", "多重", "联检"], 4),
            "pcr": _sample_titles(literature_materials, ["rt-pcr", "real-time pcr", "核酸", "荧光"], 4),
            "sample": _sample_titles(literature_materials, ["nasopharyngeal", "oropharyngeal", "nasal swab", "鼻咽", "咽拭子"], 4),
            "performance": _sample_titles(literature_materials, ["sensitivity", "specificity", "lod", "灵敏度", "特异性"], 4),
        },
    }


def _signal_sentence(signals: dict[str, Any]) -> str:
    topics = signals.get("topics", {})
    return (
        f"文献证据共 {signals.get('total', 0)} 条，其中 PubMed {signals.get('pubmed', 0)} 条、PMC/开放全文 {signals.get('pmc', 0)} 条；"
        f"已解析文本 {signals.get('extracted', 0)} 份，含摘要 {signals.get('with_abstract', 0)} 条，含结构化 Abstract {signals.get('with_structured', 0)} 条。"
        f"血液/血浆/血清相关 {topics.get('blood', 0)} 条，CSF 相关 {topics.get('csf', 0)} 条，MCI/早期认知障碍相关 {topics.get('mci', 0)} 条，"
        f"诊断/鉴别诊断相关 {topics.get('diagnosis', 0)} 条，筛查/分诊相关 {topics.get('screening', 0)} 条，风险预测/进展相关 {topics.get('risk', 0)} 条。"
    )


def _respiratory_signal_sentence(signals: dict[str, Any]) -> str:
    topics = signals.get("topics", {})
    return (
        f"文献证据共 {signals.get('total', 0)} 条，其中 PubMed {signals.get('pubmed', 0)} 条、PMC/开放全文 {signals.get('pmc', 0)} 条；"
        f"已解析文本 {signals.get('extracted', 0)} 份，含摘要 {signals.get('with_abstract', 0)} 条，含结构化 Abstract {signals.get('with_structured', 0)} 条。"
        f"甲型流感相关 {topics.get('influenza_a', 0)} 条，乙型流感相关 {topics.get('influenza_b', 0)} 条，多重/联检相关 {topics.get('multiplex', 0)} 条，"
        f"RT-PCR/核酸检测相关 {topics.get('pcr', 0)} 条，呼吸道样本相关 {topics.get('respiratory_sample', 0)} 条，性能评价相关 {topics.get('performance', 0)} 条。"
    )


def build_respiratory_project_analysis_sections(
    *,
    literature_materials: list[dict],
    regulatory_materials: list[dict],
    competitor_materials: list[dict],
    standard_materials: list[dict],
    patent_materials: list[dict],
    materials: list[dict],
    confirmations: dict | None = None,
) -> list[dict[str, Any]]:
    signals = build_respiratory_signal_summary(literature_materials, confirmations)
    topics = signals["topics"]
    examples = signals["examples"]
    signal_text = _respiratory_signal_sentence(signals)
    literature_titles = _material_title_join(literature_materials)
    competitor_titles = _material_title_join(competitor_materials)
    regulatory_titles = _material_title_join(regulatory_materials)
    standard_titles = _material_title_join(standard_materials)
    patent_titles = _material_title_join(patent_materials)
    marker = "甲型/乙型流感多重联检"
    return [
        {
            "id": "analysis-1",
            "title": "临床意义",
            "analysis": (
                f"{signal_text} 从研发立项角度看，{marker} 的核心价值是把发热、咳嗽等呼吸道感染症状人群中的甲流、乙流和可能的其他病原体快速区分，"
                "减少经验用药和重复送检，为抗病毒药物使用、院感管理和季节性流行监测提供依据。"
            ),
            "evidence": f"流感线索：{examples['influenza']}；多重检测线索：{examples['multiplex']}",
            "gap": "需要按临床场景拆出门急诊、住院、儿童、老年/基础病人群的真实检测需求和结果解释边界。",
        },
        {
            "id": "analysis-2",
            "title": "临床应用定位",
            "analysis": (
                f"{marker} 更适合作为呼吸道感染病原体鉴别诊断和快速分流工具，不宜表述为所有呼吸道感染的一次性确诊方案。"
                "如果首版只覆盖甲流/乙流，应明确是否与 SARS-CoV-2、RSV 或其他呼吸道病原体形成组合或后续扩展。"
            ),
            "evidence": f"诊断/鉴别诊断相关 {topics.get('diagnosis', 0)} 条；RT-PCR/核酸检测相关 {topics.get('pcr', 0)} 条。",
            "gap": "需要确认预期用途是否为体外诊断、辅助诊断、病原体鉴别、筛查或多病原体联检。",
        },
        {
            "id": "analysis-3",
            "title": "目标使用场景",
            "analysis": (
                "优先场景应放在发热门诊、急诊、儿科、呼吸科、感染科、基层医疗机构和医院检验科。"
                "多重联检的价值来自同一份样本、同一流程同时给出多个病原体结果，适合症状重叠且流行季病原体混杂的场景。"
            ),
            "evidence": f"门急诊/住院相关 {topics.get('hospital', 0)} 条；样本线索：{examples['sample']}",
            "gap": "需要补充实际检测周转时间、样本转运流程、报告时限和阳性结果处置路径。",
        },
        {
            "id": "analysis-4",
            "title": "目标人群",
            "analysis": (
                "目标人群应定义为出现急性呼吸道感染症状、疑似流感或需病原体鉴别的人群。儿童、老年人、孕妇、慢病和免疫低下人群可能是高价值应用对象，"
                "但说明书人群边界需要与临床评价样本保持一致。"
            ),
            "evidence": f"儿童相关 {topics.get('children', 0)} 条；流感相关材料：{examples['influenza']}",
            "gap": "需要明确无症状筛查、院感筛查、社区监测是否纳入首版预期用途。",
        },
        {
            "id": "analysis-5",
            "title": "诊疗路径",
            "analysis": (
                "合理路径是临床症状和流行病学判断后采集呼吸道样本，实验室检测后将甲流/乙流阳性、阴性和多病原体结果反馈给临床。"
                "结果需要结合发病时间、采样质量、抗病毒用药窗口和其他检查结果解释。"
            ),
            "evidence": literature_titles,
            "gap": "需要把检测结果与用药、隔离、复测和其他病原体检测策略对应起来。",
        },
        {
            "id": "analysis-6",
            "title": "金标准与参照方法",
            "analysis": (
                "核酸类多重联检通常需要与已上市核酸检测产品、单重 RT-PCR、测序或临床综合诊断进行一致性/符合率评价。"
                "如果采用抗原或免疫层析方法，则参照和性能指标体系会不同。"
            ),
            "evidence": f"PCR/核酸参照相关 {topics.get('pcr', 0)} 条；性能线索：{examples['performance']}",
            "gap": "需要确定参照试剂、阳性/阴性判定规则、临界 Ct 值处理和不一致样本复核方法。",
        },
        {
            "id": "analysis-7",
            "title": "指南与共识",
            "analysis": (
                "指南与共识主要用于确认流感检测适用场景、采样时机、样本类型和检测结果解释。"
                "自动材料可作为入口，但正式注册和临床应用仍需补齐中国本土指南、行业标准和监管要求。"
            ),
            "evidence": f"指南/标准相关 {topics.get('standard', 0)} 条；标准材料：{standard_titles}",
            "gap": "需要补充中国流感诊疗方案、病原学检测指南、呼吸道样本采集规范和实验室管理要求。",
        },
        {
            "id": "analysis-8",
            "title": "专家和组织意见",
            "analysis": (
                "专家和组织意见应围绕流感季节性流行、儿科/急诊快速分诊、抗病毒药物窗口期和多病原体鉴别价值展开。"
                "目前自动材料只能提供公开文献和规范线索，不能替代 KOL 访谈。"
            ),
            "evidence": literature_titles,
            "gap": "需要补充临床专家、检验科、注册和市场人员对首版检测组合的确认意见。",
        },
        {
            "id": "analysis-9",
            "title": "市场准入与收费",
            "analysis": (
                "多重联检的商业可行性取决于检测收费、医院项目立项、医保/自费接受度、试剂成本和仪器平台装机基础。"
                "文献不能直接证明准入可行，需要另行采集真实价格和渠道证据。"
            ),
            "evidence": "当前材料以文献、注册、标准和专利为主。",
            "gap": "需要补充各地区收费项目、同类产品价格、医院采购路径和基层/门急诊使用成本。",
        },
        {
            "id": "analysis-10",
            "title": "市场定位",
            "analysis": (
                f"{marker} 的定位可从“流感季快速鉴别”“发热门诊/急诊病原体分流”“实验室多病原体核酸检测菜单”三条线评估。"
                "如果只做甲乙流，优势在简单明确；如果扩展到多病原体 panel，价值更高但注册、成本和性能验证复杂度也更高。"
            ),
            "evidence": competitor_titles,
            "gap": "需要确认首版组合、目标医院层级、仪器平台、通量和检测成本。",
        },
        {
            "id": "analysis-11",
            "title": "竞争格局",
            "analysis": (
                f"已导入 {len(competitor_materials)} 条竞品/同类注册线索。竞争分析需要重点看已上市产品的检测靶标组合、方法学、样本类型、适用仪器、注册证有效期和说明书预期用途。"
            ),
            "evidence": competitor_titles,
            "gap": "NMPA 官方注册字段、说明书、产品组成和有效期需要逐项复核。",
        },
        {
            "id": "analysis-12",
            "title": "出口与注册",
            "analysis": (
                "呼吸道病原体 IVD 产品注册论证需要明确管理类别、适用样本、检测靶标、阳性判断、交叉反应、干扰和临床评价路径。"
                "国内注册、欧盟 IVDR 和美国 FDA 路径的证据要求不同，不能混用结论。"
            ),
            "evidence": regulatory_titles,
            "gap": "需要确认中国注册分类、临床试验/同品种比对路径、海外目标市场和申报资料清单。",
        },
        {
            "id": "analysis-13",
            "title": "技术可行性",
            "analysis": (
                f"多重联检技术风险集中在多靶标引物/探针兼容性、交叉反应、不同病毒载量下的 LoD、内参控制、污染防控和样本前处理。"
                f"当前文献中 RT-PCR/核酸检测相关 {topics.get('pcr', 0)} 条，性能评价相关 {topics.get('performance', 0)} 条，可作为方案设计入口。"
            ),
            "evidence": f"方法学线索：{examples['pcr']}；性能线索：{examples['performance']}",
            "gap": "需要研发输出 LoD、包容性、交叉反应、干扰、精密度、稳定性、阳性符合率和阴性符合率验证方案。",
        },
        {
            "id": "analysis-14",
            "title": "参考物质",
            "analysis": (
                "甲流/乙流多重联检需要稳定可获得的阳性质控、阴性质控、内参、假病毒/灭活病毒或标准品。"
                "参考物质缺失会影响 LoD、批间一致性、室内质控和注册资料可信度。"
            ),
            "evidence": standard_titles,
            "gap": "需要确认甲流、乙流各亚型/代表株参考物质、质控品和校准/判定体系。",
        },
        {
            "id": "analysis-15",
            "title": "安全要求",
            "analysis": (
                "安全风险主要来自假阴性导致延误抗病毒治疗或院感控制，假阳性导致不必要用药、隔离或焦虑。"
                "核酸检测还要关注样本采集、防污染、实验室生物安全和结果报告解释。"
            ),
            "evidence": regulatory_titles,
            "gap": "需要形成风险分析、警示语、结果解释模板、复测策略和实验室操作安全要求。",
        },
        {
            "id": "analysis-16",
            "title": "原材料可获得性",
            "analysis": (
                "关键原材料包括引物、探针、酶体系、缓冲液、内参、阳性质控和采样/保存体系。"
                "多重体系的供应风险不只在单个原料，还在不同批次组合后的兼容性和稳定性。"
            ),
            "evidence": patent_titles,
            "gap": "需要补充核心引物/探针设计边界、关键酶供应、质控材料和专利风险。",
        },
        {
            "id": "analysis-17",
            "title": "其他发现 / 待归类线索",
            "analysis": (
                "当前剩余缺口主要集中在 NMPA 字段核验、PatentHub 登录后的专利全文/FTO、中文全文来源、标准条款和业务专家复核。"
            ),
            "evidence": f"当前材料总数 {len(materials)} 条。",
            "gap": "应将缺口转为 Excel 补证任务，逐项关闭后再形成正式立项结论。",
        },
    ]


def _generic_signal_sentence(signals: dict[str, Any]) -> str:
    topics = signals.get("topics", {})
    return (
        f"文献证据共 {signals.get('total', 0)} 条，其中 PubMed {signals.get('pubmed', 0)} 条、PMC/开放全文 {signals.get('pmc', 0)} 条；"
        f"已解析文本 {signals.get('extracted', 0)} 份，含摘要 {signals.get('with_abstract', 0)} 条，含结构化 Abstract {signals.get('with_structured', 0)} 条。"
        f"诊断/鉴别诊断相关 {topics.get('diagnosis', 0)} 条，筛查/分诊相关 {topics.get('screening', 0)} 条，"
        f"风险预测/进展相关 {topics.get('risk', 0)} 条，已确认平台/方法学相关 {topics.get('methodology', 0)} 条，"
        f"性能评价相关 {topics.get('auc', 0)} 条。"
    )


def build_generic_project_analysis_sections(
    *,
    literature_materials: list[dict],
    regulatory_materials: list[dict],
    competitor_materials: list[dict],
    standard_materials: list[dict],
    patent_materials: list[dict],
    materials: list[dict],
    confirmations: dict | None = None,
) -> list[dict[str, Any]]:
    signals = build_literature_signal_summary(literature_materials, confirmations)
    marker = signals["marker"]
    topics = signals["topics"]
    examples = signals["examples"]
    signal_text = _generic_signal_sentence(signals)
    literature_titles = _material_title_join(literature_materials)
    competitor_titles = _material_title_join(competitor_materials)
    regulatory_titles = _material_title_join(regulatory_materials)
    standard_titles = _material_title_join(standard_materials)
    patent_titles = _material_title_join(patent_materials)
    sample_type = str((confirmations or {}).get("sample_type") or "已确认样本类型")
    platform = str((confirmations or {}).get("platform") or (confirmations or {}).get("methodology") or "已确认平台/方法学")
    intended_use = str((confirmations or {}).get("intended_use") or "已确认预期用途")
    return [
        {
            "id": "analysis-1",
            "title": "临床意义",
            "analysis": (
                f"{signal_text} 综合题名、摘要和全文线索看，{marker} 的项目价值应围绕目标临床场景、样本类型、检测平台和结果解释边界展开。"
                f"当前材料更适合用于判断 {intended_use} 中的临床需求和验证重点。"
            ),
            "evidence": f"{examples['diagnosis']}；{literature_titles}",
            "gap": "需要按目标场景进一步提取灵敏度、特异性、cut-off、线性范围、样本量和参照方法。",
        },
        {
            "id": "analysis-2",
            "title": "临床应用定位",
            "analysis": (
                f"诊断/鉴别诊断相关文献 {topics.get('diagnosis', 0)} 条，筛查/分诊相关 {topics.get('screening', 0)} 条，风险预测/进展相关 {topics.get('risk', 0)} 条。"
                f"{marker} 应按“{intended_use}”定位，并在说明书中明确适用人群、样本类型、结果解释和不能覆盖的临床边界。"
            ),
            "evidence": f"诊断线索：{examples['diagnosis']}；风险线索：{examples['risk']}",
            "gap": "需要医学和注册人员确认预期用途表述、阳性/阴性解释、复测建议和联合检查建议。",
        },
        {
            "id": "analysis-3",
            "title": "目标使用场景",
            "analysis": (
                f"当前确认的样本/方法画像为 {sample_type}、{platform}。目标使用场景应从临床科室入口、检测时机、报告周转时间、"
                "是否用于急诊/门诊/住院流程以及是否需要定量趋势监测等维度拆分。"
            ),
            "evidence": literature_titles,
            "gap": "需要补充真实临床流程、检测频次、报告解释、复测策略和科室使用场景。",
        },
        {
            "id": "analysis-4",
            "title": "目标人群",
            "analysis": (
                f"目标人群不能只由检索题名推断，应结合 {marker} 的预期用途、样本采集条件、禁忌或干扰因素、"
                "临床路径和同类产品说明书共同确定。"
            ),
            "evidence": literature_titles,
            "gap": "需要按人群、样本来源、疾病/生理状态和检测时间窗形成适用性说明。",
        },
        {
            "id": "analysis-5",
            "title": "诊疗路径",
            "analysis": (
                f"{marker} 在诊疗路径中的位置应通过参照方法、联合检查、复测策略和报告解释规则确定。"
                "自动检索材料只能提供线索，不能替代临床专家对实际路径的确认。"
            ),
            "evidence": examples["reference"],
            "gap": "需要确认目标科室、前置检查、后续处置、复测间隔和结果解释模板。",
        },
        {
            "id": "analysis-6",
            "title": "金标准与参照方法",
            "analysis": (
                f"研发方案应为 {marker} 明确定量或定性参照方法，包括同类已上市产品、实验室参考方法、临床判定标准和样本配对策略。"
                "不同用途下的参照标准和一致性指标不应混用。"
            ),
            "evidence": literature_titles,
            "gap": "需要定义性能评价中的参照标准、样本量、统计指标和一致性评价方法。",
        },
        {
            "id": "analysis-7",
            "title": "指南与共识",
            "analysis": (
                f"若 {marker} 对应场景已有指南、共识或临床路径文件，应优先用于界定检测目的、适用人群和报告解释边界。"
                "中文指南/全文来源未命中时，不能直接用国际文献替代本土注册和临床使用语境。"
            ),
            "evidence": literature_titles,
            "gap": "需要补齐指南、共识、临床路径和本土使用建议。",
        },
        {
            "id": "analysis-8",
            "title": "专家和组织意见",
            "analysis": (
                "现阶段专家和组织意见主要来自指南、综述、协会文件和高质量临床研究。"
                f"这些材料可用于初步判断 {marker} 的临床解释边界，但仍需企业内部医学、注册、研发和市场人员复核。"
            ),
            "evidence": literature_titles,
            "gap": "需要补充临床专家、注册人员、研发负责人和产品负责人的确认意见。",
        },
        {
            "id": "analysis-9",
            "title": "市场准入与收费",
            "analysis": (
                f"文献证据可以证明 {marker} 的临床关注度和技术可行线索，但不能直接证明收费、准入和商业化可行性。"
                "应单独采集医院项目立项、收费编码、竞品价格、套餐组合和渠道资料。"
            ),
            "evidence": "暂无直接材料",
            "gap": "需要补充收费项目、地区准入、采购渠道、竞品价格和目标医院层级。",
        },
        {
            "id": "analysis-10",
            "title": "市场定位",
            "analysis": (
                f"{marker} 的市场定位应由临床刚需、同类产品格局、方法学差异、检测成本和报告解释难度共同决定。"
                "当前自动材料更适合作为定位初筛，不能直接替代市场调研。"
            ),
            "evidence": competitor_titles,
            "gap": "需要补充目标医院层级、科室入口、检测套餐组合和商业化路径。",
        },
        {
            "id": "analysis-11",
            "title": "竞争格局",
            "analysis": (
                f"已导入 {len(competitor_materials)} 条竞品/同类注册线索。若 NMPA 采集未完成，竞争格局只能作为初步判断，"
                f"重点应核验 {marker} 直接竞品、相邻检测项目、平台方法学和样本类型差异。"
            ),
            "evidence": competitor_titles,
            "gap": "NMPA 官方数据库字段、注册证状态、说明书和有效期需要人工核验。",
        },
        {
            "id": "analysis-12",
            "title": "出口与注册",
            "analysis": (
                f"已导入 {len(regulatory_materials)} 条法规/注册材料。对 {marker} 项目，注册论证应拆分为分类界定、预期用途、样本类型、"
                "临床评价路径、同品种比对可能性和性能验证要求。"
            ),
            "evidence": regulatory_titles,
            "gap": "需要确认分类编码、临床评价路径、同品种比对可能性和注册申报资料清单。",
        },
        {
            "id": "analysis-13",
            "title": "技术可行性",
            "analysis": (
                f"与已确认平台/方法学相关的文献线索 {topics.get('methodology', 0)} 条，性能评价相关 {topics.get('auc', 0)} 条。"
                f"{marker} 的技术可行性应结合 {platform}，围绕检测限/定量限、线性或报告范围、精密度、准确度、分析特异性、干扰和样本稳定性验证。"
            ),
            "evidence": "；".join([examples["methodology"], competitor_titles]),
            "gap": "需要研发按目标方法学输出检出限/定量限、报告范围、精密度、准确度、分析特异性、干扰和稳定性验证方案。",
        },
        {
            "id": "analysis-14",
            "title": "参考物质",
            "analysis": (
                f"{marker} 的定量或半定量转化依赖校准品、质控品、溯源体系和平台一致性。"
                "若参考物质和校准体系证据不足，后续可能影响批间一致性、不同平台可比性和判定阈值固化。"
            ),
            "evidence": standard_titles,
            "gap": "需要确认适用参考物质、校准品、质控品、溯源体系和批间一致性方案。",
        },
        {
            "id": "analysis-15",
            "title": "安全要求",
            "analysis": (
                f"{marker} 检测的安全风险主要来自假阳性、假阴性、灰区结果、干扰因素和过度解释。"
                "报告应明确适用限制、复测建议和不能单独承担的临床判断。"
            ),
            "evidence": regulatory_titles,
            "gap": "需要形成风险分析、适用限制、警示语和结果解释模板。",
        },
        {
            "id": "analysis-16",
            "title": "原材料可获得性",
            "analysis": (
                f"{marker} 方法学转化依赖稳定的核心识别试剂、反应/标记体系、校准品、质控材料和配套耗材。"
                "需要同步评估关键原料供应、批间一致性、成本和平台适配风险。"
            ),
            "evidence": patent_titles,
            "gap": "需要补充关键识别试剂、反应组分、耗材、校准品和质控品供应风险。",
        },
        {
            "id": "analysis-17",
            "title": "其他发现 / 待归类线索",
            "analysis": "当前最大剩余缺口集中在注册字段核验、专利全文/FTO、中文全文、全文/PDF 可追溯材料和业务专家复核。",
            "evidence": f"当前材料总数 {len(materials)} 条",
            "gap": "应将缺口转为 Excel 补证任务，逐项关闭后再形成正式立项结论。",
        },
    ]


def build_project_analysis_sections(
    *,
    literature_materials: list[dict],
    regulatory_materials: list[dict],
    competitor_materials: list[dict],
    standard_materials: list[dict],
    patent_materials: list[dict],
    materials: list[dict],
    confirmations: dict | None = None,
) -> list[dict[str, Any]]:
    topic = str((confirmations or {}).get("primary_query") or "")
    if _is_respiratory_project(materials, confirmations, topic):
        return build_respiratory_project_analysis_sections(
            literature_materials=literature_materials,
            regulatory_materials=regulatory_materials,
            competitor_materials=competitor_materials,
            standard_materials=standard_materials,
            patent_materials=patent_materials,
            materials=materials,
            confirmations=confirmations,
        )
    if not _is_ad_biomarker_project(materials, confirmations, topic):
        return build_generic_project_analysis_sections(
            literature_materials=literature_materials,
            regulatory_materials=regulatory_materials,
            competitor_materials=competitor_materials,
            standard_materials=standard_materials,
            patent_materials=patent_materials,
            materials=materials,
            confirmations=confirmations,
        )
    signals = build_literature_signal_summary(literature_materials, confirmations)
    marker = signals["marker"]
    topics = signals["topics"]
    examples = signals["examples"]
    signal_text = _signal_sentence(signals)
    literature_titles = _material_title_join(literature_materials)
    competitor_titles = _material_title_join(competitor_materials)
    regulatory_titles = _material_title_join(regulatory_materials)
    standard_titles = _material_title_join(standard_materials)
    patent_titles = _material_title_join(patent_materials)
    return [
        {
            "id": "analysis-1",
            "title": "临床意义",
            "analysis": (
                f"{signal_text} 综合题名、摘要和全文线索看，{marker} 不是单纯研究性概念，证据集中在 AD 病理识别、MCI/早期人群分层、"
                "血液标志物替代或前置 PET/CSF 检查、以及临床试验/专科诊疗路径中的辅助判断。"
            ),
            "evidence": f"{examples['blood']}；{examples['diagnosis']}",
            "gap": "需要按人群、疾病阶段、参照方法和检测平台进一步提取 AUC、灵敏度、特异性、cut-off 和灰区比例。",
        },
        {
            "id": "analysis-2",
            "title": "临床应用定位",
            "analysis": (
                f"诊断/鉴别诊断相关文献 {topics.get('diagnosis', 0)} 条，筛查/分诊相关 {topics.get('screening', 0)} 条，风险预测/进展相关 {topics.get('risk', 0)} 条。"
                f"因此 {marker} 更适合作为 AD 辅助诊断、Aβ/Tau 病理阳性预测、认知下降风险分层和临床研究入组辅助，不宜单独声明为确诊工具。"
            ),
            "evidence": f"诊断线索：{examples['diagnosis']}；风险线索：{examples['risk']}",
            "gap": "需要医学人员确认说明书适用人群、排除条件、阴阳性解释和联合检查建议。",
        },
        {
            "id": "analysis-3",
            "title": "目标使用场景",
            "analysis": (
                f"文献中血液样本相关 {topics.get('blood', 0)} 条、MCI/早期认知障碍相关 {topics.get('mci', 0)} 条，提示首要场景应放在记忆门诊、神经内科、老年医学科、"
                "认知障碍专病门诊和临床研究队列，而不是泛人群无症状筛查。"
            ),
            "evidence": examples["blood"],
            "gap": "需要补充真实临床路径、检测频次、报告解释和复测策略。",
        },
        {
            "id": "analysis-4",
            "title": "目标人群",
            "analysis": (
                f"现有文献更集中于疑似 AD、MCI、主观认知下降、AD 连续谱及专科就诊人群。血液相关线索 {topics.get('blood', 0)} 条，"
                f"CSF 相关线索 {topics.get('csf', 0)} 条，说明血液 {marker} 可作为低创入口，但 CSF/PET 仍是重要参照。"
            ),
            "evidence": literature_titles,
            "gap": "需要按血浆、血清、全血分别形成样本适用性和干扰因素评估。",
        },
        {
            "id": "analysis-5",
            "title": "诊疗路径",
            "analysis": (
                f"文献中 amyloid PET/Aβ 病理参照相关 {topics.get('amyloid_reference', 0)} 条，tau PET/Tau 病理参照相关 {topics.get('tau_reference', 0)} 条，"
                f"CSF 参照相关 {topics.get('csf_reference', 0)} 条。{marker} 的合理位置是 PET/CSF 前的前置分层和转诊辅助，阳性/阴性解释应结合临床量表、影像和病史。"
            ),
            "evidence": examples["reference"],
            "gap": "需要确认与量表、影像、CSF、Aβ/tau 组合检测的推荐顺序。",
        },
        {
            "id": "analysis-6",
            "title": "金标准与参照方法",
            "analysis": (
                f"当前证据显示，{marker} 的评价参照不应只使用临床诊断标签。研发方案应优先定义 amyloid PET/Aβ 病理、tau PET/Tau 病理、CSF Aβ42/40 与 p-tau 等参照方法，"
                "并区分病理阳性预测、临床诊断辅助和疾病进展预测三类终点。"
            ),
            "evidence": literature_titles,
            "gap": "需要定义临床试验/性能评价中采用的参照标准和一致性评价指标。",
        },
        {
            "id": "analysis-7",
            "title": "指南与共识",
            "analysis": (
                f"文献池中指南、共识、临床实践建议和血液标志物框架性文献应优先用于确定 {marker} 的临床解释边界。"
                "自动检索结果显示该方向已有较多国际讨论，但中文指南/全文来源若未命中，不能直接替代本土注册和临床使用语境。"
            ),
            "evidence": literature_titles,
            "gap": "需要补齐中文期刊原文、指南推荐等级和引用证据级别。",
        },
        {
            "id": "analysis-8",
            "title": "专家和组织意见",
            "analysis": (
                "现阶段专家和组织意见主要间接来自指南、共识、协会实践建议和高被引综述。"
                f"这些材料可用于界定 {marker} 的适用场景、报告解释边界和与 PET/CSF 的关系，但不能替代企业内部 KOL 访谈和本地临床路径确认。"
            ),
            "evidence": literature_titles,
            "gap": "需要补充临床专家、注册人员和研发负责人的确认意见。",
        },
        {
            "id": "analysis-9",
            "title": "市场准入与收费",
            "analysis": (
                f"文献证据可以证明 {marker} 的临床需求和技术关注度，但不能直接证明市场准入可行性。"
                "收费编码、医保支付、医院检验项目立项、套餐组合和患者自费接受度需要另行采集真实市场材料。"
            ),
            "evidence": "暂无直接材料",
            "gap": "需要补充收费项目、地区准入、采购渠道和竞品价格信息。",
        },
        {
            "id": "analysis-10",
            "title": "市场定位",
            "analysis": (
                f"从文献主题看，{marker} 更适合作为 AD 血液标志物组合或单项高价值标志物进入专科辅助诊断、病理阳性预测和研究入组场景。"
                "商业定位上应强调低创、可及、可前置分层，而不是替代 PET/CSF 或单项确诊。"
            ),
            "evidence": competitor_titles,
            "gap": "需要补充目标医院层级、科室入口、检测套餐组合和商业化路径。",
        },
        {
            "id": "analysis-11",
            "title": "竞争格局",
            "analysis": (
                f"已导入 {len(competitor_materials)} 条竞品/同类注册线索。若 NMPA 采集未完成，当前竞争格局只能从文献、公开产品和相邻标志物推断，"
                f"重点关注 {marker}、其他 p-tau 标志物、Aβ42/40、NfL 等组合方案以及化学发光/免疫检测平台差异。"
            ),
            "evidence": competitor_titles,
            "gap": "NMPA 官方数据库字段、注册证状态、说明书和有效期需要人工核验。",
        },
        {
            "id": "analysis-12",
            "title": "出口与注册",
            "analysis": (
                f"已导入 {len(regulatory_materials)} 条法规/注册材料。对 {marker} 这类 AD 血液标志物项目，注册论证不能只看文献有效性，"
                "还要把预期用途、样本类型、临床评价路径、同品种比对可能性和性能验证要求拆开确认。"
            ),
            "evidence": regulatory_titles,
            "gap": "需要确认分类编码、临床评价路径、同品种比对可能性和注册申报资料清单。",
        },
        {
            "id": "analysis-13",
            "title": "技术可行性",
            "analysis": (
                f"免疫检测/ELISA/Simoa/化学发光/ECL 等平台相关文献 {topics.get('immunoassay', 0)} 条，AUC/灵敏度/特异性等性能评价相关 {topics.get('auc', 0)} 条。"
                f"{marker} 在血液中丰度低、前分析变量敏感，对抗体特异性、校准体系、批间一致性、基质效应和样本稳定性要求较高；技术可行性应从研究平台向可注册 IVD 平台做桥接验证。"
            ),
            "evidence": "；".join([examples["technology"], competitor_titles]),
            "gap": "需要研发输出 LoD/LoQ、线性、精密度、hook、交叉反应、干扰和稳定性验证方案。",
        },
        {
            "id": "analysis-14",
            "title": "参考物质",
            "analysis": (
                f"{marker} 的立项风险不只在临床价值，还在可溯源校准体系、质控品和平台一致性。"
                "若无明确参考物质/校准品证据，后续性能转化可能出现批间一致性、不同平台可比性和 cut-off 固化困难。"
            ),
            "evidence": standard_titles,
            "gap": "需要确认抗原/抗体、校准品、质控品、溯源体系和批间一致性方案。",
        },
        {
            "id": "analysis-15",
            "title": "安全要求",
            "analysis": (
                f"{marker} 检测的安全风险主要来自假阳性、假阴性、灰区结果和过度解释。"
                "对于认知障碍和 AD 风险场景，报告必须强调辅助判断属性，避免患者或医生将单项结果理解为确诊或排除诊断。"
            ),
            "evidence": regulatory_titles,
            "gap": "需要形成风险分析、适用限制、警示语和结果解释模板。",
        },
        {
            "id": "analysis-16",
            "title": "原材料可获得性",
            "analysis": (
                f"{marker} 方法学转化依赖高特异性抗体、稳定抗原/校准品、质控材料和低背景检测体系。"
                "如果无法确认抗体表位、交叉反应、供应稳定性和校准体系，临床文献再充分也不能直接转化为可注册产品。"
            ),
            "evidence": patent_titles,
            "gap": "需要补充关键抗体、抗原、磁珠/发光底物、校准品和质控品供应风险。",
        },
        {
            "id": "analysis-17",
            "title": "其他发现 / 待归类线索",
            "analysis": "当前最大剩余缺口集中在 NMPA 字段核验、专利全文/FTO、中文期刊全文、PMC PDF 下载和业务专家复核。",
            "evidence": f"当前材料总数 {len(materials)} 条",
            "gap": "应将缺口转为 Excel 补证任务，逐项关闭后再形成正式立项结论。",
        },
    ]


def build_business_action_rows(
    *,
    regulatory_materials: list[dict],
    competitor_materials: list[dict],
    standard_materials: list[dict],
    patent_materials: list[dict],
    literature_materials: list[dict],
    scenario_map: dict[str, dict],
    confirmations: dict | None = None,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    all_materials = literature_materials + regulatory_materials + competitor_materials + standard_materials + patent_materials
    is_respiratory = _is_respiratory_project(all_materials, confirmations)
    is_ad_project = _is_ad_biomarker_project(all_materials, confirmations)
    marker = _target_marker(literature_materials, confirmations)
    if not competitor_materials:
        rows.append(
            {
                "priority": "P0",
                "owner": "注册 / 产品",
                "action": (
                    f"补查 NMPA 同类产品，确认是否已有 {marker} 或相近呼吸道病原体 IVD 注册路径/产品。"
                    if is_respiratory
                    else (
                        f"补查 NMPA 同类产品，确认是否已有 AD 血液 {marker} 或相近 AD 标志物 IVD 注册路径/产品。"
                        if is_ad_project
                        else f"补查 NMPA 同类产品，确认是否已有 {marker} 或相近检测项目 IVD 注册路径/产品。"
                    )
                ),
                "acceptance": "形成同类产品清单，至少包含注册证编号、注册人、方法学、样本类型、预期用途和有效期。",
            }
        )
    if not regulatory_materials:
        rows.append(
            {
                "priority": "P0",
                "owner": "注册",
                "action": "补查 CMDE/NMPA 指导原则、审评报告和临床评价相关文件。",
                "acceptance": "明确注册分类、性能评价、临床评价和申报资料要求；无法命中专门文件时给出可类比文件。",
            }
        )
    if not standard_materials:
        rows.append(
            {
                "priority": "P0",
                "owner": "研发 / 质量",
                "action": "补查现行国家、行业和团体标准，确认性能验证、样本处理和安全要求。",
                "acceptance": "形成适用标准清单，标注标准状态、标准号、适用条款和与本项目的关系。",
            }
        )
    if not patent_materials:
        rows.append(
            {
                "priority": "P1",
                "owner": "研发 / 知识产权",
                "action": (
                    f"补查 {marker}、血液检测、免疫检测平台和 AD 辅助诊断相关专利。"
                    if is_ad_project
                    else f"补查 {marker}、目标样本、目标方法学和同类 IVD 试剂盒相关专利。"
                ),
                "acceptance": "形成高相关专利清单，并标注可能阻碍、可绕开点和需法务复核项。",
            }
        )
    if literature_materials:
        rows.append(
            {
                "priority": "P1",
                "owner": "医学 / 研发",
                "action": (
                    "复核已采集文献，筛出真正支持目标血液标志物 IVD 立项的核心证据。"
                    if is_ad_project
                    else "复核已采集文献，筛出真正支持目标检测项目 IVD 立项的核心证据。"
                ),
                "acceptance": "每条纳入证据明确对应临床意义、目标人群、诊疗路径、性能指标或技术可行性。",
            }
        )
    if scenario_map.get("pmc_fulltext", {}).get("status") == "completed":
        rows.append(
            {
                "priority": "P2",
                "owner": "医学 / 项目助理",
                "action": "核验 PMC 全文和开放 PDF 链接，补充不可下载全文的替代来源或人工下载记录。",
                "acceptance": "核心文献至少保留 PMID/PMCID/DOI、页面链接、摘要或正文摘录和全文获取状态。",
            }
        )
    return rows


def build_evidence_gap_rows(
    *,
    literature_materials: list[dict],
    regulatory_materials: list[dict],
    competitor_materials: list[dict],
    standard_materials: list[dict],
    patent_materials: list[dict],
    scenario_map: dict[str, dict],
    confirmations: dict | None = None,
) -> list[dict[str, str]]:
    """Business-facing evidence gaps and supplement tasks."""
    all_materials = literature_materials + regulatory_materials + competitor_materials + standard_materials + patent_materials
    is_respiratory = _is_respiratory_project(
        all_materials,
        confirmations,
    )
    is_ad_project = _is_ad_biomarker_project(all_materials, confirmations)
    sample_gap = (
        {
            "gap": "样本类型证据需要按鼻咽拭子、口咽拭子、鼻拭子等呼吸道样本拆分",
            "current_basis": "当前检索画像包含呼吸道样本和多重联检，但材料中的样本类型尚未统一归类。",
            "missing_evidence": "各样本类型的采样时机、保存液、稳定性、抑制物、采样质量、运输条件和平台适配证据。",
            "impact": "影响产品样本声明、采样器具选择、前处理流程和临床性能验证。",
            "owner": "研发 / 医学 / 注册",
            "acceptance": "形成样本类型对比表，并明确首版产品建议样本和不适用样本。",
        }
        if is_respiratory
        else {
            "gap": (
                "样本类型证据需要按血浆、血清、全血拆分"
                if is_ad_project
                else "样本类型证据需要按本项目确认样本拆分"
            ),
            "current_basis": (
                "当前检索画像包含血浆 / 血清 / 全血，但材料中的样本类型尚未统一归类。"
                if is_ad_project
                else "当前检索画像已包含目标样本类型，但材料中的样本适用性尚未统一归类。"
            ),
            "missing_evidence": "各样本类型的稳定性、采集条件、保存运输、基质效应、干扰和平台适配证据。",
            "impact": "影响产品样本声明、采样器具选择和分析性能验证。",
            "owner": "研发 / 医学",
            "acceptance": "形成样本类型对比表，并明确首版产品建议样本。",
        }
    )
    rows: list[dict[str, str]] = [
        {
            "gap": "NMPA 官方注册字段仍需逐项核验",
            "current_basis": f"已形成 {len(competitor_materials)} 条竞品/同类注册线索。",
            "missing_evidence": "注册证编号、注册人、有效期、产品组成、样本类型、方法学、预期用途、说明书链接。",
            "impact": "影响中国注册参照、同品种比对、竞品定位和说明书边界。",
            "owner": "注册 / 产品",
            "acceptance": "形成可点击来源链接和字段完整的竞品注册清单。",
        },
        {
            "gap": "核心文献的性能指标尚未结构化提取",
            "current_basis": f"已采集 {len(literature_materials)} 条文献材料。",
            "missing_evidence": (
                "LoD、阳性符合率、阴性符合率、灵敏度、特异性、交叉反应、干扰、样本量、参照方法、平台/方法学。"
                if is_respiratory
                else (
                    "AUC、灵敏度、特异性、cut-off、样本量、疾病分期、参照方法、平台/方法学。"
                    if is_ad_project
                    else "LoD/LoQ、线性范围、灵敏度、特异性、cut-off、样本量、参照方法、平台/方法学。"
                )
            ),
            "impact": "影响是否值得立项、性能目标设定和临床试验方案设计。",
            "owner": "医学 / 研发",
            "acceptance": "核心文献逐条形成指标摘要，并映射到项目分析 17 个章节。",
        },
        sample_gap,
        {
            "gap": "专利全文和 FTO 判断不足",
            "current_basis": f"已形成 {len(patent_materials)} 条专利检索/风险线索。",
            "missing_evidence": "高相关专利全文、权利要求、法律状态、地域、到期日、核心原料/反应体系和平台绑定风险。",
            "impact": "影响自由实施、研发路线选择和上市风险。",
            "owner": "知识产权 / 研发",
            "acceptance": "输出高相关专利清单和 FTO 初筛意见。",
        },
        {
            "gap": "参考物质、校准品和关键原材料证据不足",
            "current_basis": f"已形成 {len(standard_materials)} 条标准/性能控制材料。",
            "missing_evidence": "核心识别试剂、反应组分、校准品、质控品、溯源体系、批间一致性和供应风险。",
            "impact": "影响研发可行性、成本、供应稳定性和注册资料完整性。",
            "owner": "研发 / 供应链 / 质量",
            "acceptance": "形成关键原材料清单、候选供应商和验证计划。",
        },
        {
            "gap": "市场准入、收费和商业化路径尚未形成证据",
            "current_basis": "当前材料以文献、注册、法规和专利为主。",
            "missing_evidence": "收费项目、医院科室入口、检测套餐、竞品价格、渠道和目标医院层级。",
            "impact": "影响立项商业价值和首版产品定位。",
            "owner": "市场 / 产品",
            "acceptance": "形成市场定位和准入路径简表。",
        },
    ]
    if scenario_map.get("pmc_fulltext", {}).get("status") == "completed":
        rows.append(
            {
                "gap": "PMC 开放全文已采集，但 PDF 下载状态需逐篇确认",
                "current_basis": scenario_map.get("pmc_fulltext", {}).get("last_message", ""),
                "missing_evidence": "核心文献 PDF、本地快照、正文表格和可引用摘录。",
                "impact": "影响证据复核效率和后续审计追溯。",
                "owner": "项目助理 / 医学",
                "acceptance": "核心 PMC 文献保留原文链接、PMCID、PDF/全文状态和摘录位置。",
            }
        )
    return rows


SCREENING_SAMPLE_TERMS = [
    ("鼻咽拭子", ["鼻咽拭子", "nasopharyngeal", "np swab"]),
    ("口咽拭子", ["口咽拭子", "oropharyngeal", "op swab"]),
    ("鼻拭子", ["鼻拭子", "nasal swab"]),
    ("咽拭子", ["咽拭子", "throat swab", "pharyngeal"]),
    ("痰液", ["痰液", "sputum"]),
    ("呼吸道分泌物", ["呼吸道分泌物", "respiratory specimen", "respiratory sample"]),
    ("血浆", ["血浆", "plasma"]),
    ("血清", ["血清", "serum"]),
    ("全血", ["全血", "whole blood"]),
    ("脑脊液", ["脑脊液", "cerebrospinal fluid", "csf"]),
]

SCREENING_PLATFORM_TERMS = [
    ("多重联检", ["多重", "multiplex", "panel", "联检"]),
    ("多重RT-PCR", ["multiplex rt-pcr", "rt-pcr", "reverse transcription pcr", "实时荧光"]),
    ("荧光PCR", ["荧光pcr", "real-time pcr", "qpcr"]),
    ("核酸检测", ["核酸", "nucleic acid", "molecular"]),
    ("抗原检测", ["抗原", "antigen"]),
    ("免疫层析", ["免疫层析", "lateral flow"]),
    ("化学发光", ["化学发光", "chemiluminescence", "clia"]),
    ("ELISA", ["elisa"]),
    ("NGS", ["ngs", "sequencing", "测序"]),
]

SCREENING_REFERENCE_TERMS = [
    ("临床诊断", ["临床诊断", "clinical diagnosis"]),
    ("PCR/核酸参照", ["pcr", "rt-pcr", "核酸"]),
    ("培养", ["培养", "culture"]),
    ("测序", ["sequencing", "测序"]),
    ("影像/临床路径", ["影像", "imaging", "clinical pathway"]),
    ("NMPA/注册资料", ["nmpa", "注册证", "审评", "cmde"]),
    ("标准/指南", ["标准", "指南", "guideline", "standard"]),
]

SCREENING_USE_TERMS = [
    ("辅助诊断", ["辅助诊断", "diagnosis", "diagnostic"]),
    ("筛查/分诊", ["筛查", "分诊", "screening", "triage"]),
    ("风险分层", ["风险", "分层", "risk", "prediction"]),
    ("病原体鉴别", ["鉴别", "differential", "pathogen", "病原体"]),
    ("注册申报", ["注册", "申报", "nmpa", "cmde"]),
    ("竞品定位", ["竞品", "同类产品", "registration certificate"]),
    ("FTO/专利风险", ["专利", "patent", "claim", "freedom to operate", "fto"]),
    ("方法学验证", ["方法学", "性能", "validation", "verification"]),
]

SCREENING_POPULATION_TERMS = [
    ("儿童", ["儿童", "children", "pediatric", "paediatric"]),
    ("成人", ["成人", "adult"]),
    ("老年人", ["老年", "elderly", "older"]),
    ("疑似感染人群", ["疑似", "suspected", "symptomatic"]),
    ("门急诊人群", ["门诊", "急诊", "outpatient", "emergency"]),
    ("住院/重症人群", ["住院", "重症", "hospitalized", "severe", "icu"]),
    ("认知障碍人群", ["认知", "mci", "dementia", "alzheimer"]),
]

SCREENING_MARKER_TERMS = [
    ("甲型流感", ["甲型流感", "influenza a", "flu a", "甲流"]),
    ("乙型流感", ["乙型流感", "influenza b", "flu b", "乙流"]),
    ("SARS-CoV-2", ["sars-cov-2", "covid"]),
    ("RSV", ["rsv", "respiratory syncytial"]),
    ("呼吸道病原体", ["呼吸道病原体", "respiratory pathogen"]),
    ("p-Tau217", ["p-tau217", "ptau217", "tau217"]),
    ("p-Tau181", ["p-tau181", "ptau181", "tau181"]),
    ("Aβ42/40", ["aβ42/40", "abeta42/40", "amyloid beta 42/40"]),
]


def _screening_marker_terms(confirmations: dict | None) -> list[tuple[str, list[str]]]:
    if not has_confirmed_project_profile(confirmations):
        return SCREENING_MARKER_TERMS
    marker = _target_marker([], confirmations)
    aliases = [marker]
    synonyms = str((confirmations or {}).get("chinese_synonyms", "") or "")
    aliases.extend(
        item.strip()
        for item in re.split(r"[；;、，,|\n]+", synonyms)
        if item.strip()
    )
    return [(marker, list(dict.fromkeys(aliases)))] if marker else []

SCREENING_METRIC_TERMS = [
    ("灵敏度", ["灵敏度", "sensitivity"]),
    ("特异性", ["特异性", "specificity"]),
    ("AUC", ["auc", "area under"]),
    ("cut-off", ["cut-off", "cutoff", "阈值", "截断值"]),
    ("LoD", ["lod", "最低检出限", "limit of detection"]),
    ("一致性/Kappa", ["kappa", "一致性", "agreement"]),
    ("样本量", ["样本量", "sample size", "participants", "patients"]),
    ("精密度", ["精密度", "precision", "repeatability"]),
]


def _screening_text(card: dict, material: dict) -> str:
    raw = material.get("raw_fields") or {}
    parts: list[str] = [
        card.get("title", ""),
        card.get("summary", ""),
        " ".join(card.get("key_facts") or []),
        " ".join(card.get("parameter_facts") or []),
        material.get("title", ""),
        material.get("structured_summary", ""),
        raw.get("abstract", ""),
        raw.get("summary", ""),
        raw.get("scope", ""),
        raw.get("basic_info_text", ""),
        raw.get("full_visible_text", ""),
        raw.get("methodology", ""),
        raw.get("sample_type", ""),
        raw.get("intended_use", ""),
        raw.get("registrant", ""),
    ]
    return " ".join(str(part or "") for part in parts)


def _pick_screening_terms(
    text: str,
    candidates: list[tuple[str, list[str]]],
    *,
    fallback: str = "待复核",
    limit: int = 4,
) -> list[str]:
    lowered = str(text or "").lower()
    values: list[str] = []
    for label, terms in candidates:
        if any(term.lower() in lowered for term in terms):
            values.append(label)
        if len(values) >= limit:
            break
    return values or [fallback]


def _screening_year(material: dict) -> str:
    raw = material.get("raw_fields") or {}
    value = (
        material.get("display_year")
        or str(material.get("publication_date") or "")[:4]
        or str(raw.get("approval_date") or "")[:4]
        or str(raw.get("publication_date") or "")[:4]
    )
    match = re.search(r"\d{4}", str(value or ""))
    return match.group(0) if match else "-"


def _screening_priority(card: dict, material: dict) -> tuple[str, str]:
    material_type = material.get("material_type", "")
    source = material.get("source_scenario", "")
    score = 0
    if material_type in {"regulatory", "competitor", "standard"}:
        score += 2
    if material_type == "patent":
        score += 1
    if source in {"pubmed_literature", "pmc_fulltext", "openalex_literature"}:
        score += 1
    if material.get("pmid") or material.get("pmcid") or material.get("doi"):
        score += 1
    if card.get("parameter_facts") or card.get("metric_facts"):
        score += 3
    if card.get("include_in_report"):
        score += 1
    if material.get("download_files") or material.get("extracted_text_path"):
        score += 1
    if score >= 4:
        return "A", "A 核心必读"
    if score >= 2:
        return "B", "B 重点复核"
    return "C", "C 背景补充"


def _screening_stage(material: dict, card: dict) -> list[str]:
    material_type = material.get("material_type", "")
    text = _screening_text(card, material)
    if material_type == "regulatory":
        return ["注册路径判断"]
    if material_type == "standard":
        return ["性能/标准评价"]
    if material_type == "competitor":
        return ["竞品/注册参照"]
    if material_type == "patent":
        return ["专利/FTO初筛"]
    if _contains_any(text, ["sensitivity", "specificity", "auc", "lod", "灵敏度", "特异性", "最低检出限"]):
        return ["临床性能验证"]
    if _contains_any(text, ["guideline", "consensus", "指南", "共识"]):
        return ["指南/共识判断"]
    return ["临床价值判断"]


def _screening_region(material: dict, text: str) -> list[str]:
    source = material.get("source_scenario", "")
    if source in {"nmpa_competitor", "cmde_regulatory", "standards_current", "chinese_journal"}:
        return ["中国"]
    if _contains_any(text, ["fda", "united states", "美国"]):
        return ["美国"]
    if _contains_any(text, ["ce ", "ivdr", "europe", "欧盟", "欧洲"]):
        return ["欧盟"]
    if _contains_any(text, ["china", "chinese", "中国"]):
        return ["中国"]
    return ["国际/未限定"]


def _screening_evidence_types(material: dict) -> list[str]:
    material_type = material.get("material_type", "")
    source = material.get("source_scenario", "")
    values: list[str] = []
    if source == "pubmed_literature" or material.get("pmid"):
        values.append("PubMed")
    if source == "pmc_fulltext" or material.get("pmcid"):
        values.append("PMC全文")
    if source == "openalex_literature":
        values.append("OpenAlex")
    if material.get("doi"):
        values.append("DOI")
    type_labels = {
        "regulatory": "法规/审评",
        "competitor": "竞品注册",
        "standard": "标准",
        "patent": "专利",
        "literature": "文献",
        "local_import": "本地导入",
    }
    if type_labels.get(material_type) and type_labels[material_type] not in values:
        values.append(type_labels[material_type])
    if material.get("structured_summary"):
        values.append("摘要/正文线索")
    return values or ["来源待复核"]


def _screening_metric_labels(card: dict, text: str) -> list[str]:
    values = _pick_screening_terms(text, SCREENING_METRIC_TERMS, fallback="", limit=6)
    values = [item for item in values if item]
    for fact in card.get("parameter_facts") or []:
        head = str(fact).split("：", 1)[0].strip()
        if head and head not in values:
            values.append(head[:28])
    return values[:6] or ["待提取"]


def _screening_summary_lines(card: dict, material: dict) -> list[str]:
    raw = material.get("raw_fields") or {}
    lines = [
        f"研发用途/应用场景：{'、'.join(card.get('use') or ['待复核'])}",
        f"样本/参照/平台：{'、'.join(card.get('sample') or ['待复核'])} / {'、'.join(card.get('reference') or ['待复核'])} / {'、'.join(card.get('platform') or ['待复核'])}",
        f"参数与组合线索：{'、'.join(card.get('metrics') or ['待提取'])}；{'、'.join(card.get('markers') or ['目标物待复核'])}",
    ]
    if raw.get("registration_certificate_number"):
        lines.append(f"注册证号：{raw.get('registration_certificate_number')}")
    if raw.get("publication_number"):
        lines.append(f"公开/专利号：{raw.get('publication_number')}")
    return lines


def _screening_excerpt_lines(card: dict, material: dict) -> list[str]:
    excerpts = []
    for item in card.get("key_excerpts") or []:
        text = clean_evidence_summary(item.get("text", ""))
        if text:
            excerpts.append(text)
    if card.get("display_facts"):
        excerpts.extend(card.get("display_facts") or [])
    if not excerpts and material.get("abstract_sections_display"):
        excerpts.extend(
            f"{item.get('label')}: {item.get('text')}"
            for item in material.get("abstract_sections_display", [])[:2]
            if item.get("text")
        )
    if not excerpts and material.get("structured_summary"):
        excerpts.append(material["structured_summary"])
    return [str(item)[:700] for item in excerpts[:4]]


EXCERPT_FIELD_PATTERNS = [
    ("journal", "期刊/来源", r"期刊/来源[：:]"),
    ("source", "来源", r"来源[：:]"),
    ("query", "检索式", r"检索式[：:]"),
    ("openalex_id", "OpenAlex ID", r"OpenAlex ID[：:]"),
    ("title", "题名", r"标题[：:]"),
    ("authors", "作者", r"作者[：:]"),
    ("abstract", "Abstract", r"Abstract[：:]"),
]


def _paragraphize_reading_text(text: str, *, max_chars: int = 180) -> list[str]:
    value = " ".join(str(text or "").split())
    if not value:
        return []
    parts = re.split(r"(?<=[。！？；;])\s*|(?<=[.!?])\s+(?=[A-Z])", value)
    paragraphs: list[str] = []
    buffer = ""
    for part in parts:
        piece = part.strip()
        if not piece:
            continue
        if not buffer:
            buffer = piece
            continue
        if len(buffer) + len(piece) + 1 <= max_chars:
            buffer = f"{buffer} {piece}" if re.search(r"[A-Za-z]$", buffer) else f"{buffer}{piece}"
        else:
            paragraphs.append(buffer)
            buffer = piece
    if buffer:
        paragraphs.append(buffer)
    return paragraphs or [value]


def _excerpt_reading_blocks(text: str) -> list[dict[str, Any]]:
    value = " ".join(str(text or "").split())
    if not value:
        return []
    matches: list[tuple[int, int, str, str]] = []
    for key, label, pattern in EXCERPT_FIELD_PATTERNS:
        for match in re.finditer(pattern, value):
            matches.append((match.start(), match.end(), key, label))
    matches.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    selected_reversed: list[tuple[int, int, str, str]] = []
    occupied: list[tuple[int, int]] = []
    for item in matches:
        start, end, _, _ = item
        if any(not (end <= used_start or start >= used_end) for used_start, used_end in occupied):
            continue
        selected_reversed.append(item)
        occupied.append((start, end))
    matches = sorted(selected_reversed, key=lambda item: item[0])
    if not matches:
        return [{"label": "摘录", "paragraphs": _paragraphize_reading_text(value)}]

    blocks: list[dict[str, Any]] = []
    if matches[0][0] > 0:
        prefix = value[: matches[0][0]].strip(" ；;")
        if prefix:
            blocks.append({"label": "摘录", "paragraphs": _paragraphize_reading_text(prefix)})
    for index, (_, end, key, label) in enumerate(matches):
        next_start = matches[index + 1][0] if index + 1 < len(matches) else len(value)
        content = value[end:next_start].strip(" ；;")
        if not content:
            continue
        max_chars = 220 if key == "abstract" else 320
        blocks.append({"label": label, "paragraphs": _paragraphize_reading_text(content, max_chars=max_chars)})
    return blocks


def _screening_translation_items(
    card: dict,
    material: dict,
    translation_cache: dict[tuple[str, str, str], dict[str, Any]] | None = None,
    translation_capability: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    material_id = str(material.get("material_id") or card.get("material_id") or "")
    raw = material.get("raw_fields") or {}
    sections = _structured_abstract_items(raw)
    cache = translation_cache or {}
    capability = translation_capability or {}
    configured = bool(capability.get("configured"))
    command_available = bool(capability.get("command_available", True))

    def cached_translation(field: str, label: str, text: str) -> dict[str, str]:
        digest = text_hash(text)
        cached = cache.get((material_id, field, digest))
        if cached and cached.get("translation_zh"):
            return {
                "label": label,
                "status": "completed",
                "text": str(cached.get("translation_zh") or "").strip(),
                "paragraphs": _paragraphize_reading_text(str(cached.get("translation_zh") or "").strip()),
                "note": "基于已缓存的专业中文翻译。",
            }
        return {}

    items: list[dict[str, str]] = []
    for index, section in enumerate(sections[:3], start=1):
        label = str(section.get("label") or "Abstract")
        text = " ".join(str(section.get("text") or "").split())
        if not text or not is_mostly_english(text):
            continue
        field = f"abstract:{index}:{label}"
        generated = cached_translation(field, label, text)
        if generated:
            items.append(generated)
        else:
            if command_available and not configured:
                text_zh = "内置翻译命令已存在，但当前翻译引擎未就绪，因此尚未生成专业中文阅读版。"
                note = "推荐安装 Argos Translate 离线模型，或接入企业内网 LibreTranslate 服务；完成后运行内置翻译命令生成缓存，再重新生成报告即可显示完整译文。"
                status = "engine_not_ready"
            elif command_available:
                text_zh = "内置翻译命令已配置，但当前任务尚未生成该段专业中文阅读缓存。"
                note = "运行内置翻译命令生成缓存后，再重新生成报告即可显示完整译文。"
                status = "not_generated"
            else:
                text_zh = "当前环境未安装专业中文阅读生成能力。"
                note = "请先安装或启用诺研内置翻译能力。"
                status = "not_available"
            items.append(
                    {
                        "label": label,
                        "status": status,
                        "text": text_zh,
                        "paragraphs": _paragraphize_reading_text(text_zh),
                        "note": note,
                    }
                )
    if not items:
        for excerpt in card.get("excerpt_lines") or []:
            text = " ".join(str(excerpt or "").split())
            if is_mostly_english(text):
                generated = cached_translation("excerpt:1", "Excerpt", text)
                if generated:
                    items.append(generated)
                elif command_available and not configured:
                    text_zh = "内置翻译命令已存在，但当前翻译引擎未就绪，因此尚未生成专业中文阅读版。"
                    note = "当前保留英文原文用于追溯；安装离线模型或接入企业内网翻译服务后可生成中文阅读版。"
                    status = "engine_not_ready"
                else:
                    text_zh = "该英文摘录尚未生成专业中文阅读缓存。"
                    note = "生成翻译缓存后可在此处显示完整中文译文。"
                    status = "not_generated"
                    items.append(
                        {
                            "label": "Excerpt",
                            "status": status,
                            "text": text_zh,
                            "paragraphs": _paragraphize_reading_text(text_zh),
                            "note": note,
                        }
                    )
                break
    return items[:3]


def _cached_translation_text(
    translation_cache: dict[tuple[str, str, str], dict[str, Any]],
    *,
    material_id: str,
    field: str,
    text: str,
) -> str:
    cached = translation_cache.get((material_id, field, text_hash(text)))
    if cached and cached.get("translation_zh"):
        return str(cached.get("translation_zh") or "").strip()
    return ""


def _screening_review_points(card: dict, material: dict) -> list[str]:
    points = list(card.get("gap_tasks") or [])
    if not material.get("display_url"):
        points.append("缺少可点击原始来源链接，需要补齐来源。")
    if not material.get("download_files") and not material.get("extracted_text_path"):
        points.append("尚未取得原文/PDF或正文抽取，正式引用前需要补证。")
    if "待提取" in (card.get("metrics") or []):
        points.append("性能指标尚未结构化，需要人工抽取 AUC、灵敏度、特异性、LoD、cut-off 等。")
    if card.get("priority_class") == "A":
        points.append("核心证据，建议研发/医学/注册共同复核后进入立项材料。")
    return points[:5] or ["自动归类结果需人工确认后再作为正式结论。"]


def build_screening_cards(
    materials: list[dict],
    evidence_cards: list[dict],
    *,
    task_dir: Path | None = None,
    confirmations: dict | None = None,
) -> list[dict[str, Any]]:
    materials_by_id = {item.get("material_id"): item for item in materials}
    translation_cache = load_translation_cache(task_dir) if task_dir else {}
    translation_capability = translation_status(task_dir) if task_dir else {}
    screening_cards: list[dict[str, Any]] = []
    for index, source_card in enumerate(evidence_cards, start=1):
        material = materials_by_id.get(source_card.get("material_id"), {})
        row = dict(source_card)
        text = _screening_text(row, material)
        priority_class, priority_label = _screening_priority(row, material)
        row["card_id"] = row.get("evidence_card_id") or f"EC-{index:04d}"
        row["screening_id"] = f"SC-{index:04d}"
        row["evidence_anchor"] = evidence_card_anchor(row["card_id"])
        row["title"] = clean_display_title(row.get("title") or material.get("title") or "未命名证据")
        row["title_original"] = row["title"]
        row["title_zh"] = _cached_translation_text(
            translation_cache,
            material_id=str(material.get("material_id") or row.get("material_id") or ""),
            field="title",
            text=" ".join(row["title"].split()),
        )
        row["display_title"] = row["title_zh"] or row["title"]
        row["show_original_title"] = bool(row["title_zh"] and row["title_zh"] != row["title"])
        row["year"] = _screening_year(material)
        row["journal"] = material.get("journal") or material.get("source_scenario_zh") or material.get("source_scenario") or "-"
        row["authors"] = _first_raw(material.get("raw_fields") or {}, "authors", "author_string", "registrant", "applicant")
        row["source_url"] = material.get("display_url") or material.get("source_url", "")
        row["source_label"] = material.get("source_scenario_zh") or material.get("material_type_zh") or "-"
        row["priority_class"] = priority_class
        row["priority_label"] = priority_label
        row["project_relevant"] = material.get("project_relevant", True)
        row["project_relevance_reason"] = material.get("project_relevance_reason", "")
        row["stage"] = _screening_stage(material, row)
        row["sample"] = _pick_screening_terms(text, SCREENING_SAMPLE_TERMS, fallback="样本待复核")
        row["platform"] = _pick_screening_terms(text, SCREENING_PLATFORM_TERMS, fallback="平台待复核")
        row["reference"] = _pick_screening_terms(text, SCREENING_REFERENCE_TERMS, fallback="参照待复核")
        row["use"] = _pick_screening_terms(text, SCREENING_USE_TERMS, fallback="用途待复核")
        row["population"] = _pick_screening_terms(text, SCREENING_POPULATION_TERMS, fallback="人群待复核")
        row["markers"] = _pick_screening_terms(
            text,
            _screening_marker_terms(confirmations),
            fallback="目标物待复核",
        )
        row["metrics"] = _screening_metric_labels(row, text)
        row["region"] = _screening_region(material, text)
        row["evidence_types"] = _screening_evidence_types(material)
        row["labels"] = sorted(
            {
                *[str(item) for item in row.get("taxonomy_tags") or [] if str(item).strip()],
                material.get("material_type_zh", ""),
                material.get("source_scenario_zh", ""),
            }
            - {""}
        )[:8]
        row["summary_lines"] = _screening_summary_lines(row, material)
        row["excerpt_lines"] = _screening_excerpt_lines(row, material)
        row["excerpt_blocks"] = [
            block
            for line in row["excerpt_lines"]
            for block in _excerpt_reading_blocks(line)
        ][:10]
        row["translation_items"] = _screening_translation_items(
            row,
            material,
            translation_cache=translation_cache,
            translation_capability=translation_capability,
        )
        row["review_points"] = _screening_review_points(row, material)
        row["data_text"] = " ".join(
            str(part or "")
            for part in [
                row["title"],
                row.get("title_zh", ""),
                row.get("summary", ""),
                text,
                " ".join(row["labels"]),
                " ".join(row["summary_lines"]),
            ]
        )[:4000]
        for key in [
            "stage",
            "sample",
            "platform",
            "reference",
            "use",
            "population",
            "markers",
            "metrics",
            "region",
            "evidence_types",
        ]:
            row[f"data_{key}"] = ";".join(row[key])
        row["data_priority"] = row["priority_label"]
        screening_cards.append(row)

    priority_order = {"A": 0, "B": 1, "C": 2}
    screening_cards.sort(
        key=lambda item: (
            priority_order.get(item.get("priority_class", "C"), 9),
            item.get("year") == "-",
            str(item.get("year") or ""),
            item.get("title", ""),
        )
    )
    return screening_cards


def build_filter_groups(
    screening_cards: list[dict[str, Any]],
    *,
    include_priority: bool = True,
) -> list[dict[str, Any]]:
    definitions = [
        ("priority", "证据优先级", "priority_label"),
        ("stage", "研发阶段", "stage"),
        ("sample", "样本类型", "sample"),
        ("platform", "检测平台/方法", "platform"),
        ("reference", "参照标准", "reference"),
        ("use", "应用场景", "use"),
        ("population", "目标人群", "population"),
        ("metrics", "性能参数", "metrics"),
        ("markers", "组合标志物", "markers"),
        ("region", "地域/人群", "region"),
        ("evidence", "证据类型", "evidence_types"),
    ]
    if not include_priority:
        definitions = [item for item in definitions if item[0] != "priority"]
    groups: list[dict[str, Any]] = []
    for key, label, field in definitions:
        counts: dict[str, int] = {}
        for card in screening_cards:
            raw_values = card.get(field, [])
            values = raw_values if isinstance(raw_values, list) else [raw_values]
            for value in values:
                value = str(value or "").strip()
                if value:
                    counts[value] = counts.get(value, 0) + 1
        if key == "priority":
            order = {"A 核心必读": 0, "B 重点复核": 1, "C 背景补充": 2}
            options = sorted(counts.items(), key=lambda item: order.get(item[0], 9))
        else:
            options = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:18]
        groups.append(
            {
                "key": key,
                "label": label,
                "options": [{"value": value, "count": count} for value, count in options],
            }
        )
    return groups


def build_screening_summary(screening_cards: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"A": 0, "B": 0, "C": 0}
    for card in screening_cards:
        key = card.get("priority_class", "C")
        counts[key] = counts.get(key, 0) + 1
    return {
        "total": len(screening_cards),
        "core": counts.get("A", 0),
        "review": counts.get("B", 0),
        "background": counts.get("C", 0),
    }


def build_report(task_dir: Path, report_type: str) -> dict:
    task = read_json(task_dir / "task.json")
    css = (asset_root() / "styles" / "report.css").read_text(encoding="utf-8")
    reviewed_path = task_dir / "data" / "reviewed_evidence_cards.jsonl"
    reviewed_cards = list(read_jsonl(reviewed_path))
    review_source = "reviewed" if reviewed_cards else "draft"
    evidence_cards = normalize_evidence_cards(
        reviewed_cards or list(read_jsonl(task_dir / "data" / "evidence_cards.jsonl"))
    )
    report_sections = normalize_report_sections(
        list(read_jsonl(task_dir / "data" / "report_sections.jsonl"))
    )
    materials = normalize_materials(
        list(read_jsonl(task_dir / "data" / "materials.jsonl")),
        evidence_cards,
        task_dir=task_dir,
        confirmations=task.get("confirmations") or {},
    )
    scenario_statuses = normalize_scenario_statuses(
        list((task.get("scenario_statuses") or {}).values())
    )
    required_scenarios = formal_scenarios_for(task)
    visible_scenario_statuses = _visible_scenario_statuses(
        scenario_statuses,
        required_scenarios,
    )
    collection_alerts = build_collection_alerts(
        materials=materials,
        evidence_cards=evidence_cards,
        scenario_statuses=visible_scenario_statuses,
        required_scenario_ids=required_scenarios,
    )
    generated_at = now_iso()

    if report_type == "materials":
        template_path = asset_root() / "templates" / "materials-report.html"
        output = task_dir / "reports" / "materials-index_v001.html"
        latest = task_dir / "reports" / "latest-materials-index.html"
        html = Template(template_path.read_text(encoding="utf-8")).render(
            task_id=task["task_id"],
            topic=task["topic"],
            materials=materials,
            evidence_cards=evidence_cards,
            review_source=review_source,
            scenario_statuses=visible_scenario_statuses,
            collection_alerts=collection_alerts,
            failed_count=sum(1 for item in materials if item.get("failure_type")),
            generated_at=generated_at,
            css=css,
        )
    elif report_type == "feasibility":
        template_path = asset_root() / "templates" / "feasibility-report.html"
        output = task_dir / "reports" / "feasibility-report_v001.html"
        latest = task_dir / "reports" / "latest-feasibility-report.html"
        html = Template(template_path.read_text(encoding="utf-8")).render(
            task_id=task["task_id"],
            topic=task["topic"],
            materials=materials,
            evidence_cards=evidence_cards,
            review_source=review_source,
            scenario_statuses=visible_scenario_statuses,
            collection_alerts=collection_alerts,
            included_count=sum(1 for card in evidence_cards if card.get("include_in_report")),
            analysis_source="ai_report_sections" if report_sections else "rule_draft",
            report_sections_count=len(report_sections),
            report_sections_by_title=report_sections_by_title(report_sections),
            cards_by_section=cards_by_section(
                evidence_cards,
                include_only=review_source == "reviewed",
            ),
            analysis=build_feasibility_analysis(materials, evidence_cards, visible_scenario_statuses),
            sections=REPORT_SECTIONS,
            generated_at=generated_at,
            css=css,
        )
    else:
        raise ValueError(f"Unsupported report type: {report_type}")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    latest.write_text(html, encoding="utf-8")
    append_jsonl(
        task_dir / "data" / "report_versions.jsonl",
        {"time": generated_at, "type": report_type, "path": str(output.relative_to(task_dir))},
    )
    return {"report_path": str(output), "latest_path": str(latest)}


def _scenario_level(status: str, count: int) -> str:
    if status == "completed_with_warnings":
        return "warn"
    if count > 0 or status == "completed":
        return "success"
    if status in {"collection_failed", "needs_login", "permission_required"}:
        return "danger"
    return "warn"


def _scenario_status_text(status: str, count: int) -> str:
    if status == "completed_with_warnings":
        return "completed_with_warnings / 部分完成，需复核"
    if count > 0:
        return "completed / 已采集"
    if status == "completed":
        return "completed / 已完成"
    if status == "collection_failed":
        return "collection_failed / 采集失败"
    if status in {"needs_login", "permission_required"}:
        return f"{status} / 受限"
    if status == "no_results":
        return "no_results / 未发现结果"
    if status == "not_started":
        return "not_started / 未启动"
    return STATUS_LABELS.get(status, status or "unknown / 待确认")


def build_standard_report(task_dir: Path, output: Path | None = None) -> dict:
    """Render the business-facing tabbed delivery report."""
    task = read_json(task_dir / "task.json")
    css = (asset_root() / "styles" / "report.css").read_text(encoding="utf-8")
    reviewed_path = task_dir / "data" / "reviewed_evidence_cards.jsonl"
    reviewed_cards = list(read_jsonl(reviewed_path))
    review_source = "reviewed" if reviewed_cards else "draft"
    evidence_cards = normalize_evidence_cards(
        reviewed_cards or list(read_jsonl(task_dir / "data" / "evidence_cards.jsonl"))
    )
    report_sections = normalize_report_sections(
        list(read_jsonl(task_dir / "data" / "report_sections.jsonl"))
    )
    materials = normalize_materials(
        list(read_jsonl(task_dir / "data" / "materials.jsonl")),
        evidence_cards,
        task_dir=task_dir,
        confirmations=task.get("confirmations") or {},
    )
    materials_by_id = {item.get("material_id"): item for item in materials}
    analysis_materials = [item for item in materials if item.get("project_relevant", True)]
    excluded_materials = build_excluded_material_rows(materials)
    analysis_material_ids = {item.get("material_id") for item in analysis_materials}
    analysis_evidence_cards = [
        card for card in evidence_cards if card.get("material_id") in analysis_material_ids
    ]
    scenario_statuses = normalize_scenario_statuses(
        list((task.get("scenario_statuses") or {}).values())
    )
    required_scenarios = formal_scenarios_for(task)
    visible_scenario_statuses = _visible_scenario_statuses(
        scenario_statuses,
        required_scenarios,
    )
    scenario_map = {item.get("scenario_id"): item for item in scenario_statuses}
    collection_alerts = build_collection_alerts(
        materials=materials,
        evidence_cards=evidence_cards,
        scenario_statuses=scenario_statuses,
        required_scenario_ids=required_scenarios,
    )
    collection_gap_rows = build_business_collection_gaps(
        collection_alerts,
        scenario_statuses,
        materials=materials,
        required_scenario_ids=required_scenarios,
    )
    collection_gap_summary = build_collection_gap_summary(
        collection_gap_rows,
        materials=materials,
        evidence_cards=evidence_cards,
    )
    source_quality = build_source_quality_audit(
        task_dir,
        task=task,
        materials=materials,
        scenario_statuses=scenario_statuses,
        required_scenario_ids=required_scenarios,
    )
    research_integrity = build_research_integrity_audit(task_dir, task=task)
    analysis = build_feasibility_analysis(
        analysis_materials,
        analysis_evidence_cards,
        visible_scenario_statuses,
    )
    metric_facts = list(read_jsonl(task_dir / "knowledge" / "metric_facts.jsonl"))
    metric_facts = [
        fact for fact in metric_facts if fact.get("material_id") in analysis_material_ids
    ]
    source_runs = list(read_jsonl(task_dir / "data" / "source_runs.jsonl"))
    knowledge_status = {
        "metric_fact_count": len(metric_facts),
        "source_run_count": len(source_runs),
        "literature_graph_exists": (task_dir / "knowledge" / "literature_graph.json").exists(),
        "topic_index_exists": (task_dir / "knowledge" / "topic_index.json").exists(),
    }
    translation_capability = translation_status(task_dir)

    literature_materials = _by_type(analysis_materials, "literature")
    pubmed_materials = [
        item for item in literature_materials if item.get("source_scenario") == "pubmed_literature"
    ]
    pmc_materials = [
        item for item in literature_materials if item.get("source_scenario") == "pmc_fulltext"
    ]
    openalex_materials = [
        item for item in literature_materials if item.get("source_scenario") == "openalex_literature"
    ]
    life_science_materials = [
        item for item in literature_materials if item.get("source_scenario") == "life_science_research"
    ]
    other_literature_materials = [
        item
        for item in literature_materials
        if item.get("source_scenario") not in {
            "pubmed_literature",
            "pmc_fulltext",
            "openalex_literature",
            "life_science_research",
            "yiigle_zhsjkzz",
            "wiley_alz",
        }
    ]
    regulatory_materials = _by_type(analysis_materials, "regulatory")
    competitor_materials = _by_type(analysis_materials, "competitor")
    standard_materials = _by_type(analysis_materials, "standard")
    patent_materials = _by_type(analysis_materials, "patent")
    neurology_literature_materials = [
        item for item in literature_materials if item.get("source_scenario") == "yiigle_zhsjkzz"
    ]
    wiley_alz_materials = [
        item for item in literature_materials if item.get("source_scenario") == "wiley_alz"
    ]

    evidence_map = [
        {
            "name": "PubMed 文献",
            "count": len(pubmed_materials),
            "status": _scenario_status_text(
                scenario_map.get("pubmed_literature", {}).get("status", ""),
                len(pubmed_materials),
            ),
            "level": _scenario_level(
                scenario_map.get("pubmed_literature", {}).get("status", ""),
                len(pubmed_materials),
            ),
            "use": "支撑国际文献证据、临床价值、目标人群和检测路径判断。",
        },
        {
            "name": "PMC 全文",
            "count": len(pmc_materials),
            "status": _scenario_status_text(
                scenario_map.get("pmc_fulltext", {}).get("status", ""),
                len(pmc_materials),
            ),
            "level": _scenario_level(
                scenario_map.get("pmc_fulltext", {}).get("status", ""),
                len(pmc_materials),
            ),
            "use": "支撑可追溯全文、PDF 下载、关键表格和原文结论复核。",
        },
        {
            "name": "OpenAlex 文献",
            "count": len(openalex_materials),
            "status": _scenario_status_text(
                scenario_map.get("openalex_literature", {}).get("status", ""),
                len(openalex_materials),
            ),
            "level": _scenario_level(
                scenario_map.get("openalex_literature", {}).get("status", ""),
                len(openalex_materials),
            ),
            "use": "支撑跨库文献发现、DOI/PMID 补齐、开放获取全文和 PDF 链接识别。",
        },
        {
            "name": "外部科学数据库",
            "count": len(life_science_materials),
            "status": _scenario_status_text(
                scenario_map.get("life_science_research", {}).get("status", ""),
                len(life_science_materials),
            ),
            "level": _scenario_level(
                scenario_map.get("life_science_research", {}).get("status", ""),
                len(life_science_materials),
            ),
            "use": "补充靶标、蛋白、通路、临床试验、遗传关联和公共数据库线索。",
        },
        {
            "name": "中文文献",
            "count": len(other_literature_materials),
            "status": _scenario_status_text("", len(other_literature_materials)),
            "level": _scenario_level("", len(other_literature_materials)),
            "use": "补充国内临床应用、实验室管理、指南解读和转化场景。",
        },
        *(
            [
                {
                    "name": "神经方向中文文献",
                    "count": len(neurology_literature_materials),
                    "status": _scenario_status_text(
                        scenario_map.get("yiigle_zhsjkzz", {}).get("status", ""),
                        len(neurology_literature_materials),
                    ),
                    "level": _scenario_level(
                        scenario_map.get("yiigle_zhsjkzz", {}).get("status", ""),
                        len(neurology_literature_materials),
                    ),
                    "use": "用于神经/认知方向的中文临床语境、指南解读和专科路径补充。",
                }
            ]
            if "yiigle_zhsjkzz" in required_scenarios
            else []
        ),
        *(
            [
                {
                    "name": "Wiley Alzheimer 文献",
                    "count": len(wiley_alz_materials),
                    "status": _scenario_status_text(
                        scenario_map.get("wiley_alz", {}).get("status", ""),
                        len(wiley_alz_materials),
                    ),
                    "level": _scenario_level(
                        scenario_map.get("wiley_alz", {}).get("status", ""),
                        len(wiley_alz_materials),
                    ),
                    "use": "用于 AD/认知障碍项目的疾病专科文献补充，不作为其他 IVD 项目的默认信源。",
                }
            ]
            if "wiley_alz" in required_scenarios
            else []
        ),
        {
            "name": "法规 / 指导原则",
            "count": len(regulatory_materials),
            "status": _scenario_status_text(
                scenario_map.get("cmde_regulatory", {}).get("status", ""),
                len(regulatory_materials),
            ),
            "level": _scenario_level(
                scenario_map.get("cmde_regulatory", {}).get("status", ""),
                len(regulatory_materials),
            ),
            "use": "确认注册路径、性能评价、临床评价和申报资料要求。",
        },
        {
            "name": "NMPA 竞品",
            "count": len(competitor_materials),
            "status": _scenario_status_text(
                scenario_map.get("nmpa_competitor", {}).get("status", ""),
                len(competitor_materials),
            ),
            "level": _scenario_level(
                scenario_map.get("nmpa_competitor", {}).get("status", ""),
                len(competitor_materials),
            ),
            "use": "确认同类产品、方法学、样本类型、预期用途和注册参照。",
        },
        {
            "name": "现行标准",
            "count": len(standard_materials),
            "status": _scenario_status_text(
                scenario_map.get("standards_current", {}).get("status", ""),
                len(standard_materials),
            ),
            "level": _scenario_level(
                scenario_map.get("standards_current", {}).get("status", ""),
                len(standard_materials),
            ),
            "use": "确认性能验证、安全要求、样本处理和质量体系约束。",
        },
        {
            "name": "专利",
            "count": len(patent_materials),
            "status": _scenario_status_text(
                scenario_map.get("patenthub_patents", {}).get("status", ""),
                len(patent_materials),
            ),
            "level": _scenario_level(
                scenario_map.get("patenthub_patents", {}).get("status", ""),
                len(patent_materials),
            ),
            "use": "识别知识产权风险、可绕开空间和研发自由实施风险。",
        },
    ]

    failed_scenarios = [
        item
        for item in visible_scenario_statuses
        if item.get("status") in {"collection_failed", "needs_login", "permission_required"}
    ]
    business_decision = build_business_decision(
        materials=analysis_materials,
        literature_materials=literature_materials,
        regulatory_materials=regulatory_materials,
        competitor_materials=competitor_materials,
        standard_materials=standard_materials,
        patent_materials=patent_materials,
        scenario_map=scenario_map,
        confirmations=task.get("confirmations") or {},
    )
    action_rows = build_business_action_rows(
        regulatory_materials=regulatory_materials,
        competitor_materials=competitor_materials,
        standard_materials=standard_materials,
        patent_materials=patent_materials,
        literature_materials=literature_materials,
        scenario_map=scenario_map,
        confirmations=task.get("confirmations") or {},
    )
    gap_rows = build_evidence_gap_rows(
        literature_materials=literature_materials,
        regulatory_materials=regulatory_materials,
        competitor_materials=competitor_materials,
        standard_materials=standard_materials,
        patent_materials=patent_materials,
        scenario_map=scenario_map,
        confirmations=task.get("confirmations") or {},
    )
    registration_materials = (
        regulatory_materials + competitor_materials + standard_materials + patent_materials
    )
    screening_cards = build_screening_cards(
        materials,
        evidence_cards,
        task_dir=task_dir,
        confirmations=task.get("confirmations") or {},
    )
    analysis_screening_cards = [
        card for card in screening_cards if card.get("project_relevant", True)
    ]
    translation_capability = translation_status(task_dir)
    core_cards = [
        card for card in analysis_screening_cards if card.get("priority_class") == "A"
    ][:60]
    if not core_cards:
        core_cards = analysis_screening_cards[:30]
    filter_groups = build_filter_groups(analysis_screening_cards)
    core_filter_groups = build_filter_groups(core_cards, include_priority=False)
    screening_summary = build_screening_summary(analysis_screening_cards)
    project_analysis_sections = build_project_analysis_sections(
        literature_materials=literature_materials,
        regulatory_materials=regulatory_materials,
        competitor_materials=competitor_materials,
        standard_materials=standard_materials,
        patent_materials=patent_materials,
        materials=analysis_materials,
        confirmations=task.get("confirmations") or {},
    )
    project_analysis_sections = attach_analysis_evidence_rows(
        project_analysis_sections,
        materials=analysis_materials,
        screening_cards=analysis_screening_cards,
    )
    project_analysis_sections = merge_validated_report_sections(
        project_analysis_sections,
        report_sections,
        materials=analysis_materials,
        screening_cards=analysis_screening_cards,
    )
    metric_fact_rows = build_metric_fact_rows(
        metric_facts,
        materials_by_id=materials_by_id,
        screening_cards=analysis_screening_cards,
    )
    expert_decision = build_expert_decision(
        business_decision,
        materials=analysis_materials,
        screening_summary=screening_summary,
        knowledge_status=knowledge_status,
        failed_scenarios=failed_scenarios,
        collection_gap_summary=collection_gap_summary,
        confirmations=task.get("confirmations") or {},
    )

    template_path = asset_root() / "templates" / "standard-delivery-report.html"
    report_output = output or task_dir / "reports" / "standard-delivery-report.html"
    html = Template(template_path.read_text(encoding="utf-8")).render(
        task_id=task["task_id"],
        topic=task["topic"],
        report_title=report_display_title(task["topic"]),
        materials=analysis_materials,
        registered_material_count=len(materials),
        included_material_count=len(analysis_materials),
        excluded_materials=excluded_materials,
        excluded_material_count=len(excluded_materials),
        materials_by_id={
            item.get("material_id"): item for item in analysis_materials
        },
        evidence_cards=analysis_evidence_cards,
        screening_cards=analysis_screening_cards,
        core_cards=core_cards,
        filter_groups=filter_groups,
        core_filter_groups=core_filter_groups,
        screening_summary=screening_summary,
        review_source=review_source,
        scenario_statuses=scenario_statuses,
        scenario_map=scenario_map,
        collection_alerts=collection_alerts,
        workflow_version=WORKFLOW_VERSION,
        collection_gap_rows=collection_gap_rows,
        collection_gap_summary=collection_gap_summary,
        source_quality=source_quality,
        research_integrity=research_integrity,
        analysis=analysis,
        business_decision=business_decision,
        evidence_map=evidence_map,
        knowledge_status=knowledge_status,
        translation_capability=translation_capability,
        metric_facts=metric_fact_rows,
        metric_fact_total=len(metric_fact_rows),
        expert_decision=expert_decision,
        source_runs=source_runs,
        failed_scenarios=failed_scenarios,
        search_profile=build_search_profile(task),
        project_analysis_sections=project_analysis_sections,
        literature_materials=_source_record_limit(literature_materials),
        pubmed_materials=pubmed_materials,
        pmc_materials=pmc_materials,
        openalex_materials=openalex_materials,
        other_literature_materials=other_literature_materials,
        regulatory_materials=regulatory_materials,
        competitor_materials=competitor_materials,
        standard_materials=standard_materials,
        patent_materials=patent_materials,
        registration_materials=_source_record_limit(registration_materials),
        gap_rows=gap_rows,
        action_rows=action_rows,
        generated_at=now_iso(),
        css=css,
    )

    report_output.parent.mkdir(parents=True, exist_ok=True)
    report_output.write_text(html, encoding="utf-8")
    append_jsonl(
        task_dir / "data" / "report_versions.jsonl",
        {
            "time": now_iso(),
            "type": "standard_delivery",
            "path": str(report_output.relative_to(task_dir)),
        },
    )
    return {"report_path": str(report_output)}
