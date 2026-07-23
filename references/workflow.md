# 工作流

Codex 负责主持中文对话，`nuoyan` CLI 负责确定性执行、状态记录和产物生成。CLI 是给 agent 用的工具，不要求业务人员直接操作命令行。

## V2.1 强制入口：先补全检索条件

正式采集前必须补齐检索条件。用户给出模糊课题时，agent 不得直接 `init-task -> run-scenario`，必须先提问或给出推荐选项并取得用户确认。

最少需要补齐：

1. 产品类型：试剂盒、仪器配套试剂、LDT/科研转化、算法/组合判读等。
2. 检测项目：靶标、组合指标、比值或算法输入。
3. 疾病/适应症：疾病名称、诊疗阶段、是否用于筛查/辅助诊断/疗效监测。
4. 样本类型：血清、血浆、全血、尿液、CSF、拭子等。
5. 检测平台/方法学：化学发光、免疫层析、ELISA、qPCR、NGS、质谱等。
6. 预期用途：风险评估、辅助诊断、分层、转诊、监测、科研用途等。
7. 目标地区：NMPA 中国优先、FDA、CE、全球或指定国家。
8. 目标用户：研发、注册、医学、产品、立项评审或销售准入。
9. 竞品范围：直接竞品、相邻竞品、替代技术、参考方法。
10. 文献范围：时间范围、召回数量、文献类型、是否要求全文。
11. 专利范围：中国、全球、中美欧日、PCT、申请人范围。
12. 报告深度：快速立项判断、完整可行性报告、专项证据调研。
13. 文献 profile：`quick_scan`、`complete_literature`、`fulltext_first`、`core_must_read` 或 `chinese_first`。

如果用户回复“按推荐”，agent 应将推荐范围明确写出并通过 `update-confirmations` 写入任务状态；不得把推荐假设只留在对话里。

正式公网采集前还必须执行网络预检。推荐使用：

```bash
nuoyan doctor --network --json
```

或在流水线中保留默认开启的 `--network-preflight`。预检至少要区分 Python DNS、Python HTTPS 与系统 curl 通道；若 PubMed/PMC/OpenAlex 任何一项不可用，后续报告必须保留网络状态、采集失败原因和兜底动作。

每次关键动作前后都应查看状态：

```bash
nuoyan show-status --task-id <task_id> --json
```

状态面板按以下顺序向用户解释：

1. 当前任务
2. 已确认项
3. 待确认项
4. 场景进度
5. 材料统计
6. 失败和待复核摘要
7. 推荐下一步
8. 可选操作

用户可以回复编号，也可以用自然语言描述。自然语言只能映射到 `references/actions.md` 中定义的动作；不能临场发明新流程。

## 正式来源场景覆盖

完整可行性调研必须覆盖证据地图中“当前项目画像适用”的正式来源场景。已确认的 `primary_query`、英文关键词、样本、方法学、预期用途和竞品/专利范围优先于初始任务标题；如果初始标题带有旧项目词，不能让旧标题污染新项目画像。

- 法规/审评：`cmde_regulatory`
- 竞品注册：`nmpa_competitor`
- 现行标准：`standards_current`
- 专利：`patenthub_patents`
- 通用中文文献：`yiigle_zhjyyxzz`、`cma_lab_management`、`yiigle_fulltext`
- 通用国际文献：`pubmed_literature`、`pmc_fulltext`、`openalex_literature`
- 神经/认知方向中文专科文献：`yiigle_zhsjkzz`，仅在项目画像涉及神经、认知障碍、AD 或相关神经标志物时装配
- AD 专用国际专科文献：`wiley_alz`，仅在项目画像涉及 Alzheimer、AD、MCI、认知障碍、p-tau、Aβ、amyloid 等方向时装配
- 外部科学数据库：`life_science_research`，适用于标志物、蛋白、基因、通路、临床研究、遗传证据和公共数据库线索

适用场景可以出现 `completed`、`no_results`、`deferred` 或失败状态，但不能“无记录”。`no_results` 必须包含检索式、检索层级和范围说明；`deferred` 必须说明范围排除或暂缓原因和影响；失败状态必须进入兜底链路。非适用专科信源不应进入客户报告的资料缺口。

## NMPA 人工辅助闭环

`nmpa_competitor` 不走标准自动网页采集。agent 在确认检索画像后生成计划，提前告诉用户需要在 NMPA 官方页面逐项查询，并说明可能出现登录、验证码、筛选器或下载操作。用户只操作自己的浏览器，不运行 CLI，也不向 agent 提供密码、Cookie、token 或 API Key。

1. agent 运行 `nmpa-manual-plan`，读取 `manual/nmpa/search_plan.md`，向用户说明官方入口、每个查询词和国内/进口注册类别。
2. 用户逐项查询并保存可见截图或 NMPA 官方导出。截图应能支持核对查询条件和结果状态；官方导出应保持原始文件。
3. agent 根据用户完成情况填写检索记录模板，运行 `record-nmpa-manual-search`。仍有计划项未执行时状态为 `awaiting_user_search`；全部查询已记录但证据尚未完整导入时为 `awaiting_import`。
4. agent 填写导入清单并运行 `import-nmpa-manual`。命令逐项核对检索会话、查询词、注册类别、精确结果数、NMPA URL、证据文件及 SHA-256，并将合法证据复制到任务目录。
5. 有结果时，结构化产品记录写入 Material 并生成草稿 EvidenceCard；完整时为 `completed`，次要产品字段缺失但仍可追溯时为 `completed_with_warnings`。
6. 只有全部必要计划项均已记录和验证、每项都有可见证据且结果数全部为 0 时，才允许 `verified_no_results`。用户未操作、未上传、页面受限、只完成部分检索或只用 `import-finding` 导入线索，都不得解释为无结果；已有 NMPA 正向材料或线索与本次零结果冲突时，继续保持待人工复核。

`verify-package` 会重新校验上述文件、集合和哈希。人工检索完成后修改计划、记录、清单或证据文件会使门禁失败，必须重新记录或导入。国内限定范围只要求国内注册类别；国内+进口范围要求计划中的全部类别，未确认的额外类别不应被擅自加入。

## V2.1 文献证据增强流程

1. 确认文献 profile 和召回数量，不允许默认无上限全量抓取。标准完整调研使用 `complete_literature`，英文文献源默认 200 条/源；`quick_scan` 才使用 50 条左右的轻量上限。低 `literature_retmax` 不得静默降低完整调研 profile 的默认深度。
2. 运行 PubMed/PMC/OpenAlex 和中文文献场景，英文来源必须先使用英文核心词层级，再使用方法学扩展词、产品提示、样本/预期用途等宽检索层级；荧光免疫层析、免疫层析、侧向层析和 POCT 项目必须自动补充 `fluorescence immunochromatographic assay`、`lateral flow immunoassay`、`immunochromatographic assay` 等英文扩展词。保留 PMID、PMCID、DOI、结构化 Abstract、Similar articles、全文/PDF 状态和失败原因。
3. 标准完整调研先执行 LSR-first gate。课题涉及标志物、蛋白、基因、通路、临床试验、遗传证据，或完整 IVD 检测项目/检测试剂盒/免疫分析/POCT/临床用途画像时，先调用 life-science-research 插件；结果整理为 JSON 后使用 `import-life-science-findings` 回写材料管线。只有用户明确确认“仅做注册/竞品/标准，不做科学数据库证据”时，才允许记录豁免。
4. 对本地文献清单、腾讯文档导出表或企业共享目录，使用 `import-literature-table` 或 `import-local` 导入。
5. 运行 `generate-evidence-cards` 生成 V2.1 证据卡，证据卡应包含来源追溯、研发定位、指标事实、关键摘录、局限和补证任务。
6. 运行 `build-knowledge` 生成指标事实、主题索引、候选去重和文献关系图。
7. 运行 `source-quality`、`export-review`、`build-standard-delivery` 和 `verify-package`，确认 no_results 不存在高风险假阴性、`v21_assets_ready` 和 `business_ready`。

## 采集失败兜底链路

遇到 DNS、HTTP 429、连接失败、页面结构变化、登录态、验证码、权限或下载失败时，不得直接跳过。按以下顺序处理并记录：

1. 改写/缩短检索式重试，避免超长 query、混合中英文和过多限定词导致检索失败。CMDE、标准、中文全文和中文期刊应先用检测项目/靶标核心词，再使用产品提示、宽业务词和原始检索式作为兜底层级；PubMed、PMC 和 OpenAlex 应先用英文核心词，再使用产品提示、方法学、样本类型和预期用途等宽检索层级。
2. 使用公开官方来源、PubMed 页面、PMC 页面、OpenAlex 网页、机构公告或期刊官网手工检索；取得有效结果后用 `import-finding` 写入材料管线。NMPA 例外：必须使用专用人工检索记录和导入清单，通用导入只能保留线索。
3. 对需要 JavaScript、登录态或结构未知的网站，运行 `site-profile`、`browser-workflow`、`probe-browser-workflow` 或 `scout-browser-workflow`，并使用 `record-site-observation` 记录观察结果。
4. 对验证码、付费墙、机构权限、Cloudflare 真人验证等限制，不得绕过；应请求用户提供合法取得的文件、链接或登录后可见材料。
5. 仍无法完成时，将来源状态、失败原因、兜底动作和下一步补证任务写入报告“缺口与任务”和 Excel 审阅表。

失败或受限来源不能被包装成“未发现证据”。单一检索式判空、缺少核心词层级、检索词过长或与其他文献/科学数据库结果矛盾时，应作为“疑似假阴性”进入采集质量审计。报告应把补证任务区分为“未补齐缺口”和“已公开兜底部分补齐”。公开兜底只说明已有同类型材料支持初步复核，不等于官方通道完成；中文特定来源只能由中文公开补证或本地导入材料兜底，不能被 PubMed/LSR 泛文献自动覆盖。`business_ready` 必须保持 false，直到来源覆盖、采集质量审计、人工复核和补证任务满足验收条件。

## Chrome 观察流程

当站点无法通过 API/HTTP 稳定采集，或需要用户 Chrome 中的登录态、Cookie、机构访问权限、已打开页面时，Codex 可以使用 Chrome 观察页面。

Chrome 的定位：

- 用于站点探索、失败诊断、登录态观察和人工验证兜底；
- 不用于绕过验证码、权限墙、付费墙或网站访问控制；
- 不作为长期主采集引擎；
- 观察结果必须沉淀为 `site-profile`、adapter 代码、测试或任务日志。

NMPA 标准采集不接管 Chrome，也不使用本节浏览器 workflow。用户在自己的浏览器中合法查询并保存证据，agent 走 `record-nmpa-manual-search` 和 `import-nmpa-manual`；旧 NMPA 浏览器能力只用于适配器开发诊断。

推荐顺序：

```text
公开 API / JSON 接口
  -> HTTP/HTML 采集
  -> Codex Chrome 观察页面结构和登录态
  -> 用户上传合法取得的材料
```

使用命令：

```bash
nuoyan site-profile --scenario <scenario_id> --json
nuoyan record-site-observation --task-id <task_id> --scenario <scenario_id> --observation-json '<json>' --json
```

如果 Chrome 插件不可用，agent 应如实说明环境问题，并使用公开页面、用户上传材料或后续人工观察替代。
