# M0 系统结构

```text
CLI → 来源清单 → Fetcher → 栏目/文章 Adapter → Store (SQLite)
CLI export-rss → Store → RSS 2.0 本地 XML
```

`cdu_radar/sources.json` 是从官网院系设置建立的来源目录，不代表所有院系均已接入。`Source` 记录官网、栏目和验证状态；运行状态保存在数据库，目录同步不会覆盖近期采集错误。

`http.py` 共用 HTTPX 客户端，负责 User-Agent、超时、低频请求、有限重试、robots、官方域名限制、重定向及请求事件。`adapters.py` 只包含已观察的两种列表：计算机学院 `.div_list` 的文章行、商学院 `ul.jtfylb` 的 `li > a > p/span`。不是一套 CSS 选择器套用全校。正文共享已观察到的 VSB `form[name="_newscontent_fromname"]` 与 `vsb_content`/`vsb_content_N` 容器，但标题解析按模板区分。结构变化明确报错，不把网站导航或页脚当正文。

`collect.py` 对每来源及每文章分别隔离采集错误，只把正文验证通过的结果交给存储。通过文章最终 URL 选择对应站点正文适配器，未知站点报错。栏目和详情发布日期冲突时先报错；缺少详情发布日期时使用列表日期，不把正文中的报名截止、竞赛时间或公示时间混作发布日期。

`storage.py` 使用参数化 SQL、事务和外键，核心表为 `sources`、`notices`。`canonical_url UNIQUE` 保证最终 URL 唯一；`notice_sources` 记录一个文章 URL 被多个栏目发现的关联。文章的 `source_id` 保留最初来源，RSS 来源通过关联表取得。哈希仅针对规范化正文；标题或发布日期变化也触发更新。原始 HTML 的排版变化不会触发正文更新；图片、附件内容变化暂不追踪。不同 URL 的同一赛事保留为不同记录，赛事级去重计划后续实现。

`request_events` 保存每次请求的状态、重试次数、时间和耗时，`collection_runs` 保存按来源的新增/更新/未变/失败数。错误信息只记录类型和官方 URL，不输出环境代理凭据。所有时间点以 UTC 带偏移 ISO 8601 保存，网页发布日期以原始日期精度保存。`application_deadline` 是预留空字段，未来经验证的提取模块可填入，不需建立赛事关系库。

`rss.py` 仅读取成功入库的通知；使用 ElementTree 转义 XML，清除 XML 1.0 不合法字符，正文按 HTML 文本转义后进入摘要。没有发布日期时 pubDate 使用首次发现时间，并明确标注。RSS 临时文件与目标位于同一目录，写完后原子替换；CLI 禁止把 RSS 写到数据库本身。

`tests/` 使用裁剪的真实 HTML 和 `httpx.MockTransport`；全局测试夹具禁止真实 socket 连接。真实集成验证单独执行 CLI，并检查数据库和 Feed 内容，不属于日常 pytest。`tests/fixtures/manifest.json` 标明每个样本的原文 URL、采集时间和未裁剪原始 HTML 的 SHA-256；裁剪样本不等于完整原文存档。

新增网站时先保存无个人信息的最小真实样本、观察模板并测试，再实现列表/正文适配、注册来源。M0 没有调度器、Web 服务、AI 层或 AstrBot 适配层。
