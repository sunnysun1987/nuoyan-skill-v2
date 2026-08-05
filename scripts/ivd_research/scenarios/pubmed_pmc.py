import hashlib
import re
import subprocess
import time
import random
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from xml.etree import ElementTree

import httpx

from .registry import get_scenario
from ivd_research.models import DownloadFile, FailureType, Material
from ivd_research.scenarios.base import ScenarioResult, now_iso


NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
PUBMED_BASE = "https://pubmed.ncbi.nlm.nih.gov"
PMC_BASE = "https://pmc.ncbi.nlm.nih.gov"
USER_AGENT = "NuoYan-Skill/2.0 (IVD feasibility evidence collection; contact local user)"
REQUEST_DELAY_SECONDS = 0.35
DEFAULT_RETMAX = 20
MAX_RETMAX = 10000
DEFAULT_SIMILAR_RETMAX = 5
EFETCH_BATCH_SIZE = 100
NCBI_RETRY_ATTEMPTS = 4
DEFAULT_SIMILAR_ARTICLE_SOURCE_LIMIT = 50
DEFAULT_PDF_DOWNLOAD_LIMIT = 100


def pubmed_adapter():
    return get_scenario("pubmed_literature")


def pmc_adapter():
    return get_scenario("pmc_fulltext")


def collect_pubmed(task_id, task_dir, params):
    query = str(params.get("query", "")).strip()
    material_id = str(params.get("material_id", "MAT-000000"))
    task_dir = Path(task_dir)
    retmax_value = params.get("retmax")
    similar_retmax = _safe_int(params.get("similar_retmax"), DEFAULT_SIMILAR_RETMAX)
    similar_source_limit = _safe_int(
        params.get("similar_article_source_limit"),
        DEFAULT_SIMILAR_ARTICLE_SOURCE_LIMIT,
    )
    pdf_download_limit = _safe_int(params.get("pdf_download_limit"), DEFAULT_PDF_DOWNLOAD_LIMIT)
    query = _with_pubmed_date_filter(query, params.get("date_range"))
    if not query:
        return ScenarioResult(
            status="needs_manual_review",
            failure_type=FailureType.NEEDS_MANUAL_REVIEW,
            message_zh="PubMed 检索缺少关键词，请先确认英文关键词或项目关键词池。",
        )

    client = NCBIClient()
    try:
        search = client.esearch("pubmed", query, retmax=retmax_value)
        pmids = search.get("ids", [])
        search_xml_path = _save_raw_text(
            task_dir,
            "pubmed",
            f"{material_id}_pubmed_esearch.xml",
            search.get("xml", ""),
        )
    except NCBIHTTPError as exc:
        return _ncbi_error_result("PubMed 检索", exc)

    if not pmids:
        return ScenarioResult(
            status="no_results",
            failure_type=FailureType.NO_RESULTS,
            message_zh=f"PubMed 未查询到与“{query}”匹配的公开结果。",
        )

    try:
        articles, fetch_xml_paths = _fetch_pubmed_article_batches(
            client,
            task_dir,
            db="pubmed",
            ids=pmids,
            material_id=material_id,
            raw_subdir="pubmed",
            filename_stem="pubmed_efetch",
            parser=parse_pubmed_articles,
        )
    except NCBIHTTPError as exc:
        return _ncbi_error_result("PubMed 详情获取", exc)

    if not articles:
        return ScenarioResult(
            status="parse_failed",
            failure_type=FailureType.PARSE_FAILED,
            message_zh="PubMed 已返回结果，但未能解析出文献详情。",
        )

    materials: list[Material] = []
    seen_pmids: set[str] = set()
    for index, article in enumerate(articles, start=1):
        current_material_id = material_id if len(articles) == 1 else f"{material_id}-{index:03d}"
        seen_pmids.add(str(article.get("pmid") or ""))
        if index <= similar_source_limit:
            related_articles, related_raw_path = _related_pubmed_articles(
                client,
                task_dir,
                pmid=str(article.get("pmid") or ""),
                material_id=current_material_id,
                retmax=similar_retmax,
                seen_pmids=seen_pmids,
            )
            similar_policy = "collected"
        else:
            related_articles, related_raw_path = [], ""
            similar_policy = f"skipped_after_top_{similar_source_limit}"
        article["similar_articles"] = related_articles
        article["similar_articles_raw_path"] = related_raw_path
        if index <= pdf_download_limit:
            pdf_status, download_files, pdf_error = download_pmc_pdf(
                task_dir,
                pmcid=str(article.get("pmcid") or ""),
                material_id=current_material_id,
                title=str(article.get("title") or ""),
            )
        else:
            pdf_status, download_files, pdf_error = (
                "deferred",
                [],
                f"批量全量召回时仅对前 {pdf_download_limit} 条尝试 PDF 下载；本条保留题录/摘要和全文线索，后续可按需补下载。",
            )
        text_path = _save_literature_text(
            task_dir,
            material_filename(current_material_id, article.get("title", ""), "pubmed", ".txt"),
            format_pubmed_text(article),
        )
        material = Material(
            material_id=current_material_id,
            task_id=task_id,
            source_scenario="pubmed_literature",
            material_type="literature",
            title=article.get("title") or f"PubMed 文献 {article.get('pmid', '')}".strip(),
            source_url=article.get("pubmed_url", ""),
            search_keyword_or_query=query,
            collection_path={
                "scenario_id": "pubmed_literature",
                "query": query,
                "pubmed_esearch_raw": search_xml_path,
                "pubmed_efetch_raw": fetch_xml_paths[index - 1] if index <= len(fetch_xml_paths) else "",
                "pubmed_efetch_raw_batches": fetch_xml_paths,
                "list_index": index,
                "retmax": search.get("retmax", retmax_value),
            },
            collection_time=now_iso(),
            adapter_id="pubmed_literature",
            adapter_version="2.0.0",
            raw_fields={
                **article,
                "source_database": "PubMed",
                "query": query,
                "search_count": search.get("count", ""),
                "search_retmax": search.get("retmax", retmax_value),
                "similar_article_count": len(related_articles),
                "similar_articles_raw_path": related_raw_path,
                "similar_articles_policy": similar_policy,
                "fulltext_status": "pmcid_available" if article.get("pmcid") else "metadata_only",
                "pdf_status": pdf_status,
                "pdf_error": pdf_error,
            },
            download_status=pdf_status,
            download_files=download_files,
            extracted_text_status="completed" if text_path else "not_attempted",
            extracted_text_path=text_path,
            content_snapshot_path=fetch_xml_paths[index - 1] if index <= len(fetch_xml_paths) else "",
            failure_type=FailureType.DOWNLOAD_FAILED if pdf_status == "download_failed" else None,
            failure_reason=pdf_error if pdf_status == "download_failed" else "",
            possible_duplicate_keys=_duplicate_keys(article),
        )
        materials.append(material)

    return ScenarioResult(
        status="completed",
        materials=materials,
        message_zh=f"PubMed 检索完成，解析文献 {len(materials)} 条。",
    )


def collect_pmc(task_id, task_dir, params):
    query = str(params.get("query", "")).strip()
    material_id = str(params.get("material_id", "MAT-000000"))
    task_dir = Path(task_dir)
    retmax_value = params.get("retmax")
    pdf_download_limit = _safe_int(params.get("pdf_download_limit"), DEFAULT_PDF_DOWNLOAD_LIMIT)
    query = _with_pubmed_date_filter(query, params.get("date_range"))
    if not query:
        return ScenarioResult(
            status="needs_manual_review",
            failure_type=FailureType.NEEDS_MANUAL_REVIEW,
            message_zh="PMC 全文获取缺少关键词，请先确认英文关键词或项目关键词池。",
        )

    client = NCBIClient()
    try:
        search = client.esearch("pmc", query, retmax=retmax_value)
        pmc_numeric_ids = search.get("ids", [])
        search_xml_path = _save_raw_text(
            task_dir,
            "pmc",
            f"{material_id}_pmc_esearch.xml",
            search.get("xml", ""),
        )
    except NCBIHTTPError as exc:
        return _ncbi_error_result("PMC 检索", exc)

    if not pmc_numeric_ids:
        return ScenarioResult(
            status="no_results",
            failure_type=FailureType.NO_RESULTS,
            message_zh=f"PMC 未查询到与“{query}”匹配的开放全文结果。",
        )

    try:
        articles, fetch_xml_paths = _fetch_pubmed_article_batches(
            client,
            task_dir,
            db="pmc",
            ids=pmc_numeric_ids,
            material_id=material_id,
            raw_subdir="pmc",
            filename_stem="pmc_efetch",
            parser=parse_pmc_articles,
        )
    except NCBIHTTPError as exc:
        return _ncbi_error_result("PMC 全文 XML 获取", exc)

    if not articles:
        return ScenarioResult(
            status="parse_failed",
            failure_type=FailureType.PARSE_FAILED,
            message_zh="PMC 已返回结果，但未能解析开放全文 XML。",
        )

    materials: list[Material] = []
    collection_errors: list[dict[str, Any]] = []
    for index, article in enumerate(articles, start=1):
        current_material_id = material_id if len(articles) == 1 else f"{material_id}-{index:03d}"
        pmcid = article.get("pmcid") or f"PMC{pmc_numeric_ids[index - 1]}"
        xml_article_path = _save_raw_text(
            task_dir,
            "pmc",
            material_filename(current_material_id, article.get("title", "") or pmcid, "PMC全文XML", ".xml"),
            article.get("article_xml", ""),
        )
        text_path = _save_literature_text(
            task_dir,
            material_filename(current_material_id, article.get("title", "") or pmcid, "PMC全文", ".txt"),
            format_pmc_text(article),
        )
        if index <= pdf_download_limit:
            pdf_status, download_files, pdf_error = download_pmc_pdf(
                task_dir,
                pmcid=pmcid,
                material_id=current_material_id,
                title=article.get("title", ""),
            )
        else:
            pdf_status, download_files, pdf_error = (
                "deferred",
                [],
                f"批量全量召回时仅对前 {pdf_download_limit} 条尝试 PDF 下载；本条已保存 PMC XML 和全文抽取文本，后续可按需补下载。",
            )
        if pdf_error:
            collection_errors.append(
                {
                    "pmcid": pmcid,
                    "status": pdf_status,
                    "reason": pdf_error,
                }
            )
        material = Material(
            material_id=current_material_id,
            task_id=task_id,
            source_scenario="pmc_fulltext",
            material_type="literature",
            title=article.get("title") or f"PMC 全文 {pmcid}",
            source_url=article.get("pmc_url", ""),
            search_keyword_or_query=query,
            collection_path={
                "scenario_id": "pmc_fulltext",
                "query": query,
                "pmc_esearch_raw": search_xml_path,
                "pmc_efetch_raw": fetch_xml_paths[index - 1] if index <= len(fetch_xml_paths) else "",
                "pmc_efetch_raw_batches": fetch_xml_paths,
                "pmc_article_xml": xml_article_path,
                "list_index": index,
                "retmax": search.get("retmax", retmax_value),
            },
            collection_time=now_iso(),
            adapter_id="pmc_fulltext",
            adapter_version="2.0.0",
            raw_fields={
                **{key: value for key, value in article.items() if key != "article_xml"},
                "source_database": "PMC",
                "query": query,
                "search_count": search.get("count", ""),
                "search_retmax": search.get("retmax", retmax_value),
                "xml_status": "completed" if xml_article_path else "parse_failed",
                "fulltext_status": "completed" if text_path else "parse_failed",
                "pdf_status": pdf_status,
                "pdf_url": pmc_pdf_url(pmcid),
            },
            download_status=pdf_status,
            download_files=download_files,
            extracted_text_status="completed" if text_path else "parse_failed",
            extracted_text_path=text_path,
            content_snapshot_path=xml_article_path,
            failure_type=FailureType.DOWNLOAD_FAILED if pdf_status == "download_failed" else None,
            failure_reason=pdf_error if pdf_status == "download_failed" else "",
            possible_duplicate_keys=_duplicate_keys(article),
        )
        materials.append(material)

    status = "completed" if materials else "collection_failed"
    return ScenarioResult(
        status=status,
        materials=materials,
        failure_type=None if materials else FailureType.COLLECTION_FAILED,
        message_zh=f"PMC 全文获取完成，解析开放全文 {len(materials)} 条。",
        collection_errors=collection_errors,
    )


class NCBIHTTPError(RuntimeError):
    def __init__(self, step: str, status_code: int | None = None, message: str = "", url: str = ""):
        self.step = step
        self.status_code = status_code
        self.url = url
        super().__init__(message or f"{step} failed")


class NCBIClient:
    def __init__(self, *, timeout: float = 30.0):
        self.timeout = timeout
        self._last_request = 0.0

    def esearch(self, db: str, term: str, *, retmax: Any) -> dict[str, Any]:
        requested_all = isinstance(retmax, str) and retmax.strip().lower() in {"all", "全部", "full", "unlimited"}
        if requested_all:
            count_xml = self._esearch_xml(db, term, retmax=0)
            count_root = ElementTree.fromstring(count_xml)
            count = _safe_int(count_root.findtext(".//Count"), DEFAULT_RETMAX)
            effective_retmax = min(count, MAX_RETMAX)
            xml_text = self._esearch_xml(db, term, retmax=effective_retmax)
        else:
            effective_retmax = _safe_int(retmax, DEFAULT_RETMAX)
            xml_text = self._esearch_xml(db, term, retmax=effective_retmax)
        root = ElementTree.fromstring(xml_text)
        ids = [node.text or "" for node in root.findall(".//IdList/Id") if node.text]
        count_node = root.find(".//Count")
        return {
            "ids": ids,
            "count": count_node.text if count_node is not None else "",
            "retmax": effective_retmax,
            "retmax_policy": "all_count_capped" if requested_all and effective_retmax >= MAX_RETMAX else ("all_count" if requested_all else "fixed"),
            "xml": xml_text,
        }

    def _esearch_xml(self, db: str, term: str, *, retmax: int) -> str:
        params = {
            "db": db,
            "term": term,
            "retmode": "xml",
            "retmax": str(retmax),
            "sort": "relevance",
        }
        return self._get("esearch.fcgi", params, step=f"{db} esearch")

    def efetch(self, db: str, ids: list[str], *, rettype: str, retmode: str) -> str:
        if not ids:
            return ""
        params = {
            "db": db,
            "id": ",".join(ids),
            "retmode": retmode,
        }
        if rettype:
            params["rettype"] = rettype
        return self._get("efetch.fcgi", params, step=f"{db} efetch")

    def elink(self, dbfrom: str, db: str, ids: list[str], *, linkname: str = "", retmax: int = 5) -> dict[str, Any]:
        if not ids:
            return {"ids": [], "xml": ""}
        params = {
            "dbfrom": dbfrom,
            "db": db,
            "id": ",".join(ids),
            "retmode": "xml",
            "retmax": str(retmax),
        }
        if linkname:
            params["linkname"] = linkname
        xml_text = self._get("elink.fcgi", params, step=f"{dbfrom} elink")
        root = ElementTree.fromstring(xml_text)
        linked_ids: list[str] = []
        for node in root.findall(".//LinkSetDb/Link/Id"):
            value = (node.text or "").strip()
            if value and value not in linked_ids:
                linked_ids.append(value)
        return {"ids": linked_ids[:retmax], "xml": xml_text}

    def _get(self, endpoint: str, params: dict[str, str], *, step: str) -> str:
        url = f"{NCBI_BASE}/{endpoint}?{urlencode(params)}"
        last_error: Exception | None = None
        for attempt in range(1, NCBI_RETRY_ATTEMPTS + 1):
            elapsed = time.monotonic() - self._last_request
            if elapsed < REQUEST_DELAY_SECONDS:
                time.sleep(REQUEST_DELAY_SECONDS - elapsed)
            try:
                with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                    response = client.get(url, headers={"User-Agent": USER_AGENT})
            except Exception as exc:
                last_error = exc
                try:
                    return self._get_with_curl(url)
                except Exception as curl_exc:
                    last_error = curl_exc
                    if attempt >= NCBI_RETRY_ATTEMPTS:
                        raise NCBIHTTPError(
                            step,
                            message=f"{exc}；curl fallback failed: {curl_exc}",
                            url=url,
                        ) from exc
                    time.sleep(min(2 ** attempt, 8) + random.uniform(0.0, 0.5))
                    continue
            finally:
                self._last_request = time.monotonic()
            if response.status_code == 429:
                last_error = NCBIHTTPError(step, status_code=429, message="NCBI 请求触发限流（HTTP 429）", url=url)
                if attempt >= NCBI_RETRY_ATTEMPTS:
                    raise last_error
                time.sleep(min(2 ** attempt, 8) + random.uniform(0.0, 0.5))
                continue
            if response.status_code >= 400:
                raise NCBIHTTPError(step, status_code=response.status_code, message=response.text[:500], url=url)
            return response.text
        if isinstance(last_error, NCBIHTTPError):
            raise last_error
        raise NCBIHTTPError(step, message="NCBI 请求失败", url=url)

    def _get_with_curl(self, url: str) -> str:
        completed = subprocess.run(
            [
                "curl",
                "-fsSL",
                "--max-time",
                str(int(self.timeout)),
                "-A",
                USER_AGENT,
                url,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=self.timeout + 5,
        )
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or "").strip() or f"curl exit {completed.returncode}")
        return completed.stdout


def parse_pubmed_articles(xml_text: str) -> list[dict[str, Any]]:
    root = ElementTree.fromstring(xml_text)
    articles: list[dict[str, Any]] = []
    for article_node in root.findall(".//PubmedArticle"):
        pmid = _node_text(article_node.find(".//MedlineCitation/PMID"))
        title = _joined_node_text(article_node.find(".//ArticleTitle"))
        abstract_sections = _abstract_sections(article_node)
        abstract = _format_abstract_sections(abstract_sections)
        journal = _node_text(article_node.find(".//Journal/Title"))
        journal_iso = _node_text(article_node.find(".//Journal/ISOAbbreviation"))
        pub_date = _pubmed_date(article_node)
        doi = ""
        pmcid = ""
        for id_node in article_node.findall("./PubmedData/ArticleIdList/ArticleId"):
            id_type = (id_node.attrib.get("IdType") or "").lower()
            value = _node_text(id_node)
            if id_type == "doi":
                doi = value
            elif id_type == "pmc":
                pmcid = normalize_pmcid(value)
        authors = _pubmed_authors(article_node)
        mesh_terms = [
            _joined_node_text(mesh.find(".//DescriptorName"))
            for mesh in article_node.findall(".//MeshHeading")
            if _joined_node_text(mesh.find(".//DescriptorName"))
        ]
        keywords = [
            _joined_node_text(keyword)
            for keyword in article_node.findall(".//KeywordList/Keyword")
            if _joined_node_text(keyword)
        ]
        articles.append(
            {
                "pmid": pmid,
                "pmcid": pmcid,
                "doi": doi,
                "title": title,
                "authors": authors,
                "journal": journal or journal_iso,
                "journal_iso": journal_iso,
                "publication_date": pub_date,
                "abstract": abstract,
                "abstract_sections": abstract_sections,
                "keywords": keywords,
                "mesh_terms": mesh_terms,
                "pubmed_url": f"{PUBMED_BASE}/{pmid}/" if pmid else "",
                "pmc_url": f"{PMC_BASE}/articles/{pmcid}/" if pmcid else "",
            }
        )
    return articles


def parse_pmc_articles(xml_text: str) -> list[dict[str, Any]]:
    root = ElementTree.fromstring(xml_text)
    article_nodes = [root] if _local_name(root.tag) == "article" else root.findall(".//article")
    articles: list[dict[str, Any]] = []
    for article_node in article_nodes:
        ids = _article_ids(article_node)
        pmcid = normalize_pmcid(ids.get("pmc", ""))
        if not pmcid:
            pmcid = normalize_pmcid(ids.get("pmcid", ""))
        pmid = ids.get("pmid", "")
        doi = ids.get("doi", "")
        title = _joined_node_text(_first(article_node, ".//front/article-meta/title-group/article-title"))
        abstract_sections = _pmc_abstract_sections(article_node)
        abstract = _format_abstract_sections(abstract_sections)
        body_text = _joined_node_text(_first(article_node, ".//body"))
        journal = _joined_node_text(_first(article_node, ".//front/journal-meta/journal-title-group/journal-title"))
        pub_date, issue_date = _pmc_dates(article_node)
        authors = _pmc_authors(article_node)
        article_xml = ElementTree.tostring(article_node, encoding="unicode")
        articles.append(
            {
                "pmid": pmid,
                "pmcid": pmcid,
                "doi": doi,
                "title": title,
                "authors": authors,
                "journal": journal,
                "publication_date": pub_date,
                "issue_date": issue_date,
                "date_source": "PMC epub/ppub 优先；collection 仅作为刊期",
                "abstract": abstract,
                "abstract_sections": abstract_sections,
                "keywords": _pmc_keywords(article_node),
                "full_visible_text": body_text[:20000],
                "full_text_length": len(body_text),
                "pmc_url": f"{PMC_BASE}/articles/{pmcid}/" if pmcid else "",
                "pubmed_url": f"{PUBMED_BASE}/{pmid}/" if pmid else "",
                "article_xml": article_xml,
            }
        )
    return articles


def download_pmc_pdf(
    task_dir: Path,
    *,
    pmcid: str,
    material_id: str,
    title: str = "",
) -> tuple[str, list[DownloadFile], str]:
    url = pmc_pdf_url(pmcid)
    if not url:
        return "not_attempted", [], "缺少 PMCID，无法构造 PMC PDF 链接。"
    try:
        with httpx.Client(timeout=40.0, follow_redirects=True) as client:
            response = client.get(url, headers={"User-Agent": USER_AGENT})
    except Exception as exc:
        return "download_failed", [], str(exc)
    content_type = response.headers.get("content-type", "")
    content = response.content
    if response.status_code == 404:
        return "not_available", [], "PMC 未提供可下载 PDF。"
    if response.status_code == 429:
        return "download_failed", [], "PMC PDF 下载触发限流（HTTP 429）。"
    if response.status_code >= 400:
        return "download_failed", [], f"PMC PDF 下载失败，HTTP {response.status_code}。"
    if not _looks_like_pdf(content, content_type):
        return "not_available", [], "PMC 返回内容不是 PDF，可能仅提供 HTML/XML 全文。"

    download_dir = task_dir / "downloads" / "literature" / "pmc_pdf"
    download_dir.mkdir(parents=True, exist_ok=True)
    filename = material_filename(material_id, title or pmcid, "PMC", ".pdf")
    target = download_dir / filename
    target.write_bytes(content)
    file_record = DownloadFile(
        original_filename=f"{title or pmcid}.pdf",
        stored_filename=filename,
        relative_path=str(target.relative_to(task_dir)),
        source_url=url,
        sha256=hashlib.sha256(content).hexdigest(),
        status="downloaded",
    )
    return "downloaded", [file_record], ""


def pmc_pdf_url(pmcid: str) -> str:
    normalized = normalize_pmcid(pmcid)
    return f"{PMC_BASE}/articles/{normalized}/pdf/" if normalized else ""


def normalize_pmcid(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    match = re.search(r"PMC\s*\d+", raw, flags=re.IGNORECASE)
    if match:
        return "PMC" + re.sub(r"\D", "", match.group(0))
    if raw.isdigit():
        return f"PMC{raw}"
    return raw


def format_pubmed_text(article: dict[str, Any]) -> str:
    similar_lines = []
    for item in article.get("similar_articles") or []:
        similar_lines.append(
            "- "
            + "；".join(
                part
                for part in [
                    item.get("title", ""),
                    f"PMID：{item.get('pmid', '')}" if item.get("pmid") else "",
                    f"PMCID：{item.get('pmcid', '')}" if item.get("pmcid") else "",
                    f"DOI：{item.get('doi', '')}" if item.get("doi") else "",
                    f"相关性：{item.get('relation_reason_zh', '')}" if item.get("relation_reason_zh") else "",
                ]
                if part
            )
        )
    return "\n".join(
        [
            f"题名：{article.get('title', '')}",
            f"PMID：{article.get('pmid', '')}",
            f"PMCID：{article.get('pmcid', '')}",
            f"DOI：{article.get('doi', '')}",
            f"期刊：{article.get('journal', '')}",
            f"发表日期：{article.get('publication_date', '')}",
            f"作者：{'；'.join(article.get('authors') or [])}",
            f"MeSH：{'；'.join(article.get('mesh_terms') or [])}",
            f"Keywords：{'；'.join(article.get('keywords') or [])}",
            "",
            "Abstract：",
            article.get("abstract", ""),
            "",
            "Similar articles：",
            "\n".join(similar_lines) if similar_lines else "未采集到高相关 Similar articles。",
            "",
            f"PubMed链接：{article.get('pubmed_url', '')}",
            f"PMC链接：{article.get('pmc_url', '')}",
        ]
    )


def format_pmc_text(article: dict[str, Any]) -> str:
    full_text = article.get("full_visible_text", "")
    return "\n".join(
        [
            f"题名：{article.get('title', '')}",
            f"PMID：{article.get('pmid', '')}",
            f"PMCID：{article.get('pmcid', '')}",
            f"DOI：{article.get('doi', '')}",
            f"期刊：{article.get('journal', '')}",
            f"发表日期：{article.get('publication_date', '')}",
            f"作者：{'；'.join(article.get('authors') or [])}",
            f"Keywords：{'；'.join(article.get('keywords') or [])}",
            "",
            "Abstract：",
            article.get("abstract", ""),
            "",
            "全文片段：",
            full_text,
            "",
            f"PMC链接：{article.get('pmc_url', '')}",
            f"PubMed链接：{article.get('pubmed_url', '')}",
        ]
    )


def _save_raw_text(task_dir: Path, source: str, filename: str, text: str) -> str:
    target_dir = task_dir / "downloads" / "literature" / source
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / filename
    path.write_text(text or "", encoding="utf-8", errors="ignore")
    return str(path.relative_to(task_dir))


def _save_literature_text(task_dir: Path, filename: str, text: str) -> str:
    target_dir = task_dir / "extracted_text" / "literature"
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / filename
    path.write_text(text or "", encoding="utf-8", errors="ignore")
    return str(path.relative_to(task_dir))


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _fetch_pubmed_article_batches(
    client: NCBIClient,
    task_dir: Path,
    *,
    db: str,
    ids: list[str],
    material_id: str,
    raw_subdir: str,
    filename_stem: str,
    parser,
) -> tuple[list[dict[str, Any]], list[str]]:
    articles: list[dict[str, Any]] = []
    fetch_xml_paths: list[str] = []
    for batch_index, batch_ids in enumerate(_chunks(ids, EFETCH_BATCH_SIZE), start=1):
        fetch_xml = client.efetch(db, batch_ids, rettype="", retmode="xml")
        suffix = f"batch{batch_index:03d}" if len(ids) > EFETCH_BATCH_SIZE else "batch001"
        fetch_xml_path = _save_raw_text(
            task_dir,
            raw_subdir,
            f"{material_id}_{filename_stem}_{suffix}.xml",
            fetch_xml,
        )
        batch_articles = parser(fetch_xml)
        fetch_xml_paths.extend([fetch_xml_path] * len(batch_articles))
        articles.extend(batch_articles)
    return articles, fetch_xml_paths


def _related_pubmed_articles(
    client: NCBIClient,
    task_dir: Path,
    *,
    pmid: str,
    material_id: str,
    retmax: int,
    seen_pmids: set[str],
) -> tuple[list[dict[str, Any]], str]:
    if not pmid or retmax <= 0:
        return [], ""
    try:
        link = client.elink(
            "pubmed",
            "pubmed",
            [pmid],
            linkname="pubmed_pubmed",
            retmax=retmax + 3,
        )
    except NCBIHTTPError:
        return [], ""
    raw_path = _save_raw_text(
        task_dir,
        "pubmed",
        f"{material_id}_pubmed_similar_elink.xml",
        link.get("xml", ""),
    )
    ids = [
        item
        for item in (link.get("ids") or [])
        if item and item != pmid and item not in seen_pmids
    ][:retmax]
    if not ids:
        return [], raw_path
    try:
        fetch_xml = client.efetch("pubmed", ids, rettype="", retmode="xml")
    except NCBIHTTPError:
        return [], raw_path
    fetch_path = _save_raw_text(
        task_dir,
        "pubmed",
        f"{material_id}_pubmed_similar_efetch.xml",
        fetch_xml,
    )
    related = parse_pubmed_articles(fetch_xml)
    for item in related:
        item["relation_type"] = "similar_article"
        item["relation_reason_zh"] = "PubMed Similar articles 推荐，需人工复核主题相关性。"
        item["similar_source_pmid"] = pmid
    return related[:retmax], fetch_path or raw_path


def _ncbi_error_result(subject_zh: str, exc: NCBIHTTPError) -> ScenarioResult:
    if exc.status_code == 429:
        return ScenarioResult(
            status="collection_failed",
            failure_type=FailureType.COLLECTION_FAILED,
            message_zh=f"{subject_zh} 触发 NCBI 限流（HTTP 429），建议降低批量并稍后重试。",
            collection_errors=[{"status_code": 429, "url": exc.url, "reason": str(exc)}],
        )
    if exc.status_code in {401, 403}:
        return ScenarioResult(
            status="permission_required",
            failure_type=FailureType.PERMISSION_REQUIRED,
            message_zh=f"{subject_zh} 返回权限限制，未生成材料。",
            collection_errors=[{"status_code": exc.status_code, "url": exc.url, "reason": str(exc)}],
        )
    return ScenarioResult(
        status="collection_failed",
        failure_type=FailureType.COLLECTION_FAILED,
        message_zh=f"{subject_zh} 失败：{exc}",
        collection_errors=[{"status_code": exc.status_code, "url": exc.url, "reason": str(exc)}],
    )


def _safe_int(value: Any, default: int) -> int:
    if isinstance(value, str) and value.strip().lower() in {"all", "全部", "full", "unlimited"}:
        return MAX_RETMAX
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, MAX_RETMAX))


def _with_pubmed_date_filter(query: str, date_range: Any) -> str:
    if not date_range:
        return query
    start = ""
    end = ""
    if isinstance(date_range, dict):
        start = str(date_range.get("start") or date_range.get("from") or "").strip()
        end = str(date_range.get("end") or date_range.get("to") or "").strip()
    elif isinstance(date_range, str) and "TO" in date_range:
        left, right = date_range.split("TO", 1)
        start = left.strip(" []")
        end = right.strip(" []")
    if not start or not end:
        return query
    return f"({query}) AND ({start}:{end}[dp])"


def _node_text(node: ElementTree.Element | None) -> str:
    if node is None:
        return ""
    return " ".join("".join(node.itertext()).split())


def _joined_node_text(node: ElementTree.Element | None) -> str:
    return _node_text(node)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first(root: ElementTree.Element, path: str) -> ElementTree.Element | None:
    node = root.find(path)
    if node is not None:
        return node
    # Namespace-tolerant fallback for NLM/JATS XML
    parts = [part for part in path.split("/") if part and part != "."]
    candidates = [root]
    for part in parts:
        next_candidates = []
        for candidate in candidates:
            next_candidates.extend(
                child for child in list(candidate) if _local_name(child.tag) == part
            )
        candidates = next_candidates
        if not candidates:
            return None
    return candidates[0] if candidates else None


def _abstract_sections(article_node: ElementTree.Element) -> list[dict[str, str]]:
    sections = []
    for abstract_text in article_node.findall(".//Abstract/AbstractText"):
        label = abstract_text.attrib.get("Label") or abstract_text.attrib.get("NlmCategory") or ""
        text = _joined_node_text(abstract_text)
        if text:
            sections.append({"label": label, "text": text})
    return sections


def _format_abstract_sections(sections: list[dict[str, str]]) -> str:
    parts = []
    for section in sections:
        label = str(section.get("label") or "").strip()
        text = str(section.get("text") or "").strip()
        if not text:
            continue
        parts.append(f"{label}: {text}" if label else text)
    return "\n".join(parts)


def _pmc_abstract_sections(article_node: ElementTree.Element) -> list[dict[str, str]]:
    abstract = _first(article_node, ".//front/article-meta/abstract")
    if abstract is None:
        return []
    sections: list[dict[str, str]] = []
    for child in list(abstract):
        local = _local_name(child.tag)
        if local == "sec":
            title = _joined_node_text(_child_by_local(child, "title"))
            text_parts = [
                _joined_node_text(node)
                for node in list(child)
                if _local_name(node.tag) != "title"
            ]
            text = " ".join(part for part in text_parts if part).strip()
            if text:
                sections.append({"label": title, "text": text})
        elif local in {"p", "title"}:
            text = _joined_node_text(child)
            if text and local != "title":
                sections.append({"label": "", "text": text})
    if not sections:
        text = _joined_node_text(abstract)
        if text:
            sections.append({"label": "", "text": text})
    return sections


def _pmc_keywords(article_node: ElementTree.Element) -> list[str]:
    keywords = []
    for keyword in article_node.findall(".//front/article-meta/kwd-group/kwd"):
        text = _joined_node_text(keyword)
        if text:
            keywords.append(text)
    return keywords


def _abstract_text(article_node: ElementTree.Element) -> str:
    parts = []
    for abstract_text in article_node.findall(".//Abstract/AbstractText"):
        label = abstract_text.attrib.get("Label") or abstract_text.attrib.get("NlmCategory") or ""
        text = _joined_node_text(abstract_text)
        if text:
            parts.append(f"{label}: {text}" if label else text)
    return " ".join(parts)


def safe_filename_part(value: str, max_length: int = 80) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", " ", str(value or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.strip(". ")
    return cleaned[:max_length].strip() or "untitled"


def material_filename(material_id: str, title: str, source: str, suffix: str) -> str:
    ext = suffix if suffix.startswith(".") else f".{suffix}"
    return f"{material_id}_{safe_filename_part(title)}_{safe_filename_part(source, 24)}{ext}"


def _pubmed_date(article_node: ElementTree.Element) -> str:
    date_node = article_node.find(".//Article/Journal/JournalIssue/PubDate")
    return _date_from_node(date_node)


def _pmc_dates(article_node: ElementTree.Element) -> tuple[str, str]:
    dates = article_node.findall(".//front/article-meta/pub-date")
    by_type = {node.attrib.get("pub-type", ""): _date_from_node(node) for node in dates}
    publication_date = (
        by_type.get("epub")
        or by_type.get("epub-ppub")
        or by_type.get("ppub")
        or by_type.get("epreprint")
        or next((value for key, value in by_type.items() if key != "collection" and value), "")
        or by_type.get("collection", "")
    )
    return publication_date, by_type.get("collection", "")


def _date_from_node(node: ElementTree.Element | None) -> str:
    if node is None:
        return ""
    year = _node_text(_child_by_local(node, "Year"))
    month = _node_text(_child_by_local(node, "Month"))
    day = _node_text(_child_by_local(node, "Day"))
    medline = _node_text(_child_by_local(node, "MedlineDate"))
    if year:
        return "-".join(part for part in [year, month, day] if part)
    return medline


def _child_by_local(node: ElementTree.Element, local_name: str) -> ElementTree.Element | None:
    for child in list(node):
        if _local_name(child.tag).lower() == local_name.lower():
            return child
    return None


def _pubmed_authors(article_node: ElementTree.Element) -> list[str]:
    authors = []
    for author in article_node.findall(".//AuthorList/Author"):
        collective = _node_text(author.find("CollectiveName"))
        if collective:
            authors.append(collective)
            continue
        last = _node_text(author.find("LastName"))
        fore = _node_text(author.find("ForeName")) or _node_text(author.find("Initials"))
        name = " ".join(part for part in [fore, last] if part)
        if name:
            authors.append(name)
    return authors


def _pmc_authors(article_node: ElementTree.Element) -> list[str]:
    authors = []
    for contrib in article_node.findall(".//contrib"):
        if contrib.attrib.get("contrib-type") not in {"author", ""}:
            continue
        name_node = contrib.find(".//name")
        if name_node is None:
            continue
        surname = _node_text(name_node.find("surname"))
        given = _node_text(name_node.find("given-names"))
        name = " ".join(part for part in [given, surname] if part)
        if name:
            authors.append(name)
    if authors:
        return authors
    # Namespace-tolerant fallback
    for contrib in article_node.iter():
        if _local_name(contrib.tag) != "contrib":
            continue
        surname = ""
        given = ""
        for descendant in contrib.iter():
            if _local_name(descendant.tag) == "surname":
                surname = _node_text(descendant)
            elif _local_name(descendant.tag) == "given-names":
                given = _node_text(descendant)
        name = " ".join(part for part in [given, surname] if part)
        if name:
            authors.append(name)
    return authors


def _article_ids(article_node: ElementTree.Element) -> dict[str, str]:
    ids: dict[str, str] = {}
    for node in article_node.iter():
        if _local_name(node.tag) != "article-id":
            continue
        pub_id_type = (node.attrib.get("pub-id-type") or "").lower()
        value = _node_text(node)
        if pub_id_type and value:
            ids[pub_id_type] = value
    return ids


def _looks_like_pdf(content: bytes, content_type: str) -> bool:
    head = content[:1024].lstrip()
    return head.startswith(b"%PDF") or "application/pdf" in content_type.lower()


def _duplicate_keys(article: dict[str, Any]) -> list[str]:
    keys = []
    for key in ["doi", "pmid", "pmcid"]:
        value = str(article.get(key) or "").strip()
        if value:
            keys.append(f"{key}:{value.lower()}")
    title = str(article.get("title") or "").strip().lower()
    if title:
        normalized_title = re.sub(r"\s+", " ", title)
        keys.append(f"title:{normalized_title}")
    return keys
