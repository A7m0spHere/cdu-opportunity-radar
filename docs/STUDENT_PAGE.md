# M8 全部与精选双视图

默认“全部信息”，可切换“重要精选”；原有最新/当前/历史仍独立，精选不等于报名开放。两视图共用相同筛选，精选卡片展开查看入选依据、原文短引用和未知边界，摘要继续是官方原文节选。未分析、依据不足、智能降级、正常空和原始质量 503 分开显示。

页面读取同源 `/api/notices/v3`，`selection=all|featured` 与 v2 所有原筛选轴取交集。v1/v2 原字段和三条旧 Feed 保持。v3 的 `featured` 覆盖按当前筛选命中的全部合格信息计算，然后才取精选子集；#13 已接入 `subscription.available=true` 及当前筛选的规范相对 URL，页面提供链接、复制与手动选择。当前/历史切换也同步到订阅条件；加载/原文故障时清空旧链接。精选尚未就绪时 RSS 返回 503，路径可复制不代表分析已就绪。页首“全部 RSS 订阅”仍指向 `/feed.xml`。[RSS v1 契约](RSS_V1.md)。

`serve-rss` 默认无模型分析，精选提示尚未就绪而非“没有重要通知”。可显式提供 `--analysis-state <M7私有队列> --analysis-config <非秘密配置>`，只读当前指定 pipeline 并重验已有建议，不读 Key、不调用模型。可选 `--featured-model-threshold` / `--featured-rule-threshold` 改阈值时会更换规则版本；真实效果仍须新人工评测。Mock 结果只能在明确 offline_replay 合成服务中展示。

更多：[规则](FEATURED_POLICY.md) · [标注与评测](FEATURED_LABELING.md) · [本轮边界](M8_RESULTS.md)。下文保留原接口历史说明。

## 历史：M4-C 学生通知页

## M6 增量：多维筛选

M6 阶段页面使用 `/api/notices/v2`，支持发布单位、信息类型、主题、来源日期、报名状态与兼容原分类的交集筛选。旧 `/api/notices` 保持 JSON v1 字段。未分类和模型未处理不会被全量视图删除，来源撤销/过期和数据库故障仍经 RSS 同一门禁，失败隐藏旧列表。规则只是浏览辅助，不作官方事实或开放保证；字段及严格参数见 [TAXONOMY_V1.md](TAXONOMY_V1.md)。界面布局、空/错态及 Chrome 桌面/手机尺寸验证见 [M6_RESULTS.md](M6_RESULTS.md)。下文保留 M4-C 的 v1 历史契约说明。

M4-D 已补充[独立局域网预览方案](LAN_PREVIEW.md)和[真实信息验证](M4D_RESULTS.md)。默认仍是本机环回，跨设备入口须用户显式启用；本轮未开启 LAN 或验证真手机连通。

打开网页即可看最新校园信息、判断报名状态并进入官方原文，不需 RSS 阅读器、账号或模型 Key。页面使用现有 Python 服务、原生 HTML/CSS/JavaScript；没有前端构建工具、第三方脚本、账号、收藏、评论、推荐算法或写入 API。

## 运行

在已有 Python 3.11+ 环境运行；首次安装用 `python -m pip install -e .`，测试用 `python -m pip install -e '.[dev]'`。

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe -m cdu_radar collect --db data/student.sqlite3 --source computer --limit 3
.\.venv\Scripts\python.exe -m cdu_radar serve-rss --db data/student.sqlite3 --state-dir data/student-http --collect-interval 3600 --source computer
```

随后打开 `http://127.0.0.1:8765/`。不需要额外启动 Web 服务。只浏览已有工作库时省略 `--collect-interval`；新通知入库后由原发布器刷新，网页每 5 秒检查一次。停止用 Ctrl+C。

Linux：

```bash
.venv/bin/python -m cdu_radar collect --db data/student.sqlite3 --source computer --limit 3
.venv/bin/python -m cdu_radar serve-rss --db data/student.sqlite3 --state-dir data/student-http --collect-interval 3600 --source computer
```

保持本地 SQLite 回滚日志，不使用 WAL，不将数据库放在状态目录内。Python 包包含全部网页资源，相对路径、中文及空格目录沿用 Path/SQLite URI 的跨平台处理。本轮 Windows 原生执行通过；没有可用 WSL Linux 发行版，Linux 原生运行未执行，不以源码或 wheel 检查代替。

## 页面与数据语义

默认是“最新通知”，按可核实的原文发布日期降序；原文未提供日期时只用首次收录时间作排序依据，并明确显示“日期未提供”。系统采集时间与原文发布分开显示，不把 RSS pubDate 当成原文发布日期。

三个视图与 RSS 共用集合：

| 页面视图 | RSS | 语义 |
| --- | --- | --- |
| 最新通知 | `/feed.xml` | 全部通过质量门禁的聚合信息，包含未知报名状态和历史通知 |
| 当前机会 | `/current.xml` | 有明确时间依据的“可能仍可参与”，不是每名学生资格都已确认 |
| 历史信息 | `/history.xml` | 已举办/结果报道、明确截止或落入保守历史窗口的信息 |

报名 possible 显示“可能仍可参与”，closed 显示“报名已截止”，unknown 显示“报名状态未确认”。历史与报名状态是两个维度，历史中的 unknown 不会改为已截止。当前机会 0、分类 0、整体合格数据为空均是正常空状态，和服务异常分开。

分类使用后端学科竞赛、科研与创新实践、AI / 计算机相关活动、校园活动、其他通知。修复了“教师科研项目申报”等行政标题因含科研/竞赛/AI 字样和未来截止而进入当前机会的问题；行政事务仍自动聚合为其他通知，未知报名不会假装已截止。原文单位 pending 的转载继续被阻止。

卡片呈现标题、来源、原文日期、类别、原文节选、报名状态、系统采集时间和官方原文按钮。未知资格/截止/AI 规则不在前端生成。摘要使用 textContent；来源链接保留官方 URL，直接新窗口打开，带 noopener/noreferrer。相同 GUID 或相同原文 URL 在页面去重，不强行合并不同 URL 的通知。

## 同源只读接口与安全边界

`GET/HEAD /api/notices` 返回 JSON v1，数据直接投影自与 RSS 相同的已核验内存快照。它在原 `read_gate` 内响应，不重新开一条绕过门禁的数据库查询路径，也不读取 XML 静态文件。GUID、内容、分类和状态与该时刻的 Feed 相同；前端只改变展示顺序和筛选。

顶层字段：schema_version、items、categories、counts、refreshed_at、data_mode、valid_for_ms、poll_after_ms。每条 item 仅含 guid、title、source_name、source_url、source_published_date（可为 null）、summary、category、registration_status、listing、source_checked_at、first_aggregated_at。不会公开原始全文、私有数据库路径、审核人、审批记录、标签、证据管理表或被阻止条目的详情。

`valid_for_ms` 是距离服务器快照核验失效的剩余时长，不是来源发布时间。前端减去请求用时，超过该时限即隐藏内容；收到 503、其他错误、超时或断网也清空卡片和旧时间，不作历史缓存回退。切到后台/从浏览器历史恢复会清空旧内容并重新核验。JSON 不使用 ETag/304，避免客户端沿用没有新核验时限的响应；始终 no-store。

现有响应锁、SQLite 回滚读事务、max-age、来源检查、撤回/失效、发布原子写失败与 503 规则继续有效。为避免页面请求与后台重复工作争用，将来源目录改为每次实时计划读取一次，并移除发布时两次多余的相同计划计算；仍在每次响应重新核验来源、内容版本和时间，未增加跨请求的静态状态缓存。

明确的路由白名单只有 `/`、`/app.css`、`/app.js`、`/favicon.svg`、`/api/notices`、三个现有 Feed、`/health`。`/approved.xml` 保持 410。未列出的目录、参数变体、私有文件、管理路径都为 404；POST/PUT/PATCH/DELETE/OPTIONS 为 405。静态首页不含通知数据，服务故障时仍可加载并展示不可用提示。没有 CORS 开放头，所有请求同源；提供 CSP、no-referrer、nosniff 和 same-origin 资源策略。

## 离线真实样本预览

开发依赖安装完成后，选择不存在的新输出目录运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/preview_student_page.py --source-db '<M4-A worktree>/artifacts/m4a-local-v1/real-notices.sqlite3' --directory artifacts/student-page-new --as-of 2026-10-15T20:00:00+08:00 --port 8766
```

打开 `http://127.0.0.1:8766/`。源库只读备份到新隔离目录，保留原文、来源日期和原采集时间；固定的是明确标示的离线参考时点。页面显示“离线样本预览”，不会伪称原文此刻已重新联网核验。预览不采集校园网站，不访问模型、QQ 或生产服务器。已准备的私有回放可通过同模块 `serve(directory, as_of, port)` 函数恢复，仍须匹配原回放模式与参考时点。

本轮 18 篇真实输入仍为 16 篇合格展示、11 篇历史、0 篇当前机会；pending 原文单位的两篇仍被阻止。15 篇报名未知、1 篇明确截止。详细验证、截图和 Git 清单见 [M4-C 结果](M4C_RESULTS.md)。
