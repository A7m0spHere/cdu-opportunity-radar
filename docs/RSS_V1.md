# 参数化 RSS v1（Issue #13）

`serve-rss` 新增 `/rss/v1/all.xml` 与 `/rss/v1/featured.xml`。前者包含全部通过原文质量检查的信息，含未分析项；后者仅包含指定 M7 pipeline 已完成分析、经当前精选规则复核的入选信息。**featured 与 current 是独立维度**，重要历史新闻也可以入选。

这些是当前服务的相对路径，不是已上线的公网地址。服务仍只绑定环回地址；现有 LAN 预览只新增同样的明确只读路由，不改变启动许可、精确设备白名单或网络范围。未执行生产部署、学校采集或真实模型调用。

## 筛选与规范路径

```text
/rss/v1/featured.xml
/rss/v1/featured.xml?source=all&category=all
/rss/v1/all.xml?source=computer&type=competition&topic=ai%2Ccomputing
/rss/v1/featured.xml?view=history
```

前两种请求输出字节相同，规范路径为 `/rss/v1/featured.xml`，对应用户选择的 featured/all/all。规范参数顺序沿用 `source,type,topic,status,view,category,published,from,to`，省略 `all` 与空日期，主题按代码排序。RSS channel 扩展字段返回规范路径；JSON v3 的 `subscription.url` 返回当前筛选对应的相对路径。`available=true` 表示接口路径已提供，不代表精选分析已经就绪；最终可读性始终以 RSS 当前门禁为准。

| 参数 | 合同 |
| --- | --- |
| `source` | `all` 或当前目录已验证、适配器已实现的稳定发布单位 ID；按最终发布单位筛选，不按发现/转载栏目 |
| `category` | `all` 或既有一级分类中文值，如 `学科竞赛`；不改作主题标签 |
| `type`、`topic` | 与 [分类契约](TAXONOMY_V1.md) 相同；多主题逗号分隔取交集，拒绝重复及 `all,ai` |
| `status`、`view` | 原文报名状态与 current/history/all 列表；路径已经指定 all/featured，不接受 `selection` 或 `view=featured` |
| `published`、`from`、`to` | 来源日期有无及有效日期区间，不使用聚合/入选时间代替来源日期 |

条件取交集，先筛选完整合格集合再取最新条目；固定最多 100 条，channel 中 `total/returned/truncated` 明示是否截断，不静默宣称全量。`all` 按首次聚合时间降序、`featured` 按首次入选时间降序，GUID 作稳定的同时间排序。没有无限 limit、任意 URL、SQL、动态抓取目标或筛选组合缓存。

路径最多 2048 字符、查询最多 1024、参数最多 9 个、每值最多 100。未知/重复/空参数、非法百分号或 UTF-8、非法枚举和日期组合均为 400，错误不回显输入值。非精确路由 404，写方法 405。

## 内容、身份与时间

RSS 2.0 UTF-8，标准 `title/link/source/category/description/guid/pubDate`。摘要是已有原文连续节选，XML 与内嵌 HTML 分层转义并移除非法控制字符；精选附原文入选依据，资格、报名、附件未知保持明确，不使用模型自由生成摘要。

所有视图沿用最终官方 URL 派生的稳定 GUID；原文更新、重分析、筛选与重新入选不生成新 GUID。旧 `/feed.xml`、`/current.xml`、`/history.xml` 语义与首次聚合 `pubDate` 不变，`/approved.xml` 继续 410。

新 `all` 的 `pubDate` 仍为首次聚合登记时间。新 `featured` 的 `pubDate` 为全局 `first_featured_at`：后台首次把该 GUID 持久化为入选时登记，所有筛选组合共用，退出/重新入选和重启都不覆盖。来源发布日期另用 `source_published_date`（缺失为 unknown），两者不得混用。

私有 `<state-dir>/subscriptions.sqlite3` 保存三张独立表：`metadata` 绑定原始库路径/契约，`first_featured` 保存全局首次时间，`collection` 保存当前分析集合及有效性指纹。后台短写事务一次提交首次时间与集合；HTTP 不写任何数据库，不迁移原始表、分析队列或人工标签/审核历史。新精选时间从本版本首次持久化入选开始，不倒推历史模型完成时间。

**备份和恢复应包含同一 state-dir 中的订阅状态库。** 切换到全新 state-dir 是新建订阅身份台账，会丢失旧首次入选时间，不能称为保留身份的迁移。损坏/不匹配的状态库失败关闭，不自动重置首次时间。重启不信任旧集合，先重新验证再持久化。状态文件与 SQLite 日志符号链接被拒绝，不通过 HTTP 暴露状态目录。

## 每次响应的有效性

所有派生路由先走 `AutoFeedController.read_gate`：现行原文质量、官方来源目录、内容版本、72 小时核验、回滚日志和内存核验存活时间都适用。原文更新/撤回/目录撤销/到期先 503，后台刷新后才反映新的合格集合。

精选再读取指定私有队列，并重新校验 input/pipeline 版本、引用、事实及当前规则。原文计划指纹、数据模式、pipeline、精选策略、相关任务结果/状态/租约/更新时间共同组成集合指纹；与后台持久化集合不一致时 503 并唤醒后台，不回退旧结果。模型结果变化即使尚未改变公开决定，也须先重建集合。HTTP 不触发采集、worker、Provider、费用或付费重试。

原始库、分析队列与订阅状态均使用只读回滚事务，保持到响应写出完成，使变更不能在旧快照响应中途提交；新精选拒绝队列/订阅库 WAL，不替用户修改日志模式。队列或精选状态的锁定/损坏/缺失只使精选 503，全部信息与旧 Feed 继续走自身原文门禁。全局最多 8 个处理连接，单客户端 socket 超时 5 秒，门禁锁等待 0.1 秒，私有队列与订阅库不等待 SQLite 锁。

| 状态 | 响应 |
| --- | --- |
| 全部信息健康、筛选为空 | 200 合法空 RSS |
| 配置有效且当前筛选分析完整、没有入选项 | 200 合法空 RSS |
| 当前筛选有部分明确决定，仍有 pending/failed | 200，description 明示覆盖不完整，channel 提供 `analysis_state/coverage`；未入选不表示不重要 |
| 未配置分析、首次集合未就绪、当前非空筛选尚无任何明确决定 | 503，不能伪称没有重要信息 |
| 当前配置与队列健康、当前筛选的原文集合为空 | 200 合法空 RSS |
| 原文/数据库/集合失效，或输出超过 1 MiB | 503，禁用旧内容/旧 304，`Retry-After: 1` |
| 请求速率耗尽 | 429，`Retry-After: 1`；两个新路由及所有参数组合共用单进程桶，容量 60、每秒补 1；旧 Feed 不占此桶 |
| 8 个处理连接已占满 | 503，`Retry-After: 1`，不继续创建线程 |

所有成功、空、错误与 304 均 `Cache-Control: no-store, max-age=0`。ETag 为最终输出字节 SHA-256；只有**本次完整门禁通过后**才能响应条件请求的 304。不使用 CDN、stale-if-error、Cookie、Origin 或 Referer 作为普通阅读器前提；不信任转发头。LAN 代理继续保留已有跨站和设备限制，新增路由透传 400/429/503 以及已验证的 304。

阅读器已缓存的历史条目无法远程删除；服务只保证后续响应不泄露失效快照，使用者仍须核对官方原文。100 条窗口之外不承诺历史补拉。

## 页面与复现

页面显示当前 all/featured、current/history 及全部筛选对应的同源绝对链接，支持打开订阅、复制及剪贴板不可用时手动选择。加载、原文失效、断网和页面恢复核验期间清空旧链接并禁用复制；页面的链接只适用于当前可访问的服务，不宣称公网可用。

沿用 `serve-rss --analysis-state <M7队列> --analysis-config <当前配置>` 只读接入已有分析；只启动 HTTP 不会执行 LLM，也不会给配置模型 Key。无需模型的全部订阅可直接用普通 `serve-rss`。本轮软件回归使用隔离合成库和 offline Mock，未更改用户真实原始库或队列。

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_rss_v1.py
```

部署及 Linux 原生/真实手机验收分别保留在 #14、#6；本合同不授权公网监听或生产操作。

本轮测试、安装包与浏览器证据见 [本地验收记录](RSS_V1_RESULTS.md)。
