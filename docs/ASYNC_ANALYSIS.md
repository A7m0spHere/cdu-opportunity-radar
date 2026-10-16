# M7 私有异步分析契约（Issue #10）

异步分析是独立 CLI worker：发现新输入 → 规则预筛 → 认领/预留预算 → 有界模型请求 → JSON/证据/事实校验 → 私有建议。它不参与 HTTP 或 RSS 热路径，不修改原文、GUID、首次聚合时间、人工标签、审批历史和质量门禁。没有模型也能展示全部合格信息。

参考 [AIHOT](https://github.com/KKKKhazix/AIHOT) 的分阶段预筛、结构化判断及后续校准思路；没有引入其框架、双重评分、聚簇或自动精选门槛。重要性效果与精选由 #11–#13 分别验收。

## 文件和调用方式

原始库始终使用 SQLite `mode=ro`，不执行迁移。任务、结果和用量只写独立私有 SQLite，不能与原始库、其 `-wal/-shm/-journal` 冲突，拒绝已有其他应用表和更换原始库身份。多个 worker 必须共用一个 state-db；不同 state-db 不共享去重或预算。文件位于忽略的 `data/` 或 `artifacts/`，没有公开的状态/结果端点。

```powershell
# 本轮可直接复现：不读取 Key、不联网，明确的合成模型模式
.\.venv\Scripts\python.exe -m cdu_radar analysis-queue scan --db data/student.sqlite3 --state-db data/m7-mock/jobs.sqlite3 --mock
.\.venv\Scripts\python.exe -m cdu_radar analysis-queue work --db data/student.sqlite3 --state-db data/m7-mock/jobs.sqlite3 --mock --as-of 2026-10-16
.\.venv\Scripts\python.exe -m cdu_radar analysis-queue status --db data/student.sqlite3 --state-db data/m7-mock/jobs.sqlite3 --mock --include-results
.\.venv\Scripts\python.exe -m cdu_radar analysis-queue ledger --db data/student.sqlite3 --state-db data/m7-mock/jobs.sqlite3

# NEW 目录：合成原文、任务、费用模拟及待人工核验 CSV；已有目录会拒绝
.\.venv\Scripts\python.exe scripts/m7_offline_acceptance.py --directory artifacts/m7-acceptance-new
```

`work` 必须明确选择 `--mock` 或 `--send`，两者互斥，缺省不调用模型。`scan/status/ledger` 永远不调用模型；`status/ledger` 连任务库也只读，不会创建缺失文件或恢复租约。`status --include-results` 是本地显式查看脱敏建议，仍包含必要的短引用，不能当成公共 API。

`--watch --interval 60` 每轮先有界扫描，再处理最多 batch_limit 个任务，等待后重复，Ctrl+C 结束。没有自动启动后台服务。每轮扫描按 ID 游标滚动，旧 ID 的更新在下一次轮转发现；不是采集事务里的即时调用。输入与结果最终提交前再次核对；旧结果在查询时也会按当前输入标 stale。

## 配置与未来的付费调用

本轮不授权真实调用。未来在用户另行授权、选择实际服务并准备凭证后，才使用独立 state-db 和 `work --config <file> --send`；不能借用历史联调授权。示例为非秘密设置，示例域名不能直接运行：

```json
{
  "llm": {
    "base_url": "https://provider.example.invalid/v1",
    "model": "configured-model-id",
    "key_env": "CDU_LLM_API_KEY",
    "retries": 0,
    "timeout": 60,
    "max_input_chars": 20000,
    "max_output_tokens": 4096,
    "response_format": "json_schema"
  },
  "queue": {
    "batch_limit": 3,
    "scan_limit": 100,
    "max_attempts": 3,
    "backoff_seconds": 30,
    "lease_seconds": 180,
    "request_limit": 20,
    "token_limit": 200000,
    "window_seconds": 86400
  },
  "prescreen": {"types": [], "topics": []}
}
```

Key 仅由 `key_env` 指定的环境变量读取，配置不接受 `api_key`。固定 Prompt，无自定义 Prompt 文件或原始失败响应捕获。兼容服务可选 `json_object`，但客户端仍校验相同严格 Schema；不提供工具、浏览、执行、文件或消息能力。传输只发送到配置服务、验证 TLS、不跟随重定向；不从输入提取服务地址。

`prescreen.types/topics` 使用 M6 稳定枚举，两轴相交，同轴多个值为任选；空数组表示不过滤。默认不按重要性、AI 或学院剔除；预筛只节省模型请求，普通原文始终保留。空/超长输入和疑似注入文字进入 skipped，不改变原文发布资格。附件不下载，名称不能证明附件规则。有限模式检测不能保证识别所有注入，因此仍依靠无工具权限、严格 Schema、引用与事实复核。

## 幂等、版本与状态

唯一键是 `(notice_id, input_hash, pipeline_version)`；input_hash 包含原文哈希、标题、日期、来源及附件/正文链接元数据，不含 last_seen_at。重复发现和访问不重新调用。pipeline_version 纳入配置模型/服务/输出参数、固定 Prompt/Schema、预筛、M6 taxonomy 和确定性校验实现；超时/预算/单价不伪装成新的分析内容版本。

`model/prompt_version/schema_version/rule_version/content_hash/input_hash` 单独存储。model 是配置的模型 ID，不冒充提供商未披露的真实权重修订。新版本另建任务，旧实验缓存、训练标签和审批不读入新结果。原文变化先使旧任务 stale，运行中的迟到结果不能写成当前结果。完全相同的输入回退时，只能重验相同版本的私有建议后复用，不复用审批。

| 状态 | 含义 |
| --- | --- |
| not_enqueued_count / IDs | 当前输入/当前 pipeline 尚无任务；ID 列表最多 100 条，数量不截断 |
| queued | 已入队，未调用 |
| running | 已原子认领、预留预算，有租约令牌 |
| retry_wait | 瞬时错误，等待指数退避 |
| budget_blocked | 请求/Token 额度暂不足，或用量超过本地预留，未新增请求 |
| succeeded | JSON 与校验流程完成，可以包含明确 unknown，不代表真实正确或适合精选 |
| partial | 保留独立可信字段，其他建议/证据不支持或附件正文不可用，待核验 |
| failed | 永久错误或重试耗尽，未知不会写成否定原文 |
| skipped | 预筛或输入边界阻止调用，原文保留 |
| stale | 原文输入不再匹配；旧结果不作当前建议输出 |
| outdated | 当前输入相同但 pipeline 版本旧，仅本地历史保留 |
| lease_expired | 只读状态观察到运行租约已过期，下一次 worker 负责恢复 |

`status` 优先按指定配置计算当前 pipeline，否则使用最近 scan 记录的 active_pipeline；多种配置共享队列时应显式指定配置查看。M6 公共 v2 继续只有规则分类，不连接此私有队列；其 model_state 表示该公开投影未使用模型。后续 #12 需要单独的公开结果契约。

## 故障和预算

输入日期/类型等单条契约错误记录在私有 `input_failures`，仅保存通知 ID、版本、安全错误码与处理时间，不保存异常原文或伪造分析哈希；其他记录继续入队。重复扫描不复制失败行，原文修复后标 resolved 并恢复入队。只读查询兼容旧队列，不会为读取追加新表；分析库锁争用立即失败，由调用方展示降级，避免等待 10 秒拖住页面和原有 RSS。写 worker 保留原有有限锁等待。

SQLite IMMEDIATE 短事务覆盖认领与预算预留，不覆盖模型运行。每个任务一次独立 spawn 子进程，包含启动/传输/解析/校验的总超时；到期终止子进程，租约必须比超时大 5 秒以上。HTTP 传输内部 retries 固定为 0，只有队列可重试；连接/超时/429/5xx/worker 丢失按指数退避，最多 max_attempts（上限 5），格式、证据、权限、配置等永久失败不盲目重试。认证错误停止当前批次。

worker 崩溃后过期租约进入 retry_wait 或 failed；旧令牌不能覆盖新租约。SQLite 在本地可保证单次认领，不承诺外部服务的 exactly-once：发送后崩溃可能在恢复时重复请求，两次都必须保留预算与账本；未知用量不退还。

所有版本的潜在付费调用共用该 state-db 的滚动 window_seconds 预算。每次调用之前预留一个请求及 `UTF-8 请求/Schema 字节 + 1024 + max_output_tokens` 的本地 Token 额度，成功后也不提前释放，以保守限制调用量。未知用量继续占额度；可知用量超过预留会阻止该窗口后续调用。不同服务 tokenizer、隐藏提示或记账可能不同，这个 Token 额度是本地保守调用控制，不是提供商账单硬上限；真实上线还须提供商限额。多个 worker 应使用同一预算配置；配置变更不会删除已有预留。

账本区分 request_reservations（可能发生的请求）、dispatch_attempts（已开始传输尝试）、known_response_requests（可知收到状态码）、known_usage_requests（可知有效用量）。超时无法据此确定服务端是否计费。完整有效的 prompt/completion/total tokens 才计已知用量；未披露不是 0。可配置两项单价和币种，费用只是估算，不是账单；用量或价格缺失则费用 null。模拟与 provider pipeline 区分，mock 不占付费预算，模拟估价必须另加 synthetic 标识。

## 建议和确定性复核

模型返回 `classification/summary/importance/nature/facts`，禁止额外键，所有值均有类型与长度/数组边界；逐项证据必须在提供给模型的脱敏标题/正文及当前原文输入中定位。元数据、附件名和系统 Prompt 不能当原文证据。

M6 单主类型、多主题由固定枚举和引用关键词复核，unsupported 变 unknown/空标签。summary 的 fact 仅允许原文短摘录拼接；自由改写/推断只留在 proposal，不作为可信摘要。importance 只能 inference/unknown、advisory_only，保留理由/引用，无精选评分或发布许可。学术/行政属性检查正文词及否定/条件上下文；这些保守检查不替代人工分类质量评测。

facts 复用 screening 字段审计：资格与 AI 政策由既有语义守卫复核；校内截止、主办方截止、报名开始保留用途，发布日期/举办日/附件规则不得填入。无依据或冲突保持 unknown/null；部分支持与不支持分开保留 proposed/trusted。所有结果固定 `publication_permission=false`、`review_required=true`；facts 内历史 recommendation 也不能成为精选许可。日志仅安全错误枚举/短元数据，不输出提供商异常原文、Prompt、原始响应或 Key。

## 验收边界

离线回归验证并发去重/全局预留、原文更新/在途更新/回退重验、版本失效、失败退避/重启恢复/令牌隔离、硬超时、JSON/引用/事实拒绝、预算耗尽、未知费用、Mock 费用和公共输出兼容。`m7_offline_acceptance.py` 生成 6 条合成样例与空人工结论 CSV，费用模拟另存 synthetic JSON；不得导入为真实标签或宣称效果提升。本轮真实付费 API、官方站点请求、部署、真实精选评测均未执行。
