"""Claim traceability, research saturation, and data-boundary checks."""

import ipaddress
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qsl, urlparse

from pydantic import BaseModel, Field

from .jsonl import append_jsonl, read_json, read_jsonl


class ResearchClaim(BaseModel):
    claim_id: str
    text: str
    claim_type: Literal[
        "fact",
        "research_judgement",
        "recommendation",
        "consensus",
    ] = "fact"
    status: Literal[
        "supported",
        "partially_supported",
        "disputed",
        "unsupported",
    ] = "unsupported"
    evidence_card_ids: list[str] = Field(default_factory=list)
    metric_fact_ids: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "low"
    inference: bool = False
    impact: Literal["normal", "high"] = "normal"
    needs_human_review: bool = True
    reviewed_at: str = ""
    reviewer_role: str = ""
    review_note: str = ""


class EvidenceConflict(BaseModel):
    conflict_id: str
    claim_ids: list[str] = Field(default_factory=list)
    evidence_card_ids: list[str] = Field(default_factory=list)
    conflict_type: Literal[
        "data",
        "methodology",
        "regulatory_status",
        "source_disagreement",
        "other",
    ] = "other"
    summary: str
    resolution_status: Literal[
        "unresolved",
        "resolved",
        "accepted_difference",
    ] = "unresolved"
    resolution_note: str = ""
    needs_human_review: bool = True


class ResearchIteration(BaseModel):
    iteration_id: str
    direction: str
    pivot_axis: Literal[
        "source_lane",
        "language",
        "geography",
        "time_range",
        "claim_stance",
        "document_format",
        "coverage_gap",
        "query_strategy",
    ]
    new_verified_materials: int = 0
    new_publishers: int = 0
    new_claims: int = 0
    changed_claims: int = 0
    resolved_gaps: int = 0
    quality_targets_met: bool = False
    created_at: str = ""


class ResearchPolicy(BaseModel):
    required: bool = True
    data_classification: Literal["public", "internal", "confidential"] = "public"
    external_provider_allowed: bool = True
    internal_access_approved: bool = False
    approved_internal_route: str = ""
    high_impact_min_publishers: int = Field(default=2, ge=2)


def record_research_claim(task_dir: Path, claim: ResearchClaim | dict) -> dict:
    payload = ResearchClaim.model_validate(claim).model_dump(mode="json")
    append_jsonl(Path(task_dir) / "data" / "research_claims.jsonl", payload)
    return payload


def record_evidence_conflict(task_dir: Path, conflict: EvidenceConflict | dict) -> dict:
    payload = EvidenceConflict.model_validate(conflict).model_dump(mode="json")
    append_jsonl(Path(task_dir) / "data" / "evidence_conflicts.jsonl", payload)
    return payload


def record_research_iteration(task_dir: Path, iteration: ResearchIteration | dict) -> dict:
    payload = ResearchIteration.model_validate(iteration).model_dump(mode="json")
    path = Path(task_dir) / "logs" / "research_iterations.jsonl"
    existing = list(read_jsonl(path))
    if any(row.get("iteration_id") == payload["iteration_id"] for row in existing):
        raise ValueError(f"迭代 ID 已记录：{payload['iteration_id']}")
    normalized_direction = " ".join(payload["direction"].lower().split())
    existing_directions = {
        " ".join(str(row.get("direction") or "").lower().split())
        for row in existing
    }
    if normalized_direction in existing_directions:
        raise ValueError(f"研究方向已记录：{payload['direction']}")
    append_jsonl(path, payload)
    return payload


def research_iteration_state(task_dir: Path) -> dict:
    iterations = list(read_jsonl(Path(task_dir) / "logs" / "research_iterations.jsonl"))

    def productive(row: dict) -> bool:
        return any(
            int(row.get(field, 0) or 0) > 0
            for field in (
                "new_verified_materials",
                "new_publishers",
                "new_claims",
                "changed_claims",
                "resolved_gaps",
            )
        )

    stale_count = 0
    for row in reversed(iterations):
        if productive(row) or row.get("quality_targets_met"):
            break
        stale_count += 1

    saturation_rows = []
    for row in reversed(iterations):
        if productive(row) or not row.get("quality_targets_met"):
            break
        saturation_rows.append(row)
    saturation_rows.reverse()
    saturation_axes = {
        str(row.get("pivot_axis") or "") for row in saturation_rows if row.get("pivot_axis")
    }
    ready = len(saturation_rows) >= 2 and len(saturation_axes) >= 2
    return {
        "iteration_count": len(iterations),
        "stale_count": stale_count,
        "pivot_required": stale_count >= 2 and not ready,
        "saturation_count": len(saturation_rows),
        "saturation_axes": sorted(saturation_axes),
        "ready_for_validation": ready,
    }


def _issue(
    issue_type: str,
    finding: str,
    *,
    severity: str = "high",
    recommendation: str = "完成补证或人工复核后重新运行研究完整性审计。",
) -> dict:
    return {
        "issue_type": issue_type,
        "severity": severity,
        "finding": finding,
        "recommendation": recommendation,
    }


def _publisher_key(material: dict, card: dict) -> str:
    raw_fields = material.get("raw_fields") or {}
    for value in (
        raw_fields.get("publisher"),
        raw_fields.get("journal"),
        raw_fields.get("organization"),
        card.get("source_name"),
        material.get("source_name"),
    ):
        normalized = " ".join(str(value or "").lower().split())
        if normalized:
            return normalized
    hostname = (urlparse(str(material.get("source_url") or "")).hostname or "").lower()
    return hostname or str(material.get("source_site_id") or "").lower()


def _material_content_verified(material: dict) -> bool:
    raw_fields = material.get("raw_fields") or {}
    collection_path = material.get("collection_path") or {}
    retrieval_kind = str(
        raw_fields.get("retrieval_kind")
        or collection_path.get("retrieval_kind")
        or ""
    )
    content_verified = raw_fields.get(
        "content_verified",
        collection_path.get("content_verified"),
    )
    if retrieval_kind == "search_result":
        return False
    return content_verified is not False


def build_research_integrity_audit(task_dir: Path, *, task: dict | None = None) -> dict:
    task_dir = Path(task_dir)
    if task is None:
        task_path = task_dir / "task.json"
        task = read_json(task_path) if task_path.exists() else {}
    policy = ResearchPolicy.model_validate(task.get("research_policy") or {})
    if not policy.required:
        return {
            "required": False,
            "ready": True,
            "status": "not_required",
            "issues": [],
            "warnings": [],
            "claim_count": 0,
            "conflict_count": 0,
            "independent_publisher_count": 0,
            "iteration_state": research_iteration_state(task_dir),
            "data_boundary_ready": True,
        }

    claims = list(read_jsonl(task_dir / "data" / "research_claims.jsonl"))
    conflicts = list(read_jsonl(task_dir / "data" / "evidence_conflicts.jsonl"))
    cards = list(read_jsonl(task_dir / "data" / "evidence_cards.jsonl"))
    reviewed_cards = list(read_jsonl(task_dir / "data" / "reviewed_evidence_cards.jsonl"))
    card_by_id = {
        str(card.get("evidence_card_id") or ""): card
        for card in [*cards, *reviewed_cards]
        if card.get("evidence_card_id")
    }
    material_by_id = {
        str(material.get("material_id") or ""): material
        for material in read_jsonl(task_dir / "data" / "materials.jsonl")
        if material.get("material_id")
    }
    metric_ids = {
        str(metric.get("metric_fact_id") or "")
        for metric in read_jsonl(task_dir / "knowledge" / "metric_facts.jsonl")
        if metric.get("metric_fact_id")
    }

    issues: list[dict] = []
    warnings: list[str] = []
    publisher_keys: set[str] = set()
    conflict_claim_ids = {
        str(claim_id)
        for conflict in conflicts
        for claim_id in conflict.get("claim_ids") or []
    }

    if not claims:
        issues.append(
            _issue(
                "missing_research_claims",
                "尚未登记可追溯的研究论断，报告结论无法逐条绑定证据。",
            )
        )

    for claim in claims:
        claim_id = str(claim.get("claim_id") or "未命名论断")
        evidence_ids = [str(item) for item in claim.get("evidence_card_ids") or []]
        status = str(claim.get("status") or "unsupported")
        if claim.get("needs_human_review", True):
            issues.append(_issue("unreviewed_claim", f"论断 {claim_id} 尚未完成人工复核。"))
        elif not claim.get("reviewed_at") or not claim.get("reviewer_role"):
            issues.append(
                _issue(
                    "claim_review_trace_missing",
                    f"论断 {claim_id} 已标记为复核完成，但缺少复核角色或复核时间。",
                )
            )
        if status in {"supported", "partially_supported", "disputed"} and not evidence_ids:
            issues.append(_issue("missing_claim_evidence", f"论断 {claim_id} 缺少证据卡引用。"))
        unknown_cards = sorted(set(evidence_ids) - set(card_by_id))
        if unknown_cards:
            issues.append(
                _issue(
                    "unknown_evidence_card",
                    f"论断 {claim_id} 引用了不存在的证据卡：{'、'.join(unknown_cards)}。",
                )
            )
        unknown_metrics = sorted(
            set(str(item) for item in claim.get("metric_fact_ids") or []) - metric_ids
        )
        if unknown_metrics:
            issues.append(
                _issue(
                    "unknown_metric_fact",
                    f"论断 {claim_id} 引用了不存在的指标事实：{'、'.join(unknown_metrics)}。",
                )
            )

        claim_publishers: set[str] = set()
        for evidence_id in evidence_ids:
            card = card_by_id.get(evidence_id)
            if not card:
                continue
            material = material_by_id.get(str(card.get("material_id") or ""), {})
            if not _material_content_verified(material):
                issues.append(
                    _issue(
                        "unverified_search_result",
                        f"论断 {claim_id} 使用了仅有搜索摘要、尚未读取正文的证据卡 {evidence_id}。",
                    )
                )
                continue
            publisher = _publisher_key(material, card)
            if publisher:
                claim_publishers.add(publisher)
                publisher_keys.add(publisher)

        if claim.get("claim_type") in {"recommendation", "consensus"}:
            if not claim.get("inference") or claim.get("impact") != "high":
                issues.append(
                    _issue(
                        "invalid_high_impact_classification",
                        f"论断 {claim_id} 属于建议或共识判断，必须标记为高影响推断。",
                    )
                )
        minimum_publishers = policy.high_impact_min_publishers
        if claim.get("claim_type") == "consensus":
            minimum_publishers = max(minimum_publishers, 3)
        if (
            status == "supported"
            and claim.get("impact") == "high"
            and len(claim_publishers) < minimum_publishers
        ):
            issues.append(
                _issue(
                    "insufficient_independent_publishers",
                    f"高影响论断 {claim_id} 仅覆盖 {len(claim_publishers)} 个独立发布机构，"
                    f"最低要求为 {minimum_publishers} 个。",
                )
            )
        if status == "disputed" and claim_id not in conflict_claim_ids:
            issues.append(
                _issue(
                    "missing_conflict_record",
                    f"争议论断 {claim_id} 尚未建立证据冲突记录。",
                )
            )

    for conflict in conflicts:
        if conflict.get("resolution_status") == "unresolved" or conflict.get(
            "needs_human_review", True
        ):
            issues.append(
                _issue(
                    "unresolved_evidence_conflict",
                    f"证据冲突 {conflict.get('conflict_id') or '未命名'} 尚未完成处理。",
                )
            )

    iteration_state = research_iteration_state(task_dir)
    if not iteration_state["ready_for_validation"]:
        issues.append(
            _issue(
                "research_not_saturated",
                "尚未完成两个不同方向的零增量复核，不能证明研究已达到可验证状态。",
            )
        )

    data_boundary_ready = True
    if policy.data_classification in {"internal", "confidential"}:
        data_boundary_ready = bool(
            policy.internal_access_approved and policy.approved_internal_route
        ) and not policy.external_provider_allowed
        if not data_boundary_ready:
            issues.append(
                _issue(
                    "data_boundary_not_ready",
                    "内部或机密数据缺少已批准的内部检索通道，或仍允许发送到公共服务。",
                )
            )

    ready = not issues
    return {
        "required": True,
        "ready": ready,
        "status": "ready" if ready else "needs_action",
        "issues": issues,
        "warnings": warnings,
        "claim_count": len(claims),
        "conflict_count": len(conflicts),
        "independent_publisher_count": len(publisher_keys),
        "iteration_state": iteration_state,
        "data_boundary_ready": data_boundary_ready,
    }


def validate_retrieval_target(
    url: str,
    policy: ResearchPolicy | dict,
    *,
    external_provider: bool = True,
) -> str:
    policy = ResearchPolicy.model_validate(policy)
    validate_retrieval_policy(policy, external_provider=external_provider)

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("采集目标必须是有效的 HTTP 或 HTTPS 地址")
    if parsed.username or parsed.password:
        raise ValueError("采集地址不得包含凭据或签名参数")
    sensitive_keys = {
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "key",
        "signature",
        "sig",
        "token",
    }
    if any(key.lower() in sensitive_keys for key, _ in parse_qsl(parsed.query)):
        raise ValueError("采集地址不得包含凭据或签名参数")

    if external_provider:
        host = parsed.hostname.rstrip(".").lower()
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise ValueError("公共采集服务不得访问本机或内网地址")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError("公共采集服务不得访问本机或内网地址")
    return url


def validate_retrieval_policy(
    policy: ResearchPolicy | dict,
    *,
    external_provider: bool,
) -> ResearchPolicy:
    policy = ResearchPolicy.model_validate(policy)
    if external_provider and policy.data_classification in {"internal", "confidential"}:
        raise ValueError("内部或机密数据不得发送到公共采集服务")
    if external_provider and not policy.external_provider_allowed:
        raise ValueError("当前研究策略不允许使用公共采集服务")
    if not external_provider and policy.data_classification in {"internal", "confidential"}:
        if not policy.internal_access_approved or not policy.approved_internal_route:
            raise ValueError("内部数据采集缺少明确授权或已批准的内部检索通道")
    return policy
