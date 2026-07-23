# CLI 契约

CLI 命令由 agent 调用：

- `nuoyan doctor --json`
- `nuoyan doctor --network --json`
- `nuoyan init-task --topic <topic> --json`
- `nuoyan show-status --task-id <task_id> --json`
- `nuoyan update-confirmations --task-id <task_id> --values-json <json> --json`
- `nuoyan source-quality --task-id <task_id> --json`
- `nuoyan run-scenario --task-id <task_id> --scenario <scenario_id> --json`
- `nuoyan nmpa-manual-plan --task-id <task_id> --json`
- `nuoyan record-nmpa-manual-search --task-id <task_id> --record <search_record.json> --json`
- `nuoyan import-nmpa-manual --task-id <task_id> --manifest <import_manifest.json> --json`
- `nuoyan run-full-pipeline --task-id <task_id> [--network-preflight|--skip-network-preflight] --json`
- `nuoyan run-delivery-pipeline --task-id <task_id> [--network-preflight|--skip-network-preflight] --json`
- `nuoyan import-local --task-id <task_id> --path <path> --json`
- `nuoyan import-finding --task-id <task_id> --title <title> --source <source> --source-url <url> --content <text> --material-type <type> --json`
- `nuoyan import-life-science-findings --task-id <task_id> --findings-json-file <json> --query <query> --json`
- `nuoyan life-science-plan --task-id <task_id> --json`
- `nuoyan import-literature-table --task-id <task_id> --path <csv_or_xlsx> --json`
- `nuoyan source-sites --json`
- `nuoyan build-knowledge --task-id <task_id> --json`
- `nuoyan create-analysis-requests --task-id <task_id> --json`
- `nuoyan validate-staged --task-id <task_id> --type evidence-card|report-section --json`
- `nuoyan commit-staged --task-id <task_id> --type evidence-card|report-section --json`
- `nuoyan generate-evidence-cards --task-id <task_id> --json`
- `nuoyan export-review --task-id <task_id> --json`
- `nuoyan import-review --task-id <task_id> --xlsx <path> --json`
- `nuoyan build-report --task-id <task_id> --type materials --json`
- `nuoyan build-report --task-id <task_id> --type feasibility --json`
- `nuoyan translate-materials --task-id <task_id> --json`
- `nuoyan translation-status --task-id <task_id> --json`
- `nuoyan setup-translation-engine --provider argos|libretranslate|openai --json`
- `nuoyan build-standard-delivery --task-id <task_id> --json`
- `nuoyan verify-package --task-id <task_id> --json`
- `nuoyan package-task --task-id <task_id> --json`
- `nuoyan site-profile --scenario <scenario_id> --json`
- `nuoyan record-site-observation --task-id <task_id> --scenario <scenario_id> --observation-json <json> --json`
- `nuoyan prepare-browser-session --task-id <task_id> --scenario <scenario_id> --json`
- `nuoyan open-browser-session --task-id <task_id> --scenario <scenario_id> --json`
- `nuoyan browser-workflow --scenario <scenario_id> --query <query> --json`
- `nuoyan probe-browser-workflow --task-id <task_id> --scenario <scenario_id> --query <query> --json`
- `nuoyan scout-browser-workflow --task-id <task_id> --scenario <scenario_id> --query <query> --launch-mode playwright|edge-cdp --json`
- `nuoyan run-browser-workflow --task-id <task_id> --scenario <scenario_id> --query <query> [--methodology <method>] [--launch-mode playwright|edge-cdp] --json`

不要让非 IT 业务用户直接阅读 JSON；agent 应把 JSON 转成中文状态说明。

## V2.1 门禁契约

- `run-scenario` 执行正式来源场景前会检查检索画像；缺少必要确认项时返回 `needs_confirmation` 并以退出码 2 停止。
- `doctor --network` 用于正式公网采集前的网络体检，必须区分 Python DNS、Python HTTPS 和系统 curl 通道。
- `run-full-pipeline` 和 `run-delivery-pipeline` 在未补全检索画像时不得启动正式采集；默认开启网络预检并写入 `network_preflight` 日志。
- `nmpa_competitor` 的标准执行只生成或刷新 `manual/nmpa/search_plan.json`、可读计划和两份模板，不发起 NMPA 网络请求。旧 HTTP、Edge CDP、Playwright 和 DOM collector 不得由 `run-scenario` 或交付流水线调用。
- `record-nmpa-manual-search` 只接受与当前计划完全匹配、由操作人确认的结构化记录。部分记录保持 `awaiting_user_search`；全部计划已记录但尚未导入可见证据时为 `awaiting_import`。
- `import-nmpa-manual` 要求 `capture_complete=true`，并逐项校验查询、注册类别、精确结果数、NMPA 来源 URL、截图/官方导出文件和 SHA-256。清单或证据缺失时不得关闭来源。
- NMPA 的 `no_results` 仅对应 `verified_no_results`：全部必要 attempt 已记录并验证、每项都有可见证据、每项结果数均为 0。用户未操作、未上传、页面受限或只导入通用线索时都不得判空。
- `build-standard-delivery` 可生成草稿交付，但若检索画像缺失，会写入日志并由 `verify-package` 标记 `search_profile_ready=false`。
- `verify-package` 必须输出 9 个核心门禁字段：`delivery_artifacts_ready`、`v21_assets_ready`、`final_review_ready`、`scenario_coverage_ready`、`search_profile_ready`、`fallback_ready`、`network_ready`、`source_quality_ready`、`business_ready`。
- `business_ready` 由上述门禁共同约束；最终回复仍必须单独解释 9 个字段，避免把“文件已生成”误写为“业务已就绪”。
- 采集失败后，agent 必须使用 `import-finding`、浏览器 workflow、重试或用户材料导入等动作形成兜底记录；缺少兜底记录时 `fallback_ready=false`。
- 标准交付前必须生成 V2.1 资产：`source_sites_v21.json`、`knowledge/metric_facts.jsonl`、`knowledge/literature_graph.json`、`knowledge/topic_index.json`。缺失时 `v21_assets_ready=false`。
- life-science-research 插件结果必须通过 `import-life-science-findings` 或等价桥接进入材料管线；不得只写在聊天摘要或报告段落中。
- `verify-package` 对已完成或判零的 NMPA 场景必须重新读取检索计划、检索记录、导入清单和证据文件，校验路径边界、内容集合与 SHA-256；不能只信任 `task.json` 的顶层状态。

`prepare-browser-session` 和 `open-browser-session` 用于需要登录态、Cloudflare 真人验证或复杂页面交互的网站。它们只负责持久化合法浏览器状态，不得用于绕过验证码、付费墙或权限墙。
`probe-browser-workflow` 只做只读页面探测和状态分类，用于确认当前持久化会话是否已经登录、是否被权限限制、是否进入搜索结果页。
`scout-browser-workflow` 用于站点适配开发，保存页面 HTML、DOM 候选元素和 network response 候选。NMPA 旧适配器可以在开发诊断时使用 `--launch-mode edge-cdp` 侦察，但该结果不能替代专用人工导入，也不能进入标准来源闭环。
`run-browser-workflow` 使用固定 workflow 和同一持久化会话执行受控页面流程。当前通用层负责正常导航、弹窗安全关闭、搜索页快照、下载目录约束、状态分类和日志记录；站点级详情解析和材料生成必须在后续 collector 中明确实现，不能伪造材料。

浏览器 workflow 默认优先尝试无头执行，避免干扰用户桌面。只有需要登录、验证码、机构认证、下载授权确认或开发排错时，才主动使用 `--headed` 打开可见浏览器。若某个站点只能通过可见浏览器稳定采集，命令必须在结果和日志中记录降级原因。
