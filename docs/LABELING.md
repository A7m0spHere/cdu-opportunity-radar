# 人工标注与离线评测指南

现有 36 篇真实通知清单为 `data/m2a-labels.csv`，UTF-8 BOM，可用 Excel、LibreOffice 或文本编辑器打开。完整原文和附件内容不进入工作表，摘要最多 800 字并做基础脱敏；核对时按 canonical_url 查看官网原文。不得把这份本地文件或包含个人信息的原始库提交到仓库。

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
| registration_deadline / internal_deadline | 明确唯一时间才填写；多个赛道不同截止留空、注明候选日期，不能填发布日期或比赛日期 |
| opportunity_status | open / upcoming / closed / unknown |
| category | competition / hackathon / workshop / lecture / project_recruitment / other_activity / news / administrative / unknown |
| needs_further_check | true / false / 空白 |
| notes | 你的判断依据及待核实问题；导入历史保留 |
| reference_date | YYYY-MM-DD，人工判断所依据的北京时间日期；旧日期的标签不会用于新日期的总体推荐评测 |
| reference_time | 判断所依据的带时区具体时点，导出默认填与预测相同的时点；有时分截止的通知尤其应按该时点判断 |

布尔值支持 true/false 和 Excel 输出的 TRUE/FALSE。其他身份字段不要修改：notice_id、content_hash、input_hash、canonical_url 必须匹配当前通知版本。不要删除、重命名或调换 CSV 列。`predicted_reference_time` 表示程序预测时点，和人工 reference_date 分开；`predicted_analyzer_version` 标记配置/实现版本。

```powershell
.\.venv\Scripts\python.exe -m cdu_radar label export --as-of 2026-10-13
# 手工修改 data/m2a-labels.csv 后：
.\.venv\Scripts\python.exe -m cdu_radar label import --input data/m2a-labels.csv --labeler 本人
.\.venv\Scripts\python.exe -m cdu_radar evaluate --as-of 2026-10-13
```

导入先验证全部行，再以事务提交；错误行、重复 ID、旧版本或预测冒充真值都拒绝，不能部分导入。重复导入相同标注不新增记录，修正后的人工值追加历史。未标注行跳过，带手写值却仍写 unlabeled 的行会报错，避免来源混淆。

重新 export 默认拒绝覆盖已有文件，避免丢失未导入编辑。先 import 已完成的标签，再用另一个 output 导出，或确认后显式 `--overwrite`；只有已入库标签能够恢复到新的工作表。

## 评测口径

默认只使用当前输入版本、参考日期/时点一致的明确人工 worth_recommending 标签。真值为空或版本已过时的记录不参与指标；精确时点不一致会单独计数并排除，避免当天截止前后的状态差异成为伪误判。旧标签没有精确时点时保留日精度并明确报告数量。合成标签只有 `--include-synthetic` 才参与，结果显式标记 synthetic_included，不能当真实效果。

指定 `--as-of` 时只评估同一参考日期/时点的标签；不指定时按各条人工标签自己的参考时点评测，报告 per_label_time，避免导出和评测相隔几秒就把整批标签排除。后一种是历史样本评测，不意味着目前仍可报名。

主指标评估**候选保留**：retain + uncertain 都是预测正例，exclude 为负例。这符合高召回预筛目标，不是发布批准率。Precision=TP/(TP+FP)，Recall=TP/(TP+FN)，F1=2TP/(2TP+FP+FN)；分母为零时值为空，不制造 0 或 1。未确定比例是在有效标注样本中预测 uncertain 的比例。

输出包含混淆矩阵、类别误判计数、资格/AI 政策/相关性/截止时间等比较次数和错误样本 ID/原文 URL。多赛道日期以证据判断“存在明确截止”，不会只因单值字段为 null 就判定未提及日期。没有标签时报告样本不足、空指标及空错误列表；不等于没有误判。

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
