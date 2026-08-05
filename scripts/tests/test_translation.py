from pathlib import Path

from ivd_research.jsonl import append_jsonl
from ivd_research.translation import (
    TranslationEngine,
    is_mostly_english,
    setup_translation_engine,
    text_hash,
    translate_sections,
    translation_status,
)


TRANSLATION_ENV_KEYS = [
    "NUOYAN_TRANSLATION_PROVIDER",
    "NUOYAN_TRANSLATION_API_KEY",
    "OPENAI_API_KEY",
    "NUOYAN_TRANSLATION_BASE_URL",
    "NUOYAN_TRANSLATION_MODEL",
    "NUOYAN_LIBRETRANSLATE_URL",
    "NUOYAN_LIBRETRANSLATE_API_KEY",
]


def test_translate_sections_requires_real_engine_when_not_cached(tmp_path: Path, monkeypatch):
    for key in TRANSLATION_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    engine = TranslationEngine(provider="argos")
    rows = translate_sections(
        [{"label": "Results", "text": "The assay showed AUC 0.92 for Alzheimer disease pathology."}],
        task_dir=tmp_path,
        material_id="MAT-000001",
        engine=engine,
    )

    assert rows[0]["translation_zh"] == ""
    assert rows[0]["translation_status"] == "engine_not_ready"
    assert "未生成完整中文翻译" in rows[0]["translation_error"]


def test_translate_sections_uses_cached_complete_translation(tmp_path: Path):
    text = "First sentence. Second sentence. Third sentence. Fourth sentence."
    append_jsonl(
        tmp_path / "data" / "translations.jsonl",
        {
            "material_id": "MAT-000001",
            "field": "abstract:1:Results",
            "text_hash": text_hash(text),
            "translation_zh": "第一句。第二句。第三句。第四句。",
            "status": "completed",
            "engine": "test",
        },
    )

    rows = translate_sections(
        [{"label": "Results", "text": text}],
        task_dir=tmp_path,
        material_id="MAT-000001",
    )

    assert rows[0]["translation_zh"] == "第一句。第二句。第三句。第四句。"
    assert rows[0]["translation_status"] == "completed"


def test_title_translation_cache_uses_title_field(tmp_path: Path):
    title = "Clinical evaluation of a multiplex respiratory viral assay."
    append_jsonl(
        tmp_path / "data" / "translations.jsonl",
        {
            "material_id": "MAT-000003",
            "field": "title",
            "text_hash": text_hash(title),
            "translation_zh": "多重呼吸道病毒检测方法的临床评价。",
            "status": "completed",
            "engine": "test",
        },
    )

    from ivd_research.reports import _cached_translation_text

    cache = {
        (
            row["material_id"],
            row["field"],
            row["text_hash"],
        ): row
        for row in [
            {
                "material_id": "MAT-000003",
                "field": "title",
                "text_hash": text_hash(title),
                "translation_zh": "多重呼吸道病毒检测方法的临床评价。",
            }
        ]
    }

    assert (
        _cached_translation_text(cache, material_id="MAT-000003", field="title", text=title)
        == "多重呼吸道病毒检测方法的临床评价。"
    )


def test_excerpt_reading_blocks_split_source_metadata_and_abstract():
    from ivd_research.reports import _excerpt_reading_blocks, _paragraphize_reading_text

    text = (
        "来源：OpenAlex 检索式：respiratory influenza A influenza B "
        "标题：Clinical and virological impact 作者：Jiazhen Zheng "
        "期刊/来源：PLOS neglected tropical diseases "
        "Abstract: Severe acute respiratory syndrome coronavirus 2 mimics influenza A. "
        "Comprehensive data for adult patients were extracted. "
        "All participants were tested at admission."
    )

    blocks = _excerpt_reading_blocks(text)

    assert [block["label"] for block in blocks[:5]] == ["来源", "检索式", "题名", "作者", "期刊/来源"]
    assert blocks[-1]["label"] == "Abstract"
    assert len(blocks[-1]["paragraphs"]) >= 1
    assert len(_paragraphize_reading_text("第一句。第二句。第三句。", max_chars=6)) >= 2


def test_translate_sections_skips_chinese_source_text(tmp_path: Path):
    rows = translate_sections(
        [{"label": "摘要", "text": "这是一段中文摘要，已经可以直接阅读，不需要再生成中文阅读版。"}],
        task_dir=tmp_path,
        material_id="MAT-000002",
    )

    assert rows[0]["translation_zh"] == ""
    assert rows[0]["translation_status"] == "source_is_chinese"


def test_translation_status_reports_builtin_command_and_missing_engine(tmp_path: Path, monkeypatch):
    for key in TRANSLATION_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NUOYAN_TRANSLATION_PROVIDER", "argos")
    monkeypatch.setattr(TranslationEngine, "argos_installed", lambda self: False)
    monkeypatch.setattr(TranslationEngine, "argos_ready", lambda self: False)
    append_jsonl(
        tmp_path / "data" / "materials.jsonl",
        {
            "material_id": "MAT-000001",
            "raw_fields": {
                "abstract_sections": [
                    {
                        "label": "Background",
                        "text": "Influenza A and influenza B infections require rapid diagnostic testing in clinical settings.",
                    }
                ]
            },
        },
    )

    status = translation_status(tmp_path)

    assert status["command_available"] is True
    assert status["configured"] is False
    assert status["status"] == "not_configured"
    assert status["active_provider"] == ""
    assert status["english_section_count"] == 1


def test_translation_status_accepts_libretranslate_intranet_route(tmp_path: Path, monkeypatch):
    for key in TRANSLATION_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NUOYAN_TRANSLATION_PROVIDER", "libretranslate")
    monkeypatch.setenv("NUOYAN_LIBRETRANSLATE_URL", "http://translate.intranet.local")

    status = translation_status(tmp_path)

    assert status["configured"] is True
    assert status["active_provider"] == "libretranslate"
    assert status["libretranslate_configured"] is True


def test_auto_translation_does_not_use_cloud_key_without_explicit_provider(monkeypatch):
    for key in TRANSLATION_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "personal-key-must-not-be-used-implicitly")
    engine = TranslationEngine()
    monkeypatch.setattr(engine, "argos_ready", lambda: False)
    monkeypatch.setattr(engine, "libretranslate_ready", lambda: False)

    assert engine.active_provider() == ""

    explicit_engine = TranslationEngine(provider="openai")
    monkeypatch.setattr(explicit_engine, "argos_ready", lambda: False)
    monkeypatch.setattr(explicit_engine, "libretranslate_ready", lambda: False)
    assert explicit_engine.active_provider() == "openai"


def test_setup_translation_engine_can_skip_model_download_when_package_installed(
    monkeypatch,
):
    monkeypatch.setattr(
        "ivd_research.translation.importlib.util.find_spec",
        lambda _name: object(),
    )
    monkeypatch.setattr(TranslationEngine, "argos_installed", lambda _self: True)
    monkeypatch.setattr(TranslationEngine, "argos_ready", lambda _self: False)

    def forbid_subprocess(*_args, **_kwargs):
        raise AssertionError("install_model=False must not start a model installer")

    monkeypatch.setattr("ivd_research.translation.subprocess.run", forbid_subprocess)

    result = setup_translation_engine(provider="argos", install_model=False)

    assert result["provider"] == "argos"
    assert result["status"] == "package_installed_model_missing"
    assert result["commands"] == []
    assert "next_step_zh" in result


def test_short_english_title_can_use_title_threshold():
    assert is_mostly_english("AD biomarker panel", min_ascii_letters=8)
