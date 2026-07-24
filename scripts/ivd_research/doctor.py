import importlib.util
import os
import re
import shutil
import socket
import subprocess
import sys
import tomllib
import uuid
from importlib import metadata
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from .constants import WORKFLOW_VERSION


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def dependency_check(
    check_id: str,
    label_zh: str,
    module_name: str,
    impact_zh: str,
    required: bool,
) -> dict:
    return {
        "id": check_id,
        "label_zh": label_zh,
        "ok": module_available(module_name),
        "required": required,
        "impact_zh": impact_zh,
    }


def _codex_home(path: Path | None = None) -> Path:
    if path is not None:
        return path.expanduser().resolve()
    configured = os.environ.get("CODEX_HOME", "").strip()
    return Path(configured).expanduser().resolve() if configured else Path.home() / ".codex"


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def codex_plugin_check(
    plugin_id: str,
    label_zh: str,
    *,
    codex_home: Path | None = None,
) -> dict:
    home = _codex_home(codex_home)
    config_path = home / "config.toml"
    config = _read_toml(config_path)
    enabled = config.get("plugins", {}).get(plugin_id, {}).get("enabled") is True
    plugin_name, _, provider = plugin_id.partition("@")
    cache_root = home / "plugins" / "cache" / provider / plugin_name
    cached_manifests = sorted(cache_root.glob("*/.codex-plugin/plugin.json"))
    cached = bool(cached_manifests)
    ok = enabled and cached
    if ok:
        impact_zh = f"{label_zh} 插件已启用。"
    elif enabled:
        impact_zh = f"{label_zh} 配置标记为启用，但本地插件包不存在；需在 Codex 插件管理中重新安装。"
    elif cached:
        impact_zh = f"{label_zh} 插件已缓存但未启用；需在 Codex 插件管理中启用并重启应用。"
    else:
        impact_zh = f"未安装或未启用 {label_zh} 插件；需在 Codex 插件管理中安装、启用并重启应用。"
    check_ids = {
        "life-science-research": "life_science_plugin",
        "browser": "browser_plugin",
        "chrome": "chrome_plugin",
    }
    return {
        "id": check_ids.get(plugin_name, plugin_name.replace("-", "_") + "_plugin"),
        "label_zh": f"Codex {label_zh} 插件",
        "ok": ok,
        "required": True,
        "impact_zh": impact_zh,
        "details": {
            "plugin_id": plugin_id,
            "enabled": enabled,
            "cached": cached,
            "config_path": str(config_path),
            "cached_manifests": [str(path) for path in cached_manifests],
        },
    }


def _skill_install_check(home: Path) -> dict:
    skill_root = home / "skills" / "nuoyan-skill-v2"
    skill_file = skill_root / "SKILL.md"
    installed = skill_file.is_file()
    name_matches = False
    if installed:
        try:
            head = skill_file.read_text(encoding="utf-8")[:1000]
            name_matches = "name: nuoyan-skill-v2" in head
        except OSError:
            pass
    return {
        "id": "skill_install",
        "label_zh": "诺研 Skill 标准安装目录",
        "ok": installed and name_matches,
        "required": True,
        "impact_zh": (
            "诺研 Skill 已安装到标准目录。"
            if installed and name_matches
            else f"标准安装无效：{skill_root} 中缺少可识别的 SKILL.md。"
        ),
        "details": {
            "skill_root": str(skill_root),
            "skill_file_exists": installed,
            "skill_name_matches": name_matches,
        },
    }


def _runtime_source_check(home: Path) -> dict:
    source_root = Path(__file__).resolve().parents[2]
    expected_root = home / "skills" / "nuoyan-skill-v2"
    project = _read_toml(source_root / "pyproject.toml")
    declared_version = str(project.get("project", {}).get("version") or "")
    try:
        distribution_version = metadata.version("nuoyan-skill-v2")
    except metadata.PackageNotFoundError:
        distribution_version = ""
    workflow_version_match = re.search(r"(?:^|-)v(\d+\.\d+\.\d+)(?:-|$)", WORKFLOW_VERSION)
    workflow_semantic_version = workflow_version_match.group(1) if workflow_version_match else ""
    same_source = False
    if source_root.exists() and expected_root.exists():
        try:
            same_source = source_root.samefile(expected_root)
        except OSError:
            same_source = source_root.resolve() == expected_root.resolve()
    version_matches = bool(
        declared_version
        and distribution_version == declared_version
        and workflow_semantic_version == declared_version
    )
    ok = same_source and version_matches
    return {
        "id": "runtime_source",
        "label_zh": "实际运行代码路径与版本",
        "ok": ok,
        "required": True,
        "impact_zh": (
            "当前命令加载的是标准目录中的最新一致版本。"
            if ok
            else "当前命令加载路径、安装包版本或工作流版本不一致，可能仍在调用旧版代码。"
        ),
        "details": {
            "runtime_source_root": str(source_root),
            "expected_skill_root": str(expected_root),
            "same_source": same_source,
            "distribution_version": distribution_version,
            "declared_version": declared_version,
            "workflow_version": WORKFLOW_VERSION,
            "workflow_semantic_version": workflow_semantic_version,
            "version_matches": version_matches,
            "python_executable": sys.executable,
        },
    }


def _distribution_conflict_check() -> dict:
    providers = sorted(set(metadata.packages_distributions().get("ivd_research", [])))
    allowed = {"nuoyan-skill-v2"}
    conflicts = [name for name in providers if name.lower() not in allowed]
    ok = not conflicts
    return {
        "id": "distribution_conflict",
        "label_zh": "旧版 Python 包冲突",
        "ok": ok,
        "required": True,
        "impact_zh": (
            "未发现占用 ivd_research 命名空间的旧版安装包。"
            if ok
            else f"发现可能覆盖新版代码的旧包：{', '.join(conflicts)}；应从诺研专用虚拟环境中移除。"
        ),
        "details": {"providers": providers, "conflicts": conflicts},
    }


def _playwright_browser_check() -> dict:
    executable = ""
    error = ""
    ok = False
    if module_available("playwright"):
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                executable = playwright.chromium.executable_path
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page()
                page.set_content("<title>nuoyan-doctor</title>")
                ok = page.title() == "nuoyan-doctor"
                browser.close()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:1000]
    return {
        "id": "playwright_browser",
        "label_zh": "Playwright Chromium 浏览器",
        "ok": ok,
        "required": True,
        "impact_zh": (
            "Playwright Chromium 可启动并完成页面探测。"
            if ok
            else "Playwright Python 包或 Chromium 浏览器不可用，动态网页采集无法执行。"
        ),
        "details": {"executable": executable, "error": error},
    }


def _pdf_toolchain_check() -> dict:
    modules = {name: module_available(name) for name in ("pypdf", "pdfplumber")}
    ok = all(modules.values())
    return {
        "id": "pdf_toolchain",
        "label_zh": "PDF 文本提取工具链",
        "ok": ok,
        "required": True,
        "impact_zh": (
            "PDF 文本提取依赖完整。"
            if ok
            else "缺少 pypdf 或 pdfplumber，文献 PDF 全文提取不完整。"
        ),
        "details": modules,
    }


def _translation_engine_check() -> dict:
    installed = module_available("argostranslate")
    ready = False
    error = ""
    if installed:
        try:
            from .translation import TranslationEngine

            ready = TranslationEngine(provider="argos").argos_ready()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:1000]
    return {
        "id": "translation_engine",
        "label_zh": "Argos 英中翻译模型",
        "ok": ready,
        "required": True,
        "impact_zh": (
            "Argos English→Chinese 模型已安装。"
            if ready
            else "Argos Python 包或 English→Chinese 模型未安装，英文证据不能稳定生成中文阅读版。"
        ),
        "details": {"package_installed": installed, "model_ready": ready, "error": error},
    }


def _ocr_runtime_check() -> dict:
    module_ok = module_available("pytesseract")
    executable = shutil.which("tesseract") or ""
    ok = module_ok and bool(executable)
    return {
        "id": "ocr_runtime",
        "label_zh": "可选 OCR 运行时",
        "ok": ok,
        "required": False,
        "impact_zh": (
            "Tesseract OCR 可用。"
            if ok
            else "OCR 为可选能力；扫描版 PDF 将标记为需要人工处理或由 IT 补装 Tesseract。"
        ),
        "details": {"python_module": module_ok, "executable": executable},
    }


NETWORK_PROBES = [
    {
        "id": "ncbi_eutils",
        "label_zh": "NCBI E-utilities / PubMed",
        "host": "eutils.ncbi.nlm.nih.gov",
        "url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi?db=pubmed",
    },
    {
        "id": "openalex",
        "label_zh": "OpenAlex",
        "host": "api.openalex.org",
        "url": "https://api.openalex.org/works?search=immunoassay%20biomarker&per-page=1",
    },
]


def run_network_doctor(timeout: int = 10) -> dict:
    probes = [_network_probe(probe, timeout=timeout) for probe in NETWORK_PROBES]
    return {
        "ok": all(probe["python_https_ok"] or probe["curl_https_ok"] for probe in probes),
        "message_zh": (
            "网络体检用于区分 Python DNS、Python HTTPS 与系统 curl 通道。"
            "若 Python DNS 失败但 curl 成功，正式采集应使用 curl/浏览器/人工导入兜底，"
            "并在交付验证中保留该网络状态。"
        ),
        "probes": probes,
    }


def _network_probe(probe: dict, *, timeout: int) -> dict:
    host = probe["host"]
    url = probe["url"]
    result = {
        "id": probe["id"],
        "label_zh": probe["label_zh"],
        "host": host,
        "url": url,
        "python_dns_ok": False,
        "python_dns_error": "",
        "python_https_ok": False,
        "python_https_status": None,
        "python_https_error": "",
        "curl_https_ok": False,
        "curl_https_error": "",
    }
    try:
        result["python_dns_address"] = socket.getaddrinfo(host, 443)[0][4][0]
        result["python_dns_ok"] = True
    except OSError as exc:
        result["python_dns_error"] = f"{type(exc).__name__}: {exc}"

    try:
        request = Request(url, headers={"User-Agent": "NuoYan-Skill/2.0 network doctor"})
        with urlopen(request, timeout=timeout) as response:
            result["python_https_status"] = response.status
            result["python_https_ok"] = 200 <= response.status < 400
    except (OSError, URLError) as exc:
        result["python_https_error"] = f"{type(exc).__name__}: {exc}"

    try:
        completed = subprocess.run(
            [
                "curl",
                "-fsSL",
                "--max-time",
                str(timeout),
                "-A",
                "NuoYan-Skill/2.0 network doctor",
                url,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout + 5,
        )
        result["curl_https_ok"] = completed.returncode == 0
        if completed.returncode != 0:
            result["curl_https_error"] = (
                completed.stderr or completed.stdout or f"curl exit {completed.returncode}"
            ).strip()[:500]
        else:
            result["curl_response_preview"] = completed.stdout[:200]
    except (OSError, subprocess.SubprocessError) as exc:
        result["curl_https_error"] = f"{type(exc).__name__}: {exc}"
    return result


def run_doctor(
    output_root: Path,
    *,
    include_network: bool = False,
    profile: str = "core",
    codex_home: Path | None = None,
) -> dict:
    profile = str(profile or "core").strip().lower()
    if profile not in {"core", "standard"}:
        raise ValueError("profile must be 'core' or 'standard'")
    checks = [
        {
            "id": "python",
            "label_zh": "Python 版本",
            "ok": sys.version_info >= (3, 11),
            "required": True,
            "impact_zh": "Python 低于 3.11 会导致 CLI 不受支持。",
        },
        dependency_check(
            "typer",
            "CLI 框架",
            "typer",
            "缺少 Typer 会导致 nuoyan 命令无法运行。",
            True,
        ),
        dependency_check(
            "pydantic",
            "Schema 校验",
            "pydantic",
            "缺少 Pydantic 会导致材料和证据卡无法校验。",
            True,
        ),
        dependency_check(
            "openpyxl",
            "Excel 读写",
            "openpyxl",
            "缺少 openpyxl 会导致 Excel 复核表无法导出或导入。",
            True,
        ),
        dependency_check(
            "jinja2",
            "HTML 渲染",
            "jinja2",
            "缺少 Jinja2 会导致 HTML 报告无法生成。",
            True,
        ),
        dependency_check(
            "httpx",
            "HTTP 采集",
            "httpx",
            "缺少 httpx 会导致网页和文件下载能力不可用。",
            True,
        ),
        dependency_check(
            "beautifulsoup4",
            "HTML 解析",
            "bs4",
            "缺少 BeautifulSoup 会影响网页内容解析。",
            False,
        ),
        dependency_check(
            "playwright",
            "浏览器采集能力",
            "playwright",
            "缺少 Playwright 会影响需要 JavaScript 或登录态的网站采集，但不影响本地导入。",
            False,
        ),
        dependency_check(
            "argostranslate",
            "离线专业中文翻译",
            "argostranslate",
            "缺少 Argos Translate 时，英文材料中文阅读版需要改用企业内网 LibreTranslate 或管理员统一模型网关。",
            False,
        ),
        dependency_check(
            "pypdf",
            "PDF 文本提取",
            "pypdf",
            "缺少 PDF 解析依赖会导致部分文献全文无法自动提取，但不影响材料登记。",
            False,
        ),
        dependency_check(
            "ocr",
            "可选 OCR",
            "pytesseract",
            "缺少 OCR 仅影响扫描件全文识别，材料会标记为需要 OCR。",
            False,
        ),
    ]

    try:
        output_root.mkdir(parents=True, exist_ok=True)
        probe = output_root / f".nuoyan-write-test-{uuid.uuid4().hex}"
        probe.write_text("ok", encoding="utf-8")
        writable = True
        impact_zh = "输出目录可写。"
    except OSError as exc:
        writable = False
        impact_zh = f"输出目录不可写，会导致任务无法创建：{exc}"
    finally:
        if "probe" in locals():
            probe.unlink(missing_ok=True)

    checks.append(
        {
            "id": "output_root",
            "label_zh": "输出目录权限",
            "ok": writable,
            "required": True,
            "impact_zh": impact_zh,
        }
    )

    core_ready = all(item["ok"] for item in checks if item["required"])
    network = None
    standard_ready = None
    if profile == "standard":
        home = _codex_home(codex_home)
        standard_checks = [
            _runtime_source_check(home),
            _distribution_conflict_check(),
            _skill_install_check(home),
            _playwright_browser_check(),
            _pdf_toolchain_check(),
            _translation_engine_check(),
            codex_plugin_check(
                "life-science-research@openai-api-curated",
                "Life Science Research",
                codex_home=home,
            ),
            codex_plugin_check(
                "browser@openai-bundled",
                "Browser",
                codex_home=home,
            ),
            codex_plugin_check(
                "chrome@openai-bundled",
                "Chrome",
                codex_home=home,
            ),
            _ocr_runtime_check(),
        ]
        if include_network:
            network = run_network_doctor(timeout=8)
            network_check = {
                "id": "network_preflight",
                "label_zh": "公网采集通道预检",
                "ok": network["ok"],
                "required": True,
                "impact_zh": (
                    "PubMed/OpenAlex 公网通道至少有一种可用采集方式。"
                    if network["ok"]
                    else "PubMed/OpenAlex 公网通道不可用，正式调研需修复网络或采用浏览器/人工导入兜底。"
                ),
            }
        else:
            network_check = {
                "id": "network_preflight",
                "label_zh": "公网采集通道预检",
                "ok": False,
                "required": True,
                "impact_zh": "标准调研环境必须运行 --network，确认 PubMed/OpenAlex 公网采集通道。",
            }
        standard_checks.append(network_check)
        checks.extend(standard_checks)
        standard_ready = core_ready and all(
            item["ok"] for item in standard_checks if item["required"]
        )

    result = {
        "ok": standard_ready if profile == "standard" else core_ready,
        "profile": profile,
        "core_ready": core_ready,
        "standard_ready": standard_ready,
        "output_root": str(output_root),
        "checks": checks,
    }
    if include_network and network is None:
        network = run_network_doctor(timeout=8)
        result["ok"] = core_ready and network["ok"]
    if network is not None:
        result["network"] = network
    return result
