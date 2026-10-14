# M2-B.1 离线诊断与修复（2026-10-14）

本轮新增付费请求 **0**。初始工作区干净，从 `m2b-llm-evaluation` 创建 `codex/m2b1-offline-stability`。人工标签、原通知、RSS GUID、SQLite 和原历史报告均未修改。测试仅使用临时库与 MockTransport，全局夹具禁止真实 socket 连接。没有推送远程、生产部署或 QQ 接入。

## 故障证据与分类

实际文件为本地忽略的 `data/m2b-comparison-revalidated.json`、`data/m2b-errors-revalidated.json`、`data/m2b.sqlite3`。缓存与比较报告的版本、36 个通知 ID、状态和候选判断逐条一致。错误报告是业务错例清单，与比较报告内的错例相同；它不是模型原始响应或技术校验轨迹。

历史版本 `llm-v1:89e8a0e080bd523db08b159c`：36 条通知，5 条成功，31 条失败。保存的只有首个拒绝码；失败响应的最终 content、具体引用、JSON 位置、finish_reason、推理长度和完整字段路径均未保存。不能离线恢复这些内容。下表表示**可观察的首要拒绝阶段**，不代表已经证明底层根因。

| 首要阶段 | 数量 | 占 31 条比例 | 通知 ID / 观察码 |
|---|---:|---:|---|
| A 网络 / HTTP | 0 | 0% | 最终通知失败未落在本层；全部版本的台账仍有早期服务错误 |
| B 输出格式 | 8 | 25.81% | JSON：9、11、21、23、24、26、34；类型/枚举：31 |
| C 引用定位 | 7 | 22.58% | 8、18、20、27、28、30、33；unlocatable_evidence |
| D 事实支持约束 | 15 | 48.39% | 日期：2、17、22、32；关键引用缺失：4、6、13、36；关闭依据：10、16、35；具体值引用：12、15；主办方：3；AI 政策：19 |
| E 信息不足 | 0 可确认为首因 | 0% 可确认 | 标题正文、未解析附件、多日期、资格限制另作输入风险提示，不冒充已观察根因 |
| unknown_unobservable | 1 | 3.23% | 25：invalid_schema，无法进一步定位 |

所有 **31 条的细分根因均为 unknown_unobservable**。例如 ID 20 的引用未定位，无法判断是空白差异、HTML 清洗、语义改写还是编造；ID 34 无效 JSON，无法判断代码块、截断还是语法错误；ID 19 的 AI 政策被拒绝，无法确认模型究竟如何扩大了许可范围。没有将输入中存在附件或多个日期强行归为失败原因，也没有生成替代模型响应。

逐通知报告还单独列出各历史版本的请求错误观察；例如 ID 3 在早期版本曾出现 incomplete_output，但缺 finish_reason，不能确定是 Token 截断。ID 1 的早期 server_error 属于最终成功通知，不纳入 31 条最终失败的 A 类。历史尝试观察用于诊断，不合并新旧版本成绩。

原包装错误 `schema_validation` 共 24 条，其中确定结构/类型错误 1 条，未知细分 1 条，引用定位 7 条，事实支持约束 15 条；另有 JSON 错误 7 条。不能把这 24 条全部称为 JSON Schema 结构错误。

| 通知级流水线（分母 36） | 已通过 | 明确失败 | 未观察 / 未进入 |
|---|---:|---:|---:|
| JSON 解析 | 29 | 7 | 0 |
| Schema 结构 | 27 | 1 | 8 |
| 引用定位 | 20 | 7 | 9 |
| 原版本完整分析 | 5 | 31 | 0 |

历史版本实际 **38 次 POST**：HTTP 200 为 38/38，JSON 为 30/38（78.95%），完整分析为 5/38（13.16%）。重试/再次尝试使请求级 JSON 错误为 8 次，与通知级最终错误 7 条不同。请求台账没有校验细分字段，Schema 和引用定位的准确请求成功率留空，仅保留已知下界，不能由通知的最终拒绝码反推早期尝试。

全部版本仍为 50 次 POST、49 次用量、已知 183905 Token；未知用量与未知价格均不等于零。通知级完整成功率仍是历史的 5/36（13.89%），离线改动不能证明真实模型成功率已经提高。

## Provider 协议与能力边界

实现使用 `POST {BASE_URL}/chat/completions`，`stream=false`，最终结果只从 `choices[0].message.content` 读取。必须有 `finish_reason=stop`，拒绝 reasoning-only、工具调用、拒答和截断。不把 reasoning_content 当最终 JSON；不剥离代码块后偷偷计作原始格式成功；不增加格式重试或自动切换服务。

M2-B 文档记录最终联调配置：用户配置的第三方 OpenAI 兼容服务，模型 `deepseek-v4.1-flash`，`json_object`，`thinking.type=disabled`，temperature=0，max_tokens=8192，timeout=90，额外 retries=1。数据库确认实际请求模型/Prompt/Schema 标识和 HTTP/用量；旧台账没有逐请求 payload，不能从它验证每个可选参数实际被服务采用。本轮没有凭据读取或新的模型目录探测。源码默认仍为 json_object、4096 输出、60 秒、1 次额外网络重试、3 条上限，不能把默认值误写为历史批次的配置。

| 能力 | 官方文档 | 当前第三方服务的证据 |
|---|---|---|
| JSON object | OpenAI JSON mode 与 DeepSeek Chat Completions 均支持；只保证格式模式，不保证领域事实 | 历史多次返回无效 JSON；旧 payload/finish_reason 缺失，不能证明参数被忽略或支持可靠 |
| JSON Schema strict | OpenAI 支持指定模型与 Schema 子集；DeepSeek 此 Chat Completions 文档列 text/json_object | 第三方是否支持尚未验证；可显式配置，无自动降级，也不宣称支持 |
| thinking.type | DeepSeek 官方可 enabled/disabled | 历史记录显示请求意图，不能证明路由真正关闭了思考 |
| Temperature | DeepSeek 思考模式中无效 | 新 deepseek profile 在显式 enabled 时不发送；generic profile 保持用户配置，并记录是否发送 |
| 输出上限 | DeepSeek 此接口使用 max_tokens；OpenAI 模型/接口存在其他参数要求 | 可配置 max_tokens / max_completion_tokens；实际接受及限制效果需小样本验证 |
| 模型 ID | 当前 DeepSeek 文档列 deepseek-flash/deepseek-v4-pro | 用户别名曾由目录发现并实际使用；不将官方名称替换为用户别名的“修复” |

依据：[DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)、[Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)、[Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)、[OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat)。这些是官方 API 文档，不是第三方路由的能力证明；Responses API 的能力也不能直接移植到本项目 Chat Completions。

## 离线修复与证据标准

新增安全诊断表 `llm_request_diagnostics`，仅在显式发送路径或临时测试库迁移到 SQLite user_version=4。旧台账和数据保留。记录阶段布尔值、受限错误码、finish_reason 枚举、content/reasoning 字符数、可用的 reasoning Token 数、JSON 行列与代码块标记、Schema 字段路径/缺少的固定字段、实际发送的格式/输出上限/temperature 标记、返回模型 ID 是否一致。仍不保存原始响应、推理文本、错误正文、凭据或个人信息。旧记录无诊断时显示未知，不补造。

证据定位分别在标题、正文、来源和各链接名称中查找，不能拼接不同字段形成引用。只允许 Unicode **NFC** 和 Unicode 空白字符差异，保留原始字段与半开区间位置；没有 NFKC、大小写折叠、标点删除、HTML 二次清洗、编辑距离或语义相似匹配。规范化匹配定位真实输入跨度，仍保留模型引用，不自动补写引用。

`location_status` 专门表示原文定位；最终 `status` 与 `support` 表示事实支持检查。状态包括 valid、normalized_match、unsupported、not_found、missing，另有 unverified 表示可定位但语义未核实。定位工具的成功不能单独变成事实已核实。v1 未覆盖的语义均显示 unverified；新 v2 对关键资格、政策、参与入口、分类和日期使用保守检查，不支持的判断降为 unknown/uncertain，保留提出的值与引用供审核。

短引用会检查完整句子中的否定和范围限制，避免从“不允许使用 AI”截出“允许使用 AI”，也避免把图片制作许可扩展成所有作品许可。重复出现的短引用检查各处上下文。限定专业、学院、年级或推荐要求不能变成普通本科生无条件 eligible。日期仍需明确年份、有效日期及对应用途；不从发布时间补年，不把举办日期作为报名截止。

这些守卫不是通用语义证明器。规则无法确认的内容保留待审；只读重验可能比旧校验更保守，拒绝列表明确标识，没有覆盖历史缓存。最终 RSS 不因模型技术成功、规则命中或 fact_status 自动发布。

## 可选的简短初筛契约

原 v1 一次输出 22 个顶层字段，含主办方、完整报名信息、多个日期和重复理由。没有足够证据证明字段数量就是首次失败的根因；为降低不必要的输出负担，新增显式可选 `CDU_LLM_SCHEMA_MODE=screening_v2`，默认 detailed_v1 保持兼容。

v2 的 `schema_version=llm-screening-v2`、独立 Prompt 与 `llm-v2:` 缓存指纹，11 个核心字段：notice_id、is_participation_opportunity、category、ai_relevance、ai_use_policy、eligibility_status、opportunity_status、evidence、missing_information、recommendation、review_required。证据条目只有 field/quote/value，报名日期仅在原文明示且用途单一时提供；不要求输出赛事卡片。

空证据、未知资格、未说明政策、标题信息不足和附件未解析可以构成**成功解析但业务待审**的初筛结果。编造引用、错误类型、缺少必需字段仍是技术校验失败。日期/许可等存在但不支持判断时保留 proposed_value、unsupported 和缺失项，不能自动推荐或排除。`verified` 只表示该版本保守守卫通过，仍不是人工核实或发布批准。复杂规则超出表达范围时继续待审。

旧 NoticeAnalysis JSON 通过新增字段的缺省值继续可读；旧缓存不迁移为 v2，不混合不同 analyzer/Schema 版本评测。`llm history --analysis-version ...` 无需 Provider 配置，只读导出并标识 archive_only=true、revalidated=false。`llm compare` 当前校验拒绝的旧缓存另列 cache_revalidation_rejections；业务不支持的重验不假装“尚未分析”。

## 新评测和推荐口径

新 compare 报告版本 m2b-evaluation-v2，分 technical、business、system。旧 retain+uncertain+失败保护矩阵仅在 legacy_retention_metrics 留档；顶层模型 Precision/Recall/F1 在全量没有可判定结果时留空。明确的成功结果子集放在 comparable_metrics，并给出样本数量、正标签数量和覆盖限制。技术失败和不确定结果不计为模型 TP，也不参与事实维度错误率。

历史数据重新表达为：5 条原校验成功，1 推荐、3 不推荐、1 业务待审；31 技术失败；合计待审核 32 条。4 条可比较判断全是人工负例：TP=0、FP=1、FN=0、TN=3；子集 Precision=0、Recall=null、F1=0。没有可比较的人工正样本，不能宣称模型召回正样本成功。全量模型指标无法评估。

系统保护保留 33 条候选、自动排除 3 条，候选召回 9/9=100%；31 条由于模型故障进入待审。这是系统容错指标。历史规则基线 Precision=31.58%、Recall=66.67%、F1=42.86% 的原报告保留。

正式机会目标的口径：当前可报名；未来开始报名；规则/时限不明待审；历史学习资料；新闻/行政。历史资料和新闻/行政默认不进入正式机会 RSS；按报名事实和参考时点判断，不能按文章发布早晚判结束。原开发 RSS 继续作为原始通知导出，GUID 与行为不变，未新增生产 Feed。

人工正标签中 ID **2、9、35** 有获奖报道/晋级名单信号，ID **30** 有研究生资格信号，进入只读口径复核清单。规则提示不等于人工标签错误，也不能直接修改真值。其他含复杂日期/附件的通知继续列为核验风险。

## 离线复现

```powershell
.\.venv\Scripts\python.exe -m cdu_radar llm diagnose --output data/m2b1-diagnosis-final.json
.\.venv\Scripts\python.exe -m cdu_radar llm history --analysis-version llm-v1:89e8a0e080bd523db08b159c --output data/m2b1-history-v1.json
.\.venv\Scripts\python.exe -m pytest -q
```

已有输出默认拒绝覆盖；诊断也禁止覆盖输入的两个历史报告。诊断不创建表、不写库、不加载 Provider/Key、不发网络请求。详细逐通知归因、输入风险、阶段计数、评测与标签复核保存在本地忽略 JSON；公开文档只有聚合统计和通知 ID，没有真实原始响应。

回放范围必须区分：31 条失败只有拒绝码，无法回放原始输出；5 条原成功缓存可只读重验。旧协议输出的合成回归，以及 v2 的未知/否定/范围/日期/缓存场景由离线测试验证，不是新模型表现。

只读重验实际又将 ID 7（参与依据）、14/29（本科资格依据）转入业务待审：原技术成功仍 5 条，当前可明确比较 2 条不推荐、34 条待审，其中故障 31 条、业务不确定 3 条，not_analyzed=0。这是当前守卫对历史已解析缓存的保守处理，不是新模型结果、标签修正或历史报告替换。本地 `data/m2b1-v1-replay.json` 单独记录拒绝阶段。

## 待批准的小样本计划

本轮不执行。建议最多 **5 条通知、5 次 POST**，逐条请求并检查结果后继续；不额外探测模型目录、不增加重试、不重跑 36 条。

| 顺序 / ID | 代表性 |
|---|---|
| 1 / 1 | 人工负例、旧校验成功、历史获奖新闻，验证排除与最终文本链路 |
| 2 / 34 | 人工正例、旧 invalid_json，直接检验格式兼容 |
| 3 / 19 | 人工正例、旧 AI 政策失败、含未解析附件，检验许可范围和待审 |
| 4 / 20 | 人工正例、旧引用定位失败，检验定位与事实支持分层 |
| 5 / 9 | 人工正例、旧 invalid_json、历史获奖报道，检验旧标签口径与当前机会目标分离 |

保留用户服务与模型 `deepseek-v4.1-flash`；采用 screening_v2 + json_object + 显式 thinking disabled + temperature=0 + max_tokens=4096 + timeout=90 + retries=0。每条输入上限 20000 字符；准备好的脱敏预览分别为 3733、3721、5561、5261、3643 字符，5 条共 21919 字符。预览的 Provider URL 为明确的占位地址，实际服务由用户已有配置提供。

输出总上限为 **20480 Token**，前提是路由遵守 max_tokens；输入 Token 因第三方 tokenizer 未知，不能仅由字符数给出严格 Token/金额上限。用户单价未知，不套官方价格。预算表达为 `(实际输入 Token × 输入每百万单价 + 实际输出 Token × 输出每百万单价) / 1000000`，实际账单/缓存折扣另核验。本轮已消耗的新付费 Token 为 0。

任一 HTTP/网络、空最终文本、截断或 JSON/结构格式错误立即停后续小样本；返回 thinking 内容与 disabled 意图不符时先调查服务协议。证据不足的业务 unknown 可以继续观察，引用捏造/未定位或不支持的事实只能待审。新批处理另有可配置格式熔断，默认连续 2 条格式错误停止；小样本设置 stop_after_format_failures=1，并逐条执行，运输故障由逐条核验停止。格式熔断没有提高 retries。

5 次请求不能同时充分证明 json_schema 的可靠支持。本建议先验证 json_object 基线；若希望专门测 json_schema，必须重新提出不超过 5 次的具体替代方案并批准，不能后台追加请求。任何一次成功仅证明该响应符合要求，不能证明路由具有强约束保证。

## 验证与交付记录

初始 231 项离线测试通过，最终 **271 passed**，`pip check` 无损坏依赖，`git diff --check` 通过。新增 40 项回归覆盖规范化原文位置、否定与许可范围、missing/unsupported、年级/专业限制、日期与参考时点、Schema/版本隔离、旧缓存只读查看、分层指标、安全诊断台账和格式熔断。分支 `codex/m2b1-offline-stability`，开发起点 `7eb1bc0`；本地提交记录见交付消息，没有远程推送。

前后 SHA-256 检查覆盖 m2b.sqlite3、m2a.sqlite3、人工标签 CSV、两份 revalidated 历史报告和历史 full-analysis 文件；全部相同。原库 user_version 不因离线诊断迁移，llm_requests 仍为 50 条，人工标签仍为 36 条。新增诊断表仅由 MockTransport 临时库测试了迁移与回归。
