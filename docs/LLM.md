# M2-B 模型筛选与对照评测

M2-B.3 的 screening_v2 事实协议增加本地 `validation_version` 和 `field_validation`，逐字段保留模型提议、可信值、引用索引、原因和错误路径。请求 Schema 保持 v2，Prompt/实现形成新的缓存版本。分类不再因参与机会或日期未知被连带清空；错误日期可局部降级，但时间依据不足仍阻止认定开放，未核实推荐始终待审。旧记录可原样只读查看；详细版本、测试及 ID 34 数据边界见 [M2-B.3 离线报告](M2B3_OFFLINE.md)。

模型分析与人工标签、审批、原始通知和 RSS 分开。LLM 不读取人工答案，没有工具或附件下载权限。M2-B.1 的报告分别显示技术流水线、明确模型判断与系统故障保护；`retain + uncertain` 包括故障保留，只属于系统候选指标。完整模型指标在失败/未知导致覆盖不足时留空。详细故障归因、协议能力边界、初筛 Schema 与待批准计划见 [M2-B.1 离线报告](M2B1_OFFLINE.md)。

## 配置

支持 OpenAI-compatible `POST {BASE_URL}/chat/completions`，复用 httpx，不引入模型框架。所有配置从进程环境读取，不内置服务、模型或密钥，不自动加载 .env 文件。下表里的模型/服务必须由用户指定。密钥不要粘贴进命令文本、代码或普通日志；PowerShell 可交互读取后放入当前进程环境：

```powershell
cd 'D:\AI项目\cdu信息聚合插件'
$env:CDU_LLM_BASE_URL = 'https://your-provider.example/v1' # 替换为自己的兼容 API 地址
$env:CDU_LLM_MODEL = 'your-model-id'                     # 替换为服务实际模型 ID
$env:CDU_LLM_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'API Key' -AsSecureString)).Password
```

| 环境变量 | 默认/作用 |
|---|---|
| CDU_LLM_BASE_URL | 必填；HTTPS API 前缀，不包含 /chat/completions；仅 loopback 允许 HTTP |
| CDU_LLM_MODEL | 必填；提供商实际模型 ID |
| CDU_LLM_API_KEY | 默认的 Key 环境变量，密钥仅作 Authorization 请求头 |
| CDU_LLM_KEY_ENV | 可指定其他已有 Key 环境变量名，例如 OPENAI_API_KEY |
| CDU_LLM_TEMPERATURE | 默认不发送；支持的提供商可设置 0—2 |
| CDU_LLM_THINKING_MODE | 默认不发送；支持的提供商可显式 enabled/disabled，发送 thinking.type；进入分析版本指纹 |
| CDU_LLM_TIMEOUT | 每请求阶段默认 60 秒，不是整批的总时限 |
| CDU_LLM_RETRIES | 默认额外重试 1 次，范围 0—3；网络超时/连接、429 和部分 5xx 有限重试 |
| CDU_LLM_MAX_INPUT_CHARS | 默认 20000，system+user 消息总字符数（含 Schema）；不是 Token 数。过长明确失败，不截断正文或猜测剩余内容 |
| CDU_LLM_MAX_OUTPUT_TOKENS | 默认 4096，发送 max_tokens；截断或只有 reasoning 没有最终文本明确失败 |
| CDU_LLM_BATCH_LIMIT | 默认最多 3 条，最高 100；CLI --limit 不能超过它 |
| CDU_LLM_RESPONSE_FORMAT | 默认 json_object；支持时可用 json_schema，或显式 none；所有模式均做本地固定 Schema 校验，不自动降级 |
| CDU_LLM_SCHEMA_MODE | 默认 detailed_v1；可显式 screening_v2，独立 Schema/Prompt/缓存版本 |
| CDU_LLM_PROTOCOL_PROFILE | 默认 generic；deepseek 在显式启用 thinking 时不发送 temperature，不改变服务或模型 |
| CDU_LLM_OUTPUT_TOKEN_PARAMETER | 默认 max_tokens；可显式 max_completion_tokens，实际第三方支持需验证 |
| CDU_LLM_STOP_AFTER_FORMAT_FAILURES | 默认连续 2 条格式失败停止本批，1—10；小样本建议 1；不增加重试 |
| CDU_LLM_CAPTURE_FAILURE_DIAGNOSTICS | 默认 false；可显式 true/1，在本地台账记录有界脱敏结构片段和字段路径；不保存原始响应 |
| CDU_LLM_PROMPT_FILE | 可选可信本地补充 Prompt；内容进入版本指纹，不读取无关配置 |
| CDU_LLM_INPUT_PRICE_PER_MILLION | 可选每百万输入 Token 单价 |
| CDU_LLM_OUTPUT_PRICE_PER_MILLION | 可选每百万输出 Token 单价；必须与输入价一起设置 |
| CDU_LLM_COST_CURRENCY | 设置单价时必填计价单位；只是配置单价估算，不是账单或缓存折扣的精确费用 |

不打印密钥或服务原始错误响应，不跟随重定向，不降低 TLS 验证。HTTPX 仍使用当前环境代理。响应体流式限制 1 MiB，401/403/普通 4xx 不重试，并停止本批后续发送。

## 预览与发送

默认数据库 `data/m2b.sqlite3`，不存在时报错。M2-B 使用 M2-A 的 SQLite backup 副本，包含相同人工标签，原库不覆盖。模型相关输出要求独立 .json 文件，已有报告默认拒绝覆盖；需要重跑同名报告时显式 --overwrite。

```powershell
# 不需要 Key，不发请求，不迁移或写入数据库；默认只挑前 3 条未分析通知。
.\.venv\Scripts\python.exe -m cdu_radar llm analyze --dry-run --output data/m2b-prompt-preview.json

# 指定少量通知，检查标题、正文、来源、附件名称/链接和实际 Prompt。
.\.venv\Scripts\python.exe -m cdu_radar llm analyze --notice-id 1 --notice-id 3 --notice-id 10 --limit 3 --dry-run --output data/m2b-preview-small.json

# 查看预览后显式发送；该调用会外发脱敏内容并可能产生费用。
.\.venv\Scripts\python.exe -m cdu_radar llm analyze --notice-id 1 --notice-id 3 --notice-id 10 --limit 3 --send --output data/m2b-smoke.json

# 重复同样 ID/模型/Prompt/输入时应命中缓存，不重复调用。
.\.venv\Scripts\python.exe -m cdu_radar llm analyze --notice-id 1 --notice-id 3 --notice-id 10 --limit 3 --send --output data/m2b-smoke-cached.json

# 默认挑当前配置下尚未尝试的通知，每次最多 3 条；不会反复消费旧失败记录。
.\.venv\Scripts\python.exe -m cdu_radar llm analyze --limit 3 --send --output data/m2b-next-batch.json

# 明确重试失败：使用 --retry-failed，或通过 --notice-id 指定失败通知。
.\.venv\Scripts\python.exe -m cdu_radar llm analyze --retry-failed --limit 3 --send --output data/m2b-retry.json
```

要有意扩大一批处理数量，先改 CDU_LLM_BATCH_LIMIT，再显式 --limit。批量上限是通知数，真实请求还受 retries 影响；达到上限不继续自动跑下一批。每个请求发送前即保存台账，成功/失败后更新该条，崩溃中的 started 表示结果/费用未知。不要把未知用量当成没有消耗。

发送输入使用 M2-A 脱敏文本，再隐藏带年级标记的人名、凭据样式和链接查询参数；未发送原始 HTML、人工标签、备注、附件正文或文件。这个边界不是完整人名识别系统；先看本地预览核实剩余内容。链接删掉查询参数后不能被模型当成确定完整报名地址。确定判断须有原文可定位引用，资格/政策的缺失不能变成允许，日期用途和多个截止时间严格校验。

JSON 的身份、模型、分析时间和审批状态由程序设置；模型额外输出 approved、工具调用或未允许字段会失败。student_opportunity 是可空布尔值，其余字段兼容 NoticeAnalysis；核实事项存为 verification_items 的 unknown 证据。JSON mode 中省略的可空文本字段归为 null；证据 value 的布尔值转为文本，原文完整年月日转成规范日期。关键分类/布尔/理由/证据字段缺失仍失败，不按规则预测补模型答案。

LLM 日期验证与 M2-A 规则的固定标签解析分开：短日期引用会在原正文句子中定位用途，不能从发布日期补年份；同一句明确年份的区间允许省略重复年份，跨年不明时不猜测。证据仅有月日且归一日期未知时可以保留日期片段，不能当作已确认截止。时间根据标签参考点在本机计算，不能用模型自己假定的“今天”排除候选。错误只记录白名单校验代码，不存原始响应/推理文本。

## 对照评测

```powershell
# 只读取缓存，不发 API，也不写分析/标签/审核表；没有 Key 也能评测已有预测。
.\.venv\Scripts\python.exe -m cdu_radar llm compare --output data/m2b-comparison.json --errors-output data/m2b-errors.json
```

读取已经实测过的历史分析版本，可以显式加 `--analysis-version`；程序核对模型及 Prompt/Schema 内容版本，并以当前证据校验器重验，不发新 API。不要把不同版本或不完整结果混成当前模型成绩。

```powershell
# 本机首轮实测结果的无付费重现（已存在报告时换输出名或明确 --overwrite）：
.\.venv\Scripts\python.exe -m cdu_radar llm compare --analysis-version llm-v1:89e8a0e080bd523db08b159c --output data/m2b-comparison-history.json
```

默认按每条标签自己的参考点，沿用现有 evaluate 的正样本定义。可用 --notice-id 明确限制诊断样本；完整对照不限制 ID，覆盖全部有效 human 标签。可用 --as-of 指定固定时点，但只纳入时点一致的人工标签。没有人工标签或旧输入版本不能计入监督指标。

新报告的 technical 记录各技术阶段；business 记录成功结果、推荐/不推荐/待审及可比较标签；system 记录包含故障保护的候选召回和人工队列。失败和业务不确定性不计模型 TP 或事实维度错误；它们仍在总体覆盖与未评估清单中。comparable_metrics 只表示明确成功结果子集，并标明子集正标签数；全量模型覆盖不完整时顶层 Precision/Recall/F1 留空。旧候选矩阵在 legacy_retention_metrics 留档，历史文件不覆盖。当前校验对旧成功缓存的拒绝另列 cache_revalidation_rejections，不假装尚未分析。

不依赖当前 Provider 配置的历史原样查看与故障诊断：

```powershell
.\.venv\Scripts\python.exe -m cdu_radar llm history --analysis-version llm-v1:89e8a0e080bd523db08b159c --output data/m2b1-history-v1.json
.\.venv\Scripts\python.exe -m cdu_radar llm diagnose --output data/m2b1-diagnosis-final.json
```

history 为 archive_only，不宣称经过新校验。diagnose 不创建表、不写库、不读 Key、不发请求；旧输出不存在的细节均为 unknown_unobservable。

dimension_compared 记录用户实际填写了多少资格、截止时间、政策等细分真值。首批 36 条只填写推荐值，这些维度暂无真值，空错误数不表示零错误。对照不把模型/规则预测补成标签，人工审核决定也不自动改成批准。

usage 是当前分析版本的请求/已知 Token/可估费用；usage_all_versions 是这些通知所有历史模型版本的累计消耗。接口没有 usage 或配置没有单价时费用留空，不能报告为 0。保留原始 `data/m2b-rule-baseline.json`，不要覆盖它或按真实评测集不断改 Prompt；如要调优，先明确开发集和保留集，少量样本不能证明生产效果。

## 存储、错误与兼容

按需追加 schema_version=3 的 llm_requests 表，原 notices、notice_sources、notice_links、manual_labels、analysis_reviews 保留。模型预测仍存 notice_analyses，analyzer_version 指纹覆盖 notice 的完整 input_hash、模型/服务、Prompt/Schema 及实现/分析参数。人工标签和审核仍绑定原 input_hash，不随模型配置改变。Prompt/Schema 版本还有内容哈希写入请求台账。

超时、连接、鉴权、限流、异常 HTTP、无最终文本、无效 JSON、缺字段、无效枚举/布尔、证据不匹配、过长和空正文均保留失败分析及待审状态；有 HTTP 响应时保留安全用量字段，不保留原始错误正文。失败不是“不相关”，后续显式重试或人工核验。损坏缓存不会阻断整批，而会重新分析；已有人工审核历史始终独立。

旧 collect/export-rss/analyze/label/evaluate/review 命令保留，原 RSS GUID 不变。模型批准不了正式发布，本轮没有 AstrBot、QQ 推送或生产部署。

接口依据：[Chat Completions API](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat)。兼容提供商可能只支持 JSON mode；本地 Schema、证据及 finish_reason 验证始终执行。

DeepSeek 的 [Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/) 和 [JSON Output](https://api-docs.deepseek.com/guides/json_mode/) 说明了可选 thinking.type 和 max_tokens 的输出边界。本轮真实联调在用户指定服务中使用实际目录 ID deepseek-v4.1-flash；关闭 thinking、temperature=0、max_tokens=8192、timeout=90、retries=1，Prompt 内容始终保持初版，没有根据人工标签调优。早期截断/校验失败请求的 Token 也计入 usage_all_versions，不因后续兼容修复而消失。

首轮 36 条真实对照仅 5 条通过严格校验、31 条失败（86.11%）。规则 F1=42.86%，LLM 加失败保留的 F1 同为 42.86%；后者 Recall=100% 不代表模型能力更好。详细过程、错误数量及 50 次 POST 的已知 183905 Token 消耗见 VALIDATION.md；没有单价，费用未知。当前不适合自动推荐或正式 RSS。

M2-B.2 审批前只完成离线准备，真实请求为 0；两关样本、固定预算、参数、诊断留存及审批边界见 [M2B2_TEST_PLAN.md](M2B2_TEST_PLAN.md)。专用 probe 默认为 dry-run，真实发送要求明确批准和一致的计划指纹，在独立 SQLite 副本中执行，任一技术失败停止全部剩余请求。

随后获批的 M2-B.2 第一关只发送 ID 34：HTTP 200、finish_reason=stop，最终 content 带 Markdown 代码围栏，裸 JSON 校验失败。累计 1 次 POST、1764 Token，第二关未执行，未重试。详见 [M2B2_RESULTS.md](M2B2_RESULTS.md)；Schema、引用与业务判断没有进入真实验证，原人工标签/数据库不变。

M2-B.2a 已加入严格包装兼容：优先纯 JSON Object，仅允许完整包裹全部响应的单个三反引号 json/无语言块。记录 direct_json 与 normalized_markdown_fence 分开的指标，非法 JSON、其他包装及后续 Schema/引用/事实错误仍拒绝。v1/v2 Prompt 只加强格式指令并更新 Prompt 指纹，Schema 与业务规则保持原样。ID 34 仅有截断脱敏片段，不能完整离线重放；本轮新增付费请求 0。边界、324 项回归结果及待批准单条计划见 [M2B2A_JSON_COMPAT.md](M2B2A_JSON_COMPAT.md)。
