# 文档导航

> 本文件是 `docs/` 的唯一入口。当前版本 **0.2.0rc4**（生产运行中，2026-10-30 上线）。
> 只想知道"现在能做什么、怎么跑"，先看仓库根目录的 [README.md](../README.md)。

本目录分四类文档，**只有第一类是当前有效规范**，其余仅供追溯：

| 类别 | 位置 | 是否代表当前状态 |
| --- | --- | --- |
| 当前有效规范 | `docs/*.md` | 是 |
| 历史证据（阶段验收、性能、实验结论） | `docs/archive/` + 根目录少数被引用文件 | **否** |
| 过期规划（已完成 / 被替代 / 取消） | `docs/archive/` | **否** |
| 临时资料（一次性交接、排障、清单） | `docs/archive/` | **否** |

---

## 一、按任务找入口

| 你要做的事 | 先读 |
| --- | --- |
| 了解系统怎么跑、模块在哪、有哪些不变量 | [ARCHITECTURE](ARCHITECTURE.md) |
| 改采集、加来源、核对覆盖缺口 | [SOURCES](SOURCES.md) → [SOURCE_COVERAGE](SOURCE_COVERAGE.md) → [PRODUCT_SCOPE](PRODUCT_SCOPE.md) |
| 改分类、精选、API 投影 | [CLASSIFICATION_REVIEW](CLASSIFICATION_REVIEW.md) → [TAXONOMY_V1](TAXONOMY_V1.md) → [FEATURED_POLICY](FEATURED_POLICY.md) |
| 改 RSS 发布或订阅消费 | [AUTO_PUBLICATION](AUTO_PUBLICATION.md) → [RSS_V1](RSS_V1.md) → [RSS_CONSUMER](RSS_CONSUMER.md) |
| 改 LLM 或异步分析 | [LLM_AUTOMATION](LLM_AUTOMATION.md) → [ASYNC_ANALYSIS](ASYNC_ANALYSIS.md) → [LLM](LLM.md) |
| 改标注或评测口径 | [QUALITY_BENCHMARK](QUALITY_BENCHMARK.md) → [LABELING](LABELING.md) → [FEATURED_LABELING](FEATURED_LABELING.md) |
| 部署、升级、回滚、排障 | [DEPLOYMENT_PREPARATION](DEPLOYMENT_PREPARATION.md) → [SERVER_DEPLOYMENT](SERVER_DEPLOYMENT.md) → [M9_OPERATIONS](M9_OPERATIONS.md) |
| 查"这件事验证到什么程度、属于哪类证据" | [VALIDATION](VALIDATION.md) |
| 查"现在做到哪、下一步做什么" | [PLAN](PLAN.md) |
| 查某个历史阶段的结论和原始证据 | [archive/README](archive/README.md) |

---

## 二、当前有效规范：每份文件维护什么事实

原则：**同一事实只在一个文件里维护**，其他文件引用而不复述。

| 文档 | 维护的事实 | 不负责（见对应文档） |
| --- | --- | --- |
| [ARCHITECTURE](ARCHITECTURE.md) | 运行形态、模块职责、HTTP 路由清单、全局不变量 | 各子系统细节 |
| [PRODUCT_SCOPE](PRODUCT_SCOPE.md) | 产品范围、覆盖分母、分批顺序 | 具体栏目 URL |
| [SOURCES](SOURCES.md) | 来源目录、已实现栏目、访问与转载条件 | 覆盖统计数字 |
| [SOURCE_COVERAGE](SOURCE_COVERAGE.md) | 机器生成的覆盖台账（单位 / 栏目 / 缺口） | 产品优先级 |
| [CLASSIFICATION_REVIEW](CLASSIFICATION_REVIEW.md) | 当前分类复核口径、v4 API 与 RSS v2 契约 | 基础 taxonomy 字段 |
| [TAXONOMY_V1](TAXONOMY_V1.md) | `/api/notices/v2` 与 `taxonomy_version=1` 字段契约 | 当前 v4 投影 |
| [ANALYSIS_CONTRACT](ANALYSIS_CONTRACT.md) | AnalysisInput / NoticeAnalysis / HumanLabel 类型与脱敏边界 | 队列与预算 |
| [ASYNC_ANALYSIS](ASYNC_ANALYSIS.md) | 异步队列、认领、预算预留、超时与恢复 | 模型 Prompt / Schema |
| [LLM](LLM.md) | LLM 客户端配置、预览与对照评测 CLI | 自动化流水线 |
| [LLM_AUTOMATION](LLM_AUTOMATION.md) | 当前自动分类 / 精选流水线运行契约 | 模型调用细节 |
| [AUTO_PUBLICATION](AUTO_PUBLICATION.md) | `/feed.xml` 等自动发布契约、失效门禁 | 参数化订阅 |
| [RSS_V1](RSS_V1.md) | `/rss/v1/all.xml`、`/rss/v1/featured.xml` 契约 | v2 契约 |
| [RSS_CONSUMER](RSS_CONSUMER.md) | 定向 RSS 消费与简报输出契约 | 发布端 |
| [STUDENT_PAGE](STUDENT_PAGE.md) | 学生页、只读 JSON API、前端边界 | 数据语义 |
| [LAN_PREVIEW](LAN_PREVIEW.md) | 局域网只读预览的启用条件与操作 | 部署设计 |
| [DEPLOYMENT_PREPARATION](DEPLOYMENT_PREPARATION.md) | 部署拓扑、资源预算、备份与回滚**设计** | 现网状态 |
| [SERVER_DEPLOYMENT](SERVER_DEPLOYMENT.md) | 现网部署、运行、日志、启停**状态** | 操作顺序 |
| [M9_OPERATIONS](M9_OPERATIONS.md) | 升级 / 回滚 / 停止的**操作步骤** | 设计理由 |
| [QUALITY_REPORT](QUALITY_REPORT.md) | 离线只读质量报告口径与边界 | 评测指标 |
| [QUALITY_BENCHMARK](QUALITY_BENCHMARK.md) | 多维标注与离线评测工具契约 | 精选规则 |
| [FEATURED_POLICY](FEATURED_POLICY.md) | 精选规则与阈值 | 标注流程 |
| [FEATURED_LABELING](FEATURED_LABELING.md) | 精选人工基准与标注契约 | 综合标注 |
| [LABELING](LABELING.md) | 人工标注 CSV 字段与导入 / 评测口径 | 入门操作 |
| [LABELING_BEGINNER](LABELING_BEGINNER.md) | 首次标注的逐步操作 | 字段契约 |
| [SIMPLE_LABELING](SIMPLE_LABELING.md) | 单文件标注页使用说明 | 评测口径 |
| [PLAN](PLAN.md) | 当前状态、未完成事项、里程碑索引 | 阶段明细 |
| [VALIDATION](VALIDATION.md) | 验证索引与证据分级定义 | 验证明细 |

---

## 三、历史证据在哪、怎么找

- **`docs/archive/`**：按里程碑和主题归档的阶段报告、验收记录、实验结论、一次性交接与排障记录。全部**逐字保留原文**，只在顶部加了一行归档声明。完整索引见 [archive/README](archive/README.md)。
- **根目录仍有少数历史文档**（见下表）。它们被 `deploy/` 下的发布清单或**开放中的 GitHub Issue** 直接引用，移动会破坏 `scripts/prepare_release.py` 的路径校验或 Issue 里的链接，因此本次**保留在原位**，但在下表中明确标注为历史。

| 保留在根目录的历史文档 | 为什么没归档 |
| --- | --- |
| [M3B8_ROUTE_DECISION](M3B8_ROUTE_DECISION.md) | README 与开放 Issue #26 引用；路线冻结决策本身仍有约束力 |
| [M8_RESULTS](M8_RESULTS.md) | 开放 Issue #22 引用 |
| [M9_SERVER_RESULTS](M9_SERVER_RESULTS.md) | `deploy/m9/nginx.conf.draft`、`deploy/m9/preparation-status.json` 引用 |
| [M9_COMPLETION_RESULTS](M9_COMPLETION_RESULTS.md) | `deploy/m9/preparation-status.json` 与开放 Issue #25 引用 |
| [M9_SOURCE_REVIEW](M9_SOURCE_REVIEW.md) | `deploy/m9/preparation-status.json` 与开放 Issue #25 引用 |
| [M10B_LLM_RESULTS](M10B_LLM_RESULTS.md) | `deploy/m10/release-files.json` 与开放 Issue #22 引用 |
| [M10C_LOCAL](M10C_LOCAL.md) | `deploy/m10/release-files.json` 引用 |
| [M10C_RESULTS](M10C_RESULTS.md) | `deploy/m10/release-files.json` 引用 |
| [RELEASE_0_2_0_RC1](RELEASE_0_2_0_RC1.md) | `deploy/m10/release-files.json` 引用 |
| [SECURITY_27_RESULTS](SECURITY_27_RESULTS.md) | `deploy/m10/release-files.json` 引用 |
| [RECOVERY_RESULTS_20261025](RECOVERY_RESULTS_20261025.md) | `deploy/m10/release-files.json` 引用 |
| [RECOVERY_RESULTS_20261027](RECOVERY_RESULTS_20261027.md) | `deploy/m10/release-files.json` 引用 |
| [RSS_FRESHNESS_FIX_20261025](RSS_FRESHNESS_FIX_20261025.md) | `deploy/m10/release-files.json` 引用 |
| [ISSUE_AUDIT_20261027](ISSUE_AUDIT_20261027.md) | `deploy/m10/release-files.json` 引用 |

**引用这些历史文档时，必须按 [VALIDATION](VALIDATION.md) 的证据分级说明它属于哪一类证据**（离线模拟 / 真实外网采集 / 真实服务器验证 / 生产运行 / 人工评测）。离线或合成环境的"通过"不等于真实环境通过。

---

## 四、仅供追溯、不代表当前状态

- `docs/archive/` 下全部文件。
- 上表中的 14 份根目录历史文档。
- [sdk-4255-source-manifest.json](sdk-4255-source-manifest.json)：AstrBot SDK 4.25.5 的只读哈希清单，被 `scripts/validate_cdu_sdk_source.py` 直接读取，因此留在根目录；它属于已冻结的 AstrBot 路线证据。

---

## 五、维护约定

1. **不为每次功能开发新增永久 Markdown 报告。** 普通任务用 GitHub Issue、PR 和提交记录追溯即可。
2. **只有需要长期维护的事实才建文档**：契约、口径、运行手册、产品约束。一次性的验收过程写进 Issue / PR 描述。
3. **历史报告只归档、不删除。** 归档时逐字保留原文，顶部加一行归档声明，并在 [archive/README](archive/README.md) 登记。
4. **新增或移动文档时同步更新本文件的索引**，并保证 `docs/` 内相对链接有效。
5. 文档里不要写入真实凭据、服务器地址、群号或个人数据；确需记录时用占位符。

   当前使用的占位符：`SERVER_HOST`（本项目服务器地址）、`CDU_HOST`（学校公网地址）、`QQ_GROUP_ID`（群号）、`<credential>.dpapi`（本机凭证文件）、`/home/<user>`（服务器用户名）、`api.example.com`（第三方模型网关）。**不要把真实值写回仓库。**
