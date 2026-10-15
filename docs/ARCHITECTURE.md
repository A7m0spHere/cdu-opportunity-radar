# 系统结构

## M4-C 学生页

同一 Python `serve-rss` 提供固定网页资源和 `/api/notices`，没有独立应用服务。`AutoFeedController` 生成 RSS 时同时生成经过字段裁剪的公开快照；JSON 响应在原 read_gate 的数据库只读事务和响应锁内输出，复核来源、内容版本、时间和快照时效。静态 HTML 不嵌通知数据，失败时仍能加载界面；JSON/网络/核验失效会清空浏览器卡片。

前端原生 DOM/textContent，不使用外部脚本、CORS、客户端存储或写接口。白名单路由与内容类型明确，未知路径/参数拒绝。新增 source_checked_at 保留真实系统采集时点，原文发布日期与首次收录另列；行政通知不因未来截止进入当前机会。来源目录每次实时计划加载一次，减少冗余查询但不缓存跨请求的质量结论。详见 [学生页契约](STUDENT_PAGE.md)。

## M4-B 当前结构

`collect → Adapter → Store + notice_health → AutoRepository（规则分类、时间与来源质量）→ AutoFeedController → RSS`。HTTP 路由、OS 状态锁与保持至响应完成的 SQLite 回滚读事务复用 M3-A；自动发布状态使用独立 `auto_publications`，不查询人工审批来决定发布，不消费人工队列、标签或 LLM 缓存。

成功解析保存版本校验哈希；文章请求/解析失败使旧正文停止发布；源检查超过 72 小时也停止发布。新服务 `/approved.xml` 为 410，原审批写 CLI 与旧服务入口退役；旧表只读保存。采集不再调用写入旧审批事件的 `source_updated`，改用独立失效标记，避免原文恢复旧版本后复活历史审批。详见 [完整契约](AUTO_PUBLICATION.md)。下文为各阶段历史设计。

## M3-A RSS 服务增量

`rss_service` 将只读精确路由与后台出版分开。后台复用 approved_rss/export_approved，只记首次发布时间和系统导出事件，不生成人工审批、模型结果或标签；HTTP 使用 mode=ro 事务复核版本/批准/当前时间，失效即 503 并唤醒后台，定时循环兜底。缓存只在内存验证快照中可服务，旧磁盘 XML 不作回退；状态目录有源绑定与 OS 单写者锁，重启重新生成。

响应读事务保持到发送完成，当前只接受 SQLite 回滚日志模式，WAL 拒绝；不对公网绑定，不提供目录列表或管理接口。rss_synthetic 只操作新建、有双重实验标识的独立合成库，每个实验 URL 命名空间不同，不导入真实库。接口、部署与验收分别见 RSS_HTTP.md、M3A_TEST_GROUP.md、M3A_RESULTS.md；实际插件、Linux/代理和测试群仍未验收。

## M2-C 独立发布增量

`publication` 保存人工作出的发布快照、当前发布状态和追加事件；`publication_cli` 提供只读队列/查看/历史/草稿及具体指纹确认；`approved_rss` 导出独立 RSS，不复用原始 Feed。写入按需迁移 v5 的 publication_reviews/publication_state/publication_events，不自动迁移标签或旧 analysis_reviews 批准。

批准绑定实质源版本（实际正文摘要、存储正文哈希、标题、URL、网页日期、原文单位元数据与链接/附件），不绑定模型或采集时间。Store.save_notice 在原事务末尾仅对有活动批准且版本改变的通知调用 source_updated，冻结待复核并记录系统事件，不生成审批。导出再次核对版本、期限和状态，即使绕过采集修改正文也不会覆盖新版；更正保留 URL/GUID/首次时间并追加待处理记录。

只读预览复用现有 mode=ro 连接，无 Store 初始化或迁移，不分析、不调用网络；确认写入有 BEGIN IMMEDIATE 和前态/草稿指纹校验。人工草稿含受控原文事实与显式未知，模型及评测数据仅作参考，不拥有发布权限。

正式 XML 使用批准快照，时间和 GUID 由持久发布状态提供；文件原子替换失败可恢复同一身份。SQLite 与文件不是一个事务，采用保留时间、锁内替换、成功记录的最小恢复策略；静态 XML 到期与变更后仍需显式重导出。原 RSS 输出不改，HTTP/调度/认证及群更正发送属于后续联调设计。见 PUBLICATION.md 与 M2C_RESULTS.md。

## M2-B.1 离线诊断增量

`evidence` 只做 NFC/空白规范化和原文位置映射；`llm_semantics` 保守检查关键事实支持；`llm_screening` 提供显式可选的 llm-screening-v2 初筛契约；`llm_diagnostics` 只读历史报告与缓存，不重建缺失响应。NoticeAnalysis 的新增 Schema、fact_status 和证据评估字段都有兼容旧缓存的缺省值。

发送路径在 user_version=4 增加独立 llm_request_diagnostics，不改原台账列和人工真值。阶段诊断只存计数、位置和白名单状态；原始响应/推理仍不保存。本轮实际库只读，没有触发迁移。compare 分技术、业务和系统指标，旧候选指标仅留档；history 原样查看旧版本，diagnose 无需 Provider 配置。完整边界与离线证据见 [M2B1_OFFLINE.md](M2B1_OFFLINE.md)。

## M2-B 增量模型接口

`llm` 使用 httpx Chat Completions Provider，Provider 协议可替换；`llm_schema` 维护固定 Prompt/JSON Schema 和引用/日期用途校验；`llm_workflow` 处理追加的请求台账、小批量执行和只读缓存对照；`llm_cli` 是原 CLI 的薄适配。没有重建采集器、引入模型框架或发布模块。

NoticeAnalysis 只追加可空 student_opportunity，旧缓存和规则结果保持兼容。分析失败保存安全类别、候选不排除；缓存损坏或模型证据不匹配时可重试，不能清空原文。时间由本机按人工参考点重算，版本包含 Prompt/Schema/模型/服务/分析参数及实现指纹。人工标签不发给模型，人工审核历史独立。

仅发送时创建 user_version=3 的 llm_requests，发送前保存 started，结束后更新请求状态、已知 Token 和可估费用。使用独立 M2-B 数据库副本，原 M2-A 及其 36 条标签不变。预览/compare 用只读连接，不迁移数据库、不发送请求；模型缓存未覆盖全部标签时主指标留空，失败仍保留在同一评测样本集。

完整配置、失败分类和对照口径见 LLM.md。无工具、附件正文、自动批准、正式 Feed 或群推送；原 RSS GUID 与存储机制不变。

## M2-A 独立分析与人工评测

保留既有采集和 RSS 模块；新增 `analysis_models` 的类型化输入/分析/标签/证据，`rule_analysis` 可配置规则，`analysis_store` 追加缓存、标签和审核历史，`analysis_service` 控制版本与权限，`labels` 和 `evaluation` 提供人工 CSV 和离线指标。没有模型 SDK、网络调用或发布服务。

只有分析/标注命令按需创建 M2 表并设置 schema version=2，原 M0/M1 表保留。原 Store 不再把更高版本重设为 1；旧采集、RSS 和 v1 数据迁移测试保持可用。原通知、来源关联和附件链接不因分析或标注而更新。

`notice_analyses` 保存固定 Schema 的 JSON，读写均由 NoticeAnalysis 校验；输入哈希包含标题/正文/来源/日期/链接/附件，规则版本含实现与配置指纹。命中缓存仍验证身份、模型名、证据及审批边界，再按当前北京时间重算机会状态。`manual_labels` 和 `analysis_reviews` 是独立追加历史，按 notice_id/input_hash 绑定；自动模块不能审批、不能改写人工历史。完整约定见 ANALYSIS_CONTRACT.md。

CSV 导出提供短脱敏摘要、原文 URL 和独立预测列，人工列默认空。导入先验证全部行与原文版本，在事务中提交；评测默认只用明确人工标签，合成案例必须显式启用并标识。review 是审批记录，不是导入标签的副作用，也不是正式 Feed 发布入口。

## M1 增量变化

保留 M0 的 CLI → HTTP → Adapter → SQLite → RSS 流程。单位和栏目分开登记：`units.json` 记录官网目录层级、共享域名、中心/平台/部门，`sources.json` 仍由 `Source` 加载，一个单位可以有多个栏目；M0 的来源 ID 不变。发现/访问/文章链接/模板实现/完整采集验证分别记录，不把这几种状态合并。

`Fetcher` 默认仍使用系统 DNS 和环境代理。`--resolve HOST=IP` 是显式可选模式，要求 `--no-env-proxy`；仅接受配置中的官方域名及全局可路由 IP，通过 HTTPX transport 保持原始 Host 和 TLS SNI，使用默认证书验证。它不改变 URL/GUID、不修改操作系统 DNS、不降低 TLS 版本或绕过登录。保留默认网络方式，故障来源下次运行重新检查 robots 和连接。实际连接还可能经过本机透明网络代理，关闭环境代理不等于关闭该网络层。

失败状态在 M0 `failed` 的基础上附加 `error_category`、`error_scope`、`retryable`：DNS、TLS 证书、TLS 握手、代理和连接属于环境原因；超时、协议或解码属于本次请求；HTML 结构属于解析器；robots/401/403 属于访问策略；410 表示请求端点移除。不能把单次连接失败等同整个网站永久不可用。请求过程用流式读取限制解压后的大小，gzip 内容只解压一次。

正文跟踪只允许显式登记的官方域名和 `/info/<栏目>/<文章>.htm` 路径；未知域名/正文模板在请求前拒绝，重定向目标再次检查。分页仅沿页面上实际“下页/下一页”链接、原始栏目路径下的数字 `.htm` 页面，受 `--max-pages` 与每来源 `--limit` 限制；对循环页去重，不根据猜测生成分页 URL。

共享域名（例如基础医学院与护理学院）不凭首个匹配项猜测发布学院；数据库仅在域名对应唯一单位时自动回填，采集时对未消除的单位歧义明确失败。后续接入这类网站需要先核实原文中的发布单位元数据。

SQLite 从 M0 追加升级到 `user_version=1`，迁移在事务中执行，可重复打开，不重建 notices。新增 `units` 和 `notice_links`（body/attachment）；notices 新增 `content_source_id`、`content_domain`，notice_sources 新增发现栏目的日期。`notices.source_id` 保持 M0 的最初发现栏目，`notice_sources` 记录所有发现栏目，`content_source_id` 指向实际发布单位。M0 的关联按原文域名回填，ID、URL、首次发现时间及正文内容不改变。

转载栏目日期与原文发布日期可以不同：跨学院链接以原文详情日期为准，同时在关联表保留栏目日期；同站列表与正文日期冲突仍明确失败以提示检查。同一最终 URL 继续受 UNIQUE 约束；正文、标题、日期或链接/附件变化更新已有记录，RSS GUID 始终使用原 canonical_url。附件只记录名称与 URL，不跟踪下载，不解析附件正文；外部正文链接只记录，不能成为采集入口。

升级使用示例：先用 SQLite `backup()` 生成 `data/m0-backup.sqlite3`，再备份到 `data/m1.sqlite3` 并用 M1 打开。原 `data/radar.sqlite3` 本轮保持为 M0 格式；M0 程序不保证能够直接打开升级后的 M1 数据库，恢复 M0 应使用原库/备份，不能删除或回写覆盖数据。

`scripts/diagnose_source.py` 提供脱敏的本机/公共 DNS 诊断；`scripts/discover_sources.py` 和 `scripts/check_candidates.py` 是低频、有界、可人工审查的官方目录发现工具，不是搜索引擎爬虫。它们把证据写入忽略的 data/artifacts，不能直接把访问成功标成正式采集通过。`scripts/validate_local.py` 以只读数据库检查完整性、正文哈希、M0 数据保留和 RSS GUID。

## M0 基线结构（历史说明）

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
