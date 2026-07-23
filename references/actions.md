# 动作清单

| action_id | 中文名称 | CLI |
| --- | --- | --- |
| confirm_task_info | 确认/修改任务信息 | update-confirmations |
| confirm_keyword_pool | 确认/修改关键词池 | update-confirmations |
| confirm_collection_scope | 确认/修改采集范围 | update-confirmations |
| confirm_search_profile | 补全/确认完整检索画像 | update-confirmations |
| run_full_pipeline | 运行完整调研流水线 | run-full-pipeline |
| run_delivery_pipeline | 运行 V2 标准交付流水线 | run-delivery-pipeline |
| run_scenario | 运行指定场景 | run-scenario |
| prepare_nmpa_manual | 生成 NMPA 人工检索计划 | nmpa-manual-plan |
| record_nmpa_manual_search | 记录 NMPA 计划内人工查询 | record-nmpa-manual-search |
| import_nmpa_manual | 导入 NMPA 截图、官方导出和结构化结果 | import-nmpa-manual |
| retry_failed | 按失败场景重试并记录结果 | run-scenario / run-delivery-pipeline |
| fallback_import_finding | 外部公开来源兜底导入 | import-finding |
| import_local | 导入本地材料 | import-local |
| show_status | 查看任务状态 | show-status |
| export_review | 导出 Excel 复核表 | export-review |
| import_review | 导入 Excel 人工修订 | import-review |
| build_materials_report | 生成材料清单 HTML | build-report --type materials |
| build_feasibility_report | 生成可行性报告 HTML | build-report --type feasibility |
| build_standard_delivery | 生成 V2 标准三件套交付 | build-standard-delivery |
| verify_delivery | 验证交付物和业务就绪状态 | verify-package |
| show_manual_review | 查看待人工复核清单 | show-status |
| package_task | 打包任务目录 | package-task |

动作执行前，如果任务范围、关键词、地区、时间范围、专利范围或文献范围尚未确认，应先向用户给出选项确认。

## 动作门禁

- `run_full_pipeline`、`run_delivery_pipeline`、正式 `run_scenario` 执行前，必须完成 `confirm_search_profile`。
- `run_scenario` 执行 `nmpa_competitor` 时只生成或刷新人工检索计划，不启动旧 HTTP、Edge CDP 或 Playwright collector。
- `record_nmpa_manual_search` 必须逐项匹配检索计划的查询词、注册类别和官方入口；未记录完全部必要 attempt 时保持 `awaiting_user_search`。
- `import_nmpa_manual` 必须核对同一检索会话、精确结果数、每项可见截图/官方导出及 SHA-256。未上传或导入不完整时保持 `awaiting_import`，不得写成 `no_results`。
- NMPA 只有在全部必要 attempt 均有结构化记录和可见证据、结果数全部为 0 时，才允许 `verified_no_results`；正向结果必须通过专用清单落库后才能完成。
- `build_standard_delivery` 前必须已有材料、证据卡、场景状态和标准审阅表；否则只能生成草稿，并在报告首页显示缺口。
- `verify_delivery` 是最终回复前的必要动作；最终回复必须说明 `delivery_artifacts_ready`、`v21_assets_ready`、`final_review_ready`、`scenario_coverage_ready`、`search_profile_ready`、`fallback_ready`、`network_ready`、`source_quality_ready` 和 `business_ready`。
- `fallback_import_finding` 只能导入可回溯公开来源或用户合法提供材料，不得导入搜索摘要本身作为强证据。
- `fallback_import_finding` 导入的 NMPA 信息仅是待复核线索，不能替代专用人工导入或关闭 NMPA 来源。
