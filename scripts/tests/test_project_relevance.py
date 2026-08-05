from ivd_research.project_relevance import assess_material_relevance
from ivd_research.reports import (
    build_excluded_material_rows,
    build_screening_cards,
    build_section_evidence_rows,
    normalize_materials,
)


PROFILES = {
    "hcg": {
        "primary_query": "beta-hCG 定量检测试剂盒",
        "chinese_synonyms": "β-hCG；人绒毛膜促性腺激素；hCG",
        "english_keywords": "human chorionic gonadotropin pregnancy ectopic",
        "intended_use": "妊娠相关检测和异位妊娠辅助评估",
    },
    "ad": {
        "primary_query": "AD p-Tau217 血液标志物",
        "chinese_synonyms": "p-Tau217；阿尔茨海默病；AD",
        "english_keywords": "Alzheimer p-tau217 amyloid cognitive",
        "intended_use": "认知障碍辅助诊断",
    },
    "respiratory": {
        "primary_query": "甲乙流呼吸道多重联检",
        "chinese_synonyms": "甲型流感；乙型流感；甲乙流",
        "english_keywords": "influenza respiratory multiplex virus",
        "intended_use": "呼吸道病原体鉴别诊断",
    },
    "pct": {
        "primary_query": "降钙素原 PCT 定量检测试剂盒",
        "chinese_synonyms": "降钙素原；PCT",
        "english_keywords": "procalcitonin sepsis bacterial infection",
        "intended_use": "细菌感染和脓毒症风险辅助评估",
    },
    "troponin": {
        "primary_query": "心肌肌钙蛋白 I 检测试剂盒",
        "chinese_synonyms": "心肌肌钙蛋白I；cTnI；肌钙蛋白I",
        "english_keywords": "cardiac troponin cTnI myocardial infarction",
        "intended_use": "心肌损伤和心肌梗死辅助诊断",
    },
}


MATERIALS = [
    {
        "material_id": "MAT-HCG",
        "material_type": "literature",
        "title": "Serum beta-hCG for ectopic pregnancy assessment",
        "raw_fields": {"abstract": "Human chorionic gonadotropin in pregnancy."},
    },
    {
        "material_id": "MAT-AD",
        "material_type": "literature",
        "title": "Plasma p-Tau217 for Alzheimer disease pathology",
        "raw_fields": {"abstract": "Amyloid PET and cognitive impairment."},
    },
    {
        "material_id": "MAT-RESP",
        "material_type": "literature",
        "title": "Multiplex influenza A and B respiratory virus assay",
        "raw_fields": {"abstract": "Respiratory pathogen detection."},
    },
    {
        "material_id": "MAT-PCT",
        "material_type": "literature",
        "title": "Procalcitonin PCT immunoassay for bacterial sepsis",
        "raw_fields": {"abstract": "Sepsis risk assessment."},
    },
    {
        "material_id": "MAT-CTNI",
        "material_type": "literature",
        "title": "Cardiac troponin I cTnI assay for myocardial infarction",
        "raw_fields": {"abstract": "Cardiac injury diagnosis."},
    },
]


def test_cross_project_relevance_matrix_covers_all_completed_test_profiles():
    expected = {
        "hcg": "MAT-HCG",
        "ad": "MAT-AD",
        "respiratory": "MAT-RESP",
        "pct": "MAT-PCT",
        "troponin": "MAT-CTNI",
    }

    for profile_id, confirmations in PROFILES.items():
        rows = normalize_materials(MATERIALS, [], confirmations=confirmations)
        included = {
            row["material_id"] for row in rows if row.get("project_relevant")
        }
        assert included == {expected[profile_id]}


def test_incidental_target_mention_without_project_context_is_excluded():
    material = {
        "material_id": "MAT-COMPARATOR",
        "material_type": "literature",
        "title": "Carbon biosensors for Alzheimer biomarkers",
        "raw_fields": {
            "abstract": "A generic methods table mentions hCG only as an unrelated comparator."
        },
    }

    result = assess_material_relevance(material, PROFILES["hcg"])

    assert result["relevant"] is False
    assert result["reason"] == "no_project_signal_in_material"


def test_incomplete_profile_does_not_silently_exclude_every_material():
    result = assess_material_relevance(
        MATERIALS[0],
        {
            "sample_type": "血清",
            "platform": "化学发光",
            "patent_scope": "中国",
        },
    )

    assert result["relevant"] is True
    assert result["reason"] == "insufficient_relevance_profile"


def test_human_project_excludes_animal_only_standard():
    material = {
        "material_id": "MAT-ANIMAL-STANDARD",
        "source_scenario": "standards_current",
        "material_type": "standard",
        "title": "DB37/T 4044-2020 禽腺病毒4型荧光定量PCR检测方法",
        "raw_fields": {
            "standard_name": "禽腺病毒4型荧光定量PCR检测方法",
            "trade": "畜牧业",
        },
    }
    confirmations = {
        "primary_query": "人腺病毒呼吸道感染核酸检测试剂盒",
        "chinese_synonyms": "人腺病毒；腺病毒；HAdV",
        "english_keywords": "human adenovirus respiratory infection",
        "intended_use": "用于有呼吸道感染症状患者的辅助诊断",
    }

    result = assess_material_relevance(material, confirmations)

    assert result["relevant"] is False
    assert result["reason"] == "animal_only_standard_for_human_project"


def test_analysis_evidence_rows_exclude_cross_project_draft_cards():
    materials = normalize_materials(
        [MATERIALS[0], MATERIALS[1]],
        [],
        confirmations=PROFILES["hcg"],
    )
    cards = [
        {
            "evidence_card_id": "EC-HCG",
            "material_id": "MAT-HCG",
            "title": MATERIALS[0]["title"],
            "summary": "Pregnancy testing evidence.",
            "include_in_report": False,
        },
        {
            "evidence_card_id": "EC-AD",
            "material_id": "MAT-AD",
            "title": MATERIALS[1]["title"],
            "summary": "Alzheimer biomarker evidence.",
            "include_in_report": False,
        },
    ]
    screening_cards = build_screening_cards(
        materials,
        cards,
        confirmations=PROFILES["hcg"],
    )

    rows = build_section_evidence_rows(
        "其他发现 / 待归类线索",
        materials=materials,
        screening_cards=screening_cards,
    )

    assert {row["evidence_card_id"] for row in rows} == {"EC-HCG"}


def test_excluded_materials_remain_visible_for_audit():
    materials = normalize_materials(
        [MATERIALS[0], MATERIALS[1]],
        [],
        confirmations=PROFILES["hcg"],
    )

    rows = build_excluded_material_rows(materials)

    assert len(rows) == 1
    assert rows[0]["material_id"] == "MAT-AD"
    assert rows[0]["reason"] == "未匹配到当前项目的目标物或用途信号"
