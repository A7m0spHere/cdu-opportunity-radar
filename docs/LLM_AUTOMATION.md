# LLM 自动分类与综合重要信息筛选

2026-10-22 用户确认：沿用现有模型，本轮不按试采请求/Token 额度截断，优先验证质量；精选覆盖**综合重要校园信息**，包括选课、考试、奖助、服务变化及参与机会。人工标注只用于评测与校准，不是逐条发布审批。

## 自动流程

原文入库 → 私有异步队列发现新增/变更 → LLM 判断类型、领域、主题、重要精选、本科生阅读价值及办理事项 → 程序校验结构、引用、隐私与受众冲突 → 网页/RSS 采用有效结果。

| 维度 | 模型判断 | 程序约束 |
| --- | --- | --- |
| 分类 | 按文章主旨做语义分类，允许纠正规则初分 | 枚举、引用位置及长度；失败回退规则 |
| 综合精选 | selected/not_selected/uncertain、理由和引用 | 引用核验、分档和未知状态；引用存在不代表语义必然正确 |
| 本科生阅读范围 | 学习生活、参与及有参考价值的信息 | 教师/研究生专属冲突仍排除；无有效判断回退规则 |
| 要点 | 从正文选择有用片段 | 直接使用原文，最多 420 字，不采用模型自由改写 |
| 行动依据 | 是否描述报名、办理、提交、参与或异议等事项 | 与当前开放状态及用户资格分开，不修改既有事实 |

原文来源、撤回、解析和 72h 新鲜度门禁保持独立。LLM 不授予发布权限，不覆盖原文或人工标签。公开 HTTP/RSS 读取不调用模型、不加载密钥。

## 配置与运行

自动化配置为 `data/llm-review/config.json`，另留 `config.automatic.json` 副本；旧配置保存为 `config.pre-automatic-20261022.json`。仍引用已有 DPAPI 凭证，不写明文密钥。

```powershell
.\.venv\Scripts\python.exe -m cdu_radar serve-rss --db artifacts/m10c-live-20261022/live/notices.sqlite3 --state-dir data/automatic-live-http --analysis-state data/llm-review/live-20261022.sqlite3 --analysis-config data/llm-review/config.json --analysis-worker --collect-interval 0
```

`--analysis-worker` 启用协调器，自动处理新增/变更的原文；已有缓存按输入、模型、Prompt、Schema 与校验版本复用。`--collect-interval 0` 不增加官网采集轮次。只读展示时去掉 `--analysis-worker`。停止服务会停止协调器和模型子进程，未启动服务时没有持续后台任务。

历史冻结库继续使用原有 `data/llm-review/jobs.sqlite3`；最新三轮库使用自己的 `live-20261022.sqlite3`。不同原文库的身份与队列不互换，不通过新建队列绕开既有用量。旧任务、失败和未知用量保留。

一次性处理指定集合可用 `scripts/run_automatic_review.py --db ... --state-db ... --config ... --directory NEW_DIR --all-notices --send`；也可重复指定 `--notice-id`。历史回放传原 `--as-of`，不冒充当前官网核验。全量模式只选择参考时点的质量合格原文。

按本次授权提高了私有配置的请求/Token 窗口设置，仍保留单篇超时、有限瞬时错误重试、认证错误停止和连续格式错误停止。所有真实请求进入对应库的账本；缺少单价或失败用量时保持未知，不记为 0。

## 兼容与采用

实测兼容网关在收到 system 指令和 Schema 时仍可能返回自由摘要；把同一任务契约同时放入 user 消息后得到结构化结果。原文继续放在 `untrusted_notice` JSON 数据区，严格解析和引用校验保留，不从自由回答中猜测分类。

分类、重要性与各筛选字段分别校验，失效字段保留 issues 和回退，其他有效字段可以采用。整体格式不成立仍失败。有限证据投影优先保留摘要依据，允许原文已有的换行，不添加事实。

配置模型名为 `deepseek-v4.1-flash`，返回元数据可能为 `deepseek-v4-flash`；这是兼容服务声明，不证明底层身份。

## 已填标注与评测

10 篇真实标注保存在独立 `data/quality-human.sqlite3`；原始 JSON、CSV 和对照报告在 `artifacts/m10-human-20261022/`。没有将模型或合成测试写成 human。

旧标注包仍可转换、导入人工答案；旧预测不冒充当前结果。`refresh_quality_review.py` 新建预测包，仅在原文、哈希、参考时点、采样记录和 development/holdout 划分逐项相同时转接原答案，不改变人工值。当前只对 development 做效果对照。

```powershell
.\.venv\Scripts\python.exe scripts/refresh_quality_review.py --from-directory artifacts/m10-quality-20261021 --directory artifacts/new-evaluation --db artifacts/collection-20261018-162632/notices.sqlite3 --analysis-state data/llm-review/jobs.sqlite3 --analysis-config data/llm-review/config.json --labels-csv artifacts/m10-human-20261022/human-review.csv --labels-db data/quality-human.sqlite3 --labeler user-20261022
```

原答案存在口径分歧：部分获奖回顾被标作科研/通知，退选课及学籍核对被标为没有办理事项且不入精选。用户随后确认综合重要信息范围，因此保留原答案并列出待复核案例，不为拟合这 10 篇而排除重要教务信息。小样本对照不能泛化为全校准确率；#22 的完整人工效果仍待独立验收。
