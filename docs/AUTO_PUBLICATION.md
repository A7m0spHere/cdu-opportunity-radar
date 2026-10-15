# M4-B 自动聚合发布

面向成都大学学生，聚合官方通知中的竞赛、科研实践、校园活动和其他信息。流程是采集、解析、自动分类和去重、信息质量检查、RSS 自动发布。人工批准不是发布条件；规则只用于浏览分类与保守时间判断，不使用推荐评分筛选发布。

## 最小本地运行

Python 3.11+，安装 `pip install -e .`；开发验证安装 `pip install -e '.[dev]'`。只需既有 httpx、BeautifulSoup 和标准库，不需 Key、模型 SDK、AstrBot、NapCat 或 Agent 框架。

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe -m cdu_radar collect --db data/auto.sqlite3 --source computer --limit 3 --max-pages 1
.\.venv\Scripts\python.exe -m cdu_radar aggregate --db data/auto.sqlite3
.\.venv\Scripts\python.exe -m cdu_radar serve-rss --db data/auto.sqlite3 --state-dir data/auto-http --source computer --collect-interval 3600 --interval 10 --max-age 30
```

Linux 对应 `.venv/bin/python`，参数及相对路径相同。URI 使用 `Path.resolve().as_uri()`，临时文件和 XML 原子替换在同一目录；Windows 使用 msvcrt 锁，Linux 使用 fcntl 锁。中文、空格路径已在 Windows 测试；本轮没有 Linux 发行版可实际运行，不能据此声称 Linux 原生验收通过。

第一条命令建库并做有界采集；随后后台每小时采集所选来源，每 10 秒刷新 Feed。`--collect-interval 0`（默认）仅自动发布库内新增/变更，不自行联网；省略 `--source` 的定时采集只访问已有 9 个 verified 且实现解析的栏目，每栏默认最多 3 篇、1 页。定时采集最低间隔 60 秒，沿用 robots、TLS、可信域名、3 MiB 上限；timeout=12 秒、retries=0、请求间隔至少 2.5 秒，不下载附件。`--no-env-proxy` 可显式关闭环境代理。

Ctrl+C 结束服务和采集线程。服务仅绑定显式环回地址，数据库必须先存在，运行中删除库不会重建替代空库。使用本地 SQLite delete/truncate/persist 日志，WAL 拒绝服务；不会自动修改日志模式。状态目录必须专用且与数据库分开，不能复用旧批准服务目录。

## 接口与列表

| 路由 | 含义 |
| --- | --- |
| `/feed.xml` | 通过当前质量检查的所有聚合通知，含明确标记的历史/未知 |
| `/current.xml` | 有明确未到期报名截止、没有不确定时间约束且未识别为历史的“可能仍可参与”通知 |
| `/history.xml` | 明确截止、结果报道、已举办或超过保守历史窗口的通知 |
| `/health` | 200 ready 或 503 unavailable，不泄露数据库路径 |
| `/approved.xml` | 410，解释旧人工批准语义已退役，订阅者须主动改订 `/feed.xml` |

仅支持 GET/HEAD；其他路径 404，写方法 405。每次响应先核验数据库、版本、来源和时间状态；旧快照失效先 503，后台重建后才返回新内容。导出失败、数据库丢失/损坏/锁定、WAL、验证超过 max-age 均不返回旧 XML 或旧 ETag 的 304。仍然 no-store，不以静态缓存或代理文件绕过守门。

`aggregate --db ... [--as-of ...]` 输出合格 items、被阻止的 notice_id/reason，以及类别/状态/列表信息；可用来查看 `parse_failed`、`unavailable`、`withdrawn`、`source_check_expired` 等状态。它可以给旧库追加自动表，但不登记发布时间，不写旧审核表，不调用模型。

## 独立数据契约

保留原 `notices`、`notice_sources`、`notice_links`；每次采集成功后自动记录质量状态。追加的表使用独立 v1 自动聚合契约，不降低已有 `PRAGMA user_version`，不要求先建 v5 审批表。

| 表 | 字段及用途 |
| --- | --- |
| `notice_health` | notice_id、status、checked_at、validated_hash、reason；valid / parse_failed / unavailable / withdrawn |
| `auto_publications` | notice_id、唯一 guid、first_published_at；仅自动发布身份与时间，不含 approval/reviewer |
| `legacy_publication_invalidations` | review_id；仅保护历史批准不因原文恢复旧版本而复活，旧审批和事件行不改写 |

`Store` 迁移幂等，只追加表，不回填伪造的健康检查。旧库原始通知没有逐篇健康记录时标 `not_revalidated`，正常重新采集即可获得资格，不能复制审批、规则标签或一次虚假的当前时间绕过。历史验证脚本只在新副本回放既有 parser 输出和原始采集时间，明确标为离线回放。

每个自动 item 至少有 title、source_name、source_url、source_published_date（null 表示未知）、category、summary、summary_evidence、registration_status、listing、facts、audience、warnings。summary 是原始正文的连续 240 字以内节选；证据保存原 content_hash、正文字符起止和逐字 quote，可直接在存储正文中定位。

RSS 使用标准 title/link/guid/pubDate/source/category/description；扩展命名空间 `urn:cdu-radar:auto:v1` 中有 registration_status、listing、source_published_date、summary_evidence。description 用中文展开未知项、原文日期、类别、状态及可点击官方原文链接。XML 和内嵌 HTML 分层转义，清除非法 XML 控制字符。

GUID 是 `urn:cdu-radar:notice:` 加最终官方 URL 的 SHA-256；相同最终 URL 重采只更新一条，正文更新沿用 GUID。不同 URL 不按标题或语义强合并，避免误并不同轮次。pubDate 是首次聚合发布身份登记的带时区时间，磁盘写入失败重试/重启仍沿用；它不是原文发布日期、最新抓取时间或报名开始时间。原文更新刷新摘要、事实和 ETag，不产生新的发布时间。

## 来源、时间与异常

发现栏目和最终原文发布单位分别检查。自动发现入口限既有 verified、parser_supported、已实现 adapter 的栏目；最终域名也必须对应既有已验证单位，且存储单位/域名一致。不因 URL 含 `cdu.edu.cn` 就放行，不把转载栏目当成原文发布单位，不采用数据库中随意填写的来源名称。M4-A 的教务处转载虽有模板，其目录状态仍 pending，因此阻止自动发布。

标题、正文、官方原文 URL、可信单位、原文版本哈希必须有效。来源发布日期只采用解析器取得的详情或列表元数据；缺失为未知，不使用 discovered_at/checked_at 代替。未来或无效来源日期不发布。原始数据保留过去有依据的发布日期时，不将新抓取时间覆盖成发布日期。

类别为学科竞赛、科研与创新实践、AI / 计算机相关活动、校园活动、其他通知，仅供浏览。截止/报名开始只接受原文有明确年份、用途、单一日期且能独立定位的证据；多赛道、冲突、暂定、缺年不压成一个确定日期。已过校内报名/推荐截止也不推断仍可参与。缺资格、开始或活动时间明确标为未知；附件正文未读取，不能猜测附件条件。

报名状态分别为 possible（可能仍可参与）、closed（明确已经截止）、unknown（报名状态未知）。缺最终报名截止、仅有活动时间、只有未来报名开始、历史报道或不确定时间约束都不能推出开放报名。来源发布日期早于参考日 90 天的通知保守归历史；这只是展示窗口，不伪称“报名截止”。明确截止、已举办和获奖报道都不进当前机会。

逐篇请求失败/404/410 标 unavailable；详情解析失败标 parse_failed，保留旧正文作诊断并停止发布；恢复成功解析可自动恢复，身份/时间不变。原文明示“本通知已撤回/本活动取消”等标 withdrawn，成功重采也不自动解除撤回标记。栏目列表失败不等同每篇原文已撤回，不伪造逐篇失败；所有通知最近一次成功源检查超过 72 小时后都停止作为有效条目，等待重新采集。超过当前有界采集范围的旧通知也会自然失效，历史原始记录仍在库内。

HTTP 守门只能拒绝之后的过时响应；阅读器已经缓存的条目无法远程删除。每条仍保留原文和状态说明，以便阅读器使用者核对。

## 审批退役与 LLM

新路径从未要求 publication_reviews、publication_state、analysis_reviews、manual_labels，也不使用审核队列、确认指纹或历史规则评分。旧审核/标签/分析/审批记录和原批准 XML 均不删除、改写或转成自动批准。采集不再往旧 publication_events 添加 source_changed；独立失效标记保留旧批准的版本保护。

旧 `review list/show/history/template` 及未确认预览可只读查看历史；带确认的 approve/reject/defer/revoke/set、确认的 export-approved-rss 和 serve-approved-rss 返回 2，不再改审批状态。旧底层模块保留用于历史数据解释及隔离回归，不作为新产品入口。新 HTTP 对 approved.xml 返回 410，不将自动内容伪装成人工审核。

自动路径完全不执行 LLM；保留的 `llm` 独立实验命令仍需手动显式 --send，不是采集/发布步骤。规则辅助失败可降级为基础信息和未知状态；不会触发模型回退、付费重试或新 Agent。

## 验证与展示页建议

运行 `.venv/Scripts/python.exe -X utf8 -m pytest -q`（Linux 用 `.venv/bin/python`）。真实离线回放命令：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/validate_auto_publish.py --source-db '<M4-A worktree>/artifacts/m4a-local-v1/real-notices.sqlite3' --directory artifacts/m4b-new-run --as-of 2026-10-15T20:00:00+08:00
```

输出目录必须不存在；源库 mode=ro + SQLite backup，新副本才迁移/回放/提供短暂环回 HTTP，结束关闭。结果含源库哈希、历史行一致性、状态分布和 HTTP 消费；不访问校园站点，不把离线链接当成已重新验证可访问。

后续学生页建议只有标题/节选卡片、官方来源与原文按钮、类别筛选，以及“当前机会 / 报名未知 / 历史”切换。未知日期显示“未提供”，过期显示截止依据，原文更新显示提示。先共用服务端质量和时间契约，不引入审批后台、推荐评分榜或复杂前端；本轮只给建议。
