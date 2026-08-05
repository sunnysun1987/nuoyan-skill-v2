from ivd_research.source_adapters.source_sites import all_source_sites, source_site_map


def test_v21_source_sites_have_required_runtime_fields():
    sites = all_source_sites()

    assert len(sites) >= 15
    for site in sites:
        assert site.source_site_id
        assert site.display_name
        assert site.source_category
        assert site.base_url
        assert site.access_mode
        assert site.adapter_id
        assert site.capture_fields
        assert site.restriction_notes


def test_v21_source_sites_include_standard_literature_and_plugin_sources():
    site_map = source_site_map()

    for source_id in [
        "pubmed_literature",
        "pmc_fulltext",
        "openalex_literature",
        "life_science_research",
        "local_import",
        "zotero_optional",
    ]:
        assert source_id in site_map

    assert site_map["life_science_research"].access_mode == "plugin"
    assert site_map["pubmed_literature"].access_mode == "api"


def test_nmpa_standard_source_is_manual_assisted():
    site_map = source_site_map()

    assert site_map["nmpa_competitor"].access_mode == "manual_assisted"
    assert "截图" in site_map["nmpa_competitor"].restriction_notes
