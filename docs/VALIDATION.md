# 本机验证记录

## M2-A 验证（2026-10-13，Asia/Shanghai）

开始时实际分支 `m1-source-quality`、提交 `14a55e2`、工作区干净；100 项 M1 测试通过。机器配置实际为 42 个单位、152 个栏目、9 个完整采集验证栏目；M1 数据库有 36 篇通知、schema_version=1，完整性检查为 ok。与用户提供的状态一致；M1 后来已按用户指令推送，这不改变当时开发阶段“未推送”的历史记录。

本轮从 M1 创建 `m2a-rule-evaluation` 分支，使用 SQLite backup 保留 `data/m1-before-m2a.sqlite3` 并生成独立 `data/m2a.sqlite3`。原 M1 库没有覆盖或升级，没有网络采集、真实 LLM 请求、API 密钥读取、QQ/AstrBot 推送或正式 Feed 发布。

### 已实现与数据模型

- dataclass 校验 AnalysisInput、NoticeAnalysis、Evidence、InputLink、ManualLabel，枚举/布尔/日期/时区/引用明确；新增可配置 rules.json，不引入模型 SDK 或新运行依赖。
- 独立 notice_analyses、manual_labels、analysis_reviews，按需 schema_version=2。修复 Store 原先无条件重设 version=1 的问题，重开 v2 不降级，M0/M1 迁移测试不退化。
- 输入同时保存发现栏目和实际原文发布单位；缓存考虑标题、正文、来源、发布日期、链接、附件与脱敏版本。实现/规则配置、模型名或输入版本改变时不能误用旧分析；相同内容只重新计算时间状态。
- analyze、label export/import、evaluate、review list/set CLI；标签导入事务化、版本核验、重复导入幂等和工作表覆盖保护。人类标签不等于审批；自动输出和缓存不能伪造人工批准，人工批准/拒绝历史不被重新分析覆盖。
- 未来模型接口仅是协议和校验：新输出/缓存核验身份、事实引用、日期用途和模型元数据。不能把出版日期、校内截止或举办日期换成最终报名截止；不合法输出保留原通知并转待审核，不存异常文本中的敏感信息。

### 36 篇真实通知的规则预测

固定参考点为 **2026-10-13 00:00:00 +08:00**，规则版本 `rules-v3:921e47e2bcb4`。以下仅为程序预测，没有人工真值：

| 类型 | 数量 |
|---|---:|
| competition | 20 |
| news | 8 |
| administrative | 6 |
| other_activity | 1 |
| unknown | 1 |

相关性：direct_ai 13、ai_applicable 13、uncertain 3、unrelated 7。候选决定：retain 2、uncertain 17、exclude 17；预筛保留共 19 条，不等于 19 条已审核可推送机会。机会状态 unknown 33、closed 3；缺年份、时间范围和多赛道问题保持不确定，没有用旧发布日期或活动举办日期补出报名截止。22 条标记需要进一步核实，全部 review_status=pending，0 条人工批准/拒绝。

第一次按最终版本分析 36 条，失败 0；重复运行 cache_hits=36、analyzed=0。代码或配置变动的开发旧版本保留历史，不计入这次 36 条的分类统计。分析 JSON 为 `data/m2a-analysis.json`。

### 标注与评测证据

`data/m2a-labels.csv`：36 行，UTF-8 BOM；有脱敏短摘要、官方原文 URL、附件数量、发现/发布来源及独立 predicted_* 列。全部行 label_status=unlabeled，人工列为空；**没有用规则填充人工真值**。工作表记录人工参考日期/具体时点以及预测时点，评测支持固定 as-of 或逐标签时点，避免截止前后的时间差被误报为算法错误。字段说明在 LABELING.md。

实际执行未标注清单导入：imported=0、unchanged=0、unlabeled_skipped=36。实际 evaluate：valid_labels=0、human_labels=0、synthetic_labels=0；Precision/Recall/F1/未确定比例为 null，混淆矩阵全 0，error_samples 为空，明确输出“没有有效人工标签，无法评估真实效果”，production_validated=false。这不表示没有误报/漏报。

导入已标注、修正/重复导入、旧快照拒绝、后续错误行全事务回滚及错例输出均由离线临时数据库测试覆盖。模拟和合成标签不进入这 36 条真实记录，默认评测排除 synthetic；显式合成评测测试验证 TP=0/FP=2/FN=1/TN=0 的数学结果及资格错误列表，**不是实际筛选性能**。

### 数据兼容及测试

原库与副本逐表对比 notices、notice_sources、notice_links，所有行和原有字段完全一致。SQLite 完整性及外键检查通过。`data/m2a-raw-feed.xml` 仍有 36 个唯一 GUID，与 M1 Feed 集合一致，feedparser 正常识别 RSS 2.0。没有按规则过滤或自动批准 Feed。

最终 `python -m pytest -q`：**159 passed in 9.24s**，包括全部 100 项旧测试和 59 项新增测试/参数场景。新增涵盖直接 AI、允许 AI 的跨专业比赛、禁 AI 政策、获奖新闻、教师/研究生/学院资格、缺资格/正文、附件信息不完整、无年份/暂定/多赛道/校内校外/精确时分日期、多个报名入口、恶意指令、类型/规则配置错误、缓存/输入更新/模型名变更、人工审批保护、缓存伪审批、标签真假来源/事务/旧版/空样本、时间口径、RSS 和 v2 重开。常规测试继续禁止 socket 联网。

开发中修正了几项真实规则缺陷：不应因标题“拟推荐公示”而把标题整段脱敏；AI 禁用条款不应当作 AI 主题；资格“仅限某学院”不应误当 AI 部分允许；“仅限本科生，教师可指导”不能跨句匹配教师专属。修复保留原要求并加回归测试，没有降低测试标准。另修正 Windows 测试读 JSON 时显式 UTF-8，产物本来就是 UTF-8。

`pip check` 无依赖冲突；wheel 构建通过，包含 rules.json、分析模块及 42 个单位资源。从 wheel 导入 RuleAnalyzer 并加载配置通过，分析器版本与源码一致；未把此检查等同 Linux 部署或真实模型验收。原始数据库、CSV、分析与评测 JSON、Feed 都在 data/，被 Git 忽略；测试文本明确为合成，不复制实际学生名单、联系人、凭据到仓库。

### 局限与下一轮

现在没有人工标注的真实 FP/FN 证据，不能报告生产筛选效果。规则不能完整理解赛道、复杂否定或历史引用；有的活动虽可能报名但只在附件/日期范围写时间，当前保持 unknown。缺失信息不等于无限制或允许 AI。基础脱敏不能保证识别所有人名，未来外发前还需人工检查。

请先按 LABELING.md 完成人工标注并检查错例，再补足 100—200 条具有代表性的真实样本。真实 LLM 接入另行明确服务、模型/提示版本、固定 Schema、输入脱敏及长度、成本限制、超时失败回退和禁工具权限。M2-A 本地提交可回退，本轮未推送。

## M1 验证（2026-10-13，Asia/Shanghai）

基线 `1e209b4`、main 工作区干净；修改前全部 49 项 M0 测试通过。分支 `m1-source-quality`，保留 main 的 M0 代码。没有重新初始化项目，也没有生产部署或推送 GitHub。

### 连接诊断及 M0 问题修复

| 配置/层级 | 创新创业学院实际结果 |
|---|---|
| 系统 DNS IPv4 | `198.18.0.11`；TCP/TLS 探测为 ConnectionResetError（WinError 10054） |
| 系统 DNS IPv6 | `2001:2::2b`；TLS 探测为 SSLEOFError |
| 环境代理 | HTTP_PROXY/HTTPS_PROXY/ALL_PROXY/NO_PROXY 均未设置；HTTPX ConnectError / TLS EOF |
| `trust_env=False` / `--no-env-proxy` | 同样 ConnectError / TLS EOF |
| 强制 IPv4 / IPv6 | 均 ConnectError / TLS EOF，未获得 HTTP 响应 |
| 公共 DNS（Google DoH，HTTPS 验证开启） | CNAME `fa140.cdu.edu.cn`，A `202.115.80.140` |
| 公共地址 + 原域名 Host/SNI | TLS 1.3、证书验证通过、HTTP 200；取得列表、本站正文和跨学院原文 |

结论：本机解析/透明网络路径是故障环节，HTTPX 代理开关不是根因；不是网站永久不可用，也不是观察到的证书校验失败。TLS 握手阶段即终止，因此系统路径没有证据表明是重定向或 HTML 解析问题。不能仅依据当前证据认定是哪款本机网络软件导致，也未修改系统网络配置。

`198.18.0.0/15` 在 [IANA](https://www.iana.org/assignments/iana-ipv4-special-registry) 中是不可全球路由的基准测试地址段；系统返回这类地址与公共 DNS 不同，提示透明网络/DNS 代理路径。完整分组结果在本地 `data/m1-network-diagnosis.json`、`data/m1-public-dns.json` 和 `data/m1-final-network.json`。复现工具为 `python -m scripts.diagnose_source --public-dns`，只输出脱敏代理信息。公共地址会变化，覆盖是显式可选参数，不能把此地址当永久配置。

师范学院本机也使用本轮公共 DNS `202.115.80.140` 进行显式解析验证。新增 TLS/DNS/代理/超时/协议/解码/访问策略/解析器分类及 retryable，替代 M0 只保留 ConnectError 类型的粗略说明；同一来源下次运行可以恢复。正文跟踪从“任意 CDU 子域”缩小为明确配置的主机和 VSB 原文路径。

M0 将同站与跨学院的日期差异一律按冲突处理；M1 保留同站冲突检查，允许跨学院转载日期不同。真实案例：影视栏目日期 2026-09-07、教务处原文 2026-09-04；创新创业栏目日期 2026-10-08、美术学院原文 2026-09-30。分别保存，RSS 采用原文日期。新流式请求曾发生 gzip 重复解码，已修复并加回归测试；这属于 M1 开发中发现的问题，没有当作网站故障。

### 全校来源覆盖

官网 30 个院系条目逐一复核，另登记实际组织机构和导航中观察到的 12 个校级部门/平台，合计 42 个单位。41 个主页通过本轮已记录网络配置访问；化学化工学院仍只有 `#` 占位符。完整类型、关系和网址见 `SOURCES.md` 与 `units.json`。

首页共实际发现 160 条候选栏目导航，排除 8 条组织架构/管理制度/平台路径，保留 152 个候选栏目：152 个访问通过，139 个有规范 `/info/` 文章链接，9 个已实现且完整采集验证。13 个未发现这种文章链接的页面可能是介绍页、其他列表路径或动态页面，标为待适配，不能宣称没有任何机会信息。教务处详情适配验证通过，其列表未宣称完整接入。

### 真实采集及分页

| 来源 ID | 官方栏目 | 本轮完整验证 |
|---|---|---|
| computer | https://computer.cdu.edu.cn/cxcy/xkjs.htm | 首次处理 2 篇已有文章，重复未新增 |
| business | https://bs.cdu.edu.cn/xsgz2/tzgg.htm | 首次处理 2 篇已有文章，重复未新增 |
| innovation | https://cxcyxy.cdu.edu.cn/jyjx/xkjs.htm | 首次新增 2 篇，另验证 2 页/19 篇与跨学院原文 |
| msys | https://msys.cdu.edu.cn/xsgz/cxcy.htm | 首次新增 2 篇，重复未新增 |
| ftva | https://ftva.cdu.edu.cn/bkjy/tzgg.htm | 首次新增 2 篇，正确解析教务处原文 |
| pharm | https://pharm.cdu.edu.cn/xsyd/cxcy.htm | 首次新增 2 篇，重复未新增 |
| ele | https://ele.cdu.edu.cn/index/cxcy.htm | 首次新增 2 篇，重复未新增 |
| jztm | https://jztm.cdu.edu.cn/zsjy/cxcy1.htm | 首次新增 2 篇，重复未新增 |
| sfxy | https://sfxy.cdu.edu.cn/txgz/tzgg.htm | 首次新增 2 篇，重复未新增 |

真实运行三组证据：

- 9 栏目、每来源 2 篇/1 页：新增 14、更新 0、未变 4、失败 0、解析失败 0，共 72.048 秒，数据库由 M0 的 6 篇增至 20 篇。
- 同范围重复：新增 0、更新 0、未变 18、失败 0、解析失败 0，共 72.063 秒，记录数仍 20。
- 创新创业学院 `max-pages=2, limit=19`：实际读取首页及页面中真实“下页”指向的第二页；新增 16、更新 1、未变 2、失败 0、解析失败 0，共 46.109 秒。更新的已有数学竞赛通知补充了附件等元数据，原 ID/URL/正文哈希保持；总计 36 篇。

运行 stdout/log 文件保存在本地 `data/m1-live-first.*`、`data/m1-live-repeat.*`、`data/m1-live-pages.*`。无高频或大规模并发抓取，默认至少 2 秒请求间隔，验证没有进行附件下载。

### 数据库、RSS 和离线测试

升级使用 SQLite backup 保留 `data/m0-backup.sqlite3`，复制得到 `data/m1.sqlite3` 后追加迁移到 user_version=1；原 `data/radar.sqlite3` 仍保留 M0 数据。完整性和外键检查均通过。36 篇通知保存 13 条正文链接、27 条附件名称/URL；1 篇原文具有两个发现栏目。原 M0 的 6 篇 ID、URL、首次发现时间和正文哈希逐条与备份一致，6 个旧 GUID 都存在于新 Feed。

`data/m1-feed.xml`：36 个唯一 GUID，ElementTree XML 可解析，feedparser 识别为 rss20 且 bozo=False；摘要注明发现学院和实际发布单位、原文链接及日期语义。没有公开 Feed，没有 AstrBot/QQ 推送。

最终离线 pytest：**100 passed in 5.16s**。保留 49 项 M0 测试的行为检查；两项目录快照断言随功能扩展更新（来源不再仅 30 条、创新创业不再是未知模板），未知模板保护仍验证未支持的应急学院。新增 51 项涵盖七种来源列表/正文、教务处原文与附件、空结果/结构变化、分页边界/循环和数量限制、两类跨学院日期及归并、M0 迁移幂等、GUID 稳定、共享域名归属歧义、流式大小和 gzip、请求分类、Host/SNI 保留、可信域名、公网地址限制、恢复重试及诊断脱敏。测试夹具禁止 socket 联网，不用真实网络凑测试数量。

`pip check`：No broken requirements found。wheel 构建通过，确认包含 CLI、42 个单位和 185 个来源配置记录（152 个栏目加 33 个未选主栏目的单位占位记录）；不等同于 Linux 部署验收。新增 HTML 样本是实际页面的合理裁剪、脱敏内容，出处和未裁剪页面哈希见 `tests/fixtures/m1/manifest.json`；不含联系人、名单或作者姓名。开发中初次样本测试纠正了原文日期假设、较短正文裁剪和 Mock 第二页的相对路径，不降低解析要求。

### 剩余范围与下一步

143 个候选栏目尚未实现完整列表/详情采集；化学化工学院的独立官网待核验。分页目前只支持实际观察的 VSB 数字分页，不覆盖动态 API、其他新闻 URL 或所有历史模板；纯图片/附件正文仍明确失败。附件内容变更、精确文章时间与 HTTP 条件更新未实现；网站结构和公共 DNS 地址仍可能变化。

36 篇本地原始通知可作为 M2 人工标注起点，含报名/选拔、获奖新闻、公示和普通通知，不能直接当作模型已经筛选的机会清单。原始数据可能包含个人信息，应先确定脱敏和发送边界；本轮没有调用模型。M2 待决策已记录到 PLAN.md。

### M1 Git 与数据边界

本轮只在 `m1-source-quality` 上进行本地开发与提交，提交说明沿用 feat/fix 前缀和中文内容；main 与 origin/main 保持 M0 的 `1e209b4`，没有推送 M1。Git 的实际提交记录以本地 log 为准。数据库、Feed、原始整页、诊断结果、虚拟环境与临时构建材料均被忽略；仅把合理裁剪的脱敏样本及官网配置纳入代码。升级前后的本地数据库保留，原始 HTML 在核对/制作样本后清理。

## M0 历史验证记录

验证日期：2026-10-13，Asia/Shanghai。环境：Windows PowerShell、Python 3.12.10、项目独立 `.venv`。

M0 的采集 → SQLite → RSS 最小闭环通过。已完整验证计算机学院及商学院两个真实官方栏目；**优先指定的创新创业学院未完成本机采集验证**。商学院作为第二种模板补充来源，不等同于对创新创业学院的验收。

## 自动化与环境

- `python -m pytest -q`：**49 passed in 1.96s**（提交前复验）。全部使用裁剪真实 HTML 或 MockTransport；自动夹具禁止 socket 联网。初次 4 个失败是 Mock 响应将 HTML 标成默认 text/plain，修正响应的真实 Content-Type 后通过，未降低生产验证标准。之后补充了畸形端口/URL 的错误隔离检查。
- `python -m pip check`：No broken requirements found。`pip install -e ".[dev]"` 成功，CLI 可运行。
- Wheel 构建通过；在 Python `-I` 隔离模式下从 wheel 导入包、读取 30 个打包来源条目和运行 sources CLI 通过。该检查复用当前虚拟环境依赖，不等同于 Linux 安装验收。
- 解析测试检查真实标题、完整 URL、发布日期和正文短段落；覆盖结构变化、空日期、错误日期、正文截止日期不混用、图片正文拒绝、中文 inline span 连接和未知站点。
- 请求测试检查 3 次有限超时尝试、服务端恢复重试、401/403/429 不重试、robots 禁止/失败缓存、间隔和 Crawl-delay、重定向域名及新站 robots 检查。
- 存储/CLI/RSS 测试覆盖来源失败隔离、幂等、内容哈希更新、首次发现时间保留、多来源关联、失败后保留旧记录、XML/中文/特殊字符、Feed 数量限制、未知日期和数据库防覆盖。

## 真实集成

首次：2026-10-13 19:35:21—19:35:39。计算机学院 inserted=3，商学院 inserted=3，均 failed=0。
重复：2026-10-13 19:41:32—19:42:06。两个成功来源均 inserted=0、updated=0、unchanged=3；创新创业学院 failed=1。包含失败来源的命令退出码为 1，正确保留其他来源成功结果。

```powershell
.\.venv\Scripts\python.exe -m cdu_radar collect --source computer --source business --limit 3 --no-env-proxy
.\.venv\Scripts\python.exe -m cdu_radar collect --source innovation --source computer --source business --limit 3 --no-env-proxy
.\.venv\Scripts\python.exe -m cdu_radar export-rss --limit 30
```

SQLite 共 6 篇唯一通知、6 条来源关联、5 次来源运行、23 条 HTTP 请求事件。`PRAGMA integrity_check` 为 `ok`。第二次采集的 `discovered_at`/`updated_at` 均保持首次值，仅 `last_seen_at` 改变；已逐条对照保存的真实正文页标题和哈希。

| 来源 | 原文 | 网页发布日期 | 可读正文字符数 |
|---|---|---|---|
| 商学院 | [【求职创业补贴公示】商学院2027届毕业生求职创业补贴拟推荐名单公示](https://bs.cdu.edu.cn/info/1041/13252.htm) | 2026-10-12 | 240 |
| 商学院 | [【评优评奖】商学院本科生国家奖学金候选人拟推荐名单公示(2025-2026学年)](https://bs.cdu.edu.cn/info/1041/13253.htm) | 2026-10-12 | 638 |
| 商学院 | [【评优评奖】成都大学商学院关于2025-2026学年研究生国家奖学金拟推荐人选的公示](https://bs.cdu.edu.cn/info/1041/13254.htm) | 2026-10-13 | 216 |
| 计算机学院 | [【通知公告】第十八届全国大学生数学竞赛成都大学报名](https://computer.cdu.edu.cn/info/1036/12337.htm) | 2026-09-14 | 736 |
| 计算机学院 | [我院学子在2026年（第19届）中国大学生计算机设计大赛斩获全国二等奖](https://computer.cdu.edu.cn/info/1036/12338.htm) | 2026-09-14 | 895 |
| 计算机学院 | [我院学子在全国大学生嵌入式芯片与系统设计竞赛中斩获全国三等奖](https://computer.cdu.edu.cn/info/1036/12341.htm) | 2026-09-14 | 722 |

数学竞赛通知发布日期为 2026-09-14，正文报名截止为 2026-09-17，二者明确分开；正文“我校在校全日制本科生”可读，截止字段仍为 NULL。商学院第一条通知发布日期为 2026-10-13，可读正文首段正确。未替官方修正正文内其他日期，也未把已过期通知视为当前可报名机会。

`data/feed.xml`：ElementTree 解析通过；feedparser 6.0.14 识别为 `rss20`，`bozo=False`，6 条条目，GUID 和链接唯一且与数据库完全一致。未知发布日期使用首次发现日期的路径由离线测试覆盖，本轮真实 6 篇均有网页日期。原文真实变更未人为触发；更新行为由离线存储测试验证。

本地数据：`data/radar.sqlite3`、`data/feed.xml`、`data/validation.json`（含标题、URL、哈希、各次运行结果）；均在 .gitignore 内，不进入版本控制。短暂保存的原始 HTML 已用于核对并制作裁剪样本，收尾后删除。

## 限制与未执行

- 创新创业学院本机网络连接失败，尚无经过真实 DOM 验证的解析器；远程网页工具能够阅读并不等于本机可采集。
- 全校其余目录条目仅登记官网链接，未完成栏目发现和适配；分页、增量游标、附件/OCR、赛事级去重、筛选及截止时间抽取未实现。
- 标准库 robots 解析器不是完整 RFC 9309 实现；复杂匹配规则需在扩展来源前验证。
- 请求超时为 HTTPX 各阶段 timeout，不是整个采集任务的总时限；当前 3 MiB 检查在响应读入后执行，未来可改流式大小限制。
- 未执行 Linux 服务器部署、AstrBot 或 QQ 群订阅。Feed 只生成本地文件。尚未选定对外开源许可证。

## Git

原目录为空、没有仓库；M0 验证结束时已初始化本地 main 仓库，项目文件尚未提交，没有远程配置，也没有推送。这是验证时的状态快照，后续提交状态以 Git 为准。数据、Feed、虚拟环境、构建和临时材料均被忽略。
