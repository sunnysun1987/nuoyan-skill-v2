from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from .constants import EVIDENCE_STRENGTH_LABELS, MATERIAL_TYPE_LABELS


class FailureType(str, Enum):
    COLLECTION_FAILED = "collection_failed"
    NO_RESULTS = "no_results"
    NO_VALID_MATERIALS = "no_valid_materials"
    DOWNLOAD_FAILED = "download_failed"
    PARSE_FAILED = "parse_failed"
    PERMISSION_REQUIRED = "permission_required"
    NEEDS_LOGIN = "needs_login"
    NEEDS_OCR = "needs_ocr"
    NEEDS_MANUAL_REVIEW = "needs_manual_review"


class DownloadFile(BaseModel):
    original_filename: str = ""
    stored_filename: str = ""
    relative_path: str = ""
    source_url: str = ""
    sha256: str = ""
    status: str = "not_downloaded"


class KeyExcerpt(BaseModel):
    text: str
    location: str = "location_unknown"
    source_path: str = ""


class Material(BaseModel):
    material_id: str
    task_id: str
    source_scenario: str
    material_type: str = "unknown"
    title: str
    source_url: str = ""
    search_keyword_or_query: str = ""
    collection_path: dict[str, Any] = Field(default_factory=dict)
    collection_time: str
    adapter_id: str = ""
    adapter_version: str = ""
    raw_fields: dict[str, Any] = Field(default_factory=dict)
    download_status: str = "not_attempted"
    download_files: list[DownloadFile] = Field(default_factory=list)
    extracted_text_status: str = "not_attempted"
    extracted_text_path: str = ""
    content_snapshot_path: str = ""
    failure_type: FailureType | None = None
    failure_reason: str = ""
    possible_duplicate_keys: list[str] = Field(default_factory=list)
    source_site_id: str = ""
    source_name: str = ""
    evidence_lane: str = ""
    source_run_id: str = ""

    @property
    def display_labels(self) -> dict[str, str]:
        return {
            "material_type": MATERIAL_TYPE_LABELS.get(
                self.material_type, self.material_type
            )
        }


class EvidenceCard(BaseModel):
    evidence_card_id: str
    material_id: str
    material_type: str
    title: str
    translated_title: str = ""
    source_type: str = ""
    source_quality: str = ""
    publication_or_issue_date: str = ""
    identifier: str = ""
    summary: str
    tr1_file: str = ""
    primary_tag: str = ""
    secondary_tag: str = ""
    performance_tag: str = ""
    evidence_conclusion: str = ""
    exact_data: str = ""
    source_location: str = ""
    original_excerpt_or_table_marker: str = ""
    chinese_evidence_explanation: str = ""
    key_facts: list[str] = Field(default_factory=list)
    key_excerpts: list[KeyExcerpt] = Field(default_factory=list)
    taxonomy_tags: list[str] = Field(default_factory=list)
    evidence_strength: str = "needs_review"
    confidence_level: str = "待复核"
    include_in_report: bool = False
    report_usage: str = ""
    facts: list[str] = Field(default_factory=list)
    inferences: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    needs_review: bool = True
    review_reasons: list[str] = Field(default_factory=lambda: ["未人工复核"])
    manual_review: dict[str, Any] = Field(default_factory=dict)
    source_site_id: str = ""
    source_input_trace: dict[str, str] = Field(default_factory=dict)
    source_name: str = ""
    priority_level: str = "C"
    card_status: str = "draft"
    disease: str = ""
    biomarker_or_target: str = ""
    sample_type: str = ""
    intended_use: str = ""
    population: str = ""
    platform: str = ""
    reference_standard: str = ""
    research_stage: str = ""
    one_sentence_conclusion: str = ""
    metric_facts: list[dict[str, Any]] = Field(default_factory=list)
    related_material_ids: list[str] = Field(default_factory=list)
    relation_type: str = ""
    relation_reason: str = ""
    shared_marker: str = ""
    shared_cohort: str = ""
    cites: list[str] = Field(default_factory=list)
    gap_tasks: list[str] = Field(default_factory=list)
    permission_status: str = ""
    fulltext_status: str = ""
    reviewer_role: str = ""
    manual_decision: str = ""
    reviewed_at: str = ""

    @property
    def evidence_strength_label(self) -> str:
        return EVIDENCE_STRENGTH_LABELS.get(
            self.evidence_strength, self.evidence_strength
        )


class ManualCollectionState(BaseModel):
    phase: str = "not_started"
    plan_path: str = ""
    plan_sha256: str = ""
    search_record_path: str = ""
    search_record_sha256: str = ""
    manifest_path: str = ""
    manifest_sha256: str = ""
    required_attempt_ids: list[str] = Field(default_factory=list)
    recorded_attempt_ids: list[str] = Field(default_factory=list)
    validated_attempt_ids: list[str] = Field(default_factory=list)
    imported_material_ids: list[str] = Field(default_factory=list)
    observed_result_count: int = 0
    zero_results_verified: bool = False
    last_updated: str = ""


class ScenarioStatus(BaseModel):
    scenario_id: str
    label_zh: str
    status: str = "not_started"
    material_count: int = 0
    failure_count: int = 0
    last_message: str = ""
    manual_collection: ManualCollectionState | None = None


class LiteratureRecord(BaseModel):
    material_id: str
    title: str
    pmid: str = ""
    pmcid: str = ""
    doi: str = ""
    journal: str = ""
    publication_date: str = ""
    source_database: str = ""
    fulltext_status: str = ""
    pdf_status: str = ""
    similar_article_count: int = 0
    duplicate_keys: list[str] = Field(default_factory=list)


class MetricFact(BaseModel):
    metric_fact_id: str = ""
    metric_type: str
    value: str
    comparator: str = ""
    cohort: str = ""
    sample_type: str = ""
    platform: str = ""
    reference_standard: str = ""
    excerpt: str
    evidence_card_id: str = ""
    material_id: str = ""
    source_location: str = ""


class EvidenceRelation(BaseModel):
    source_id: str
    target_id: str
    relation_type: str
    weight: float = 0.0
    evidence: str = ""


class SourceRun(BaseModel):
    source_run_id: str
    task_id: str = ""
    evidence_lane: str
    source_database: str
    query: str
    plugin_name: str = ""
    skill_name: str = ""
    status: str = "imported"
    imported_material_ids: list[str] = Field(default_factory=list)
    collection_time: str = ""


class SourceSite(BaseModel):
    source_site_id: str
    display_name: str
    source_category: str
    base_url: str
    search_url_template: str = ""
    detail_url_pattern: str = ""
    access_mode: str
    auth_required: bool = False
    query_fields: list[str] = Field(default_factory=list)
    capture_fields: list[str] = Field(default_factory=list)
    field_map: dict[str, str] = Field(default_factory=dict)
    adapter_id: str
    restriction_notes: str = ""
    source_origin: str = "诺研_skill 代码版本 V2.1 20260630 开发说明内置信源基线"


class RecommendedAction(BaseModel):
    action_id: str
    label_zh: str
    reason_zh: str


class TaskState(BaseModel):
    task_id: str
    topic: str
    task_dir: str
    created_at: str
    workflow_version: str
    taxonomy_version: str
    confirmations: dict[str, Any] = Field(default_factory=dict)
    scenario_statuses: dict[str, ScenarioStatus] = Field(default_factory=dict)
    research_policy: dict[str, Any] = Field(default_factory=dict)
