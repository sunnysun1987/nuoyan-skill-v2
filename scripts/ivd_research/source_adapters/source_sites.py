from __future__ import annotations

from pathlib import Path

from ivd_research.jsonl import write_json
from ivd_research.models import SourceSite


SOURCE_SITES: list[SourceSite] = [
    SourceSite(
        source_site_id="cmde_regulatory",
        display_name="CMDE 器审中心",
        source_category="regulatory",
        base_url="https://www.cmde.org.cn/",
        search_url_template="https://www.cmde.org.cn/search/?keywords={query}",
        access_mode="http",
        query_fields=["keywords"],
        capture_fields=["title", "publish_date", "column", "attachments", "body", "download_status"],
        adapter_id="cmde_regulatory",
        restriction_notes="公开页面可能返回安全脚本或空正文，需记录 permission_required 并进入浏览器 workflow 或人工导入。",
    ),
    SourceSite(
        source_site_id="nmpa_competitor",
        display_name="NMPA 医疗器械注册查询",
        source_category="competitor",
        base_url="https://www.nmpa.gov.cn/datasearch/home-index.html#category=ylqx",
        access_mode="manual_assisted",
        query_fields=["product_name", "registration_type", "methodology"],
        capture_fields=["registration_certificate_number", "product_name", "registrant", "model", "scope", "approval_date", "valid_until"],
        adapter_id="nmpa_competitor",
        restriction_notes=(
            "标准流程由 agent 生成检索计划，用户在官方页面合法操作并保存截图或官方导出，"
            "再由专用清单导入；未执行、未上传或证据未核验不得判定为无注册结果。"
        ),
    ),
    SourceSite(
        source_site_id="standards_current",
        display_name="国家标准信息公共服务平台",
        source_category="standard",
        base_url="https://std.samr.gov.cn/",
        search_url_template="https://std.samr.gov.cn/search/stdPage?q={query}&tid=",
        access_mode="http",
        query_fields=["q", "status"],
        capture_fields=["standard_no", "standard_name", "status", "publish_date", "implementation_date", "technical_committee", "scope"],
        adapter_id="standards_current",
        restriction_notes="结果偏少时需按宽检索式、核心词、方法学或样本限定分层重试。",
    ),
    SourceSite(
        source_site_id="patenthub_patents",
        display_name="PatentHub 专利汇",
        source_category="patent",
        base_url="https://www.patenthub.cn/",
        search_url_template="https://www.patenthub.cn/s?ds=all&q={query}",
        access_mode="browser_workflow",
        auth_required=True,
        query_fields=["q", "ds"],
        capture_fields=["title", "publication_number", "application_number", "applicant", "assignee", "abstract", "claims", "legal_status", "pdf_status"],
        adapter_id="patenthub_patents",
        restriction_notes="PDF 或全文常受登录权限限制，只能记录可见信息并生成补证任务。",
    ),
    SourceSite(
        source_site_id="yiigle_zhjyyxzz",
        display_name="中华检验医学杂志",
        source_category="literature",
        base_url="https://zhjyyxzz.yiigle.com/",
        access_mode="http",
        query_fields=["keyword"],
        capture_fields=["title", "authors", "journal", "issue", "abstract", "doi", "detail_url", "fulltext_status"],
        adapter_id="yiigle_zhjyyxzz",
        restriction_notes="全文可能需要机构权限；摘要、DOI 和详情页需保留。",
    ),
    SourceSite(
        source_site_id="yiigle_zhsjkzz",
        display_name="中华神经科杂志",
        source_category="literature",
        base_url="https://zhsjkzz.yiigle.com/",
        access_mode="http",
        query_fields=["keyword"],
        capture_fields=["title", "authors", "journal", "abstract", "doi", "disease_keywords", "fulltext_status"],
        adapter_id="yiigle_zhsjkzz",
        restriction_notes="全文可能需要机构权限；未取得全文需进入补证任务。",
    ),
    SourceSite(
        source_site_id="cma_lab_management",
        display_name="中华临床实验室管理电子杂志",
        source_category="literature",
        base_url="https://zhlcsysgldzzz.cma-cmc.com.cn/",
        search_url_template="https://zhlcsysgldzzz.cma-cmc.com.cn/CN/searchresult",
        access_mode="http",
        query_fields=["keyword"],
        capture_fields=["title", "quality_control", "laboratory_process", "abstract", "doi", "body_clues"],
        adapter_id="cma_lab_management",
        restriction_notes="站内检索偏窄时必须使用短核心词重试。",
    ),
    SourceSite(
        source_site_id="yiigle_fulltext",
        display_name="中华医学期刊全文数据库",
        source_category="literature",
        base_url="https://www.yiigle.com/",
        search_url_template="https://www.yiigle.com/apiVue/search/searchList",
        access_mode="public_api",
        query_fields=["篇关摘", "文献类型", "出版日期"],
        capture_fields=["title", "abstract", "doi", "journal", "authors", "literature_type", "publication_date", "fulltext_status"],
        adapter_id="yiigle_fulltext",
        restriction_notes="公开 API 保存题录和摘要；PDF/全文若需机构权限必须显式记录。",
    ),
    SourceSite(
        source_site_id="wiley_alz",
        display_name="Wiley Alzheimer 期刊",
        source_category="literature",
        base_url="https://alz-journals.onlinelibrary.wiley.com/",
        search_url_template="https://alz-journals.onlinelibrary.wiley.com/action/doSearch?AllField={query}",
        access_mode="http",
        query_fields=["AllField"],
        capture_fields=["title", "authors", "doi", "abstract", "journal", "open_status", "access_restriction"],
        adapter_id="wiley_alz",
        restriction_notes="Cloudflare、机构权限或访问限制需记录真实状态。",
    ),
    SourceSite(
        source_site_id="pubmed_literature",
        display_name="PubMed / NCBI E-utilities",
        source_category="literature",
        base_url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils",
        detail_url_pattern="https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        access_mode="api",
        query_fields=["term", "retmax", "date_range"],
        capture_fields=["pmid", "title", "authors", "journal", "publication_date", "abstract_sections", "keywords", "doi", "pmcid", "similar_articles"],
        adapter_id="pubmed_literature",
        restriction_notes="必须限制 retmax；Similar articles 和 PDF 下载需设置阶段上限。",
    ),
    SourceSite(
        source_site_id="pmc_fulltext",
        display_name="PMC 开放全文",
        source_category="literature",
        base_url="https://pmc.ncbi.nlm.nih.gov/",
        detail_url_pattern="https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/",
        access_mode="api",
        query_fields=["term", "retmax", "date_range"],
        capture_fields=["pmcid", "fulltext_xml", "sections", "table_clues", "pdf_url", "pdf_status", "extracted_text"],
        adapter_id="pmc_fulltext",
        restriction_notes="PDF 不可用时保留 XML/HTML 和抽取文本。",
    ),
    SourceSite(
        source_site_id="openalex_literature",
        display_name="OpenAlex Works API",
        source_category="literature",
        base_url="https://api.openalex.org/works",
        access_mode="api",
        query_fields=["search", "filter"],
        capture_fields=["openalex_id", "doi", "pmid", "pmcid", "cited_by_count", "open_access", "concepts", "institutions"],
        adapter_id="openalex_literature",
        restriction_notes="作为 PubMed 补充来源，不替代 PMID 主键。",
    ),
    SourceSite(
        source_site_id="life_science_research",
        display_name="Codex life-science-research 插件",
        source_category="life_science_db",
        base_url="codex-plugin://life-science-research",
        access_mode="plugin",
        query_fields=["entity", "disease", "biomarker", "evidence_lane"],
        capture_fields=["source_database", "query", "entity", "result_summary", "source_url", "evidence_lane", "plugin_name", "collection_time"],
        adapter_id="life_science_research_bridge",
        restriction_notes="插件结果必须回写 Material / EvidenceCard / SourceRun，不能停留在聊天摘要中。",
    ),
    SourceSite(
        source_site_id="clinicaltrials_plugin",
        display_name="ClinicalTrials.gov 插件通道",
        source_category="life_science_db",
        base_url="codex-plugin://life-science-research/clinicaltrials",
        access_mode="plugin",
        query_fields=["condition", "intervention", "biomarker"],
        capture_fields=["nct_id", "status", "condition", "intervention", "outcomes", "eligibility", "locations", "updated_at"],
        adapter_id="life_science_research_bridge",
        restriction_notes="临床试验证据不得直接外推为 IVD 诊断性能证据。",
    ),
    SourceSite(
        source_site_id="local_import",
        display_name="本地 PDF / Excel / Obsidian / 企业共享目录",
        source_category="manual_import",
        base_url="file://local",
        access_mode="manual_import",
        query_fields=["path", "title", "manual_notes"],
        capture_fields=["file_path", "sha256", "title", "source", "excerpt", "review_status"],
        adapter_id="local_import",
        restriction_notes="用户合法提供的材料通过 import-local 或 CSV/Excel 导入，缺 DOI/PMID 时人工确认。",
    ),
    SourceSite(
        source_site_id="zotero_optional",
        display_name="Zotero 导出文件",
        source_category="manual_import",
        base_url="file://zotero-export",
        access_mode="manual_import",
        query_fields=["ris", "bibtex", "csv", "pdf_directory"],
        capture_fields=["doi", "pmid", "pdf_path", "tags", "notes", "citation"],
        adapter_id="reference_import",
        restriction_notes="企业默认不强依赖；只作为高级用户本地文献库导入选项。",
    ),
]


def all_source_sites() -> list[SourceSite]:
    return list(SOURCE_SITES)


def source_site_map() -> dict[str, SourceSite]:
    return {site.source_site_id: site for site in SOURCE_SITES}


def get_source_site(source_site_id: str) -> SourceSite | None:
    return source_site_map().get(source_site_id)


def export_source_sites(path: Path) -> None:
    payload = {"source_sites": [site.model_dump(mode="json") for site in SOURCE_SITES]}
    write_json(path, payload)
