# M2-B.2b 单条真实探针：本次待批准计划（2026-10-14）

执行更新：本计划下方保留调用前的检查快照。用户随后明确批准本次单条调用，已执行 ID 34 **1 次 POST**，采用默认脱敏诊断，未同意或启用完整响应留存。结果见 [M2B2B_RESULTS.md](M2B2B_RESULTS.md)；没有继续其他样本。

本次尚未批准调用，实际 POST **0**、新增付费 Token **0**。不沿用 M2-B.2 的批准，不运行五样本脚本，不开发新功能。

## 检查结果与唯一请求

当前分支 codex/m2b2a-json-wrapper、提交 f919704，检查时工作区干净。**324 passed**，pip check 与 git diff --check 通过。最新 Prompt 为 student-opportunity-screening-v2-format-v3:3e0701b1fa1b，Schema 为 llm-screening-v2:dc6d12ad780c，analyzer 为 llm-v2:03002ab7deec1a5e458feda7。没有改代码、Prompt、Schema 或事实规则。

ID **34** 的本地原文、输入哈希与上一轮提案一致。人工 worth_recommending=true，参考时点 2026-10-14T00:00:00+08:00；资格、AI 政策和报名时间的人工维度真值为空。原 M2-B.2 是 Markdown 围栏触发 JSON 失败，新分析版本下尚无请求。模型只收到脱敏通知，不收到人工答案、标注者或备注。

沿用原 Provider 地址和 deepseek-v4.1-flash，固定参数：

```json
{
  "model": "deepseek-v4.1-flash",
  "response_format": {"type": "json_object"},
  "thinking": {"type": "disabled"},
  "temperature": 0,
  "max_tokens": 4096,
  "stream": false
}
```

timeout=90（每 HTTP 阶段）、retries=0、batch_limit=1。只传入 [34]，累计最多 **1 次 POST**，成功或失败都结束；不自动继续 1、19、20、9。输入预览 3702 字符，上限 20000 字符；不截断原文。输出上限 4096 Token 的效果仍取决于第三方执行参数，价格和 tokenizer 未取得，金额未知。

冻结计划 `data/m2b2b-plan-v1.json` 指纹：009d991391e9ebe7680134157f54a29fb43b26437fe03f593324d0ff015b77f3。源库/输入/配置/请求正文与版本都固定；当前本地没有可用服务密钥，不重用此前临时 Key。批准后须安全配置已有服务的密钥，不在聊天、命令文本、Git 或诊断中保存明文。

## 本次执行与判断

批准后备份到新的独立运行 SQLite，旧源库和 M2-B.2 结果不改。通过现有 execute_samples(..., [34]) 执行，不使用五样本 main --send。技术失败或输出预算/协议警讯立即停止，禁止重试。每条记录先落请求意图，再保存最终状态和 usage。

分别记录 HTTP、finish_reason、direct_json / normalized_markdown_fence / invalid_json / unsupported_wrapper、Schema、引用位置/规范化状态、事实支持与 unsupported/missing、最终候选/待审、与人工推荐标签的一致性，以及输入/输出/总 Token。无 usage 或无价格时标未知，不写零费用。

技术成功不等于事实全部核实或业务正确。unsupported 结论仍降为 unknown 并待审；解析或 Schema/引用失败的故障保护也不能当作模型推荐。所有结果 review_status=pending，不能自动批准发布。

Prompt 在 M2-B.2a 同时加强了格式指令；本次输出与历史不同，不能把变化全部归因于解析器。单个成功样本也不能宣称总体准确率达标。是否适合另行小样本验证，要在本次结果之后再判断，不自动扩大预算。

## 留存选择与隐私边界

**默认且当前唯一已启用方式：有界脱敏诊断。** 未获得完整留存同意前，执行过程不写完整最终 content，仍用现有本地脱敏片段、错误路径/行列/摘要与阶段记录；不得事后声称能恢复完整文本。

可另外选择完整留存：只保存经过安全检查的完整模型最终 content、finish_reason、usage、版本/输入哈希和必要重放信息；不保存请求头、密钥、reasoning_content、整个 HTTP 交换或无关通知。保存之前清除已知凭据与不必要个人标识；若无法可靠去除敏感信息，则取消完整留存，退回脱敏诊断。

保存位置在仓库外 `%LOCALAPPDATA%/CDUOpportunityRadar/private-probes/m2b2b-id34-<request_uid>.dpapi`，使用 Windows 当前用户 DPAPI 加密，不生成明文副本。已通过合成数据的内存加密/解密往返检验，未保存真实响应。该方案是本次探针的可选操作，不加入项目功能或改变 Provider 验证过程。

每个保存包写 created_at、expires_at，**有效保留期为保存后 24 小时**。在任何重放/后续处理前检查到期状态；过期则清理。用户也可提前按实际返回的文件路径执行 `Remove-Item -LiteralPath '<具体加密文件路径>'`；仅删除该文件，不递归清理目录。本计划没有创建定时任务，因此不会声称已实现无人值守自动删除，届时需执行清理命令或在下一次访问时清理。

若安全脱敏改变了 content，须标明 lossy/privacy_transform；它是完整的脱敏文本，不再是逐字原始响应。保留原请求当时观测到的错误/成功状态，不以脱敏重放覆盖历史结论。诊断/加密包不自动提交 Git，公开报告只放 ID、计数与必要结论。

## 本次审批边界

需要本次明确批准 **ID 34 最多 1 次 POST**。完整留存是独立选择：只批准调用、不指定留存时，按默认脱敏方式执行；只有另外明确同意“完整最终 content、本地加密、24 小时”时才启用该方案。服务地址、历史批准和离线测试通过都不算本次付费或完整留存批准。

已检查的 70 个既有本地数据/报告文件哈希一致，原运行库仍 51 请求、36 人工标签，新版本下 ID 34 的调用数为 0。没有修改原始通知、缓存、人工标签、GUID，没有 QQ、正式 RSS、M2-C 或部署。当前仅新增这份计划文档和忽略的本地预览/检查结果，未提交、未推送。
