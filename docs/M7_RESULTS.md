# M7 异步分析验收（Issue #10）

2026-10-16，依赖 PR #17 的 M6 提交 `8ebdab0`，独立分支 `codex/issue-10-async-analysis`。本轮没有合并 PR #17、扩大来源、修改原始数据或四份部署草稿。

已交付独立私有 SQLite 队列、只读原始库扫描、版本/输入幂等、预算预留、进程总超时、退避/恢复/租约隔离、可追溯模型建议和逐项确定性校验。使用现有 Python/httpx/SQLite，无新依赖。原始 HTTP/RSS 路径不读取队列，分析成功、失败或队列不可用不成为“全部”信息的发布条件。[完整契约与命令](ASYNC_ANALYSIS.md)。

本地 Windows / Python 3.12 全量 **852 passed、2 skipped（178.28 秒）**，跳过是既有两项本机符号链接权限限制，不计通过。M7 新增 **47 项**离线回归，最终功能版本专项 **47 passed（15.42 秒）**；完整覆盖共 854 项。最终提交的跨平台 CI 与 wheel 安装验收见关联 PR。

## 合成离线验收

`scripts/m7_offline_acceptance.py --directory artifacts/m7-final-v1` 已实际执行：6 条合成原文，3 succeeded、2 partial、1 skipped；6 次 Mock 尝试，其中 1 次模拟超时、退避后成功。原始库执行前后 SHA-256 一致，GUID/首次时间/历史表另有回归验证。`human-check.csv` 保留人工判定空列，没有伪造人工真值。

| 样例 | 已观察到的行为 |
| --- | --- |
| 普通教学通知 | 保留普通信息，明确未知报名，不依赖 AI |
| AI/编程竞赛 | 主办方报名截止冒充校内截止被拒绝；独立摘要和主题保留 |
| 科研讲座 | 有短引用的学术属性可保留；重要性仅推断、待效果评测 |
| 附件规则 | 附件名不能证明规则，正文未读，partial |
| 提示注入 | skipped，无工具执行或模型调用，原文仍留库 |
| 药学研讨会 | 首次模拟超时后按退避重试，最终完成 |

`budget-simulation.json` 单独标记 `synthetic=true`、`usage_origin=invented_test_numbers`、`actual_model_requests=0`，验证一请求预算耗尽及可知用量/价格估算。测试中的 10/5/15 Token 和单价不是实测收费。正常 Mock 账本保留费用 null。

本地结果在忽略目录 `artifacts/m7-final-v1/`。实际模型请求 **0**、校方请求 **0**、原始生产库写入 **0**、新增监听 **0**。没有读取或使用 API Key，没有付费验证、真实标签效果、原生手机/Linux 实机、部署或精选发布结论。最终测试数量、安装包验证和四组 CI 记录在关联 PR，避免 CI 后改动提交造成证据漂移。
