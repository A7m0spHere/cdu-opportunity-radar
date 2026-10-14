# M2-B.2a JSON 响应包装兼容性修复（2026-10-14）

本轮仅离线修复，新增真实 POST **0**。起始 `codex/m2b2-api-probe`、`eaae9e0`、工作区干净，没有其他尚未提交改动。修复在独立本地分支 `codex/m2b2a-json-wrapper`，不推送。

## 根因与 ID 34 的重验边界

M2-B.2 保存的诊断确认：HTTP 200、finish_reason=stop、最终 content 以 Markdown 围栏开头，裸 JSON 在第 1 行第 1 列失败。输出 387 Token，没有输出上限截断证据。第三方收到 json_object 请求但返回围栏，不能证明该参数得到强制执行；服务透传、上游输出和路由包装的责任仍无法区分。

本地 `data/m2b2-results.json` 和运行 SQLite 只有 `redacted-failure-v1` 的 **384 字符掩码结构片段**，原 content 为 **1122 字符**。缓存是失败保护结果，没有完整最终文本。没有找到安全保存的完整原始响应，不能恢复尾部、字段内容或语义。

因此 **没有重放 ID 34**。JSON 是否恢复、Schema 是否通过、引用能否定位、事实支持及与人工标签是否一致均为未知。新的只读版本化诊断 `data/m2b2a-id34-offline-v1.json` 明确写 full_response_available=false、replay_attempted=false；未把片段当模型输出，也未覆盖历史报告。

## 严格解析边界

独立函数 `llm_json.parse_model_json()`，解析器版本 `model-json-wrapper-v1`：先用原严格 decoder 解析完整响应，要求顶层为 Object；只有直接解析失败，才识别完整包住全部响应的单个 Markdown 代码块，再对原封不动的块内文本做严格 JSON 解析。

只接受恰好三枚反引号、语言为小写 json 或无语言标识、开闭围栏各占一行、块内没有其他围栏行；外围只忽略 ASCII 空格、Tab、CR/LF，支持 LF/CRLF。围栏外解释文字、多块、缺少闭围栏、其他语言、波浪号/四反引号、BOM 均拒绝。代码块里的 JSON 若不完整、重复键、非有限数值、顶层数组/字符串/null 或语法非法，仍拒绝。

不从自然语言里寻找 JSON，不补字段，不修语法，不更改业务字段或引用。JSON 字符串里的反引号保持原值，不递归剥离。HTTP API 包装继续使用原始 strict_json，不应用模型文本兼容处理。finish_reason、拒答、工具调用和最终 content 检查仍在解析之前。

成功后仍执行既有 Schema、引用定位、日期用途、资格与 AI 政策约束、原始输入检查和审批边界。纯格式恢复不等于事实核实或业务正确，也不能产生 approved/rejected。

## 诊断与统计

保存 `parse_method`：direct_json、normalized_markdown_fence、invalid_json、unsupported_wrapper。未进入解析时为 null。另记 parser_version、direct_json_ok、直接解析错误坐标、兼容解析错误坐标/安全错误代码。

`json_success` 表示接受的 Object 解析，包括严格围栏兼容；新增独立 `direct_json_success` 和 `json_parsing` 四类计数，不能把兼容成功伪装为提供商原生 JSON 成功。Schema/证据失败时保留已发生的解析方法。旧诊断没有这些字段时，新增计数标记 unobservable_or_not_reached，旧原始成功/失败记录不改。

诊断仍使用现有有界脱敏机制，不保存完整响应、自由文本或推理。本轮没有增加真实输出留存范围。

## Prompt、契约与缓存兼容

v1/v2 只加强格式指令：输出单个 JSON Object，不添加 Markdown 围栏或解释性前后缀。赛事推荐、资格、AI 政策、日期、unknown 和人工审核口径均未改变。

Schema 仍为 llm-schema-v1 / llm-screening-v2，Schema 内容指纹不变。Prompt 标识分别为 student-opportunity-v1-format-v2、student-opportunity-screening-v2-format-v3。新解析模块加入分析实现指纹；新旧分析版本不混合评测，旧缓存通过原 dataclass 与 history 只读能力继续可读，没有迁移或改写历史失败。

Provider、模型及 response_format 模式不变，没有切换服务端 json_schema。接受合法围栏只代表客户端兼容，不能据此宣布第三方 JSON mode 已可靠。

## 离线验证

基线 **286 passed**，最终 **324 passed**，新增 38 项回归；pip check 无损坏依赖，git diff --check 通过。测试夹具禁止真实 socket。

覆盖纯 JSON、完整 json/无语言围栏、CRLF/外围空白、解释前后缀、多块/嵌套/不完整围栏、非法 JSON、重复键/NaN、非 Object、原文坐标、API 包装不剥围栏、字符串值不改写及旧缓存兼容。Provider 合成回归证明：围栏内非法 Schema 仍失败；捏造引用仍失败；“未禁止 AI”不能成为允许；恶意正文诱导输出 approved 被拒绝；截断输出不进入兼容解析。依据不足仍为业务待审。

合成测试中的 Schema/引用失败是预期的守卫结果，不是 ID 34 的新实测失败。没有完整原响应，无法判断该通知剥除合法围栏后是否还有其他错误，不能声称真实问题已完全修复。

既有 66 个本地数据/报告文件前后 SHA-256 相同，涵盖人工 CSV、各 SQLite、缓存、原始通知和历史 RSS 文件；没有覆盖标签、重新采集或改变 GUID。新诊断与单条建议只写新文件。

## 下一次单条探针建议（未批准、未执行）

只重测 **ID 34，最多 1 次 POST**，不运行原五样本 --send 命令、不自动调用 1/19/20/9。保持用户服务及 deepseek-v4.1-flash，response_format=json_object、thinking disabled、temperature=0、max_tokens=4096、stream=false、timeout=90、retries=0，仍使用 v2 和完整事实检查。

新版输入预览为 3702 字符，输入 Token 和第三方单价未知，金额未知；单次输出上限 4096 以前端请求被服务执行为前提。使用新的独立运行库及结果文件，不能覆盖 M2-B.2。技术失败或协议预算警讯立即停。

单条建议保存在本地 `data/m2b2a-single-probe-proposal-v1.json`，包括实际参数、消息预览、输入/版本指纹和 requires_fresh_approval=true。当前 analyzer 为 llm-v2:03002ab7deec1a5e458feda7，v2 Prompt 内容指纹为 3e0701b1fa1b，Schema 指纹仍 dc6d12ad780c。

必须重新获得用户明确批准并安全配置有效密钥后才发送。下一次报告应分别展示直接 JSON、围栏规范化、Schema、证据、事实支持及业务结果，不能把仅恢复 JSON 解析算作筛选成功。没有 M2-C、AstrBot、QQ 或部署改动。

## Git

本轮只有 JSON 包装兼容这一项独立功能，将代码、合成回归及文档创建一个本地提交；实际提交记录见交付消息。真实诊断、人工数据、数据库与请求预览仍忽略，不自动推送。
