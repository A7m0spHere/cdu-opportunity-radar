# M2-A 筛选与分析契约

当前是可解释规则基线，没有语言模型调用。服务目标是成都大学普通本科生的学习、实践和创作机会；“信息热度”“标题包含 AI”都不是推荐依据。`retain`/`uncertain` 是供人工检查的候选，不代表批准推送。

## 类型与字段

`analysis_models.py` 的冻结 dataclass 实现输入、分析、证据及标签校验；`NoticeAnalysis.from_dict()` 拒绝额外字段、无效枚举、字符串布尔值、非法日期和缺少引用的事实。分析与原始 notices 分开存储。

| 字段 | 语义 |
|---|---|
| notice_id / content_hash | 原通知及原始正文版本；不回写正文 |
| input_hash | 标题、正文、来源、发布日期、URL、正文链接、附件信息及脱敏版本的完整输入指纹 |
| analyzer_version | 规则实现和配置的指纹；实现或配置变化不能复用旧结果 |
| category | competition / hackathon / workshop / lecture / project_recruitment / other_activity / news / administrative / unknown |
| relevance_level | direct_ai：活动主题涉及 AI 技术；ai_applicable：跨学科实践或允许 AI 作为工具；unrelated：明显不匹配；uncertain：不足以判断 |
| eligibility_status | eligible：明示覆盖普通本科生；ineligible：明确教师、仅研究生或其他院校专属；uncertain：资格缺失或有学院/专业/年级/推荐限制，不能推断所有本科生符合 |
| ai_use_policy | explicitly_allowed / partially_allowed / explicitly_prohibited / unspecified；名称可能适合 AI 不等于规则允许 |
| opportunity_status | open：明确最终截止仍未到；upcoming：明确未来报名开始；closed：最终截止已过或明示报名结束；unknown：其余，包括仅知道活动日期 |
| candidate_decision | retain：规则认为可保留；uncertain：信息不足但保留候选；exclude：结果新闻、明确行政/资格不符、明确禁 AI 且未识别 AI 学习目标，或已确定报名结束 |
| event_name / organizer / target_audience | 名称目前采用标题候选，不保证正式全称；主办方只取原文明确标注；受众保留限制，发布学院不是主办方或资格证明 |
| registration_deadline / internal_deadline | 主办方最终截止与校内报名/推荐截止，不能互相替代 |
| registration_start_date / event_start_date / event_end_date / internal_event_date | 未来报名开始、活动起止、校内选拔举办日期；都不能冒充最终报名截止 |
| registration_url | 明确命名的报名链接；多个入口留空并保存证据，不跟踪访问 |
| requirements_summary | 当前保留明确受众限制摘要，非完整资格解析 |
| evidence | 类型化条目：field、kind、quote、reason、可选 value；kind=fact/inference/unknown，引用来自脱敏输入，不当作人工真值 |
| needs_manual_review | 内容需要进一步核实；False 也不表示批准发布 |
| review_status / review_reason / reviewed_at | pending / approved / rejected；仅独立人工 review 操作产生审批历史 |
| model_name / analyzed_at | 当前 model_name=null，分析时间为带时区 ISO 8601；未来模型名称来自适配器可信配置 |

日期值为带年份 `YYYY-MM-DD`，明示时分时为 `YYYY-MM-DDTHH:MM:SS+08:00`。计算时区固定 Asia/Shanghai。日期只有日精度时，截止按当天结束计算；`--as-of YYYY-MM-DD` 明确采用该日北京时间 00:00。没有年份、暂定/预计、无效日期或多个赛道截止不强行确定单值，原文候选值保留在 evidence 中。校内截止已过但主办方仍开放时，参与途径可能受限，状态保留 unknown。

## 可配置规则与已知局限

`rules.json` 提供参与、类型、AI 技术、跨学科、结果新闻、行政、资格和使用政策表达。自定义 `--rules 路径` 会校验规则结构并生成不同版本。

规则优先高召回：没有 AI 词的数字媒体、设计、创新创业等保留；不能仅按栏目或旧发布日期排除。结果报道与禁用政策中的 AI 字样不构成“现在有直接 AI 机会”。存在歧义时不自动肯定资格或规则许可。

局限：不理解完整赛道语义、复杂否定和引用上下文；结果新闻夹带未来招募可能误排，历史说明夹在新通知里也可能影响匹配。附件未解析，报名日期范围和无年份日期可能留空，资格摘要不是精确用户画像。本文保守规则不是人工标签，更不是生产效果承诺。

## 保存、缓存与审核

新增 `notice_analyses`、`manual_labels`、`analysis_reviews`，分析命令按需迁移到 user_version=2。原 notices、notice_sources、notice_links 的采集信息保持不变。修复了 M1 打开数据库时无条件重设 version=1 的行为，重新打开 v2 不会降级。

成功分析按 notice_id + input_hash + analyzer_version 缓存，模型名也须匹配；失败允许再次尝试。内容未变时只重算时间状态，不重新匹配全文；这避免同一缓存跨过截止时点后仍显示 open。正文、标题、附件或来源变化会重新分析；旧分析记录保留。

人工标签和审核按完整输入版本绑定，都是追加历史。更换规则/分析器不能覆盖同一输入的人工批准或拒绝。原文变化后，新版本重新 pending，旧版审批仍保留；不是把人类决定删除或改成模型决定。值得推荐的人工标签也不等于发布批准。

开发 RSS 始终由原始通知导出，GUID 仍为原文 URL；不会因为新增分析表、人工标签或 review=approved 自动发布正式 Feed。正式发布须在后续另建明确的审批及机会状态契约，本轮没有该入口。

## 后续 LLM 适配边界

`Analyzer` 协议仅接受 AnalysisInput，返回经过验证的 NoticeAnalysis。输入含标题、正文、发现栏目 sources、实际发布单位 content_source、发布日期、URL 及附件名称/链接；不把转载学院混作发布单位。不授予模型文件、消息、浏览器、网络或其他工具。所有网页文字，包括“系统提示”“批准指令”，均是不可信数据，不能控制角色、模型配置或审批。

当前脱敏移除常见联系方式、证件号、名单推荐段、凭据样式和无关 HTML；不保证识别所有人名。下一轮真实外发前仍须人工检查脱敏边界并控制输入长度。不能因为有 sanitizer 就宣称可把原始个人信息发送给模型。

未来适配器应把模型、提示和配置纳入稳定版本标识，使用固定结构化输出；本轮只提供协议、类型校验及失败/权限回归测试，没有 OpenAI SDK、API 密钥读取或真实 LLM 请求。自动输出（包括缓存）不得 approved/rejected，事实必须引用所供输入；日期须有对应用途标签、原文日期和值一致的证据，不能把元数据中的发布日期换成报名截止。不合法输出或异常只保存错误类型，不保存原始异常文本或完整响应中的敏感信息。已有人工审批独立保留。
