from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import parse_qsl, urlparse

from .jsonl import read_json
from .source_adapters.source_sites import SOURCE_SITES


SCENARIO_ID = "nmpa_competitor"
MANUAL_DIR = Path("manual/nmpa")
PLAN_PATH = MANUAL_DIR / "search_plan.json"
PLAN_MARKDOWN_PATH = MANUAL_DIR / "search_plan.md"
SEARCH_RECORD_TEMPLATE_PATH = MANUAL_DIR / "search_record_template.json"
IMPORT_MANIFEST_TEMPLATE_PATH = MANUAL_DIR / "import_manifest_template.json"
SEARCH_RECORDS_DIR = MANUAL_DIR / "search_records"
IMPORT_MANIFESTS_DIR = MANUAL_DIR / "import_manifests"
NMPA_SOURCE_SITE = next(
    site for site in SOURCE_SITES if site.source_site_id == SCENARIO_ID
)
NMPA_SEARCH_URL = NMPA_SOURCE_SITE.base_url

DOMESTIC_REGISTRATION = "境内医疗器械（注册）"
IMPORT_REGISTRATION = "进口医疗器械（注册）"
SUPPORTED_EVIDENCE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".pdf",
    ".csv",
    ".xls",
    ".xlsx",
    ".html",
    ".htm",
    ".json",
}
REQUIRED_RESULT_FIELDS = (
    "registration_certificate_number",
    "product_name",
    "registrant",
    "source_url",
)
OPTIONAL_RESULT_FIELDS = ("model", "scope", "approval_date", "valid_until")
FORBIDDEN_KEY_TOKENS = {
    "auth",
    "password",
    "passwd",
    "pwd",
    "cookie",
    "cookies",
    "token",
    "secret",
    "credential",
    "credentials",
    "authorization",
    "apikey",
}


def registration_types(competitor_scope: Any) -> list[str]:
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


def attempt_id(registration_type: str, query: str) -> str:
    digest = sha256(f"{registration_type}\0{query}".encode()).hexdigest()[:12]
    return f"NMPA-{digest.upper()}"


def attempt_signature(plan: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [
        (
            str(item.get("attempt_id") or ""),
            str(item.get("query") or ""),
            str(item.get("registration_type") or ""),
        )
        for item in plan.get("attempts", [])
    ]


def safe_path_segment(value: Any, *, label: str) -> str:
    text = str(value or "").strip()
    if (
        not text
        or len(text) > 80
        or text in {".", ".."}
        or "/" in text
        or "\\" in text
        or any(ord(char) < 32 for char in text)
    ):
        raise ValueError(f"{label} 无效，必须是不含路径符号的短文本。")
    safe = re.sub(r"[^\w.-]+", "_", text, flags=re.UNICODE).strip("._")
    if not safe:
        raise ValueError(f"{label} 无效。")
    return safe


def _forbidden_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
    tokens = set(normalized.split("_"))
    return normalized in {"api_key", "apikey"} or bool(
        tokens.intersection(FORBIDDEN_KEY_TOKENS)
    )


def reject_sensitive_fields(value: Any, *, path: str = "payload") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if _forbidden_key(key):
                raise ValueError(f"检测到禁止保存的敏感字段：{child_path}")
            reject_sensitive_fields(child, path=child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_sensitive_fields(child, path=f"{path}[{index}]")


def is_official_nmpa_url(value: Any) -> bool:
    parsed = urlparse(str(value or "").strip())
    host = str(parsed.hostname or "").lower()
    query_has_secret = any(_forbidden_key(key) for key, _ in parse_qsl(parsed.query))
    return (
        not parsed.username
        and not parsed.password
        and not query_has_secret
        and parsed.scheme == "https"
        and (host == "nmpa.gov.cn" or host.endswith(".nmpa.gov.cn"))
    )


def _validate_search_time(value: Any, *, attempt_id: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{attempt_id} 的 search_time 必须是 ISO 8601 时间。") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{attempt_id} 的 search_time 必须包含时区。")
    return text


def load_plan(task_dir: Path) -> dict[str, Any]:
    path = task_dir / PLAN_PATH
    if not path.exists():
        raise ValueError("尚未生成 NMPA 人工检索计划。")
    plan = read_json(path)
    if not plan.get("attempts"):
        raise ValueError("NMPA 人工检索计划没有必做检索项。")
    return plan


def plan_attempt_map(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item["attempt_id"]): item
        for item in plan.get("attempts", [])
        if item.get("attempt_id")
    }


def validate_task_id(payload: dict[str, Any], expected_task_id: str) -> None:
    if str(payload.get("task_id") or "") != expected_task_id:
        raise ValueError("task_id 与当前任务不一致。")


def validate_record_attempt(
    item: Any,
    *,
    plan_attempts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("search record 的 attempts 必须是对象列表。")
    item_attempt_id = str(item.get("attempt_id") or "").strip()
    expected = plan_attempts.get(item_attempt_id)
    if expected is None:
        raise ValueError(f"未知的 NMPA attempt_id：{item_attempt_id or '<empty>'}")
    if str(item.get("query") or "") != str(expected["query"]):
        raise ValueError(f"{item_attempt_id} 的 query 与检索计划不一致。")
    if str(item.get("registration_type") or "") != str(
        expected["registration_type"]
    ):
        raise ValueError(
            f"{item_attempt_id} 的 registration_type 与检索计划不一致。"
        )
    official_url = str(item.get("official_search_url") or "").strip()
    if not is_official_nmpa_url(official_url):
        raise ValueError(f"{item_attempt_id} 的网址不是 NMPA 官方 HTTPS 地址。")
    if official_url != str(expected["official_search_url"]):
        raise ValueError(
            f"{item_attempt_id} 的 official_search_url 与检索计划不一致。"
        )
    result_count = item.get("result_count")
    if isinstance(result_count, bool) or not isinstance(result_count, int):
        raise ValueError(f"{item_attempt_id} 的 result_count 必须是非负整数。")
    if result_count < 0:
        raise ValueError(f"{item_attempt_id} 的 result_count 不能小于 0。")
    return {
        "attempt_id": item_attempt_id,
        "query": str(expected["query"]),
        "registration_type": str(expected["registration_type"]),
        "search_time": _validate_search_time(
            item.get("search_time"), attempt_id=item_attempt_id
        ),
        "official_search_url": official_url,
        "result_count": result_count,
        "notes": str(item.get("notes") or ""),
    }


def record_evidence_signature(item: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(
        item.get(field)
        for field in (
            "query",
            "registration_type",
            "search_time",
            "official_search_url",
            "result_count",
        )
    )


def _validate_evidence_content(path: Path) -> None:
    suffix = path.suffix.lower()
    size = path.stat().st_size
    if size == 0:
        raise ValueError(f"证据文件为空：{path}")
    with path.open("rb") as handle:
        header = handle.read(16)
    valid = True
    if suffix == ".png":
        valid = header.startswith(b"\x89PNG\r\n\x1a\n")
    elif suffix in {".jpg", ".jpeg"}:
        valid = header.startswith(b"\xff\xd8\xff")
    elif suffix == ".webp":
        valid = header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    elif suffix == ".pdf":
        valid = header.startswith(b"%PDF-")
    elif suffix == ".xlsx":
        valid = header.startswith(b"PK\x03\x04")
    elif suffix == ".xls":
        valid = header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    elif suffix in {".html", ".htm"}:
        with path.open("rb") as handle:
            prefix = handle.read(65536).decode("utf-8", errors="ignore").lower()
        valid = "<html" in prefix or "<!doctype html" in prefix
    elif suffix == ".json":
        try:
            json.loads(path.read_text(encoding="utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError):
            valid = False
    if not valid:
        raise ValueError(f"证据文件扩展名与内容格式不一致：{path}")


def resolve_evidence_path(manifest_path: Path, raw_path: Any) -> Path:
    text = str(raw_path or "").strip()
    if not text:
        raise ValueError("evidence_files 中存在空路径。")
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = manifest_path.parent / path
    if not path.exists() or not path.is_file():
        raise ValueError(f"证据文件不存在：{path}")
    if path.suffix.lower() not in SUPPORTED_EVIDENCE_SUFFIXES:
        raise ValueError(f"不支持的证据文件类型：{path.suffix or '<none>'}")
    resolved = path.resolve()
    _validate_evidence_content(resolved)
    return resolved


def _validate_result(item: Any, *, attempt_id: str) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(item, dict):
        raise ValueError(f"{attempt_id} 的 results 必须是对象列表。")
    normalized = {str(key): value for key, value in item.items()}
    missing_required = [
        field
        for field in REQUIRED_RESULT_FIELDS
        if not str(normalized.get(field) or "").strip()
    ]
    if missing_required:
        raise ValueError(
            f"{attempt_id} 的结果缺少必填字段：{', '.join(missing_required)}"
        )
    if not is_official_nmpa_url(normalized["source_url"]):
        raise ValueError(f"{attempt_id} 的结果 source_url 不是 NMPA 官方 HTTPS 地址。")
    for field in REQUIRED_RESULT_FIELDS:
        normalized[field] = str(normalized[field]).strip()
    for field in OPTIONAL_RESULT_FIELDS:
        normalized[field] = str(normalized.get(field) or "").strip()
    missing_optional = [field for field in OPTIONAL_RESULT_FIELDS if not normalized[field]]
    warnings = [
        f"{attempt_id} / {normalized['registration_certificate_number']} 缺少可选字段 {field}"
        for field in missing_optional
    ]
    return normalized, warnings


def validate_manifest_attempt(
    item: Any,
    *,
    manifest_path: Path,
    plan_attempts: dict[str, dict[str, Any]],
    recorded_attempts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("import manifest 的 attempts 必须是对象列表。")
    item_attempt_id = str(item.get("attempt_id") or "").strip()
    if item_attempt_id not in plan_attempts:
        raise ValueError(f"未知的 NMPA attempt_id：{item_attempt_id or '<empty>'}")
    record = recorded_attempts.get(item_attempt_id)
    if record is None:
        raise ValueError(f"{item_attempt_id} 尚未记录人工检索，不能导入。")
    evidence_values = item.get("evidence_files")
    if not isinstance(evidence_values, list) or not evidence_values:
        raise ValueError(
            f"{item_attempt_id} 必须提供至少一个 evidence_files 证据文件。"
        )
    evidence_paths = [
        resolve_evidence_path(manifest_path, raw_path) for raw_path in evidence_values
    ]
    results = item.get("results")
    if not isinstance(results, list):
        raise ValueError(f"{item_attempt_id} 的 results 必须是列表。")
    expected_count = int(record["result_count"])
    if len(results) != expected_count:
        raise ValueError(
            f"{item_attempt_id} 的 results 数量与检索记录 "
            f"result_count={expected_count} 不一致。"
        )
    normalized_results: list[dict[str, Any]] = []
    warnings: list[str] = []
    registration_numbers: set[str] = set()
    for result in results:
        normalized, result_warnings = _validate_result(
            result, attempt_id=item_attempt_id
        )
        registration_number = normalized["registration_certificate_number"].lower()
        if registration_number in registration_numbers:
            raise ValueError(f"{item_attempt_id} 的 results 存在重复注册证编号。")
        registration_numbers.add(registration_number)
        normalized_results.append(normalized)
        warnings.extend(result_warnings)
    return {
        "attempt_id": item_attempt_id,
        "evidence_source_paths": evidence_paths,
        "results": normalized_results,
        "warnings": warnings,
    }


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
