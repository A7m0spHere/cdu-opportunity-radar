# 人工标注与离线评测指南

第一次接触标注，请先看 [从零开始的操作教程](LABELING_BEGINNER.md)。第一轮只需填写四列，本文用于查询完整字段和评测口径。

2026-10-14 首批标注已导入：36 条人工判断，推荐=9、不推荐=27；转换后的文件为 `data/m2a-labels-human-2026-10-14.csv`，评测结果为 `data/m2a-evaluation-human-2026-10-14.json`。原易读版 Excel 未修改，人工标签不是审批；数据和结果只保存在本地。详细结果及口径差异见 VALIDATION.md。

导入前复验：`data/m2a.sqlite3` 有 36 篇唯一真实通知、0 条人工标签、0 条合成标签。当时另行导出的 `data/m2a-labels-2026-10-14.csv` 为 36 行未标注清单，参考点为北京时间 2026-10-14 00:00，身份/输入版本与原库一致。这份空白表及旧 `data/m2a-labels.csv` 保留作原始快照，不会因导入用户判断而自动改写。

文件为 UTF-8 BOM，可用 Excel、LibreOffice 或文本编辑器编辑。完整原文和附件内容不进入工作表，摘要最多 800 字并做基础脱敏；核对时按 canonical_url 查看官网原文。这是采集快照，不保证原网页当前内容未更新；差异请写入 notes。文件和原始库保留在被 Git 忽略的 data/。

## 实际操作

在项目根目录执行 PowerShell。新表已生成，直接从填写开始；以下 export 命令用于复现，文件存在会拒绝覆盖，需要重新导出时换一个文件名。

```powershell
cd 'D:\AI项目\cdu信息聚合插件'
.\.venv\Scripts\python.exe -m cdu_radar label export --db data/m2a.sqlite3 --output data/m2a-labels-2026-10-14.csv --as-of 2026-10-14
```

用 Excel 编辑时，建议通过“数据 → 从文本/CSV”导入，编码选择 UTF-8、分隔符选择逗号，并将所有列类型设为文本，避免哈希或日期自动转换。编辑后保存为 CSV UTF-8（逗号分隔），保留全部列名和顺序，不把 .xlsx 直接改名为 .csv。日期必须保持下表规定格式。

每行先读原文，再独立判断人工字段；改 label_status=human。已确定推荐价值时填写 worth_recommending=true/false，并写判断依据；暂时无法判断时该值留空，needs_further_check=true，notes 写待核实问题。不用 predicted_* 填充真值，未处理行保持 unlabeled。

完成后导入，命令只读取工作表并追加标签历史：

```powershell
.\.venv\Scripts\python.exe -m cdu_radar label import --db data/m2a.sqlite3 --input data/m2a-labels-2026-10-14.csv --labeler 本人
.\.venv\Scripts\python.exe -m cdu_radar evaluate --db data/m2a.sqlite3 --output data/m2a-evaluation-manual.json
```

导入输出 imported（本次新增）、unchanged（相同标签未重复保存）、unlabeled_skipped（跳过未标注行）。当前空表导入结果为 imported=0、unchanged=0、unlabeled_skipped=36；这不是已完成人工标注。默认 evaluate 按每条标签自己的参考时点评测，适合分批填写。若全部标签都保持本次固定参考点，也可加 `--as-of 2026-10-14`；不能把昨天的标签直接拿来评估今天的报名状态。

## 推荐标准

值得推荐应同时考虑：是学生能够参与的比赛/活动，契合 AI 学习、创作或跨学科实践，资格及参与途径合理，当前仍存在机会。未知规则需要进一步核实，不等同于不值得推荐，也不等同于允许使用 AI。比赛已举行的日期、发布日期和报名截止必须分开核对。

程序预测只在 `predicted_*` 列，**不能复制或批量转换为人工真值**。本轮没有代替用户填写真实标签。为了方便对照而显示预测，不代表它是正确答案，建议先阅读原文再判断。

## 如何填写

无需全部字段一次填完。最小标注只需将 `label_status` 改为 `human`，填写 `worth_recommending=true/false`，并写简短依据；不确定可留空并填写 needs_further_check=true 和备注，这类行可导入但不参与推荐二分类指标。

| 可编辑列 | 合法值与含义 |
|---|---|
| label_status | unlabeled：尚未标注；human：明确由本人判断；prediction 不允许导入为真值；synthetic 仅用于显式测试模式 |
| worth_recommending | true / false / 空白；针对 reference_date 当天普通本科生的推荐价值 |
| student_opportunity | true / false / 空白；活动类型与学生资格，区别于新闻/行政；当前是否开放另看 opportunity_status |
| relevance_level | direct_ai / ai_applicable / unrelated / uncertain，语义见 ANALYSIS_CONTRACT.md |
| ai_use_policy | explicitly_allowed / partially_allowed / explicitly_prohibited / unspecified |
| eligibility_status | eligible / ineligible / uncertain；学院/专业/年级限制尚未确认一般学生满足时使用 uncertain，写清限制 |
| deadline_explicit | true / false / 空白；原文或人工核实的附件是否有明确最终截止，多个赛道都有日期也可为 true |
| registration_deadline / internal_deadline | 空白或 YYYY-MM-DD / YYYY-MM-DDTHH:MM:SS+08:00，年份须为 20xx；分别为主办方最终截止/校内推荐截止。明确唯一时间才填写；多个赛道不同截止留空并注明候选日期，不能填发布日期或比赛日期 |
| opportunity_status | open / upcoming / closed / unknown |
| category | competition / hackathon / workshop / lecture / project_recruitment / other_activity / news / administrative / unknown |
| needs_further_check | true / false / 空白 |
| notes | 你的判断依据及待核实问题；导入历史保留 |
| reference_date | 必填 YYYY-MM-DD（20xx 年），人工判断所依据的北京时间日期；本表为 2026-10-14，不是实际编辑完成日期 |
| reference_time | 带时区 ISO 时间，建议保留导出值 2026-10-14T00:00:00+08:00；可留空表示日精度。若主动改判断时点，必须与 reference_date 在北京时间对应同一天 |

布尔值支持 true/false 和 Excel 输出的 TRUE/FALSE；除 label_status 与 reference_date 外，人工字段可留空。枚举值使用表内英文原值，不填中文别名。reference_time 留空时，评测以 reference_date 的北京时间 00:00 为参考点，不代表全天都具备同样状态。

以下自动生成列只读，不要修改。notice_id、content_hash、input_hash、canonical_url 会在导入时核验；其他上下文列用于人工阅读，不是人工判断值。不要删除、重命名或调换 CSV 列。

| 列 | 内容/值 |
|---|---|
| notice_id | 数据库正整数 ID |
| content_hash / input_hash | 64 位十六进制正文/完整输入版本指纹 |
| canonical_url | 官方原文 URL |
| title / sources / content_source | 标题、发现栏目、实际发布单位 |
| published_at | 网页发布日期 YYYY-MM-DD，缺失为空；不是报名截止 |
| content_excerpt | 最多 800 字脱敏摘要 |
| attachment_count | 附件元数据数量，非已解析附件数量 |
| predicted_reference_time | 程序预测参考时间，ISO 带时区 |
| predicted_analyzer_version | 规则实现与配置指纹 |
| predicted_decision | retain / uncertain / exclude |
| predicted_category | category 列允许的类别值 |
| predicted_relevance | relevance_level 列允许的相关性值 |
| predicted_eligibility | eligible / ineligible / uncertain |
| predicted_ai_policy | ai_use_policy 列允许的政策值 |
| predicted_status | open / upcoming / closed / unknown |

导入先验证全部行，再以事务提交；错误行、重复 ID、旧版本或预测冒充真值都拒绝，不能部分导入。重复导入相同标注不新增记录，修正后的人工值追加历史。未标注行跳过，带手写值却仍写 unlabeled 的行会报错，避免来源混淆。

重新 export 默认拒绝覆盖已有文件，避免丢失未导入编辑。先 import 已完成的标签，再用另一个 output 导出，或确认后显式 `--overwrite`；只有已入库标签能够恢复到新的工作表。

## 评测口径

默认只使用当前输入版本、参考日期/时点一致的明确人工 worth_recommending 标签。真值为空或版本已过时的记录不参与指标；精确时点不一致会单独计数并排除，避免当天截止前后的状态差异成为伪误判。旧标签没有精确时点时保留日精度并明确报告数量。合成标签只有 `--include-synthetic` 才参与，结果显式标记 synthetic_included，不能当真实效果。

指定 `--as-of` 时只评估同一参考日期/时点的标签；不指定时按各条人工标签自己的参考时点评测，报告 per_label_time，避免导出和评测相隔几秒就把整批标签排除。后一种是历史样本评测，不意味着目前仍可报名。

主指标评估**候选保留**：retain + uncertain 都是预测正例，exclude 为负例。这符合高召回预筛目标，不是发布批准率。Precision=TP/(TP+FP)，Recall=TP/(TP+FN)，F1=2TP/(2TP+FP+FN)；分母为零时值为空，不制造 0 或 1。未确定比例是在有效标注样本中预测 uncertain 的比例。

输出包含混淆矩阵、类别误判计数、资格/AI 政策/相关性/截止时间等比较次数和错误样本 ID/原文 URL。多赛道日期以证据判断“存在明确截止”，不会只因单值字段为 null 就判定未提及日期。没有标签时报告样本不足、空指标及空错误列表；不等于没有误判。

查看结果和具体误报/漏报：

```powershell
$report = Get-Content -Raw -Encoding UTF8 data/m2a-evaluation-manual.json | ConvertFrom-Json
$report | Select-Object valid_labels,human_labels,precision,recall,f1,uncertain_ratio,warning
$report.confusion_matrix
$report.error_samples | Where-Object { $_.error_types -contains 'false_positive' } | Format-List
$report.error_samples | Where-Object { $_.error_types -contains 'missed_opportunity' } | Format-List
$report.dimension_errors
$report.dimension_compared
```

false_positive：规则保留（retain/uncertain），人工认为不值得推荐；missed_opportunity：规则排除，人工认为值得推荐。error_samples 还包含类型、资格、政策和时间等维度错误。当前实现只对 worth_recommending 非空且版本/时点有效的标签运行这些比较；只填其他字段的行不会独立计入维度指标。发现 valid_labels=0 时先检查是否已明确 human、是否填写推荐判断、是否有版本或参考时点不匹配。

开发数量提示阈值为 30 条，不是生产标准。即使 36 条全部标注也缺少代表性；后续逐步补充 100—200 个真实、人工判断的跨专业样本，再分析漏报、误报和各类别差异。

测试中的文本和模拟人工操作全部是合成场景，只用于验证类型、导入事务、权限和数学指标，不算真实样本或人工效果证据。

## 审核边界

标签用于评测，review 用于保留审核决定，彼此不自动转换。

```powershell
.\.venv\Scripts\python.exe -m cdu_radar review list --as-of 2026-10-13
# 仅当你实际完成核验时，选择对应 ID 和状态：
.\.venv\Scripts\python.exe -m cdu_radar review set --notice-id 19 --status approved --reason 已核对原文资格及规则 --reviewer 本人
```

该命令只保存本地人工审核历史，不会推送、不改原始 Feed。规则重新运行不能取消拒绝或替你批准；原文变动后的新版本要重新审查。
