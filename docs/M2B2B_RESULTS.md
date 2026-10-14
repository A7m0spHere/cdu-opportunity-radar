# M2-B.2b 单条真实探针结果（2026-10-14）

本次明确批准后，仅执行 **ID 34、1 次 POST、0 次重试**。原生 JSON、v2 Schema 和引用定位通过，技术链路恢复；事实支持未全部通过，最终为 **uncertain / review_required / pending**。不能把它算作模型正确识别人工正例，也不能自动发布。ID 1、19、20、9 未调用。

## 实际配置与消耗

沿用用户配置的第三方 OpenAI 兼容 Provider，地址仅在本地忽略的计划中。模型 `deepseek-v4.1-flash`，返回 model 字段与配置一致；这不能证明第三方底层模型身份。

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

timeout=90（每 HTTP 阶段），retries=0，batch_limit=1。调用前核对冻结计划、源库、输入、代码、配置和实际请求正文指纹；只向既有执行函数传入 `[34]`，未运行五条样本入口。模型输入不包含人工答案或标注备注。

| POST | 输入 Token | 输出 Token | 总 Token | 耗时 | 费用 |
|---:|---:|---:|---:|---:|---|
| 1 | 1395 | 251 | 1646 | 2360 ms | 第三方单价未知，金额未知 |

usage 来自本次响应，总量等于输入加输出；cached_tokens=0。没有套用 DeepSeek 或 OpenAI 官方价格，也不能将未知金额写成零。

本次 Prompt 为 `student-opportunity-screening-v2-format-v3:3e0701b1fa1b`，Schema 为 `llm-screening-v2:dc6d12ad780c`，analyzer 为 `llm-v2:03002ab7deec1a5e458feda7`，解析器为 `model-json-wrapper-v1`。没有修改实现、Prompt、Schema 或事实校验规则。

## 分阶段结果

| 阶段 | 实际结果 |
|---|---|
| HTTP / API 包装 | HTTP 200，application/json；API 包装解析成功 |
| finish_reason | stop；没有截断信号 |
| thinking | 请求 disabled；未返回 reasoning_content，reasoning_tokens=0 |
| 最终 content | 存在，786 字符 |
| JSON | **direct_json**；原生 JSON Object，未使用围栏规范化 |
| v2 Schema | 通过，无 Schema 错误 |
| 引用定位 | 6 个提供的非空引用全部直接定位；normalized_match=0，not_found=0 |
| 事实支持 | 未全部通过：4 个 unsupported、2 个 unverified；另有 1 个缺失引用的派生检查项 |
| 分析链路 | analysis_state=success；request/json/schema/evidence/analysis_ok 均为 true |
| 业务状态 | candidate_decision=uncertain，needs_manual_review=true，fact_status=review_required，review_status=pending |

引用缺失检查项来自模型提出 `ai_relevance=unrelated` 却未提供对应证据；它不是第七个未定位引用。`evidence_ok=true` 表示给出的引用可定位，不表示所有引用支持结论；`analysis_ok=true` 表示完成了含未知降级的分析，不表示事实全部核实。

## 仍需处理的事实问题

以下归因依据新结果中保存的字段、位置和 proposed_value，以及现有校验代码的离线检查；没有补造完整模型响应。

| 字段 | 观察与处理 |
|---|---|
| is_participation_opportunity | 模型提出 true，但所引片段主要说明将举办竞赛，没有报名或招募词；现有参与机会校验拒绝该引用，降为 null。不能由片段拒绝推断整篇通知没有参与机会。 |
| category | 提出 competition，引用能定位且含竞赛表述；由于参与机会先被降为未知，分类的现有联动规则将其标为 unsupported，降为 unknown。这是连带拒绝，不能表述成模型编造赛事分类。 |
| eligibility_status | 提出 eligible；原文资格表述包含“本、专科学生”，现有 undergraduate 词表只覆盖“本科生”等写法，离线匹配为 false。全局 teacher_only / graduate_only / restricted 均未命中。该拒绝暴露了校验器的词表覆盖不足，不能把它全部归因于模型无依据。仍需核验选拔和技能条件，不能直接放宽为全体本科生均符合。 |
| ai_relevance | 提出 unrelated，无对应引用，降为 uncertain。不能在信息不足时自动判定不相关。 |
| ai_use_policy | 保留 unspecified，没有凭空认定允许 AI；本条未覆盖明确允许、禁止或限定用途的实际验证。 |
| 报名日期 | 两个日期引用可定位，但 value=null、原文日期未明确年份，保留 unverified，没有补造日期。 |
| opportunity_status | 模型提出 open，但没有支持参考时点仍可报名的明确年份日期；被标为 unsupported_at_reference_time，降为 unknown。模型自己也记录了年份缺失，却仍提出 open，存在结论与不确定信息矛盾。 |

本轮只验证和记录，不调整词表、证据选择、业务口径或评测脚本。没有关闭事实校验，没有自动补证或修改人工真值。unsupported 并不一律等于事实造假，需分别看引用选取、规则覆盖和真实信息不足。

## 业务比较与历史变化

ID 34 的人工推荐标签为 true，维度标签（资格、AI 政策、报名日期和机会状态）为空。本次最终是未知，`business_correct_against_human=null`，可比较的确定业务结果为 **0**，不计算 Precision / Recall / F1，不计 TP，也不把它当作明确负例。时间口径仍需人工核验，人工标签未改。

| 指标 | M2-B.2 ID 34 | 本次 ID 34 |
|---|---|---|
| HTTP / finish | 200 / stop | 200 / stop |
| 最终格式 | Markdown 围栏；裸 JSON 解析失败 | 原生 JSON，direct_json |
| Schema / 引用 | 未进入 | 通过 / 全部提供引用可定位 |
| 事实支持 | 未验证 | 校验完成，存在 unsupported / missing / unverified，保留待审 |
| 业务一致性 | 无法评估，技术故障保护 | 无法评估，事实与业务不确定性 |
| 输入 / 输出 / 总 Token | 1377 / 387 / 1764 | 1395 / 251 / 1646 |

原 M2-B 的 ID 34 历史技术失败在这次新版本请求中恢复，但历史结果没有被改写。Prompt 在 M2-B.2a 也加强了格式指令；本次走 direct_json，**没有在真实响应中触发围栏修复**。因此不能把变化全归因于解析器，也不能声称已真实验证 NFC / 空白规范化。围栏和规范化行为仍由离线测试支撑，旧完整响应缺失，不能追溯证明旧围栏内容通过全部校验。

## 诊断留存与数据完整性

用户批准本次调用，但未另行同意完整响应留存；采用默认有界脱敏诊断。没有保存完整最终 content、reasoning、敏感请求头或 API Key。成功响应未生成失败片段；保存了阶段、usage、版本、规范化分析和引用位置/支持检查，不能把这些视为可逐字重放的原始响应。

完整响应可选留存的仓库外 DPAPI 加密、24 小时期限与清理方式见 [测试计划](M2B2B_TEST_PLAN.md)。本次未启用，未创建加密包或定时任务。密钥只在请求进程中使用，未另写配置文件；运行后检查新诊断和计划文件未含该密钥，检查进程中没有残留该环境变量。

新的忽略文件为 `data/m2b2b.sqlite3`、`data/m2b2b-results.json`、`data/m2b2b-final-summary-v1.json`、`data/m2b2b-integrity-after-v1.json`；调用前计划和检查快照保留，不覆盖旧报告。

**70 个既有数据/报告文件 SHA-256 全部不变**。源运行库仍为 51 请求、370 分析、1 诊断；独立新副本为 52 请求、371 分析、2 诊断，只追加本次各 1 条，所有旧行保留，integrity_check=ok。两库 notices、36 条 manual_labels、analysis_reviews、notice_links、sources、notice_sources、request_events、collection_runs、units 逐表相同；原始通知、人工标签、既有 RSS 文件和 GUID 未改。

## 后续判断与验证状态

这次说明新版本可以完成单条真实技术分析，但没有得到可评估的确定推荐结论。**不建议立即批量扩大验证**：先离线检查资格同义表达覆盖、参与证据选取和分类联动拒绝，保留事实标准及版本区分，再提出需要新批准的小样本计划。若后续要验证围栏处理，需实际观察该包装；不能用此次原生 JSON 成功替代。

目前没有足够证据要求更换模型或 Provider；本次未再观察到格式故障，剩余问题同时涉及模型选择证据、报名状态判断和校验器覆盖。明确 AI 限制、复杂日期、负例正确性及其他旧失败样本均未验证。单个样本不能证明总体稳定性或准确率达标。

调用前 **324 项离线测试通过**，pip check 通过；调用后只新增报告，不改实现，无需重复整套测试。收尾检查 git diff --check。分支 `codex/m2b2a-json-wrapper`，HEAD `f919704`；本轮两份文档尚未提交、未推送。未部署、未接入 QQ 或正式 RSS，未开发 M2-C。
