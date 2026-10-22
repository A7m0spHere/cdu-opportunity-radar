# M10-C：短时本机闭环与服务器阶段交接

2026-10-22 用户范围：本机真实采集只尝试 2–3 轮；本轮执行 3 轮。真实 48 小时运行留到服务器阶段。本机脚本没有持续调度器，也不连接服务器。

## 固定版本与隔离状态

本轮基线为 `c8c17dfe8e0a258900bcdee584eeece0ee6c581c`；新增脚本的提交状态以 Git 为准。每次验收必须使用全新目录，不读取或改写已有业务库。一个验收目录下分别保存：

| 路径 | 用途 |
| --- | --- |
| `live/notices.sqlite3` | 本轮真实通知、来源健康、复查队列及首次发布登记 |
| `live/jobs.sqlite3` | 独立分析任务库；本轮为空，不加载配置或密钥 |
| `live/publisher/` | 发布器独占锁及状态；有有效精选时才产生精选状态库 |
| `live/consumer.sqlite3` | 消费者的订阅身份、GUID 水位和草稿记录 |
| `live/round-N.json` | 每轮采集结果、请求数、故障和只读探针 |
| `live/result.json` | 三轮汇总与真实发布器进程重启结果 |
| `private.log` | 私有请求/错误日志，不加入 Git |

脚本只采用已实现且已验证的来源。每轮最多 48 个实际 HTTP 请求，其中发现阶段最多 24 个，剩余供到期原文复查使用；每来源最多新增 1 篇、最多复查 2 篇、列表最多 1 页。请求间隔至少 2.5 秒，超时 10 秒，自动重试为 0。180 秒后设置停止标记，在下一次请求/任务检查时退出，不声称能中断正在执行的网络读取。

## 运行与停止

```powershell
# 合成采集、环回服务和故障注入：官网请求 0。
.\.venv\Scripts\python.exe scripts/m10_local_acceptance.py --directory artifacts/my-m10c-faults

# 真实采集：仅接受 2 或 3，执行完自动停止。不得自动补跑第四轮。
.\.venv\Scripts\python.exe scripts/m10_local_acceptance.py --directory artifacts/my-m10c-live --live-rounds 3
```

真实轮次共享同一隔离库与持久游标，每轮关闭并重新打开数据库/发布器。结束时另启一个**采集间隔为 0** 的发布器子进程：初始化消费者 → 终止该进程 → 同端口重启 → 核验水位和首次时间。这个进程恢复验证不会增加采集轮次。只终止脚本创建且保留句柄的子进程，最后检查自己的端口已关闭。

如果三轮报告已落盘而恢复阶段中断，可仅续做恢复：`python scripts/m10_local_acceptance.py --directory artifacts/my-m10c-live --resume-recovery`。该模式不调用采集器，保留原有水位和端口身份；已有最终结果时拒绝覆盖。恢复前后比较只读事务内全部表数据、schema、首次时间和水位，不以运行中 SQLite 文件的字节布局判断业务数据一致性。进程退出后有界等待端口释放，再开始下一次启动。

真实数据不注入 Mock 模型结果。全部 RSS 与 JSON 应一致；没有真实分析结果时，精选 RSS 的 503 是明确未就绪，不能解释为“没有重要通知”。合成链路单独验证采集、规则、合成分析、精选、JSON、全部/精选 RSS 的一致性，不将其称作真实精选效果。

## 一次只读观测

需要手动查看已有隔离库时，先使用现有 `serve-rss` 命令启动环回发布器，`--collect-interval 0` 禁用真实采集。探针本身不会启动服务或采集，也不会加载模型配置。

```powershell
.\.venv\Scripts\python.exe -m cdu_radar serve-rss --db artifacts/my-m10c-live/live/notices.sqlite3 --state-dir artifacts/my-m10c-live/live/publisher --port 8765 --collect-interval 0

# 在另一终端执行；--output 必须是尚不存在的文件。
.\.venv\Scripts\python.exe scripts/m10_probe.py --raw-db artifacts/my-m10c-live/live/notices.sqlite3 --state-dir artifacts/my-m10c-live/live/publisher --analysis-db artifacts/my-m10c-live/live/jobs.sqlite3 --consumer-db artifacts/my-m10c-live/live/consumer.sqlite3 --base-url http://127.0.0.1:8765 --output artifacts/my-m10c-live/manual-probe-001.json
```

探针以 SQLite `mode=ro` 和只读事务访问状态，缺库/锁库报告 unavailable，不创建替代库。HTTP 只接受字面量 `127.0.0.1`，不跟随重定向或读取代理，每个响应最多 2 MiB。记录健康检查最大年龄、72h 超期量、到期复查量/最长积压、任务状态和用量预留数、消费者水位数量，以及 JSON/RSS 的 200/429/503 和 GUID 一致性。截断或不可用时一致性为 null，不以空数组判为通过。

资源字段中的内存是**探针所在进程**的当前工作集；独立运行探针时不能代替发布器的内存。服务器阶段应另采服务进程/服务单元的内存、CPU、磁盘和重启计数。

## 故障与恢复判据

| 事件 | 本机核验 |
| --- | --- |
| SQLite 独占锁 | 实际 HTTP 返回 503，不返回旧正文或 304；释放锁、刷新后恢复 |
| 分析排队/精选覆盖不足 | 全部 RSS 可读；精选 RSS 返回 503，JSON 保留覆盖缺口 |
| 限流 | 实际 HTTP 返回 429，不立即重试 |
| 原文连续失败 | 合成原文两次失败进入退避，旧原文退出合格集合；恢复后重新核验 |
| 72h 原文超期 | 仅推进注入时钟 73 小时；旧响应先 503，刷新后排除过期原文 |
| 复查积压 | 记录到期条数、最长积压及失败次数，不把积压当成采集成功 |
| 停止/恢复 | 首次 GUID/pubDate、首次入选、非空合成用量记录、水位不被重置；没有重复草稿 |

停止所有写者后才备份。现有 `scripts/backup_state.py` 处理 raw/jobs/subscriptions；新增消费者 `consumer.sqlite3` 需要另外保存一致副本，现有备份工具不会自动包含它。原目录和旧用量保留，恢复至新目录；消费者订阅身份包含发布器地址/端口，改端口会触发新的首次初始化，不等同原身份续接。

## 后续服务器真实 48 小时

该阶段本轮**未执行**。迁移前固定包含本轮脚本的提交/wheel、Python 版本、独立目录、数据库和状态清单；确定服务器及请求/资源预算、采集间隔、准确起止时间后再启动。模型默认关闭。不能用本机模拟时钟替代真实经过时间。

运行开始、每次采集后、计划观察点和结束时保存唯一命名的探针结果；外部记录服务进程资源和停止/重启事件。累计 HTTP 状态、来源成功/失败、实际请求预算、队列积压、健康年龄、精选覆盖和同范围 GUID 差异。服务或数据库不可用时保存故障，不用旧 Feed 填补。

到期先停止采集与其他写者，保存任务/用量、首次时间、水位和最终报告，再关闭服务并确认端口释放。服务器阶段的通过判据是实际连续经过至少 48 小时、故障与恢复可追溯、预算未越界和数据新鲜度/投影一致性结果明确；只有成功 HTTP 响应不足以通过。恢复与回滚继续遵循 [M9 运维](M9_OPERATIONS.md)。
