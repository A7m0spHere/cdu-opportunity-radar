# 校园信息分类与 LLM 复核 v2

规则初分即时展示，模型在私有队列中异步复核。有效分类会用于网页筛选及新版 RSS；缺少模型、未处理、失败、依据不足、预算不足或输入/版本变化时回到新版规则结果。分类不改变原文的来源、完整性、撤回或 72 小时核验门禁，也不生成任何人工批准或真值。

## 分类与界面

主类型保留通知公告、新闻动态、竞赛、活动与学术交流、科研实践、未分类。`domains` 独立表示教务考试、奖助评优、竞赛双创、学术科研、就业实习、校园服务、学生事务、文体活动；`topics` 表示学科主题。

公示和教务事务优先识别为通知，已获奖、摘金、顺利举办等回顾识别为新闻。程序设计、系统设计、教学设计和毕业设计不会仅因“设计”触发艺术；机测、聊天软件、AI 使用规范和“论文学术”跨词片段不作为学科主题依据。单位支持或发布身份也不能单独证明主题。未知仍保留，不强行填满分类。

卡片以信息类型为主，显示有效领域和学科标签；“分类依据”展开后显示规则结果、模型复核及短引用。列表统计已复核、已纠正、待复核、依据不足和暂不可用数量。原有“报名状态未确认”标签继续隐藏。

## 新接口与兼容

- `GET/HEAD /api/notices/v4`：`schema_version=4`、`taxonomy_version=2`。`classification` 是实际用于筛选的有效分类；`method=rules|llm_reviewed`。`classification_review` 包含状态、是否纠正、规则结果、依据和理由。
- 状态为 `not_analyzed/pending/reviewed/uncertain/failed/stale`。模型返回完成并不必然是 reviewed，也不代表人工验收。
- 增加 `domain` 筛选，可为单值、逗号组合或 `unclassified`；其他类型、学科、来源、日期、报名状态筛选沿用既有语义。跨轴及多标签取交集。
- `/rss/v2/all.xml` 与 `/rss/v2/featured.xml` 采用相同有效分类、条件和身份。RSS 的 category 是新主类型，附加 type/domains/topics/classification_state 元数据；GUID 与原有首次时间保持稳定。
- 旧 JSON v1/v2/v3、RSS v1 及旧 Feed 保留兼容投影。局域网代理和 Nginx 草稿同步白名单；未改变监听范围或部署状态。

HTTP 与 RSS 只读原文和既有建议，不调用模型、不读取密钥。原文和队列的读取快照保持到响应结束；队列故障可降级规则分类，原文故障仍返回 503。精选订阅继续验证既有持久化集合及首次精选时间，失败不沿用旧快照。

## 运行与凭证

本机非秘密配置为 `data/llm-review/config.json`；原文库为当前采集库，队列使用独立的 `data/llm-review/jobs.sqlite3`。两者均在 Git 忽略目录。后续测试沿用此配置和同一队列，不能通过另建队列规避既有预算。

当前配置使用第三方 OpenAI 兼容地址 `https://api.czrouter.online/v1`、配置模型 `deepseek-v4.1-flash`、thinking disabled、json_object、最大输出 4096、90 秒总时限。配置名称不证明第三方底层模型身份。

`credential_file` 引用本机 `%LOCALAPPDATA%/CDURadar/llm/czrouter-key.dpapi` 的当前用户 DPAPI 密文。真实请求优先使用指定环境变量，缺省由模型子进程解密该文件；密钥不进入明文配置、命令参数、Git、日志或 HTTP。其他平台可以提供 `CDU_LLM_API_KEY`，不会尝试解密 Windows 文件。读取状态或启动只读展示不加载密钥。

```powershell
# 指定样本：同一组 --notice-id 必须同时限制扫描与认领，可重复追加。
.\.venv\Scripts\python.exe -m cdu_radar analysis-queue work --db artifacts/collection-20261018-162632/notices.sqlite3 --state-db data/llm-review/jobs.sqlite3 --config data/llm-review/config.json --notice-id 20 --send

# 只读展示已有结果，不调用模型。
.\.venv\Scripts\python.exe -m cdu_radar serve-rss --db artifacts/collection-20261018-162632/notices.sqlite3 --state-dir data/review-http --analysis-state data/llm-review/jobs.sqlite3 --analysis-config data/llm-review/config.json
```

显式追加 `serve-rss --analysis-worker` 才启动受管审核协调器，模型请求仍在可终止的子进程中进行；`--analysis-notice-id` 可重复限定样本。服务关闭会停止协调器及运行中的模型子进程，认证或凭证错误停止该协调器。省略这些选项不会自动产生付费请求。

本次预算为串行、每篇最多两次尝试、共 18 次请求、滚动 24 小时 1,000,000 本地预留 Token。传输层不重试，队列仅对瞬时错误有限重试；格式/证据错误不盲目重试。账本保留实际用量，费用缺少单价时为未知，本地预留不等于服务商账单硬限额。

## 校验、版本与重新校验

新版固定 Prompt/Schema 为 `campus-classification-review-v2`。模型输出 type/domains/topics 及逐项引用，引用须同时在脱敏输入和原文中定位；类型、领域和主题复核主旨及语境，不能要求与旧粗关键词分类完全一致。格式错误、虚构引用、语义不支持分别记录；有不支持分类字段时保留规则分类，不把裁剪后的建议伪装成完整复核。

输入、模型/配置、Prompt、Schema 和校验实现均进入版本身份。原文或版本改变后旧结果不直接采用。`analysis-queue revalidate --from-pipeline <旧版本> --notice-id <ID>` 是显式、零请求的离线重新校验：必须匹配同一原文输入、模型/服务参数、Prompt、Schema 和调用模式，重新运行当前校验并记录 `revalidated_from`。模型或 Prompt 改变时拒绝复用；原始调用和费用账本保持不变。

## 本次真实验证

2026-10-18 对前一轮核查的 15 篇问题样本运行真实调用，共 15 次请求、可知用量 27,413 Token，原文、GUID/首次时间及已有历史表哈希一致。首次为 8 succeeded、6 partial、1 格式校验失败。对“顺利完成”等引用语境补齐并显式离线重验后，当前为 **9 篇分类通过、5 篇依据不足、1 篇返回格式校验失败**，没有追加模型请求。

这是问题样本功能核验，不是整体人工准确率。依据不足的典型情况是把泛化的“创新能力”当作创新创业，或给出不能支持所提主题的引用；页面保留规则分类和真实状态。规则已解决的错例与模型额外纠正分开统计。

私有证据见 `artifacts/llm-review-20261018-173917/`：原始试验结果 `result.json`、显式重验 `revalidated.json`、逐篇对照和账本；不提交完整原文、私有结果或密钥。离线回归覆盖错例、跨词误判、模型实际改变筛选、伪造引用、旧版失效、预算、样本限制、HTTP/RSS/LAN 一致及子进程停止。
