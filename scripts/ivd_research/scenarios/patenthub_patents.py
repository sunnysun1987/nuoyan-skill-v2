import re
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ivd_research.models import Material
from ivd_research.scenarios.base import now_iso

from .registry import get_scenario
from .site_collect import collect_search_snapshot

PATENTHUB_GLOBAL_SEARCH_URL = "https://www.patenthub.cn/s?ds=all&q={query}"
PATENT_NUMBER_RE = re.compile(r"\b[A-Z]{2}\d{5,}[A-Z0-9]?\b")
PATENT_DETAIL_RE = re.compile(r"/patent/([A-Z]{2}\d{5,}[A-Z0-9]?)\.html$", re.I)
PATENTHUB_LOGIN_MARKERS = (
    "用户登录",
    "注册登录后可以查看更多专利信息",
    "登录后可以查看更多专利信息",
)


def adapter():
    return get_scenario("patenthub_patents")


def collect(task_id, task_dir, params):
    failure_modes = ("collection_failed", "no_results")
    result = collect_search_snapshot(
        task_id=task_id,
        task_dir=task_dir,
        params=params,
        scenario_id="patenthub_patents",
        material_type="patent",
        subject_zh="专利汇全球专利",
        search_url_template=PATENTHUB_GLOBAL_SEARCH_URL,
        no_result_markers=["没有找到", "暂无数据"],
        validation_rules=["专利条目包含标题、公开号或基本信息", *failure_modes],
    )
    if result.status in {"needs_login", "permission_required", "collection_failed"}:
        result.message_zh = (
            f"{result.message_zh} 兜底动作：先重试公开检索；仍失败时运行 open-browser-session "
            "--scenario patenthub_patents --background，完成登录或真人验证后，"
            "重新运行 PatentHub 采集。"
        )
    return result


def parse_patenthub_result_list(html: str, base_url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    if patenthub_page_status(soup.get_text(" ", strip=True), soup.title.get_text(" ", strip=True) if soup.title else "") == "needs_login":
        return []
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, anchor in enumerate(soup.select("a[href*='/patent/']"), start=1):
        title = normalize_text(anchor.get_text(" ", strip=True))
        href = anchor.get("href", "")
        if not title or not href:
            continue
        detail_url = urljoin(base_url, href)
        publication_number = patent_number_from_detail_url(detail_url)
        if not publication_number:
            continue
        dedupe_key = publication_number.upper()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        container = anchor.find_parent(["div", "li", "tr", "article"]) or anchor.parent
        visible_text = normalize_text(container.get_text(" ", strip=True) if container else title)
        entries.append(
            {
                "title": title,
                "detail_url": detail_url,
                "list_index": index,
                "publication_number": first_patent_number(visible_text) or publication_number,
                "visible_text": visible_text,
            }
        )
    return entries


def parse_patenthub_detail(html: str, detail_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    title_node = soup.find("h1") or soup.find("title")
    page_title = title_node.get_text(" ", strip=True) if title_node else "专利详情"
    body_text = soup.get_text(" ", strip=True)
    compact_text = normalize_text(body_text)
    page_status = patenthub_page_status(compact_text, page_title)

    if page_status != "page_ready":
        return {
            "title": "",
            "detail_url": detail_url,
            "basic_info_text": "",
            "publication_number": "",
            "application_number": "",
            "application_date": "",
            "publication_date": "",
            "inventors": "",
            "applicant": "",
            "patentee": "",
            "ipc": "",
            "abstract": "",
            "legal_status": "",
            "extracted_text": "",
            "pdf_status": "not_attempted",
            "pdf_restriction_zh": "",
            "page_status": page_status,
            "is_valid_patent_detail": False,
        }

    basic_info_text = (
        section_text_by_heading(soup, "基本信息")
        or text_between(compact_text, "基本信息", ["法律状态", "相似专利", "扩展内容", "任务列表", "PDF"])
        or compact_text
    )
    basic_info_text = trim_noise(basic_info_text, 3000)
    publication_number = (
        field_after_label(compact_text, "公开号")
        or field_after_label(compact_text, "公开(公告)号")
        or patent_number_from_detail_url(detail_url)
        or first_patent_number(compact_text)
    )
    patent_title = (
        field_after_label(compact_text, "专利标题")
        or title_from_patenthub_text(compact_text, publication_number)
        or clean_patent_title(page_title)
    )
    abstract = field_after_label(compact_text, "摘要") or text_between(
        compact_text,
        "摘要",
        ["主权项", "权利要求", "说明书", "法律状态", "基本信息"],
    )
    applicant = field_after_label(compact_text, "申请人")
    patentee = field_after_label(compact_text, "专利权人")
    inventors = field_after_label(compact_text, "发明人")
    application_number = field_after_label(compact_text, "申请号")
    application_date = field_after_label(compact_text, "申请日")
    publication_date = field_after_label(compact_text, "公开日") or field_after_label(
        compact_text, "公开(公告)日"
    )
    ipc = field_after_label(compact_text, "IPC分类号") or field_after_label(compact_text, "分类号")
    legal_status = field_after_label(compact_text, "法律状态")

    pdf_restricted = any(token in body_text for token in ["VIP", "购买VIP", "付费", "权限"])
    pdf_status = "permission_required" if pdf_restricted else "not_found"
    pdf_restriction_zh = "专利 PDF 或全文下载受 VIP/权限限制，未尝试付费下载。" if pdf_restricted else ""

    detail = {
        "title": patent_title,
        "detail_url": detail_url,
        "basic_info_text": basic_info_text,
        "publication_number": publication_number,
        "application_number": application_number,
        "application_date": application_date,
        "publication_date": publication_date,
        "inventors": inventors,
        "applicant": applicant,
        "patentee": patentee,
        "ipc": ipc,
        "abstract": trim_noise(abstract, 2000),
        "legal_status": legal_status,
        "extracted_text": patent_extracted_text(
            title=patent_title,
            publication_number=publication_number,
            application_number=application_number,
            application_date=application_date,
            publication_date=publication_date,
            inventors=inventors,
            applicant=applicant,
            patentee=patentee,
            ipc=ipc,
            abstract=abstract,
            legal_status=legal_status,
            basic_info_text=basic_info_text,
        ),
        "pdf_status": pdf_status,
        "pdf_restriction_zh": pdf_restriction_zh,
        "page_status": page_status,
    }
    detail["is_valid_patent_detail"] = valid_patenthub_detail(detail)
    return detail


def patenthub_page_status(text: str, title: str = "") -> str:
    normalized = normalize_text(f"{title} {text}")
    if any(marker in normalized for marker in PATENTHUB_LOGIN_MARKERS):
        return "needs_login"
    return "page_ready"


def valid_patenthub_detail(detail: dict[str, Any]) -> bool:
    publication_number = normalize_text(str(detail.get("publication_number", "")))
    title = normalize_text(str(detail.get("title", "")))
    evidence_fields = (
        "application_number",
        "applicant",
        "patentee",
        "inventors",
        "ipc",
        "abstract",
        "legal_status",
    )
    has_detail_evidence = any(normalize_text(str(detail.get(key, ""))) for key in evidence_fields)
    return bool(
        PATENT_NUMBER_RE.fullmatch(publication_number)
        and title
        and title not in {"专利详情", "用户登录"}
        and has_detail_evidence
    )


def patent_number_from_detail_url(url: str) -> str:
    match = PATENT_DETAIL_RE.search(urlparse(url).path)
    return match.group(1) if match else ""


def first_patent_number(text: str) -> str:
    match = PATENT_NUMBER_RE.search(text)
    return match.group(0) if match else ""


def normalize_text(text: str) -> str:
    return " ".join((text or "").split())


def trim_noise(text: str, limit: int) -> str:
    return normalize_text(text)[:limit]


def clean_patent_title(title: str) -> str:
    cleaned = re.sub(r"\s*[-_]\s*专利汇.*$", "", normalize_text(title))
    cleaned = re.split(r"\s+专利标题[（(]英[）)]\s*[:：]", cleaned, maxsplit=1)[0]
    cleaned = cleaned.replace("专利汇", "").strip(" -_")
    return cleaned or "专利详情"


def title_from_patenthub_text(text: str, publication_number: str) -> str:
    if not publication_number:
        return ""
    match = re.search(
        rf"(?:发明公开|实用新型|外观设计)?\s*{re.escape(publication_number)}\s+(.+?)(?:\s+(?:基本信息|申请号|申请人|发明人|摘要|法律状态)\b)",
        text,
    )
    return clean_patent_title(match.group(1)) if match else ""


def text_between(text: str, start: str, end_tokens: list[str]) -> str:
    start_index = text.find(start)
    if start_index < 0:
        return ""
    tail = text[start_index + len(start) :]
    positions = [tail.find(token) for token in end_tokens if tail.find(token) >= 0]
    if positions:
        tail = tail[: min(positions)]
    return normalize_text(tail)


def field_after_label(text: str, label: str) -> str:
    labels = (
        "专利标题|公开号|公开\\(公告\\)号|申请号|申请日|公开日|公开\\(公告\\)日|"
        "申请人|专利权人|发明人|IPC分类号|分类号|摘要|摘要（英）|主权项|"
        "公开/授权文献|扩展内容|信息查询|任务列表|法律状态|基本信息"
    )
    pattern = rf"{re.escape(label)}\s*[:：]\s*(.+?)(?=\s+(?:{labels})\s*[:：]|\s*$)"
    match = re.search(pattern, text)
    return normalize_text(match.group(1))[:1000] if match else ""


def patent_extracted_text(**fields: str) -> str:
    labels = [
        ("title", "专利标题"),
        ("publication_number", "公开号"),
        ("application_number", "申请号"),
        ("application_date", "申请日"),
        ("publication_date", "公开日"),
        ("applicant", "申请人"),
        ("patentee", "专利权人"),
        ("inventors", "发明人"),
        ("ipc", "IPC分类号"),
        ("legal_status", "法律状态"),
        ("abstract", "摘要"),
        ("basic_info_text", "基本信息全文"),
    ]
    lines = []
    for key, label in labels:
        value = normalize_text(fields.get(key, ""))
        if value:
            lines.append(f"{label}：{value}")
    return "\n".join(lines)


def section_text_by_heading(soup: BeautifulSoup, heading_text: str) -> str:
    for heading in soup.find_all(["h1", "h2", "h3", "h4"]):
        if heading_text not in heading.get_text(" ", strip=True):
            continue
        parent = heading.find_parent(["section", "div", "article"])
        if parent:
            return normalize_text(parent.get_text(" ", strip=True))
    return ""


def build_patenthub_material(
    *,
    task_id: str,
    material_id: str,
    query: str,
    search_url: str,
    search_snapshot: str,
    detail_snapshot: str,
    entry: dict[str, Any],
    detail: dict[str, Any],
    extracted_text_path: str = "",
) -> Material:
    if not detail.get("is_valid_patent_detail"):
        raise ValueError("not a valid PatentHub patent detail")
    return Material(
        material_id=material_id,
        task_id=task_id,
        source_scenario="patenthub_patents",
        material_type="patent",
        title=detail.get("title") or entry["title"],
        source_url=entry["detail_url"],
        search_keyword_or_query=query,
        collection_path={
            "scenario_id": "patenthub_patents",
            "search_url": search_url,
            "detail_url": entry["detail_url"],
            "search_snapshot": search_snapshot,
            "detail_snapshot": detail_snapshot,
            "list_index": entry.get("list_index"),
        },
        collection_time=now_iso(),
        adapter_id="patenthub_patents",
        adapter_version="0.5.0",
        raw_fields={
            "publication_number": detail.get("publication_number") or entry.get("publication_number", ""),
            "application_number": detail.get("application_number", ""),
            "application_date": detail.get("application_date", ""),
            "publication_date": detail.get("publication_date", ""),
            "inventors": detail.get("inventors", ""),
            "applicant": detail.get("applicant", ""),
            "patentee": detail.get("patentee", ""),
            "ipc": detail.get("ipc", ""),
            "abstract": detail.get("abstract", ""),
            "legal_status": detail.get("legal_status", ""),
            "basic_info_text": detail.get("basic_info_text", ""),
            "list_visible_text": entry.get("visible_text", ""),
            "pdf_status": detail.get("pdf_status", ""),
            "pdf_restriction_zh": detail.get("pdf_restriction_zh", ""),
        },
        download_status=detail.get("pdf_status", "not_attempted"),
        extracted_text_status="completed" if extracted_text_path else "not_attempted",
        extracted_text_path=extracted_text_path,
        content_snapshot_path=detail_snapshot,
    )
