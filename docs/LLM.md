# M2-B 模型筛选与对照评测

模型分析与人工标签、审批、原始通知和 RSS 分开。LLM 不读取人工答案，没有工具或附件下载权限。`retain + uncertain`（包括失败后待审的候选）作为保留候选，`exclude` 为排除；这不是发布批准。同一有效人工标签集对照规则与模型，不剔除失败或难例。尚未分析的记录会让完整模型指标留空，不能拿已完成的容易样本冒充全量结果。

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

报告包括两种方案的混淆矩阵、Precision/Recall/F1、正/负样本、不确定和失败比例、完整覆盖状态、具体 FP/FN、纠正的规则 FN 和被改错的原正确记录。missing 模型结果不算失败或成功：not_analyzed_count 明示数量，完整矩阵及主指标为 null；partial_confusion_matrix 只作联调诊断。已尝试的失败仍进入完整样本集，并作为待审候选处理，不能通过排除失败提高指标。

dimension_compared 记录用户实际填写了多少资格、截止时间、政策等细分真值。首批 36 条只填写推荐值，这些维度暂无真值，空错误数不表示零错误。对照不把模型/规则预测补成标签，人工审核决定也不自动改成批准。

usage 是当前分析版本的请求/已知 Token/可估费用；usage_all_versions 是这些通知所有历史模型版本的累计消耗。接口没有 usage 或配置没有单价时费用留空，不能报告为 0。保留原始 `data/m2b-rule-baseline.json`，不要覆盖它或按真实评测集不断改 Prompt；如要调优，先明确开发集和保留集，少量样本不能证明生产效果。

## 存储、错误与兼容

按需追加 schema_version=3 的 llm_requests 表，原 notices、notice_sources、notice_links、manual_labels、analysis_reviews 保留。模型预测仍存 notice_analyses，analyzer_version 指纹覆盖 notice 的完整 input_hash、模型/服务、Prompt/Schema 及实现/分析参数。人工标签和审核仍绑定原 input_hash，不随模型配置改变。Prompt/Schema 版本还有内容哈希写入请求台账。

超时、连接、鉴权、限流、异常 HTTP、无最终文本、无效 JSON、缺字段、无效枚举/布尔、证据不匹配、过长和空正文均保留失败分析及待审状态；有 HTTP 响应时保留安全用量字段，不保留原始错误正文。失败不是“不相关”，后续显式重试或人工核验。损坏缓存不会阻断整批，而会重新分析；已有人工审核历史始终独立。

旧 collect/export-rss/analyze/label/evaluate/review 命令保留，原 RSS GUID 不变。模型批准不了正式发布，本轮没有 AstrBot、QQ 推送或生产部署。

接口依据：[Chat Completions API](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat)。兼容提供商可能只支持 JSON mode；本地 Schema、证据及 finish_reason 验证始终执行。

DeepSeek 的 [Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/) 和 [JSON Output](https://api-docs.deepseek.com/guides/json_mode/) 说明了可选 thinking.type 和 max_tokens 的输出边界。本轮真实联调在用户指定服务中使用实际目录 ID deepseek-v4.1-flash；关闭 thinking、temperature=0、max_tokens=8192、timeout=90、retries=1，Prompt 内容始终保持初版，没有根据人工标签调优。早期截断/校验失败请求的 Token 也计入 usage_all_versions，不因后续兼容修复而消失。

首轮 36 条真实对照仅 5 条通过严格校验、31 条失败（86.11%）。规则 F1=42.86%，LLM 加失败保留的 F1 同为 42.86%；后者 Recall=100% 不代表模型能力更好。详细过程、错误数量及 50 次 POST 的已知 183905 Token 消耗见 VALIDATION.md；没有单价，费用未知。当前不适合自动推荐或正式 RSS。
