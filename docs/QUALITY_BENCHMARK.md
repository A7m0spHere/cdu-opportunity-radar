# 多维人工标注与离线评测（Issue #22）

本期按用户决定先生成待标注包。真实精选 Precision/Recall、本科生视图误收/漏收及真实错例修复待人工填写后验收；空表不能证明效果良好。

**标注者优先使用[简单标注页](SIMPLE_LABELING.md)**：每次看一篇，先做 10 篇，点选答案和原文依据后导出 JSON。下面的 CSV 和命令供回收、导入与评测时使用，不需要标注者直接填写技术列。

`quality-benchmark` 复用 [重要精选人工契约](FEATURED_LABELING.md) 的冻结输入、独立真值、证据引用和 development/holdout 划分，增加分类、本科生实用范围及报名/办理依据。旧 M2 标签不导入，不创建发布审批，不阻断采集或 RSS。

## 导出固定样本

```powershell
.\.venv\Scripts\python.exe -m cdu_radar quality-benchmark export --db artifacts/collection-20261018-162632/notices.sqlite3 --directory artifacts/my-quality-review --as-of 2026-10-18T18:00:00+08:00 --limit 50 --analysis-state data/llm-review/jobs.sqlite3 --analysis-config data/llm-review/config.json
```

缓存路径与配置须成对提供；省略后仍能评测规则和本科生视图。只读解析既有模型缓存，并按当前引用/版本合同重验，不加载 Key、不发送请求、不更新任务库。参考时点匹配旧通知库的核验窗口，不代表今天重新验证了官方网站。

按发布单位与内容类型分层，固定种子轮转抽样，最多 50 篇，约四分之一为 holdout，同一最终 URL 不跨划分。`sampling.json` 明列类型、来源数、时间状态、未覆盖状态和样本不足。权重只描述本地库；无当前开放样本就记录缺口。

| 文件 | 用途 |
| --- | --- |
| `review-labels.csv` | 本轮需要填写的综合表，人工列全部为空 |
| `原文核对.md` | 脱敏冻结原文、官方链接与样本 ID，不展示模型结论 |
| `dataset.json` | 来源、全文、附件链接、哈希、划分 |
| `predictions.json` | 规则、缓存模型与最终预测的封存；标注时避免参考 |
| `sampling.json` | 分母、样本构成及覆盖缺口 |
| `labels.csv` | 兼容旧工具的仅精选表，本轮无需重复填写 |

这些文件放在被 Git 忽略的私有目录。现有脱敏器不是完整的人名识别器，不添加额外个人资料；附件正文未读取，不能从文件名猜测资格。

## 填写综合表

不要修改身份、哈希、原文、时点或划分列。原有 `type` 是冻结抽样字段，不是人工答案；人工答案填 `expected_type`。可分批标注不同维度，未判断的人工列继续为空。

| 人工列 | 填写方式 |
| --- | --- |
| `objective_featured` / `importance` | `true/false/unknown`；重要性分档 `high/normal/low/unknown`，沿用精选指南 |
| `criterion_codes` / `evidence_quote` | 规则 ID，多项用竖线分隔；明确正负均须原文证据；正例须规则 ID |
| `expected_type` | `notice/news/event/competition/research`，不确定填 `unknown` |
| `expected_domains` | `academic_affairs/student_aid/competition/research/career/campus_service/student_affairs/culture_sports`，多项用竖线分隔；无标签填 `none`，不确定填 `unknown` |
| `expected_topics` | `ai/computing/pharmacy/design/innovation/sports/humanities`，多项用竖线分隔；无标签填 `none`，不确定填 `unknown` |
| `classification_quote` | 支持分类判断的原句；可换行分隔多个原句，每句须可定位 |
| `undergraduate_relevant` / `undergraduate_quote` | 是否属于本科生实用通知或参与机会：`true/false/unknown`；明确正负须原文依据 |
| `action_basis` / `action_quote` | 是否有实际报名/办理/异议/参与依据：`true/false/unknown`；不等于当前开放或本人符合资格 |
| `personal_relevance_note` | 可选，个人兴趣不改变客观重要性 |
| `notes` | 必填判断理由；unknown 说明缺什么依据 |
| `label_origin` / `confirmed_guideline` | 真实人工逐项复核后填 `human` / `true` |

精选规则 ID 为 `student_rights`、`academic_affairs`、`safety_service`、`participation`、`research`、`major_news`，见 [精选规则](FEATURED_POLICY.md)。证据须逐字来自冻结标题/正文，不填模型解释或改写摘要。CSV 中带保护前缀的单元格原样保留。

## 导入与评测

```powershell
.\.venv\Scripts\python.exe -m cdu_radar quality-benchmark import --directory artifacts/my-quality-review --input artifacts/my-quality-review/review-labels.csv --labels-db data/quality-human.sqlite3 --labeler YOUR_REVIEWER_NAME

.\.venv\Scripts\python.exe -m cdu_radar quality-benchmark evaluate --directory artifacts/my-quality-review --db artifacts/collection-20261018-162632/notices.sqlite3 --labels-db data/quality-human.sqlite3 --split development --output artifacts/my-quality-review/development-v1.json

.\.venv\Scripts\python.exe -m cdu_radar quality-benchmark evaluate --directory artifacts/my-quality-review --db artifacts/collection-20261018-162632/notices.sqlite3 --labels-db data/quality-human.sqlite3 --split holdout --output artifacts/my-quality-review/holdout-v1.json
```

零标签时省略 `--labels-db` 即可完整生成报告，所有效果指标为空。已有输出拒绝覆盖。身份、版本、证据和枚举整批先校验，再写独立 `quality_labels` 数据库；重复导入幂等，修订追加历史；拒绝原始库、旧标签库及 SQLite 侧文件路径。

程序不验证填表者是不是人，Agent 和模型不能替真人填写 `human`。`--allow-synthetic` 仅用于软件测试，默认评测排除这类标签，后续合成标签也不掩盖人类记录。

## 报告解释

分别比较规则重要性基线、缓存模型建议、最终模型加规则精选；分类分别比较规则、有效模型复核与最终标签。规则基线是原文命中加权条件，模型建议不代表发布许可。

失败、未分析、依据不足、stale 均保留计数。最终展示召回将 pending 正例计为漏收；模型缺失时全量模型指标为空，另列明确输出子集和覆盖缺口。分类有未预测项时总错误率为空，同时列明确子集错误率与未解决比例。本科生视图和办理依据单独报告误收、漏收与弃权；办理依据预测是原文指令检索基线，不修改生产报名状态。

development 报告至多输出 5 个有人工真值的具体错例、官方链接、证据及回归方向；holdout 不生成调参建议。无标签时错例为空，不能声称没有误报或漏报。原文输入变化后标记 stale，旧标签不继续计有效。不声称全校准确率或生产验收。
