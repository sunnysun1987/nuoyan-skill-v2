# 诺研_skill_IVD研发项目调研

`nuoyan-skill-v2` is a Codex skill for IVD R&D project research. It helps an agent confirm search scope, collect regulatory, competitor, standards, patent, literature and external scientific database evidence, generate enhanced evidence cards, build an HTML research analysis report, export an Excel evidence review table, and preserve local knowledge assets.

Skill name policy: the Codex loading ID is fixed as `nuoyan-skill-v2`. Keep it lowercase, ASCII-only and hyphenated so it matches the installed folder name. The Chinese display title remains `诺研_skill_IVD研发项目调研`.

## Install

Clone this repository into a Codex skills directory, then restart Codex so the skill can be discovered.

```bash
mkdir -p ~/.codex/skills
git clone git@github.com:sunnysun1987/NuoYan_Skill.git ~/.codex/skills/nuoyan-skill-v2
```

If an earlier install exists but Codex does not load it, check the first lines of `~/.codex/skills/nuoyan-skill-v2/SKILL.md`. The frontmatter must include `name: nuoyan-skill-v2`; older packages that use `name: 诺研_skill` should be updated with `git pull` or reinstalled.

For local CLI use, install the Python package in editable mode:

```bash
cd ~/.codex/skills/nuoyan-skill-v2
python3 -m pip install -e ".[dev]"
```

Optional capabilities:

```bash
python3 -m pip install -e ".[browser,pdf,ocr,dev]"
```

### Windows company workstations

Windows business workstations should not reuse an arbitrary system Python environment. IT or Codex should run the repository's `install-windows.ps1`, which installs the skill at `%USERPROFILE%\.codex\skills\nuoyan-skill-v2`, creates a private `.venv`, installs the browser/PDF/translation components, installs Chromium, checks the Argos English-to-Chinese model, and runs the strict standard-environment doctor.

Business users do not run shell commands. Give Codex the copyable Chinese request and follow the app-plugin steps in [Windows 标准调研环境安装与使用](docs/windows-standard-environment.md).

## Quick Check

Run a dependency and output-path check:

```bash
nuoyan doctor --json
```

Before formal public-source collection, run the network preflight:

```bash
nuoyan doctor --network --json
```

For a formal research workstation, use the standard profile and strict exit code:

```bash
nuoyan doctor --profile standard --network --strict --json
```

This profile checks the actual runtime source and version, legacy distribution conflicts, Playwright Chromium startup, PDF tools, the Argos English-to-Chinese model, enabled Codex Life Science Research/Browser/Chrome plugins, and public-source connectivity. A cached plugin is not treated as enabled.

The network preflight separates Python DNS, Python HTTPS and system curl status for PubMed/NCBI and OpenAlex. DNS or proxy failures must be treated as collection failures, not as evidence absence.

## Network And Proxy

Enterprise networks, VPNs, proxies, DNS filtering and sandboxed agent runtimes can block PubMed/NCBI, PMC or OpenAlex. If `doctor --network` reports `gaierror`, `Could not resolve host`, `No DNS configuration available` or timeout errors, configure the host environment before rerunning collection.

Common proxy setup:

```bash
export HTTPS_PROXY=http://proxy.example.com:8080
export HTTP_PROXY=http://proxy.example.com:8080
export NO_PROXY=localhost,127.0.0.1
```

Recommended allowlist:

- `eutils.ncbi.nlm.nih.gov`
- `pubmed.ncbi.nlm.nih.gov`
- `pmc.ncbi.nlm.nih.gov`
- `api.openalex.org`
- `www.nmpa.gov.cn`
- `www.cmde.org.cn`
- `std.samr.gov.cn`

If public APIs remain blocked, use the skill's fallback path: record the true failure, collect from legal public pages or user-provided files, import findings with `import-finding`, and keep unresolved items in the Excel review and supplement task table. NMPA competitor registration is an exception: its formal source closure uses the dedicated human-assisted workflow below; a generic finding is only a clue.

## Workflow Contract

The first step is always search-scope confirmation. A formal research run must confirm product type, target analyte, disease or intended use, sample type, platform or methodology, target region, competitor scope, literature range, patent scope and report depth before collection.

Formal collection should cover the evidence map:

- regulatory and review guidance
- NMPA competitor registrations
- current standards
- patents
- Chinese literature
- PubMed, PMC and OpenAlex literature
- fallback or manually imported evidence when a source is restricted
- life-science-research plugin evidence when the topic involves biomarkers, proteins, genes, pathways, clinical studies, genetic evidence or public scientific databases

Literature retrieval depth is controlled by `literature_profile`, not by an ad-hoc hidden cap. Standard research uses `complete_literature`, which keeps PubMed, PMC and OpenAlex at 200 records per source by default. `quick_scan` is the lightweight profile for roughly 50 records per source. A stale low `literature_retmax` confirmation cannot silently downgrade complete profiles; use `quick_scan` for a short scan, or use `all` only after explicit risk confirmation.

V2.1 adds a standard source-site baseline and lightweight local knowledge assets:

- `nuoyan source-sites --json` exports built-in source configuration.
- `nuoyan life-science-plan --task-id <task_id> --json` writes the required external plugin query plan when a topic involves biomarkers, proteins, genes, pathways, clinical studies, genetic evidence or public scientific databases.
- `nuoyan import-life-science-findings --task-id <task_id> --findings-json-file external_findings.json --json` imports plugin findings into the material pipeline.
- `nuoyan import-literature-table --task-id <task_id> --path literature.xlsx --json` imports local CSV/XLSX literature lists.
- `nuoyan build-knowledge --task-id <task_id> --json` generates metric facts, topic index, dedup index and a literature graph.
- `nuoyan source-quality --task-id <task_id> --json` audits no-result sources for possible false negatives, including single-query no-results, missing core-query attempts, overconstrained long queries and cross-source contradictions such as OpenAlex no-results while PubMed/PMC/LSR already has related literature.

V2.2.2 keeps validated 17-section project analysis inside the standard six-tab delivery report. It adds bad-case regression coverage for resumable scenario state, keeps all evidence cards auditable with relevance labels, separates artifact delivery from business readiness, and records executable fallback actions for restricted sources. Release CI, strict doctor health, source compilation and patch checks remain mandatory.

V2.2.0 adds a claim-level research integrity layer on top of materials, evidence cards and metric facts:

- Search results and snippets are discovery leads. `import-finding --source web_search` records them as `retrieval_kind=search_result` and `content_verified=false` by default; they cannot support a research claim until the underlying page or document has been fetched and inspected.
- `ResearchClaim` records claim type, support status, confidence, impact, inference and links to evidence cards or metric facts. High-impact supported claims require independent publishers; consensus claims require at least three.
- `EvidenceConflict` preserves contradictory data, methods, regulatory status or source conclusions until an expert resolves or accepts the difference.
- `ResearchIteration` records new verified materials, publishers, claims, changed claims and resolved gaps. Two zero-yield audits from different structural directions are required before research is considered saturated; repeating the same direction is rejected.
- `ResearchPolicy` classifies task data as public, internal or confidential. Internal or confidential content cannot be routed to public retrieval providers, and secret-bearing or private-network URLs are rejected.

Agent-only command entry points:

```bash
nuoyan set-research-policy --task-id <task_id> --policy <policy.json> --json
nuoyan record-research-claim --task-id <task_id> --claim <claim.json> --json
nuoyan record-evidence-conflict --task-id <task_id> --conflict <conflict.json> --json
nuoyan record-research-iteration --task-id <task_id> --iteration <iteration.json> --json
nuoyan research-integrity --task-id <task_id> --json
```

These controls are implemented and covered by deterministic tests. They have not yet been certified through a real sales or production research project; claim wording, publisher independence and conflict resolution still require qualified human review.

## NMPA Human-Assisted Collection

NMPA competitor registration uses a human-assisted evidence workflow. Standard scenario and delivery commands generate a deterministic search plan; they do not run the legacy NMPA HTTP, Edge CDP or Playwright collectors. The user performs legal searches on the official NMPA page in their own browser and saves a visible screenshot or official export for every required query/category. The agent then records and imports those files:

```bash
nuoyan nmpa-manual-plan --task-id <task_id> --json
nuoyan record-nmpa-manual-search --task-id <task_id> --record <search_record.json> --json
nuoyan import-nmpa-manual --task-id <task_id> --manifest <import_manifest.json> --json
```

The business user does not run these commands and never provides passwords, cookies, tokens or API keys. The agent should tell the user before collection that the official page may require login, verification or manual filters, and should translate the generated plan into plain Chinese instructions.

The source remains open while its phase is `awaiting_user_search` or `awaiting_import`. Missing user action, a missing upload, a blocked page or an incomplete plan must never become `no_results`. A zero-result closure is valid only when every required plan attempt has a structured search record, visible evidence, exact result count and a complete import manifest; only then may the phase become `verified_no_results`. An existing positive NMPA clue conflicts with a later zero-result capture and keeps the source open for review. Positive imports create traceable NMPA materials and draft evidence cards and close as `completed` or `completed_with_warnings`.

`verify-package` rechecks the plan, search record, manifest, copied evidence and SHA-256 values instead of trusting the top-level scenario status. Generic NMPA information imported with `import-finding` remains `needs_manual_review` and cannot satisfy the official-source gate. Legacy browser collectors remain available only for adapter development and diagnostics.

Professional Chinese reading support is built into this repository as a delivery-time capability. R&D users should receive an HTML report that already contains Chinese reading text; they do not need to run translation commands or configure accounts:

- Recommended offline engine: [Argos Translate](https://github.com/argosopentech/argos-translate), an open-source offline translation library. Install `argostranslate` and import an English→Chinese model once on the standard R&D workstation image.
- `nuoyan setup-translation-engine --provider argos --json` installs/checks the optional Argos Python dependency and attempts to install the English→Chinese offline model. Use `--skip-model` when IT wants to install model files separately.
- Translation is an explicit pre-delivery step: run `nuoyan translation-status --task-id <task_id> --json`, then `nuoyan translate-materials --task-id <task_id> --json` when an engine is ready. Report and delivery rendering only read `data/translations.jsonl`; they never start translation or network calls.
- `nuoyan translation-status --task-id <task_id> --json` is an internal agent/maintainer check, not a user-facing R&D operation.
- HTML reports prioritize Chinese titles and “专业中文阅读”; original English remains visible for traceability.
- Evidence excerpts are rendered as reading blocks. Source, query, title, authors, journal/source and Abstract text are separated, and Chinese reading text is split into short paragraphs for review.
- The standard HTML report uses product-style reading navigation. Project analysis keeps a persistent left-side chapter directory with clear click targets, and reading-entry metric cards include a business definition plus a click-through target for evidence maps, evidence cards, core papers, gaps and metric facts.
- Metric facts are rendered as a standalone top-level tab with a searchable evidence table. Users can combine global search with field-specific filters for metric type, value, material title, evidence card, sample type and platform/method. Materials link to source titles, and evidence-card links switch to the full evidence-card tab and anchor the matching card.
- Project-analysis chapters include a paginated evidence-basis table with source-title links, original supporting excerpts and evidence-card anchors, so expert reviewers can trace each analysis section back to materials. The data layer must keep the full matched evidence list; pagination is only a reading control, not a backend truncation rule.
- Validated `data/report_sections.jsonl` chapters are merged into the standard six-tab workbench. They replace only the matching 17 project-analysis chapter content; they must not replace the main HTML or remove the R&D reading, metric facts, core literature, all evidence cards or evidence-gap tabs.
- The “first read” decision block is written as an R&D expert gate review: decision confidence, product positioning, validation focus, evidence readout and next gate are shown instead of a shallow one-line conclusion.

The final verification command reports whether the package is ready for business review:

```bash
nuoyan verify-package --task-id <task_id> --json
```

Core fields are `delivery_artifacts_ready`, `v21_assets_ready`, `final_review_ready`, `scenario_coverage_ready`, `search_profile_ready`, `fallback_ready`, `network_ready`, `source_quality_ready`, `research_integrity_required`, `research_integrity_ready` and `business_ready`.

`business_ready=true` requires more than generated files. The package must have confirmed search scope, complete source coverage or documented fallback, source-site and knowledge assets, reviewed evidence cards, reviewed claim-level evidence links, resolved conflicts, two distinct saturation audits, and a valid standard delivery folder.

V2.2.2 fixes four release regressions found in real project replay: interrupted long pipelines now persist each completed scenario, the “全部证据卡” workbench includes relevance-excluded cards for audit, `delivery_artifacts_ready` no longer conflates missing evidence with missing files, and PatentHub login restrictions carry an executable browser补证 action. V2.2.1 fixes standard-report integration so validated `report_sections.jsonl` content replaces only the matching project-analysis chapters while preserving the R&D reading, metric facts, core literature, all evidence cards and evidence-gap tabs.

V2.2.0 adds claim-level traceability, contradiction preservation, source independence checks, research saturation state, data-boundary routing and search-result evidence downgrading. The HTML gap tab, Excel review workbook and trace package expose the same research-integrity audit used by `verify-package`.

V2.1.12 adds a Windows standard-environment installer and a strict `doctor --profile standard` gate. The gate checks the actual runtime path and version, conflicting legacy distributions, a launchable Playwright Chromium, the PDF toolchain, the Argos English-to-Chinese model, enabled Codex Life Science Research/Browser/Chrome plugins, and live public-source connectivity. Plugin cache presence alone no longer counts as an enabled capability.

V2.1.11 adds an opt-in real-network acceptance test for OpenAlex and PubMed, allows reviewed staged evidence cards to replace automatic drafts with the same ID, honors explicitly small `quick_scan` retrieval limits, and tightens metric extraction so engineering sensitivity, citation numbers and lowercase conjunctions are not presented as diagnostic facts. LoD and complete cutoff ranges are now captured as structured metric facts.

The GitHub audit candidate's reproducible execution scope and known business-readiness limits are recorded in [the V2.1.11 live validation summary](audit/v2.1.11-live-validation.md). The summary intentionally does not claim that restricted regulatory, patent or Chinese-journal scenarios are business-ready.

The V2.2.2 regression, clean-install, CLI, installed-skill and live-public-source checks are recorded in [the V2.2.2 release validation](audit/v2.2.2-release-validation.md). It does not replace project-specific business readiness or Windows workstation acceptance.

V2.1.10 aligns the skill instructions with the registered `nuoyan` CLI, makes report and delivery rendering translation-cache-only, fails closed when optional function signatures cannot be inspected, preserves partial query failures as `completed_with_warnings`, and makes delivery artifacts plus V2.1 knowledge assets explicit `business_ready` gates. CLI references, scenario documentation, report rules and the nine verification fields now share one contract.

V2.1.9 removes analyte-by-analyte project patches from the common execution path. Confirmed project fields now control project identity, report subject labels, screening tags and local topic indexes; unrelated literature titles cannot reclassify the project. NMPA and PatentHub use short layered target/product/method queries instead of the full project profile blob. The cross-journal Yiigle source now uses its public search API before the legacy web-page fallback, and source-quality auditing detects contradictions between a no-result aggregate channel and successful specialist Chinese journals.

V2.1.8 hardens literature-depth behavior and method-specific retrieval. Standard complete literature profiles keep their default floor, so a stale low `literature_retmax` value cannot reduce PubMed/PMC/OpenAlex to a quick-scan depth. English literature plans now add method expansion layers for fluorescence immunochromatography, lateral flow immunoassay, immunochromatographic assay and point-of-care immunoassay, and the HTML search profile distinguishes retrieval limits from display limits.

V2.1.7 adds source-quality auditing to make `no_results` reviewable rather than treating it as final evidence absence. English literature sources now use core-first query layers for PubMed, PMC and OpenAlex before broad product/methodology terms. Standard delivery records query attempts for HTTP and browser workflows, surfaces high-risk suspected false negatives in the HTML gap tab and Excel alert sheet, and keeps `business_ready=false` when high-risk source-quality issues remain.

V2.1.6 adds source-safe query planning and gap reconciliation. CMDE, standards, OpenAlex, Chinese full-text and Chinese journal sources start with target/analyte core terms before product hints, broad business terms or the original query are attempted. Standard reports now separate unresolved gaps from “public fallback partially covered” items, and Excel alerts include the fallback-covered source count. Public fallback never closes an official channel by itself; Chinese-specific sources are only covered by Chinese public fallback or local imports, not by generic PubMed/LSR literature.

V2.1.4 makes formal source coverage project-aware. Common IVD projects use the shared regulatory, registration, standards, patent, Chinese laboratory, PubMed, PMC, OpenAlex and Chinese full-text sources. Neurology-specific Chinese literature and Wiley Alzheimer sources are only added when the confirmed project profile indicates neurology, cognitive impairment or AD biomarkers. If the initial task title contains stale wording, confirmed `primary_query` and keywords take precedence.

For biomarker, disease-mechanism or full IVD test-kit projects, `business_ready=true` also requires life-science-research plugin coverage. The default gate requires at least 12 imported plugin materials, 5 source databases and 4 evidence lanes. Missing or shallow plugin imports keep `scenario_coverage_ready=false` and the HTML evidence map shows the external scientific database status separately.

V2.1.5 changes life-science-research from a late verification reminder into an LSR-first gate. Standard full/delivery pipelines now stop before common source collection when the confirmed project profile needs external scientific database evidence and no imported LSR material is present. This is intentionally conservative: complete IVD research that looks like a test kit, assay, IVD product, POCT product, quantitative/qualitative detection project, clinical-use project or biomarker/target project triggers LSR by default. A narrow registry/competitor/standard-only task can opt out only through an explicit confirmation such as `life_science_required=false` or `life_science_scope=只做注册/竞品/标准`.

V2.1.4 also neutralizes AD-only examples in network checks and translation prompts, and expands local topic indexing to general IVD markers, samples, platforms and reference methods.

V2.1.4 fixes AD template bleed-through by routing source coverage, delivery pipelines, verification warnings and report gaps through the confirmed project profile instead of a fixed AD-oriented source list.

V2.1.3 fixes the plugin bridge so source database names such as `EFO/OLS` are stored with safe filenames, imported plugin findings update the `life_science_research` scenario status, and full/delivery pipelines generate a staged plugin query plan instead of silently skipping the external scientific database step.

## Tests

Code changes and releases must follow the [development and release checklist](docs/development-release-checklist.md). It covers bad-case regression, serial task-state writes, real-project replay, installed-skill parity and GitHub release boundaries.

Run deterministic tests without depending on live public networks:

```bash
python3 -m pytest
```

Live network checks should remain explicit and separate from normal CI:

```bash
nuoyan doctor --network --json
NUOYAN_RUN_LIVE_TESTS=1 python3 -m pytest -q scripts/tests/test_live_public_literature_e2e.py
```

The opt-in acceptance test calls the real OpenAlex and PubMed routes, then verifies material records, evidence cards and the Excel review artifact. A passing deterministic suite alone does not establish that live sources are currently reachable.

## Release Note

The Codex loading ID is `nuoyan-skill-v2`; the formal display title is `诺研_skill_IVD研发项目调研`. The installed folder name should remain `nuoyan-skill-v2` so Codex can validate and load the skill consistently.
