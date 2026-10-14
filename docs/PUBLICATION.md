# M2-C 本地人工审核与已批准 RSS

原始通知、筛选预测、评测人工标签、发布审批分开保存。`worth_recommending=true`、规则 retain、LLM 技术成功、旧 `analysis_reviews.approved` 均不进入正式 Feed。只有新的管理员发布审批有效且当前原文版本一致时，才导出批准快照；没有审批则生成标准空 Feed。LLM 失败不妨碍查看原文或人工审核。

## 数据与迁移

写操作按需追加 SQLite user_version=5：

| 表 | 用途 |
|---|---|
| publication_reviews | 追加人工决定、正文哈希/实质版本哈希、审核人/时间/原因、批准标题/摘要/原文 URL/来源、事实草稿、确认指纹、前一审核 ID |
| publication_state | 当前决定、唯一原文 URL 和 GUID、ready/published/withheld/revoked、持久首次发布时间、首次成功导出时间、撤销时间 |
| publication_events | 人工批准/拒绝/暂缓/撤销，系统原文变化/导出，以及更正待处理信息 |

待审 pending 是未作发布决定的派生状态；needs_revision 既可人工暂缓，也可由批准过期/原文更新派生。历史 approved 行永不覆盖成 rejected 或 revoked，新决定追加并关联 supersedes_id。`publication_status=published` 仅表示文件已导出，不表示 AstrBot 或 QQ 已发送。

首次导出先持久保留 GUID/pubDate，再在数据库锁下原子替换文件，成功后记录 first_exported_at。进程或写文件失败时 ready 状态仍待完成，重试沿用保留的 pubDate；已写 XML 但尚未提交数据库的崩溃窗口也不会产生新 GUID/虚假新时间。published_at 是首次发布操作保留的时点，first_exported_at 是可确认的首次成功完成时点，两者不冒充群消息到达时间。

原 notices、人工标签、全部分析/字段审计、旧审核、请求台账和原始 Feed 不重建、不清空。只读 list/show/history/template 和预览不创建表、不分析、不联网。旧命令 `review set` 保留为分析审核入口，新增明确 `--confirm`，仍不能发布 RSS；正式发布只用下面的新动作。

## 建立工作库

本机离线验收使用从 `data/m2b2b.sqlite3` 备份的 `data/m2c.sqlite3`，已按需升级，36 条人工标签、371 条分析和全部旧表行保持一致；36 条通知均未发布批准。原库保持只读，不自动挑选另一个库。新环境使用自己已有的采集库即可，命令显式指定 --db。

如尚无工作副本，可第一次执行下列命令；目标已存在会拒绝，禁止覆盖：

```powershell
.\.venv\Scripts\python.exe -c "import sqlite3; from pathlib import Path; source=sqlite3.connect('file:data/m2b2b.sqlite3?mode=ro',uri=True); Path('data/m2c.sqlite3').open('xb').close(); target=sqlite3.connect('data/m2c.sqlite3'); source.backup(target); target.close(); source.close()"
```

## 查看队列、原文和证据

```powershell
.\.venv\Scripts\python.exe -m cdu_radar review list --db data/m2c.sqlite3
.\.venv\Scripts\python.exe -m cdu_radar review list --all --db data/m2c.sqlite3
.\.venv\Scripts\python.exe -m cdu_radar review show 34 --db data/m2c.sqlite3
.\.venv\Scripts\python.exe -m cdu_radar review history 34 --db data/m2c.sqlite3
.\.venv\Scripts\python.exe -m cdu_radar review template 34 --db data/m2c.sqlite3
```

show 显示脱敏原文、标题、来源、网页发布日期、官方 URL、所有版本规则/LLM 缓存、evidence_validation/field_validation、失败状态、评测标签和旧审核。标明分析及标签是否匹配当前内容/完整输入；旧版本留存供参考，不能自动变成新版依据。history 同时列出新发布操作和独立的旧分析审核。list 不自动计算新预测，模型配置/Key 不存在也可使用。

34 只是查看示例，本轮未批准它，不能据此认为它仍可报名。管理员审核任何 ID 前，核实当前参与入口、年份和日期用途、校内限制、专业/院校/地区/年级/选拔条件、AI 限定用途及附件细则。

## 人工草稿与确认

将 template 输出作为 JSON 草稿手工保存到忽略目录，编辑 `title`、`summary`、`source_url`、`participation_quote`、`valid_until`、`facts`。可参考 [合成格式示例](examples/publication-draft.example.json)，其中 URL 和通知均为合成示例，不能直接批准真实 ID。

facts 字段为 registration_start_date、registration_deadline、internal_deadline、eligibility、ai_use_policy。未知值填 null；已核实字段填 `{ "value": "...", "quote": "原文完整条款" }`：

- 日期必须有明示年份、正确报名/校内用途、真实日历和可定位引用。不能从发布日期或举办日期补全。多个赛道截止有冲突时不能任选一个。
- eligibility 的 value 必须等于完整引用，只发布所引范围，不推出全校学生都符合；选拔及其他细则仍提醒以原文为准。
- AI policy 的 value 只可为 explicitly_allowed、partially_allowed、explicitly_prohibited，完整引用须支持该范围。图片许可不能提升为全部作品许可，不能剥去限定用途或否定词。
- 日期、资格及 AI 许可断言使用受控 facts，不塞进自由标题/简介。界面拒绝常见关键事实断言；自由简介仍需要管理员语义核验，程序不声称能证明任何自然语言描述都正确。
- valid_until 是**管理员批准的维护有效期**，要求带时区时间，不是补造的报名截止。若已核实截止更早，有效期自动取较早的时点。明确报名已结束或原文有效截止已过，不能靠省略 facts 发布为当前机会。

未知字段要求 `--ack-unknown`，导出后仍明确写“未知，需核验；不能据此认定符合或允许”，不会被标为已核实。允许非关键未知，不能省掉参与邀请证据或把未知写成开放/符合/允许。

```powershell
# 先检查 34 的原文，再自行编辑 data/review-34.json；此处仅演示命令，不批准该通知。
.\.venv\Scripts\python.exe -m cdu_radar review approve 34 --db data/m2c.sqlite3 --draft data/review-34.json --reviewer 管理员 --reason '已核实参与入口并保留未知事项' --ack-unknown
# 阅读 preview 中的具体内容和 confirmation_token，原样追加该 token 再执行：
.\.venv\Scripts\python.exe -m cdu_radar review approve 34 --db data/m2c.sqlite3 --draft data/review-34.json --reviewer 管理员 --reason '已核实参与入口并保留未知事项' --ack-unknown --confirm '<confirmation_token>'
```

没有 confirm 只读预览；确认时在写锁内再次检查原文版本、草稿、前次审核 ID、审核人和原因。源内容/草稿/操作改变使指纹失效；相同操作重复提交同一指纹返回 idempotent，不追加重复批准。

拒绝、暂缓、撤销均先预览再确认，不需要发布草稿：

```powershell
.\.venv\Scripts\python.exe -m cdu_radar review reject 34 --db data/m2c.sqlite3 --reviewer 管理员 --reason '不符合当前参与目标'
.\.venv\Scripts\python.exe -m cdu_radar review defer 34 --db data/m2c.sqlite3 --reviewer 管理员 --reason '附件资格尚未核实'
.\.venv\Scripts\python.exe -m cdu_radar review revoke 34 --db data/m2c.sqlite3 --reviewer 管理员 --reason '发现信息变更，撤销发布'
# 阅读对应 preview 后，对同一命令追加 --confirm '<confirmation_token>'。
```

这是可信本地管理员流程，reviewer 是审计声明，不是身份认证。没有远程修改接口；未来远程审核必须独立实现认证、授权及 CSRF 等边界，不能把该 CLI 暴露成无认证网络接口。

## 原文变化、更正和撤销

批准绑定正文实际 SHA-256、存储正文哈希、标题、官方 URL、发布日期、原文发布单位元数据、正文/附件链接。模型版本和结果、抓取时间、首次发现/last_seen 不属于此版本。

正常采集在同一事务中发现实质版本变化后，将 active 批准置 withheld，追加 system/source_changed；不改旧人工决定，重新入队。即使原文后来恢复到旧内容，也不自动恢复批准。导出另行检查当前版本，绕过采集直接改正文也不会让旧批准覆盖新版。

已发布内容的实质更正或源版本更新后重新批准，需要 `--ack-correction`。同一个官方 URL 保持 GUID 和原 pubDate，更正不伪装成“新的比赛”；操作历史记录 correction_required、prior_guid，供后续独立更正消息机制使用。重新批准同一已发布内容不算新增发布条目。

撤销/过期/更新后的条目从下一次正式导出排除。**RSS 移除不能撤回已经发送的 QQ 消息；同 GUID 更正也不保证插件重新推送。** 管理员要在后续测试群流程处理更正提醒，本轮不生成或发送群消息。

## 导出独立 RSS

```powershell
.\.venv\Scripts\python.exe -m cdu_radar export-approved-rss --db data/m2c.sqlite3 --output data/approved.xml --dry-run
# 检查每个 entries 的批准内容、未知字段、GUID，以及 new_publications；使用当前 plan_hash：
.\.venv\Scripts\python.exe -m cdu_radar export-approved-rss --db data/m2c.sqlite3 --output data/approved.xml --confirm '<plan_hash>'
```

默认是预览，--dry-run 明确不能与 --confirm 共用；旧 `export-rss` 和 data/feed.xml 完全独立。新命令拒绝数据库文件、feed.xml 和已有的其他类型 Feed，避免把原技术 Feed 改造成正式 Feed。

RSS 2.0 只包含仍有效的批准快照，标题和简要摘要来自管理员，原文 URL/来源明确；GUID 是官方 URL 的固定命名空间 UUID，isPermaLink=false。pubDate 使用持久首次发布时间，重复导出不变；按它排序。XML/HTML 文本正确转义，临时文件与目标同目录、flush/fsync 后原子替换，不生成半个 XML。

没有批准时是合法空 Feed。本机 data/approved.xml 已实际生成并重复导出，0 条目；所有历史 36 条均未批准。feedparser 6.0.14 验证通过。[批准 RSS 格式示例](examples/approved-example.xml) 仅为合成示例，不是本机正式通知。

静态 XML 是导出时的有效快照，并不会自己监听数据库或时钟。收集、审核变更或有效期到达后必须重新导出；导出失败应暂停对外提供旧文件并处理错误，不能把旧静态文件当成实时审批状态。未来调度/HTTP 服务必须把这些步骤串行化，本轮没有部署调度器或服务。

## 未来轻量 HTTP 与测试群联调

不要把整个 data 目录暴露给 HTTP，它含 SQLite、标签和私有诊断。另设只放正式 XML 的目录，例如先导出至 data/public/approved.xml，再由管理员自行启动：

```powershell
.\.venv\Scripts\python.exe -m http.server 8000 --bind 127.0.0.1 --directory data/public
```

上述服务本轮未启动。跨容器或测试机需要另外选定仅供测试网络访问的地址、只读目录及网络权限；不要发布审核端点。M2-C 不需要大型 Web 后端，生产前仍要设计变更后重导出、有效期刷新和失败停服策略。

插件首次订阅及去重差异、测试群分步骤方案见 [ASTRBOT_HANDOFF.md](ASTRBOT_HANDOFF.md)。本轮没有安装 AstrBot、连接 QQ、付费 LLM 或生产部署。
