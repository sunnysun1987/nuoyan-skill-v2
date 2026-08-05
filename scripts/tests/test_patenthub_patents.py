import pytest

from ivd_research import browser_collect
from ivd_research.browser_collect import collect_patenthub_visible_results
from ivd_research.browser_workflows import classify_browser_page
from ivd_research.scenarios.patenthub_patents import (
    build_patenthub_material,
    collect,
    parse_patenthub_detail,
    parse_patenthub_result_list,
)


LOGIN_HTML = """
<html>
  <head><title>专利汇 - 用户登录</title></head>
  <body>
    <h1>用户登录</h1>
    <p>注册登录后可以查看更多专利信息，获取分析数据、下载专利。</p>
    <form><input name="password" type="password"></form>
    <a href="/patent/CN111344253A.html">返回原专利</a>
  </body>
</html>
"""


VALID_DETAIL_HTML = """
<html>
  <head><title>一种甲型流感病毒抗原检测试剂盒 - 专利汇</title></head>
  <body>
    <h1>一种甲型流感病毒抗原检测试剂盒</h1>
    <div>基本信息</div>
    <div>公开号：CN111344253A</div>
    <div>申请号：CN202010123456.7</div>
    <div>申请人：示例生物科技有限公司</div>
    <div>IPC分类号：G01N33/569</div>
    <div>摘要：本发明公开了一种甲型流感病毒抗原检测试剂盒。</div>
    <div>法律状态：有效专利</div>
  </body>
</html>
"""


def test_patenthub_login_page_does_not_produce_search_entries():
    entries = parse_patenthub_result_list(
        LOGIN_HTML,
        "https://www.patenthub.cn/s?ds=cn&q=CN111344253A",
    )

    assert entries == []


def test_patenthub_login_detail_is_marked_invalid():
    detail = parse_patenthub_detail(
        LOGIN_HTML,
        "https://www.patenthub.cn/patent/CN111344253A.html",
    )

    assert detail["page_status"] == "needs_login"
    assert detail["is_valid_patent_detail"] is False
    assert detail["title"] == ""


def test_patenthub_valid_detail_requires_real_patent_fields():
    detail = parse_patenthub_detail(
        VALID_DETAIL_HTML,
        "https://www.patenthub.cn/patent/CN111344253A.html",
    )

    assert detail["page_status"] == "page_ready"
    assert detail["is_valid_patent_detail"] is True
    assert detail["publication_number"] == "CN111344253A"
    assert detail["application_number"] == "CN202010123456.7"
    assert detail["applicant"] == "示例生物科技有限公司"


def test_patenthub_invalid_detail_cannot_become_material():
    detail = parse_patenthub_detail(
        LOGIN_HTML,
        "https://www.patenthub.cn/patent/CN111344253A.html",
    )

    with pytest.raises(ValueError, match="not a valid PatentHub patent detail"):
        build_patenthub_material(
            task_id="TASK-001",
            material_id="MAT-000001",
            query="CN111344253A",
            search_url="https://www.patenthub.cn/s?ds=cn&q=CN111344253A",
            search_snapshot="downloads/search.html",
            detail_snapshot="downloads/detail.html",
            entry={
                "title": "返回原专利",
                "detail_url": "https://www.patenthub.cn/patent/CN111344253A.html",
                "publication_number": "CN111344253A",
            },
            detail=detail,
        )


def test_patenthub_login_copy_is_classified_as_needs_login():
    result = classify_browser_page(
        "patenthub_patents",
        "https://www.patenthub.cn/s?ds=cn&q=test",
        "专利汇 - 用户登录",
        "注册登录后可以查看更多专利信息，获取分析数据、下载专利。",
    )

    assert result["status"] == "needs_login"


def test_patenthub_http_restriction_records_executable_browser_fallback(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "ivd_research.scenarios.site_collect.fetch_html",
        lambda url: (LOGIN_HTML, "https://www.patenthub.cn/login?reason=blocked", 200),
    )

    result = collect(
        task_id="TASK-001",
        task_dir=tmp_path,
        params={"query": "甲型流感", "material_id": "MAT-000001"},
    )

    assert result.status == "needs_login"
    assert "open-browser-session" in result.message_zh
    assert "重新运行 PatentHub 采集" in result.message_zh


def test_patenthub_transport_failure_records_retry_and_browser_fallback(
    monkeypatch, tmp_path
):
    def fail_fetch(url):
        raise OSError("DNS resolution failed")

    monkeypatch.setattr("ivd_research.scenarios.site_collect.fetch_html", fail_fetch)

    result = collect(
        task_id="TASK-001",
        task_dir=tmp_path,
        params={"query": "甲型流感", "material_id": "MAT-000001"},
    )

    assert result.status == "collection_failed"
    assert "重试" in result.message_zh
    assert "open-browser-session" in result.message_zh


def test_patenthub_detail_failure_is_not_reported_as_no_results():
    status, _ = browser_collect.patenthub_collection_outcome(
        entry_count=1,
        materials=[],
        collection_errors=[
            {
                "status": "collection_failed",
                "reason": "detail navigation timed out",
            }
        ],
    )

    assert status == "collection_failed"


def test_patenthub_collector_records_per_detail_navigation_failure(tmp_path):
    class BrokenPage:
        def goto(self, *args, **kwargs):
            raise RuntimeError("detail navigation timed out")

        def close(self):
            return None

    class Context:
        def new_page(self):
            return BrokenPage()

    search_html = """
    <html><body>
      <a class="patent-title" href="/patent/CN111344253A.html">甲型流感检测试剂盒</a>
    </body></html>
    """

    materials, _, errors = collect_patenthub_visible_results(
        task_dir=tmp_path,
        task_id="TASK-001",
        context=Context(),
        search_html=search_html,
        search_url="https://www.patenthub.cn/s?ds=cn&q=甲型流感",
        query="甲型流感",
        search_snapshot="downloads/search.html",
        page_limit=10,
        start_index=1,
    )

    assert materials == []
    assert errors[0]["status"] == "collection_failed"
    assert "timed out" in errors[0]["reason"]
