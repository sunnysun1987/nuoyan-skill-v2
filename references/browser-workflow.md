# Codex Chrome 使用规则

本 skill 运行在 Codex 中，应该利用 Chrome 插件能力，但 Chrome 不是主采集引擎。
当 HTTP/API adapter 不能覆盖真实网站流程时，使用 Playwright 持久化浏览器会话作为固定执行层。

NMPA 是明确例外。标准 `nmpa_competitor` 场景采用“计划生成 -> 用户在自己的浏览器查询并保存证据 -> agent 专用导入”，不接管 Chrome，不启动 HTTP/Edge/Playwright collector。下文 NMPA 旧浏览器能力只允许用于适配器开发和失败诊断，不能关闭正式来源。

## 什么时候用 Chrome

- HTTP/API adapter 失败，需要观察真实页面。
- 网站依赖 JavaScript 渲染。
- 用户已经在 Chrome 中登录，且任务只需要读取合法可见内容。
- 需要确认验证码、Cloudflare、权限墙或空页面原因。
- 开发或修复站点 adapter，需要观察 DOM、表单字段、结果列表和详情页。

## 什么时候用 Playwright

- 网站需要登录态，且用户可以合法登录。
- 网站有 Cloudflare 或真人验证，用户需要在可见浏览器中手动完成验证。
- 网站依赖稳定页面交互：搜索框、下拉筛选、分页、详情页、PDF 下载。
- 需要保留同一任务/同一站点的登录状态，避免每次重新登录。

Playwright 会话状态保存在任务目录下的 `browser_state/<scenario_id>`。agent 可以调用：

- `nuoyan prepare-browser-session --task-id <task_id> --scenario <scenario_id> --json`
- `nuoyan open-browser-session --task-id <task_id> --scenario <scenario_id> --json`
- `nuoyan probe-browser-workflow --task-id <task_id> --scenario <scenario_id> --query <query> --json`
- `nuoyan scout-browser-workflow --task-id <task_id> --scenario <scenario_id> --query <query> --launch-mode playwright|edge-cdp --json`
- `nuoyan run-browser-workflow --task-id <task_id> --scenario <scenario_id> --query <query> [--methodology <method>] [--launch-mode playwright|edge-cdp] --json`

`open-browser-session` 会打开可见浏览器。用户完成登录、Cloudflare 真人验证或机构认证后，登录态会保存在对应场景目录中。
`probe-browser-workflow` 只做只读探测和状态分类。`scout-browser-workflow` 用于站点适配开发，保存 DOM 候选元素和 network response 候选；诊断旧 NMPA 适配器时可以显式使用 `--launch-mode edge-cdp`，但结果不能替代人工证据导入。`run-browser-workflow` 是已支持自动化场景的受控执行入口，返回标准状态：`needs_login`、`permission_required`、`search_results`、`completed` 或 `collection_failed`，并写入用户事件日志和开发调试日志。

## 什么时候不能用 Chrome

- 绕过验证码、付费墙、权限墙。
- 自动破解 Cloudflare 或验证码。
- 高频批量点击。
- 提交非查询类表单。
- 修改网站数据。
- 读取 Cookie、localStorage、密码或敏感账户信息。

## 标准流程

1. `nuoyan site-profile --scenario <scenario_id> --json`
2. 用 Chrome 打开或接管目标页面；如果需要登录态或持久化验证状态，改用 Playwright 持久化会话。
3. 观察搜索框、筛选项、分页、结果列表、详情页、下载入口。
4. 如果出现登录/验证码/权限限制，停止并记录。
5. `nuoyan record-site-observation ...`
6. 把稳定观察沉淀为 adapter 代码和测试。

## Playwright 固定 workflow 流程

1. `nuoyan browser-workflow --scenario <scenario_id> --query <query> --json`
2. `nuoyan prepare-browser-session --task-id <task_id> --scenario <scenario_id> --json`
3. 必要时先运行 `probe-browser-workflow` 判断是否需要用户登录或验证。
4. 对结构未知或动态阻塞的网站，运行 `scout-browser-workflow` 固化 DOM/network 观察结果。
5. 如需人工处理，运行 `open-browser-session`，让用户在可见浏览器中合法完成。
6. 运行 `run-browser-workflow` 执行固定搜索/正常导航流程。
7. 后续站点 collector 在此基础上实现分页、详情页解析和允许范围内的下载。

`run-browser-workflow` 会把快照保存到 `downloads/browser_workflow/<scenario_id>/snapshots/`，把下载目录限制在 `downloads/browser_workflow/<scenario_id>/downloads/`。弹窗只能通过场景配置中的普通关闭或“稍后关注”等控件关闭，不能点击登录、授权、支付或非查询提交控件。

### PatentHub

PatentHub 使用持久化 Playwright profile 复用登录态。首次运行先打开可见浏览器，由用户手动登录；登录成功后关闭可见会话，再用同一 `profile-scope` 执行无头探测和采集。账号密码不由 CLI 接收或保存。

采集器对页面执行两层校验：先排除“用户登录”“注册登录后可以查看更多专利信息”等登录页，再要求详情页同时包含有效公开号、非占位标题和至少一个真实专利字段。任一校验不通过时不生成 Material，并在 `collection_errors` 中记录 `needs_login` 或采集失败原因。

默认浏览器采集应优先尝试无头模式，避免打扰研发人员当前桌面。只有在需要用户手动登录、Cloudflare/验证码、机构认证、下载授权确认，或开发排错时，才主动使用 `--headed`。此规则不改变 NMPA 的人工辅助标准路径；NMPA 的 Edge/CDP 启动逻辑仅用于显式开发诊断。

Chrome 观察结果不是最终材料。只有 CLI 生成的 `materials.jsonl`、`evidence_cards.jsonl`、HTML 报告和 Excel 复核表才是正式产物。
Playwright 页面交互也不是自由点击。稳定路径必须沉淀为可重复 workflow 或 adapter，并把失败状态写入日志。
