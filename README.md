# CDU Opportunity Radar · 校园机会雷达

[![Offline CI](https://github.com/A7m0spHere/cdu-opportunity-radar/actions/workflows/offline-ci.yml/badge.svg)](https://github.com/A7m0spHere/cdu-opportunity-radar/actions/workflows/offline-ci.yml)

面向成都大学学生的公开官方信息聚合工具。把普通通知、学院新闻、学术活动、比赛与科研实践放在一处浏览，按发布单位、类型、主题和日期/报名状态自主筛选，每条信息保留官方原文。长期目标与分批边界见[产品范围](docs/PRODUCT_SCOPE.md)。

**可信来源自动采集 · 网页直接阅读 · RSS 订阅 · 无需逐条人工审批**

Python 3.11+ · [AGPL-3.0-only](LICENSE) · 本地 MVP，有限体验准备阶段

[快速开始](#快速开始) · [信息与接口](#信息与接口) · [当前边界](#当前边界) · [文档](#文档) · [许可证](#许可证)

## 目前能做什么

- **浏览通知**：默认展示全部合格信息，支持发布单位、类型、主题标签、来源日期、报名状态与当前/历史范围筛选，兼容手机与桌面布局；普通公告、非 AI 和未分类内容不会被采集层丢弃。
- **核实来源**：展示官方发布单位、原文链接、有依据的来源日期，以及可追溯的原文节选。
- **自动更新**：可启用定时采集；按最终原文 URL 去重，内容更新后刷新 RSS 与网页，保持 GUID 和首次聚合发布时间稳定。
- **保留不确定性**：缺少报名依据时明确显示“报名状态未确认”；当前机会可以为空。
- **阻止无效内容**：来源未验证、解析异常、原文失效或撤回的信息不作为有效通知发布；服务故障返回 503，网页隐藏旧数据。

当前为本地 MVP 与有限体验准备阶段。离线回归覆盖 **807 项**，分平台通过/跳过及 CI 结果见[M6 验证记录](docs/M6_RESULTS.md)；既有 M5-B 试点接入学生处、团委两个栏目，实采新增 **6 篇独立原文、6 篇合格信息、0 篇新增参与候选/明确开放证据**。M6 本轮不扩抓来源；有界样本不代表全校覆盖率或当前机会总数。

## 快速开始

需要 **Python 3.11+** 和 Git。Windows 实际验证环境为 Python 3.12.10；Linux 命令已提供，原生运行仍待验证。

```sh
git clone https://github.com/A7m0spHere/cdu-opportunity-radar.git
cd cdu-opportunity-radar
```

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .

# 先创建本地采集库；示例仅采集计算机学院，最多尝试 3 篇
.\.venv\Scripts\python.exe -m cdu_radar collect --db data/student.sqlite3 --source computer --limit 3

# 启动网页和 RSS，同时启用定时采集
.\.venv\Scripts\python.exe -m cdu_radar serve-rss --db data/student.sqlite3 --state-dir data/student-http --source computer --collect-interval 3600
```

### Linux / Bash

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m cdu_radar collect --db data/student.sqlite3 --source computer --limit 3
.venv/bin/python -m cdu_radar serve-rss --db data/student.sqlite3 --state-dir data/student-http --source computer --collect-interval 3600
```

打开 **<http://127.0.0.1:8765/>** 即可阅读通知，无需安装 RSS 阅读器。按 **Ctrl+C** 停止服务。

首次 `collect` 需要访问官方站点；来源失败时查看终端中的失败类别，不将失败当作“没有通知”。没有合格通知时，网页显示正常空状态。

### 采集与刷新

| 参数或操作 | 含义 |
| --- | --- |
| `--collect-interval 3600` | 服务启动后立即采集一轮，此后每轮结束等待 3600 秒再采集 |
| 省略 `--collect-interval` | 仅展示已有数据库；新入库内容仍由发布器刷新 |
| `--source computer --source msys` | 选择多个既有来源；定时采集仅允许已验证且已实现适配器的来源 |
| `serve-rss` 省略 `--source` | 对所有既有已验证、已实现适配器的来源启用采集；仍需非零采集间隔 |
| `serve-rss --limit 3` | 每轮每来源最多尝试 3 篇，当前定时采集只读取第一页 |
| 独立 `collect --max-pages 2 --limit 6` | 在所选来源支持分页时，按上限执行一次有界采集 |

网页约每 5 秒获取一次数据，这与访问官方站点的采集间隔分别控制。默认自动路径不调用 LLM，不需要模型 Key、QQ、AstrBot 或 NapCat。

另有独立的[私有异步分析 worker](docs/ASYNC_ANALYSIS.md)，支持离线 Mock、预算、重试和证据校验；它不参与网页/RSS 读取，不自动决定精选，本轮未调用真实模型。复现与边界见 [M7 验收](docs/M7_RESULTS.md)。

## 信息与接口

```text
既有可信官方来源 → 采集与解析 → 分类、去重及质量检查 → 网页 / RSS
```

| 路径 | 内容 |
| --- | --- |
| `/` | 学生通知页，默认最新通知 |
| `/api/notices` | 同源只读 JSON，与 RSS 共用质量和失效门禁 |
| `/feed.xml` | 全部通过质量检查的聚合通知，包含未知状态和历史信息 |
| `/current.xml` | 有明确时间依据、符合规则条件的可能参与机会，具体资格仍需核对原文 |
| `/history.xml` | 明确截止、已举办等历史信息；历史不等于报名已截止 |
| `/health` | 服务当前能否提供有效数据快照 |
| `/approved.xml` | **410 Gone**，旧人工批准 Feed 已退役 |

报名状态分为“可能仍可参与”“明确已经截止”“报名状态未知”。未知不会自动变成开放或已截止；规则已识别为资格不符合的通知不进入当前机会。自动分类只供浏览，不代表发布单位的官方认定。

**时间字段各有含义：** 来源发布日期取自有依据的网页元数据，缺失就显示未知；系统采集时间表示最近成功解析；RSS `pubDate` 表示首次聚合发布登记时间。后两者都不能替代原文发布日期。

## 当前边界

- **来源覆盖**：当前实现并验证了 11 个采集栏目。来源目录中的候选栏目、可访问页面和 `pending` 单位不等于可自动发布来源。[来源清单](docs/SOURCES.md)
- **采集覆盖**：定时任务有文章和页数上限，尚无完整的跨轮补采与旧通知复查调度；集中新增或较早通知更新可能未被覆盖。
- **有效性**：最近成功原文检查超过 72 小时的通知停止公开，等待重新采集。数据库中的历史记录保留；历史列表也受有效性检查约束。
- **内容理解**：摘要为原文节选，日期与资格采用保守规则。附件仅记录名称和链接，尚未读取其正文；多赛道或不明确的条件保持未知。
- **访问方式**：核心服务只监听环回地址，`127.0.0.1` 仅供服务所在设备访问。另有须显式启用、按具体设备 IP 限制的[局域网预览方案](docs/LAN_PREVIEW.md)。
- **验证范围**：Windows 本机、手机尺寸视口和故障隐藏已验证；真实手机跨设备、Linux 原生及长期无人值守运行尚未验收。目前没有生产部署或公网体验站。

使用本地 SQLite 回滚日志模式，服务拒绝 WAL；数据库与服务状态目录分开放置。不要把仓库、数据库或日志目录作为静态网站公开。正文链接由浏览器直接访问官方网页，本服务不自动报名、登录或收集学生个人资料。

## 开发与验证

安装测试依赖并运行离线回归：

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m pytest -q
```

Linux 将解释器替换为 `.venv/bin/python`。常规测试使用隔离数据库、裁剪 HTML、Mock 与环回 HTTP，不访问外网或调用付费模型。`requirements-lock.txt` 是已验证环境的依赖快照，不代表所有平台均已通过。

PR 与 main push 的离线 CI 覆盖 Windows / Ubuntu、Python 3.11 / 3.12，并从安装后的 wheel 检查 CLI 与网页静态资源。CI 不替代 Linux 实机、手机或生产长期运行验收。[原文复查机制与验证说明](docs/M5A_RECHECK_CI.md)。

复现实采可使用 `scripts/validate_trial_live.py --directory artifacts/trial-new`，须通过上述虚拟环境 Python 从仓库根目录执行，输出目录必须不存在。**该脚本会访问既有官方来源**；测试数据库、日志与采集结果留在被 Git 忽略的本地目录。真实 HTML 夹具的裁剪和出处见 [样本说明](tests/fixtures/README.md)。

离线只读报告：`python -m cdu_radar quality-report --db <数据库路径> --format markdown`，默认输出 72 小时窗口与全库质量快照，不联网、不迁移数据库。历史数据加 `--as-of` 和 `--data-mode offline_replay`；输出字段、未知状态和精确延迟不可计算的边界见 [质量报告](docs/QUALITY_REPORT.md)。两个新增栏目的有界实采与扩展前后比较使用 [M5-B 验证脚本](scripts/validate_sources_trial.py)，默认只预览，显式 `--run` 才联网。

提交问题时，请提供来源 URL、失败类别、复现命令和必要的脱敏输出。通知类别或日期解析修复应补充边界样例；不要提交数据库、完整学生名单、凭据或私人日志。

## 文档

| 主题 | 入口 |
| --- | --- |
| 学生页运行、JSON 契约与离线预览 | [STUDENT_PAGE](docs/STUDENT_PAGE.md) |
| 自动发布、来源与时间状态 | [AUTO_PUBLICATION](docs/AUTO_PUBLICATION.md) |
| 局域网预览、手动验证与停止 | [LAN_PREVIEW](docs/LAN_PREVIEW.md) |
| 最近真实采集和测试证据 | [M4-D 验收](docs/M4D_RESULTS.md) |
| 只读质量报告与校级来源试点 | [质量报告](docs/QUALITY_REPORT.md) · [M5-B 结果](docs/M5B_RESULTS.md) |
| 产品覆盖、多维分类与筛选 | [覆盖计划](docs/PRODUCT_SCOPE.md) · [目录盘点](docs/SOURCE_COVERAGE.md) · [v2 契约](docs/TAXONOMY_V1.md) · [M6 验收](docs/M6_RESULTS.md) |
| 架构、来源目录与开发计划 | [架构](docs/ARCHITECTURE.md) · [来源](docs/SOURCES.md) · [计划](docs/PLAN.md) |
| 历史分析和发布设计 | [分析契约](docs/ANALYSIS_CONTRACT.md) · [旧审批设计](docs/PUBLICATION.md) · [路线冻结决策](docs/M3B8_ROUTE_DECISION.md) |

M2–M3 文档保留用于理解历史数据和实验，不是当前启动指南。旧审批确认写入、`serve-approved-rss` 已停用；旧 `export-rss` 只作技术诊断，不能替代带失效保护的自动 Feed。QQ 插件和独立 OneBot 发送路线继续冻结。

## 许可证

Copyright (C) 2026 CDU Opportunity Radar contributors.

本项目原创源代码与文档采用 **GNU Affero General Public License v3.0 only（`AGPL-3.0-only`）**，完整条款见 [LICENSE](LICENSE)。允许使用、修改、再分发和商用；分发时应保留相关声明并满足对应源码等义务。修改版本通过网络向用户提供交互服务时，应显著、免费地向这些用户提供实际运行版本的对应源码获取方式。

部署修改版时，可在网页中设置“本版本源码”入口，指向包含自身修改的对应版本源码，并提供必要的构建、安装和运行脚本。生产数据库、密码和学生个人数据不作为源码公开。项目按许可证条款提供，不附带保证。

官方通知原文、测试夹具中的第三方网页片段、校名标识与第三方依赖不因本项目采用 AGPL 而被重新授权；各自的权利与使用条件仍需分别遵守。样本出处见 [HTML 样本说明](tests/fixtures/README.md)。本项目不代表成都大学或各发布单位的官方服务，通知详情与参与条件以官方原文为准。
