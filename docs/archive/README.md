# 历史归档

> 本目录存放**历史证据、过期规划和临时资料**。所有文件**内容逐字保留归档时的原文**，只做了两件事：顶部加一行归档声明；把相对链接路径更新到归档后的位置。
> **这里的内容不代表当前实现或当前状态**；当前规范见 [docs/README.md](../README.md)。

归档日期：2026-11-03。归档原因：M2–M10 各阶段报告、验收记录和一次性交接记录与当前规范混放在 `docs/` 根目录，干扰当前开发。本次只做位置迁移，**未删除任何文件、未修改任何历史结论、数量或证据边界**。

## 证据类别

每份文件在归档时标注了它记录的是哪一类证据。引用前请先确认类别，**离线模拟通过不等于真实环境通过**：

- **离线模拟 / 离线测试**：Mock、合成数据、裁剪 HTML、注入时钟。证明代码路径与契约，不证明真实站点或真实模型可用。
- **真实外网采集**：真实 GET 或真实模型 API 调用。证明当时真实可达与解析正确，不证明长期稳定。
- **真实服务器验证**：隔离实例、实机切换。证明部署与回滚可行，不证明公网放行。
- **生产运行**：线上实例、连续运行、真实投递回执。证明线上行为。
- **人工评测**：真人标注、人工核对。证明效果指标，样本量有限。

完整的分级定义和"哪类证据能证明什么"见 [VALIDATION.md](../VALIDATION.md)。

---

## 一、M0–M2：规则分析 → LLM 对照 → 人工审批

| 文件 | 日期 | 内容 | 证据类别 |
| --- | --- | --- | --- |
| [VALIDATION_M0_M2](VALIDATION_M0_M2.md) | 2026-10-13 ~ 10-14 | M0–M2-B.1 本机验证总账（逐字归档） | 混：离线测试 + 真实模型调用 + 人工评测 + 真实外网采集 |
| [M2B1_OFFLINE](M2B1_OFFLINE.md) | 2026-10-14 | 31 条历史失败阶段归因与离线修复 | 混：离线测试 + 历史真实调用复盘 |
| [M2B2_TEST_PLAN](M2B2_TEST_PLAN.md) | 2026-10-14 | 两关真实协议探针计划（已执行） | 计划（编写时 0 次 POST） |
| [M2B2_RESULTS](M2B2_RESULTS.md) | 2026-10-14 | 首个真实协议探针（Markdown 围栏失败） | 真实外网采集（真实模型 API） |
| [M2B2A_JSON_COMPAT](M2B2A_JSON_COMPAT.md) | 2026-10-14 | JSON 围栏兼容离线修复 | 离线测试（新增真实 POST 为 0） |
| [M2B2B_TEST_PLAN](M2B2B_TEST_PLAN.md) | 2026-10-14 | 单条探针批准计划（已执行） | 计划 |
| [M2B2B_RESULTS](M2B2B_RESULTS.md) | 2026-10-14 | 单条真实探针结果与用量 | 真实外网采集（真实模型 API） |
| [M2B3_OFFLINE](M2B3_OFFLINE.md) | 2026-10-14 | 字段级降级离线修复 | 离线测试（0 次真实请求） |
| [M2C_RESULTS](M2C_RESULTS.md) | 2026-10-14 | 人工审批发布流程离线验收 | 离线测试 |
| [PUBLICATION](PUBLICATION.md) | 2026-10-14 | 人工审批发布设计（**已退役**，`/approved.xml` 返回 410） | 离线验收 |

## 二、M3：AstrBot 插件与只读 RSS HTTP 路线（最终 NO-GO）

> 结论：生产 AstrBot 插件安装与独立 OneBot 自动发送均为 **NO-GO**，除非未来获得新的实质证据。当前有效决策见 [M3B8_ROUTE_DECISION.md](../M3B8_ROUTE_DECISION.md)。

| 文件 | 日期 | 内容 | 证据类别 |
| --- | --- | --- | --- |
| [M3A_RESULTS](M3A_RESULTS.md) | 2026-10-14 | 只读 RSS HTTP 与安全刷新验收 | 离线测试 + 本机环回 |
| [RSS_HTTP](RSS_HTTP.md) | 2026-10-14 | M3-A 只读 RSS HTTP 接口说明（**已被自动发布取代**） | 离线 / 环回 |
| [M3A_TEST_GROUP](M3A_TEST_GROUP.md) | 2026-10-14 | 测试群联调操作手册 | **未执行** |
| [M3B_PREFLIGHT](M3B_PREFLIGHT.md) | 2026-10-15 | 真实联调前预检与授权清单 | **未执行**（仅本机预检） |
| [M3B1_ISOLATION_DESIGN](M3B1_ISOLATION_DESIGN.md) | 2026-10-15 | 三容器隔离设计（**已被 M3-B.4 最小插件方案替代**） | 设计（未执行） |
| [M3B4_MINIMAL_PLUGIN](M3B4_MINIMAL_PLUGIN.md) | 2026-10-15 | 专用最小消费插件方案 | 设计（离线） |
| [M3B4_RESULTS](M3B4_RESULTS.md) | 2026-10-15 | 最小插件离线验收与本地资源测量 | 离线测试 |
| [M3B5_REVIEW](M3B5_REVIEW.md) | 2026-10-15 | 交付审查与五份未执行的生产操作申请 | 离线审查 |
| [M3B6_SDK_READONLY](M3B6_SDK_READONLY.md) | 2026-10-15 | 生产 SDK 只读核验（未加载插件、未执行钩子） | 真实服务器验证（只读） |
| [M3B7_DEPLOYMENT_DECISION](M3B7_DEPLOYMENT_DECISION.md) | 2026-10-15 | 基于 M3-B.6 证据的部署决策 | 复用前序真实服务器只读证据（本轮 0 连接） |
| [ASTRBOT_HANDOFF](ASTRBOT_HANDOFF.md) | 2026-10-15 | 一次性 Agent 测试群交接清单 | **未执行** |

## 三、M4–M5：自动发布 → 学生页 → 来源试点 → 桌面验收

| 文件 | 日期 | 内容 | 证据类别 |
| --- | --- | --- | --- |
| [M4B_RESULTS](M4B_RESULTS.md) | 2026-10-15 | 自动聚合发布验收 | 混：离线测试 + 真实历史数据回放（本轮未联网） |
| [M4C_RESULTS](M4C_RESULTS.md) | 2026-10-15 | 学生通知页验收 | 离线测试 + 本机浏览器 |
| [M4D_RESULTS](M4D_RESULTS.md) | 2026-10-15 | 有限体验准备与一次受控真实采集 | 混：真实外网采集 + 离线回归 |
| [M5A_RECHECK_CI](M5A_RECHECK_CI.md) | 2026-10-16 | 原文复查队列与跨平台离线 CI | 离线测试 |
| [M5B_RESULTS](M5B_RESULTS.md) | 2026-10-16 | 学生处 / 团委两个新来源真实实采 | 真实外网采集 |
| [M5C_DESKTOP_RESULTS](M5C_DESKTOP_RESULTS.md) | 2026-10-17 | 电脑端阶段验收 | 离线 / 本机（非真实服务器） |

## 四、M6–M8：多维分类 → 异步分析 → 精选与参数化 RSS v1

| 文件 | 日期 | 内容 | 证据类别 |
| --- | --- | --- | --- |
| [M6_RESULTS](M6_RESULTS.md) | 2026-10-16 | 来源覆盖治理与多维分类验收 | 离线测试 + 本机浏览器 |
| [M7_RESULTS](M7_RESULTS.md) | 2026-10-16 | 私有异步分析合成离线验收 | 离线模拟（0 次真实模型请求） |
| [MERGE_REVIEW_FIXES](MERGE_REVIEW_FIXES.md) | 2026-10-16 | PR #17/#18/#19 合并审查修复与回归 | 离线测试 |
| [RSS_V1_RESULTS](RSS_V1_RESULTS.md) | 2026-10-17 | 参数化 RSS v1 本地验收 | 离线测试 + 本机浏览器 |

## 五、M9–M10：部署准备、连续运行与生产上线

| 文件 | 日期 | 内容 | 证据类别 |
| --- | --- | --- | --- |
| [M9_PREPARATION_RESULTS](M9_PREPARATION_RESULTS.md) | 2026-10-17 | 本机部署准备离线验证 | 离线测试 |
| [M10_RESULTS](M10_RESULTS.md) | 2026-10-21 | 定向 RSS 消费与本机交付验收 | 离线模拟（合成环回） |
| [M10C_SERVER_RESULTS_20261029](M10C_SERVER_RESULTS_20261029.md) | 2026-10-29 | 生产服务器连续运行 51 小时 5 分验收 | **生产运行** |
| [RECOVERY_RESULTS_20261030](RECOVERY_RESULTS_20261030.md) | 2026-10-30 | rc4 上线与复查 503 修复 | 混：生产运行 + 服务器 Linux 全量 / 压测 |

## 六、来源接入

| 文件 | 日期 | 内容 | 证据类别 |
| --- | --- | --- | --- |
| [TEACHING_SOURCE_RESULTS](TEACHING_SOURCE_RESULTS.md) | 2026-10-18 | 教务处公告接入的有界实采 | 真实外网采集 |

## 七、计划与架构的历史正文

| 文件 | 内容 |
| --- | --- |
| [PLAN_HISTORY](PLAN_HISTORY.md) | 原 `docs/PLAN.md` 的 M0–M10 阶段计划与结果摘要（逐字归档）。当前计划见 [PLAN.md](../PLAN.md) |
| [ARCHITECTURE_HISTORY](ARCHITECTURE_HISTORY.md) | 原 `docs/ARCHITECTURE.md` 的 M0–M4-D 里程碑增量记录（逐字归档）。当前结构见 [ARCHITECTURE.md](../ARCHITECTURE.md) |

---

## 还有哪些历史文档不在本目录

14 份历史文档因被 `deploy/` 下的发布清单或**开放中的 GitHub Issue** 直接引用，本次保留在 `docs/` 根目录。清单和原因见 [docs/README.md 第三节](../README.md#三历史证据在哪怎么找)。它们同样是历史证据，不代表当前状态。

## 归档约定

- 归档文件**不删除、不改写**，只在顶部加归档声明。
- 新增归档时在本文件登记：文件、日期、内容、证据类别。
- 归档文件内部链接已按新位置修正；引用归档文件时请用相对路径。
