---
name: nuoyan-skill-v2
description: 用于 IVD 企业研发人员开展研发项目调研，组织调研范围确认、多源文献与科学数据库证据采集、材料包、增强证据卡、HTML 调研分析综述、Excel 证据审阅表和本地知识索引等产出。适用于体外诊断产品立项、竞品与法规证据整理、研发可行性评估和面向评审的材料准备。
---

# 诺研_skill_IVD研发项目调研

使用本 skill 时，先做项目理解和范围确认，再进入资料收集、证据整理、分析和报告生成。不要直接替用户假设产品边界、适用场景、目标市场、注册路径或证据标准。

> 命名规则：Codex 加载 ID 固定为 `nuoyan-skill-v2`，保持小写字母、数字和连字符格式，并与安装目录名一致。中文展示标题保留为“诺研_skill_IVD研发项目调研”。

## 工作原则

- 全程使用中文与用户沟通，必要时保留法规、技术和产品术语的英文原文。
- 默认把用户视为不熟悉 IVD 研发、注册和临床证据体系的业务提出方。用户需求可能模糊、不完整或含有专业误区时，Agent 必须先以产品经理视角理解真实目标，主动补全项目边界、指出关键假设和风险，并给出可执行推荐方案；不得机械照抄用户原话或要求用户先具备专业知识。
- 先确认调研范围：产品类型、检测项目、预期用途、目标地区、目标用户、竞品范围、时间范围和报告深度。
- V2.1 的第一步是补全检索条件，不得跳过。用户只给出模糊课题时，必须先围绕产品类型、检测项目、疾病/适应症、样本类型、检测平台/方法学、预期用途、目标地区、目标用户、竞品范围、文献时间范围、文献召回数量、文献 profile、专利范围和报告深度提问。用户回复“按推荐”时，也必须先明确推荐假设并写入确认项，再开始正式采集。
- 即使记忆、历史任务或本地文件中已有项目画像，也只能作为“推荐检索条件草案”。正式采集前必须把检索条件草案展示给用户确认；用户确认后再写入 `update-confirmations` 并执行采集。不得因为记忆中已有信息就直接开跑。
- 对不明确的问题提供文字 RPG 式选项，让用户通过编号或简短文字选择下一步，例如“1. 快速立项判断 / 2. 完整可行性报告 / 3. 只做竞品和法规证据”。
- 不得绕过验证码、登录、付费墙、访问控制或网站服务条款。遇到受限资料时，如实说明限制，并请求用户提供合法取得的文件或链接。
- CLI 只给 agent 使用，不面向非 IT 用户。不要让业务用户直接操作命令行。
- Codex Chrome 用于站点探索、登录态页面观察、验证码/权限限制确认和失败诊断；观察结果必须通过 `record-site-observation` 记录，后续沉淀为站点 adapter 或 site profile。不要让 Codex 每次自由点击网页来替代稳定 CLI。
- 当 HTTP/API adapter 无法覆盖真实网站流程时，可以使用 Playwright 持久化浏览器会话执行固定页面 workflow；已定义人工辅助流程的来源除外。登录、Cloudflare 真人验证或机构认证必须由用户在可见浏览器中合法完成，agent 只读取登录后合法可见内容。
- 遇到 DNS、HTTP 429、连接失败、页面结构变化、登录态、验证码、权限或下载失败时，不得直接跳过来源场景。必须记录真实失败状态，并执行兜底链路：缩短/改写检索式重试、官方/公开网页检索后用 `import-finding` 导入、浏览器 workflow 观察并记录、请求用户提供合法材料。所有兜底动作或阻塞原因都必须进入报告“缺口与任务”和 Excel 补证表。
- 无结果或命中偏少时必须分层重试，不得以单个宽检索式结束；场景状态记录全部检索层级。
- 中文来源先用检测项目/靶标核心词；NMPA 和专利再追加产品提示与短方法学提示。
- 英文文献来源先用核心词，再按方法学、产品、样本和预期用途扩展；具体分层规则读取 `references/workflow.md`。
- `no_results` 不是最终事实，只是某一来源、某一检索策略下的状态。标准交付必须运行或自动生成采集质量审计：检查是否存在单一检索式判空、缺少核心词层级、检索词过长、OpenAlex 与 PubMed/PMC/LSR 互相矛盾等假阴性风险。存在高风险疑似假阴性时，HTML 报告“资料缺口”、Excel“采集异常”和 `verify-package` 必须显式提示，并使 `business_ready=false`。
- 已确认的项目画像是项目身份、目标物标签、报告通用分析和知识索引的唯一优先依据。材料题名只能在旧任务缺少确认画像时兜底，不得因材料池混入其他疾病、方法学或多重检测文献而改写当前项目类型；新增检测项目不得通过增加 analyte 专属排除词来修复污染。
- NMPA 和专利等产品型来源必须使用目标物核心词、产品提示词和短方法学提示的分层检索计划；禁止把样本、平台、用途、同义词和完整范围说明拼成单个超长检索式。中华医学期刊跨刊检索优先使用公开检索接口保存题录与摘要，前端只返回 loading shell 时不得判为 `no_results`。
- NMPA 标准采集固定使用人工辅助闭环：agent 生成计划并提前提示用户在自己的浏览器中完成官方查询；用户保存每次查询的截图或官方导出；agent 再记录检索并通过专用清单导入。标准 `run-scenario` 和交付流水线不得调用旧 HTTP、Edge CDP 或 Playwright NMPA collector；这些实现只可用于开发诊断。
- 正式来源按已确认项目画像动态装配；场景范围、适用条件和失败口径读取 `references/scenarios.md`。
- 原始文件、PDF、网页快照和全文抽取文件必须优先按材料标题命名，文件名格式建议为：`MAT-000001_材料标题前80字_来源_YYYYMMDD.ext`。标题需做文件名安全清理；只有无标题时才退回 `material_id` 命名。下载失败、权限受限或仅有题录/摘要时，必须在材料记录、证据卡和 Excel 补证表中写明“未取得原文”的原因。
- PubMed/PMC 文献采集必须保留页面 Abstract 的完整结构化内容，包括 Objective/Methods/Results/Interpretation/Keywords 等分段；不得只保存摘要前几句。PubMed 命中文献后，还要抓取 Similar articles 中高相关条目并记录为相关文献线索。若 PubMed 页面存在 Free full text / PMC 入口，应进入 PMC 全文页，优先下载 PDF；PDF 不可用时保存 PMC XML/HTML 全文和抽取文本，并记录不可下载原因。
- 标准完整调研使用 `complete_literature`，默认 200 条/英文源；轻量任务显式选择 `quick_scan`。
- 用户要求全量或不设上限时，先报告命中数、时间、磁盘、限流和超时风险，取得二次确认后分批执行。
- 全量策略即使被二次确认，也必须先查询命中总数、向用户说明预计数据量，再分批获取详情；Similar articles、PDF 下载、出版社全文下载、网页快照和 ZIP 打包等二级动作必须设置可解释的策略上限或分阶段执行，不能让二级下载拖成主流程阻塞。
- 文献摘要不只在原始 JSON 中保留。HTML 报告、Excel 文献检索表和 Markdown 证据卡都必须展示结构化 Abstract 分段；如果来源只给出非结构化摘要，也要标注为 Abstract，而不是截成一段无法审阅的短文本。
- 英文材料保留完整原文和结构化摘要；AUC、灵敏度、特异性、cut-off、CI、样本量等另列参数要点。
- 交付前先运行 `translation-status`。引擎可用时显式运行 `translate-materials`，再生成报告；报告渲染只读缓存，不发起翻译或网络请求。
- 翻译能力缺失时，主动说明影响并提示安装诺研翻译插件组件；不得把账号、API Key 或手工命令配置转嫁给研发用户。
- HTML 报告页面标题不得只写成“可行性调研报告”，也不得保留“立项”作为展示标题词。标准交付报告的浏览器标题和 H1 应统一使用“XX项目调研分析综述”这类更宽口径标题；文件名 `00_立项调研综合报告.html` 可保持稳定，避免破坏既有交付目录链接。
- HTML 主报告采用研发筛选工作台；页签、目录、筛选器、证据锚点和字段规格读取 `references/report-rules.md`。
- 报告把采集异常转为业务可读的缺口清单；具体展示和公开兜底规则读取 `references/report-rules.md`。
- 项目分析必须聚合真实材料和证据卡，不得只输出固定模板，也不得带入上一项目的专属结论。
- 初始标题与确认画像冲突时，以已确认的 `primary_query` 和关键词池为准。
- V2.1 内置标准信源配置，运行时可通过 `source-sites` 导出。标准信源包括 CMDE、NMPA、国家标准平台、PatentHub、中华医学期刊、PubMed/PMC/OpenAlex、life-science-research 插件通道、本地导入和 Zotero 可选导入。信源配置必须进入 `90_系统追溯数据/01_原始材料数据_data/source_sites_v21.json`。
- 范围确认后，如果课题涉及标志物、蛋白、基因、疾病机制、通路、临床试验、遗传证据或公共科学数据库线索，应调用 life-science-research 插件能力。插件结果不得停留在聊天摘要中，必须通过 `import-life-science-findings` 或等价桥接写入 Material、SourceRun、EvidenceCard 和本地知识索引。
- 生物标志物、蛋白/基因、疾病机制、通路、临床试验或遗传证据项目必须先运行或生成 `life-science-plan`，再通过 life-science-research 插件查询并导入材料。默认最低覆盖为 12 条插件材料、5 个来源数据库和 4 个证据通道；未导入、导入过少或证据通道过窄时，`verify-package` 必须保持 `scenario_coverage_ready=false` 和 `business_ready=false`。
- 完整 IVD 调研默认触发 `life-science-plan`；仅在用户明确限定注册/竞品/标准范围时记录豁免。
- 标准交付流水线必须执行 LSR-first gate：需要 life-science-research 且尚未导入达标材料时，应先生成查询计划并停止通用采集，不得先跑 PubMed/NMPA/标准/专利后再补 LSR。
- V2.1 证据卡必须尽量填充来源追溯、研发定位、指标事实、原文摘录、关系字段、局限和补证任务。涉及 AUC、灵敏度、特异性、cut-off、HR/OR、CI、样本量等参数时，应进入 `MetricFact`，并展示在 HTML、Excel 和 Markdown 证据卡中。
- V2.2 的正式结论必须登记为 `ResearchClaim`，逐条记录事实/研发判断/建议/共识、支持状态、可信度、影响等级、是否包含推断，以及关联证据卡和指标事实。搜索结果摘要只能作为发现线索，未读取底层正文时不得支撑论断。
- 高影响支持性论断至少需要两个独立发布机构；“行业共识”至少需要三个。不同网址或同一机构的不同子域名不能自动算作独立来源。
- 发现性能数据、方法学、法规状态或来源结论不一致时，必须建立 `EvidenceConflict`；争议不得静默删除。冲突未完成专家处理时，`research_integrity_ready=false`。
- 完整调研必须记录结构化研究迭代。达到来源覆盖后，再从反向证据和覆盖缺口等两个不同方向执行零增量审计；重复同一方向不能计入饱和。
- 任务数据必须按 `public | internal | confidential` 设置研究策略。内部或机密内容不得发送给公共搜索、Jina、Exa 或其它外部服务；地址包含 token、签名、账号信息或指向本机/内网时必须阻断。
- 每次标准交付前应生成本地知识资产：`knowledge/metric_facts.jsonl`、`knowledge/literature_graph.json`、`knowledge/topic_index.json`、`knowledge/dedup_index.json` 和 `knowledge/relation_summary.md`。这些文件用于后续项目复用、主题关联和文献去重。
- 不要把中间 JSON、内部状态或调试字段暴露给非 IT 用户；面向用户时输出自然语言结论和可审阅的文件。
- 所有关键判断都要尽量绑定来源、日期、证据强度和不确定性。
- 每次 HTML 报告结构、页签、筛选器、翻译阅读、资料缺口或用户交互方式发生改造时，必须同步迭代 skill 代码、模板、样式、测试、README/SKILL 说明，并提交 Git 版本；不得只改某一个已生成 HTML 文件。

## 推荐流程

1. 理解项目：确认产品、适用场景、目标地区、评审目的和时间限制。
2. 补全检索条件：用文字 RPG 式选项补齐产品类型、检测项目、疾病、样本、平台、预期用途、地区、用户、竞品、文献、专利和报告深度；这是正式检索前的必要动作。
3. 收集材料：整理法规、指南、竞品、文献、市场和技术路线资料；NMPA 进入人工辅助闭环时，应先向用户说明需要在官方页面查询并保存截图或导出文件。
4. 生成证据卡：记录证据摘要、来源、关键结论、可信度、风险和待复核点。
5. 登记研究论断与冲突：将报告结论写入 `ResearchClaim`，关联证据卡和指标事实；将不一致结果写入 `EvidenceConflict` 并完成人工复核。
6. 完成研究迭代审计：记录每轮新增材料、发布机构、论断、变化和已关闭缺口；覆盖达标后完成两个不同方向的零增量复核。
7. 形成材料包：将原始资料索引、证据卡和状态文件组织成可追溯材料包。
8. 生成报告：输出标准交付目录中的 `00_立项调研综合报告.html`，报告必须采用研发筛选版工作台，含项目分析、研发阅读入口、核心必读文献、全部证据卡和资料缺口；证据地图、缺口任务、关键证据、文献、竞品、标准、专利、指标事实和研究完整性审计应以业务可读方式呈现。
9. 生成审阅表：输出 `01_证据审阅与补证任务表.xlsx`，包含“论断与冲突”页，便于专家逐条确认、修订和补充。
10. 复制证据卡：标准交付目录必须包含 `02_证据卡/`，其中放置全部 Markdown 证据卡，便于研发、医学和注册人员逐张审阅。
11. 生成知识索引：运行或由交付命令自动触发 `build-knowledge`，形成指标事实、主题索引、文献关系图和候选去重索引。
12. 采集质量审计：运行或由交付流水线自动执行 `source-quality`，检查 no_results 是否存在检索策略假阴性、缺少核心词层级或跨库矛盾。
13. 交付验证：运行 `verify-package`，确认检索条件、来源场景覆盖、失败兜底、采集质量、研究完整性、证据卡、人工复核、知识资产和标准交付物是否满足交付门槛；不满足时必须说明 `business_ready=false` 的具体原因。

### 主要命令入口

```bash
nuoyan init-task --topic <topic> --json
nuoyan show-status --task-id <task_id> --json
nuoyan run-full-pipeline --task-id <task_id> --json
nuoyan run-delivery-pipeline --task-id <task_id> --json
nuoyan research-integrity --task-id <task_id> --json
nuoyan verify-package --task-id <task_id> --json
```

## 交互与能力预检

- 运行前检查网络、磁盘、浏览器、登录态、下载权限、翻译引擎和外部插件，提前说明缺失能力及影响。
- 预计某信源登录后信息更完整时，先提示用户登录，再启动采集；不要等系统误判为空结果后才解释。
- 当前 Chrome 已登录不等于独立 Playwright profile 已登录；必须分别探测，不得混写为同一登录态。
- 需要人工登录、验证码或机构认证时，提前提示并由用户在合法可见页面中完成。只有对应场景支持持久化自动采集时才切回无头模式；NMPA 继续走人工证据导入。
- 操作可能弹窗、抢焦点、打开下载窗口、占用同一浏览器 profile 或运行较久时，执行前先提示用户。
- 能力缺失但可安装时，明确提示安装对应插件组件；用户拒绝或无法安装时，保留可用降级路径和资料缺口。

## 参考文档

按当前动作读取，不要把全部细节塞入主指令：

- `references/workflow.md`：完整工作流、查询分层和失败兜底。
- `references/cli-contract.md`：命令、参数和交付门禁字段。
- `references/scenarios.md`：信源场景、适用范围和失败状态。
- `references/actions.md`：自然语言动作映射与动作门禁。
- `references/browser-workflow.md`：浏览器 profile、登录和无头/可见模式。
- `references/life-science-research-workflow.md`：科学数据库插件接入。
- `references/evidence-rules.md`：材料与证据卡规则。
- `references/report-rules.md`：HTML 报告结构、筛选和翻译展示。
- `references/troubleshooting.md`：环境与采集故障诊断。

## CLI 使用约束

优先使用 `scripts/ivd_research` 下的 CLI 和工具函数处理可重复、可验证的文件生成任务。CLI 是 agent 的内部工具，用户只需要接收结果文件和结论摘要。

Windows 标准安装使用 Skill 目录内的独立运行时。Agent 必须优先调用 `.venv\Scripts\nuoyan.exe`，不得假定系统 PATH 中的 `nuoyan`、Python 包、Playwright 浏览器或翻译模型已经存在。正式调研前运行 `.venv\Scripts\nuoyan.exe doctor --profile standard --network --strict --json`；未达到 `standard_ready=true` 时，先报告实际缺失项并完成环境修复，不得以聊天回答替代标准流水线输出。安装和更新方式见 `docs/windows-standard-environment.md`。

CLI 或脚本失败时，不要掩盖失败，不要编造结果。应说明失败步骤、已完成内容、未完成内容、错误信息摘要和可继续的最小下一步。

当 HTTP/API 采集失败、页面需要登录态或页面结构未知时，agent 可以使用 Codex Chrome 观察页面。但 Chrome 观察只用于诊断和适配器开发：

1. 先运行 `nuoyan site-profile --scenario <scenario_id> --json` 查看站点策略。
2. 在 Chrome 中观察搜索框、筛选项、结果列表、详情页和下载入口。
3. 遇到登录、验证码、Cloudflare、权限墙时停止自动化并如实记录。
4. 运行 `nuoyan record-site-observation --task-id <task_id> --scenario <scenario_id> --observation-json <json> --json` 记录观察结果。
5. 后续把稳定观察沉淀成 adapter 代码和测试，不要依赖每次临场点击。

当站点需要登录态、Cloudflare 真人验证、复杂分页、筛选或下载流程时，agent 应使用 Playwright 持久化会话：

1. 运行 `nuoyan browser-workflow --scenario <scenario_id> --query <query> --json` 查看固定页面 workflow 和目标搜索 URL。
2. 运行 `nuoyan prepare-browser-session --task-id <task_id> --scenario <scenario_id> --json` 创建会话目录。
3. 可先运行 `nuoyan probe-browser-workflow --task-id <task_id> --scenario <scenario_id> --query <query> --json` 做只读探测，判断当前登录态是否可用。
4. 对结构未知或动态阻塞的网站，运行 `nuoyan scout-browser-workflow --task-id <task_id> --scenario <scenario_id> --query <query> --launch-mode playwright --json` 保存 DOM/network 候选。
5. 如返回 `needs_login` 或 `permission_required`，运行 `nuoyan open-browser-session --task-id <task_id> --scenario <scenario_id> --json` 打开可见浏览器。
6. 引导用户在浏览器中手动完成登录、机构认证或真人验证。
7. 运行 `nuoyan run-browser-workflow --task-id <task_id> --scenario <scenario_id> --query <query> [--methodology <method>] [--launch-mode playwright|edge-cdp] --json` 执行已支持场景的固定搜索或正常导航流程，保存快照并记录状态；如自动降级为可见浏览器，必须记录降级原因。NMPA 不使用此入口完成标准采集。
8. 后续采集使用同一任务目录下的 `browser_state/<scenario_id>`，保留登录态和验证状态。
9. 不得自动破解验证码、Cloudflare、付费墙或权限墙；无法合法访问时必须记录失败原因。

### NMPA 人工辅助采集

- `run-scenario --scenario nmpa_competitor`、`run-full-pipeline` 和 `run-delivery-pipeline` 只生成或刷新检索计划，不访问 NMPA 网络接口，也不接管用户浏览器。
- agent 应把 `manual/nmpa/search_plan.md` 中的官方入口、查询词和注册类别转述给用户，并提前说明可能需要登录、验证码或页面人工操作。用户不需要运行 CLI。
- 用户在自己的浏览器中逐项查询，保存包含查询条件和结果状态的可见截图，或保存 NMPA 官方导出文件。不得读取或保存用户密码、Cookie、token、API Key 等凭据。
- agent 使用以下内部命令完成记录和导入：

```bash
nuoyan nmpa-manual-plan --task-id <task_id> --json
nuoyan record-nmpa-manual-search --task-id <task_id> --record <search_record.json> --json
nuoyan import-nmpa-manual --task-id <task_id> --manifest <import_manifest.json> --json
```

- `awaiting_user_search` 表示仍有计划内查询未执行；`awaiting_import` 表示查询已记录但截图、导出或结构化结果尚未完整导入。这两个状态都不得解释为“未检出竞品”。
- 只有计划内全部查询均有结构化记录、每项均有可见证据、导入清单声明完整且结果数全部为 0 时，才允许进入 `verified_no_results`。有真实结果并完成专用导入时进入 `completed` 或 `completed_with_warnings`。
- 如果任务中已经存在 NMPA 正向材料或待核验线索，后续零结果与其冲突时必须保持 `needs_manual_review`，不得覆盖为 `verified_no_results`。
- 通用 `import-finding` 导入的 NMPA 线索只能标为待人工复核，不能替代专用清单、不能关闭该来源，也不能让 `business_ready=true`。
- 旧 NMPA HTTP、Edge CDP、Playwright 和 DOM 采集器只保留作开发诊断，不得接入标准采集或业务就绪判断。

### PatentHub 登录态采集

- PatentHub 首次采集必须使用 `open-browser-session --scenario patenthub_patents` 打开可见浏览器，由用户手动完成合法登录或验证码；不得把账号、密码、cookie 或 token 写入任务材料、日志、报告或长期记忆。
- 登录完成后使用同一 `profile-scope` 运行 `probe-browser-workflow`。确认不再返回 `needs_login` 后，才可使用 `run-browser-workflow --scenario patenthub_patents --headless` 执行检索和详情采集。
- 登录页、注册提示页和权限提示页不得生成专利 Material。详情页必须同时具备有效公开号、非占位标题，以及申请号、申请人、发明人、IPC、摘要或法律状态中的至少一个真实字段，才能入库。
- PDF、权利要求或说明书受 VIP/权限限制时，只保存合法可见的题录、摘要和状态信息，并记录 `permission_required`；不得绕过付费或访问控制。

## 外部搜索结果导入

当 Agent 通过 WebSearch、Jina Reader 或其它外部渠道获取到有效证据时，使用 `import-finding` 命令将其写入材料管线：

```bash
nuoyan import-finding --task-id <task_id> \
  --title "证据标题" \
  --source "web_search" \
  --source-url "https://..." \
  --content "证据正文内容..." \
  --material-type "regulatory" \
  --json
```

支持 `--content-file` 从文件读取长文本。`--material-type` 可选值：`regulatory | competitor | standard | patent | literature | local_import`，省略时自动推断。

NMPA 竞品注册的正式闭环必须使用 `record-nmpa-manual-search` 和 `import-nmpa-manual`。通过本节通用命令导入的 NMPA 信息仅作为线索，不代表官方来源已完成。

## life-science-research 插件证据导入

当 Codex 通过 `life-science-research:research-router-skill` 或其下游子 skill 得到 UniProt、STRING、Reactome、OpenTargets、ClinicalTrials、GWAS、ClinVar、Human Protein Atlas、PMC 等结果时，将结果整理为 JSON 列表，并导入材料管线：

```bash
nuoyan life-science-plan --task-id <task_id> --json
```

```bash
nuoyan import-life-science-findings --task-id <task_id> \
  --findings-json-file external_findings.json \
  --query "plasma p-tau217 Alzheimer disease" \
  --json
```

每条 finding 建议包含 `source_database`、`evidence_lane`、`entity`、`query`、`result_summary`、`source_url` 和 `identifier`。导入后必须继续运行 `generate-evidence-cards`、`build-knowledge`、`export-review` 和 `build-standard-delivery`。

## V2.1 文献 profile 与本地知识资产

文献 profile 用于控制速度、召回量和二级下载范围。可选 profile 包括：

- `quick_scan`：快速扫描，默认 50 条/英文文献源，适合 30 分钟内判断是否值得深入。
- `complete_literature`：完整文献，默认 200 条/英文文献源，适合标准调研材料。
- `fulltext_first`：全文优先，默认 200 条/英文文献源并提高全文/PDF 获取优先级，适合方法学和性能参数抽取。
- `core_must_read`：核心必读，默认 100 条/英文文献源，适合研发阅读入口。
- `chinese_first`：中文优先，默认 100 条/文献源，适合国内临床应用和注册语境补充。

如需导入本地文献清单或腾讯文档导出表，可使用：

```bash
nuoyan import-literature-table --task-id <task_id> --path literature.xlsx --json
```

本地知识资产通过以下命令生成：

```bash
nuoyan build-knowledge --task-id <task_id> --json
```

采集质量审计用于复盘来源 no_results 是否可信：

```bash
nuoyan source-quality --task-id <task_id> --json
```

该命令是 agent/维护者内部体检工具。业务用户只需要在 HTML 报告“资料缺口”和 Excel“采集异常”中看到“疑似假阴性”和处理建议。

## AI 分析章节生成流程

可行性报告的 17 个分析章节需要 Agent 基于已采集材料生成：

1. `nuoyan create-analysis-requests --task-id <task_id>` 生成分析请求模板
2. Agent 逐章阅读材料全文和证据卡，为每个章节写入 `staging/report_sections/<section_id>.json`
3. `nuoyan validate-staged --task-id <task_id> --type report-section` 校验章节
4. `nuoyan commit-staged --task-id <task_id> --type report-section` 提交入库
5. `nuoyan build-report --task-id <task_id> --type feasibility` 渲染最终报告

每个 report_section JSON 必须包含：
- `section_id`、`section_title`、`facts`、`analysis`
- `evidence_gaps`：没有充分证据时写明缺口，不得伪装确定结论
- `evidence_strength_summary`：`strong | moderate | weak | gap`（必填）
- `confidence_level`：`高 | 中 | 低`
- `supporting_evidence_refs`：`[{material_id, evidence_card_id, excerpt}]`（必须引用真实材料）

## 最终产出

一次完整调研通常应产出：

- `交付目录/00_立项调研综合报告.html`：唯一默认主入口，含多页签和 17 章项目分析。
- `交付目录/01_证据审阅与补证任务表.xlsx`：证据审阅、文献检索、补证任务和责任角色回填入口。
- `交付目录/02_证据卡/`：全部 Markdown 证据卡，是人工逐条复核的重要业务材料。
- `交付目录/90_系统追溯数据/`：材料、证据卡、日志、下载文件、内部报告、暂存数据、标准信源配置和本地知识索引。

如果用户只要求部分产出，只生成所需文件，并说明未生成的内容不在本次范围内。
