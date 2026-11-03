# 系统结构（当前）

> 本文件只描述**当前**运行形态、模块职责、HTTP 路由与全局不变量，不记录里程碑增量。
> 里程碑历史（M0–M4-D）已归档在 [archive/ARCHITECTURE_HISTORY.md](archive/ARCHITECTURE_HISTORY.md)（内容逐字保留，仅链接路径按归档位置更新）；各子系统细节见 [文档导航](README.md) 与下方契约索引。

## 运行形态

- 单一 Python 包 `cdu_radar`（Python ≥3.11，AGPL-3.0-only）。CLI 入口 `cdu-radar`，定义在 `pyproject.toml` 的 `[project.scripts]`（`cdu_radar.cli:main`）。
- 核心服务 `serve-rss`：一个进程同时提供只读 RSS/XML、学生页静态资源与只读 JSON API。默认只绑定 `127.0.0.1`；`make_server` 与 `serve-rss` 都拒绝非环回绑定。
- 可选 `serve-lan-preview`：默认不监听，需显式 RFC1918 地址、精确设备白名单和 `--enable-lan`；只做只读转发，不读数据库或本地文件（[LAN_PREVIEW.md](LAN_PREVIEW.md)）。
- 后台 `--analysis-worker`：`serve-rss` 内可选启动的异步分析 worker，按私有队列与预算调用模型，每次请求为可终止子进程（[ASYNC_ANALYSIS.md](ASYNC_ANALYSIS.md)、[LLM_AUTOMATION.md](LLM_AUTOMATION.md)）。
- 采集由 `collect` 命令或 `serve-rss --collect-interval`（≥60 秒）触发，不依赖 HTTP 请求。

## 数据流

```text
collect → http.Fetcher → adapters → storage.Store → notice_health
        → auto_publish / classification / taxonomy
        → rss_service / review_api / student_page

异步（--analysis-worker）：
async_queue → async_worker → llm_* → analysis_store（私有库；原始库只读）
```

HTTP 只读内存或数据库快照：请求路径不触发采集、不调用模型、不写数据库。

## HTTP 路由

| 路由 | 说明 |
| --- | --- |
| `/feed.xml`、`/current.xml`、`/history.xml` | 自动聚合 RSS（[AUTO_PUBLICATION.md](AUTO_PUBLICATION.md)） |
| `/rss/v1/all.xml`、`/rss/v1/featured.xml` | 参数化 RSS v1（[RSS_V1.md](RSS_V1.md)） |
| `/rss/v2/all.xml`、`/rss/v2/featured.xml` | 分类复核 RSS v2（`review_api.RSS_ROUTES`，[CLASSIFICATION_REVIEW.md](CLASSIFICATION_REVIEW.md)） |
| `/api/notices` | JSON v1（兼容保留） |
| `/api/notices/v2` | taxonomy v1 投影（[TAXONOMY_V1.md](TAXONOMY_V1.md)） |
| `/api/notices/v3` | 精选双视图 v3（[STUDENT_PAGE.md](STUDENT_PAGE.md)） |
| `/api/notices/v4` | 当前分类复核 JSON（`review_api.API_V4`，[CLASSIFICATION_REVIEW.md](CLASSIFICATION_REVIEW.md)） |
| `/health` | 就绪状态；未就绪返回 503 |
| `/approved.xml` | 410，人工审批发布已退役 |

v1/v2/v3 为兼容保留；当前学生页与订阅使用 `/api/notices/v4` 与 `/rss/v2`。学生页静态资源由同一服务提供（[STUDENT_PAGE.md](STUDENT_PAGE.md)）。

## 模块职责

| 子系统 | 模块与职责 |
| --- | --- |
| 采集 | `http.py` 抓取/robots/域名校验；`adapters.py` 列表与正文解析；`collect.py` 有界采集编排；`recheck.py` 原文复查；`sources.py` 加载来源目录；`storage.py` SQLite 存储与关联 |
| 规则与分类 | `rule_analysis.py` 保守规则预筛；`rule_projection.py` 静态规则事实投影；`classification.py` 版本化校园分类；`taxonomy.py` 规则派生筛选维度；`featured.py` 重要精选策略；`undergraduate.py` 本科生阅读范围 |
| 自动发布 | `auto_publish.py` 确定性聚合与失效门禁；`auto_service.py` 自动 RSS/JSON 服务与 503 读门禁 |
| RSS | `rss.py` XML 转义与旧导出；`rss_service.py` 环回只读 HTTP 与后台发布；`rss_v1.py` 参数化 RSS v1；`rss_synthetic.py` 独立合成实验库；`rss_consumer.py` RSS 到简报草稿；`subscription_store.py` 订阅私有状态与首次时间 |
| 学生页/API | `student_page.py` 同源页面与公开快照；`review_api.py` v4 JSON 与 RSS v2；`review_contract.py` 仅分类的模型复核契约；`featured_api.py` v3 精选投影 |
| 异步分析 | `async_queue.py` 私有队列（原始库只读）；`async_worker.py` 可终止单请求 worker；`async_contract.py` 私有建议契约；`async_cli.py` 本地显式 CLI；`managed_worker.py` 有界监督与私有健康；`analysis_store.py` 追加式分析与标签存储；`analysis_models.py` 分析类型契约；`analysis_service.py` 证据校验与编排 |
| LLM | `llm.py` OpenAI 兼容传输；`llm_schema.py` 固定 Prompt/Schema；`llm_workflow.py` 请求台账与缓存对照；`llm_semantics.py` 保守事实守卫；`llm_screening.py` 初筛契约；`llm_diagnostics.py` 只读历史诊断；`llm_json.py` 严格 JSON 解析；`llm_cli.py` CLI 适配；`llm_debug.py` 可选失败证据；`credentials.py` DPAPI 凭证；`privacy.py` 离线脱敏；`evidence.py` 有界引用定位 |
| 质量与评测 | `quality.py` 离线只读质量报告；`quality_benchmark.py` 多维标注与缓存评测；`featured_benchmark.py` 分层精选基准；`evaluation.py` 人工标签指标；`labels.py` 标注 CSV 与快照身份；`simple_review.py` 离线人工复核 UI；`automatic_review.py` 证据约束的语义分类；`coverage.py` 来源覆盖台账 |
| 运维与安全 | `backup_state.py` 停机备份与恢复；`tls.py` 项目 CA 上下文；`lan_preview.py` 局域网只读转发；`discovery_failures.py` 列表候选冷却 |
| 前端 | `cdu_radar/web/index.html`、`app.css`、`app.js`、`favicon.svg`：`serve-rss` 同源静态资源，原生 DOM，无第三方脚本；`simple-review.html`：`simple_review.py` 生成的离线单文件复核页模板 |

## 全局不变量

1. 服务读门禁只接受 SQLite 回滚日志模式（DELETE/TRUNCATE/PERSIST），拒绝 WAL；数据库与服务状态目录分离，状态目录拒绝非空或跨库（[AUTO_PUBLICATION.md](AUTO_PUBLICATION.md)、[DEPLOYMENT_PREPARATION.md](DEPLOYMENT_PREPARATION.md)）。
2. HTTP 读事务保持到响应发送完成；失效、来源不健康或快照过期一律 503 并清空旧内容，不返回旧 XML、不 304 回退（[AUTO_PUBLICATION.md](AUTO_PUBLICATION.md)、[RSS_V1.md](RSS_V1.md)）。
3. 请求路径不触发采集、不调用模型、不写数据库（[LLM_AUTOMATION.md](LLM_AUTOMATION.md)、[STUDENT_PAGE.md](STUDENT_PAGE.md)）。
4. 原文最近一次成功检查超过 72 小时的通知停止公开（[AUTO_PUBLICATION.md](AUTO_PUBLICATION.md)）。
5. RSS GUID 与首次发布时间持久稳定，重复导出不产生新条目（[AUTO_PUBLICATION.md](AUTO_PUBLICATION.md)、[RSS_V1.md](RSS_V1.md)）。
6. 人工审批发布已退役：`/approved.xml` 返回 410，审批写入 CLI 停用，旧表只读（[AUTO_PUBLICATION.md](AUTO_PUBLICATION.md)）。
7. 来源白名单与正文路径白名单：未知域名或模板在请求前拒绝（[SOURCES.md](SOURCES.md)）。
8. 附件只记录名称与链接，不下载、不解析正文（[ANALYSIS_CONTRACT.md](ANALYSIS_CONTRACT.md)、[SOURCES.md](SOURCES.md)）。
9. 模型只接收脱敏数据、返回固定 Schema、无工具权限；模型失败不排除候选，回退规则（[LLM_AUTOMATION.md](LLM_AUTOMATION.md)、[ANALYSIS_CONTRACT.md](ANALYSIS_CONTRACT.md)）。
10. 只读公开页面：不自动报名、不登录、不采集学生个人名单作为公开数据源（[PRODUCT_SCOPE.md](PRODUCT_SCOPE.md)、[STUDENT_PAGE.md](STUDENT_PAGE.md)）。

## 子系统契约索引

| 子系统 | 权威文档 |
| --- | --- |
| 采集与来源 | [SOURCES.md](SOURCES.md) |
| 分类与 taxonomy | [TAXONOMY_V1.md](TAXONOMY_V1.md) |
| RSS v1 | [RSS_V1.md](RSS_V1.md) |
| RSS v2 | [CLASSIFICATION_REVIEW.md](CLASSIFICATION_REVIEW.md) |
| 自动发布 | [AUTO_PUBLICATION.md](AUTO_PUBLICATION.md) |
| 学生页与 API | [STUDENT_PAGE.md](STUDENT_PAGE.md) |
| 异步分析 | [ASYNC_ANALYSIS.md](ASYNC_ANALYSIS.md) |
| LLM 自动化 | [LLM_AUTOMATION.md](LLM_AUTOMATION.md) |
| 质量报告 | [QUALITY_REPORT.md](QUALITY_REPORT.md) |
| 标注与评测 | [QUALITY_BENCHMARK.md](QUALITY_BENCHMARK.md) |
| 部署与运维 | [DEPLOYMENT_PREPARATION.md](DEPLOYMENT_PREPARATION.md)、[SERVER_DEPLOYMENT.md](SERVER_DEPLOYMENT.md) |
| 局域网预览 | [LAN_PREVIEW.md](LAN_PREVIEW.md) |

## 历史

M0–M4-D 的里程碑增量记录已归档（内容逐字保留），仅作历史证据，不代表当前实现：[archive/ARCHITECTURE_HISTORY.md](archive/ARCHITECTURE_HISTORY.md)。
