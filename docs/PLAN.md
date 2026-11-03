# 开发计划

本文件只记录当前状态与近期计划；历史阶段计划与结果已归档到 [PLAN_HISTORY](archive/PLAN_HISTORY.md)（内容逐字保留，仅链接路径按归档位置更新），不再作为当前实现或当前状态的依据。

## 当前状态

- 版本 **0.2.0rc4**，2026-10-30 上线；发行、切换与回退边界见 [10 月 30 日部署记录](archive/RECOVERY_RESULTS_20261030.md)。
- 2026-10-29 完成 **51 小时 5 分**连续运行验收（rc3，同一进程、204 份快照跨度 51 小时 2 分）；rc4 上线后连续运行时间重新计算。见 [服务器连续运行验收](archive/M10C_SERVER_RESULTS_20261029.md)。
- 公网只读网页与 RSS 运行中：网页、全部/精选 RSS 与 JSON 共用受众范围、原文要点与有效分类。见 [服务器部署与观察](SERVER_DEPLOYMENT.md)。
- 数据库与服务状态目录分开放置；SQLite 使用回滚日志模式、拒绝 WAL。见 [自动发布](AUTO_PUBLICATION.md) 与 [学生页](STUDENT_PAGE.md)。

## 未完成 / 开放事项

以下按现有文档核实；服务在线不等于这些事项已通过。

- **#22 真实人工标注效果**：已有 10 篇 development 标注与离线评测，独立 holdout 仍无真人真值，代表性 precision/recall 与本科生误收/漏收未验收。见 [人工与模型对照](M10B_LLM_RESULTS.md)。
- **#24 下一批官方栏目**：常规采集仍为已有 12 个来源；新增 2–3 个栏目的列表/详情实采与同窗口增量比较未完成。见 [Issue 收尾核查](ISSUE_AUDIT_20261027.md)。
- **#25 公开 Beta 放行**：CDU 域名/主办主体、来源再分发与摘要许可、正式证书与续期、长期运行条件未齐；IP HTTPS 入口上的只读投影已上线，逐项 GO/NO-GO 收口与公开内容最小化抽检仍未完成。见 [来源与公开入口核查](M9_SOURCE_REVIEW.md)。
- **#26 MaiBot 联调与真实回执**：已有订阅、模型审核和少量真实回执；实质内容更新、撤回与 429 的完整真实联调证据仍不齐。见 [Issue 收尾核查](ISSUE_AUDIT_20261027.md)。
- **手机/LAN 现场体验与参与者反馈**：按用户决定暂缓，未执行不记为通过。见 [M9 完成记录](M9_COMPLETION_RESULTS.md)。
- **长期无人值守运行**：rc4 的连续运行时间从 2026-10-30 重新计算；长期容量、慢响应与真实证书续期未验收。见 [服务器部署与观察](SERVER_DEPLOYMENT.md)。

## 里程碑索引

各阶段证据类别（离线模拟/真实外网采集/真实服务器验证/生产运行/人工评测）见 [VALIDATION](VALIDATION.md)。

| 里程碑 | 日期 | 一句话结论 | 证据 |
| --- | --- | --- | --- |
| M0 | 2026-10-13 | 两真实栏目采集→SQLite→RSS 最小闭环通过；创新创业学院本机网络失败未接入 | [M0–M2 验证](archive/VALIDATION_M0_M2.md) |
| M1 | 2026-10-13 | 9 栏目真实采集与重复验证；42 单位/152 候选栏目；分页与跨学院原文归并 | [M0–M2 验证](archive/VALIDATION_M0_M2.md) |
| M2-A | 2026-10-13 | 规则分析、人工标签与评测 CLI；36 篇真实通知离线预测，无人工真值 | [M0–M2 验证](archive/VALIDATION_M0_M2.md) |
| M2-B | 2026-10-14 | 真实模型全量尝试 36 条，5 条通过校验、31 条失败；F1 未改善 | [M0–M2 验证](archive/VALIDATION_M0_M2.md) · [协议探针](archive/M2B2_RESULTS.md) |
| M2-B.1 | 2026-10-14 | 31 条失败按可观察阶段归因；新增付费请求 0 | [M2-B.1](archive/M2B1_OFFLINE.md) |
| M2-C | 2026-10-14 | 人工审批队列与 approved.xml 离线验收；36 条历史通知批准数 0 | [M2-C](archive/M2C_RESULTS.md) |
| M3-A | 2026-10-15 | 环回只读 RSS/health、守门与合成实验通过；Linux/插件/测试群未执行 | [M3-A](archive/M3A_RESULTS.md) |
| M3-B | 2026-10-15 | 生产 AstrBot 插件与独立 OneBot 自动发送均 NO-GO，路线冻结 | [最终路线决策](M3B8_ROUTE_DECISION.md) |
| M4-B | 2026-10-15 | 自动聚合发布与三条 Feed 离线回放通过；当前机会 0，未部署 | [M4-B](archive/M4B_RESULTS.md) |
| M4-C | 2026-10-15 | 学生通知页与同源只读 JSON 本机验收；16 篇回放，当前 0 | [M4-C](archive/M4C_RESULTS.md) |
| M4-D | 2026-10-15 | 9 栏目受控真实采集 17 原文/15 合格；本机只读转发通过；LAN/手机/Linux 未执行 | [M4-D](archive/M4D_RESULTS.md) |
| M5-A | 2026-10-16 | 原文复查队列、退避与四组离线 CI 落地；有界真实复查方案未执行 | [M5-A](archive/M5A_RECHECK_CI.md) |
| M5-B | 2026-10-16 | 学生处、团委两栏目接入，verified 来源增至 11；隔离副本实采 6 新增，无新增参与候选 | [M5-B](archive/M5B_RESULTS.md) |
| M5-C | 2026-10-17 | Windows 安装包、环回代理与桌面任务通过；手机/Linux/同学反馈未执行 | [M5-C](archive/M5C_DESKTOP_RESULTS.md) |
| M6 | 2026-10-16 | 42 单位/152 候选栏目覆盖快照与 v2 多维分类；未扩来源 | [M6](archive/M6_RESULTS.md) |
| M7 | 2026-10-16 | 独立异步分析 worker 与 Mock 离线验收；真实模型请求 0 | [M7](archive/M7_RESULTS.md) |
| M8 | 2026-10-16 | 重要精选规则、待标注表与双视图离线验收；人工真值 0 | [M8](M8_RESULTS.md) |
| M9 | 2026-10-17 | 共享服务器隔离实例 Linux/Nginx/TLS 与 A→B→A→B 切换通过；公网未放行，手机/LAN 暂缓 | [M9 服务器](M9_SERVER_RESULTS.md) · [M9 完成](M9_COMPLETION_RESULTS.md) |
| M10-A/B/C | 2026-10-21—10-29 | 离线消费与待标注包、10 篇开发集真人标注与 v3 真实模型、3 轮真实采集、服务器 51 小时连续运行依次完成；独立 holdout 人工效果与 #26 联调仍开放 | [M10-A](archive/M10_RESULTS.md) · [M10-B](M10B_LLM_RESULTS.md) · [M10-C](M10C_RESULTS.md) · [服务器验收](archive/M10C_SERVER_RESULTS_20261029.md) |

## 边界与不做

- **M3-B 路线**：生产 AstrBot 插件安装与独立 OneBot 自动发送均为 **NO-GO**，不启用自动 QQ 推送；见 [最终路线决策](M3B8_ROUTE_DECISION.md)。
- **手机/LAN**：现场体验与参与者反馈已暂缓，未执行不记为通过；见 [M9 完成记录](M9_COMPLETION_RESULTS.md)。
- **QQ 插件路线**：冻结；不以更多同类静态扫描、Mock 通过或版本号/哈希不变重新开启；见 [最终路线决策](M3B8_ROUTE_DECISION.md)。
- **产品边界**：不自动报名、不登录教务系统、不收集学生个人资料；通知详情与参与条件以官方原文为准。

## 历史

2026-11-03 文档治理：原 PLAN.md 的 218 行阶段流水账已归档到 [PLAN_HISTORY](archive/PLAN_HISTORY.md)（内容逐字保留，仅链接路径按归档位置更新）；本文件不再重复各阶段明细，历史证据只描述当时状态。
