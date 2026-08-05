# 证据卡规则

- 所有材料都应生成证据卡。
- 一篇文献或一个材料条目通常对应一张证据卡。
- 事实必须有摘录、字段来源或文件来源。
- 推断必须绑定事实或证据卡。
- 建议必须来自证据缺口、失败项或用户目标。
- 没有正文、摘要、字段或摘录的证据默认不纳入报告。
- `include_in_report=true` 的证据卡必须包含 `key_excerpts`。
- 证据卡必须引用已登记的 `material_id`。
- LLM 只能写 staging 文件；正式 `data/evidence_cards.jsonl` 必须由 CLI 校验后提交。
- 搜索引擎、Exa 或其它发现服务返回的摘要必须标记为 `retrieval_kind=search_result`、`content_verified=false`，只能用于发现下一步目标。
- 只有实际读取网页正文、官方文档或用户合法提供的完整文件后，才能使用 `fetched_page` 或 `supplied_document` 并标记 `content_verified=true`。
- 正式报告结论必须登记为 `ResearchClaim`。`supported`、`partially_supported` 和 `disputed` 必须引用已登记证据卡；高影响支持性论断至少覆盖两个独立发布机构。
- `disputed` 论断必须关联 `EvidenceConflict`；冲突处理必须保留差异原因、解决状态和人工复核结果。
- 完整研究必须形成 `logs/research_iterations.jsonl`，达到来源覆盖后至少完成两个不同结构方向的零增量审计。
