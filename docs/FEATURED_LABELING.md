# 重要精选人工基准与标注指南 v1

口径 `campus-objective-importance-v1`，与 M2 的“适合本科生参与/AI 机会”不同。旧 36 条人工标签原样保留，不复制为新真值。模型、规则和合成标签都不能自动转成人工标签。

## 固定样本

```powershell
# 在既有库的固定参考时点，只读质量合格原文；输出到 NEW 私有目录
.\.venv\Scripts\python.exe -m cdu_radar featured-benchmark export --db artifacts/m5b-live-v1/notices.sqlite3 --directory artifacts/my-featured-labels --limit 24 --as-of 2026-10-16T19:00:00+08:00
```

`dataset.json` 冻结输入哈希、脱敏原文、来源、附件元数据、分类、参考时点、规则/阈值、抽样种子和划分，并封存 dataset_id；改动后校验失败。按发布单位与权益/学业/其他信息类型分层，固定哈希排序后轮流抽样，小来源也有抽取机会。相同最终 URL 的跨院转载仅一条，同一 URL 不跨划分；没有做语义事件聚类，近似重复仍需人工检查。

约每四条固定抽一条 holdout，其余 development；默认只评 holdout。记录每层可用总体、样本、抽取比例和总体/样本权重，未覆盖层显式为 0/null。权重只描述这个本地库，不能推算全校总体；未做学校总体加权估计。当前池不足时不编造科研、时效或其他学院样本，需后续按有界采集补齐。

当前实际准备的 `artifacts/m8-human-final-v1/labels.csv` 为既有 23 条原始中的 21 条质量合格样本、10 个发布单位；2 条不可信来源单独排除。日期为固定历史回放，不代表重新实采。它是小规模待标注清单，不是全校代表性基准。

## 填写 labels.csv

身份、哈希、来源、标题、摘要、划分、权重和时点列不要修改；表里不预填模型预测或旧人工结论。可用原文链接、dataset.json 脱敏全文核实；附件只有名称/链接，未读附件正文不能臆测其规则。表格中公式开头文本已加保护前缀，导入仍核对冻结值。

| 人工列 | 填写要求 |
| --- | --- |
| objective_featured | true / false / unknown：按校园客观重要性，不能按个人兴趣单独决定 |
| importance | high / normal / low / unknown：人工重要性分档，不是模型 confidence |
| criterion_codes | 规则维度 ID，多个用 `\|`；正例至少一项，完整枚举见规则文档 |
| evidence_quote | 正例必须引用冻结标题/正文中的原句；不能改写、只引系统 Prompt 或附件名 |
| personal_relevance_note | 单独说明某学院/专业/主题的个人相关性，不进入客观二分类真值 |
| notes | 必填人工理由；unknown 说明缺什么事实，不当作 false |
| label_origin | 真实人工重新确认填 human；合成软件样例只能 synthetic |
| confirmed_guideline | 确实按本指南重新判断后填 true |

判断时特别检查：单学院重大权益通知不能因受众小漏掉；常规报销/例行行政流程不因“官方”就入选；跨院转载不增加重要性；重大历史新闻可重要但不再是当前机会；无截止日不代表已截止或开放；模型失败不代表不重要。

```powershell
# 只有完成新口径人工核验后才执行；标签写独立数据库，不写原始/M2 数据库
.\.venv\Scripts\python.exe -m cdu_radar featured-benchmark import --dataset artifacts/my-featured-labels/dataset.json --input artifacts/my-featured-labels/labels.csv --labels-db data/featured-human.sqlite3 --labeler YOUR_REVIEWER_NAME
```

空行保持未标注。整批先校验，任何身份、引用、枚举或确认错误均不部分导入；相同人工记录重复导入不增加行，修订和新审核人追加历史。原有原文、审批、M2 标签不变。`--allow-synthetic` 仅用于软件样例，默认评测排除 synthetic；程序不验证真人身份，因此应由实际审核者明确确认，不能把机器生成的表填为 human。

## 离线报告

```powershell
# 先看未标注报告：没有模型/人工标签时仍输出覆盖缺口，指标为 null
.\.venv\Scripts\python.exe -m cdu_radar featured-benchmark evaluate --dataset artifacts/my-featured-labels/dataset.json --db artifacts/m5b-live-v1/notices.sqlite3 --split all --output artifacts/my-featured-labels/unlabeled-report.json

# 有新人工真值和既有 M7 缓存后，只读比较，绝不发送模型请求
.\.venv\Scripts\python.exe -m cdu_radar featured-benchmark evaluate --dataset artifacts/my-featured-labels/dataset.json --db artifacts/m5b-live-v1/notices.sqlite3 --labels-db data/featured-human.sqlite3 --analysis-state data/m7/jobs.sqlite3 --analysis-config data/m7-config.json --split holdout --output artifacts/my-featured-labels/holdout-v1.json
```

报告记录 dataset/policy/model/Prompt/Schema/pipeline 版本、固定时点与划分，生成 evaluation_id；新文件写入，拒绝覆盖旧报告。原文更新后的标签/模型结果显示 stale；质量撤回单列 source_unqualified，不归咎为模型负例。规则实现变化需要新 benchmark 修订，不能悄悄修改冻结阈值。标签可依据同一输入继续保留，但不得用 holdout 做开发反馈。

同时看三层，而非只报准确率：

- `labeled_subset_metrics`：有明确新真值的样本中，实际 selected 为正，pending 正例也算展示漏报 FN；未知是弃权，不是模型断言负例。附 TP/FP/FN/TN、Precision/Recall/F1、典型漏报/误报。
- `decisive_subset_metrics`：只比较有明确入选/不入选决定的子集，必须结合覆盖率看，不能当全量效果。
- `full_benchmark_metrics`：只有所选划分全部样本具有当前有效明确真值时才给出，否则 null。无标签时漏报数量也为 null，空示例列表不代表没有漏报。

另外列出按发布单位/信息类型/抽样层的数量、标签覆盖、当前可用分析覆盖、未分析、失败、依据不足比例、质量排除和漏报 ID。未标注、人为 unknown 与 stale 分开统计。不同来源样本很少时只作错例检查，不作全校公平性/准确率结论。

可复现合成软件报告：`python scripts/m8_offline_acceptance.py --directory artifacts/my-m8-synthetic`。7 条合成边界产生 TP=3/FP=0/FN=2/TN=2，失败/未分析正例会进入 FN；这些数字用于验证计算和弃权统计，**不是真实人工评测或模型效果**。默认不带 `--include-synthetic` 时仍是 0 有效标签。
