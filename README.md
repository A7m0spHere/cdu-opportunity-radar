# cdu-opportunity-radar

成都大学官方通知采集器。M1 支持来源登记、受控采集和 SQLite/RSS；M2-A 支持规则预筛、人工标注及评测；M2-B 增加受控 OpenAI-compatible 模型分析和缓存对照评测。采集、存储、分析和发布独立，不依赖 AstrBot API。

**当前 Feed 仍是原始技术验证 Feed**，包括获奖新闻和普通通知；规则或模型预测不会自动改变 Feed 或批准发布。模型只在配置有效且显式 --send 时调用，没有 QQ 群订阅或公开发布。复杂日期语义、赛事级转载去重和完整学院采集仍属于后续阶段。

## 安装

要求 Python 3.11+；本机实际使用 Python 3.12.10。Windows PowerShell 无需激活虚拟环境：

```powershell
cd 'D:\AI项目\cdu信息聚合插件'
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
```

Linux：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

## 本地运行

### M2-B 模型分析

配置、预览、发送、缓存和对照评测见 [docs/LLM.md](docs/LLM.md)。配置从环境变量读取，默认预览、每批 3 条；API Key 不写入代码、Git 或日志。使用独立 `data/m2b.sqlite3`，保留原始通知和人工标签；对照命令只读取已有模型缓存，不发付费请求。

```powershell
.\.venv\Scripts\python.exe -m cdu_radar llm analyze --dry-run --output data/m2b-prompt-preview.json
.\.venv\Scripts\python.exe -m cdu_radar llm analyze --limit 3 --send --output data/m2b-batch.json
.\.venv\Scripts\python.exe -m cdu_radar llm compare --output data/m2b-comparison.json --errors-output data/m2b-errors.json
```

### M2-A 离线规则、人工标注与评测

本轮使用独立 `data/m2a.sqlite3`，由 M1 的 36 篇通知备份复制，原 M1 数据库保持不变。新环境先完成采集；已有 M1 数据可用 Python 标准库 SQLite backup 建副本：

```powershell
.\.venv\Scripts\python.exe -c "import sqlite3; from pathlib import Path; source=sqlite3.connect('file:data/m1.sqlite3?mode=ro',uri=True); Path('data/m2a.sqlite3').open('xb').close(); target=sqlite3.connect('data/m2a.sqlite3'); source.backup(target); target.close(); source.close()"
```

上述复制命令只用于第一次建立副本，目标文件已存在时会拒绝操作，保留已有分析和标签。

```powershell
.\.venv\Scripts\python.exe -m cdu_radar analyze --as-of 2026-10-13
.\.venv\Scripts\python.exe -m cdu_radar label export --as-of 2026-10-13
# 人工编辑 data/m2a-labels.csv，明确 label_status=human 后导入：
.\.venv\Scripts\python.exe -m cdu_radar label import --input data/m2a-labels.csv --labeler 本人
.\.venv\Scripts\python.exe -m cdu_radar evaluate --as-of 2026-10-13
.\.venv\Scripts\python.exe -m cdu_radar review list --as-of 2026-10-13
```

新命令默认 `--db data/m2a.sqlite3`，库不存在时报错，不生成伪空样本。`--as-of` 固定北京时间参考点，日期单值代表该日 00:00；省略时用当前北京时间。`--rules 文件` 可修改关键词配置，无需改 Python。

输出：`data/m2a-analysis.json`、`data/m2a-labels.csv`、`data/m2a-evaluation.json`。CSV 有明确独立的 `predicted_*` 和人工字段；新行是未标注状态。导出默认拒绝覆盖已有工作表，需要时用另一 output，或先导入再显式 `--overwrite`。**人工标签不等于审批，程序预测不等于人工真值。**

事实、推断、未知分别保存；只有明确年份和日期用途才进入时间字段。多赛道时间不强制归成一个日期，AI 可适用不等于规则允许，学院来源不等于所有本科生有资格。成功结果缓存同时考虑正文和完整输入；原文变化重新待审，旧人工结论留存。分析失败只保留错误类型并待审核。

首批 36 篇已由用户完成真实推荐标注并导入：9 条推荐、27 条不推荐。规则基线 Precision=31.58%、Recall=66.67%、F1=42.86%；资格、时间和 AI 政策等细分真值尚未填写。评测以“保留候选（retain + uncertain）”为正例，输出混淆矩阵、未确定比例和错例；不是推送批准率。完整字段和方法见 [分析契约](docs/ANALYSIS_CONTRACT.md) 与 [人工标注指南](docs/LABELING.md)。

### 原始采集与 RSS（保持兼容）

```powershell
.\.venv\Scripts\python.exe -m cdu_radar sources
.\.venv\Scripts\python.exe -m cdu_radar collect --limit 3 --max-pages 1 --no-env-proxy
.\.venv\Scripts\python.exe -m cdu_radar export-rss --limit 30 --output data/feed.xml
.\.venv\Scripts\python.exe -m pytest -q
```

默认采集 9 个已实现适配器的来源，每来源跨页最多尝试 3 篇正文，默认最多 1 页。默认数据库 `data/radar.sqlite3`；M1 会自动做兼容升级，保留原有记录。升级前建议用 SQLite backup 保留 M0 数据，本轮验证实际使用独立 `data/m1.sqlite3`，原库没有升级。重复运行刷新 `last_seen_at`，正文、标题、发布日期或链接/附件变化更新原记录及 `updated_at`。同一最终官方 URL 只保存一篇，保留多个发现栏目和实际原文发布单位。

可单独选择 `--source computer`、`business`、`innovation`、`msys`、`ftva`、`pharm`、`ele`、`jztm`、`sfxy`，`--source` 可重复指定。`--max-pages` 控制最大页数，`--limit` 控制每来源最大文章尝试数；未接入栏目不会被默认采集。运行 JSON 保持 M0 的列表结构并增加分类/耗时/分页统计，stderr 汇总成功/失败来源、新增、更新、重复、解析失败数及耗时。

M1 登记 42 个单位/平台、152 个候选栏目；152 个可访问、139 个发现文章链接，9 个完整采集验证通过。发现和访问成功不等于已实现解析，也不代表通知适合学生报名。

## 本机创新创业学院连接诊断与复现

本机系统 DNS 返回 `198.18.0.11` / `2001:2::2b`，公共 DNS 返回 `202.115.80.140`；系统路径 TLS 关闭，显式公共地址保持原域名 SNI 后证书校验和 HTTP 请求通过。环境代理变量为空，`--no-env-proxy` 无法改变透明网络层的 DNS。具体证据见 `docs/VALIDATION.md`，这是本机环境诊断，不能推广为所有设备的 DNS 状态。

```powershell
.\.venv\Scripts\python.exe -m scripts.diagnose_source --public-dns
.\.venv\Scripts\python.exe -m cdu_radar collect --db data/m1.sqlite3 --source innovation --limit 3 --max-pages 1 --no-env-proxy --resolve cxcyxy.cdu.edu.cn=202.115.80.140
.\.venv\Scripts\python.exe -m cdu_radar export-rss --db data/m1.sqlite3 --limit 50 --output data/m1-feed.xml
.\.venv\Scripts\python.exe -m scripts.validate_local --baseline data/m0-backup.sqlite3
```

DNS 地址可能变化，先运行诊断确认，不要长期依赖快照中的固定 IP。`--resolve` 可重复，只接受已登记域名和公网 IP，保留 TLS 验证，不修改系统 DNS。师范学院本机也需要显式解析覆盖，其本轮地址见验证记录。网络恢复后可省略覆盖；失败不会永久禁用来源，下一次 collect 仍会尝试。

`scripts` 维护工具需要在仓库目录执行；wheel 分发包含主采集 CLI 和机器来源配置。

所有子命令均支持 `--db 路径`。`--limit` 必须大于 0。退出码：0 成功；1 至少一个来源/文章采集失败（其他来源仍继续）；2 参数、文件或数据库错误。失败不会把空正文入库，也不会清除已存通知。来源状态和错误可用 `sources` 命令查询。

analyze 返回 1 表示至少一条分析失败；标签不足的 evaluate 返回 0，同时报告空指标和样本不足。它表示评测流程运行成功，不表示筛选效果通过。

请求默认采用环境代理；本机使用 `--no-env-proxy` 完成直连验证。需要代理时省略此参数。请求保留 TLS 证书验证，不降低 TLS 安全策略。

## 请求策略与数据

- User-Agent：`cdu-opportunity-radar/0.1 (local research; low-frequency notice collector)`。
- 每个请求阶段超时 20 秒；网络错误或 HTTP 502/503/504 最多额外重试 2 次，退避 2/4 秒。401、403、429 不重试。
- 全局请求间隔至少 2 秒，遵守 robots 的 Crawl-delay 和 Request-rate；最多跟随 5 次重定向，每个目标均限定为成都大学官方域名并核查 robots。
- robots 404/410 视为未提供规则；200 中解析可用规则，无规则（包括 HTML 维护页）会明确警告。robots 网络错误、5xx、401/403 均停止该站采集。使用标准库 `RobotFileParser`，并非完整 RFC 9309 实现。
- 图片、需要认证/验证码的页面不处理；纯图片或空正文明确失败。正文原始链接单独记录，附件仅保存名称/URL，不下载或解析 PDF/Office，不读取报名表或附件中的个人资料。
- `published_at` 是网页发布日期（仅日期）；`discovered_at` 是首次发现 UTC 时间；`application_deadline` 当前为空，不从正文日期猜测截止时间。RSS 对未知发布日期使用首次发现时间，并在摘要中写清楚；日期只有天级精度时，RSS 使用北京时间 00:00 表示。
- 数据库、Feed、日志和原始页面仅保存在本地，被 `.gitignore` 排除。`request_events` 记录 URL、状态码、请求时间、重试序号、耗时和错误；`collection_runs` 记录每来源运行结果。运行 JSON 写 stdout，请求日志写 stderr。

## 已实现、已验证与计划

来源清单和现状见 [docs/SOURCES.md](docs/SOURCES.md)，本机真实运行证据见 [docs/VALIDATION.md](docs/VALIDATION.md)。结构见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，M0 计划及下一轮需求见 [docs/PLAN.md](docs/PLAN.md)。常规 pytest 只使用裁剪 HTML 和 Mock，不访问互联网；来源网页可能随时变化，失败时请记录并重验。

若要复现本轮具体依赖版本，可在新虚拟环境先安装 `requirements-lock.txt`，再执行 `pip install --no-deps -e .`。这是本轮环境快照，不保证所有未来平台都完全兼容。
