# M6 分类与筛选契约（Issue #9）

新增 `GET/HEAD /api/notices/v2`，JSON `schema_version=2`、`taxonomy_version=1`。旧 `/api/notices` 仍只返回 JSON v1 的原字段；`category` 原有五分类以及 `/feed.xml`、`/current.xml`、`/history.xml` 的内容/身份/时间语义不改。数据库没有新增分类表或迁移，旧数据按当前原文确定性派生，原文与人工历史不写回。

## 三种来源身份

- `publisher_id` 是已验证的最终官方发布单位 ID，由原文 URL、存储单位/域名与当前目录共同核验，不使用展示名、模糊域名或用户 URL 推导。
- `discovery_source_ids` 是该原文已观察到的、当前目录内真实栏目 ID；同一原文可由多个学院/栏目发现。发现栏目不是最终发布单位，不能据此提升原文单位可信性。
- `source_name` 为兼容的展示名，`source_url` 是最终官方原文 URL；两者不能替代稳定 ID 作为筛选键。来源筛选用 publisher，不用转载发现来源。

例如 innovation 栏目发现 msys 原文：`publisher_id=msys`，`discovery_source_ids=[innovation]`，只属于发布单位 msys 的来源筛选。同一个 URL 再被 computer 发现仍只有一条信息、同一 GUID，发现来源列表增加 computer。

## 分类字段

每条 v2 item 在 v1 原字段之外增加 `publisher_id`、`discovery_source_ids` 和 `classification`，不输出正文、数据库路径、模型缓存或人工审批字段。

| 字段 | 当前值与语义 |
| --- | --- |
| `classification.version` | `1`，当前分类结构版本 |
| `classification.policy` | `m6-rules-v1`，版本化确定性规则 |
| `classification.method` | `rules`，浏览辅助，不是官方认定或人工真值 |
| `classification.type` | `notice` 通知公告、`news` 新闻动态、`event` 活动/学术交流、`competition` 竞赛、`research` 科研实践、`unknown` 未分类；一个主类型 |
| `classification.topics` | 多主题代码数组：`ai`、`computing`、`pharmacy`、`design`、`innovation`、`sports`、`humanities`；同一通知可同时有 AI、计算机或艺术等主题 |
| `type_state` / `topic_state` | 主类型 classified / unclassified；主题 tagged / unclassified。未命中词典不意味着内容无主题或无价值 |
| `model_state` | `not_analyzed`：尚未在本契约下做模型分析，不读取历史独立 LLM 实验来伪造本轮处理完成 |

报名状态继续独立使用 `registration_status=possible/closed/unknown`，历史范围继续使用 listing；发布日期 null、类型 unknown、主题未分类与模型未处理是不同维度，不相互替代。新闻可以提到 AI，但不会因此被判定允许使用 AI、资格符合或报名开放。竞赛类型 + AI 主题构成跨维度双标签，不复制原文或 GUID。

规则优先识别公示/评审等通知、新闻/动态，再沿用已有规则的竞赛/科研/活动信号；无充分类型依据则 unknown。主题只根据标题和正文的版本化关键词，不按学院名称硬贴主题。规则没有重要性评分、排名或推荐门禁。原文变化或目录撤销先使旧快照失效，再由后台重建；不需要模型响应。

## 筛选与时间

v2 无查询参数默认返回全部当前合格信息。跨维度取交集，主题逗号组合也取交集；未选择任何条件时保留普通公告、非 AI、未分类和模型未处理信息。

| 参数 | 值 |
| --- | --- |
| `source` | `all` 或响应 `filter_options.sources` 中稳定发布单位 ID |
| `type` | `all` 或上述 type 代码（含 unknown） |
| `topic` | `all`、`unclassified` 或主题代码逗号组合，例如 `ai,computing`；排序规范化，重复标签不接受 |
| `status` | `all/possible/closed/unknown`，报名证据状态 |
| `view` | `all/current/history`，与原有范围一致，不含未来 featured |
| `category` | `all` 或原有五分类的精确值，用于兼容筛选；不与 type 混用 |
| `published` | `all/known/unknown`，来源日期是否存在 |
| `from/to` | `20YY-MM-DD`，仅按来源发布日期筛选，两端包含；不以采集时间补日期 |

```text
/api/notices/v2?source=computer&type=competition&topic=ai,computing
/api/notices/v2?type=news&topic=pharmacy&from=2026-10-01&to=2026-10-31
/api/notices/v2?published=unknown&status=unknown
```

响应 `applied_filters` 返回规范化条件，`counts` 是该条件下的唯一原文数量及 listing 分组，条件正常为空仍为 200 合法空集合。参数不执行 SQL、不成为抓取 URL，不触发采集或 LLM。重复/未知参数、空值、非法 UTF-8/百分号编码、未知枚举、日期倒置或 unknown 日期加日期区间均为 400；查询长度最多 1024、路径 2048、最多 9 个参数、单值最多 100 字符。错误不回显参数值或本地路径。

v1 不接受新的查询参数，原有非法路径仍 404。v2 及所有筛选先经过与 RSS 相同的 live read_gate，数据库/来源/快照失效时返回 503，不能返回旧集合或 304。v2 无 ETag，no-store、同源/CSP/只读头保持；HEAD 同样验证但无正文。发布单位选项自身变化也使旧元数据快照失效，即使该单位暂时没有合格通知。

页面使用相同 v2 接口完成来源/类型/主题/日期/状态/原分类筛选；当前/历史范围对返回集合做相同 listing 条件，测试核对集合与 API/RSS。主界面三项筛选，日期、报名状态和原分类放在可展开区域，支持标签、键盘、手机视口、重置、正常空与错误状态，不保存用户偏好或账户。原文日期未知不会显示成采集日期。

LAN 预览代理只转发固定环回上游的明确 v2 路由/参数，并检查顶层、item 和 classification/filter_options 字段白名单；未知私有字段拒绝。原 v1 投影规则保持。这里只做环回代理回归，不代表真实手机/LAN 或 Linux 实机验收。

v2 本身不生成 RSS 订阅链接。后续 #10–#13 已新增独立异步分析、精选 v3 和[参数化 RSS v1](RSS_V1.md)，复用这里的分类/筛选契约；账户和部署仍未接入。
